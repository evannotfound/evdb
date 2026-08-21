from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__, backup, docker
from .config import protected
from .errors import CommandError, Error
from .files import disk
from .models import Config, Database
from .run import clean, redact, run

VERSION = 2


def collect(
    config: Config,
    database: Database | None = None,
    *,
    progress: Callable[[str], None] | None = None,
    preview: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    selected = (database,) if database else config.databases
    host_errors = []
    pending_repository = {
        "url": config.host.backup.repository,
        "ready": None,
        "available": False,
        "pending": True,
    }
    _progress(progress, "Checking host")
    host = _host(config, host_errors, repository=pending_repository)
    observations = {}
    assessment_errors = {}
    databases = {}
    local_errors = list(host_errors)
    for target in selected:
        _progress(progress, f"Checking {target.identity}")
        observed, item_errors = _observe(config, target)
        observations[target.identity] = observed
        assessment_errors[target.identity] = item_errors
        current_errors = _database_errors(target, observed, item_errors)
        databases[target.identity] = _database_value(
            target,
            observed,
            _pending_backup(target),
            current_errors,
        )
        local_errors.extend(current_errors)
    partial = _result(host, databases, local_errors, pending=True)
    if preview is not None:
        preview(partial)

    remote_snapshots = None
    backup_failure = None
    repository_errors = []
    _progress(progress, "Reading backup repository")
    if any(target.durable for target in selected):
        try:
            remote_snapshots = backup.host_snapshots(config)
            repository = {
                "url": config.host.backup.repository,
                "ready": True,
                "available": True,
            }
        except (Error, OSError) as exc:
            backup_failure = exc
            remote_snapshots = []
            repository = _repository_error(config, repository_errors, exc)
    else:
        repository = _repository(config, repository_errors)

    final_host = {**host, "repository": repository}
    final_host["healthy"] = _host_healthy(final_host)
    final_errors = [*host_errors, *repository_errors]
    final_databases = {}
    for target in selected:
        backup_state, backup_errors = _backup(
            config,
            target,
            remote_snapshots=remote_snapshots,
            backup_failure=backup_failure,
        )
        current_errors = _database_errors(
            target,
            observations[target.identity],
            [*assessment_errors[target.identity], *backup_errors],
        )
        final_databases[target.identity] = _database_value(
            target,
            observations[target.identity],
            backup_state,
            current_errors,
        )
        final_errors.extend(current_errors)
    return _result(final_host, final_databases, final_errors)


def _result(
    host: dict[str, Any],
    databases: dict[str, dict[str, Any]],
    errors: list[dict[str, str]],
    *,
    pending: bool = False,
) -> dict[str, Any]:
    value = {
        "version": VERSION,
        "healthy": None
        if pending
        else host["healthy"] and all(item["healthy"] for item in databases.values()),
        "host": host,
        "databases": databases,
        "errors": errors,
    }
    if pending:
        value["pending"] = True
    return value


def dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _progress(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def render(value: dict[str, Any], *, width: int | None = None) -> str:
    width = width or shutil.get_terminal_size((100, 24)).columns
    identity_width = max(12, min(36, width - 37))
    host = value["host"]
    lines = [
        fit(
            f"Host {host['id']}  evdb {host['tool_version']}  {host_text(value)}",
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


def _host(
    config: Config,
    errors: list[dict[str, str]],
    *,
    repository: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = config.paths
    storage = _storage(config, paths.state, errors, "host/storage")
    database_storage = []
    for index, root in enumerate(config.host.data_roots, 1):
        item = _storage(config, root, errors, f"host/storage/{index}")
        item["databases"] = [
            database.identity
            for database in config.databases
            if database.settings.data_root == root
        ]
        database_storage.append(item)
    infrastructure = _infrastructure(config, errors)
    timer = _timer(config, errors)
    repository = repository if repository is not None else _repository(config, errors)
    return {
        "id": config.host.id,
        "tool_version": __version__,
        "healthy": None
        if repository.get("pending")
        else _host_healthy(
            {
                "storage": storage,
                "database_storage": database_storage,
                "infrastructure": infrastructure,
                "timer": timer,
                "repository": repository,
            }
        ),
        "source": {"config": str(paths.source), "valid": True},
        "infrastructure": infrastructure,
        "storage": storage,
        "database_storage": database_storage,
        "repository": repository,
        "timer": timer,
    }


def _host_healthy(value: dict[str, Any]) -> bool:
    return bool(
        value["storage"]["ok"]
        and all(item["ok"] for item in value["database_storage"])
        and value["infrastructure"]["healthy"]
        and value["timer"]["ok"]
        and value["repository"]["ready"] is True
    )


def _repository(config: Config, errors: list[dict[str, str]]) -> dict[str, Any]:
    value = {"url": config.host.backup.repository, "ready": None, "available": False}
    try:
        value["ready"] = backup.repository_ready(config)
        value["available"] = True
    except (Error, OSError) as exc:
        return _repository_error(config, errors, exc)
    if value["available"] and not value["ready"]:
        errors.append(
            _error("repository_unavailable", "host/repository", config.host.backup.repository)
        )
    return value


def _repository_error(
    config: Config,
    errors: list[dict[str, str]],
    exc: BaseException,
) -> dict[str, Any]:
    errors.append(_error("repository_assessment_failed", "host/repository", _message(config, exc)))
    return {"url": config.host.backup.repository, "ready": None, "available": False}


def _database(
    config: Config,
    target: Database,
    *,
    remote_snapshots: list[dict[str, Any]] | None = None,
    backup_failure: BaseException | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    observed, assessment_errors = _observe(config, target)
    backup_state, backup_errors = _backup(
        config,
        target,
        remote_snapshots=remote_snapshots,
        backup_failure=backup_failure,
    )
    errors = _database_errors(target, observed, [*assessment_errors, *backup_errors])
    return _database_value(target, observed, backup_state, errors), errors


def _observe(config: Config, target: Database) -> tuple[dict[str, Any], list[dict[str, str]]]:
    errors = []
    try:
        from .database import observe

        observed = observe(config, target)
    except (Error, OSError) as exc:
        observed = {"running": None, "healthy": False, "health": "unknown"}
        errors.append(_error("assessment_failed", target.identity, _message(config, exc)))
    return observed, errors


def _backup(
    config: Config,
    target: Database,
    *,
    remote_snapshots: list[dict[str, Any]] | None = None,
    backup_failure: BaseException | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    errors = []
    latest = None
    if target.durable:
        if backup_failure is not None:
            errors.append(
                _error(
                    "backup_assessment_failed",
                    target.identity,
                    _message(config, backup_failure),
                )
            )
        else:
            try:
                rows = backup.history(config, target, remote_snapshots=remote_snapshots)
                remote = [
                    row
                    for row in rows
                    if row.get("remote")
                    and isinstance(row.get("snapshot"), str)
                    and row["snapshot"]
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
        backup_state = _disabled_backup()
    return backup_state, errors


def _pending_backup(target: Database) -> dict[str, Any]:
    if not target.durable:
        return _disabled_backup()
    return {
        "state": "loading",
        "time": None,
        "backup": None,
        "snapshot": None,
        "local": False,
        "remote": False,
    }


def _disabled_backup() -> dict[str, Any]:
    return {
        "state": "disabled",
        "time": None,
        "backup": None,
        "snapshot": None,
        "local": False,
        "remote": False,
    }


def _database_errors(
    target: Database,
    observed: dict[str, Any],
    errors: list[dict[str, str]],
) -> list[dict[str, str]]:
    values = list(errors)
    if not observed["healthy"]:
        values.append(_error("database_unhealthy", target.identity, observed["health"]))
    return values


def _database_value(
    target: Database,
    observed: dict[str, Any],
    backup_state: dict[str, Any],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    current_error = next(
        (
            item["message"]
            for item in errors
            if item["code"] in {"assessment_failed", "backup_assessment_failed"}
        ),
        next((item["message"] for item in reversed(errors)), None),
    )
    return {
        "project": target.project,
        "role": target.role,
        "engine": target.engine,
        "running": observed["running"],
        "health": observed["health"],
        "healthy": observed["healthy"] and not errors,
        "image": target.image,
        "latest_backup": backup_state,
        "error": current_error[:500] if current_error else None,
    }


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
    try:
        from .host import certificate_ready

        acme_ok = certificate_ready(config)
    except (Error, OSError) as exc:
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


def _storage(
    config: Config,
    path: Path,
    errors: list[dict[str, str]],
    scope: str,
) -> dict[str, Any]:
    try:
        value = _disk(path, config.host.backup.min_free_gb)
    except (Error, OSError) as exc:
        value = {
            "path": str(path),
            "mount": None,
            "source": None,
            "filesystem": None,
            "total_bytes": None,
            "used_bytes": None,
            "free_bytes": None,
            "free_gb": None,
            "ok": False,
            "available": False,
        }
        errors.append(_error("disk_assessment_failed", scope, _message(config, exc)))
        return value
    if not value["ok"]:
        errors.append(
            _error("disk_low", scope, f"free space is below policy: {path} ({value['mount']})")
        )
    return value


def _disk(path: Path, minimum: int) -> dict[str, Any]:
    value = disk(path)
    value["free_gb"] = round(value["free_bytes"] / (1024**3), 2)
    value["ok"] = value["free_gb"] >= minimum
    value["available"] = True
    return value


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo else result.replace(tzinfo=UTC)


def backup_text(value: dict[str, Any]) -> str:
    if value["state"] == "loading":
        return "loading"
    if value["state"] == "disabled":
        return "disabled"
    if value["state"] == "stale":
        return "stale/missing"
    date = _date(value.get("time"))
    return date.strftime("%m-%d %H:%M") if date else "current"


def host_text(value: dict[str, Any]) -> str:
    if value.get("pending"):
        return "checking backups"
    return "healthy" if value["host"]["healthy"] else "needs attention"


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
