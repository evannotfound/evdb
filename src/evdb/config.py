from __future__ import annotations

import configparser
import json
import os
import pwd
import re
import stat
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, quote_plus

import yaml

from . import dns
from .errors import ConfigError
from .files import write_text
from .models import (
    CONFIG_DIR,
    HTTP,
    KV,
    BackupSettings,
    Config,
    Host,
    Operator,
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
        raise ConfigError("unsupported source configuration: host.yml")
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
    if values == {}:
        return {}
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
    backup = {
        "repository": host.backup.repository,
        "min_free_gb": host.backup.min_free_gb,
        "max_age_hours": host.backup.max_age_hours,
    }
    if host.backup.rclone_config is not None:
        backup["rclone_config"] = str(host.backup.rclone_config)
    projects = {}
    for project in config.projects:
        item: dict[str, Any] = {}
        if project.postgres:
            pool = project.postgres.pgbouncer
            item["postgres"] = {
                "image": project.postgres.image,
                "data_root": str(project.postgres.data_root),
                "username": project.postgres.username,
                "database": project.postgres.database,
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
                "data_root": str(project.kv.data_root),
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
            "data_roots": [str(path) for path in host.data_roots],
            "backup": backup,
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
    write_text(config.paths.source, dump(config), mode=0o600, owner=source_owner)
    if secrets:
        write_text(
            config.paths.secrets,
            dump_secrets(config.secrets),
            mode=0o600,
            owner=secret_owner,
        )


def protected(config: Config) -> tuple[str, ...]:
    rclone = config.host.backup.rclone_config
    values = [*config.secrets.values, *(_rclone_credentials(rclone) if rclone else ())]
    encoded = set()
    for value in values:
        encoded.update((value, quote(value, safe=""), quote_plus(value), json.dumps(value)[1:-1]))
    encoded.discard("")
    return tuple(sorted(encoded, key=len, reverse=True))


def _rclone_credentials(path: Path) -> list[str]:
    opened = _open_rclone(path, missing_ok=True)
    if opened is None:
        return []
    descriptor, _details = opened
    parser = configparser.RawConfigParser(interpolation=None)
    try:
        with os.fdopen(descriptor, encoding="utf-8") as source:
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


def defaults(role: str, data_root: Path, engine: str = "dragonfly") -> Postgres | KV:
    if role == "postgres":
        return Postgres(
            DEFAULT_IMAGES["postgres"],
            PgBouncer(True, DEFAULT_IMAGES["pgbouncer"], 100, 20, 5),
            data_root,
        )
    if role != "kv" or engine not in {"redis", "dragonfly"}:
        raise ConfigError("database role must be postgres or kv")
    return KV(
        engine,
        DEFAULT_IMAGES[engine],
        "durable",
        HTTP(True, DEFAULT_IMAGES["http"], 20),
        data_root,
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


def validate_postgres_name(value: str, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(char in value for char in "\0\r\n")
        or len(value.encode()) > 63
    ):
        raise ConfigError(f"Postgres {name} must be one non-empty value of at most 63 bytes")
    return value


def require_valid(config: Config) -> None:
    errors = []
    host = config.host
    for value, validator in (
        (host.id, validate_host_id),
        (host.domain, validate_domain),
        (host.routing.acme_email, validate_email),
    ):
        try:
            validator(value)
        except ConfigError as exc:
            errors.append(str(exc))
    try:
        roots = validate_data_roots(host.data_roots, config.paths, host.backup.repository)
    except ConfigError as exc:
        errors.append(str(exc))
        roots = ()
    for managed in (config.paths.config, config.paths.state):
        unsafe = _unsafe_directory(managed)
        if unsafe is not None:
            errors.append(f"managed directory is symlinked or unsafe: {unsafe}")
    try:
        repository_owner(host.backup)
    except ConfigError as exc:
        errors.append(str(exc))
    if type(host.backup.min_free_gb) is not int or host.backup.min_free_gb < 0:
        errors.append("host.backup.min_free_gb must be non-negative")
    if type(host.backup.max_age_hours) is not int or host.backup.max_age_hours < 1:
        errors.append("host.backup.max_age_hours must be positive")
    try:
        dns.require_versions(host.routing.traefik_image)
        canonical = dns.normalize(host.routing.dns_provider)
        if canonical != host.routing.dns_provider:
            errors.append(f"host.routing.dns_provider must use canonical provider name {canonical}")
        dns.validate_variables(canonical, dict(config.secrets.dns))
    except ConfigError as exc:
        errors.append(str(exc))
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
            if settings.data_root not in roots:
                errors.append(f"{project.id}/{role}: data_root is not configured on the host")
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


def reject_unsupported(paths: Paths) -> None:
    unsupported = (paths.config / "host.yml", paths.state / "state/host.json")
    found = next((path for path in unsupported if path.exists() or path.is_symlink()), None)
    if found and not paths.source.exists():
        raise ConfigError(f"unsupported source layout at {found}; provide current config.yml")


def validate_host_id(value: str) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value) or len(value) > 60:
        raise ConfigError("host ID must be a safe lowercase name of at most 60 characters")
    return value


def validate_domain(value: str) -> str:
    if not isinstance(value, str) or not _DOMAIN.fullmatch(value) or len(value) > 253:
        raise ConfigError(
            "base domain must be a valid lowercase domain; example: storage.example.com"
        )
    return value


def validate_email(value: str) -> str:
    if not isinstance(value, str) or not _EMAIL.fullmatch(value):
        raise ConfigError("ACME email must be valid; example: operations@example.com")
    return value


def validate_data_root(
    value: str | Path,
    paths: Paths | None = None,
    repository: str | None = None,
) -> Path:
    path = Path(value)
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ConfigError("host.data_root must be a normalized absolute path") from exc
    if not path.is_absolute() or path != resolved:
        raise ConfigError("host.data_root must be a normalized absolute path")
    unsafe = _unsafe_directory(path)
    if unsafe is not None:
        raise ConfigError(f"host.data_root is symlinked or unsafe: {unsafe}")

    managed = paths or Paths()
    reserved = (
        managed.config,
        managed.projects,
        managed.traefik,
        managed.backups,
        managed.locks,
    )
    if any(_paths_overlap(path, item) for item in reserved):
        raise ConfigError("host.data_root overlaps an evdb managed path")
    if (
        repository is not None
        and repository_parts(repository) is None
        and _paths_overlap(path, Path(repository))
    ):
        raise ConfigError("host.data_root overlaps the local backup repository")
    if path != managed.databases:
        try:
            parent = path.parent.lstat()
        except OSError as exc:
            raise ConfigError(f"host.data_root parent is missing or unsafe: {path.parent}") from exc
        if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
            raise ConfigError(f"host.data_root parent is missing or unsafe: {path.parent}")
        if managed.config == CONFIG_DIR and parent.st_mode & 0o022:
            mode = stat.S_IMODE(parent.st_mode)
            raise ConfigError(
                "host.data_root parent must not be group/world-writable: "
                f"{path.parent} (uid={parent.st_uid}, mode={mode:04o})"
            )
    return path


def validate_data_roots(
    values: tuple[Path, ...] | list[Path],
    paths: Paths | None = None,
    repository: str | None = None,
) -> tuple[Path, ...]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ConfigError("host.data_roots must be a non-empty list of paths")
    roots = tuple(validate_data_root(value, paths, repository) for value in values)
    if len(set(roots)) != len(roots):
        raise ConfigError("host.data_roots must not contain duplicates")
    for index, root in enumerate(roots):
        if any(_paths_overlap(root, other) for other in roots[index + 1 :]):
            raise ConfigError("host.data_roots must not overlap")
    return roots


def _config(data: dict[str, Any], secrets: Secrets, paths: Paths) -> Config:
    if {"databases", "instances", "releases"} & set(data):
        raise ConfigError("unsupported source schema")
    _only(data, {"host", "projects"}, "config")
    host_data = _object(_required(data, "host", "config"), "host")
    _only(host_data, {"id", "domain", "data_roots", "backup", "routing"}, "host")
    backup_data = _object(_required(host_data, "backup", "host"), "host.backup")
    _only(
        backup_data,
        {"repository", "rclone_config", "min_free_gb", "max_age_hours"},
        "host.backup",
    )
    routing_data = _object(_required(host_data, "routing", "host"), "host.routing")
    _only(routing_data, {"acme_email", "dns_provider", "traefik_image"}, "host.routing")
    rclone_value = backup_data.get("rclone_config")
    if rclone_value is not None and (not isinstance(rclone_value, str) or not rclone_value):
        raise ConfigError("host.backup.rclone_config must be a non-empty string or omitted")
    host = Host(
        _string(host_data, "id", "host"),
        _string(host_data, "domain", "host"),
        _data_roots(_required(host_data, "data_roots", "host")),
        BackupSettings(
            _string(backup_data, "repository", "host.backup"),
            Path(rclone_value) if rclone_value is not None else None,
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
    _only(
        data,
        {"image", "data_root", "username", "database", "pgbouncer"},
        f"projects.{project}.postgres",
    )
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
        Path(_string(data, "data_root", f"projects.{project}.postgres")),
        validate_postgres_name(
            _string(data, "username", f"projects.{project}.postgres"), "username"
        ),
        validate_postgres_name(
            _string(data, "database", f"projects.{project}.postgres"), "database name"
        ),
    )


def _kv(value: Any, project: str) -> KV:
    data = _object(value, f"projects.{project}.kv")
    _only(
        data,
        {"engine", "image", "data_root", "mode", "http", "memory", "threads"},
        f"projects.{project}.kv",
    )
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
        Path(_string(data, "data_root", f"projects.{project}.kv")),
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


def _data_roots(value: Any) -> tuple[Path, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise ConfigError("host.data_roots must be a non-empty list of paths")
    return tuple(Path(item) for item in value)


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
    expected = (
        (paths.config, True, 0, 0, 0o700),
        (source, False, 0, 0, 0o600),
        (secrets, False, 0, 0, 0o600),
    )
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
    return (0, 0), (0, 0)


def repository_parts(value: str) -> tuple[str, str] | None:
    if not isinstance(value, str) or not value or any(char in value for char in "\0\r\n"):
        raise ConfigError("host.backup.repository must be a non-empty one-line value")
    if value.startswith("rclone:"):
        remote_path = value.removeprefix("rclone:")
        if ":" not in remote_path:
            raise ConfigError("rclone repository must have form rclone:<remote>:<path>")
        remote, relative = remote_path.split(":", 1)
        path = PurePosixPath(relative)
        if (
            not remote
            or not relative
            or ":" in remote
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ConfigError("rclone repository must have form rclone:<remote>:<safe-path>")
        return remote, relative
    if "://" in value or re.match(r"^[a-z][a-z0-9+.-]*:", value, re.IGNORECASE):
        raise ConfigError("repository must use rclone:<remote>:<path> or an absolute local path")
    path = Path(value)
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ConfigError("local repository must be a normalized absolute path") from exc
    if not path.is_absolute() or path != resolved:
        raise ConfigError("local repository must be a normalized absolute path")
    unsafe = _unsafe_directory(path)
    if unsafe is not None:
        raise ConfigError(f"local repository is symlinked or unsafe: {unsafe}")
    return None


def rclone_remotes(path: Path) -> tuple[str, ...]:
    parser = _rclone_parser(path)
    return tuple(sorted(parser.sections()))


def repository_owner(settings: BackupSettings) -> Operator:
    parts = repository_parts(settings.repository)
    if parts is not None:
        if settings.rclone_config is None:
            raise ConfigError("rclone repository requires host.backup.rclone_config")
        remote, _relative = parts
        if remote not in rclone_remotes(settings.rclone_config):
            raise ConfigError(f"rclone repository remote is not configured: {remote}:")
        return rclone_owner(settings.rclone_config)
    if settings.rclone_config is not None:
        raise ConfigError("local repository must omit host.backup.rclone_config")
    return _local_owner(Path(settings.repository))


def rclone_owner(path: Path) -> Operator:
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ConfigError("host.backup.rclone_config must be a normalized absolute path") from exc
    if not path.is_absolute() or path != resolved:
        raise ConfigError("host.backup.rclone_config must be a normalized absolute path")
    descriptor, details = _open_rclone(path)
    os.close(descriptor)
    if stat.S_IMODE(details.st_mode) != 0o600:
        raise ConfigError(f"rclone configuration must have mode 0600: {path}")
    operator = _operator(details.st_uid, path)
    try:
        parent = path.parent.lstat()
    except OSError as exc:
        raise ConfigError(f"rclone configuration has an unsafe parent: {path}") from exc
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != details.st_uid
        or parent.st_mode & 0o022
        or parent.st_mode & 0o300 != 0o300
    ):
        raise ConfigError(
            f"rclone configuration parent must be safely user-writable: {path.parent}"
        )
    return operator


def _local_owner(path: Path) -> Operator:
    repository_parts(str(path))
    try:
        if path.exists() or path.is_symlink():
            details = path.lstat()
            if not stat.S_ISDIR(details.st_mode) or path.is_symlink():
                raise ConfigError(f"local repository is missing or unsafe: {path}")
            parent = path.parent.lstat()
            if (
                not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != details.st_uid
                or parent.st_mode & 0o022
                or parent.st_mode & 0o300 != 0o300
            ):
                raise ConfigError(f"local repository owner differs from its parent: {path}")
            selected = path
        else:
            selected = path.parent
            details = selected.lstat()
    except OSError as exc:
        raise ConfigError(f"local repository parent is missing or unsafe: {path.parent}") from exc
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid == 0
        or details.st_mode & 0o022
        or details.st_mode & 0o300 != 0o300
    ):
        raise ConfigError(
            "local repository or parent must be safely user-writable "
            f"by a non-root owner: {selected}"
        )
    return _operator(details.st_uid, selected)


def _operator(uid: int, source: Path) -> Operator:
    if uid == 0:
        raise ConfigError(f"repository operator must be non-root: {source}")
    try:
        account = pwd.getpwuid(uid)
    except KeyError as exc:
        raise ConfigError(f"repository owner is unknown: {source}") from exc
    home = Path(account.pw_dir)
    try:
        resolved_home = home.resolve(strict=False)
        home_details = home.lstat()
    except (OSError, RuntimeError) as exc:
        raise ConfigError(f"repository operator has an unsafe home: {home}") from exc
    if (
        not home.is_absolute()
        or home != resolved_home
        or not stat.S_ISDIR(home_details.st_mode)
        or home_details.st_uid != account.pw_uid
        or home_details.st_mode & 0o022
    ):
        raise ConfigError(f"repository operator has an unsafe home: {home}")
    for current in dict.fromkeys((*source.parent.parents, *home.parents)):
        try:
            ancestor = current.lstat()
        except OSError as exc:
            raise ConfigError(f"repository operator has an unsafe ancestor: {current}") from exc
        shared = ancestor.st_uid == 0 and ancestor.st_mode & stat.S_ISVTX
        if not stat.S_ISDIR(ancestor.st_mode) or (ancestor.st_mode & 0o022 and not shared):
            raise ConfigError(f"repository operator has an unsafe ancestor: {current}")
    try:
        groups = tuple(
            sorted(set(os.getgrouplist(account.pw_name, account.pw_gid)) - {account.pw_gid})
        )
    except OSError as exc:
        raise ConfigError(f"repository operator groups are unavailable: {source}") from exc
    return Operator(account.pw_uid, account.pw_gid, account.pw_name, home, groups)


def _rclone_parser(path: Path) -> configparser.RawConfigParser:
    opened = _open_rclone(path)
    descriptor, _details = opened
    parser = configparser.RawConfigParser(interpolation=None)
    try:
        with os.fdopen(descriptor, encoding="utf-8") as source:
            parser.read_file(source)
    except (configparser.Error, OSError, UnicodeError) as exc:
        raise ConfigError(f"invalid rclone configuration: {path}") from exc
    return parser


def _open_rclone(path: Path, *, missing_ok: bool = False) -> tuple[int, os.stat_result] | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ConfigError(f"rclone configuration is missing or unsafe: {path}") from None
    except OSError as exc:
        raise ConfigError(f"rclone configuration is missing or unsafe: {path}") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise ConfigError(f"rclone configuration is missing or unsafe: {path}")
        return descriptor, details
    except BaseException:
        os.close(descriptor)
        raise


def _unsafe_directory(path: Path) -> Path | None:
    for current in (path, *path.parents):
        try:
            details = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            return current
    return None


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


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
