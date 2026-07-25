from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import deployment, details
from .config import Config, DatabaseSource, HostSource, Instance, load, load_lock, runtime_data
from .errors import Error, ProtocolError
from .files import free_gb, read_json
from .lifecycle import redact_logs
from .restore import promotion as restore_promotion
from .run import run

VERSION = 4
BASE_TIMERS = (
    "evanovation-db-status.timer",
    "evanovation-db-restore.timer",
)
GROUP_TIMERS = (
    "evanovation-db-weekly@{group}.timer",
    "evanovation-db-monthly@{group}.timer",
)


def remote(config: Config) -> dict[str, Any]:
    release, manifest, release_ok, release_error = _release(config)
    infrastructure = (
        deployment.infrastructure_state(config, manifest)
        if manifest is not None
        else {"network": {"exists": False}, "traefik": _empty_traefik()}
    )
    limit = min(10, config.host.timeouts["health"])
    databases = []
    for instance in config.instances:
        try:
            live = details.container(config, instance, timeout=limit)
            engine_ok = None
            engine_error = None
            if live["running"]:
                try:
                    engine_ok = deployment.engine_healthy(config, instance, timeout=limit)
                    if not engine_ok:
                        engine_error = "engine-native check failed"
                except (Error, OSError, ValueError) as exc:
                    engine_ok = False
                    engine_error = _message(exc)
            deployed = (
                manifest["databases"].get(instance.selector) if manifest is not None else None
            )
            services = deployment.database_state(config, deployed) if deployed is not None else None
            facts = _operation_facts(config, instance)
            databases.append(
                {
                    "selector": instance.selector,
                    "engine": instance.engine,
                    "durable": instance.durable,
                    "container": live,
                    "services": services["services"] if services is not None else {},
                    "engine_check": {"ok": engine_ok, "error": engine_error},
                    "retained": restore_promotion.retained(instance),
                    **facts,
                    "error": None,
                }
            )
        except (Error, OSError, ValueError) as exc:
            databases.append(
                _failed_database(instance, _message(exc), restore_promotion.retained(instance))
            )

    disks = {
        "data": _disk(config.host.data_root, config.host.min_free_gb),
        "backup": _disk(config.host.backup_dir, config.host.min_free_gb),
    }
    timers = _timers(config)
    return {
        "version": VERSION,
        "host": {
            "id": config.host.id,
            "active_release": release,
            "release_consistent": release_ok,
            "release_error": release_error,
            "disks": disks,
            "timers": timers,
        },
        "manifest": manifest,
        "infrastructure": infrastructure,
        "databases": databases,
    }


def assess(
    config: Config,
    wanted: dict[str, Any],
    observed: dict[str, Any],
    *,
    selector: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    validate_remote(config, observed)
    current = now or datetime.now(timezone.utc)
    remote_host = observed["host"]
    active = observed["manifest"]
    deployed = active["databases"] if active is not None else {}
    desired = wanted["databases"]
    live = {item["selector"]: item for item in observed["databases"]}
    infrastructure = _assess_infrastructure(
        wanted.get("infrastructure"),
        active.get("infrastructure") if active is not None else None,
        observed["infrastructure"],
    )
    names = set(desired)
    if selector is None:
        names.update(deployed)
        names.update(live)
    else:
        names = {selector}

    rows = []
    for name in sorted(names):
        instance = _instance(config, name)
        row = _assess_database(
            config,
            instance,
            name,
            desired.get(name),
            deployed.get(name),
            live.get(name),
            current,
        )
        rows.append(row)

    host = {
        "id": config.host.id,
        "ssh": {"reachable": True, "error": None},
        "active_release": remote_host["active_release"],
        "source_lock_consistent": True,
        "release_consistent": remote_host["release_consistent"],
        "release_error": remote_host["release_error"],
        "disks": remote_host["disks"],
        "timers": remote_host["timers"],
        "infrastructure": infrastructure,
    }
    host_ok = (
        host["release_consistent"]
        and all(item["ok"] for item in host["disks"].values())
        and host["timers"]["ok"]
        and infrastructure["healthy"]
    )
    healthy = host_ok and all(row["healthy"] for row in rows)
    return {"version": VERSION, "healthy": healthy, "host": host, "databases": rows}


def unreachable(
    config: Config, error: BaseException, *, selector: str | None = None
) -> dict[str, Any]:
    names = [selector] if selector is not None else [item.selector for item in config.instances]
    message = _message(error)
    rows = []
    for name in names:
        instance = config.select(name)
        rows.append(
            {
                "selector": instance.selector,
                "engine": instance.engine,
                "state": "failed",
                "healthy": False,
                "docker": None,
                "engine_check": {"ok": None, "error": "host unreachable"},
                "deployment": {
                    "state": "unknown",
                    "active_image": None,
                    "desired_image": instance.image,
                },
                "backup": _empty_backup(instance.durable),
                "restore": _empty_restore(instance.durable),
                "retained_data": [],
                "errors": {},
                "details": ["host unreachable"],
            }
        )
    host = {
        "id": config.host.id,
        "ssh": {"reachable": False, "error": message},
        "active_release": None,
        "source_lock_consistent": True,
        "release_consistent": False,
        "release_error": "host unreachable",
        "disks": {
            name: {
                "free_gb": None,
                "minimum_gb": config.host.min_free_gb,
                "ok": False,
                "error": "host unreachable",
            }
            for name in ("data", "backup")
        },
        "timers": {"ok": False, "required": [], "error": "host unreachable"},
        "infrastructure": _unknown_infrastructure("host unreachable"),
    }
    return {"version": VERSION, "healthy": False, "host": host, "databases": rows}


def config_error(
    source: HostSource,
    error: BaseException,
    *,
    selector: str | None = None,
) -> dict[str, Any]:
    selected = _source_select(source, selector) if selector is not None else source.databases
    message = _message(error)
    rows = [
        {
            "selector": item.selector,
            "engine": item.type,
            "state": "failed",
            "healthy": False,
            "docker": None,
            "engine_check": {"ok": None, "error": "source lock is inconsistent"},
            "deployment": {"state": "unknown", "active_image": None, "desired_image": None},
            "backup": _empty_backup(item.mode == "durable"),
            "restore": _empty_restore(item.mode == "durable"),
            "retained_data": [],
            "errors": {},
            "details": ["source lock is inconsistent"],
        }
        for item in selected
    ]
    host = {
        "id": source.id,
        "ssh": {"reachable": False, "error": "not assessed because source lock is inconsistent"},
        "active_release": None,
        "source_lock_consistent": False,
        "release_consistent": False,
        "release_error": message,
        "disks": {
            name: {
                "free_gb": None,
                "minimum_gb": source.min_free_gb,
                "ok": False,
                "error": "not assessed",
            }
            for name in ("data", "backup")
        },
        "timers": {"ok": False, "required": [], "error": "not assessed"},
        "infrastructure": _unknown_infrastructure("not assessed"),
    }
    return {"version": VERSION, "healthy": False, "host": host, "databases": rows}


def render(result: dict[str, Any]) -> str:
    host = result["host"]
    ssh = "ok" if host["ssh"]["reachable"] else "failed"
    release = host["active_release"] or "none"
    lock = "ok" if host["source_lock_consistent"] and host["release_consistent"] else "failed"
    disk = ", ".join(f"{name}={_free(value)}" for name, value in sorted(host["disks"].items()))
    timers = "ok" if host["timers"]["ok"] else "failed"
    infrastructure = host["infrastructure"]
    lines = [
        f"HOST {host['id']}  ssh={ssh}  release={release}  lock={lock}  {disk}  "
        f"timers={timers}  traefik={infrastructure['state']}"
    ]
    if host["ssh"]["error"]:
        lines.append(f"HOST DETAIL: {host['ssh']['error']}")
    elif host["release_error"]:
        lines.append(f"HOST DETAIL: {host['release_error']}")
    for name, value in sorted(host["disks"].items()):
        if value["error"]:
            lines.append(f"HOST DETAIL: {name} disk: {value['error']}")
        elif not value["ok"]:
            lines.append(
                f"HOST DETAIL: {name} disk has {value['free_gb']:.1f} GiB free; "
                f"minimum is {value['minimum_gb']} GiB"
            )
    if host["timers"]["error"]:
        lines.append(f"HOST DETAIL: timers: {host['timers']['error']}")
    for timer in host["timers"]["required"]:
        if not timer["ok"]:
            lines.append(f"HOST DETAIL: timer {timer['name']} is {timer['state']}")
    for detail in infrastructure["details"]:
        lines.append(f"HOST DETAIL: {detail}")

    lines.append(
        "DATABASE                         ENGINE      STATE     DOCKER    BACKUP   RESTORE  DEPLOY"
    )
    for row in result["databases"]:
        docker_state = "unknown" if row["docker"] is None else row["docker"]["state"]
        backup = row["backup"]["state"]
        restore = row["restore"]["state"]
        lines.append(
            f"{row['selector']:<32} {row['engine']:<11} {row['state']:<9} "
            f"{docker_state:<9} {backup:<8} {restore:<8} {row['deployment']['state']}"
        )
        if row["details"]:
            lines.append(f"  DETAIL: {'; '.join(row['details'])}")
        for path in row["retained_data"]:
            lines.append(f"  RETAINED: {path}")
    return "\n".join(lines)


def validate_remote(config: Config, data: Any) -> None:
    if not isinstance(data, dict) or set(data) != {
        "version",
        "host",
        "manifest",
        "infrastructure",
        "databases",
    }:
        raise ProtocolError("remote status result does not match protocol version 3")
    if data["version"] != VERSION:
        raise ProtocolError("remote status result uses an unsupported version")
    host = data["host"]
    if (
        not isinstance(host, dict)
        or set(host)
        != {"id", "active_release", "release_consistent", "release_error", "disks", "timers"}
        or host["id"] != config.host.id
        or not isinstance(host["release_consistent"], bool)
        or (host["active_release"] is not None and not isinstance(host["active_release"], str))
        or (host["release_error"] is not None and not isinstance(host["release_error"], str))
    ):
        raise ProtocolError("remote status host facts are invalid")
    _validate_disks(host["disks"])
    _validate_timers(host["timers"])
    manifest = data["manifest"]
    if manifest is not None and (
        not isinstance(manifest, dict)
        or manifest.get("id") != host["active_release"]
        or manifest.get("host") != config.host.id
        or not isinstance(manifest.get("infrastructure"), dict)
        or not isinstance(manifest.get("databases"), dict)
    ):
        raise ProtocolError("remote status release manifest is invalid")
    _validate_infrastructure(data["infrastructure"])
    databases = data["databases"]
    if not isinstance(databases, list):
        raise ProtocolError("remote status database facts are invalid")
    seen = set()
    for item in databases:
        if not _valid_database(item) or item["selector"] in seen:
            raise ProtocolError("remote status database facts are invalid")
        seen.add(item["selector"])


def get(config: Config, *, now: datetime | None = None) -> tuple[list[dict[str, Any]], bool]:
    observed = remote(config)
    active = observed["manifest"]
    wanted = active or {"databases": {}}
    result = assess(config, wanted, observed, now=now)
    return result["databases"], not result["healthy"]


def _assess_infrastructure(
    desired: dict[str, Any] | None,
    deployed: dict[str, Any] | None,
    live: dict[str, Any],
) -> dict[str, Any]:
    if desired is None:
        return _unknown_infrastructure("desired release infrastructure is missing")
    desired_traefik = desired["traefik"]
    active_traefik = deployed.get("traefik") if deployed is not None else None
    network = live["network"]
    traefik = live["traefik"]
    pending = active_traefik is None or (
        desired["network"] != deployed.get("network")
        or desired_traefik["image"] != active_traefik.get("image")
        or desired_traefik["service_hash"] != active_traefik.get("service_hash")
    )
    drifted = active_traefik is not None and (
        traefik["image"] != active_traefik.get("image")
        or traefik["service_hash"] != active_traefik.get("service_hash")
    )
    details_list = []
    if network["exists"] is not True:
        details_list.append(f"shared Docker network {deployment.NETWORK} is absent")
    if traefik["running"] is not True:
        details_list.append("Traefik container is not running")
    elif traefik["healthy"] is not True:
        details_list.append("Traefik container health check failed")
    if drifted:
        details_list.append("live Traefik image or service contract differs from active release")
    if network["exists"] is not True or traefik["running"] is not True:
        state = "stopped"
    elif traefik["healthy"] is not True:
        state = "failed"
    elif drifted:
        state = "drifted"
    elif pending:
        state = "pending"
    else:
        state = "healthy"
    return {
        "selector": desired["selector"],
        "state": state,
        "healthy": state in {"healthy", "pending"},
        "network": network,
        "traefik": traefik,
        "active_image": active_traefik.get("image") if active_traefik else None,
        "desired_image": desired_traefik["image"],
        "details": details_list,
    }


def _unknown_infrastructure(error: str) -> dict[str, Any]:
    return {
        "selector": None,
        "state": "failed",
        "healthy": False,
        "network": {"exists": False},
        "traefik": _empty_traefik(),
        "active_image": None,
        "desired_image": None,
        "details": [error],
    }


def _empty_traefik() -> dict[str, Any]:
    return {
        "running": False,
        "healthy": False,
        "image": None,
        "service_hash": None,
    }


def _assess_database(
    config: Config,
    instance: Instance | None,
    selector: str,
    desired: dict[str, Any] | None,
    deployed: dict[str, Any] | None,
    facts: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    durable = instance.durable if instance is not None else bool(facts and facts["durable"])
    backup = _backup_assessment(config, durable, facts, now)
    restore = _restore_assessment(config, durable, facts, now)
    errors = dict(facts["errors"]) if facts is not None else {}
    docker = facts["container"] if facts is not None else None
    engine_check = facts["engine_check"] if facts is not None else {"ok": None, "error": None}
    desired_image = desired.get("image") if desired is not None else None
    active_image = deployed.get("image") if deployed is not None else None
    live_image = docker.get("image") if docker is not None else None
    live_hash = docker.get("service_hash") if docker is not None else None
    service_drift, service_failed, service_details = _service_assessment(deployed, facts)

    pending = desired is not None and (
        deployed is None
        or any(
            desired.get(key) != deployed.get(key)
            for key in ("image", "service_hash", "config_hash")
        )
    )
    if pending and deployed is None:
        backup = _empty_backup(False)
        restore = _empty_restore(False)
    drifted = False
    if deployed is not None and live_image is not None and live_image != active_image:
        drifted = live_image != desired_image
    if deployed is not None and docker is not None and live_hash != deployed.get("service_hash"):
        drifted = True
    drifted = drifted or service_drift
    if desired is None and (deployed is not None or facts is not None):
        drifted = True
    deployment_state = "drifted" if drifted else "pending" if pending else "current"

    details_list = []
    failed = service_failed
    stopped = False
    if facts is None:
        if pending:
            details_list.append("not present in the active release")
        else:
            details_list.append("remote database facts are missing")
            failed = True
    elif facts["error"]:
        details_list.append(facts["error"])
        failed = True
    elif deployed is not None:
        if docker is None or not docker["running"]:
            stopped = True
            details_list.append("container is not running")
        elif docker["health"] == "unhealthy":
            failed = True
            details_list.append("Docker health check failed")
        if engine_check["ok"] is False:
            failed = True
            details_list.append(engine_check["error"] or "engine-native check failed")
    if drifted:
        details_list.append("live image or service contract differs from the active release")
    details_list.extend(service_details)
    if backup["state"] == "stale":
        details_list.append("confirmed remote backup is stale")
    if restore["state"] == "stale":
        details_list.append("full backup verification is stale")
    for command, error in sorted(errors.items()):
        message = error.get("message") if isinstance(error, dict) else None
        details_list.append(f"{command}: {message or 'operation failed'}")

    stale = backup["state"] == "stale" or restore["state"] == "stale"
    if failed or errors:
        state = "failed"
    elif stopped:
        state = "stopped"
    elif drifted:
        state = "drifted"
    elif stale:
        state = "stale"
    elif pending:
        state = "pending"
    else:
        state = "healthy"
    healthy = state in {"healthy", "pending"}
    return {
        "selector": selector,
        "engine": instance.engine if instance is not None else facts["engine"],
        "state": state,
        "healthy": healthy,
        "docker": docker,
        "engine_check": engine_check,
        "deployment": {
            "state": deployment_state,
            "active_image": active_image,
            "desired_image": desired_image,
        },
        "backup": backup,
        "restore": restore,
        "retained_data": list(facts["retained"]) if facts is not None else [],
        "errors": errors,
        "details": details_list,
    }


def _service_assessment(
    deployed: dict[str, Any] | None, facts: dict[str, Any] | None
) -> tuple[bool, bool, list[str]]:
    if deployed is None or facts is None:
        return False, False, []
    expected = deployed.get("services")
    live = facts.get("services")
    if not isinstance(expected, dict) or not isinstance(live, dict):
        return True, True, ["managed project service facts are missing"]
    drifted = set(live) != set(expected)
    failed = False
    details_list = []
    contract_hash = deployed.get("service_hash")
    for name, service in expected.items():
        if service.get("health") != "docker":
            continue
        state = live.get(name)
        container = service.get("container", name)
        if not isinstance(state, dict) or state.get("running") is not True:
            drifted = True
            failed = True
            details_list.append(f"managed sidecar {container} is absent or stopped")
            continue
        if state.get("healthy") is not True:
            drifted = True
            failed = True
            details_list.append(f"managed sidecar {container} is unhealthy")
        if state.get("image") != service.get("image") or state.get("service_hash") != contract_hash:
            drifted = True
            details_list.append(f"managed sidecar {container} has deployment drift")
    return drifted, failed, details_list


def _release(config: Config) -> tuple[str | None, dict[str, Any] | None, bool, str | None]:
    try:
        current = deployment.active()
        if current is None:
            return None, None, False, "no active release is available"
        path, manifest = current
        if manifest["host"] != config.host.id:
            return manifest["id"], manifest, False, "active release is for another host"
        release_runtime = load(path / "runtime")
        release_lock = load_lock(path / "host.lock.json")
        consistent = (
            _digest(runtime_data(release_runtime)) == manifest.get("runtime_hash")
            and _digest(release_lock.as_dict()) == manifest.get("lock_hash")
            and _digest(runtime_data(config)) == manifest.get("runtime_hash")
        )
        error = None if consistent else "active release runtime or lock does not match its manifest"
        return manifest["id"], manifest, consistent, error
    except (Error, OSError, ValueError, json.JSONDecodeError) as exc:
        return None, None, False, _message(exc)


def _operation_facts(config: Config, instance: Instance) -> dict[str, Any]:
    path = config.host.state_dir / "state" / instance.group / f"{instance.id}.json"
    try:
        data = read_json(path) if path.is_file() else {}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "backup": None,
            "upload": None,
            "restore": None,
            "errors": {"state": {"message": _message(exc)}},
        }
    if not isinstance(data, dict):
        data = {}
    backup = data.get("backup") if isinstance(data.get("backup"), dict) else {}
    upload = data.get("upload") if isinstance(data.get("upload"), dict) else {}
    if not upload:
        upload = backup.get("upload") if isinstance(backup.get("upload"), dict) else {}
    restore = data.get("restore") if isinstance(data.get("restore"), dict) else {}
    errors = data.get("errors") if isinstance(data.get("errors"), dict) else {}
    if isinstance(data.get("error"), dict):
        command = data["error"].get("command", "legacy")
        errors = {**errors, str(command): data["error"]}
    return {
        "backup": {"time": _optional_string(backup.get("finished"))} if backup else None,
        "upload": {
            "ok": upload.get("ok") is True,
            "time": _optional_string(upload.get("time")),
            "snapshot": _optional_string(upload.get("snapshot")),
        }
        if upload
        else None,
        "restore": {
            "ok": restore.get("ok") is True,
            "time": _optional_string(restore.get("time")),
            "backup": _optional_string(restore.get("backup")),
        }
        if restore
        else None,
        "errors": _safe_errors(errors),
    }


def _failed_database(instance: Instance, error: str, retained: list[str]) -> dict[str, Any]:
    return {
        "selector": instance.selector,
        "engine": instance.engine,
        "durable": instance.durable,
        "container": None,
        "services": {},
        "engine_check": {"ok": False, "error": error},
        "retained": retained,
        "backup": None,
        "upload": None,
        "restore": None,
        "errors": {},
        "error": error,
    }


def _disk(path: Path, minimum: int) -> dict[str, Any]:
    try:
        available = round(free_gb(path), 3)
        return {
            "free_gb": available,
            "minimum_gb": minimum,
            "ok": available >= minimum,
            "error": None,
        }
    except OSError as exc:
        return {"free_gb": None, "minimum_gb": minimum, "ok": False, "error": _message(exc)}


def _timers(config: Config) -> dict[str, Any]:
    names = set(BASE_TIMERS)
    groups = {item.group for item in config.instances if item.durable}
    for group in groups:
        names.update(template.format(group=group) for template in GROUP_TIMERS)
    names.update(
        f"evanovation-db-backup@{item.group}-{item.id}.timer"
        for item in config.instances
        if item.durable
    )
    command = [
        "systemctl",
        "show",
        *sorted(names),
        "--property=Id",
        "--property=ActiveState",
        "--property=UnitFileState",
        "--no-pager",
    ]
    try:
        result = run(command, timeout=min(30, config.host.timeouts["command"]), check=False)
    except Error as exc:
        return {"ok": False, "required": [], "error": _message(exc)}
    if result.code != 0:
        return {"ok": False, "required": [], "error": "systemd timer state is unavailable"}
    values = _parse_units(result.out)
    required = []
    for name in sorted(names):
        item = values.get(name, {})
        active = item.get("ActiveState") == "active"
        enabled = item.get("UnitFileState") == "enabled"
        state = f"{item.get('ActiveState', 'unknown')}/{item.get('UnitFileState', 'unknown')}"
        required.append(
            {
                "name": name,
                "active": active,
                "enabled": enabled,
                "ok": active and enabled,
                "state": state,
            }
        )
    return {"ok": all(item["ok"] for item in required), "required": required, "error": None}


def _parse_units(text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    for line in [*text.splitlines(), ""]:
        if not line:
            name = current.get("Id")
            if name:
                result[name] = current
            current = {}
        elif "=" in line:
            key, value = line.split("=", 1)
            current[key] = value
    return result


def _backup_assessment(
    config: Config, durable: bool, facts: dict[str, Any] | None, now: datetime
) -> dict[str, Any]:
    if not durable:
        return _empty_backup(False)
    local = facts.get("backup") if facts is not None else None
    upload = facts.get("upload") if facts is not None else None
    uploaded = _time(upload.get("time") if upload else None)
    stale = uploaded is None or now - uploaded > timedelta(hours=config.host.backup_max_age_hours)
    return {
        "state": "stale" if stale else "fresh",
        "local_time": local.get("time") if local else None,
        "upload_time": upload.get("time") if upload else None,
        "upload_ok": bool(upload and upload.get("ok")),
        "snapshot": upload.get("snapshot") if upload else None,
    }


def _restore_assessment(
    config: Config, durable: bool, facts: dict[str, Any] | None, now: datetime
) -> dict[str, Any]:
    if not durable:
        return _empty_restore(False)
    restore = facts.get("restore") if facts is not None else None
    checked = _time(restore.get("time") if restore else None)
    stale = checked is None or now - checked > timedelta(days=config.host.restore_max_age_days)
    return {
        "state": "stale" if stale else "fresh",
        "time": restore.get("time") if restore else None,
        "ok": bool(restore and restore.get("ok")),
        "backup": restore.get("backup") if restore else None,
    }


def _empty_backup(required: bool) -> dict[str, Any]:
    return {
        "state": "stale" if required else "n/a",
        "local_time": None,
        "upload_time": None,
        "upload_ok": False,
        "snapshot": None,
    }


def _empty_restore(required: bool) -> dict[str, Any]:
    return {"state": "stale" if required else "n/a", "time": None, "ok": False, "backup": None}


def _instance(config: Config, selector: str) -> Instance | None:
    try:
        return config.select(selector)
    except Error:
        return None


def _source_select(source: HostSource, selector: str) -> tuple[DatabaseSource, ...]:
    if "/" in selector:
        engine, name = selector.split("/", 1)
        matches = [item for item in source.databases if item.type == engine and item.name == name]
    else:
        matches = [item for item in source.databases if item.name == selector]
    if len(matches) == 1:
        return (matches[0],)
    if len(matches) > 1:
        choices = ", ".join(sorted(item.selector for item in matches))
        raise ProtocolError(f"ambiguous database {selector}; use one of: {choices}")
    raise ProtocolError(f"unknown database: {selector}")


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _safe_errors(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for command, error in value.items():
        if not isinstance(command, str) or not isinstance(error, dict):
            continue
        item = {}
        for key in ("command", "step", "time", "backup"):
            if isinstance(error.get(key), str):
                item[key] = error[key]
        item["message"] = _message(error.get("message", "operation failed"))
        result[command] = item
    return result


def _valid_database(item: Any) -> bool:
    if not isinstance(item, dict) or set(item) != {
        "selector",
        "engine",
        "durable",
        "container",
        "services",
        "engine_check",
        "retained",
        "backup",
        "upload",
        "restore",
        "errors",
        "error",
    }:
        return False
    if (
        not isinstance(item["selector"], str)
        or not isinstance(item["engine"], str)
        or not isinstance(item["durable"], bool)
        or not isinstance(item["retained"], list)
        or not all(isinstance(path, str) and path for path in item["retained"])
        or not isinstance(item["errors"], dict)
        or (item["error"] is not None and not isinstance(item["error"], str))
    ):
        return False
    container = item["container"]
    if container is not None and (
        not isinstance(container, dict)
        or set(container) != {"state", "running", "health", "image", "image_id", "service_hash"}
        or not isinstance(container["running"], bool)
        or not isinstance(container["state"], str)
        or not isinstance(container["health"], str)
        or any(
            container[key] is not None and not isinstance(container[key], str)
            for key in ("image", "image_id", "service_hash")
        )
    ):
        return False
    services = item["services"]
    if not isinstance(services, dict):
        return False
    for name, service in services.items():
        if (
            not isinstance(name, str)
            or not isinstance(service, dict)
            or set(service) != {"running", "healthy", "image", "service_hash"}
            or not isinstance(service["running"], bool)
            or (service["healthy"] is not None and not isinstance(service["healthy"], bool))
            or any(
                service[key] is not None and not isinstance(service[key], str)
                for key in ("image", "service_hash")
            )
        ):
            return False
    check = item["engine_check"]
    if (
        not isinstance(check, dict)
        or set(check) != {"ok", "error"}
        or (check["ok"] is not None and not isinstance(check["ok"], bool))
        or (check["error"] is not None and not isinstance(check["error"], str))
    ):
        return False
    return _valid_facts(item)


def _valid_facts(item: dict[str, Any]) -> bool:
    backup = item["backup"]
    if backup is not None and (
        not isinstance(backup, dict)
        or set(backup) != {"time"}
        or (backup["time"] is not None and not isinstance(backup["time"], str))
    ):
        return False
    upload = item["upload"]
    if upload is not None and (
        not isinstance(upload, dict)
        or set(upload) != {"ok", "time", "snapshot"}
        or not isinstance(upload["ok"], bool)
        or any(
            upload[key] is not None and not isinstance(upload[key], str)
            for key in ("time", "snapshot")
        )
    ):
        return False
    restore = item["restore"]
    if restore is not None and (
        not isinstance(restore, dict)
        or set(restore) != {"ok", "time", "backup"}
        or not isinstance(restore["ok"], bool)
        or any(
            restore[key] is not None and not isinstance(restore[key], str)
            for key in ("time", "backup")
        )
    ):
        return False
    for command, error in item["errors"].items():
        if (
            not isinstance(command, str)
            or not isinstance(error, dict)
            or not set(error).issubset({"command", "step", "time", "backup", "message"})
            or "message" not in error
            or not all(isinstance(value, str) for value in error.values())
        ):
            return False
    return True


def _validate_disks(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"data", "backup"}:
        raise ProtocolError("remote status disk facts are invalid")
    for item in value.values():
        if (
            not isinstance(item, dict)
            or set(item) != {"free_gb", "minimum_gb", "ok", "error"}
            or not isinstance(item["minimum_gb"], int)
            or not isinstance(item["ok"], bool)
            or (item["free_gb"] is not None and not isinstance(item["free_gb"], (int, float)))
            or (item["error"] is not None and not isinstance(item["error"], str))
        ):
            raise ProtocolError("remote status disk facts are invalid")


def _validate_timers(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"ok", "required", "error"}:
        raise ProtocolError("remote status timer facts are invalid")
    if not isinstance(value["ok"], bool) or not isinstance(value["required"], list):
        raise ProtocolError("remote status timer facts are invalid")
    for item in value["required"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "active", "enabled", "ok", "state"}
            or not isinstance(item["name"], str)
            or not isinstance(item["state"], str)
            or not all(isinstance(item[key], bool) for key in ("active", "enabled", "ok"))
        ):
            raise ProtocolError("remote status timer facts are invalid")


def _validate_infrastructure(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"network", "traefik"}:
        raise ProtocolError("remote status infrastructure facts are invalid")
    network = value["network"]
    traefik = value["traefik"]
    if (
        not isinstance(network, dict)
        or set(network) != {"exists"}
        or not isinstance(network["exists"], bool)
        or not isinstance(traefik, dict)
        or set(traefik) != {"running", "healthy", "image", "service_hash"}
        or not isinstance(traefik["running"], bool)
        or not isinstance(traefik["healthy"], bool)
        or any(
            traefik[name] is not None and not isinstance(traefik[name], str)
            for name in ("image", "service_hash")
        )
    ):
        raise ProtocolError("remote status infrastructure facts are invalid")


def _free(value: dict[str, Any]) -> str:
    return "unknown" if value["free_gb"] is None else f"{value['free_gb']:.1f}GiB"


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _message(value: Any) -> str:
    return redact_logs(str(value)).replace("\n", " ")[:500]


def _digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()
