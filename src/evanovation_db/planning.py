from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config as config_module
from .config import Config, HostLock, HostSource, load_lock, load_source, normalize, resolve_lock
from .deployment import VERSION as RELEASE_VERSION
from .deployment import Bundle, build
from .errors import ConfigError, ProtocolError
from .files import hash as file_hash

CHANGE_ACTIONS = {"create", "update", "restart", "drift"}


@dataclass(frozen=True)
class Desired:
    source_path: Path
    lock_path: Path
    source: HostSource
    lock: HostLock
    config: Config
    bundle: Bundle


@dataclass(frozen=True)
class Action:
    kind: str
    selector: str
    project: str | None
    reason: str


@dataclass(frozen=True)
class Plan:
    host: str
    active: str | None
    actions: tuple[Action, ...]
    blocked: tuple[Action, ...]

    @property
    def affected(self) -> tuple[str, ...]:
        selectors = {item.selector for item in self.actions if item.kind in CHANGE_ACTIONS}
        host = f"host/{self.host}"
        return tuple(sorted(selectors, key=lambda item: (item != host, item)))

    @property
    def changed(self) -> bool:
        return bool(self.actions)

    def render(self) -> str:
        lines = [f"Host: {self.host}", f"Active release: {self.active or 'none'}"]
        if not self.actions and not self.blocked:
            lines.append("No changes.")
        for item in self.actions:
            lines.append(f"{item.kind.upper():7} {item.selector}: {item.reason}")
        for item in self.blocked:
            lines.append(f"BLOCKED {item.selector}: {item.reason}")
        return "\n".join(lines)

    def summary(self) -> dict[str, list[dict[str, str]]]:
        return {
            "actions": [
                {"kind": item.kind, "selector": item.selector, "reason": item.reason}
                for item in self.actions
            ],
            "blocked": [
                {"kind": item.kind, "selector": item.selector, "reason": item.reason}
                for item in self.blocked
            ],
        }


def desired(
    path: str | Path,
    *,
    resolver: Callable[..., str] | None = None,
) -> Desired:
    source_path = config_module.source_path(path)
    source = load_source(source_path)
    lock_path = source_path.parent / "host.lock.json"
    current = load_lock(lock_path) if lock_path.is_file() else HostLock.empty(source.id)
    selected_resolver = resolver or config_module.resolve_image_digest
    host_lock = resolve_lock(source, current, resolver=selected_resolver)
    config = normalize(source, host_lock)
    return Desired(
        source_path,
        lock_path,
        source,
        host_lock,
        config,
        build(config, host_lock, file_hash(source_path)),
    )


def compare(wanted: Desired, state: Any) -> Plan:
    _validate_state(wanted.config, state)
    active = state["manifest"]
    deployed = active["databases"] if active is not None else {}
    desired_databases = wanted.bundle.manifest["databases"]
    live = state["live"]
    desired_infrastructure = wanted.bundle.manifest["infrastructure"]
    deployed_infrastructure = active["infrastructure"] if active is not None else None
    live_infrastructure = state["infrastructure"]
    actions: list[Action] = []
    blocked = []

    host_selector = desired_infrastructure["selector"]
    desired_traefik = desired_infrastructure["traefik"]
    deployed_traefik = (
        deployed_infrastructure["traefik"] if deployed_infrastructure is not None else None
    )
    if deployed_traefik is None:
        actions.append(
            Action(
                "create",
                host_selector,
                desired_traefik["project"],
                "shared Docker network and Traefik are not deployed",
            )
        )
    else:
        if (
            desired_infrastructure["network"] != deployed_infrastructure["network"]
            or desired_traefik["image"] != deployed_traefik.get("image")
            or desired_traefik["service_hash"] != deployed_traefik.get("service_hash")
        ):
            actions.append(
                Action(
                    "update",
                    host_selector,
                    desired_traefik["project"],
                    "shared network or Traefik release definition changed",
                )
            )
        network = live_infrastructure.get("network")
        traefik = live_infrastructure.get("traefik")
        if (
            not isinstance(network, dict)
            or network.get("exists") is not True
            or not isinstance(traefik, dict)
            or traefik.get("running") is not True
            or traefik.get("healthy") is not True
        ):
            actions.append(
                Action(
                    "restart",
                    host_selector,
                    desired_traefik["project"],
                    "shared Docker network or Traefik is not running",
                )
            )
        elif traefik.get("image") != deployed_traefik.get("image") or traefik.get(
            "service_hash"
        ) != deployed_traefik.get("service_hash"):
            actions.append(
                Action(
                    "drift",
                    host_selector,
                    desired_traefik["project"],
                    "live Traefik contract is not active",
                )
            )

    for selector in sorted(set(deployed) - set(desired_databases)):
        database = deployed[selector]
        blocked.append(
            Action(
                "blocked",
                selector,
                database["project"],
                "deployed database is absent from source; use a future retirement workflow",
            )
        )

    for selector, desired_database in sorted(desired_databases.items()):
        deployed_database = deployed.get(selector)
        if deployed_database is None:
            actions.append(
                Action("create", selector, desired_database["project"], "database is not deployed")
            )
            continue
        if desired_database["image"] != deployed_database.get("image") or desired_database[
            "service_hash"
        ] != deployed_database.get("service_hash"):
            actions.append(
                Action(
                    "update",
                    selector,
                    desired_database["project"],
                    "locked image or generated service definition changed",
                )
            )
        elif desired_database["config_hash"] != deployed_database.get("config_hash"):
            actions.append(
                Action(
                    "pending",
                    selector,
                    desired_database["project"],
                    "normalized runtime configuration changed",
                )
            )

        current = live.get(selector)
        restart, drift = _live_service_drift(deployed_database, current)
        if restart:
            actions.append(
                Action(
                    "restart",
                    selector,
                    desired_database["project"],
                    "primary database service is not running",
                )
            )
        if drift:
            actions.append(
                Action(
                    "drift",
                    selector,
                    desired_database["project"],
                    "one or more live project services differ from the active contract",
                )
            )

    host_changes = []
    if active is not None:
        if active.get("runtime_hash") != wanted.bundle.manifest["runtime_hash"]:
            host_changes.append("normalized runtime configuration")
        if (
            active.get("code_hash") != wanted.bundle.manifest["code_hash"]
            or active.get("runtime_version") != wanted.bundle.manifest["runtime_version"]
        ):
            host_changes.append("internal host runtime")
    if host_changes and not any(item.selector == host_selector for item in actions):
        actions.append(
            Action(
                "pending",
                f"host/{wanted.config.host.id}",
                None,
                " and ".join(host_changes) + " changed",
            )
        )
    return Plan(wanted.config.host.id, state["release"], tuple(actions), tuple(blocked))


def _live_service_drift(expected: dict[str, Any], observed: Any) -> tuple[bool, bool]:
    services = expected.get("services")
    if not isinstance(services, dict):
        return True, True
    live = observed.get("services") if isinstance(observed, dict) else None
    if not isinstance(live, dict):
        return True, True
    restart = False
    drift = set(live) != set(services)
    contract_hash = expected.get("service_hash")
    for name, service in services.items():
        state = live.get(name)
        primary = service.get("health") == "engine"
        if not isinstance(state, dict):
            restart = restart or primary
            drift = True
            continue
        if state.get("running") is not True:
            restart = restart or primary
            drift = drift or not primary
        if (
            state.get("image") != service.get("image")
            or state.get("service_hash") != contract_hash
            or (service.get("health") == "docker" and state.get("healthy") is not True)
        ):
            drift = True
    return restart, drift


def _validate_state(config: Config, state: Any) -> None:
    if not isinstance(state, dict) or set(state) != {
        "release",
        "manifest",
        "infrastructure",
        "live",
    }:
        raise ProtocolError("remote release state does not match protocol version 1")
    release = state["release"]
    manifest = state["manifest"]
    if release is None:
        if manifest is not None:
            raise ProtocolError("remote release state is inconsistent")
    elif not isinstance(release, str) or not release:
        raise ProtocolError("remote release identity is invalid")
    elif (
        not isinstance(manifest, dict)
        or manifest.get("version") != RELEASE_VERSION
        or manifest.get("id") != release
        or manifest.get("host") != config.host.id
        or not isinstance(manifest.get("infrastructure"), dict)
        or not isinstance(manifest.get("databases"), dict)
    ):
        raise ProtocolError("remote active release manifest is invalid")
    live = state["live"]
    if not isinstance(live, dict):
        raise ProtocolError("remote live state is invalid")
    for selector, item in live.items():
        if (
            not isinstance(selector, str)
            or not isinstance(item, dict)
            or set(item) != {"services"}
            or not isinstance(item["services"], dict)
        ):
            raise ProtocolError("remote live database state is invalid")
        for service in item["services"].values():
            if (
                not isinstance(service, dict)
                or set(service) != {"running", "healthy", "image", "service_hash"}
                or not isinstance(service["running"], bool)
                or (service["healthy"] is not None and not isinstance(service["healthy"], bool))
                or (service["image"] is not None and not isinstance(service["image"], str))
                or (
                    service["service_hash"] is not None
                    and not isinstance(service["service_hash"], str)
                )
            ):
                raise ProtocolError("remote live database service state is invalid")
    if manifest is not None:
        infrastructure = manifest["infrastructure"]
        traefik = infrastructure.get("traefik")
        if (
            infrastructure.get("selector") != f"host/{config.host.id}"
            or not isinstance(infrastructure.get("network"), dict)
            or not isinstance(traefik, dict)
            or not all(
                isinstance(traefik.get(name), str)
                for name in ("project", "container", "image", "compose", "service_hash")
            )
        ):
            raise ProtocolError("remote active release infrastructure manifest is invalid")
        observed_infrastructure = state["infrastructure"]
        if not isinstance(observed_infrastructure, dict):
            raise ProtocolError("remote live infrastructure state is invalid")
        network = observed_infrastructure.get("network")
        observed_traefik = observed_infrastructure.get("traefik")
        if (
            not isinstance(network, dict)
            or set(network) != {"exists"}
            or not isinstance(network["exists"], bool)
            or not isinstance(observed_traefik, dict)
            or set(observed_traefik) != {"running", "image", "service_hash", "healthy"}
            or not isinstance(observed_traefik["running"], bool)
            or not isinstance(observed_traefik["healthy"], bool)
            or any(
                observed_traefik[name] is not None and not isinstance(observed_traefik[name], str)
                for name in ("image", "service_hash")
            )
        ):
            raise ProtocolError("remote live infrastructure state is invalid")
        for selector, item in manifest["databases"].items():
            if (
                not isinstance(selector, str)
                or not isinstance(item, dict)
                or not isinstance(item.get("project"), str)
                or not isinstance(item.get("image"), str)
                or not isinstance(item.get("service_hash"), str)
                or not isinstance(item.get("config_hash"), str)
                or not isinstance(item.get("services"), dict)
            ):
                raise ProtocolError("remote active release database manifest is invalid")


def require_applicable(plan: Plan) -> None:
    if plan.blocked:
        selectors = ", ".join(item.selector for item in plan.blocked)
        raise ConfigError(
            f"database removal is blocked for {selectors}; restore the source entry or use a "
            "future "
            "retirement workflow"
        )
