from __future__ import annotations

import json
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import yaml

from . import compose, docker, secrets
from .config import Config, Database, load_state
from .engines import dragonfly, postgres, redis
from .files import free_gb
from .log import sanitize
from .run import run

VERSION = 1
_REQUIRED_TIMERS = (
    "evdb-status.timer",
    "evdb-backup-test.timer",
    "evdb-retention.timer",
    "evdb-prune.timer",
    "evdb-repository-check.timer",
)


def collect(config: Config, database: Database | None = None) -> dict[str, Any]:
    errors = []
    try:
        state = load_state(config)
    except Exception as exc:
        state = None
        errors.append(_error("state_incompatible", "host", exc))
    orphans = _orphans(config, state)
    for identity in orphans:
        errors.append(
            _error(
                "orphan_installed",
                identity,
                "installed database is absent from source; removal is unsupported",
            )
        )
    try:
        host = _host(config, state, errors, orphans)
    except Exception as exc:
        errors.append(_error("host_assessment_failed", "host", exc))
        host = _failed_host(config, state, orphans)
    databases = {}
    selected = (database,) if database is not None else config.databases
    for target in selected:
        try:
            item, item_errors = _database(config, target, state)
        except Exception as exc:
            item = {
                "project": target.project,
                "role": target.role,
                "engine": target.engine,
                "running": False,
                "health": "unknown",
                "healthy": False,
                "image": None,
                "source_image": target.image,
                "configuration_match": False,
                "backup": None,
                "upload": None,
                "backup_test": None,
                "error": _message(exc),
            }
            item_errors = [_error("assessment_failed", target.identity, exc)]
        databases[target.identity] = item
        errors.extend(item_errors)
    healthy = host["healthy"] and all(item["healthy"] for item in databases.values())
    return {
        "version": VERSION,
        "healthy": healthy,
        "host": host,
        "databases": databases,
        "errors": errors,
    }


def render(value: dict[str, Any]) -> str:
    host = value["host"]
    lines = [
        f"Host {host['id']}  tool {host['tool_version']}  "
        f"{'healthy' if host['healthy'] else 'needs attention'}",
        "DATABASE                     ENGINE      RUN HEALTH       CONFIG  LOCAL          "
        "UPLOAD         TEST           ERROR                    IMAGE",
    ]
    for identity, item in value["databases"].items():
        image = item.get("image") or "-"
        local = _backup_cell(item.get("backup"))
        upload = _upload_cell(item.get("upload"), item.get("backup"))
        test = _test_cell(item.get("backup_test"))
        error = _cell(item.get("error"), 24)
        lines.append(
            f"{identity:<28} {item['engine']:<11} "
            f"{('yes' if item['running'] else 'no'):<3} {item['health']:<12} "
            f"{('ok' if item['configuration_match'] else 'differs'):<7} "
            f"{local:<14} {upload:<14} {test:<14} {error:<24} {image}"
        )
    for error in value["errors"]:
        lines.append(f"{error['scope']}: {error['message']}")
    return "\n".join(lines)


def dumps(value: dict[str, Any]) -> str:
    return json.dumps(_safe(value), sort_keys=True, separators=(",", ":"))


def _host(config, state, errors, orphans):
    paths = config.paths
    data_path = _existing(config.host.data_root)
    backup_path = _existing(paths.backups)
    disks = {
        "data": {"path": str(data_path), "free_gb": round(free_gb(data_path), 2)},
        "backup": {"path": str(backup_path), "free_gb": round(free_gb(backup_path), 2)},
    }
    for name, item in disks.items():
        item["ok"] = item["free_gb"] >= config.host.backup.min_free_gb
        if not item["ok"]:
            errors.append(_error("disk_low", f"host/{name}", "free space is below policy"))
    infrastructure = _infrastructure(config, state, errors)
    timers = _timers(config, errors)
    transaction = _transaction(config)
    if transaction:
        errors.append(_error("transaction_incomplete", "host", transaction))
    healthy = (
        state is not None
        and infrastructure["healthy"]
        and disks["data"]["ok"]
        and disks["backup"]["ok"]
        and timers["ok"]
        and transaction is None
        and not orphans
    )
    return {
        "id": config.host.id,
        "tool_version": _version(),
        "healthy": healthy,
        "configuration": {
            "ok": state is not None and not orphans,
            "state_version": state.version if state else None,
            "orphans": list(orphans),
        },
        "infrastructure": infrastructure,
        "disks": disks,
        "timers": timers,
        "transaction": transaction,
    }


def _failed_host(config, state, orphans):
    return {
        "id": config.host.id,
        "tool_version": _version(),
        "healthy": False,
        "configuration": {
            "ok": state is not None and not orphans,
            "state_version": state.version if state else None,
            "orphans": list(orphans),
        },
        "infrastructure": {
            "healthy": False,
            "network": False,
            "network_owned": False,
            "traefik": False,
            "traefik_configuration_match": False,
            "traefik_contract_match": False,
            "acme": False,
            "listeners": {"5432": False, "6379": False},
            "source_image": config.host.routing.traefik_image,
            "image": None,
            "running_image": None,
        },
        "disks": {
            "data": {"path": str(config.host.data_root), "free_gb": None, "ok": False},
            "backup": {"path": str(config.paths.backups), "free_gb": None, "ok": False},
        },
        "timers": {
            "ok": False,
            "required": {name: _empty_unit() for name in _REQUIRED_TIMERS},
            "databases": {
                target.identity: {
                    "unit": _backup_timer(target.identity),
                    **_empty_unit(),
                }
                for target in config.databases
                if target.durable
            },
        },
        "transaction": None,
    }


def _database(config: Config, target: Database, state) -> tuple[dict[str, Any], list[dict]]:
    errors = []
    role = state.roles.get(target.identity) if state else None
    if role is None or not role.installed:
        errors.append(_error("not_installed", target.identity, "database is not installed"))
        return (
            {
                "project": target.project,
                "role": target.role,
                "engine": target.engine,
                "running": False,
                "health": "not installed",
                "healthy": False,
                "image": None,
                "source_image": target.image,
                "configuration_match": False,
                "backup": None,
                "upload": None,
                "backup_test": None,
                "error": None,
            },
            errors,
        )

    image = role.images.get("primary").image if role.images.get("primary") else None
    source_match = _image_sources_match(target, role)
    if not source_match:
        errors.append(
            _error(
                "image_source_mismatch",
                target.identity,
                "configured image sources differ from resolved machine state",
            )
        )
    generated = compose.database(config, target, state)
    expected = compose.expected_services(generated, target)
    generated_match = _compose_matches(target.compose, generated)
    configuration_match = source_match and generated_match
    if not generated_match:
        errors.append(
            _error("generated_changed", target.identity, "generated configuration differs")
        )
    running = False
    services_healthy = True
    for name, item in expected.items():
        observed = docker.state(
            item["container"],
            timeout=min(10, config.host.timeouts["health"]),
            health=item["health"] == "docker",
        )
        if name.endswith("-primary"):
            running = bool(observed["running"])
        ok = bool(
            observed["running"]
            and observed["image"] == item["image"]
            and observed["labels"].get(compose.CONTRACT_LABEL) == item["contract"]
            and (item["health"] != "docker" or observed["healthy"])
        )
        services_healthy = services_healthy and ok
        if not ok:
            errors.append(_error("service_unhealthy", target.identity, item["container"]))
    engine_ok = _engine_health(config, target) if running else False
    if running and not engine_ok:
        errors.append(_error("engine_unhealthy", target.identity, "engine query failed"))
    operations = role.operations if role else {}
    backup_state = _safe(operations.get("backup"))
    upload = _safe(operations.get("upload"))
    backup_test = _safe(operations.get("backup_test"))
    if target.durable:
        _freshness(config, target, upload, backup_test, errors)
    current_error = None
    recorded_errors = operations.get("errors", {})
    operation_errors = (
        [item for item in recorded_errors.values() if isinstance(item, dict)]
        if isinstance(recorded_errors, dict)
        else []
    )
    if isinstance(backup_test, dict) and backup_test.get("ok") is False:
        operation_errors.append(
            {
                "time": backup_test.get("time", ""),
                "message": backup_test.get("error") or "backup test failed",
            }
        )
    if operation_errors:
        latest = max(operation_errors, key=lambda item: item.get("time", ""))
        current_error = _message(latest.get("message", "operation failed"))
        errors.append(_error("operation_failed", target.identity, current_error))
    healthy = bool(
        role
        and role.installed
        and running
        and services_healthy
        and engine_ok
        and configuration_match
        and not errors
    )
    return (
        {
            "project": target.project,
            "role": target.role,
            "engine": target.engine,
            "running": running,
            "health": (
                "stopped"
                if not running
                else "healthy"
                if services_healthy and engine_ok
                else "unhealthy"
            ),
            "healthy": healthy,
            "image": image,
            "source_image": target.image,
            "configuration_match": configuration_match,
            "backup": backup_state,
            "upload": upload,
            "backup_test": backup_test,
            "error": current_error,
        },
        errors,
    )


def _engine_health(config: Config, target: Database) -> bool:
    name = f"evdb-{target.project}-{target.role}-primary"
    if target.engine == "postgres":
        return postgres.health(name, user=target.settings.user, database=target.settings.database)
    values = secrets.credentials(config, target)
    if target.engine == "redis":
        return redis.health(name, values.password)
    return dragonfly.health(name, values.password)


def _image_sources_match(target: Database, role) -> bool:
    expected = {"primary": target.image}
    if target.role == "postgres" and target.settings.pgbouncer.enabled:
        expected["pgbouncer"] = target.settings.pgbouncer.image
    if target.role == "kv" and target.settings.http.enabled:
        expected["http"] = target.settings.http.image
    return set(role.images) == set(expected) and all(
        role.images[name].source == source for name, source in expected.items()
    )


def _orphans(config: Config, state) -> tuple[str, ...]:
    configured = {target.identity for target in config.databases}
    installed = {
        identity for identity, role in (state.roles.items() if state else ()) if role.installed
    }
    for path in config.paths.projects.glob("*/*/compose.yaml"):
        try:
            project, role, name = path.relative_to(config.paths.projects).parts
        except (ValueError, OSError):
            continue
        if name == "compose.yaml" and role in {"postgres", "kv"}:
            installed.add(f"{project}/{role}")
    return tuple(sorted(installed - configured))


def _infrastructure(config, state, errors):
    network = run(
        ["docker", "network", "inspect", compose.NETWORK],
        timeout=10,
        check=False,
    )
    network_available = network.code == 0
    network_owned = False
    if network_available:
        try:
            network_data = json.loads(network.out)
            network_owned = (
                isinstance(network_data, list)
                and len(network_data) == 1
                and network_data[0].get("Labels", {}).get(compose.NETWORK_LABEL) == "true"
            )
        except (AttributeError, json.JSONDecodeError, TypeError):
            network_owned = False
    network_ok = network_available and network_owned
    proxy = docker.state(compose.TRAEFIK_CONTAINER, timeout=10, health=True)
    traefik_source_match = False
    traefik_definition_match = False
    traefik_contract_match = False
    expected_image = None
    traefik_state = state.images.get("traefik") if state else None
    if traefik_state is not None:
        traefik_source_match = traefik_state.source == config.host.routing.traefik_image
        expected_image = traefik_state.image
        try:
            generated = compose.traefik(config, state)
            traefik_definition_match = _compose_matches(
                config.paths.traefik / "compose.yaml", generated
            )
            expected = generated["services"]["traefik"]
            traefik_contract_match = bool(
                proxy["image"] == expected["image"]
                and proxy["labels"].get(compose.CONTRACT_LABEL)
                == expected["labels"][compose.CONTRACT_LABEL]
            )
        except (KeyError, TypeError, ValueError):
            pass
    traefik_configuration_match = traefik_source_match and traefik_definition_match
    traefik_ok = bool(
        proxy["running"]
        and proxy["healthy"]
        and traefik_configuration_match
        and traefik_contract_match
    )
    acme = config.paths.traefik / "acme/acme.json"
    acme_ok = acme.is_file() and acme.stat().st_mode & 0o777 == 0o600
    listeners = _listeners()
    listeners_ok = all(listeners.values())
    if not network_available:
        errors.append(
            _error(
                "network_unavailable",
                "host/infrastructure",
                "dedicated Docker network is unavailable",
            )
        )
    elif not network_owned:
        errors.append(
            _error(
                "network_unowned",
                "host/infrastructure",
                "dedicated Docker network is not owned by evdb",
            )
        )
    for code, ok, message in (
        (
            "traefik_unhealthy",
            bool(proxy["running"] and proxy["healthy"]),
            "dedicated Traefik is unhealthy",
        ),
        (
            "traefik_configuration_changed",
            traefik_configuration_match,
            "dedicated Traefik configuration differs",
        ),
        (
            "traefik_contract_changed",
            traefik_contract_match,
            "running Traefik differs from its installed contract",
        ),
        ("acme_unsafe", acme_ok, "ACME storage is missing or not private"),
        ("listener_missing", listeners_ok, "native database listener is unavailable"),
    ):
        if not ok:
            errors.append(_error(code, "host/infrastructure", message))
    return {
        "healthy": network_ok and traefik_ok and acme_ok and listeners_ok,
        "network": network_ok,
        "network_owned": network_owned,
        "traefik": traefik_ok,
        "traefik_configuration_match": traefik_configuration_match,
        "traefik_contract_match": traefik_contract_match,
        "acme": acme_ok,
        "listeners": listeners,
        "source_image": config.host.routing.traefik_image,
        "image": expected_image,
        "running_image": proxy["image"],
    }


def _listeners() -> dict[str, bool]:
    result = run(["ss", "-H", "-ltn"], timeout=10, check=False)
    text = result.out if result.code == 0 else ""
    return {
        "5432": ":5432 " in text or ":5432\n" in text,
        "6379": ":6379 " in text or ":6379\n" in text,
    }


def _timers(config, errors):
    required = _REQUIRED_TIMERS
    database_units = {
        target.identity: _backup_timer(target.identity)
        for target in config.databases
        if target.durable
    }
    result = run(
        [
            "systemctl",
            "show",
            *required,
            *database_units.values(),
            "--property=Id,LoadState,UnitFileState,ActiveState",
        ],
        timeout=20,
        check=False,
    )
    units = {}
    current = {}
    for line in result.out.splitlines() + [""]:
        if not line:
            if current.get("Id"):
                units[current["Id"]] = {
                    "loaded": current.get("LoadState") == "loaded",
                    "enabled": current.get("UnitFileState") == "enabled",
                    "active": current.get("ActiveState") == "active",
                }
            current = {}
        elif "=" in line:
            key, value = line.split("=", 1)
            current[key] = value
    required_ok = result.code == 0 and all(_unit_ok(units.get(name)) for name in required)
    databases = {
        identity: {"unit": name, **units.get(name, _empty_unit())}
        for identity, name in database_units.items()
    }
    database_ok = result.code == 0 and all(
        _unit_ok(units.get(name)) for name in database_units.values()
    )
    if not required_ok:
        errors.append(_error("timer_inactive", "host/timers", "required timers are inactive"))
    for identity, item in databases.items():
        if not _unit_ok(item):
            errors.append(_error("timer_inactive", identity, "database backup timer is inactive"))
    return {
        "ok": required_ok and database_ok,
        "required": {name: units.get(name, _empty_unit()) for name in required},
        "databases": databases,
    }


def _backup_timer(identity: str) -> str:
    escaped = "".join(
        "-" if character == "/" else (rf"\x{ord(character):02x}" if character == "-" else character)
        for character in identity
    )
    return f"evdb-backup@{escaped}.timer"


def _empty_unit() -> dict[str, bool]:
    return {"loaded": False, "enabled": False, "active": False}


def _unit_ok(value) -> bool:
    return bool(value and value.get("loaded") and value.get("enabled") and value.get("active"))


def _transaction(config: Config) -> str | None:
    roots = (config.paths.state / "transactions", config.paths.restores)
    for root in roots:
        if root.is_dir():
            for path in sorted(root.iterdir()):
                if path.is_dir() and any(path.iterdir()):
                    return str(path)
    return None


def _freshness(config, target, upload, test, errors):
    now = datetime.now(timezone.utc)
    upload_time = _date(upload.get("time")) if isinstance(upload, dict) else None
    test_time = _date(test.get("time")) if isinstance(test, dict) and test.get("ok") else None
    upload_stale = (
        upload_time is None
        or (now - upload_time).total_seconds() > config.host.backup.max_age_hours * 3600
    )
    if upload_stale:
        errors.append(_error("backup_stale", target.identity, "remote backup is stale"))
    if test_time is None or (now - test_time).days > config.host.backup.test_max_age_days:
        errors.append(_error("backup_test_stale", target.identity, "backup test is stale"))


def _compose_matches(path: Path, expected: dict[str, Any]) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        text = path.read_text()
        rendered = yaml.safe_dump(expected, sort_keys=False)
        return yaml.safe_load(text) == expected and text == rendered
    except (OSError, yaml.YAMLError):
        return False


def _existing(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _version() -> str:
    try:
        return version("evanovation-db")
    except PackageNotFoundError:
        return "unknown"


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _backup_cell(value) -> str:
    if not isinstance(value, dict):
        return "-"
    return _cell(value.get("backup") or _short_time(value.get("time")), 14)


def _upload_cell(value, backup) -> str:
    if not isinstance(value, dict) and isinstance(backup, dict):
        value = backup.get("upload")
    if not isinstance(value, dict):
        return "-"
    if value.get("ok") is False:
        return "failed"
    return _cell(value.get("snapshot") or _short_time(value.get("time")) or "ok", 14)


def _test_cell(value) -> str:
    if not isinstance(value, dict):
        return "-"
    state = "ok" if value.get("ok") else "failed"
    selected = value.get("backup") or value.get("snapshot") or _short_time(value.get("time"))
    return _cell(f"{state}:{selected}" if selected else state, 14)


def _short_time(value) -> str | None:
    date = _date(value)
    return date.strftime("%m-%d %H:%M") if date else None


def _cell(value, width: int) -> str:
    if value is None or value == "":
        return "-"
    text = str(value).replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "~"


def _error(code: str, scope: str, value: Any) -> dict[str, str]:
    return {"code": code, "scope": scope, "message": _message(value)}


def _message(value: Any) -> str:
    return sanitize(str(value).replace("\x00", ""))[:500]


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _safe(item)
            for key, item in value.items()
            if not any(
                name in str(key).lower()
                for name in ("password", "token", "secret", "credential", "authorization")
            )
        }
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, str):
        return sanitize(value)
    return value
