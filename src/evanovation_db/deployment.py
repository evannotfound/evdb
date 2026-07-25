from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from . import __version__, ansible
from .config import (
    Config,
    HostLock,
    Instance,
    from_runtime,
    image_major,
    load,
    load_lock,
    render_runtime,
    runtime_data,
    write_lock,
)
from .errors import DeploymentError, Error
from .files import read_json, write_bytes, write_json, write_text
from .lock import operation
from .run import run
from .secrets import SecretFile
from .secrets import protected as protected_values
from .secrets import read as read_secret

VERSION = 4
AUDIT_VERSION = 1
HISTORY_VERSION = 1
ROLLBACK_VERSION = 1
ROOT = Path("/opt/evanovation-db")
MANIFEST = "release.json"
SOURCE = Path(__file__).parent
SYSTEMD_SOURCE = SOURCE.parents[1] / "systemd"
UNIT_DIR = Path("/etc/systemd/system")
NETWORK = "traefik-net"
TRAEFIK_PROJECT = "evanovation-db-traefik"
TRAEFIK_CONTAINER = "evanovation-db-traefik"
TRAEFIK_COMPOSE = "compose/traefik/compose.json"
CONTRACT_LABEL = "com.evanovation-db.service-hash"
_HASH = re.compile(r"[0-9a-f]{64}")
_RELEASE = re.compile(r"release-[A-Za-z0-9][A-Za-z0-9.-]{0,126}")
_PLAN_KINDS = {"create", "update", "restart", "pending", "drift", "blocked"}
_AFFECTED_KINDS = {"create", "update", "restart", "drift"}
UNIT_NAMES = (
    "evanovation-db-backup@.service",
    "evanovation-db-backup@.timer",
    "evanovation-db-status.service",
    "evanovation-db-status.timer",
    "evanovation-db-restore.service",
    "evanovation-db-restore.timer",
    "evanovation-db-weekly@.service",
    "evanovation-db-weekly@.timer",
    "evanovation-db-monthly@.service",
    "evanovation-db-monthly@.timer",
)


@dataclass(frozen=True)
class Bundle:
    config: Config
    lock: HostLock
    runtime: dict[str, Any]
    files: dict[str, str]
    manifest: dict[str, Any]


@dataclass(frozen=True, repr=False)
class _FileState:
    path: Path
    existed: bool
    content: bytes | None
    mode: int | None
    uid: int | None
    gid: int | None

    def __repr__(self) -> str:
        return f"_FileState(path={self.path!r}, content=<redacted>)"


class _ActivationError(DeploymentError):
    def __init__(self, message: str, *, recovery_failed: bool):
        super().__init__(message)
        self.recovery_failed = recovery_failed


def build(config: Config, host_lock: HostLock, source_hash: str) -> Bundle:
    if not isinstance(source_hash, str) or not _HASH.fullmatch(source_hash):
        raise DeploymentError("source configuration hash is invalid")
    runtime = runtime_data(config)
    runtime_config = from_runtime(runtime)
    files, service_hashes, expected_services, infrastructure_hash = _assets(config)
    inventory, variables = ansible.data(runtime_config)
    files["ansible/inventory.json"] = json.dumps(inventory, sort_keys=True, indent=2) + "\n"
    files["ansible/variables.json"] = json.dumps(variables, sort_keys=True, indent=2) + "\n"

    instances = {item.selector: item for item in config.instances}
    runtime_instances = {item["engine"] + "/" + item["id"]: item for item in runtime["instances"]}
    databases = {}
    for selector, instance in sorted(instances.items()):
        path = _compose_name(instance)
        service_hash = service_hashes[selector]
        databases[selector] = {
            "engine": instance.engine,
            "major": _major(instance.image),
            "project": instance.project,
            "container": instance.container,
            "image": instance.image,
            "compose": path,
            "service_hash": service_hash,
            "config_hash": _digest(runtime_instances[selector]),
            "labels": {CONTRACT_LABEL: service_hash},
            "services": expected_services[selector],
        }

    selector = host_selector(config)
    body = {
        "version": VERSION,
        "host": config.host.id,
        "source_hash": source_hash,
        "runtime_hash": _digest(runtime),
        "lock_hash": _digest(host_lock.as_dict()),
        "files_hash": _digest(files),
        "code_hash": _tree_digest(SOURCE),
        "controller_version": __version__,
        "runtime_version": __version__,
        "infrastructure": {
            "selector": selector,
            "network": {"name": NETWORK, "contract_hash": _digest({"name": NETWORK})},
            "traefik": {
                "project": TRAEFIK_PROJECT,
                "container": TRAEFIK_CONTAINER,
                "image": config.host.images["traefik"],
                "compose": TRAEFIK_COMPOSE,
                "service_hash": infrastructure_hash,
                "labels": {CONTRACT_LABEL: infrastructure_hash},
            },
        },
        "databases": databases,
    }
    release = "release-" + _digest(body)[:20]
    manifest = {**body, "id": release}
    return Bundle(config, host_lock, runtime, files, manifest)


def compose(config: Config) -> dict[str, str]:
    files, _, _, _ = _assets(config)
    return files


def host_selector(config: Config) -> str:
    return f"host/{config.host.id}"


def _assets(
    config: Config,
) -> tuple[dict[str, str], dict[str, str], dict[str, dict[str, Any]], str]:
    files = {TRAEFIK_COMPOSE: json.dumps(_traefik(config), sort_keys=True, indent=2) + "\n"}
    for name in UNIT_NAMES:
        path = SYSTEMD_SOURCE / name
        if not path.is_file():
            raise DeploymentError(f"canonical systemd unit is missing: {name}")
        files[f"systemd/{name}"] = path.read_text()
    for instance in config.instances:
        files[_compose_name(instance)] = (
            json.dumps(_project(config, instance), sort_keys=True, indent=2) + "\n"
        )
        if instance.engine == "postgres" and instance.settings.get("pgbouncer") is True:
            files[_pool_name(instance)] = _pool_config(instance)

    service_hashes = {}
    for instance in config.instances:
        path = _compose_name(instance)
        service_files = {path: files[path]}
        pool = _pool_name(instance)
        if instance.engine == "postgres" and pool in files:
            service_files[pool] = files[pool]
        service_hashes[instance.selector] = _digest(service_files)
    infrastructure_hash = _digest({"network": {"name": NETWORK}, "compose": files[TRAEFIK_COMPOSE]})

    for instance in config.instances:
        _add_contract_labels(files, _compose_name(instance), service_hashes[instance.selector])
    _add_contract_label(files, TRAEFIK_COMPOSE, TRAEFIK_CONTAINER, infrastructure_hash)
    expected_services = {
        instance.selector: _expected_services(files[_compose_name(instance)], instance)
        for instance in config.instances
    }
    return files, service_hashes, expected_services, infrastructure_hash


def _add_contract_labels(files: dict[str, str], name: str, contract_hash: str) -> None:
    data = json.loads(files[name])
    services = data.get("services")
    if not isinstance(services, dict) or not services:
        raise DeploymentError(f"managed services are missing from generated Compose: {name}")
    for service in services.values():
        if not isinstance(service, dict):
            raise DeploymentError(f"managed service is invalid in generated Compose: {name}")
        service.setdefault("labels", {})[CONTRACT_LABEL] = contract_hash
    files[name] = json.dumps(data, sort_keys=True, indent=2) + "\n"


def _add_contract_label(
    files: dict[str, str], name: str, container: str, contract_hash: str
) -> None:
    data = json.loads(files[name])
    service = next(
        (item for item in data["services"].values() if item.get("container_name") == container),
        None,
    )
    if service is None:
        raise DeploymentError(f"managed service is missing from generated Compose: {container}")
    labels = service.setdefault("labels", {})
    labels[CONTRACT_LABEL] = contract_hash
    files[name] = json.dumps(data, sort_keys=True, indent=2) + "\n"


def _expected_services(text: str, instance: Instance) -> dict[str, dict[str, str]]:
    data = json.loads(text)
    result = {}
    for name, service in sorted(data["services"].items()):
        container = service.get("container_name")
        image = service.get("image")
        if not isinstance(container, str) or not isinstance(image, str):
            raise DeploymentError(
                f"managed service identity is invalid: {instance.selector}/{name}"
            )
        result[name] = {
            "container": container,
            "image": image,
            "health": "engine" if container == instance.container else "docker",
        }
    if sum(item["health"] == "engine" for item in result.values()) != 1:
        raise DeploymentError(f"managed primary service is invalid: {instance.selector}")
    return result


def request(
    bundle: Bundle,
    affected: tuple[str, ...],
    secret_files: tuple[SecretFile, ...],
    expected: str | None,
    plan: dict[str, Any],
) -> dict[str, Any]:
    return {
        "manifest": bundle.manifest,
        "runtime": bundle.runtime,
        "lock": bundle.lock.as_dict(),
        "files": bundle.files,
        "secrets": [
            {"path": str(item.path), "content": item.content, "preserve": item.preserve}
            for item in secret_files
        ],
        "affected": list(affected),
        "expected": expected,
        "plan": plan,
    }


def validate_request(payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) != {
        "manifest",
        "runtime",
        "lock",
        "files",
        "secrets",
        "affected",
        "expected",
        "plan",
    }:
        raise DeploymentError("apply payload fields are invalid")
    config = from_runtime(payload["runtime"])
    host_lock = HostLock.from_dict(payload["lock"])
    if host_lock.host != config.host.id:
        raise DeploymentError("apply lock is for another host")
    files = payload["files"]
    if (
        not isinstance(files, dict)
        or not files
        or not all(
            isinstance(path, str) and isinstance(text, str) and _safe_name(path)
            for path, text in files.items()
        )
    ):
        raise DeploymentError("apply release files are invalid")
    manifest = payload["manifest"]
    if not isinstance(manifest, dict):
        raise DeploymentError("apply release manifest is invalid")
    source_hash = manifest.get("source_hash")
    if not isinstance(source_hash, str) or not _HASH.fullmatch(source_hash):
        raise DeploymentError("apply source hash is invalid")
    expected_bundle = build(config, host_lock, source_hash)
    if files != expected_bundle.files or payload["manifest"] != expected_bundle.manifest:
        raise DeploymentError("apply release assets do not match normalized configuration")

    affected = payload["affected"]
    if (
        not isinstance(affected, list)
        or not all(isinstance(item, str) for item in affected)
        or len(set(affected)) != len(affected)
        or not set(affected).issubset(
            {*expected_bundle.manifest["databases"], host_selector(config)}
        )
    ):
        raise DeploymentError("apply affected projects are invalid")
    if payload["expected"] is not None and not isinstance(payload["expected"], str):
        raise DeploymentError("apply predecessor is invalid")
    _validate_plan(payload["plan"], set(affected))
    _validate_secrets(config, payload["secrets"])


def apply(config: Config, payload: dict[str, Any]) -> dict[str, Any]:
    validate_request(payload)
    candidate = from_runtime(payload["runtime"])
    for name in ("config_dir", "state_dir", "backup_dir", "lock_dir"):
        if getattr(candidate.host, name) != getattr(config.host, name):
            raise DeploymentError(f"apply cannot change the managed host {name}")
    timeout = candidate.host.timeouts["command"]
    with operation(candidate.host, write=True, timeout=timeout):
        current = active()
        observed = state(candidate)
        if observed["release"] != payload["expected"]:
            raise DeploymentError("active release changed after confirmation; run plan again")
        prior_release = current[0] if current is not None else None
        prior_manifest = current[1] if current is not None else None
        prior_config = (
            _load_prior_config(candidate, prior_release, prior_manifest)
            if prior_release is not None and prior_manifest is not None
            else None
        )
        deployed = observed["manifest"]["databases"] if observed["manifest"] else {}
        desired = payload["manifest"]["databases"]
        removed = set(deployed) - set(desired)
        if removed:
            selectors = ", ".join(sorted(removed))
            raise DeploymentError(
                f"database removal is blocked for {selectors}; use a future retirement workflow"
            )
        required = _affected(
            desired,
            deployed,
            observed["live"],
            payload["manifest"]["infrastructure"],
            observed["manifest"]["infrastructure"] if observed["manifest"] else None,
            observed["infrastructure"],
        )
        if set(payload["affected"]) != required:
            raise DeploymentError("affected projects changed after confirmation; run plan again")

        release = stage(payload)
        _record_stage(candidate, payload)
        protected = _payload_secrets(payload["secrets"])
        secret_state: tuple[_FileState, ...] = ()
        try:
            secret_state = _snapshot_files(Path(item["path"]) for item in payload["secrets"])
            _reject_deployed_secret_changes(candidate, prior_manifest, payload["secrets"])
        except (Error, OSError):
            _finish_apply(candidate, release.name, "failed", recovered=False)
            raise
        try:
            _install_secrets(payload["secrets"])
            _validate_compose(
                release,
                payload["manifest"],
                timeout=timeout,
                protected=protected,
            )
        except (Error, OSError) as exc:
            restore_failures = _restore_files(secret_state)
            outcome = "recovery_failed" if restore_failures else "failed"
            _finish_apply(candidate, release.name, outcome, recovered=not restore_failures)
            if restore_failures:
                raise DeploymentError(
                    "staged release validation failed and prior secret files could not be "
                    "restored; inspect release history for recovery steps"
                ) from exc
            raise DeploymentError("staged release validation failed") from exc

        try:
            if payload["affected"]:
                _ensure_network(timeout=timeout)
            for selector in _ordered(candidate, payload["affected"]):
                _up(
                    candidate,
                    release,
                    payload["manifest"],
                    selector,
                    timeout=timeout,
                    protected=protected,
                )
                _health(candidate, payload["manifest"], selector)
            _activate_release(candidate, release)
        except (Error, OSError) as exc:
            secret_failures = _restore_files(secret_state)
            failures = _recover(
                candidate,
                release,
                payload["manifest"],
                prior_config if not secret_failures else None,
                prior_release if not secret_failures else None,
                prior_manifest if not secret_failures else None,
                payload["affected"],
                timeout=timeout,
                protected=protected,
            )
            failures.extend(f"secret:{path}" for path in secret_failures)
            if isinstance(exc, _ActivationError) and exc.recovery_failed:
                failures.append("activation")
            if failures:
                _finish_apply(candidate, release.name, "recovery_failed", recovered=False)
                raise DeploymentError(
                    "staged release failed health checks and prior service recovery also failed; "
                    "both releases were retained; inspect release history for recovery steps"
                ) from exc
            _finish_apply(candidate, release.name, "failed", recovered=True)
            raise DeploymentError(
                "staged release failed health checks; prior services were restored and remain "
                "active"
            ) from exc

        _record_activation(
            candidate,
            release.name,
            payload["expected"],
            operation_name="apply",
        )
        return {
            "release": payload["manifest"]["id"],
            "affected": list(payload["affected"]),
        }


def _load_prior_config(candidate: Config, release: Path, manifest: dict[str, Any]) -> Config:
    try:
        return load(release / "runtime")
    except (Error, OSError) as exc:
        if (
            manifest.get("host") != candidate.host.id
            or manifest.get("runtime_version") == __version__
            or not isinstance(manifest.get("databases"), dict)
        ):
            raise DeploymentError("prior release runtime configuration is invalid") from exc
        return candidate


def stage(payload: dict[str, Any]) -> Path:
    validate_request(payload)
    release_id = payload["manifest"]["id"]
    releases = ROOT / "releases"
    releases.mkdir(parents=True, exist_ok=True, mode=0o755)
    target = releases / release_id
    if target.exists():
        if target.is_symlink() or not target.is_dir():
            raise DeploymentError("staged release path is invalid")
        _validate_stage(target, payload)
        _validate_frozen(target)
        return target

    partial = releases / f".{release_id}.partial"
    if partial.exists():
        if partial.is_symlink() or not partial.is_dir():
            raise DeploymentError("partial release path is invalid")
        _thaw(partial)
        shutil.rmtree(partial)
    partial.mkdir(mode=0o700)
    code = partial / "src/evanovation_db"
    shutil.copytree(
        SOURCE,
        code,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    render_runtime(from_runtime(payload["runtime"]), partial / "runtime")
    write_lock(partial / "host.lock.json", HostLock.from_dict(payload["lock"]))
    for name, text in payload["files"].items():
        write_text(partial / name, text, mode=0o640)
    write_json(partial / MANIFEST, payload["manifest"], mode=0o644)
    _validate_stage(partial, payload)
    _freeze(partial)
    partial.replace(target)
    return target


def active() -> tuple[Path, dict[str, Any]] | None:
    current = ROOT / "current"
    try:
        target = Path(os.readlink(current))
    except OSError:
        return None
    if not target.is_absolute():
        target = current.parent / target
    target = target.resolve()
    releases = (ROOT / "releases").resolve()
    if target.parent != releases or not target.is_dir():
        raise DeploymentError("active release pointer is invalid")
    manifest = _manifest(target)
    return target, manifest


def state(config: Config) -> dict[str, Any]:
    current = active()
    if current is None:
        return {"release": None, "manifest": None, "infrastructure": {}, "live": {}}
    _, manifest = current
    if manifest["host"] != config.host.id:
        raise DeploymentError("active release is for another host")
    infrastructure = _infrastructure_state(config, manifest)
    live = {}
    for selector, database in manifest["databases"].items():
        live[selector] = database_state(config, database)
    return {
        "release": manifest["id"],
        "manifest": manifest,
        "infrastructure": infrastructure,
        "live": live,
    }


def infrastructure_state(config: Config, manifest: dict[str, Any]) -> dict[str, Any]:
    return _infrastructure_state(config, manifest)


def history(config: Config) -> dict[str, Any]:
    with operation(config.host, timeout=config.host.timeouts["command"]):
        current = active()
        active_id = current[1]["id"] if current is not None else None
        rows = []
        releases = ROOT / "releases"
        if releases.is_dir():
            for path in sorted(releases.iterdir()):
                if path.is_symlink() or not path.is_dir() or not _RELEASE.fullmatch(path.name):
                    continue
                manifest = _manifest(path)
                if manifest["host"] != config.host.id:
                    continue
                audit = _read_audit(config, path.name, required=False)
                rows.append(_history_row(manifest, audit, active_id))
        rows.sort(key=lambda item: (item["created_at"] or "", item["id"]), reverse=True)
        return {"version": HISTORY_VERSION, "active": active_id, "releases": rows}


def rollback_plan(config: Config, selected: str | None) -> dict[str, Any]:
    if selected is not None:
        _validate_release_id(selected, "rollback release")
    with operation(config.host, timeout=config.host.timeouts["command"]):
        return _rollback_plan(config, selected)


def validate_rollback_request(payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) != {"release", "expected"}:
        raise DeploymentError("rollback payload fields are invalid")
    _validate_release_id(payload["release"], "rollback release")
    _validate_release_id(payload["expected"], "rollback predecessor")


def rollback(config: Config, payload: dict[str, Any]) -> dict[str, Any]:
    validate_rollback_request(payload)
    timeout = config.host.timeouts["command"]
    with operation(config.host, write=True, timeout=timeout):
        plan = _rollback_plan(config, payload["release"])
        if plan["active"] != payload["expected"]:
            raise DeploymentError("active release changed after confirmation; plan rollback again")
        if plan["release"] != payload["release"]:
            raise DeploymentError(
                "rollback release changed after confirmation; plan rollback again"
            )
        if not plan["activate"]:
            return {
                "release": plan["release"],
                "previous": plan["active"],
                "affected": [],
            }

        current = active()
        assert current is not None
        current_release, current_manifest = current
        target_release = _release_path(plan["release"])
        target_manifest = _manifest(target_release)
        current_config = load(current_release / "runtime")
        target_config = load(target_release / "runtime")
        changes = plan["changes"]
        affected = [item["selector"] for item in changes]
        _start_event(
            config,
            target_release.name,
            "rollback",
            plan["active"],
            {"actions": changes, "blocked": []},
        )

        try:
            for item in _ordered_changes(config, changes):
                if item["action"] == "stop":
                    _stop(
                        current_config,
                        current_release,
                        current_manifest,
                        item["selector"],
                        timeout=timeout,
                        protected=(),
                    )
            if changes:
                _ensure_network(timeout=timeout)
            for item in _ordered_changes(config, changes):
                if item["action"] == "stop":
                    continue
                _up(
                    target_config,
                    target_release,
                    target_manifest,
                    item["selector"],
                    timeout=timeout,
                    protected=(),
                )
                _health(target_config, target_manifest, item["selector"])
            _activate_release(target_config, target_release)
        except (Error, OSError) as exc:
            failures = _recover(
                target_config,
                target_release,
                target_manifest,
                current_config,
                current_release,
                current_manifest,
                affected,
                timeout=timeout,
                protected=(),
            )
            if isinstance(exc, _ActivationError) and exc.recovery_failed:
                failures.append("activation")
            status = "recovery_failed" if failures else "failed"
            _finish_event(
                config,
                target_release.name,
                "rollback",
                status,
                recovered=not failures,
            )
            if failures:
                raise DeploymentError(
                    "rollback failed and current service recovery also failed; both releases were "
                    "retained; inspect release history for recovery steps"
                ) from exc
            raise DeploymentError(
                "rollback failed health checks; current services were restored and remain active"
            ) from exc

        _record_activation(
            target_config,
            target_release.name,
            current_manifest["id"],
            operation_name="rollback",
        )
        return {
            "release": target_release.name,
            "previous": current_manifest["id"],
            "affected": affected,
        }


def compose_path(release: Path, manifest: dict[str, Any], selector: str) -> Path:
    infrastructure = manifest.get("infrastructure")
    if isinstance(infrastructure, dict) and selector == infrastructure.get("selector"):
        service = infrastructure.get("traefik")
        name = service.get("compose") if isinstance(service, dict) else None
    else:
        try:
            name = manifest["databases"][selector]["compose"]
        except (KeyError, TypeError) as exc:
            raise DeploymentError(f"service is absent from active release: {selector}") from exc
    if not isinstance(name, str) or not _safe_name(name):
        raise DeploymentError("active release Compose path is invalid")
    path = release / name
    if not path.is_file():
        raise DeploymentError(f"active release Compose file is missing: {selector}")
    return path


def health(
    config: Config,
    instance: Instance,
    *,
    contract_hash: str | None = None,
    services: dict[str, Any] | None = None,
) -> None:
    deadline = time.monotonic() + config.host.timeouts["health"]
    while True:
        containers_ok = (
            _services_healthy(config, services, contract_hash)
            if services is not None
            else _container_healthy(config, instance, contract_hash=contract_hash)
        )
        if containers_ok and engine_healthy(config, instance):
            return
        if time.monotonic() >= deadline:
            raise DeploymentError(f"database did not become healthy: {instance.selector}")
        time.sleep(1)


def infrastructure_health(config: Config, manifest: dict[str, Any]) -> None:
    expected = manifest["infrastructure"]["traefik"]["service_hash"]
    deadline = time.monotonic() + config.host.timeouts["health"]
    while True:
        live = _infrastructure_state(config, manifest)
        traefik = live["traefik"]
        if (
            live["network"]["exists"] is True
            and traefik["running"] is True
            and traefik["healthy"] is True
            and traefik["service_hash"] == expected
        ):
            return
        if time.monotonic() >= deadline:
            raise DeploymentError("Traefik release infrastructure did not become healthy")
        time.sleep(1)


def _health(config: Config, manifest: dict[str, Any], selector: str) -> None:
    if selector == manifest["infrastructure"]["selector"]:
        infrastructure_health(config, manifest)
        return
    instance = config.select(selector)
    health(
        config,
        instance,
        contract_hash=manifest["databases"][selector]["service_hash"],
        services=manifest["databases"][selector]["services"],
    )


def _up(
    config: Config,
    release: Path,
    manifest: dict[str, Any],
    selector: str,
    *,
    timeout: int,
    protected: tuple[str, ...],
) -> None:
    path = compose_path(release, manifest, selector)
    run(
        [*_compose_command(path, _project_name(config, manifest, selector)), "up", "-d"],
        timeout=timeout,
        secrets=protected,
    )


def _stop(
    config: Config,
    release: Path,
    manifest: dict[str, Any],
    selector: str,
    *,
    timeout: int,
    protected: tuple[str, ...],
) -> None:
    path = compose_path(release, manifest, selector)
    run(
        [*_compose_command(path, _project_name(config, manifest, selector)), "stop"],
        timeout=timeout,
        secrets=protected,
    )


def _project_name(config: Config, manifest: dict[str, Any], selector: str) -> str:
    if selector == manifest["infrastructure"]["selector"]:
        return manifest["infrastructure"]["traefik"]["project"]
    return manifest["databases"][selector]["project"]


def _ordered(config: Config, selectors: list[str] | tuple[str, ...]) -> list[str]:
    host = host_selector(config)
    return sorted(selectors, key=lambda item: (item != host, item))


def _ordered_changes(config: Config, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    host = host_selector(config)
    return sorted(changes, key=lambda item: (item["selector"] != host, item["selector"]))


def _ensure_network(*, timeout: int) -> None:
    result = run(["docker", "network", "inspect", NETWORK], timeout=timeout, check=False)
    if result.code == 0:
        return
    detail = (result.err or result.out).lower()
    if detail and "not found" not in detail and "no such network" not in detail:
        raise DeploymentError("shared Docker network inspection failed")
    run(["docker", "network", "create", NETWORK], timeout=timeout)


def _infrastructure_state(config: Config, manifest: dict[str, Any]) -> dict[str, Any]:
    network = run(
        ["docker", "network", "inspect", NETWORK],
        timeout=config.host.timeouts["health"],
        check=False,
    )
    traefik = _container_state(
        manifest["infrastructure"]["traefik"]["container"],
        timeout=config.host.timeouts["health"],
        health=True,
    )
    return {"network": {"exists": network.code == 0}, "traefik": traefik}


def database_state(config: Config, database: dict[str, Any]) -> dict[str, Any]:
    services = {}
    for name, expected in database["services"].items():
        services[name] = _container_state(
            expected["container"],
            timeout=config.host.timeouts["health"],
            health=expected["health"] == "docker",
        )
    return {"services": services}


def _container_state(name: str, *, timeout: int, health: bool = False) -> dict[str, Any]:
    result = run(["docker", "inspect", name], timeout=timeout, check=False)
    value: dict[str, Any] = {
        "running": False,
        "healthy": False if health else None,
        "image": None,
        "service_hash": None,
    }
    if result.code != 0:
        return value
    try:
        items = json.loads(result.out)
        item = items[0] if isinstance(items, list) and len(items) == 1 else None
        if not isinstance(item, dict):
            return value
        state = item.get("State") if isinstance(item.get("State"), dict) else {}
        config = item.get("Config") if isinstance(item.get("Config"), dict) else {}
        labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
        value.update(
            {
                "running": state.get("Running") is True,
                "image": config.get("Image") if isinstance(config.get("Image"), str) else None,
                "service_hash": labels.get(CONTRACT_LABEL)
                if isinstance(labels.get(CONTRACT_LABEL), str)
                else None,
            }
        )
        if health:
            health_data = state.get("Health") if isinstance(state.get("Health"), dict) else None
            value["healthy"] = bool(
                value["running"]
                and health_data is not None
                and health_data.get("Status") == "healthy"
            )
    except (json.JSONDecodeError, AttributeError, IndexError, TypeError):
        return value
    return value


def _project(config: Config, instance: Instance) -> dict[str, Any]:
    secret_dir = config.host.config_dir / "secrets"
    service = f"{instance.id}-{'postgres' if instance.engine == 'postgres' else 'redis'}"
    database: dict[str, Any] = {
        "image": instance.image,
        "container_name": instance.container,
        "restart": "unless-stopped",
        "volumes": [
            f"{instance.data}:"
            f"{'/var/lib/postgresql/data' if instance.engine == 'postgres' else '/data'}"
        ],
        "networks": ["traefik-net"],
    }
    services: dict[str, Any] = {service: database}
    prefix = "pg" if instance.engine == "postgres" else "kv"
    route = {
        "traefik.enable": "true",
        "traefik.docker.network": "traefik-net",
        f"traefik.tcp.routers.{prefix}-{instance.id}.entrypoints": (
            "postgres" if instance.engine == "postgres" else "redis"
        ),
        f"traefik.tcp.routers.{prefix}-{instance.id}.rule": f"HostSNI(`{instance.domain}`)",
        f"traefik.tcp.routers.{prefix}-{instance.id}.tls": "true",
        f"traefik.tcp.services.{prefix}-{instance.id}.loadbalancer.server.port": str(instance.port),
    }
    if instance.engine == "postgres":
        database["environment"] = {
            "POSTGRES_USER": instance.settings["user"],
            "POSTGRES_DB": instance.settings["database"],
            "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres-password",
        }
        database["volumes"].append(
            f"{secret_dir}/postgres-{instance.id}.password:/run/secrets/postgres-password:ro"
        )
        if instance.settings.get("pgbouncer") is True:
            services[f"{instance.id}-pgbouncer"] = {
                "image": config.host.images["pgbouncer"],
                "container_name": f"{instance.id}-pgbouncer-1",
                "restart": "unless-stopped",
                "command": ["pgbouncer", "/etc/pgbouncer/pgbouncer.ini"],
                "depends_on": [service],
                "healthcheck": _docker_healthcheck(
                    ["CMD", "pg_isready", "-h", "127.0.0.1", "-p", "5432"]
                ),
                "volumes": [
                    f"./{instance.id}.pgbouncer.ini:/etc/pgbouncer/pgbouncer.ini:ro",
                    f"{secret_dir}/postgres-{instance.id}.users:/run/secrets/pgbouncer-users:ro",
                ],
                "networks": ["traefik-net"],
                "labels": route,
            }
        else:
            database["labels"] = route
    else:
        suffix = "conf" if instance.engine == "redis" else "flags"
        command = (
            ["redis-server", "/run/secrets/redis.conf"]
            if instance.engine == "redis"
            else ["--flagfile=/run/secrets/dragonfly.flags"]
        )
        database["command"] = command
        database["volumes"].append(
            f"{secret_dir}/kv-{instance.id}.{suffix}:/run/secrets/{instance.engine}.{suffix}:ro"
        )
        database["labels"] = route
        if instance.http is not None and instance.http.get("enabled") is True:
            services[f"{instance.id}-http"] = {
                "image": instance.http["image"],
                "container_name": f"{instance.id}-http-1",
                "restart": "unless-stopped",
                "env_file": [str(secret_dir / f"kv-{instance.id}-http.env")],
                "environment": {
                    "SRH_MODE": "env",
                    "SRH_MAX_CONNECTIONS": str(instance.http["max_connections"]),
                },
                "depends_on": [service],
                "healthcheck": _docker_healthcheck(
                    ["CMD", "wget", "--spider", "--quiet", "http://127.0.0.1:80/"]
                ),
                "ports": [f"127.0.0.1:{instance.http['port']}:80"],
                "networks": ["traefik-net"],
            }
    return {
        "name": instance.project,
        "services": services,
        "networks": {NETWORK: {"external": True, "name": NETWORK}},
    }


def _traefik(config: Config) -> dict[str, Any]:
    return {
        "name": TRAEFIK_PROJECT,
        "services": {
            "traefik": {
                "image": config.host.images["traefik"],
                "container_name": TRAEFIK_CONTAINER,
                "restart": "unless-stopped",
                "command": [
                    "--providers.docker=true",
                    "--providers.docker.exposedbydefault=false",
                    "--providers.docker.network=traefik-net",
                    "--ping=true",
                    "--entrypoints.postgres.address=:5432",
                    "--entrypoints.redis.address=:6379",
                ],
                "ports": ["5432:5432/tcp", "6379:6379/tcp"],
                "healthcheck": _docker_healthcheck(["CMD", "traefik", "healthcheck", "--ping"]),
                "volumes": ["/var/run/docker.sock:/var/run/docker.sock:ro"],
                "networks": ["traefik-net"],
            }
        },
        "networks": {NETWORK: {"external": True, "name": NETWORK}},
    }


def _docker_healthcheck(test: list[str]) -> dict[str, Any]:
    return {
        "test": test,
        "interval": "10s",
        "timeout": "5s",
        "retries": 12,
        "start_period": "10s",
    }


def _pool_config(instance: Instance) -> str:
    return (
        "[databases]\n"
        f"* = host={instance.container} port=5432\n\n"
        "[pgbouncer]\nlisten_addr = 0.0.0.0\nlisten_port = 5432\n"
        "auth_type = plain\nauth_file = /run/secrets/pgbouncer-users\n"
        f"max_client_conn = {instance.settings['max_clients']}\n"
        f"default_pool_size = {instance.settings['pool_size']}\n"
        f"reserve_pool_size = {instance.settings['reserve_size']}\n"
        "ignore_startup_parameters = extra_float_digits\n"
    )


def _validate_stage(path: Path, payload: dict[str, Any]) -> None:
    if read_json(path / MANIFEST) != payload["manifest"]:
        raise DeploymentError("staged release manifest does not match")
    if load_lock(path / "host.lock.json").as_dict() != payload["lock"]:
        raise DeploymentError("staged release lock does not match")
    staged = load(path / "runtime")
    if runtime_data(staged) != payload["runtime"]:
        raise DeploymentError("staged runtime configuration does not match")
    for name, text in payload["files"].items():
        target = path / name
        if not target.is_file() or target.read_text() != text:
            raise DeploymentError(f"staged release file does not match: {name}")
        if name.startswith("compose/") and name.endswith(".json"):
            try:
                compose_data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise DeploymentError(f"staged Compose file is invalid: {name}") from exc
            if not isinstance(compose_data, dict):
                raise DeploymentError(f"staged Compose file is invalid: {name}")
    if not (path / "src/evanovation_db").is_dir():
        raise DeploymentError("staged application code is missing")
    if _tree_digest(path / "src/evanovation_db") != payload["manifest"]["code_hash"]:
        raise DeploymentError("staged application code does not match release manifest")
    expected = {
        MANIFEST,
        "host.lock.json",
        "runtime/host.json",
        *payload["files"],
        *(
            f"runtime/instances/"
            f"{'postgres' if item['engine'] == 'postgres' else 'kv'}-{item['id']}.json"
            for item in payload["runtime"]["instances"]
        ),
    }
    for target in path.rglob("*"):
        if not target.is_file():
            continue
        name = target.relative_to(path).as_posix()
        if not name.startswith("src/evanovation_db/") and name not in expected:
            raise DeploymentError(f"staged release contains an unexpected file: {name}")


def _validate_compose(
    release: Path,
    manifest: dict[str, Any],
    *,
    timeout: int,
    protected: tuple[str, ...],
) -> None:
    names = {item["compose"] for item in manifest["databases"].values()}
    names.add(TRAEFIK_COMPOSE)
    for name in sorted(names):
        run(
            [
                "docker",
                "compose",
                "-f",
                str(release / name),
                "config",
                "--quiet",
                "--no-env-resolution",
            ],
            timeout=timeout,
            secrets=protected,
        )


def _validate_secrets(config: Config, values: Any) -> None:
    if not isinstance(values, list) or not values:
        raise DeploymentError("apply secret files are invalid")
    allowed = _secret_paths(config)
    found = set()
    for item in values:
        if not isinstance(item, dict) or set(item) != {"path", "content", "preserve"}:
            raise DeploymentError("apply secret file fields are invalid")
        path = item["path"]
        if (
            not isinstance(path, str)
            or path not in allowed
            or path in found
            or not isinstance(item["content"], str)
            or not item["content"]
            or not isinstance(item["preserve"], bool)
        ):
            raise DeploymentError("apply secret file is invalid")
        if item["preserve"] != allowed[path]:
            raise DeploymentError("apply secret preservation mode is invalid")
        found.add(path)
    if found != set(allowed):
        raise DeploymentError("apply secret files are incomplete")


def _validate_plan(value: Any, affected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != {"actions", "blocked"}:
        raise DeploymentError("apply plan fields are invalid")
    changed = set()
    for name in ("actions", "blocked"):
        rows = value[name]
        if not isinstance(rows, list):
            raise DeploymentError("apply plan entries are invalid")
        for row in rows:
            if (
                not isinstance(row, dict)
                or set(row) != {"kind", "selector", "reason"}
                or row.get("kind") not in _PLAN_KINDS
                or not _bounded_string(row.get("selector"), 256)
                or not _bounded_string(row.get("reason"), 512)
            ):
                raise DeploymentError("apply plan entry is invalid")
            if name == "blocked" and row["kind"] != "blocked":
                raise DeploymentError("apply blocked plan entry is invalid")
            if name == "actions" and row["kind"] == "blocked":
                raise DeploymentError("apply action plan entry is invalid")
            if row["kind"] in _AFFECTED_KINDS:
                changed.add(row["selector"])
    if changed != affected:
        raise DeploymentError("apply plan does not match affected projects")


def _secret_paths(config: Config) -> dict[str, bool]:
    root = config.host.config_dir / "secrets"
    result = {
        str(root / "restic_password"): False,
        str(config.host.state_dir / "rclone/rclone.conf"): True,
    }
    for instance in config.instances:
        result.update({str(path): False for path in _instance_secret_paths(config, instance)})
    return result


def _instance_secret_paths(config: Config, instance: Instance) -> tuple[Path, ...]:
    root = config.host.config_dir / "secrets"
    result = [root / f"{instance.group}-{instance.id}.password"]
    if instance.engine == "postgres" and instance.settings.get("pgbouncer") is True:
        result.append(root / f"postgres-{instance.id}.users")
    elif instance.engine == "redis":
        result.append(root / f"kv-{instance.id}.conf")
    elif instance.engine == "dragonfly":
        result.append(root / f"kv-{instance.id}.flags")
    if instance.http is not None and instance.http.get("enabled") is True:
        result.extend([root / f"kv-{instance.id}-http.env", root / f"kv-{instance.id}-http.token"])
    return tuple(result)


def _reject_deployed_secret_changes(
    config: Config,
    prior_manifest: dict[str, Any] | None,
    values: list[dict[str, Any]],
) -> None:
    if prior_manifest is None:
        return
    content = {Path(item["path"]): item["content"].encode() for item in values}
    deployed = prior_manifest["databases"]
    for instance in config.instances:
        if instance.selector not in deployed:
            continue
        for path in _instance_secret_paths(config, instance):
            if path.is_file() and path.read_bytes() != content[path]:
                raise DeploymentError(
                    f"credential or protected database config change is blocked for "
                    f"{instance.selector}; credential rotation requires a separate workflow"
                )


def _snapshot_files(paths: Iterable[Path]) -> tuple[_FileState, ...]:
    result = []
    for path in sorted(set(paths)):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise DeploymentError(f"managed file path is not a regular file: {path}")
        if not path.is_file():
            result.append(_FileState(path, False, None, None, None, None))
            continue
        metadata = path.stat()
        result.append(
            _FileState(
                path,
                True,
                path.read_bytes(),
                stat.S_IMODE(metadata.st_mode),
                metadata.st_uid,
                metadata.st_gid,
            )
        )
    return tuple(result)


def _restore_files(states: tuple[_FileState, ...]) -> list[Path]:
    failures = []
    for item in states:
        try:
            if item.existed:
                assert item.content is not None and item.mode is not None
                write_bytes(item.path, item.content, mode=item.mode)
                assert item.uid is not None and item.gid is not None
                os.chown(item.path, item.uid, item.gid)
            elif item.path.exists() or item.path.is_symlink():
                if not item.path.is_file() or item.path.is_symlink():
                    raise DeploymentError(f"managed file path changed type: {item.path}")
                item.path.unlink()
        except (OSError, Error):
            failures.append(item.path)
    return failures


def _install_secrets(values: list[dict[str, Any]]) -> None:
    for item in values:
        path = Path(item["path"])
        if item["preserve"]:
            if path.is_file():
                continue
            if path.exists():
                raise DeploymentError(f"preserved secret path is not a file: {path}")
        ownership = path.stat() if path.is_file() else None
        write_text(path, item["content"], mode=0o600)
        if ownership is not None:
            os.chown(path, ownership.st_uid, ownership.st_gid)


def _payload_secrets(values: list[dict[str, Any]]) -> tuple[str, ...]:
    files = tuple(SecretFile(Path(item["path"]), item["content"]) for item in values)
    return protected_values(files)


def _affected(
    desired: dict[str, Any],
    deployed: dict[str, Any],
    live: dict[str, Any],
    desired_infrastructure: dict[str, Any],
    deployed_infrastructure: dict[str, Any] | None,
    live_infrastructure: dict[str, Any],
) -> set[str]:
    result = set()
    selector = desired_infrastructure["selector"]
    wanted_traefik = desired_infrastructure["traefik"]
    current_traefik = (
        deployed_infrastructure.get("traefik")
        if isinstance(deployed_infrastructure, dict)
        else None
    )
    observed_traefik = live_infrastructure.get("traefik")
    network = live_infrastructure.get("network")
    if (
        current_traefik is None
        or current_traefik.get("image") != wanted_traefik["image"]
        or current_traefik.get("service_hash") != wanted_traefik["service_hash"]
        or not isinstance(network, dict)
        or network.get("exists") is not True
        or not isinstance(observed_traefik, dict)
        or observed_traefik.get("running") is not True
        or observed_traefik.get("healthy") is not True
        or observed_traefik.get("image") != current_traefik.get("image")
        or observed_traefik.get("service_hash") != current_traefik.get("service_hash")
    ):
        result.add(selector)
    for selector, item in desired.items():
        current = deployed.get(selector)
        observed = live.get(selector)
        if (
            current is None
            or current.get("image") != item["image"]
            or current.get("service_hash") != item["service_hash"]
            or not _project_matches(current, observed)
        ):
            result.add(selector)
    return result


def _project_matches(expected: dict[str, Any], observed: Any) -> bool:
    if not isinstance(observed, dict) or set(observed) != {"services"}:
        return False
    live = observed["services"]
    services = expected.get("services")
    if not isinstance(live, dict) or not isinstance(services, dict) or set(live) != set(services):
        return False
    contract_hash = expected.get("service_hash")
    for name, service in services.items():
        state = live.get(name)
        if (
            not isinstance(state, dict)
            or state.get("running") is not True
            or state.get("image") != service.get("image")
            or state.get("service_hash") != contract_hash
            or (service.get("health") == "docker" and state.get("healthy") is not True)
        ):
            return False
    return True


def _recover(
    failed_config: Config,
    failed_release: Path,
    failed_manifest: dict[str, Any],
    prior_config: Config | None,
    prior_release: Path | None,
    prior_manifest: dict[str, Any] | None,
    affected: list[str] | tuple[str, ...],
    *,
    timeout: int,
    protected: tuple[str, ...],
) -> list[str]:
    failures = []
    failed_infrastructure = failed_manifest["infrastructure"]
    prior_infrastructure = prior_manifest["infrastructure"] if prior_manifest is not None else None
    prior_databases = prior_manifest["databases"] if prior_manifest is not None else {}
    failed_databases = failed_manifest["databases"]
    for selector in _ordered(failed_config, affected):
        present = selector == failed_infrastructure["selector"] or selector in failed_databases
        if not present:
            continue
        try:
            _stop(
                failed_config,
                failed_release,
                failed_manifest,
                selector,
                timeout=timeout,
                protected=protected,
            )
        except (Error, OSError):
            failures.append(f"stop:{selector}")

    if prior_config is None or prior_release is None or prior_manifest is None:
        return failures
    restored = []
    for selector in _ordered(prior_config, affected):
        if selector != prior_infrastructure["selector"] and selector not in prior_databases:
            continue
        try:
            _ensure_network(timeout=timeout)
            _up(
                prior_config,
                prior_release,
                prior_manifest,
                selector,
                timeout=timeout,
                protected=protected,
            )
            restored.append(selector)
        except (Error, OSError):
            failures.append(f"restore:{selector}")
    for selector in restored:
        try:
            _health(prior_config, prior_manifest, selector)
        except (Error, OSError):
            failures.append(f"health:{selector}")
    return failures


def _rollback_plan(config: Config, selected: str | None) -> dict[str, Any]:
    current = active()
    if current is None:
        raise DeploymentError("no active release is available")
    current_release, current_manifest = current
    if current_manifest["host"] != config.host.id:
        raise DeploymentError("active release is for another host")
    target_id = selected or _previous_release(config, current_manifest["id"])
    target_release = _release_path(target_id)
    target_manifest = _manifest(target_release)
    if target_manifest["host"] != config.host.id:
        raise DeploymentError("rollback release is for another host")
    if target_id != current_manifest["id"]:
        audit = _read_audit(config, target_id, required=True)
        if audit.get("successful") is not True or audit.get("activated_at") is None:
            raise DeploymentError("rollback release was never successfully active")

    current_config = load(current_release / "runtime")
    target_config = load(target_release / "runtime")
    _compatible(current_config, current_manifest, target_config, target_manifest)
    changes = _rollback_changes(current_manifest, target_manifest)
    return {
        "version": ROLLBACK_VERSION,
        "host": config.host.id,
        "active": current_manifest["id"],
        "release": target_id,
        "activate": target_id != current_manifest["id"],
        "changes": changes,
    }


def _rollback_changes(current: dict[str, Any], target: dict[str, Any]) -> list[dict[str, Any]]:
    changes = []
    current_infrastructure = current["infrastructure"]
    target_infrastructure = target["infrastructure"]
    current_traefik = current_infrastructure["traefik"]
    target_traefik = target_infrastructure["traefik"]
    if (
        current_infrastructure["network"] != target_infrastructure["network"]
        or current_traefik["image"] != target_traefik["image"]
        or current_traefik["service_hash"] != target_traefik["service_hash"]
    ):
        changes.append(
            {
                "action": "update",
                "selector": target_infrastructure["selector"],
                "from_image": current_traefik["image"],
                "to_image": target_traefik["image"],
                "from_major": None,
                "to_major": None,
            }
        )
    current_databases = current["databases"]
    target_databases = target["databases"]
    for selector in sorted(set(current_databases) | set(target_databases)):
        before = current_databases.get(selector)
        after = target_databases.get(selector)
        if after is None:
            action = "stop"
        elif before is None:
            action = "start"
        elif before["image"] != after["image"] or before["service_hash"] != after["service_hash"]:
            action = "update"
        else:
            continue
        changes.append(
            {
                "action": action,
                "selector": selector,
                "from_image": before["image"] if before is not None else None,
                "to_image": after["image"] if after is not None else None,
                "from_major": before["major"] if before is not None else None,
                "to_major": after["major"] if after is not None else None,
            }
        )
    return changes


def _compatible(
    current_config: Config,
    current_manifest: dict[str, Any],
    target_config: Config,
    target_manifest: dict[str, Any],
) -> None:
    for name in ("config_dir", "state_dir", "backup_dir", "lock_dir", "data_root"):
        if getattr(current_config.host, name) != getattr(target_config.host, name):
            raise DeploymentError(
                f"rollback cannot change managed host {name}; use the backup restore workflow "
                "instead"
            )
    current_by_selector = {item.selector: item for item in current_config.instances}
    current_by_data = {str(item.data): item for item in current_config.instances}
    current_by_name: dict[str, list[Instance]] = {}
    target_by_name: dict[str, list[Instance]] = {}
    for item in current_config.instances:
        current_by_name.setdefault(item.id, []).append(item)
    for item in target_config.instances:
        target_by_name.setdefault(item.id, []).append(item)

    for target in target_config.instances:
        current = current_by_selector.get(target.selector) or current_by_data.get(str(target.data))
        if current is None:
            same_name = current_by_name.get(target.id, [])
            if len(same_name) == 1 and len(target_by_name[target.id]) == 1:
                current = same_name[0]
        if current is None:
            continue
        if current.engine != target.engine:
            raise DeploymentError(
                f"rollback blocked before stopping {current.selector}: engine type change from "
                f"{current.engine} to {target.engine} cannot use live data; use the backup restore "
                "workflow instead"
            )
        if current.selector == target.selector and current.data != target.data:
            raise DeploymentError(
                f"rollback blocked before stopping {current.selector}: release data paths differ; "
                "use the backup restore workflow instead"
            )
        current_major = current_manifest["databases"][current.selector]["major"]
        target_major = target_manifest["databases"][target.selector]["major"]
        if current.engine == "postgres":
            current_major = _postgres_data_major(current, current_major)
        if target.engine == "postgres" and target_major != current_major:
            raise DeploymentError(
                f"rollback blocked before stopping {target.selector}: PostgreSQL major "
                f"{target_major} cannot safely open live major {current_major} data; use the "
                "backup restore workflow instead"
            )
        if target.engine in {"redis", "dragonfly"} and target_major < current_major:
            raise DeploymentError(
                f"rollback blocked before stopping {target.selector}: {target.engine} major "
                f"{target_major} cannot safely open newer major {current_major} data; use the "
                "backup restore workflow instead"
            )


def _postgres_data_major(instance: Instance, fallback: int) -> int:
    path = instance.data / "PG_VERSION"
    if not path.exists():
        return fallback
    try:
        value = path.read_text().strip().split(".", 1)[0]
        major = int(value)
    except (OSError, ValueError) as exc:
        raise DeploymentError(
            f"rollback blocked before stopping {instance.selector}: live PostgreSQL data version "
            "is invalid; use the backup restore workflow instead"
        ) from exc
    if major < 1:
        raise DeploymentError(
            f"rollback blocked before stopping {instance.selector}: live PostgreSQL data version "
            "is invalid; use the backup restore workflow instead"
        )
    return major


def _previous_release(config: Config, current: str) -> str:
    for event in reversed(_activation_events(config)):
        release_id = event["release"]
        if release_id != current and _successful(config, release_id):
            return release_id
    audit = _read_audit(config, current, required=False)
    predecessor = audit.get("predecessor") if audit is not None else None
    if isinstance(predecessor, str) and _successful(config, predecessor):
        return predecessor
    candidates = []
    root = config.host.state_dir / "releases"
    if root.is_dir():
        for path in root.glob("release-*.json"):
            value = _read_audit(config, path.stem, required=False)
            if (
                value is not None
                and value.get("release") != current
                and value.get("successful") is True
                and isinstance(value.get("activated_at"), str)
            ):
                candidates.append(value)
    if candidates:
        candidates.sort(key=lambda item: item["activated_at"], reverse=True)
        return candidates[0]["release"]
    raise DeploymentError("no previous successful active release is available")


def _successful(config: Config, release_id: str) -> bool:
    try:
        _release_path(release_id)
    except DeploymentError:
        return False
    audit = _read_audit(config, release_id, required=False)
    return bool(
        audit is not None
        and audit.get("successful") is True
        and isinstance(audit.get("activated_at"), str)
    )


def _record_stage(config: Config, payload: dict[str, Any]) -> None:
    manifest = payload["manifest"]
    release_id = manifest["id"]
    now = _now()
    audit = _read_audit(config, release_id, required=False)
    fixed = {
        "version": AUDIT_VERSION,
        "release": release_id,
        "source_hash": manifest["source_hash"],
        "lock_hash": manifest["lock_hash"],
        "controller_version": manifest["controller_version"],
        "runtime_version": manifest["runtime_version"],
        "engine_majors": {
            selector: {"engine": item["engine"], "major": item["major"]}
            for selector, item in manifest["databases"].items()
        },
    }
    if audit is None:
        audit = {
            **fixed,
            "created_at": now,
            "updated_at": now,
            "finished_at": None,
            "activated_at": None,
            "predecessor": payload["expected"],
            "plan": payload["plan"],
            "status": "staged",
            "outcome": "staged",
            "severity": "info",
            "successful": False,
            "recovery_steps": [],
            "events": [],
        }
    elif any(audit.get(name) != value for name, value in fixed.items()):
        raise DeploymentError("release audit does not match immutable release metadata")
    audit.update(
        {
            "updated_at": now,
            "finished_at": None,
            "predecessor": payload["expected"],
            "plan": payload["plan"],
            "status": "staged",
            "outcome": "staged",
            "severity": "info",
            "recovery_steps": [],
        }
    )
    audit["events"].append(
        {
            "operation": "apply",
            "started_at": now,
            "finished_at": None,
            "predecessor": payload["expected"],
            "plan": payload["plan"],
            "outcome": "staged",
            "severity": "info",
            "recovered": None,
        }
    )
    _write_audit(config, audit)


def _start_event(
    config: Config,
    release_id: str,
    operation_name: str,
    predecessor: str,
    plan: dict[str, Any],
) -> None:
    audit = _read_audit(config, release_id, required=True)
    now = _now()
    audit["updated_at"] = now
    audit["events"].append(
        {
            "operation": operation_name,
            "started_at": now,
            "finished_at": None,
            "predecessor": predecessor,
            "plan": plan,
            "outcome": "staged",
            "severity": "info",
            "recovered": None,
        }
    )
    _write_audit(config, audit)


def _finish_apply(config: Config, release_id: str, outcome: str, *, recovered: bool) -> None:
    _finish_event(config, release_id, "apply", outcome, recovered=recovered)


def _finish_event(
    config: Config,
    release_id: str,
    operation_name: str,
    outcome: str,
    *,
    recovered: bool,
) -> None:
    audit = _read_audit(config, release_id, required=True)
    now = _now()
    event = next(
        (
            item
            for item in reversed(audit["events"])
            if item.get("operation") == operation_name and item.get("finished_at") is None
        ),
        None,
    )
    if event is None:
        raise DeploymentError("release audit has no open operation event")
    severity = "high" if outcome == "recovery_failed" else "error"
    event.update(
        {
            "finished_at": now,
            "outcome": outcome,
            "severity": severity,
            "recovered": recovered,
        }
    )
    audit.update(
        {
            "updated_at": now,
            "finished_at": now,
            "outcome": outcome,
            "severity": severity,
            "recovery_steps": _recovery_steps(release_id) if outcome == "recovery_failed" else [],
        }
    )
    if operation_name == "apply":
        audit["status"] = outcome
    _write_audit(config, audit)


def _record_activation(
    config: Config,
    release_id: str,
    predecessor: str | None,
    *,
    operation_name: str,
) -> None:
    now = _now()
    if predecessor is not None and predecessor != release_id:
        previous = _read_audit(config, predecessor, required=False)
        if previous is not None:
            previous.update(
                {
                    "updated_at": now,
                    "status": "rolled_back" if operation_name == "rollback" else "superseded",
                }
            )
            _write_audit(config, previous)
    audit = _read_audit(config, release_id, required=True)
    event = next(
        (
            item
            for item in reversed(audit["events"])
            if item.get("operation") == operation_name and item.get("finished_at") is None
        ),
        None,
    )
    if event is None:
        raise DeploymentError("release audit has no open activation event")
    event.update(
        {
            "finished_at": now,
            "outcome": "active",
            "severity": "info",
            "recovered": None,
        }
    )
    audit.update(
        {
            "updated_at": now,
            "finished_at": now,
            "activated_at": now,
            "status": "active",
            "outcome": "active",
            "severity": "info",
            "successful": True,
            "recovery_steps": [],
        }
    )
    _write_audit(config, audit)
    events = _activation_events(config)
    events.append(
        {
            "release": release_id,
            "predecessor": predecessor,
            "operation": operation_name,
            "activated_at": now,
        }
    )
    write_json(
        config.host.state_dir / "release-activations.json",
        {"version": HISTORY_VERSION, "events": events},
        mode=0o600,
    )


def _activation_events(config: Config) -> list[dict[str, Any]]:
    path = config.host.state_dir / "release-activations.json"
    if not path.is_file():
        return []
    try:
        data = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentError("release activation history is invalid") from exc
    if (
        not isinstance(data, dict)
        or set(data) != {"version", "events"}
        or data.get("version") != HISTORY_VERSION
        or not isinstance(data.get("events"), list)
    ):
        raise DeploymentError("release activation history is invalid")
    for event in data["events"]:
        if (
            not isinstance(event, dict)
            or set(event) != {"release", "predecessor", "operation", "activated_at"}
            or not isinstance(event.get("release"), str)
            or (event.get("predecessor") is not None and not isinstance(event["predecessor"], str))
            or event.get("operation") not in {"apply", "rollback"}
            or not isinstance(event.get("activated_at"), str)
        ):
            raise DeploymentError("release activation history is invalid")
    return list(data["events"])


def _read_audit(config: Config, release_id: str, *, required: bool) -> dict[str, Any] | None:
    path = _audit_path(config, release_id)
    if not path.is_file():
        if required:
            raise DeploymentError(f"release audit is unavailable: {release_id}")
        return None
    try:
        data = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentError(f"release audit is invalid: {release_id}") from exc
    if (
        not isinstance(data, dict)
        or data.get("version") != AUDIT_VERSION
        or data.get("release") != release_id
        or not isinstance(data.get("events"), list)
        or not isinstance(data.get("engine_majors"), dict)
        or not isinstance(data.get("successful"), bool)
    ):
        raise DeploymentError(f"release audit is invalid: {release_id}")
    return data


def _write_audit(config: Config, audit: dict[str, Any]) -> None:
    if not _secret_free(audit):
        raise DeploymentError("release audit contains protected data")
    write_json(_audit_path(config, audit["release"]), audit, mode=0o600)


def _audit_path(config: Config, release_id: str) -> Path:
    _validate_release_id(release_id, "release audit")
    return config.host.state_dir / "releases" / f"{release_id}.json"


def _history_row(
    manifest: dict[str, Any], audit: dict[str, Any] | None, active_id: str | None
) -> dict[str, Any]:
    return {
        "id": manifest["id"],
        "active": manifest["id"] == active_id,
        "status": audit.get("status", "unknown") if audit is not None else "unknown",
        "outcome": audit.get("outcome", "unknown") if audit is not None else "unknown",
        "severity": audit.get("severity", "info") if audit is not None else "info",
        "successful": audit.get("successful", False) if audit is not None else False,
        "created_at": audit.get("created_at") if audit is not None else None,
        "updated_at": audit.get("updated_at") if audit is not None else None,
        "finished_at": audit.get("finished_at") if audit is not None else None,
        "activated_at": audit.get("activated_at") if audit is not None else None,
        "predecessor": audit.get("predecessor") if audit is not None else None,
        "source_hash": manifest["source_hash"],
        "lock_hash": manifest["lock_hash"],
        "controller_version": manifest["controller_version"],
        "runtime_version": manifest["runtime_version"],
        "engine_majors": audit.get("engine_majors", {}) if audit is not None else {},
        "plan": audit.get("plan", {"actions": [], "blocked": []})
        if audit is not None
        else {"actions": [], "blocked": []},
        "recovery_steps": audit.get("recovery_steps", []) if audit is not None else [],
    }


def _recovery_steps(release_id: str) -> list[str]:
    return [
        "Do not delete, rename, or replace any database data path.",
        f"Inspect affected projects using retained release {release_id} and the prior release.",
        "Run evdb status and use the backup restore workflow if live data is incompatible.",
    ]


def _activate_runtime(config: Config, source: Path) -> None:
    target = config.host.config_dir
    instances = target / "instances"
    jobs = target / "jobs"
    instances.mkdir(parents=True, exist_ok=True, mode=0o750)
    jobs.mkdir(parents=True, exist_ok=True, mode=0o750)
    _replace_runtime_json(target / "host.json", read_json(source / "host.json"))
    wanted = set()
    for path in (source / "instances").glob("*.json"):
        wanted.add(path.name)
        _replace_runtime_json(instances / path.name, read_json(path))
    for path in instances.glob("*.json"):
        if path.name not in wanted:
            path.unlink()
    wanted_jobs = set()
    for instance in config.instances:
        name = f"{instance.group}-{instance.id}"
        wanted_jobs.add(name)
        _replace_runtime_text(
            jobs / name,
            f"GROUP={instance.group}\nNAME={instance.id}\n",
        )
    for path in jobs.iterdir():
        if path.is_file() and path.name not in wanted_jobs:
            path.unlink()


def _activate_release(config: Config, release: Path) -> None:
    unit_paths = tuple(UNIT_DIR / name for name in UNIT_NAMES)
    unit_state = _snapshot_files(unit_paths)
    runtime_state = _snapshot_files(_runtime_paths(config, release / "runtime"))
    current = ROOT / "current"
    if current.exists() and not current.is_symlink():
        raise DeploymentError("active release pointer is invalid")
    prior_link = os.readlink(current) if current.is_symlink() else None
    try:
        _install_units(release)
        _activate_runtime(config, release / "runtime")
        _activate(release)
        _reload_units(config)
    except (Error, OSError) as exc:
        failures = []
        try:
            _restore_current(current, prior_link)
        except OSError:
            failures.append("active-pointer")
        failures.extend(f"runtime:{path}" for path in _restore_files(runtime_state))
        failures.extend(f"unit:{path}" for path in _restore_files(unit_state))
        try:
            _reload_units(config)
        except (Error, OSError):
            failures.append("systemd-reload")
        raise _ActivationError(
            "release activation failed"
            + (" and prior active files could not be restored" if failures else ""),
            recovery_failed=bool(failures),
        ) from exc


def _runtime_paths(config: Config, source: Path) -> tuple[Path, ...]:
    target = config.host.config_dir
    paths = {target / "host.json"}
    paths.update(path for path in (target / "instances").glob("*.json"))
    paths.update(path for path in (target / "jobs").glob("*") if path.is_file())
    paths.update(target / "instances" / path.name for path in (source / "instances").glob("*.json"))
    paths.update(target / "jobs" / f"{item.group}-{item.id}" for item in config.instances)
    return tuple(sorted(paths))


def _install_units(release: Path) -> None:
    for name in UNIT_NAMES:
        source = release / "systemd" / name
        if not source.is_file():
            raise DeploymentError(f"staged systemd unit is missing: {name}")
        target = UNIT_DIR / name
        ownership = target.stat() if target.is_file() else None
        write_text(target, source.read_text(), mode=0o644)
        if ownership is not None:
            os.chown(target, ownership.st_uid, ownership.st_gid)


def _reload_units(config: Config) -> None:
    run(["systemctl", "daemon-reload"], timeout=config.host.timeouts["command"])


def _restore_current(current: Path, target: str | None) -> None:
    temporary = current.with_name(".current-restore")
    temporary.unlink(missing_ok=True)
    if target is None:
        current.unlink(missing_ok=True)
        return
    temporary.symlink_to(target)
    temporary.replace(current)


def _replace_runtime_json(path: Path, value: Any) -> None:
    ownership = path.stat() if path.is_file() else None
    write_json(path, value, mode=0o640)
    if ownership is not None:
        os.chown(path, ownership.st_uid, ownership.st_gid)


def _replace_runtime_text(path: Path, value: str) -> None:
    ownership = path.stat() if path.is_file() else None
    write_text(path, value, mode=0o640)
    if ownership is not None:
        os.chown(path, ownership.st_uid, ownership.st_gid)


def _activate(release: Path) -> None:
    current = ROOT / "current"
    current.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    temporary = current.with_name(f".current-{release.name}")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(release)
    temporary.replace(current)


def _manifest(release: Path) -> dict[str, Any]:
    try:
        data = read_json(release / MANIFEST)
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentError("release manifest is unavailable") from exc
    if (
        not isinstance(data, dict)
        or set(data)
        != {
            "version",
            "host",
            "source_hash",
            "runtime_hash",
            "lock_hash",
            "files_hash",
            "code_hash",
            "controller_version",
            "runtime_version",
            "infrastructure",
            "databases",
            "id",
        }
        or data.get("version") != VERSION
        or data.get("id") != release.name
        or not _bounded_string(data.get("host"), 128)
        or any(
            not isinstance(data.get(name), str) or not _HASH.fullmatch(data[name])
            for name in ("source_hash", "runtime_hash", "lock_hash", "files_hash", "code_hash")
        )
        or not _bounded_string(data.get("controller_version"), 128)
        or not _bounded_string(data.get("runtime_version"), 128)
        or not isinstance(data.get("infrastructure"), dict)
        or not isinstance(data.get("databases"), dict)
    ):
        raise DeploymentError("release manifest is invalid")
    _validate_release_id(data["id"], "release manifest")
    _validate_infrastructure(data["host"], data["infrastructure"])
    for selector, item in data["databases"].items():
        if (
            not _bounded_string(selector, 256)
            or not isinstance(item, dict)
            or set(item)
            != {
                "engine",
                "major",
                "project",
                "container",
                "image",
                "compose",
                "service_hash",
                "config_hash",
                "labels",
                "services",
            }
            or item.get("engine") not in {"postgres", "redis", "dragonfly"}
            or type(item.get("major")) is not int
            or item["major"] < 1
            or not all(
                _bounded_string(item.get(name), 512)
                for name in ("project", "container", "image", "compose")
            )
            or not _safe_name(item["compose"])
            or any(
                not isinstance(item.get(name), str) or not _HASH.fullmatch(item[name])
                for name in ("service_hash", "config_hash")
            )
            or item.get("labels") != {CONTRACT_LABEL: item.get("service_hash")}
            or not _valid_expected_services(item.get("services"), item.get("container"))
        ):
            raise DeploymentError("release database manifest is invalid")
    return data


def _valid_expected_services(value: Any, primary: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    primary_count = 0
    containers = set()
    for name, item in value.items():
        if (
            not _bounded_string(name, 256)
            or not isinstance(item, dict)
            or set(item) != {"container", "image", "health"}
            or not _bounded_string(item.get("container"), 256)
            or not _bounded_string(item.get("image"), 512)
            or item.get("health") not in {"engine", "docker"}
            or item["container"] in containers
        ):
            return False
        containers.add(item["container"])
        if item["health"] == "engine":
            primary_count += 1
            if item["container"] != primary:
                return False
    return primary_count == 1


def _validate_infrastructure(host: str, value: dict[str, Any]) -> None:
    if set(value) != {"selector", "network", "traefik"} or value.get("selector") != f"host/{host}":
        raise DeploymentError("release infrastructure manifest is invalid")
    network = value.get("network")
    traefik = value.get("traefik")
    if (
        not isinstance(network, dict)
        or set(network) != {"name", "contract_hash"}
        or network.get("name") != NETWORK
        or not isinstance(network.get("contract_hash"), str)
        or not _HASH.fullmatch(network["contract_hash"])
        or not isinstance(traefik, dict)
        or set(traefik) != {"project", "container", "image", "compose", "service_hash", "labels"}
        or traefik.get("project") != TRAEFIK_PROJECT
        or traefik.get("container") != TRAEFIK_CONTAINER
        or not _bounded_string(traefik.get("image"), 512)
        or traefik.get("compose") != TRAEFIK_COMPOSE
        or not isinstance(traefik.get("service_hash"), str)
        or not _HASH.fullmatch(traefik["service_hash"])
        or traefik.get("labels") != {CONTRACT_LABEL: traefik.get("service_hash")}
    ):
        raise DeploymentError("release infrastructure manifest is invalid")


def _container_healthy(
    config: Config, instance: Instance, *, contract_hash: str | None = None
) -> bool:
    state = _container_state(instance.container, timeout=min(10, config.host.timeouts["health"]))
    if state["running"] is not True:
        return False
    return contract_hash is None or state["service_hash"] == contract_hash


def _services_healthy(config: Config, services: dict[str, Any], contract_hash: str | None) -> bool:
    for _name, expected in services.items():
        if not isinstance(expected, dict):
            return False
        state = _container_state(
            expected["container"],
            timeout=min(10, config.host.timeouts["health"]),
            health=expected["health"] == "docker",
        )
        if (
            state["running"] is not True
            or state["image"] != expected["image"]
            or (contract_hash is not None and state["service_hash"] != contract_hash)
            or (expected["health"] == "docker" and state["healthy"] is not True)
        ):
            return False
    return True


def engine_healthy(config: Config, instance: Instance, *, timeout: int = 10) -> bool:
    if instance.engine == "postgres":
        args = [
            "docker",
            "exec",
            instance.container,
            "pg_isready",
            "-U",
            str(instance.settings["user"]),
            "-d",
            str(instance.settings["database"]),
        ]
        return run(args, timeout=timeout, check=False).code == 0
    password = read_secret(config.host, instance, "password")
    result = run(
        ["docker", "exec", "--env", "REDISCLI_AUTH", instance.container, "redis-cli", "PING"],
        env={"REDISCLI_AUTH": password},
        secrets=[password],
        timeout=timeout,
        check=False,
    )
    return result.code == 0 and result.out.strip() == "PONG"


def _compose_command(path: Path, project: str) -> list[str]:
    return ["docker", "compose", "-f", str(path), "--project-name", project]


def _compose_name(instance: Instance) -> str:
    return f"compose/{instance.group}/{instance.id}.json"


def _pool_name(instance: Instance) -> str:
    return f"compose/postgres/{instance.id}.pgbouncer.ini"


def _safe_name(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value) and not path.is_absolute() and ".." not in path.parts and path.parts[0] != "."
    )


def _release_path(release_id: str) -> Path:
    _validate_release_id(release_id, "release")
    path = ROOT / "releases" / release_id
    if path.is_symlink() or not path.is_dir():
        raise DeploymentError(f"release is unavailable: {release_id}")
    return path


def _validate_release_id(value: Any, name: str) -> None:
    if not isinstance(value, str) or not _RELEASE.fullmatch(value):
        raise DeploymentError(f"{name} identity is invalid")


def _bounded_string(value: Any, limit: int) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= limit and "\x00" not in value


def _secret_free(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(word in lowered for word in ("password", "token", "secret")):
                return False
            if not _secret_free(item):
                return False
    elif isinstance(value, list):
        return all(_secret_free(item) for item in value)
    elif isinstance(value, str):
        return "op://" not in value
    return True


def _freeze(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
        path.chmod(0o555)
    root.chmod(0o555)


def _thaw(root: Path) -> None:
    root.chmod(0o700)
    for path in root.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)


def _validate_frozen(root: Path) -> None:
    if root.stat().st_mode & 0o222:
        raise DeploymentError("staged release directory is writable")
    for path in root.rglob("*"):
        if path.stat().st_mode & 0o222:
            raise DeploymentError(f"staged release asset is writable: {path.relative_to(root)}")


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def _major(image: str) -> int:
    major = image_major(image)
    if major is None:
        raise DeploymentError("engine image tag must contain a positive major version")
    return major
