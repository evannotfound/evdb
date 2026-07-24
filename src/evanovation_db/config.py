from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .files import write_json

ENGINES = {"postgres", "redis", "dragonfly"}
KV_ENGINES = {"redis", "dragonfly"}


@dataclass(frozen=True)
class Host:
    id: str
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
    runtime: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Host:
        try:
            return cls(
                id=str(data["id"]),
                config_dir=Path(data["config_dir"]),
                state_dir=Path(data["state_dir"]),
                backup_dir=Path(data["backup_dir"]),
                lock_dir=Path(data["lock_dir"]),
                repos=dict(data["repos"]),
                retention={key: int(value) for key, value in data["retention"].items()},
                images=dict(data["images"]),
                resources=dict(data["resources"]),
                secrets=dict(data["secrets"]),
                min_free_gb=int(data.get("min_free_gb", 5)),
                runtime=bool(data.get("runtime", False)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"invalid host config: {exc}") from exc


@dataclass(frozen=True)
class Instance:
    id: str
    env: str
    engine: str
    container: str
    project: str
    data: Path
    domain: str
    durable: bool
    current: dict[str, Any]
    target: dict[str, Any]
    backup: dict[str, Any]
    resources: dict[str, str]
    secrets: dict[str, str]
    settings: dict[str, Any] = field(default_factory=dict)
    http: dict[str, Any] | None = None

    @property
    def group(self) -> str:
        return "postgres" if self.engine == "postgres" else "kv"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Instance:
        try:
            return cls(
                id=str(data["id"]),
                env=str(data["env"]),
                engine=str(data["engine"]),
                container=str(data["container"]),
                project=str(data["project"]),
                data=Path(data["data"]),
                domain=str(data["domain"]),
                durable=bool(data["durable"]),
                current=dict(data["current"]),
                target=dict(data["target"]),
                backup=dict(data["backup"]),
                resources=dict(data["resources"]),
                secrets=dict(data["secrets"]),
                settings=dict(data.get("settings", {})),
                http=dict(data["http"]) if data.get("http") is not None else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            name = data.get("id", "unknown") if isinstance(data, dict) else "unknown"
            raise ConfigError(f"invalid instance {name}: {exc}") from exc


@dataclass(frozen=True)
class Config:
    host: Host
    instances: tuple[Instance, ...]

    def get(self, group: str, name: str) -> Instance:
        for instance in self.instances:
            if instance.group == group and instance.id == name:
                return instance
        raise ConfigError(f"unknown instance: {group}/{name}")


def _read(path: Path) -> Any:
    if path.suffix == ".json":
        return json.loads(path.read_text())
    try:
        import yaml
    except ImportError as exc:
        raise ConfigError("PyYAML is required to read source config") from exc
    return yaml.safe_load(path.read_text())


def load(path: str | Path) -> Config:
    root = Path(path)
    if root.is_file():
        data = _read(root)
        host = Host.from_dict(data["host"])
        instances = tuple(Instance.from_dict(item) for item in data["instances"])
    else:
        host_data = _read(_pick(root, "host"))
        host = Host.from_dict(host_data["host"])
        items: list[dict[str, Any]] = []
        instance_dir = root / "instances"
        if instance_dir.is_dir():
            for item_path in sorted(instance_dir.glob("*.json")):
                items.append(_read(item_path)["instance"])
        else:
            for name in ("postgres", "kv"):
                data = _read(_pick(root, name))
                items.extend(data.get("instances", []))
        instances = tuple(Instance.from_dict(item) for item in items)
    config = Config(host=host, instances=instances)
    require_valid(config)
    return config


def _pick(root: Path, stem: str) -> Path:
    for suffix in (".json", ".yml", ".yaml"):
        path = root / f"{stem}{suffix}"
        if path.exists():
            return path
    raise ConfigError(f"missing config file: {stem}")


def validate(config: Config) -> list[str]:
    errors: list[str] = []
    seen: dict[str, dict[Any, str]] = {
        "id": {},
        "container": {},
        "project": {},
        "domain": {},
        "http port": {},
        "http domain": {},
    }

    for instance in config.instances:
        _unique(errors, seen["id"], (instance.group, instance.id), instance.id, "id")
        _unique(errors, seen["container"], instance.container, instance.id, "container")
        _unique(
            errors,
            seen["project"],
            (instance.group, instance.project),
            instance.id,
            "project",
        )
        _unique(errors, seen["domain"], instance.domain.lower(), instance.id, "domain")

        if instance.engine not in ENGINES:
            errors.append(f"{instance.id}: unknown engine {instance.engine}")
        if instance.current.get("engine") != instance.engine:
            errors.append(f"{instance.id}: current engine does not match")
        if instance.target.get("engine") != instance.engine:
            errors.append(f"{instance.id}: target engine does not match")
        if Path(str(instance.current.get("data", ""))) != instance.data:
            errors.append(f"{instance.id}: current data path does not match")
        if Path(str(instance.target.get("data", ""))) != instance.data:
            errors.append(f"{instance.id}: target data path change is not allowed")
        if not str(instance.current.get("image_id", "")).startswith("sha256:"):
            errors.append(f"{instance.id}: current image id is required")
        _check_image(errors, instance.id, str(instance.target.get("image", "")))

        if instance.durable and not instance.backup.get("enabled"):
            errors.append(f"{instance.id}: durable instance must have backup enabled")
        _check_resources(errors, instance)
        _check_refs(errors, instance.id, instance.secrets, config.host)

        if instance.engine in KV_ENGINES:
            if instance.http is None:
                errors.append(f"{instance.id}: HTTP settings are required")
            else:
                _check_http(errors, seen, instance, config.host)
        elif instance.http is not None:
            errors.append(f"{instance.id}: HTTP settings are only valid for KV")

    if config.host.id == "montreal-01":
        counts = {engine: 0 for engine in ENGINES}
        for instance in config.instances:
            counts[instance.engine] = counts.get(instance.engine, 0) + 1
        expected = {"postgres": 14, "dragonfly": 7, "redis": 4}
        if counts != expected:
            errors.append(f"montreal-01: expected engine counts {expected}, got {counts}")
        enabled = [item for item in config.instances if item.http and item.http.get("enabled")]
        if len(enabled) != 11:
            errors.append(f"montreal-01: expected 11 HTTP sidecars, got {len(enabled)}")

    for key, image in config.host.images.items():
        _check_image(errors, f"host image {key}", image)
    if config.host.resources != {"traefik": "unlimited"}:
        errors.append("host: Traefik resources must preserve the current unlimited setting")
    _check_refs(errors, "host", config.host.secrets, config.host)
    return errors


def _unique(errors: list[str], seen: dict[Any, str], value: Any, name: str, label: str) -> None:
    if value in seen:
        errors.append(f"{name}: duplicate {label} with {seen[value]}")
    else:
        seen[value] = name


def _check_image(errors: list[str], name: str, image: str) -> None:
    if not image or "@sha256:" not in image or ":latest" in image.split("@", 1)[0]:
        errors.append(f"{name}: target image must use a fixed version and digest")


def _check_resources(errors: list[str], instance: Instance) -> None:
    expected = {"database"}
    if instance.engine == "postgres" and instance.settings.get("pgbouncer"):
        expected.add("pgbouncer")
    if instance.http and instance.http.get("enabled"):
        expected.add("http")
    if set(instance.resources) != expected:
        errors.append(f"{instance.id}: resource services must be {sorted(expected)}")
        return
    if any(value != "unlimited" for value in instance.resources.values()):
        errors.append(f"{instance.id}: resources must preserve the current unlimited setting")


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
        errors.append(f"{instance.id}: HTTP enabled must be a boolean")
    if enabled:
        port = http.get("port")
        if not isinstance(port, int) or not 1024 <= port <= 65535:
            errors.append(f"{instance.id}: invalid HTTP loopback port")
        else:
            _unique(errors, seen["http port"], port, instance.id, "HTTP port")
        domain = str(http.get("domain", "")).lower()
        _unique(errors, seen["http domain"], domain, instance.id, "HTTP domain")
        expected = f"{instance.id}.kv-montreal-01.storage.evanovation.com"
        if domain != expected:
            errors.append(f"{instance.id}: target HTTP domain must be {expected}")
        _check_image(errors, f"{instance.id} HTTP", str(http.get("image", "")))
        _check_refs(errors, f"{instance.id} HTTP", {"token": http.get("token")}, host)
        if not isinstance(http.get("max_connections"), int) or http["max_connections"] < 1:
            errors.append(f"{instance.id}: HTTP max_connections must be positive")


def require_valid(config: Config) -> None:
    errors = validate(config)
    if errors:
        raise ConfigError("config failed:\n- " + "\n- ".join(errors))


def render(source: str | Path, output: str | Path) -> Config:
    config = load(source)
    root = Path(output)
    instance_dir = root / "instances"
    instance_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(root / "host.json", {"host": _host_dict(config.host, runtime=True)}, mode=0o600)
    for instance in config.instances:
        write_json(
            instance_dir / f"{instance.group}-{instance.id}.json",
            {"instance": _instance_dict(instance, config.host, runtime=True)},
            mode=0o600,
        )
    return config


def _host_dict(host: Host, *, runtime: bool = False) -> dict[str, Any]:
    data = dict(vars(host))
    for key in ("config_dir", "state_dir", "backup_dir", "lock_dir"):
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
            data["http"] = {
                **instance.http,
                "token": str(secret_dir / f"kv-{instance.id}-http.token"),
            }
    return data
