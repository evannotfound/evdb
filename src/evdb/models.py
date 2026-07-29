from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path("/etc/evdb")
STATE_DIR = Path("/var/lib/evdb")
HTTP_PORT_START = 13379
HTTP_PORT_COUNT = 10000


def http_port(project: str) -> int:
    digest = hashlib.sha256(f"{project}/kv".encode()).digest()
    return HTTP_PORT_START + int.from_bytes(digest[:8], "big") % HTTP_PORT_COUNT


@dataclass(frozen=True)
class Paths:
    config: Path = CONFIG_DIR
    state: Path = STATE_DIR

    @property
    def source(self) -> Path:
        return self.config / "config.yml"

    @property
    def secrets(self) -> Path:
        return self.config / "secrets.yml"

    @property
    def rclone(self) -> Path:
        return self.config / "rclone.conf"

    @property
    def projects(self) -> Path:
        return self.config / "projects"

    @property
    def traefik(self) -> Path:
        return self.config / "traefik"

    @property
    def backups(self) -> Path:
        return self.state / "backups"

    @property
    def locks(self) -> Path:
        return self.state / "locks"

    def role_config(self, project: str, role: str) -> Path:
        return self.projects / project / role

    def compose(self, project: str, role: str) -> Path:
        return self.role_config(project, role) / "compose.yaml"

    def role_backups(self, project: str, role: str) -> Path:
        return self.backups / project / role

    def role_lock(self, project: str, role: str) -> Path:
        return self.locks / project / f"{role}.lock"


@dataclass(frozen=True)
class BackupSettings:
    repository: str
    min_free_gb: int = 5
    max_age_hours: int = 26


@dataclass(frozen=True)
class Routing:
    acme_email: str
    dns_provider: str
    traefik_image: str


@dataclass(frozen=True)
class Host:
    id: str
    domain: str
    data_root: Path
    backup: BackupSettings
    routing: Routing

    @property
    def timeouts(self) -> dict[str, int]:
        return {"command": 300, "health": 120, "backup": 8 * 3600}


@dataclass(frozen=True)
class PgBouncer:
    enabled: bool
    image: str
    max_clients: int
    pool_size: int
    reserve_size: int


@dataclass(frozen=True)
class HTTP:
    enabled: bool
    image: str
    connections: int
    domain: str | None = None


@dataclass(frozen=True)
class Postgres:
    image: str
    pgbouncer: PgBouncer


@dataclass(frozen=True)
class KV:
    engine: str
    image: str
    mode: str
    http: HTTP
    memory: str | None = None
    threads: int | None = None


@dataclass(frozen=True)
class Project:
    id: str
    postgres: Postgres | None = None
    kv: KV | None = None


@dataclass(frozen=True, repr=False)
class RoleSecrets:
    password: str
    http_token: str | None = None

    def __repr__(self) -> str:
        return "RoleSecrets(<redacted>)"


@dataclass(frozen=True, repr=False)
class ProjectSecrets:
    id: str
    postgres: RoleSecrets | None = None
    kv: RoleSecrets | None = None

    def __repr__(self) -> str:
        return f"ProjectSecrets(id={self.id!r}, <redacted>)"


@dataclass(frozen=True, repr=False)
class Secrets:
    restic_password: str
    dns: tuple[tuple[str, str], ...]
    projects: tuple[ProjectSecrets, ...] = ()

    def __repr__(self) -> str:
        return "Secrets(<redacted>)"

    def select(self, project: str, role: str) -> RoleSecrets:
        for item in self.projects:
            if item.id == project:
                value = getattr(item, role, None)
                if value is not None:
                    return value
        from .errors import ConfigError

        raise ConfigError(f"{project}/{role}: matching secrets are missing")

    @property
    def values(self) -> tuple[str, ...]:
        values = [self.restic_password, *(value for _name, value in self.dns)]
        for project in self.projects:
            for role in (project.postgres, project.kv):
                if role is not None:
                    values.append(role.password)
                    if role.http_token:
                        values.append(role.http_token)
        return tuple(dict.fromkeys(item for item in values if item))


@dataclass(frozen=True)
class Database:
    project: str
    role: str
    settings: Postgres | KV
    host: Host
    paths: Paths
    credentials: RoleSecrets = field(repr=False)

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
    def generated(self) -> Path:
        return self.paths.role_config(self.project, self.role)

    @property
    def compose(self) -> Path:
        return self.paths.compose(self.project, self.role)

    @property
    def domain(self) -> str:
        return f"{self.project}.{self.host.id}.{self.host.domain}"

    @property
    def port(self) -> int:
        return 5432 if self.role == "postgres" else 6379

    @property
    def http_port(self) -> int:
        return http_port(self.project)

    def service(self, name: str) -> str:
        return f"evdb-{self.project}-{self.role}-{name}"


@dataclass(frozen=True)
class Config:
    host: Host
    projects: tuple[Project, ...]
    secrets: Secrets = field(repr=False)
    paths: Paths = field(default_factory=Paths)

    @property
    def databases(self) -> tuple[Database, ...]:
        values = []
        for project in self.projects:
            for role in ("postgres", "kv"):
                settings = getattr(project, role)
                if settings is not None:
                    values.append(
                        Database(
                            project.id,
                            role,
                            settings,
                            self.host,
                            self.paths,
                            self.secrets.select(project.id, role),
                        )
                    )
        return tuple(values)

    def select(self, selector: str) -> Database:
        from .errors import ConfigError

        matches = (
            [item for item in self.databases if item.identity == selector]
            if "/" in selector
            else [item for item in self.databases if item.project == selector]
        )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            choices = ", ".join(item.identity for item in matches)
            raise ConfigError(f"ambiguous database {selector}; use one of: {choices}")
        raise ConfigError(f"unknown database: {selector}")
