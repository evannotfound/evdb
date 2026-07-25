from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .files import write_json, write_text
from .run import run

ENGINES = {"postgres", "redis", "dragonfly"}
KV_ENGINES = {"redis", "dragonfly"}
IMAGE_KEYS = {"postgres", "pgbouncer", "redis", "dragonfly", "http", "traefik"}
LOCK_VERSION = 1
LOCK_OS = "linux"
LOCK_ARCH = "amd64"

CONFIG_DIR = Path("/etc/evanovation-db")
STATE_DIR = Path("/var/lib/evanovation-db")
BACKUP_DIR = STATE_DIR / "backups"
LOCK_DIR = STATE_DIR / "locks"
DEFAULT_RETENTION = {"daily": 7, "weekly": 4, "monthly": 12, "data_parts": 12}
DEFAULT_TIMEOUTS = {
    "command": 300,
    "health": 120,
    "backup": 6 * 3600,
    "restore": 8 * 3600,
    "maintenance": 24 * 3600,
}
DEFAULT_HTTP_START = 13379
DEFAULT_HTTP_END = 13478
DEFAULT_HTTP_CONNECTIONS = 20

_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_ENV = re.compile(r"-(dev|test|prod)-[0-9]+$")
_MEMORY = re.compile(r"[1-9][0-9]*(?:kb|mb|gb)")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SSH = re.compile(r"[A-Za-z0-9_.@:-]+")
_DOMAIN = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
)


@dataclass(frozen=True)
class DatabaseSource:
    name: str
    type: str
    mode: str = "durable"
    pooler: bool | None = None
    max_clients: int | None = None
    pool_size: int | None = None
    reserve_size: int | None = None
    memory: str | None = None
    threads: int | None = None
    http: bool | None = None

    @property
    def selector(self) -> str:
        return f"{self.type}/{self.name}"


@dataclass(frozen=True)
class HostSource:
    id: str
    ssh: str
    domain: str
    data_root: Path
    images: dict[str, str]
    repos: dict[str, str]
    vault: str
    system_item: str
    databases: tuple[DatabaseSource, ...]
    retention: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_RETENTION))
    min_free_gb: int = 5
    backup_max_age_hours: int = 26
    restore_max_age_days: int = 30
    http_port_start: int = DEFAULT_HTTP_START
    http_port_end: int = DEFAULT_HTTP_END


@dataclass(frozen=True)
class Host:
    id: str
    ssh: str
    domain: str
    data_root: Path
    config_dir: Path
    state_dir: Path
    backup_dir: Path
    lock_dir: Path
    repos: dict[str, str]
    retention: dict[str, int]
    images: dict[str, str]
    resources: dict[str, str]
    secrets: dict[str, str]
    min_free_gb: int = 5
    backup_max_age_hours: int = 26
    restore_max_age_days: int = 30
    http_port_start: int = DEFAULT_HTTP_START
    http_port_end: int = DEFAULT_HTTP_END
    timeouts: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_TIMEOUTS))
    runtime: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Host:
        try:
            return cls(
                id=_runtime_string(data, "id", "host"),
                ssh=_runtime_string(data, "ssh", "host"),
                domain=_runtime_string(data, "domain", "host"),
                data_root=Path(_runtime_string(data, "data_root", "host")),
                config_dir=Path(_runtime_string(data, "config_dir", "host")),
                state_dir=Path(_runtime_string(data, "state_dir", "host")),
                backup_dir=Path(_runtime_string(data, "backup_dir", "host")),
                lock_dir=Path(_runtime_string(data, "lock_dir", "host")),
                repos=_string_dict(data["repos"], "host.repos"),
                retention=_int_dict(data["retention"], "host.retention"),
                images=_string_dict(data["images"], "host.images"),
                resources=_string_dict(data.get("resources", {}), "host.resources"),
                secrets=_string_dict(data["secrets"], "host.secrets"),
                min_free_gb=_runtime_int(data, "min_free_gb", "host"),
                backup_max_age_hours=_runtime_int(data, "backup_max_age_hours", "host"),
                restore_max_age_days=_runtime_int(data, "restore_max_age_days", "host"),
                http_port_start=_runtime_int(data, "http_port_start", "host"),
                http_port_end=_runtime_int(data, "http_port_end", "host"),
                timeouts=_int_dict(data["timeouts"], "host.timeouts"),
                runtime=data.get("runtime") is True,
            )
        except KeyError as exc:
            raise ConfigError(f"invalid runtime host: missing {exc.args[0]}") from exc


@dataclass(frozen=True)
class Instance:
    id: str
    env: str
    engine: str
    image: str
    port: int
    container: str
    project: str
    data: Path
    domain: str
    durable: bool
    backup: dict[str, Any]
    resources: dict[str, str]
    secrets: dict[str, str]
    settings: dict[str, Any] = field(default_factory=dict)
    http: dict[str, Any] | None = None

    @property
    def group(self) -> str:
        return "postgres" if self.engine == "postgres" else "kv"

    @property
    def selector(self) -> str:
        return f"{self.engine}/{self.id}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Instance:
        name = data.get("id", "unknown") if isinstance(data, dict) else "unknown"
        if not isinstance(data, dict):
            raise ConfigError("invalid runtime instance: expected an object")
        migration = {"current", "target"}.intersection(data)
        if migration:
            field_name = sorted(migration)[0]
            raise ConfigError(f"invalid runtime instance {name}: migration-only field {field_name}")
        try:
            http = data.get("http")
            if http is not None and not isinstance(http, dict):
                raise ConfigError(f"invalid runtime instance {name}: http must be an object")
            settings = data.get("settings", {})
            backup = data["backup"]
            if not isinstance(settings, dict) or not isinstance(backup, dict):
                raise ConfigError(f"invalid runtime instance {name}: invalid settings or backup")
            durable = data["durable"]
            if not isinstance(durable, bool):
                raise ConfigError(f"invalid runtime instance {name}: durable must be a boolean")
            return cls(
                id=_runtime_string(data, "id", f"instance {name}"),
                env=_runtime_string(data, "env", f"instance {name}"),
                engine=_runtime_string(data, "engine", f"instance {name}"),
                image=_runtime_string(data, "image", f"instance {name}"),
                port=_runtime_int(data, "port", f"instance {name}"),
                container=_runtime_string(data, "container", f"instance {name}"),
                project=_runtime_string(data, "project", f"instance {name}"),
                data=Path(_runtime_string(data, "data", f"instance {name}")),
                domain=_runtime_string(data, "domain", f"instance {name}"),
                durable=durable,
                backup=dict(backup),
                resources=_string_dict(data.get("resources", {}), f"instance {name}.resources"),
                secrets=_string_dict(data["secrets"], f"instance {name}.secrets"),
                settings=dict(settings),
                http=dict(http) if http is not None else None,
            )
        except KeyError as exc:
            raise ConfigError(f"invalid runtime instance {name}: missing {exc.args[0]}") from exc


@dataclass(frozen=True)
class Config:
    host: Host
    instances: tuple[Instance, ...]

    def select(self, selector: str) -> Instance:
        if "/" in selector:
            engine, name = selector.split("/", 1)
            if engine not in ENGINES or not name:
                raise ConfigError(f"invalid database selector: {selector}")
            matches = [item for item in self.instances if item.engine == engine and item.id == name]
        else:
            matches = [item for item in self.instances if item.id == selector]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            choices = ", ".join(sorted(item.selector for item in matches))
            raise ConfigError(f"ambiguous database {selector}; use one of: {choices}")
        raise ConfigError(f"unknown database: {selector}")

    def get(self, group: str, name: str) -> Instance:
        if group in ENGINES:
            return self.select(f"{group}/{name}")
        if group != "kv":
            raise ConfigError(f"unknown instance: {group}/{name}")
        matches = [item for item in self.instances if item.group == group and item.id == name]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            choices = ", ".join(sorted(item.selector for item in matches))
            raise ConfigError(f"ambiguous database {name}; use one of: {choices}")
        raise ConfigError(f"unknown instance: {group}/{name}")


@dataclass(frozen=True)
class ImageLock:
    source: str
    digest: str


@dataclass(frozen=True)
class HostLock:
    version: int
    host: str
    os: str
    architecture: str
    images: dict[str, ImageLock]
    http_ports: dict[str, int]

    @classmethod
    def empty(cls, host: str) -> HostLock:
        return cls(LOCK_VERSION, host, LOCK_OS, LOCK_ARCH, {}, {})

    @classmethod
    def from_dict(cls, data: Any) -> HostLock:
        if not isinstance(data, dict):
            raise ConfigError("invalid host lock: expected an object")
        if _has_secret(data):
            raise ConfigError("invalid host lock: secret data is not allowed")
        _only(data, {"version", "host", "platform", "images", "http_ports"}, "host lock")
        version = data.get("version")
        if type(version) is not int or version != LOCK_VERSION:
            raise ConfigError(f"invalid host lock: unsupported version {version!r}")
        host = _required_string(data, "host", "host lock")
        platform = _mapping(data.get("platform"), "host lock.platform")
        _only(platform, {"os", "architecture"}, "host lock.platform")
        os_name = _required_string(platform, "os", "host lock.platform")
        architecture = _required_string(platform, "architecture", "host lock.platform")
        if (os_name, architecture) != (LOCK_OS, LOCK_ARCH):
            raise ConfigError(f"invalid host lock: platform must be {LOCK_OS}/{LOCK_ARCH}")

        image_data = _mapping(data.get("images"), "host lock.images")
        if set(image_data) != IMAGE_KEYS:
            raise ConfigError("invalid host lock: image entries are incomplete")
        images: dict[str, ImageLock] = {}
        for name, raw in image_data.items():
            if name not in IMAGE_KEYS:
                raise ConfigError(f"invalid host lock: unknown image {name}")
            item = _mapping(raw, f"host lock.images.{name}")
            _only(item, {"source", "digest"}, f"host lock.images.{name}")
            source = _required_string(item, "source", f"host lock.images.{name}")
            digest = _required_string(item, "digest", f"host lock.images.{name}")
            if not _DIGEST.fullmatch(digest):
                raise ConfigError(f"invalid host lock: invalid digest for image {name}")
            source_digest = _source_image_digest(source, f"host lock.images.{name}.source")
            if source_digest is not None and source_digest != digest:
                raise ConfigError(f"invalid host lock: source digest differs for image {name}")
            images[name] = ImageLock(source, digest)

        port_data = _mapping(data.get("http_ports"), "host lock.http_ports")
        ports: dict[str, int] = {}
        used: dict[int, str] = {}
        for selector, port in port_data.items():
            _lock_selector(selector)
            if type(port) is not int or not 1024 <= port <= 65535:
                raise ConfigError(f"invalid host lock: invalid HTTP port for {selector}")
            if port in used:
                raise ConfigError(
                    f"invalid host lock: duplicate HTTP port for {selector} and {used[port]}"
                )
            used[port] = selector
            ports[selector] = port
        return cls(version, host, os_name, architecture, images, ports)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "host": self.host,
            "platform": {"os": self.os, "architecture": self.architecture},
            "images": {
                name: {"source": item.source, "digest": item.digest}
                for name, item in sorted(self.images.items())
            },
            "http_ports": dict(sorted(self.http_ports.items())),
        }


def load_source(path: str | Path) -> HostSource:
    source_path = _source_path(Path(path))
    root = source_path.parent
    old = [name for name in ("postgres.yml", "kv.yml") if (root / name).exists()]
    if old:
        raise ConfigError("old source layout is not supported; move all databases into host.yml")
    data = _read_yaml(source_path)
    source = _mapping(data, "source")
    if "instances" in source:
        raise ConfigError("old source field instances is not supported; use databases")
    _only(source, {"host", "databases"}, "source")
    host_data = _mapping(source.get("host"), "host")
    databases_data = source.get("databases")
    if not isinstance(databases_data, list):
        raise ConfigError("databases must be a list")
    databases = tuple(_database(item, index) for index, item in enumerate(databases_data))
    result = _host(host_data, databases)
    _validate_sources(result)
    return result


def load_lock(path: str | Path, source: HostSource | None = None) -> HostLock:
    lock_path = Path(path)
    if not lock_path.is_file():
        raise ConfigError(f"missing generated host lock: {lock_path.name}")
    try:
        data = json.loads(lock_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"invalid generated host lock: {lock_path.name}") from exc
    lock = HostLock.from_dict(data)
    if source is not None and lock.host != source.id:
        raise ConfigError(f"host lock is for {lock.host}, not configured host {source.id}")
    return lock


def write_lock(path: str | Path, lock: HostLock) -> None:
    HostLock.from_dict(lock.as_dict())
    write_json(path, lock.as_dict(), mode=0o644)


def resolve_image_digest(
    image: str,
    *,
    os_name: str = LOCK_OS,
    architecture: str = LOCK_ARCH,
    timeout: int = 120,
) -> str:
    result = run(
        ["docker", "manifest", "inspect", "--verbose", image],
        timeout=timeout,
    )
    try:
        data = json.loads(result.out)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"image {image}: docker returned invalid manifest JSON") from exc
    matches = []
    for item in _manifest_items(data):
        descriptor = item.get("Descriptor") if isinstance(item.get("Descriptor"), dict) else {}
        platform = item.get("Platform") or descriptor.get("platform") or item.get("platform")
        if not isinstance(platform, dict):
            continue
        if platform.get("os") != os_name or platform.get("architecture") != architecture:
            continue
        digest = item.get("Digest") or descriptor.get("digest") or item.get("digest")
        if isinstance(digest, str) and _DIGEST.fullmatch(digest):
            matches.append(digest)
    unique = sorted(set(matches))
    if len(unique) != 1:
        raise ConfigError(
            f"image {image}: expected one {os_name}/{architecture} manifest, found {len(unique)}"
        )
    return unique[0]


def resolve_lock(
    source: HostSource,
    current: HostLock | None = None,
    *,
    resolver: Callable[..., str] = resolve_image_digest,
) -> HostLock:
    lock = current or HostLock.empty(source.id)
    if lock.host != source.id:
        raise ConfigError(f"host lock is for {lock.host}, not configured host {source.id}")
    if (lock.version, lock.os, lock.architecture) != (LOCK_VERSION, LOCK_OS, LOCK_ARCH):
        raise ConfigError("host lock version or platform is incompatible")

    images: dict[str, ImageLock] = {}
    for name in sorted(IMAGE_KEYS):
        image = source.images[name]
        existing = lock.images.get(name)
        if existing is not None and existing.source == image:
            images[name] = existing
            continue
        digest = _source_image_digest(image, f"host.images.{name}")
        if digest is None:
            digest = resolver(image, os_name=LOCK_OS, architecture=LOCK_ARCH)
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise ConfigError(f"image {name}: resolver returned an invalid digest")
        images[name] = ImageLock(image, digest)

    enabled = {
        item.selector
        for item in source.databases
        if item.type in KV_ENGINES and item.http is not False
    }
    outside = {
        selector: port
        for selector, port in lock.http_ports.items()
        if selector in enabled and not source.http_port_start <= port <= source.http_port_end
    }
    if outside:
        selectors = ", ".join(sorted(outside))
        raise ConfigError(
            f"locked HTTP ports fall outside the configured range for {selectors}; "
            "expand the range instead of reallocating surviving databases"
        )
    ports = {selector: port for selector, port in lock.http_ports.items() if selector in enabled}
    used = set(ports.values())
    free = (
        port for port in range(source.http_port_start, source.http_port_end + 1) if port not in used
    )
    for selector in sorted(enabled - ports.keys()):
        try:
            port = next(free)
        except StopIteration as exc:
            raise ConfigError("no free HTTP ports remain in the configured range") from exc
        ports[selector] = port
        used.add(port)
    return HostLock(LOCK_VERSION, source.id, LOCK_OS, LOCK_ARCH, images, ports)


def normalize(source: HostSource, lock: HostLock) -> Config:
    errors = _lock_errors(source, lock)
    if errors:
        raise ConfigError("config lock failed:\n- " + "\n- ".join(errors))
    images = {
        name: _locked_image(source.images[name], lock.images[name].digest)
        for name in sorted(IMAGE_KEYS)
    }
    vault = source.vault
    system = source.system_item
    host = Host(
        id=source.id,
        ssh=source.ssh,
        domain=source.domain,
        data_root=source.data_root,
        config_dir=CONFIG_DIR,
        state_dir=STATE_DIR,
        backup_dir=BACKUP_DIR,
        lock_dir=LOCK_DIR,
        repos=dict(source.repos),
        retention=dict(source.retention),
        images=images,
        resources={"traefik": "unlimited"},
        secrets={
            "restic_password": f"op://{vault}/{system}/restic-password",
            "rclone_config": f"op://{vault}/{system}/rclone-config",
        },
        min_free_gb=source.min_free_gb,
        backup_max_age_hours=source.backup_max_age_hours,
        restore_max_age_days=source.restore_max_age_days,
        http_port_start=source.http_port_start,
        http_port_end=source.http_port_end,
    )
    instances = tuple(_normalize_database(source, item, lock, images) for item in source.databases)
    config = Config(host, instances)
    require_valid(config)
    return config


def load(path: str | Path) -> Config:
    root = Path(path)
    if root.is_dir() and (root / "host.json").is_file() and not (root / "host.yml").exists():
        return _load_runtime(root)
    source_path = _source_path(root)
    source = load_source(source_path)
    lock = load_lock(source_path.parent / "host.lock.json", source)
    return normalize(source, lock)


def source_path(path: str | Path) -> Path:
    return _source_path(Path(path))


def add_database(path: str | Path, engine: str, name: str) -> bool:
    target = source_path(path)
    source = load_source(target)
    item = _database({"name": name, "type": engine}, len(source.databases))
    if any(current.selector == item.selector for current in source.databases):
        return False
    updated = replace(source, databases=(*source.databases, item))
    _validate_sources(updated)

    data = _mapping(_read_yaml(target), "source")
    databases = data.get("databases")
    if not isinstance(databases, list):
        raise ConfigError("databases must be a list")
    databases.append({"name": name, "type": engine})
    write_text(target, _dump_yaml(data), mode=0o644)
    return True


def validate(config: Config) -> list[str]:
    errors: list[str] = []
    seen: dict[str, dict[Any, str]] = {
        "identity": {},
        "container": {},
        "project": {},
        "domain": {},
        "http port": {},
        "http domain": {},
    }
    for instance in config.instances:
        _unique(
            errors,
            seen["identity"],
            (instance.engine, instance.id),
            instance.selector,
            "typed identity",
        )
        _unique(errors, seen["container"], instance.container, instance.selector, "container")
        _unique(
            errors,
            seen["project"],
            (instance.group, instance.project),
            instance.selector,
            "project",
        )
        _unique(
            errors,
            seen["domain"],
            instance.domain.lower(),
            instance.selector,
            "domain",
        )
        if instance.engine not in ENGINES:
            errors.append(f"{instance.id}: unsupported database type {instance.engine}")
        if not _NAME.fullmatch(instance.id):
            errors.append(f"{instance.id}: unsafe database name")
        _check_locked_image(errors, instance.selector, instance.image)
        if not instance.data.is_absolute():
            errors.append(f"{instance.selector}: data path must be absolute")
        if instance.durable != bool(instance.backup.get("enabled")):
            errors.append(f"{instance.selector}: backup setting does not match durability")
        _check_refs(errors, instance.selector, instance.secrets, config.host)
        if instance.engine in KV_ENGINES:
            if instance.http is None:
                errors.append(f"{instance.selector}: HTTP settings are required")
            else:
                _check_http(errors, seen, instance, config.host)
        elif instance.http is not None:
            errors.append(f"{instance.selector}: HTTP settings are only valid for KV")

    if set(config.host.images) != IMAGE_KEYS:
        errors.append(
            "host: images must define postgres, pgbouncer, redis, dragonfly, http, traefik"
        )
    for name, image in config.host.images.items():
        _check_locked_image(errors, f"host image {name}", image)
    _check_refs(errors, "host", config.host.secrets, config.host)
    return errors


def require_valid(config: Config) -> None:
    errors = validate(config)
    if errors:
        raise ConfigError("config failed:\n- " + "\n- ".join(errors))


def render(source: str | Path, output: str | Path) -> Config:
    config = load(source)
    render_runtime(config, output)
    return config


def runtime_data(config: Config) -> dict[str, Any]:
    return {
        "host": _host_dict(config.host, runtime=True),
        "instances": [
            _instance_dict(item, config.host, runtime=True)
            for item in sorted(config.instances, key=lambda current: current.selector)
        ],
    }


def from_runtime(data: Any) -> Config:
    wrapper = _mapping(data, "runtime config")
    _only(wrapper, {"host", "instances"}, "runtime config")
    host = Host.from_dict(_mapping(wrapper.get("host"), "runtime config.host"))
    instances_data = wrapper.get("instances")
    if not isinstance(instances_data, list):
        raise ConfigError("runtime config.instances must be a list")
    instances = tuple(Instance.from_dict(item) for item in instances_data)
    config = Config(host, instances)
    require_valid(config)
    return config


def render_runtime(config: Config, output: str | Path) -> None:
    root = Path(output)
    instance_dir = root / "instances"
    instance_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    wanted = {f"{item.group}-{item.id}.json" for item in config.instances}
    for stale in instance_dir.glob("*.json"):
        if stale.name not in wanted:
            stale.unlink()
    data = runtime_data(config)
    write_json(root / "host.json", {"host": data["host"]}, mode=0o600)
    for item in data["instances"]:
        instance = Instance.from_dict(item)
        write_json(
            instance_dir / f"{instance.group}-{instance.id}.json",
            {"instance": item},
            mode=0o600,
        )


def _host(data: dict[str, Any], databases: tuple[DatabaseSource, ...]) -> HostSource:
    _only(
        data,
        {
            "id",
            "ssh",
            "domain",
            "data_root",
            "images",
            "backup",
            "one_password",
            "http_ports",
        },
        "host",
    )
    host_id = _required_string(data, "id", "host")
    if not _NAME.fullmatch(host_id):
        raise ConfigError("host.id must be a safe lowercase name")
    ssh = _required_string(data, "ssh", "host")
    if not _SSH.fullmatch(ssh):
        raise ConfigError("host.ssh contains unsupported characters")
    domain = _required_string(data, "domain", "host").lower()
    if not _DOMAIN.fullmatch(domain):
        raise ConfigError("host.domain must be a valid lowercase domain")
    data_root = Path(_required_string(data, "data_root", "host"))
    if not data_root.is_absolute():
        raise ConfigError("host.data_root must be an absolute path")

    image_data = _mapping(data.get("images"), "host.images")
    if set(image_data) != IMAGE_KEYS:
        raise ConfigError(
            "host.images must define postgres, pgbouncer, redis, dragonfly, http, and traefik"
        )
    images = {}
    for name in sorted(IMAGE_KEYS):
        image = _required_string(image_data, name, "host.images")
        _source_image_digest(image, f"host.images.{name}")
        if name in ENGINES and image_major(image) is None:
            raise ConfigError(
                f"host.images.{name} tag must start with a positive engine major version"
            )
        images[name] = image

    backup = _mapping(data.get("backup"), "host.backup")
    _only(
        backup,
        {"repos", "retention", "min_free_gb", "max_age_hours", "restore_max_age_days"},
        "host.backup",
    )
    repos = _string_dict(backup.get("repos"), "host.backup.repos")
    if set(repos) != {"postgres", "kv"} or any(not value for value in repos.values()):
        raise ConfigError("host.backup.repos must define non-empty postgres and kv repositories")
    retention_data = backup.get("retention", {})
    retention_map = _mapping(retention_data, "host.backup.retention")
    _only(retention_map, set(DEFAULT_RETENTION), "host.backup.retention")
    retention = dict(DEFAULT_RETENTION)
    for name, value in retention_map.items():
        retention[name] = _positive_int(value, f"host.backup.retention.{name}")

    one_password = _mapping(data.get("one_password"), "host.one_password")
    _only(one_password, {"vault", "system_item"}, "host.one_password")
    vault = _op_segment(one_password, "vault")
    system_item = _op_segment(one_password, "system_item")

    port_data = _mapping(data.get("http_ports", {}), "host.http_ports")
    _only(port_data, {"start", "end"}, "host.http_ports")
    start = _positive_int(port_data.get("start", DEFAULT_HTTP_START), "host.http_ports.start")
    end = _positive_int(port_data.get("end", DEFAULT_HTTP_END), "host.http_ports.end")
    if start < 1024 or end > 65535 or start > end:
        raise ConfigError("host.http_ports must be an ordered unprivileged port range")

    return HostSource(
        id=host_id,
        ssh=ssh,
        domain=domain,
        data_root=data_root,
        images=images,
        repos=repos,
        vault=vault,
        system_item=system_item,
        databases=databases,
        retention=retention,
        min_free_gb=_positive_int(backup.get("min_free_gb", 5), "host.backup.min_free_gb"),
        backup_max_age_hours=_positive_int(
            backup.get("max_age_hours", 26), "host.backup.max_age_hours"
        ),
        restore_max_age_days=_positive_int(
            backup.get("restore_max_age_days", 30), "host.backup.restore_max_age_days"
        ),
        http_port_start=start,
        http_port_end=end,
    )


def _database(data: Any, index: int) -> DatabaseSource:
    item = _mapping(data, f"databases[{index}]")
    migration = {
        "id",
        "env",
        "engine",
        "container",
        "project",
        "data",
        "domain",
        "durable",
        "current",
        "target",
        "backup",
        "resources",
        "secrets",
        "settings",
    }.intersection(item)
    if migration:
        name = item.get("name", f"index {index}")
        field_name = sorted(migration)[0]
        raise ConfigError(
            f"database {name}: migration-only field {field_name}; use the concise name/type schema"
        )
    allowed = {
        "name",
        "type",
        "mode",
        "pooler",
        "max_clients",
        "pool_size",
        "reserve_size",
        "memory",
        "threads",
        "http",
    }
    _only(item, allowed, f"databases[{index}]")
    name = _required_string(item, "name", f"databases[{index}]")
    if not _NAME.fullmatch(name):
        raise ConfigError(f"database {name}: name must be a safe lowercase identifier")
    if _ENV.search(name) is None:
        raise ConfigError(f"database {name}: name must end in -dev-N, -test-N, or -prod-N")
    engine = _required_string(item, "type", f"database {name}")
    if engine not in ENGINES:
        raise ConfigError(f"database {name}: unsupported type {engine}")
    mode = item.get("mode", "durable")
    if mode not in {"durable", "cache"}:
        raise ConfigError(f"database {name}: mode must be durable or cache")
    if engine == "postgres" and mode != "durable":
        raise ConfigError(f"database {name}: cache mode is only valid for KV")

    pool_fields = {"pooler", "max_clients", "pool_size", "reserve_size"}.intersection(item)
    if engine != "postgres" and pool_fields:
        raise ConfigError(f"database {name}: pool settings are only valid for postgres")
    dragonfly_fields = {"memory", "threads"}.intersection(item)
    if engine != "dragonfly" and dragonfly_fields:
        raise ConfigError(f"database {name}: memory and threads are only valid for dragonfly")
    if engine == "postgres" and "http" in item:
        raise ConfigError(f"database {name}: HTTP is only valid for KV")

    pooler = _optional_bool(item, "pooler", f"database {name}")
    http = _optional_bool(item, "http", f"database {name}")
    memory = item.get("memory")
    if memory is not None and (not isinstance(memory, str) or not _MEMORY.fullmatch(memory)):
        raise ConfigError(f"database {name}: memory must be a positive kb, mb, or gb value")
    return DatabaseSource(
        name=name,
        type=engine,
        mode=mode,
        pooler=pooler,
        max_clients=_optional_positive_int(item, "max_clients", f"database {name}"),
        pool_size=_optional_positive_int(item, "pool_size", f"database {name}"),
        reserve_size=_optional_positive_int(item, "reserve_size", f"database {name}"),
        memory=memory,
        threads=_optional_positive_int(item, "threads", f"database {name}"),
        http=http,
    )


def _validate_sources(source: HostSource) -> None:
    seen_identity: set[tuple[str, str]] = set()
    seen_container: dict[str, str] = {}
    seen_project: dict[tuple[str, str], str] = {}
    seen_domain: dict[str, str] = {}
    for item in source.databases:
        identity = (item.type, item.name)
        if identity in seen_identity:
            raise ConfigError(f"database {item.selector}: duplicate typed identity")
        seen_identity.add(identity)
        group = "postgres" if item.type == "postgres" else "kv"
        container = f"{item.name}-{'postgres' if group == 'postgres' else 'redis'}-1"
        project = (group, item.name)
        domain = f"{item.name}.{group}-{source.id}.{source.domain}"
        for value, seen, label in (
            (container, seen_container, "container"),
            (project, seen_project, "project"),
            (domain, seen_domain, "domain"),
        ):
            if value in seen:
                raise ConfigError(
                    f"database {item.selector}: derived {label} collides with {seen[value]}"
                )
            seen[value] = item.selector


def _normalize_database(
    source: HostSource,
    item: DatabaseSource,
    lock: HostLock,
    images: dict[str, str],
) -> Instance:
    group = "postgres" if item.type == "postgres" else "kv"
    env_match = _ENV.search(item.name)
    assert env_match is not None
    enabled_http = item.type in KV_ENGINES and item.http is not False
    durable = item.mode == "durable"
    if item.type == "postgres":
        pooler = item.pooler is not False
        settings: dict[str, Any] = {
            "user": "default",
            "database": "postgres",
            "pgbouncer": pooler,
            "max_clients": item.max_clients or 100,
            "pool_size": item.pool_size or 20,
            "reserve_size": item.reserve_size or 5,
        }
        resources = {"database": "unlimited"}
        if pooler:
            resources["pgbouncer"] = "unlimited"
        http = None
    else:
        if item.type == "dragonfly":
            settings = {
                "threads": item.threads or 1,
                "maxmemory": item.memory or "256mb",
                "cache": item.mode == "cache",
            }
        else:
            settings = {"cache": True} if item.mode == "cache" else {}
        resources = {"database": "unlimited"}
        if enabled_http:
            resources["http"] = "unlimited"
        http = {
            "enabled": enabled_http,
            "port": lock.http_ports.get(item.selector),
            "domain": f"{item.name}.kv-{source.id}.{source.domain}",
            "image": images["http"],
            "max_connections": DEFAULT_HTTP_CONNECTIONS,
        }
        if enabled_http:
            http["token"] = f"op://{source.vault}/{item.name}-kv/http-token"
    suffix = "postgres" if group == "postgres" else "redis"
    return Instance(
        id=item.name,
        env=env_match.group(1),
        engine=item.type,
        image=images[item.type],
        port=5432 if item.type == "postgres" else 6379,
        container=f"{item.name}-{suffix}-1",
        project=item.name,
        data=source.data_root / group / item.name / "data",
        domain=f"{item.name}.{group}-{source.id}.{source.domain}",
        durable=durable,
        backup={"enabled": durable},
        resources=resources,
        secrets={"password": f"op://{source.vault}/{item.name}-{group}/password"},
        settings=settings,
        http=http,
    )


def _lock_errors(source: HostSource, lock: HostLock) -> list[str]:
    errors = []
    if lock.host != source.id:
        errors.append(f"lock host {lock.host} does not match {source.id}")
    if (lock.version, lock.os, lock.architecture) != (LOCK_VERSION, LOCK_OS, LOCK_ARCH):
        errors.append("lock version or platform is incompatible")
    if set(lock.images) != IMAGE_KEYS:
        errors.append("lock images are incomplete")
    else:
        for name in sorted(IMAGE_KEYS):
            if lock.images[name].source != source.images[name]:
                errors.append(f"image {name} source changed; refresh host.lock.json")
            source_digest = _source_image_digest(source.images[name], f"host.images.{name}")
            if source_digest is not None and lock.images[name].digest != source_digest:
                errors.append(f"image {name} digest changed; refresh host.lock.json")
    expected = {
        item.selector
        for item in source.databases
        if item.type in KV_ENGINES and item.http is not False
    }
    missing = expected - lock.http_ports.keys()
    stale = lock.http_ports.keys() - expected
    if missing:
        errors.append("missing HTTP ports for " + ", ".join(sorted(missing)))
    if stale:
        errors.append("stale HTTP ports for " + ", ".join(sorted(stale)))
    for selector, port in lock.http_ports.items():
        if not source.http_port_start <= port <= source.http_port_end:
            errors.append(f"HTTP port for {selector} is outside the configured range")
    return errors


def _load_runtime(root: Path) -> Config:
    try:
        host_data = json.loads((root / "host.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError("invalid runtime host.json") from exc
    host_wrapper = _mapping(host_data, "runtime host")
    _only(host_wrapper, {"host"}, "runtime host")
    host = Host.from_dict(_mapping(host_wrapper.get("host"), "runtime host.host"))
    instance_dir = root / "instances"
    if not instance_dir.is_dir():
        raise ConfigError("missing runtime instances directory")
    instances = []
    for path in sorted(instance_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"invalid runtime instance file: {path.name}") from exc
        wrapper = _mapping(raw, f"runtime {path.name}")
        _only(wrapper, {"instance"}, f"runtime {path.name}")
        instances.append(Instance.from_dict(_mapping(wrapper.get("instance"), path.name)))
    config = Config(host, tuple(sorted(instances, key=lambda item: item.selector)))
    require_valid(config)
    return config


def _host_dict(host: Host, *, runtime: bool = False) -> dict[str, Any]:
    data = dict(vars(host))
    for key in ("data_root", "config_dir", "state_dir", "backup_dir", "lock_dir"):
        data[key] = str(data[key])
    if runtime:
        data["runtime"] = True
        data["secrets"] = {
            "restic_password": str(host.config_dir / "secrets/restic_password"),
            "rclone_config": str(host.state_dir / "rclone/rclone.conf"),
        }
    return data


def _instance_dict(
    instance: Instance, host: Host | None = None, *, runtime: bool = False
) -> dict[str, Any]:
    data = dict(vars(instance))
    data["data"] = str(instance.data)
    if runtime:
        assert host is not None
        secret_dir = host.config_dir / "secrets"
        data["secrets"] = {"password": str(secret_dir / f"{instance.group}-{instance.id}.password")}
        if instance.http is not None:
            data["http"] = dict(instance.http)
            if instance.http.get("enabled"):
                data["http"]["token"] = str(secret_dir / f"kv-{instance.id}-http.token")
            else:
                data["http"].pop("token", None)
    return data


def _manifest_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    manifests = data.get("manifests")
    if isinstance(manifests, list):
        return [item for item in manifests if isinstance(item, dict)]
    return [data]


def _locked_image(source: str, digest: str) -> str:
    return f"{source.rsplit('@', 1)[0]}@{digest}"


def _source_image_digest(image: str, name: str) -> str | None:
    if any(character.isspace() for character in image):
        raise ConfigError(f"{name} must use an explicit non-latest tag or sha256 digest")
    base = image
    digest = None
    if "@" in image:
        if image.count("@") != 1:
            raise ConfigError(f"{name} must use a valid sha256 digest reference")
        base, digest = image.rsplit("@", 1)
        if not base or not _DIGEST.fullmatch(digest):
            raise ConfigError(f"{name} must use a valid sha256 digest reference")
    leaf = base.rsplit("/", 1)[-1]
    tag = leaf.rsplit(":", 1)[1] if ":" in leaf else None
    if tag is not None and tag.lower() == "latest":
        raise ConfigError(f"{name} must not use the latest tag")
    if digest is None and not tag:
        raise ConfigError(f"{name} must use an explicit non-latest tag or sha256 digest")
    return digest


def image_major(image: str) -> int | None:
    source = image.split("@", 1)[0]
    leaf = source.rsplit("/", 1)[-1]
    if ":" not in leaf:
        return None
    tag = leaf.rsplit(":", 1)[1]
    match = re.match(r"v?([0-9]+)(?:[._-]|$)", tag)
    if match is None:
        return None
    major = int(match.group(1))
    return major if major > 0 else None


def _source_path(path: Path) -> Path:
    if path.is_dir():
        source = path / "host.yml"
        if not source.is_file():
            raise ConfigError("missing source config file: host.yml")
        return source
    if not path.is_file():
        raise ConfigError(f"missing source config file: {path.name}")
    if path.suffix not in {".yml", ".yaml"}:
        raise ConfigError("source config must be YAML")
    return path


def _read_yaml(path: Path) -> Any:
    try:
        import yaml
    except ImportError as exc:
        raise ConfigError("PyYAML is required to read source config") from exc
    try:
        return yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"invalid source YAML: {path.name}") from exc


def _dump_yaml(data: Any) -> str:
    try:
        import yaml
    except ImportError as exc:
        raise ConfigError("PyYAML is required to write source config") from exc
    return yaml.safe_dump(data, sort_keys=False)


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


def _runtime_string(data: dict[str, Any], key: str, name: str) -> str:
    return _required_string(data, key, name)


def _runtime_int(data: dict[str, Any], key: str, name: str) -> int:
    value = data.get(key)
    if type(value) is not int:
        raise ConfigError(f"{name}.{key} must be an integer")
    return value


def _positive_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ConfigError(f"{name} must be a positive integer")
    return value


def _optional_positive_int(data: dict[str, Any], key: str, name: str) -> int | None:
    if key not in data:
        return None
    return _positive_int(data[key], f"{name}.{key}")


def _optional_bool(data: dict[str, Any], key: str, name: str) -> bool | None:
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, bool):
        raise ConfigError(f"{name}.{key} must be a boolean")
    return value


def _string_dict(value: Any, name: str) -> dict[str, str]:
    data = _mapping(value, name)
    if not all(isinstance(item, str) for item in data.values()):
        raise ConfigError(f"{name} values must be strings")
    return dict(data)


def _int_dict(value: Any, name: str) -> dict[str, int]:
    data = _mapping(value, name)
    if not all(type(item) is int for item in data.values()):
        raise ConfigError(f"{name} values must be integers")
    return dict(data)


def _op_segment(data: dict[str, Any], key: str) -> str:
    value = _required_string(data, key, "host.one_password")
    if "/" in value or "\n" in value or value.startswith("op://"):
        raise ConfigError(f"host.one_password.{key} must be an item name, not a secret reference")
    return value


def _lock_selector(selector: str) -> None:
    if not isinstance(selector, str) or "/" not in selector:
        raise ConfigError("invalid host lock: malformed HTTP selector")
    engine, name = selector.split("/", 1)
    if engine not in KV_ENGINES or not _NAME.fullmatch(name):
        raise ConfigError(f"invalid host lock: malformed HTTP selector {selector}")


def _has_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(word in lowered for word in ("password", "token", "secret")):
                return True
            if _has_secret(item):
                return True
    elif isinstance(value, list):
        return any(_has_secret(item) for item in value)
    elif isinstance(value, str):
        return value.startswith("op://")
    return False


def _unique(errors: list[str], seen: dict[Any, str], value: Any, name: str, label: str) -> None:
    if value in seen:
        errors.append(f"{name}: duplicate {label} with {seen[value]}")
    else:
        seen[value] = name


def _check_locked_image(errors: list[str], name: str, image: str) -> None:
    if "@" not in image or not _DIGEST.fullmatch(image.rsplit("@", 1)[-1]):
        errors.append(f"{name}: image must use a locked sha256 digest")


def _check_refs(errors: list[str], name: str, refs: dict[str, Any], host: Host) -> None:
    for key, value in refs.items():
        if host.runtime and _runtime_secret(host, value):
            continue
        if not host.runtime and isinstance(value, str) and value.startswith("op://"):
            continue
        if host.runtime:
            errors.append(f"{name}: {key} must be a protected runtime path")
        else:
            errors.append(f"{name}: {key} must be an op:// reference")


def _runtime_secret(host: Host, value: Any) -> bool:
    if not isinstance(value, str) or not Path(value).is_absolute():
        return False
    path = Path(value)
    roots = (host.config_dir / "secrets", host.state_dir / "rclone")
    return any(path == root or root in path.parents for root in roots)


def _check_http(
    errors: list[str], seen: dict[str, dict[Any, str]], instance: Instance, host: Host
) -> None:
    assert instance.http is not None
    http = instance.http
    enabled = http.get("enabled")
    if not isinstance(enabled, bool):
        errors.append(f"{instance.selector}: HTTP enabled must be a boolean")
        return
    if not enabled:
        if http.get("port") is not None:
            errors.append(f"{instance.selector}: disabled HTTP must not allocate a port")
        return
    port = http.get("port")
    if type(port) is not int or not host.http_port_start <= port <= host.http_port_end:
        errors.append(f"{instance.selector}: invalid HTTP loopback port")
    else:
        _unique(errors, seen["http port"], port, instance.selector, "HTTP port")
    domain = http.get("domain")
    if not isinstance(domain, str) or not domain:
        errors.append(f"{instance.selector}: invalid HTTP domain")
    else:
        _unique(errors, seen["http domain"], domain.lower(), instance.selector, "HTTP domain")
        if domain.lower() != instance.domain.lower():
            errors.append(f"{instance.selector}: HTTP domain must match the KV domain")
    _check_locked_image(errors, f"{instance.selector} HTTP", str(http.get("image", "")))
    _check_refs(errors, f"{instance.selector} HTTP", {"token": http.get("token")}, host)
    if type(http.get("max_connections")) is not int or http["max_connections"] < 1:
        errors.append(f"{instance.selector}: HTTP max_connections must be positive")
