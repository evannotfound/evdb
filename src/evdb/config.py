from __future__ import annotations

import configparser
import grp
import json
import pwd
import re
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus

import yaml

from .errors import ConfigError
from .files import write_bytes, write_text
from .models import (
    CONFIG_DIR,
    HTTP,
    KV,
    BackupSettings,
    Config,
    Host,
    Paths,
    PgBouncer,
    Postgres,
    Project,
    ProjectSecrets,
    RoleSecrets,
    Routing,
    Secrets,
    http_port,
)

DEFAULT_IMAGES = {
    "postgres": "postgres:16",
    "pgbouncer": "edoburu/pgbouncer:v1.25.1-p0",
    "redis": "redis:7.2.5",
    "dragonfly": "docker.dragonflydb.io/dragonflydb/dragonfly:v1.34.1",
    "http": (
        "hiett/serverless-redis-http@"
        "sha256:5b0bb9239fce53abf87b2018a7a0deb9ec7bd900c5360738fe5fbeeb426f9150"
    ),
    "traefik": "traefik:v3.7.8",
}
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_ENV = re.compile(r"-(?:dev|test|prod)-[0-9]+$")
_DOMAIN = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
)
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_IMAGE_DIGEST = re.compile(r"[^\s]+@sha256:[0-9a-f]{64}")
_DNS_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RCLONE_CREDENTIAL_FIELDS = {
    "access_token",
    "client_id",
    "client_secret",
    "key",
    "pass",
    "password",
    "refresh_token",
    "secret_access_key",
    "token",
}
_SYSTEM_DATA_ROOTS = tuple(
    Path(value)
    for value in (
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/lib",
        "/lib64",
        "/opt",
        "/proc",
        "/root",
        "/run",
        "/sbin",
        "/sys",
        "/usr",
    )
)
_SHALLOW_DATA_ROOTS = {
    Path(value) for value in ("/", "/data", "/home", "/media", "/mnt", "/srv", "/var", "/var/lib")
}


class _Loader(yaml.SafeLoader):
    pass


def _mapping(loader: _Loader, node: yaml.MappingNode, deep: bool = False) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ConfigError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load(
    path: str | Path = CONFIG_DIR / "config.yml",
    *,
    secrets_path: str | Path | None = None,
    paths: Paths | None = None,
) -> Config:
    source = Path(path)
    if source.name == "host.yml":
        raise ConfigError("pre-v1 host.yml is unsupported; reset the disposable host or migrate it")
    if source.name != "config.yml":
        raise ConfigError("source configuration must be named config.yml")
    if not source.is_file() or source.is_symlink():
        raise ConfigError(f"missing source configuration: {source}")
    managed = paths or Paths(config=source.parent)
    secret_source = Path(secrets_path) if secrets_path else source.with_name("secrets.yml")
    _require_canonical_layout(managed, source, secret_source)
    data = _object(_read(source), "config")
    secrets = load_secrets(secret_source)
    config = _config(data, secrets, managed)
    require_valid(config)
    _require_source_mode(source)
    return config


def load_secrets(path: str | Path = CONFIG_DIR / "secrets.yml") -> Secrets:
    source = Path(path)
    if not source.is_file() or source.is_symlink():
        raise ConfigError(f"missing secret configuration: {source}")
    mode = source.stat().st_mode & 0o777
    if mode != 0o600:
        raise ConfigError(f"secret configuration must have mode 0600: {source}")
    data = _object(_read(source), "secrets")
    _only(data, {"host", "projects"}, "secrets")
    host = _object(_required(data, "host", "secrets"), "secrets.host")
    _only(host, {"restic_password", "dns"}, "secrets.host")
    restic = _string(host, "restic_password", "secrets.host")
    dns_data = dns_values(
        _object(_required(host, "dns", "secrets.host"), "secrets.host.dns"),
        "secrets.host.dns",
    )
    projects = []
    for project, raw in _object(_required(data, "projects", "secrets"), "secrets.projects").items():
        _project_id(project)
        item = _object(raw, f"secrets.projects.{project}")
        _only(item, {"postgres", "kv"}, f"secrets.projects.{project}")
        projects.append(
            ProjectSecrets(
                project,
                _role_secret(item["postgres"], project, "postgres") if "postgres" in item else None,
                _role_secret(item["kv"], project, "kv") if "kv" in item else None,
            )
        )
    return Secrets(
        restic, tuple(sorted(dns_data.items())), tuple(sorted(projects, key=lambda p: p.id))
    )


def dns_values(values: dict[str, Any], name: str) -> dict[str, str]:
    if not values or not all(
        isinstance(key, str)
        and _DNS_KEY.fullmatch(key)
        and isinstance(value, str)
        and value
        and not any(character in value for character in "\0\r\n")
        for key, value in values.items()
    ):
        raise ConfigError(f"{name} must contain safe keys and non-empty one-line values")
    return values


def dump(config: Config) -> str:
    return yaml.safe_dump(as_dict(config), sort_keys=False)


def dump_secrets(value: Secrets) -> str:
    projects: dict[str, Any] = {}
    for project in value.projects:
        roles = {}
        if project.postgres:
            roles["postgres"] = {"password": project.postgres.password}
        if project.kv:
            roles["kv"] = {"password": project.kv.password}
            if project.kv.http_token is not None:
                roles["kv"]["http_token"] = project.kv.http_token
        projects[project.id] = roles
    data = {
        "host": {"restic_password": value.restic_password, "dns": dict(value.dns)},
        "projects": projects,
    }
    return yaml.safe_dump(data, sort_keys=False)


def as_dict(config: Config) -> dict[str, Any]:
    host = config.host
    projects = {}
    for project in config.projects:
        item: dict[str, Any] = {}
        if project.postgres:
            pool = project.postgres.pgbouncer
            item["postgres"] = {
                "image": project.postgres.image,
                "pgbouncer": {
                    "enabled": pool.enabled,
                    "image": pool.image,
                    "max_clients": pool.max_clients,
                    "pool_size": pool.pool_size,
                    "reserve_size": pool.reserve_size,
                },
            }
        if project.kv:
            http = project.kv.http
            role = {
                "engine": project.kv.engine,
                "image": project.kv.image,
                "mode": project.kv.mode,
                "http": {
                    "enabled": http.enabled,
                    "image": http.image,
                    "connections": http.connections,
                    "domain": http.domain,
                },
            }
            if project.kv.engine == "dragonfly":
                role.update(memory=project.kv.memory, threads=project.kv.threads)
            item["kv"] = role
        projects[project.id] = item
    return {
        "host": {
            "id": host.id,
            "domain": host.domain,
            "data_root": str(host.data_root),
            "backup": {
                "repository": host.backup.repository,
                "min_free_gb": host.backup.min_free_gb,
                "max_age_hours": host.backup.max_age_hours,
            },
            "routing": {
                "acme_email": host.routing.acme_email,
                "dns_provider": host.routing.dns_provider,
                "traefik_image": host.routing.traefik_image,
            },
        },
        "projects": projects,
    }


def write(config: Config, *, secrets: bool = True) -> None:
    require_valid(config)
    source_owner, secret_owner = _source_owners(config.paths)
    write_text(config.paths.source, dump(config), mode=0o640, owner=source_owner)
    if secrets:
        write_text(
            config.paths.secrets,
            dump_secrets(config.secrets),
            mode=0o600,
            owner=secret_owner,
        )


def seed_rclone(config: Config, source: str | Path | None) -> None:
    target = config.paths.rclone
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file():
            raise ConfigError(f"rclone configuration is unsafe: {target}")
        if target.stat().st_mode & 0o077:
            raise ConfigError(f"rclone configuration must be private: {target}")
        return
    if source is None:
        raise ConfigError("initialization requires --rclone-config when rclone.conf is absent")
    candidate = Path(source)
    if candidate.is_symlink() or not candidate.is_file():
        raise ConfigError(f"rclone seed is missing or unsafe: {candidate}")
    data = candidate.read_bytes()
    if not data.strip():
        raise ConfigError("rclone seed is empty")
    _source_owner, secret_owner = _source_owners(config.paths)
    write_bytes(target, data, mode=0o600, owner=secret_owner)


def protected(config: Config) -> tuple[str, ...]:
    values = [*config.secrets.values, *_rclone_credentials(config.paths.rclone)]
    encoded = set()
    for value in values:
        encoded.update((value, quote(value, safe=""), quote_plus(value), json.dumps(value)[1:-1]))
    encoded.discard("")
    return tuple(sorted(encoded, key=len, reverse=True))


def _rclone_credentials(path: Path) -> list[str]:
    if not path.exists() and not path.is_symlink():
        return []
    if path.is_symlink() or not path.is_file():
        raise ConfigError(f"rclone configuration is unsafe: {path}")
    parser = configparser.RawConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as source:
            parser.read_file(source)
    except (configparser.Error, OSError, UnicodeError) as exc:
        raise ConfigError(f"invalid rclone configuration: {path}") from exc
    values = []
    try:
        groups = [
            parser.defaults(),
            *(dict(parser.items(section)) for section in parser.sections()),
        ]
        for items in groups:
            for key, value in items.items():
                if key.lower() in _RCLONE_CREDENTIAL_FIELDS and value:
                    values.append(value)
                values.extend(_json_credentials(value))
    except configparser.Error as exc:
        raise ConfigError(f"invalid rclone configuration: {path}") from exc
    return values


def defaults(role: str, engine: str = "dragonfly") -> Postgres | KV:
    if role == "postgres":
        return Postgres(
            DEFAULT_IMAGES["postgres"],
            PgBouncer(True, DEFAULT_IMAGES["pgbouncer"], 100, 20, 5),
        )
    if role != "kv" or engine not in {"redis", "dragonfly"}:
        raise ConfigError("database role must be postgres or kv")
    return KV(
        engine,
        DEFAULT_IMAGES[engine],
        "durable",
        HTTP(True, DEFAULT_IMAGES["http"], 20),
        "256mb" if engine == "dragonfly" else None,
        1 if engine == "dragonfly" else None,
    )


def add_role(
    config: Config,
    project_id: str,
    role: str,
    settings: Postgres | KV,
    credentials: RoleSecrets,
) -> Config:
    _project_id(project_id)
    current = next(
        (database for database in config.databases if database.identity == f"{project_id}/{role}"),
        None,
    )
    if current is not None:
        if current.credentials != credentials:
            raise ConfigError("password rotation is outside v1")
        return config
    projects = list(config.projects)
    for index, project in enumerate(projects):
        if project.id == project_id:
            projects[index] = replace(project, **{role: settings})
            break
    else:
        projects.append(Project(project_id, **{role: settings}))
    secret_projects = list(config.secrets.projects)
    for index, project in enumerate(secret_projects):
        if project.id == project_id:
            secret_projects[index] = replace(project, **{role: credentials})
            break
    else:
        secret_projects.append(ProjectSecrets(project_id, **{role: credentials}))
    updated = replace(
        config,
        projects=tuple(sorted(projects, key=lambda item: item.id)),
        secrets=replace(
            config.secrets,
            projects=tuple(sorted(secret_projects, key=lambda item: item.id)),
        ),
    )
    require_valid(updated)
    return updated


def replace_role(config: Config, selector: str, settings: Postgres | KV) -> Config:
    target = config.select(selector)
    projects = tuple(
        replace(project, **{target.role: settings}) if project.id == target.project else project
        for project in config.projects
    )
    updated = replace(config, projects=projects)
    require_valid(updated)
    return updated


def validate_image(value: Any, name: str = "image") -> None:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ConfigError(f"{name} must be a concrete image reference")
    slash = value.rfind("/")
    colon = value.rfind(":")
    tagged = colon > slash and colon < len(value) - 1 and value[colon + 1 :] != "latest"
    if (
        "://" in value
        or any(character.isspace() for character in value)
        or not (tagged or _IMAGE_DIGEST.fullmatch(value))
    ):
        raise ConfigError(f"{name} must use an explicit non-latest tag or SHA-256 digest")


def require_valid(config: Config) -> None:
    errors = []
    host = config.host
    if not _NAME.fullmatch(host.id) or len(host.id) > 60:
        errors.append("host.id must be a safe lowercase name of at most 60 characters")
    if not _DOMAIN.fullmatch(host.domain) or len(host.domain) > 253:
        errors.append("host.domain must be a valid lowercase domain")
    supplied_data_root = host.data_root
    try:
        data_root = supplied_data_root.resolve(strict=False)
    except OSError, RuntimeError:
        data_root = supplied_data_root
        errors.append("host.data_root cannot be normalized safely")
    if ".." in supplied_data_root.parts:
        errors.append("host.data_root must not contain '..'")
    if supplied_data_root != data_root:
        errors.append("host.data_root must equal its normalized absolute path")
    if (
        not supplied_data_root.is_absolute()
        or len(data_root.parts) < 3
        or data_root in _SHALLOW_DATA_ROOTS
    ):
        errors.append("host.data_root must be a dedicated safe absolute path")
    if any(data_root == root or data_root.is_relative_to(root) for root in _SYSTEM_DATA_ROOTS):
        errors.append("host.data_root must not use a system path")
    if config.paths.config == CONFIG_DIR and any(
        data_root == root or data_root.is_relative_to(root)
        for root in (Path("/tmp"), Path("/var/tmp"))
    ):
        errors.append("host.data_root must not use temporary storage")
    for managed in (config.paths.config, config.paths.state):
        normalized_managed = managed.resolve(strict=False)
        if (
            data_root == normalized_managed
            or data_root.is_relative_to(normalized_managed)
            or normalized_managed.is_relative_to(data_root)
        ):
            errors.append(f"host.data_root overlaps managed path {normalized_managed}")
    for managed in (
        config.paths.config,
        config.paths.state,
        supplied_data_root,
    ):
        unsafe = _unsafe_directory(managed)
        if unsafe is not None:
            errors.append(f"managed directory is symlinked or unsafe: {unsafe}")
    if not host.backup.repository or any(
        character in host.backup.repository for character in "\0\r\n"
    ):
        errors.append("host.backup.repository must be non-empty")
    if re.search(r"://[^/@\s]+@", host.backup.repository):
        errors.append("host.backup.repository must not contain credentials")
    if type(host.backup.min_free_gb) is not int or host.backup.min_free_gb < 0:
        errors.append("host.backup.min_free_gb must be non-negative")
    if type(host.backup.max_age_hours) is not int or host.backup.max_age_hours < 1:
        errors.append("host.backup.max_age_hours must be positive")
    if not _EMAIL.fullmatch(host.routing.acme_email):
        errors.append("host.routing.acme_email must be a valid email")
    if not _NAME.fullmatch(host.routing.dns_provider):
        errors.append("host.routing.dns_provider must be a safe name")
    try:
        validate_image(host.routing.traefik_image, "host.routing.traefik_image")
    except ConfigError as exc:
        errors.append(str(exc))
    seen = set()
    http_domains = {}
    http_ports = {}
    for project in config.projects:
        if project.id in seen:
            errors.append(f"{project.id}: duplicate project")
        seen.add(project.id)
        try:
            _project_id(project.id)
        except ConfigError as exc:
            errors.append(str(exc))
        if project.postgres is None and project.kv is None:
            errors.append(f"{project.id}: project must contain a database role")
        for role in ("postgres", "kv"):
            settings = getattr(project, role)
            if settings is None:
                continue
            native_domain = f"{project.id}.{host.id}.{host.domain}"
            if not _DOMAIN.fullmatch(native_domain) or len(native_domain) > 253:
                errors.append(f"{project.id}/{role}: native domain is invalid")
            try:
                credentials = config.secrets.select(project.id, role)
                _credential(credentials.password, f"{project.id}/{role} password")
                if role == "kv" and settings.http.enabled:
                    _credential(credentials.http_token, f"{project.id}/kv HTTP token")
                from .engines import get

                get("postgres" if role == "postgres" else settings.engine).validate(settings)
            except ConfigError as exc:
                errors.append(str(exc))
            if role == "kv" and settings.http.enabled:
                domain = settings.http.domain or f"{project.id}.{host.id}.{host.domain}"
                port = http_port(project.id)
                if not _DOMAIN.fullmatch(domain) or len(domain) > 253:
                    errors.append(f"{project.id}/kv: HTTP domain is invalid")
                if domain in http_domains:
                    errors.append(
                        f"{project.id}/kv: HTTP domain collides with {http_domains[domain]}"
                    )
                if port in http_ports:
                    errors.append(
                        f"{project.id}/kv: HTTP port {port} collides with {http_ports[port]}"
                    )
                http_domains[domain] = f"{project.id}/kv"
                http_ports[port] = f"{project.id}/kv"
    configured = {
        (p.id, role) for p in config.projects for role in ("postgres", "kv") if getattr(p, role)
    }
    secret_roles = {
        (p.id, role)
        for p in config.secrets.projects
        for role in ("postgres", "kv")
        if getattr(p, role)
    }
    extra = sorted(secret_roles - configured)
    if extra:
        errors.append(f"unexpected secret role: {extra[0][0]}/{extra[0][1]}")
    secret_ids = [project.id for project in config.secrets.projects]
    if len(secret_ids) != len(set(secret_ids)):
        errors.append("secret projects must not contain duplicates")
    exposed = protected(config)
    for name, value in _scalars(as_dict(config)):
        if any(secret == value or (len(secret) >= 8 and secret in value) for secret in exposed):
            errors.append(f"{name} contains a managed credential value")
    if errors:
        raise ConfigError("config failed:\n- " + "\n- ".join(errors))


def reject_legacy(paths: Paths) -> None:
    legacy = (paths.config / "host.yml", paths.state / "state/host.json")
    found = next((path for path in legacy if path.exists() or path.is_symlink()), None)
    if found and not paths.source.exists():
        raise ConfigError(
            f"pre-v1 state is unsupported at {found}; reset the disposable host or migrate it"
        )


def _config(data: dict[str, Any], secrets: Secrets, paths: Paths) -> Config:
    if {"databases", "instances", "releases"} & set(data):
        raise ConfigError("pre-v1 source schema is unsupported")
    _only(data, {"host", "projects"}, "config")
    host_data = _object(_required(data, "host", "config"), "host")
    _only(host_data, {"id", "domain", "data_root", "backup", "routing"}, "host")
    backup_data = _object(_required(host_data, "backup", "host"), "host.backup")
    _only(backup_data, {"repository", "min_free_gb", "max_age_hours"}, "host.backup")
    routing_data = _object(_required(host_data, "routing", "host"), "host.routing")
    _only(routing_data, {"acme_email", "dns_provider", "traefik_image"}, "host.routing")
    host = Host(
        _string(host_data, "id", "host"),
        _string(host_data, "domain", "host"),
        Path(_string(host_data, "data_root", "host")),
        BackupSettings(
            _string(backup_data, "repository", "host.backup"),
            _integer(backup_data, "min_free_gb", "host.backup", minimum=0),
            _integer(backup_data, "max_age_hours", "host.backup", minimum=1),
        ),
        Routing(
            _string(routing_data, "acme_email", "host.routing"),
            _string(routing_data, "dns_provider", "host.routing"),
            _string(routing_data, "traefik_image", "host.routing"),
        ),
    )
    projects = []
    for project, raw in _object(_required(data, "projects", "config"), "projects").items():
        _project_id(project)
        item = _object(raw, f"projects.{project}")
        _only(item, {"postgres", "kv"}, f"projects.{project}")
        projects.append(
            Project(
                project,
                _postgres(item["postgres"], project) if "postgres" in item else None,
                _kv(item["kv"], project) if "kv" in item else None,
            )
        )
    return Config(host, tuple(sorted(projects, key=lambda item: item.id)), secrets, paths)


def _postgres(value: Any, project: str) -> Postgres:
    data = _object(value, f"projects.{project}.postgres")
    _only(data, {"image", "pgbouncer"}, f"projects.{project}.postgres")
    pool = _object(_required(data, "pgbouncer", f"projects.{project}.postgres"), "pgbouncer")
    _only(pool, {"enabled", "image", "max_clients", "pool_size", "reserve_size"}, "pgbouncer")
    return Postgres(
        _string(data, "image", f"projects.{project}.postgres"),
        PgBouncer(
            _boolean(pool, "enabled", "pgbouncer"),
            _string(pool, "image", "pgbouncer"),
            _integer(pool, "max_clients", "pgbouncer"),
            _integer(pool, "pool_size", "pgbouncer"),
            _integer(pool, "reserve_size", "pgbouncer"),
        ),
    )


def _kv(value: Any, project: str) -> KV:
    data = _object(value, f"projects.{project}.kv")
    _only(data, {"engine", "image", "mode", "http", "memory", "threads"}, f"projects.{project}.kv")
    http = _object(_required(data, "http", f"projects.{project}.kv"), "http")
    _only(http, {"enabled", "image", "connections", "domain"}, "http")
    domain = _required(http, "domain", "http")
    if domain is not None and not isinstance(domain, str):
        raise ConfigError("http.domain must be a string or null")
    memory = data.get("memory")
    if memory is not None and not isinstance(memory, str):
        raise ConfigError("kv.memory must be a string or null")
    threads = data.get("threads")
    if threads is not None and type(threads) is not int:
        raise ConfigError("kv.threads must be an integer or null")
    return KV(
        _string(data, "engine", f"projects.{project}.kv"),
        _string(data, "image", f"projects.{project}.kv"),
        _string(data, "mode", f"projects.{project}.kv"),
        HTTP(
            _boolean(http, "enabled", "http"),
            _string(http, "image", "http"),
            _integer(http, "connections", "http"),
            domain,
        ),
        memory,
        threads,
    )


def _role_secret(value: Any, project: str, role: str) -> RoleSecrets:
    data = _object(value, f"secrets.projects.{project}.{role}")
    allowed = {"password"} if role == "postgres" else {"password", "http_token"}
    _only(data, allowed, f"secrets.projects.{project}.{role}")
    token = data.get("http_token")
    if token is not None and not isinstance(token, str):
        raise ConfigError(f"secrets.projects.{project}.{role}.http_token must be a string")
    return RoleSecrets(_string(data, "password", f"secrets.projects.{project}.{role}"), token)


def _credential(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value or any(char in value for char in "\0\r\n"):
        raise ConfigError(f"{name} must be one non-empty line")


def _read(path: Path) -> Any:
    try:
        return yaml.load(path.read_text(), Loader=_Loader)
    except ConfigError:
        raise
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"invalid YAML: {path}") from exc


def _require_source_mode(path: Path) -> None:
    if path.stat().st_mode & 0o022:
        raise ConfigError(f"source configuration is writable by group or others: {path}")


def _require_canonical_layout(paths: Paths, source: Path, secrets: Path) -> None:
    if paths.config != CONFIG_DIR:
        return
    try:
        root_uid = pwd.getpwnam("root").pw_uid
        evdb_uid = pwd.getpwnam("evdb").pw_uid
        evdb_gid = grp.getgrnam("evdb").gr_gid
    except KeyError as exc:
        raise ConfigError("canonical configuration requires the evdb account and group") from exc
    expected = (
        (paths.config, True, root_uid, evdb_gid, 0o1770),
        (source, False, root_uid, evdb_gid, 0o640),
        (secrets, False, evdb_uid, evdb_gid, 0o600),
    )
    if paths.rclone.exists() or paths.rclone.is_symlink():
        expected += ((paths.rclone, False, evdb_uid, evdb_gid, 0o600),)
    for path, directory, uid, gid, mode in expected:
        if path.is_symlink() or (not path.is_dir() if directory else not path.is_file()):
            raise ConfigError(f"canonical path is missing or unsafe: {path}")
        details = path.stat()
        if details.st_uid != uid or details.st_gid != gid or details.st_mode & 0o7777 != mode:
            raise ConfigError(
                f"canonical path has unexpected owner, group, or mode {mode:04o}: {path}"
            )


def _source_owners(paths: Paths) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    if paths.config != CONFIG_DIR:
        return None, None
    try:
        root_uid = pwd.getpwnam("root").pw_uid
        evdb_uid = pwd.getpwnam("evdb").pw_uid
        evdb_gid = grp.getgrnam("evdb").gr_gid
    except KeyError as exc:
        raise ConfigError("canonical configuration requires the evdb account and group") from exc
    return (root_uid, evdb_gid), (evdb_uid, evdb_gid)


def _unsafe_directory(path: Path) -> Path | None:
    for current in (path, *path.parents):
        try:
            details = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            return current
    return None


def _json_credentials(value: str) -> list[str]:
    try:
        data = json.loads(value)
    except json.JSONDecodeError, TypeError:
        return []
    found = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if (
                    isinstance(key, str)
                    and key.lower() in _RCLONE_CREDENTIAL_FIELDS
                    and isinstance(child, str)
                    and child
                ):
                    found.append(child)
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(data)
    return found


def _scalars(value: Any, name: str = "config"):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _scalars(child, f"{name}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _scalars(child, f"{name}[{index}]")
    elif isinstance(value, str):
        yield name, value


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigError(f"{name} must be an object")
    return value


def _only(data: dict[str, Any], allowed: set[str], name: str) -> None:
    extra = sorted(set(data) - allowed)
    if extra:
        raise ConfigError(f"{name}: unsupported field {extra[0]}")


def _required(data: dict[str, Any], key: str, name: str) -> Any:
    if key not in data:
        raise ConfigError(f"{name}.{key} is required")
    return data[key]


def _string(data: dict[str, Any], key: str, name: str) -> str:
    value = _required(data, key, name)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{name}.{key} must be a non-empty string")
    return value


def _integer(data: dict[str, Any], key: str, name: str, minimum: int = 1) -> int:
    value = _required(data, key, name)
    if type(value) is not int or value < minimum:
        raise ConfigError(f"{name}.{key} must be an integer of at least {minimum}")
    return value


def _boolean(data: dict[str, Any], key: str, name: str) -> bool:
    value = _required(data, key, name)
    if type(value) is not bool:
        raise ConfigError(f"{name}.{key} must be a boolean")
    return value


def _project_id(value: str) -> None:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ConfigError(f"project {value}: must be a safe lowercase name")
    if not _ENV.search(value):
        raise ConfigError(f"project {value}: must end in -dev-N, -test-N, or -prod-N")
