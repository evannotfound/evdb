from __future__ import annotations

import json
import os
import re
import socket
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError
from .files import write_bytes, write_json
from .images import image_major, locked_image, source_digest, validate_source
from .images import state as resolve_image

CONFIG_DIR = Path("/etc/evdb")
STATE_DIR = Path("/var/lib/evdb")
TOOL_DIR = Path("/opt/evdb")
STATE_VERSION = 2
DEFAULT_RETENTION = {"daily": 7, "weekly": 4, "monthly": 12, "data_parts": 12}
DEFAULT_TIMEOUTS = {
    "command": 300,
    "health": 120,
    "restore": 8 * 3600,
    "maintenance": 24 * 3600,
}
DEFAULT_HTTP_START = 13379
DEFAULT_HTTP_END = 13478
DEFAULT_HTTP_CONNECTIONS = 20
POSTGRES_USER = "default"
POSTGRES_DATABASE = "postgres"
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
_ENV = re.compile(r"-(dev|test|prod)-[0-9]+$")
_DOMAIN = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
)
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_MEMORY = re.compile(r"[1-9][0-9]*(?:kb|mb|gb)")
_SECRET_WORDS = ("password", "token", "secret", "credential")


@dataclass(frozen=True)
class Paths:
    config: Path = CONFIG_DIR
    state: Path = STATE_DIR
    tool: Path = TOOL_DIR

    @property
    def source(self) -> Path:
        return self.config / "host.yml"

    @property
    def previous(self) -> Path:
        return self.config / "host.previous.yml"

    @property
    def projects(self) -> Path:
        return self.config / "projects"

    @property
    def traefik(self) -> Path:
        return self.config / "traefik"

    @property
    def secrets(self) -> Path:
        return self.config / "secrets"

    @property
    def machine_state(self) -> Path:
        return self.state / "state" / "host.json"

    @property
    def backups(self) -> Path:
        return self.state / "backups"

    @property
    def restores(self) -> Path:
        return self.state / "restores"

    @property
    def locks(self) -> Path:
        return self.state / "locks"

    @property
    def rclone(self) -> Path:
        return self.state / "rclone"

    @property
    def activity(self) -> Path:
        return self.state / "activity.jsonl"

    def role_config(self, project: str, role: str) -> Path:
        return self.projects / project / role

    def compose(self, project: str, role: str) -> Path:
        return self.role_config(project, role) / "compose.yaml"

    def role_secrets(self, project: str, role: str) -> Path:
        return self.secrets / project / role

    def role_backups(self, project: str, role: str) -> Path:
        return self.backups / project / role

    def role_lock(self, project: str, role: str) -> Path:
        return self.locks / project / f"{role}.lock"


@dataclass(frozen=True)
class BackupSettings:
    repos: dict[str, str]
    retention: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_RETENTION))
    min_free_gb: int = 5
    max_age_hours: int = 26
    test_max_age_days: int = 30


@dataclass(frozen=True)
class Routing:
    acme_email: str
    dns_provider: str
    traefik_image: str = DEFAULT_IMAGES["traefik"]


@dataclass(frozen=True)
class Host:
    id: str
    domain: str
    data_root: Path
    backup: BackupSettings
    routing: Routing
    http_port_start: int = DEFAULT_HTTP_START
    http_port_end: int = DEFAULT_HTTP_END

    @property
    def timeouts(self) -> dict[str, int]:
        return dict(DEFAULT_TIMEOUTS)


@dataclass(frozen=True)
class PgBouncer:
    enabled: bool = True
    image: str = DEFAULT_IMAGES["pgbouncer"]
    max_clients: int = 100
    pool_size: int = 20
    reserve_size: int = 5


@dataclass(frozen=True)
class HTTP:
    enabled: bool = True
    image: str = DEFAULT_IMAGES["http"]
    connections: int = DEFAULT_HTTP_CONNECTIONS
    domain: str | None = None


@dataclass(frozen=True)
class Postgres:
    image: str
    pgbouncer: PgBouncer = field(default_factory=PgBouncer)

    @property
    def user(self) -> str:
        return POSTGRES_USER

    @property
    def database(self) -> str:
        return POSTGRES_DATABASE


@dataclass(frozen=True)
class KV:
    engine: str
    image: str
    mode: str = "durable"
    http: HTTP = field(default_factory=HTTP)
    memory: str | None = None
    threads: int | None = None


@dataclass(frozen=True)
class Project:
    id: str
    postgres: Postgres | None = None
    kv: KV | None = None


@dataclass(frozen=True)
class Database:
    project: str
    role: str
    settings: Postgres | KV
    host: Host
    paths: Paths

    @property
    def identity(self) -> str:
        return f"{self.project}/{self.role}"

    @property
    def engine(self) -> str:
        return "postgres" if self.role == "postgres" else self.settings.engine

    @property
    def image(self) -> str:
        return self.settings.image

    @property
    def durable(self) -> bool:
        return self.role == "postgres" or self.settings.mode == "durable"

    @property
    def compose_project(self) -> str:
        return f"evdb-{self.project}-{self.role}"

    @property
    def data(self) -> Path:
        return self.host.data_root / self.project / self.role / "data"

    @property
    def compose(self) -> Path:
        return self.paths.compose(self.project, self.role)

    @property
    def domain(self) -> str:
        return project_domain(self.project, self.host)

    @property
    def port(self) -> int:
        return 5432 if self.role == "postgres" else 6379


@dataclass(frozen=True)
class Config:
    host: Host
    projects: tuple[Project, ...]
    paths: Paths = field(default_factory=Paths)

    @property
    def databases(self) -> tuple[Database, ...]:
        result = []
        for project in self.projects:
            if project.postgres is not None:
                result.append(
                    Database(project.id, "postgres", project.postgres, self.host, self.paths)
                )
            if project.kv is not None:
                result.append(Database(project.id, "kv", project.kv, self.host, self.paths))
        return tuple(result)

    def select(self, selector: str) -> Database:
        if "/" in selector:
            project, role = selector.split("/", 1)
            if role not in {"postgres", "kv"} or not project:
                raise ConfigError(f"invalid database selector: {selector}")
            matches = [item for item in self.databases if item.identity == selector]
        else:
            matches = [item for item in self.databases if item.project == selector]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            choices = ", ".join(item.identity for item in matches)
            raise ConfigError(f"ambiguous database {selector}; use one of: {choices}")
        raise ConfigError(f"unknown database: {selector}")


@dataclass(frozen=True)
class ImageState:
    source: str
    digest: str

    @property
    def image(self) -> str:
        return locked_image(self.source, self.digest)


@dataclass(frozen=True)
class RoleState:
    engine: str
    images: dict[str, ImageState] = field(default_factory=dict)
    http_port: int | None = None
    compose_hash: str | None = None
    installed: bool = False
    operations: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MachineState:
    version: int
    host: str
    images: dict[str, ImageState]
    roles: dict[str, RoleState]
    tool_version: str | None = None

    @classmethod
    def empty(cls, host: str) -> MachineState:
        return cls(STATE_VERSION, host, {}, {})


def load(path: str | Path = CONFIG_DIR / "host.yml", *, paths: Paths | None = None) -> Config:
    source = Path(path)
    if not source.is_file():
        raise ConfigError(f"missing source config file: {source.name}")
    if source.name != "host.yml":
        raise ConfigError("source config must be host.yml")
    root = source.parent
    old = [name for name in ("postgres.yml", "kv.yml", "host.lock.json") if (root / name).exists()]
    if old:
        raise ConfigError(f"old source layout is not supported: {old[0]}")
    data = _read_yaml(source)
    if _has_secret(data):
        raise ConfigError("source configuration must not contain secrets or secret references")
    config = _config(_mapping(data, "source"), paths or Paths())
    require_valid(config)
    return config


def dump(config: Config) -> str:
    return yaml.safe_dump(as_dict(config), sort_keys=False)


def as_dict(config: Config) -> dict[str, Any]:
    host = config.host
    data: dict[str, Any] = {
        "host": {
            "id": host.id,
            "domain": host.domain,
            "data_root": str(host.data_root),
            "backup": {
                "repos": dict(host.backup.repos),
                "retention": dict(host.backup.retention),
                "min_free_gb": host.backup.min_free_gb,
                "max_age_hours": host.backup.max_age_hours,
                "test_max_age_days": host.backup.test_max_age_days,
            },
            "routing": {
                "acme_email": host.routing.acme_email,
                "dns_provider": host.routing.dns_provider,
                "traefik_image": host.routing.traefik_image,
            },
            "http_ports": {"start": host.http_port_start, "end": host.http_port_end},
        },
        "projects": {},
    }
    for project in config.projects:
        item: dict[str, Any] = {}
        if project.postgres is not None:
            postgres = project.postgres
            item["postgres"] = {
                "image": postgres.image,
                "pgbouncer": {
                    "enabled": postgres.pgbouncer.enabled,
                    "image": postgres.pgbouncer.image,
                    "max_clients": postgres.pgbouncer.max_clients,
                    "pool_size": postgres.pgbouncer.pool_size,
                    "reserve_size": postgres.pgbouncer.reserve_size,
                },
            }
        if project.kv is not None:
            kv = project.kv
            kv_data: dict[str, Any] = {
                "engine": kv.engine,
                "image": kv.image,
                "mode": kv.mode,
                "http": {
                    "enabled": kv.http.enabled,
                    "image": kv.http.image,
                    "connections": kv.http.connections,
                },
            }
            if kv.http.domain is not None:
                kv_data["http"]["domain"] = kv.http.domain
            if kv.engine == "dragonfly":
                kv_data["memory"] = kv.memory or "256mb"
                kv_data["threads"] = kv.threads or 1
            item["kv"] = kv_data
        data["projects"][project.id] = item
    return data


def append_activity(
    config: Config,
    *,
    command: str,
    database: Database | None = None,
    changed: tuple[str, ...] = (),
    result: str,
    recovery: str | None = None,
    started: str | None = None,
    backup: str | None = None,
    safety_snapshot: str | None = None,
) -> None:
    finished = datetime.now(UTC).isoformat()
    record = {
        "time": finished,
        "started": started or finished,
        "finished": finished,
        "host": config.host.id,
        "project": database.project if database else None,
        "role": database.role if database else None,
        "engine": database.engine if database else None,
        "command": command,
        "changed": sorted(set(changed)),
        "result": result,
        "recovery": recovery,
        "backup": backup,
        "safety_snapshot": safety_snapshot,
    }
    if _has_secret(record):
        raise ConfigError("activity record contains a secret")
    from .lock import lock

    try:
        with lock(config.paths.locks / "activity.lock", timeout=config.host.timeouts["command"]):
            path = config.paths.activity
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, (json.dumps(record, sort_keys=True, default=str) + "\n").encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            data = path.read_bytes()
            lines = data.splitlines(keepends=True)
            if len(lines) > 1000 or len(data) > 1024 * 1024:
                lines = lines[-1000:]
                while lines and sum(map(len, lines)) > 1024 * 1024:
                    lines.pop(0)
                write_bytes(path, b"".join(lines), mode=0o600)
    except BaseException as exc:
        from .log import write as log_write

        with suppress(BaseException):
            log_write(
                "activity_operation",
                host=config.host.id,
                project=database.project if database else None,
                role=database.role if database else None,
                engine=database.engine if database else None,
                command=command,
                step="write",
                result="failed",
                error=str(exc),
            )


def load_state(config: Config) -> MachineState:
    path = config.paths.machine_state
    if not path.exists():
        return MachineState.empty(config.host.id)
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError("invalid machine state") from exc
    if _has_secret(data):
        raise ConfigError("machine state must not contain secrets")
    state = _machine_state(data)
    if state.host != config.host.id:
        raise ConfigError(f"machine state is for {state.host}, not {config.host.id}")
    return state


def require_no_orphans(config: Config, state: MachineState | None = None) -> None:
    current = state or load_state(config)
    configured = {item.identity for item in config.databases}
    installed = {identity for identity, role in current.roles.items() if role.installed}
    for path in config.paths.projects.glob("*/*/compose.yaml"):
        try:
            project, role, name = path.relative_to(config.paths.projects).parts
        except ValueError, OSError:
            continue
        if name == "compose.yaml" and role in {"postgres", "kv"}:
            installed.add(f"{project}/{role}")
    missing = installed - configured
    if missing:
        names = ", ".join(sorted(missing))
        raise ConfigError(
            f"database removal is unsupported; restore source roles before mutation: {names}"
        )


def write_state(config: Config, state: MachineState) -> None:
    if state.host != config.host.id or state.version != STATE_VERSION:
        raise ConfigError("incompatible machine state")
    data = state_dict(state)
    if _has_secret(data):
        raise ConfigError("machine state must not contain secrets")
    write_json(config.paths.machine_state, data, mode=0o600)


def resolve_state(
    config: Config,
    current: MachineState | None = None,
    *,
    resolver=None,
    port_available: Callable[[int], bool] | None = None,
) -> MachineState:
    state = current or MachineState.empty(config.host.id)
    if state.version != STATE_VERSION or state.host != config.host.id:
        raise ConfigError("incompatible machine state")

    resolve = (
        (lambda source: resolve_image(source))
        if resolver is None
        else (lambda source: resolve_image(source, resolver))
    )
    images = dict(state.images)
    source = config.host.routing.traefik_image
    if "traefik" not in images or images["traefik"].source != source:
        images["traefik"] = resolve(source)

    roles = dict(state.roles)
    enabled_http = {
        item.identity
        for item in config.databases
        if item.role == "kv" and item.settings.http.enabled
    }
    owners: dict[int, str] = {}
    for identity, role in roles.items():
        if role.http_port is None:
            continue
        if role.http_port in owners:
            raise ConfigError(
                f"HTTP port {role.http_port} is assigned to both "
                f"{owners[role.http_port]} and {identity}"
            )
        owners[role.http_port] = identity
    used = set(owners)
    configured = {item.identity for item in config.databases}
    outside = {
        identity
        for identity in configured
        if identity in roles
        and roles[identity].http_port is not None
        and not config.host.http_port_start
        <= roles[identity].http_port
        <= config.host.http_port_end
    }
    if outside:
        names = ", ".join(sorted(outside))
        raise ConfigError(
            f"HTTP ports fall outside the configured range for {names}; "
            "expand the range instead of reallocating"
        )
    check_port = port_available or _port_available
    available = (
        port
        for port in range(config.host.http_port_start, config.host.http_port_end + 1)
        if port not in used and check_port(port)
    )
    for database in config.databases:
        existing = roles.get(database.identity)
        if existing is not None and existing.installed and existing.engine != database.engine:
            raise ConfigError(
                f"{database.identity}: changing {existing.engine} to {database.engine} "
                "requires an explicit migration"
            )
        role_images = dict(existing.images) if existing else {}
        sources = {"primary": database.image}
        if database.role == "postgres" and database.settings.pgbouncer.enabled:
            sources["pgbouncer"] = database.settings.pgbouncer.image
        if database.role == "kv" and database.settings.http.enabled:
            sources["http"] = database.settings.http.image
        role_images = {
            name: (
                role_images[name]
                if name in role_images and role_images[name].source == image
                else resolve(image)
            )
            for name, image in sources.items()
        }
        port = existing.http_port if database.role == "kv" and existing is not None else None
        if database.identity in enabled_http and port is None:
            try:
                port = next(available)
            except StopIteration as exc:
                raise ConfigError("no free HTTP ports remain in the configured range") from exc
            used.add(port)
        roles[database.identity] = RoleState(
            database.engine,
            role_images,
            port,
            existing.compose_hash if existing else None,
            existing.installed if existing else False,
            dict(existing.operations) if existing else {},
        )
    return MachineState(state.version, state.host, images, roles, state.tool_version)


def _port_available(port: int) -> bool:
    current = socket.socket()
    try:
        current.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        current.close()


def state_dict(state: MachineState) -> dict[str, Any]:
    return {
        "version": state.version,
        "host": state.host,
        "tool_version": state.tool_version,
        "images": {name: _image_state_dict(item) for name, item in sorted(state.images.items())},
        "roles": {
            name: {
                "engine": role.engine,
                "images": {
                    key: _image_state_dict(value) for key, value in sorted(role.images.items())
                },
                "http_port": role.http_port,
                "compose_hash": role.compose_hash,
                "installed": role.installed,
                "operations": role.operations,
            }
            for name, role in sorted(state.roles.items())
        },
    }


def with_role(config: Config, project_id: str, role: str, settings: Postgres | KV) -> Config:
    if role not in {"postgres", "kv"}:
        raise ConfigError(f"unsupported role: {role}")
    if role == "kv":
        domain = settings.http.domain or project_domain(project_id, config.host)
        settings = replace(
            settings,
            http=replace(settings.http, domain=domain),
            memory=settings.memory or ("256mb" if settings.engine == "dragonfly" else None),
            threads=settings.threads or (1 if settings.engine == "dragonfly" else None),
        )
    projects = list(config.projects)
    for index, project in enumerate(projects):
        if project.id != project_id:
            continue
        current = getattr(project, role)
        if current is not None and current == settings:
            return config
        if current is not None:
            raise ConfigError(f"database already exists: {project_id}/{role}")
        projects[index] = replace(project, **{role: settings})
        break
    else:
        _validate_project_id(project_id)
        projects.append(Project(project_id, **{role: settings}))
    updated = replace(config, projects=tuple(sorted(projects, key=lambda item: item.id)))
    require_valid(updated)
    return updated


def replace_role(config: Config, database: Database, settings: Postgres | KV) -> Config:
    projects = []
    for project in config.projects:
        if project.id == database.project:
            project = replace(project, **{database.role: settings})
        projects.append(project)
    updated = replace(config, projects=tuple(projects))
    require_valid(updated)
    return updated


def validate(config: Config) -> list[str]:
    errors: list[str] = []
    seen_routes: dict[tuple[str, int], str] = {}
    seen_http_domains: dict[str, str] = {}
    databases: list[Database] = []

    def image(value: Any, name: str, *, engine: str | None = None) -> None:
        try:
            validate_source(value, name)
        except ConfigError as exc:
            errors.append(str(exc))
            return
        if engine is not None and image_major(value) is None:
            errors.append(f"{name} tag must start with a positive {engine} major version")

    if not isinstance(config.host, Host):
        return ["host must be a Host"]
    if not isinstance(config.paths, Paths):
        errors.append("paths must be Paths")

    host = config.host
    if not isinstance(host.id, str) or not _NAME.fullmatch(host.id) or len(host.id) > 60:
        errors.append("host.id must be a safe lowercase name of at most 60 characters")
    if (
        not isinstance(host.domain, str)
        or len(host.domain) > 253
        or not _DOMAIN.fullmatch(host.domain)
    ):
        errors.append("host.domain must be a valid lowercase domain")
    if (
        not isinstance(host.data_root, Path)
        or not host.data_root.is_absolute()
        or host.data_root == Path("/")
    ):
        errors.append("host.data_root must be a safe absolute path")

    backup = host.backup
    if not isinstance(backup, BackupSettings):
        errors.append("host.backup must be backup settings")
    else:
        repos = backup.repos
        if (
            not isinstance(repos, dict)
            or set(repos) != {"postgres", "kv"}
            or not all(
                isinstance(key, str) and isinstance(value, str) and value
                for key, value in repos.items()
            )
        ):
            errors.append("host.backup.repos must define non-empty postgres and kv repositories")
        elif _has_secret(repos):
            errors.append("host.backup.repos must not contain credentials")
        retention = backup.retention
        if not isinstance(retention, dict) or set(retention) != set(DEFAULT_RETENTION):
            errors.append("host.backup.retention must define the default retention keys")
        else:
            for name, value in retention.items():
                if type(value) is not int or value < 1:
                    errors.append(f"host.backup.retention.{name} must be a positive integer")
        if type(backup.min_free_gb) is not int or backup.min_free_gb < 0:
            errors.append("host.backup.min_free_gb must be a non-negative integer")
        if type(backup.max_age_hours) is not int or backup.max_age_hours < 1:
            errors.append("host.backup.max_age_hours must be a positive integer")
        if type(backup.test_max_age_days) is not int or backup.test_max_age_days < 1:
            errors.append("host.backup.test_max_age_days must be a positive integer")

    routing = host.routing
    if not isinstance(routing, Routing):
        errors.append("host.routing must be routing settings")
    else:
        if not isinstance(routing.acme_email, str) or not _EMAIL.fullmatch(routing.acme_email):
            errors.append("host.routing.acme_email must be a valid email")
        if not isinstance(routing.dns_provider, str) or not _NAME.fullmatch(routing.dns_provider):
            errors.append("host.routing.dns_provider must be a safe name")
        image(routing.traefik_image, "host.routing.traefik_image")

    if (
        type(host.http_port_start) is not int
        or type(host.http_port_end) is not int
        or host.http_port_start < 1024
        or host.http_port_end > 65535
        or host.http_port_start > host.http_port_end
    ):
        errors.append("host.http_ports must be an ordered unprivileged range")

    if isinstance(config.paths, Paths) and isinstance(host.data_root, Path):
        data_root = host.data_root
        for managed in (config.paths.config, config.paths.state, config.paths.tool):
            if (
                data_root == managed
                or data_root.is_relative_to(managed)
                or managed.is_relative_to(data_root)
            ):
                errors.append(f"host.data_root overlaps managed path {managed}")

    if not isinstance(config.projects, tuple):
        errors.append("projects must be a tuple")
    else:
        seen_projects: set[str] = set()
        for project in config.projects:
            if not isinstance(project, Project):
                errors.append("projects must contain Project values")
                continue
            if not isinstance(project.id, str) or not _NAME.fullmatch(project.id):
                errors.append(f"project {project.id}: must be a safe lowercase name")
            elif project.id in seen_projects:
                errors.append(f"{project.id}: duplicate project")
            else:
                seen_projects.add(project.id)
            if isinstance(project.id, str) and not _ENV.search(project.id):
                errors.append(f"{project.id}: project must end in -dev-N, -test-N, or -prod-N")
            if project.postgres is not None:
                if isinstance(project.postgres, Postgres):
                    databases.append(
                        Database(project.id, "postgres", project.postgres, host, config.paths)
                    )
                else:
                    errors.append(f"{project.id}: postgres must be Postgres settings")
            if project.kv is not None:
                if isinstance(project.kv, KV):
                    databases.append(Database(project.id, "kv", project.kv, host, config.paths))
                else:
                    errors.append(f"{project.id}: kv must be KV settings")

    for database in databases:
        if len(database.compose_project) > 63:
            errors.append(f"{database.identity}: derived Compose project is too long")
        if len(database.domain) > 253 or not _DOMAIN.fullmatch(database.domain):
            errors.append(f"{database.identity}: derived domain is invalid")
        route = (database.domain, database.port)
        previous = seen_routes.get(route)
        if previous:
            errors.append(f"{database.identity}: native route collides with {previous}")
        seen_routes[route] = database.identity
        if isinstance(host.data_root, Path) and (
            not database.data.is_absolute() or database.data == Path("/")
        ):
            errors.append(f"{database.identity}: data path is unsafe")
        if database.role == "kv" and database.settings.engine not in {"redis", "dragonfly"}:
            errors.append(f"{database.identity}: unsupported KV engine")
        if database.role == "postgres":
            image(database.settings.image, f"{database.identity}.image", engine="postgres")
            pool = database.settings.pgbouncer
            if not isinstance(pool, PgBouncer):
                errors.append(f"{database.identity}: pgbouncer must be PgBouncer settings")
            else:
                if type(pool.enabled) is not bool:
                    errors.append(f"{database.identity}: pgbouncer must be true or false")
                for name in ("max_clients", "pool_size", "reserve_size"):
                    value = getattr(pool, name)
                    if type(value) is not int or value < 1:
                        errors.append(f"{database.identity}: {name} must be a positive integer")
                image(pool.image, f"{database.identity}.pgbouncer.image")
        else:
            settings = database.settings
            image(settings.image, f"{database.identity}.image", engine=settings.engine)
            if settings.mode not in {"durable", "cache"}:
                errors.append(f"{database.identity}: mode must be durable or cache")
            if settings.engine == "redis" and (
                settings.memory is not None or settings.threads is not None
            ):
                errors.append(f"{database.identity}: memory and threads require dragonfly")
            if settings.engine == "dragonfly":
                if settings.memory is not None and (
                    not isinstance(settings.memory, str) or not _MEMORY.fullmatch(settings.memory)
                ):
                    errors.append(
                        f"{database.identity}: memory must be a positive kb, mb, or gb value"
                    )
                if settings.threads is not None and (
                    type(settings.threads) is not int or settings.threads < 1
                ):
                    errors.append(f"{database.identity}: threads must be a positive integer")
            http = settings.http
            if not isinstance(http, HTTP):
                errors.append(f"{database.identity}: http must be HTTP settings")
            else:
                if type(http.enabled) is not bool:
                    errors.append(f"{database.identity}: http must be true or false")
                if type(http.connections) is not int or http.connections < 1:
                    errors.append(
                        f"{database.identity}: http_connections must be a positive integer"
                    )
                if http.domain is not None and (
                    not isinstance(http.domain, str)
                    or len(http.domain) > 253
                    or not _DOMAIN.fullmatch(http.domain)
                ):
                    errors.append(f"{database.identity}: HTTP domain is invalid")
                if http.enabled and http.domain is not None:
                    previous = seen_http_domains.get(http.domain)
                    if previous:
                        errors.append(f"{database.identity}: HTTP domain collides with {previous}")
                    seen_http_domains[http.domain] = database.identity
                image(http.image, f"{database.identity}.http.image")
    return errors


def require_valid(config: Config) -> None:
    errors = validate(config)
    if errors:
        raise ConfigError("config failed:\n- " + "\n- ".join(errors))


def project_domain(project: str, host: Host) -> str:
    return f"{project}.{host.id}.{host.domain}"


def _config(data: dict[str, Any], paths: Paths) -> Config:
    if "databases" in data or "instances" in data:
        raise ConfigError("old source schema is not supported; use projects and roles")
    _only(data, {"host", "projects"}, "source")
    host = _host(_mapping(data.get("host"), "host"))
    raw_projects = _mapping(data.get("projects"), "projects")
    projects = []
    for project_id, raw in raw_projects.items():
        _validate_project_id(project_id)
        item = _mapping(raw, f"projects.{project_id}")
        _only(item, {"postgres", "kv"}, f"projects.{project_id}")
        postgres = _postgres(item["postgres"], project_id) if "postgres" in item else None
        kv = _kv(item["kv"], project_id, host) if "kv" in item else None
        projects.append(Project(project_id, postgres, kv))
    return Config(host, tuple(sorted(projects, key=lambda item: item.id)), paths)


def _host(data: dict[str, Any]) -> Host:
    _only(data, {"id", "domain", "data_root", "backup", "routing", "http_ports"}, "host")
    host_id = _required_string(data, "id", "host")
    domain = _required_string(data, "domain", "host").lower()
    data_root = Path(_required_string(data, "data_root", "host"))
    backup_data = _mapping(data.get("backup"), "host.backup")
    _only(
        backup_data,
        {"repos", "retention", "min_free_gb", "max_age_hours", "test_max_age_days"},
        "host.backup",
    )
    repos = _string_dict(backup_data.get("repos"), "host.backup.repos")
    retention = dict(DEFAULT_RETENTION)
    raw_retention = _mapping(backup_data.get("retention", {}), "host.backup.retention")
    _only(raw_retention, set(DEFAULT_RETENTION), "host.backup.retention")
    for name, value in raw_retention.items():
        retention[name] = _positive_int(value, f"host.backup.retention.{name}")
    backup = BackupSettings(
        repos,
        retention,
        _nonnegative_int(backup_data.get("min_free_gb", 5), "host.backup.min_free_gb"),
        _positive_int(backup_data.get("max_age_hours", 26), "host.backup.max_age_hours"),
        _positive_int(backup_data.get("test_max_age_days", 30), "host.backup.test_max_age_days"),
    )
    routing_data = _mapping(data.get("routing"), "host.routing")
    _only(routing_data, {"acme_email", "dns_provider", "traefik_image"}, "host.routing")
    email = _required_string(routing_data, "acme_email", "host.routing")
    provider = _required_string(routing_data, "dns_provider", "host.routing")
    traefik_image = routing_data.get("traefik_image", DEFAULT_IMAGES["traefik"])
    ports = _mapping(data.get("http_ports", {}), "host.http_ports")
    _only(ports, {"start", "end"}, "host.http_ports")
    start = _positive_int(ports.get("start", DEFAULT_HTTP_START), "host.http_ports.start")
    end = _positive_int(ports.get("end", DEFAULT_HTTP_END), "host.http_ports.end")
    return Host(
        host_id,
        domain,
        data_root,
        backup,
        Routing(email, provider, traefik_image),
        start,
        end,
    )


def _postgres(value: Any, project: str) -> Postgres:
    data = _mapping(value, f"projects.{project}.postgres")
    _only(data, {"image", "pgbouncer"}, f"projects.{project}.postgres")
    image = _engine_image(data, "image", "postgres", f"projects.{project}.postgres")
    pool_data = data.get("pgbouncer", {})
    pool = _mapping(pool_data, f"projects.{project}.postgres.pgbouncer")
    _only(
        pool,
        {"enabled", "image", "max_clients", "pool_size", "reserve_size"},
        f"projects.{project}.postgres.pgbouncer",
    )
    enabled = _bool(pool.get("enabled", True), f"projects.{project}.postgres.pgbouncer.enabled")
    sidecar = pool.get("image", DEFAULT_IMAGES["pgbouncer"])
    return Postgres(
        image,
        PgBouncer(
            enabled,
            sidecar,
            _positive_int(pool.get("max_clients", 100), "pgbouncer.max_clients"),
            _positive_int(pool.get("pool_size", 20), "pgbouncer.pool_size"),
            _positive_int(pool.get("reserve_size", 5), "pgbouncer.reserve_size"),
        ),
    )


def _kv(value: Any, project: str, host: Host) -> KV:
    data = _mapping(value, f"projects.{project}.kv")
    _only(data, {"engine", "image", "mode", "http", "memory", "threads"}, f"projects.{project}.kv")
    engine = _required_string(data, "engine", f"projects.{project}.kv")
    if engine not in {"redis", "dragonfly"}:
        raise ConfigError(f"projects.{project}.kv.engine must be redis or dragonfly")
    image = _engine_image(data, "image", engine, f"projects.{project}.kv")
    mode = data.get("mode", "durable")
    if mode not in {"durable", "cache"}:
        raise ConfigError(f"projects.{project}.kv.mode must be durable or cache")
    memory = data.get("memory")
    threads = data.get("threads")
    if engine != "dragonfly" and (memory is not None or threads is not None):
        raise ConfigError(f"projects.{project}.kv: memory and threads require dragonfly")
    if memory is not None and (not isinstance(memory, str) or not _MEMORY.fullmatch(memory)):
        raise ConfigError(f"projects.{project}.kv.memory must be a positive kb, mb, or gb value")
    if threads is not None:
        threads = _positive_int(threads, f"projects.{project}.kv.threads")
    http_data = data.get("http", {})
    http = _mapping(http_data, f"projects.{project}.kv.http")
    _only(http, {"enabled", "image", "connections", "domain"}, f"projects.{project}.kv.http")
    enabled = _bool(http.get("enabled", True), f"projects.{project}.kv.http.enabled")
    http_image = http.get("image", DEFAULT_IMAGES["http"])
    domain = http.get("domain") or project_domain(project, host)
    if domain is not None and (not isinstance(domain, str) or not _DOMAIN.fullmatch(domain)):
        raise ConfigError(f"projects.{project}.kv.http.domain must be a valid domain")
    return KV(
        engine,
        image,
        mode,
        HTTP(
            enabled,
            http_image,
            _positive_int(http.get("connections", DEFAULT_HTTP_CONNECTIONS), "http.connections"),
            domain,
        ),
        memory or ("256mb" if engine == "dragonfly" else None),
        threads or (1 if engine == "dragonfly" else None),
    )


def _engine_image(data: dict[str, Any], key: str, _engine: str, name: str) -> str:
    image = _required_string(data, key, name)
    return image


def _machine_state(value: Any) -> MachineState:
    data = _mapping(value, "machine state")
    _only(data, {"version", "host", "tool_version", "images", "roles"}, "machine state")
    version = _required(data, "version", "machine state")
    if version != STATE_VERSION:
        raise ConfigError(f"unsupported machine state version: {version!r}")
    host = _required_string(data, "host", "machine state")
    images = {
        name: _image_state(item, f"machine state.images.{name}")
        for name, item in _mapping(
            _required(data, "images", "machine state"), "machine state.images"
        ).items()
    }
    roles = {}
    for identity, raw in _mapping(
        _required(data, "roles", "machine state"), "machine state.roles"
    ).items():
        role = _mapping(raw, f"machine state.roles.{identity}")
        _only(
            role,
            {"engine", "images", "http_port", "compose_hash", "installed", "operations"},
            f"machine state.roles.{identity}",
        )
        selector = _selector(identity)
        engine = _required_string(role, "engine", f"machine state.roles.{identity}")
        if (selector[1] == "postgres" and engine != "postgres") or (
            selector[1] == "kv" and engine not in {"redis", "dragonfly"}
        ):
            raise ConfigError(f"machine state role engine does not match {identity}")
        port = _required(role, "http_port", f"machine state.roles.{identity}")
        if port is not None and (type(port) is not int or not 1024 <= port <= 65535):
            raise ConfigError(f"machine state has invalid HTTP port for {identity}")
        operations = _mapping(
            _required(role, "operations", f"machine state.roles.{identity}"),
            f"machine state.roles.{identity}.operations",
        )
        roles[identity] = RoleState(
            engine,
            {
                name: _image_state(item, f"machine state.roles.{identity}.images.{name}")
                for name, item in _mapping(
                    _required(role, "images", f"machine state.roles.{identity}"),
                    f"machine state.roles.{identity}.images",
                ).items()
            },
            port,
            _optional_string(
                _required(role, "compose_hash", f"machine state.roles.{identity}"),
                f"machine state {identity} compose_hash",
            ),
            _bool(
                _required(role, "installed", f"machine state.roles.{identity}"),
                f"machine state {identity} installed",
            ),
            dict(operations),
        )
    tool_version = _optional_string(
        _required(data, "tool_version", "machine state"), "machine state.tool_version"
    )
    return MachineState(version, host, images, roles, tool_version)


def _image_state(value: Any, name: str) -> ImageState:
    data = _mapping(value, name)
    _only(data, {"source", "digest"}, name)
    source = _required_string(data, "source", name)
    validate_source(source, f"{name}.source")
    digest = _required_string(data, "digest", name)
    if source_digest(f"image@{digest}", f"{name}.digest") != digest:
        raise ConfigError(f"{name}.digest is invalid")
    return ImageState(source, digest)


def _image_state_dict(value: ImageState) -> dict[str, Any]:
    return {"source": value.source, "digest": value.digest}


class _UniqueLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _UniqueLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ConfigError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.load(path.read_text(), Loader=_UniqueLoader)
    except ConfigError:
        raise
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"invalid source YAML: {path.name}") from exc


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigError(f"{name} must be an object")
    return value


def _only(data: dict[str, Any], allowed: set[str], name: str) -> None:
    extra = sorted(set(data) - allowed)
    if extra:
        raise ConfigError(f"{name}: unsupported field {extra[0]}")


def _required_string(data: dict[str, Any], key: str, name: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{name}.{key} must be a non-empty string")
    return value


def _required(data: dict[str, Any], key: str, name: str) -> Any:
    if key not in data:
        raise ConfigError(f"{name}.{key} is required")
    return data[key]


def _optional_string(value: Any, name: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ConfigError(f"{name} must be a string or null")
    return value


def _positive_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ConfigError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ConfigError(f"{name} must be a non-negative integer")
    return value


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be a boolean")
    return value


def _string_dict(value: Any, name: str) -> dict[str, str]:
    data = _mapping(value, name)
    if not all(isinstance(item, str) for item in data.values()):
        raise ConfigError(f"{name} values must be strings")
    return dict(data)


def _validate_project_id(value: str) -> None:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ConfigError(f"project {value}: must be a safe lowercase name")
    if not _ENV.search(value):
        raise ConfigError(f"project {value}: must end in -dev-N, -test-N, or -prod-N")


def _selector(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or value.count("/") != 1:
        raise ConfigError(f"invalid database selector: {value}")
    project, role = value.split("/", 1)
    _validate_project_id(project)
    if role not in {"postgres", "kv"}:
        raise ConfigError(f"invalid database selector: {value}")
    return project, role


def _has_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"projects", "roles"} and isinstance(item, dict):
                if any(_has_secret(child) for child in item.values()):
                    return True
                continue
            if any(word in str(key).lower() for word in _SECRET_WORDS):
                return True
            if _has_secret(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_has_secret(item) for item in value)
    elif isinstance(value, str):
        lowered = value.lower()
        return (
            lowered.startswith("op://")
            or "-----begin private key-----" in lowered
            or bool(re.search(r"://[^/@\s]+@", value))
        )
    return False
