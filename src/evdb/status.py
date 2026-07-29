from __future__ import annotations

import json
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__, backup, docker
from .config import protected
from .errors import CommandError, Error
from .files import free_gb
from .models import Config, Database
from .run import clean, redact, run

VERSION = 1


def collect(config: Config, database: Database | None = None) -> dict[str, Any]:
    errors = []
    host = _host(config, errors)
    databases = {}
    selected = (database,) if database else config.databases
    for target in selected:
        item, item_errors = _database(config, target)
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


def dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def render(value: dict[str, Any], *, width: int | None = None) -> str:
    width = width or shutil.get_terminal_size((100, 24)).columns
    identity_width = max(12, min(36, width - 37))
    host = value["host"]
    lines = [
        fit(
            f"Host {host['id']}  evdb {host['tool_version']}  "
            f"{'healthy' if host['healthy'] else 'needs attention'}",
            width,
        ),
        f"{'Database':<{identity_width}}  {'Engine':<9}  {'Status':<9}  Backup",
    ]
    for identity, item in value["databases"].items():
        lines.append(
            f"{fit(identity, identity_width):<{identity_width}}  "
            f"{fit(item['engine'], 9):<9}  {fit(item['health'], 9):<9}  "
            f"{backup_text(item['latest_backup'])}"
        )
    if not value["databases"]:
        lines.append("No databases configured")
    return "\n".join(lines)


def _host(config: Config, errors: list[dict[str, str]]) -> dict[str, Any]:
    paths = config.paths
    disks = {}
    for name, path in (("data", config.host.data_root), ("backup", paths.backups)):
        try:
            disks[name] = _disk(path, config.host.backup.min_free_gb)
        except (Error, OSError) as exc:
            disks[name] = {
                "path": str(path),
                "free_gb": None,
                "ok": False,
                "available": False,
            }
            errors.append(
                _error(
                    "disk_assessment_failed",
                    f"host/{name}",
                    _message(config, exc),
                )
            )
    for name, item in disks.items():
        if item["available"] and not item["ok"]:
            errors.append(_error("disk_low", f"host/{name}", "free space is below policy"))
    infrastructure = _infrastructure(config, errors)
    timer = _timer(config, errors)
    repository = {
        "url": config.host.backup.repository,
        "ready": None,
        "available": False,
    }
    try:
        repository["ready"] = backup.repository_ready(config)
        repository["available"] = True
    except (Error, OSError) as exc:
        errors.append(
            _error(
                "repository_assessment_failed",
                "host/repository",
                _message(config, exc),
            )
        )
    if repository["available"] and not repository["ready"]:
        errors.append(
            _error("repository_unavailable", "host/repository", config.host.backup.repository)
        )
    healthy = (
        all(item["ok"] for item in disks.values())
        and infrastructure["healthy"]
        and timer["ok"]
        and repository["ready"] is True
    )
    return {
        "id": config.host.id,
        "tool_version": __version__,
        "healthy": healthy,
        "source": {"config": str(paths.source), "valid": True},
        "infrastructure": infrastructure,
        "disks": disks,
        "repository": repository,
        "timer": timer,
    }


def _database(config: Config, target: Database) -> tuple[dict[str, Any], list[dict[str, str]]]:
    errors = []
    try:
        from .database import observe

        observed = observe(config, target)
    except (Error, OSError) as exc:
        observed = {"running": None, "healthy": False, "health": "unknown"}
        errors.append(_error("assessment_failed", target.identity, _message(config, exc)))
    latest = None
    if target.durable:
        try:
            rows = backup.history(config, target)
            remote = [
                row
                for row in rows
                if row.get("remote") and isinstance(row.get("snapshot"), str) and row["snapshot"]
            ]
            latest = max(
                remote,
                key=lambda row: _date(row.get("time")) or datetime.min.replace(tzinfo=UTC),
                default=None,
            )
        except (Error, OSError) as exc:
            errors.append(
                _error("backup_assessment_failed", target.identity, _message(config, exc))
            )
        date = _date(latest.get("time")) if latest and latest.get("remote") else None
        stale = (
            date is None
            or (datetime.now(UTC) - date).total_seconds() > config.host.backup.max_age_hours * 3600
        )
        if stale:
            errors.append(
                _error("backup_stale", target.identity, "remote backup is stale or missing")
            )
        backup_state = {
            "state": "stale" if stale else "current",
            "time": latest.get("time") if latest else None,
            "backup": latest.get("backup") if latest else None,
            "snapshot": latest.get("snapshot") if latest else None,
            "local": latest.get("local", False) if latest else False,
            "remote": latest.get("remote", False) if latest else False,
        }
    else:
        backup_state = {
            "state": "disabled",
            "time": None,
            "backup": None,
            "snapshot": None,
            "local": False,
            "remote": False,
        }
    if not observed["healthy"]:
        errors.append(_error("database_unhealthy", target.identity, observed["health"]))
    current_error = next(
        (
            item["message"]
            for item in errors
            if item["code"] in {"assessment_failed", "backup_assessment_failed"}
        ),
        next((item["message"] for item in reversed(errors)), None),
    )
    return (
        {
            "project": target.project,
            "role": target.role,
            "engine": target.engine,
            "running": observed["running"],
            "health": observed["health"],
            "healthy": observed["healthy"] and not errors,
            "image": target.image,
            "latest_backup": backup_state,
            "error": current_error[:500] if current_error else None,
        },
        errors,
    )


def _infrastructure(config: Config, errors: list[dict[str, str]]) -> dict[str, Any]:
    network_ok = None
    try:
        network = run(["docker", "network", "inspect", docker.NETWORK], timeout=10, check=False)
        if network.code:
            detail = network.err.strip() or network.out.strip() or "no output"
            raise CommandError(f"Docker network inspection failed ({network.code}): {detail}")
        try:
            item = json.loads(network.out)[0]
            labels = item["Labels"] or {}
            if not isinstance(item, dict) or not isinstance(labels, dict):
                raise TypeError
            network_ok = labels.get(docker.NETWORK_LABEL) == "true"
        except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
            raise CommandError("invalid Docker network inspection") from exc
    except (Error, OSError) as exc:
        errors.append(_error("network_assessment_failed", "host/network", _message(config, exc)))
    proxy = {"running": None, "healthy": None, "image": None}
    try:
        proxy = docker.state(docker.TRAEFIK_CONTAINER, timeout=10, health=True)
        traefik_ok = bool(proxy["running"] and proxy["healthy"])
    except (Error, OSError) as exc:
        traefik_ok = None
        errors.append(_error("traefik_assessment_failed", "host/traefik", _message(config, exc)))
    try:
        listeners = _listeners()
    except (Error, OSError) as exc:
        listeners = {"5432": None, "6379": None}
        errors.append(_error("listener_assessment_failed", "host/listeners", _message(config, exc)))
    acme = config.paths.traefik / "acme/acme.json"
    try:
        details = acme.lstat()
        acme_ok = stat.S_ISREG(details.st_mode) and details.st_mode & 0o777 == 0o600
    except FileNotFoundError:
        acme_ok = False
    except OSError as exc:
        acme_ok = None
        errors.append(_error("acme_assessment_failed", "host/acme", _message(config, exc)))
    values = {
        "network": network_ok,
        "traefik": traefik_ok,
        "listeners": listeners,
        "acme": acme_ok,
        "image": proxy["image"],
    }
    values["healthy"] = (
        network_ok is True
        and traefik_ok is True
        and all(value is True for value in listeners.values())
        and acme_ok is True
    )
    if not values["healthy"]:
        errors.append(
            _error(
                "infrastructure_unhealthy",
                "host/infrastructure",
                "routing infrastructure is incomplete",
            )
        )
    return values


def _listeners() -> dict[str, bool]:
    result = run(["ss", "-H", "-ltn"], timeout=10, check=False)
    if result.code:
        detail = result.err.strip() or result.out.strip() or "no output"
        raise CommandError(f"listener inspection failed ({result.code}): {detail}")
    text = result.out
    return {str(port): f":{port} " in text or f":{port}\n" in text for port in (5432, 6379)}


def _timer(config: Config, errors: list[dict[str, str]]) -> dict[str, Any]:
    value = {
        "unit": "evdb-backup.timer",
        "loaded": None,
        "enabled": None,
        "active": None,
        "available": False,
        "ok": False,
    }
    try:
        result = run(
            [
                "systemctl",
                "show",
                "evdb-backup.timer",
                "--property=LoadState,UnitFileState,ActiveState",
            ],
            timeout=20,
            check=False,
        )
        if result.code:
            detail = result.err.strip() or result.out.strip() or "no output"
            raise CommandError(f"timer inspection failed ({result.code}): {detail}")
        fields = dict(line.split("=", 1) for line in result.out.splitlines() if "=" in line)
        required = {"LoadState", "UnitFileState", "ActiveState"}
        if not required.issubset(fields):
            raise CommandError("timer inspection returned incomplete fields")
        value.update(
            loaded=fields["LoadState"] == "loaded",
            enabled=fields["UnitFileState"] == "enabled",
            active=fields["ActiveState"] == "active",
            available=True,
        )
        value["ok"] = all(value[name] for name in ("loaded", "enabled", "active"))
    except (Error, OSError) as exc:
        errors.append(_error("timer_assessment_failed", "host/timer", _message(config, exc)))
        return value
    if not value["ok"]:
        errors.append(_error("timer_inactive", "host/timer", "evdb-backup.timer is inactive"))
    return value


def _disk(path: Path, minimum: int) -> dict[str, Any]:
    available = round(free_gb(path), 2)
    return {
        "path": str(path),
        "free_gb": available,
        "ok": available >= minimum,
        "available": True,
    }


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo else result.replace(tzinfo=UTC)


def backup_text(value: dict[str, Any]) -> str:
    if value["state"] == "disabled":
        return "disabled"
    if value["state"] == "stale":
        return "stale/missing"
    date = _date(value.get("time"))
    return date.strftime("%m-%d %H:%M") if date else "current"


def fit(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width < 7:
        return value[:width]
    left = (width - 1) // 2
    return value[:left] + "~" + value[-(width - left - 1) :]


def _error(code: str, scope: str, message: str) -> dict[str, str]:
    return {"code": code, "scope": scope, "message": clean(str(message))[:500]}


def _message(config: Config, value: Any) -> str:
    return redact(str(value).replace("\0", ""), protected(config))[:500]
