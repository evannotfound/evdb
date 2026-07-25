from __future__ import annotations

import shutil
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import manifest, restic
from ..config import Config, Instance
from ..errors import BackupError
from ..files import finish, private_dir, read_json, require_space, write_json
from ..lifecycle import redact_logs
from ..lock import operation
from ..log import write as log
from . import dragonfly, postgres, redis

ENGINES = {"postgres": postgres.backup, "redis": redis.backup, "dragonfly": dragonfly.backup}


def backup(config: Config, instance: Instance, *, upload: bool = True) -> Path:
    host = config.host
    group_root = private_dir(host.backup_dir / instance.group / instance.id)
    require_space(group_root, host.min_free_gb)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    partial = group_root / f"{run_id}.partial"
    started = datetime.now(timezone.utc)
    clock = time.monotonic()

    with operation(host, instance):
        try:
            partial.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise BackupError(f"partial backup already exists: {partial}") from exc
        step = "engine"
        log(
            "backup_start",
            host=host.id,
            group=instance.group,
            instance=instance.id,
            command="backup",
            result="running",
        )
        try:
            facts = ENGINES[instance.engine](host, instance, partial, run_id)
            data = {
                "status": "complete",
                "host": host.id,
                "group": instance.group,
                "instance": instance.id,
                "engine": instance.engine,
                "image": instance.image,
                "started": started.isoformat(),
                "finished": datetime.now(timezone.utc).isoformat(),
                "version": facts.get("version", "unknown"),
                "facts": {
                    key: value for key, value in facts.items() if key not in {"files", "version"}
                },
                "files": manifest.files(partial, facts["files"]),
                "checks": ["size", "sha256", instance.engine],
                "upload": {"ok": False},
            }
            manifest.write(partial, data)
            folder = finish(partial)
            _state(host.state_dir, instance, data)
            if upload:
                step = "upload"
                snapshot = restic.upload(host, instance, folder)
                data["upload"] = {
                    "ok": True,
                    "snapshot": snapshot,
                    "time": datetime.now(timezone.utc).isoformat(),
                }
                manifest.write(folder, data)
                _state(host.state_dir, instance, data)
                _clean(group_root, keep=2)
        except Exception as exc:
            _failure(host.state_dir, instance, "backup", step, exc)
            log(
                "backup_failed",
                host=host.id,
                group=instance.group,
                instance=instance.id,
                command="backup",
                step=step,
                result="failed",
                duration=round(time.monotonic() - clock, 3),
                error=redact_logs(str(exc)),
            )
            raise
        log(
            "backup_done",
            host=host.id,
            group=instance.group,
            instance=instance.id,
            command="backup",
            step="complete",
            result="success",
            duration=round(time.monotonic() - clock, 3),
        )
        return folder


def backup_all(config: Config, instances: Iterable[Instance]) -> dict[str, str]:
    results = {}
    for instance in instances:
        if not instance.durable:
            continue
        key = f"{instance.group}/{instance.id}"
        try:
            results[key] = str(backup(config, instance))
        except Exception as exc:
            results[key] = f"error: {redact_logs(str(exc))}"
    return results


def history(config: Config, instance: Instance) -> list[dict[str, Any]]:
    if not instance.durable:
        raise BackupError(f"backups are disabled for {instance.selector}")
    state = _read_state(config, instance)
    rows: list[dict[str, Any]] = []
    root = config.host.backup_dir / instance.group / instance.id
    if root.is_dir():
        for folder in root.iterdir():
            if not folder.is_dir() or folder.name.endswith(".partial"):
                continue
            try:
                data = manifest.read(folder)
            except (BackupError, OSError, ValueError):
                continue
            if data.get("status") != "complete" or not _matches(config, instance, data):
                continue
            upload = data.get("upload") if isinstance(data.get("upload"), dict) else {}
            snapshot = _string(upload.get("snapshot"))
            timestamp = _string(data.get("finished")) or _run_time(folder.name)
            if timestamp is None:
                continue
            rows.append(
                {
                    "backup": folder.name,
                    "time": timestamp,
                    "snapshot": snapshot,
                    "local": True,
                    "remote": False,
                    "verification": _verification(state, folder.name, snapshot),
                }
            )

    by_snapshot = {row["snapshot"]: row for row in rows if row["snapshot"]}
    by_backup = {row["backup"]: row for row in rows}
    for snapshot in restic.snapshots(config.host, instance):
        snapshot_id = _string(snapshot.get("id"))
        timestamp = _string(snapshot.get("time"))
        if snapshot_id is None or timestamp is None:
            continue
        backup_id = _snapshot_backup(snapshot) or snapshot_id
        row = by_snapshot.get(snapshot_id) or by_backup.get(backup_id)
        if row is None:
            row = {
                "backup": backup_id,
                "time": timestamp,
                "snapshot": snapshot_id,
                "local": False,
                "remote": True,
                "verification": _verification(state, backup_id, snapshot_id),
            }
            rows.append(row)
            by_backup[backup_id] = row
        else:
            row["snapshot"] = snapshot_id
            row["remote"] = True
            if row["time"] is None:
                row["time"] = timestamp
        by_snapshot[snapshot_id] = row

    for row in rows:
        row["source"] = (
            "local+remote"
            if row["local"] and row["remote"]
            else "local"
            if row["local"]
            else "remote"
        )
    return sorted(rows, key=lambda item: _sort_time(item["time"]), reverse=True)


def select_backup(
    config: Config,
    instance: Instance,
    value: str | None,
) -> dict[str, Any]:
    rows = history(config, instance)
    if value is None:
        if not rows:
            raise BackupError(f"no completed backup for {instance.selector}")
        return rows[0]
    if not value or value.strip() != value or "/" in value:
        raise BackupError("backup selector must be an exact backup or snapshot id")
    matches = [row for row in rows if value in {row["backup"], row["snapshot"]}]
    if not matches:
        raise BackupError(f"backup does not exist for {instance.selector}: {value}")
    if len(matches) != 1:
        raise BackupError(f"backup selector is ambiguous for {instance.selector}: {value}")
    return matches[0]


def _state(root: Path, instance: Instance, data: dict) -> None:
    path = root / "state" / instance.group / f"{instance.id}.json"
    previous = read_json(path) if path.is_file() else {}
    if not isinstance(previous, dict):
        previous = {}
    prior_backup = previous.get("backup") if isinstance(previous.get("backup"), dict) else {}
    prior_upload = (
        prior_backup.get("upload") if isinstance(prior_backup.get("upload"), dict) else {}
    )
    if not isinstance(previous.get("upload"), dict) and prior_upload.get("ok") is True:
        previous["upload"] = dict(prior_upload)
    previous["backup"] = data
    upload = data.get("upload") if isinstance(data.get("upload"), dict) else {}
    if upload.get("ok") is True:
        previous["upload"] = dict(upload)
    errors = _errors(previous)
    errors.pop("backup", None)
    if errors:
        previous["errors"] = errors
    else:
        previous.pop("errors", None)
    write_json(path, previous)


def _failure(root: Path, instance: Instance, command: str, step: str, error: Exception) -> None:
    path = root / "state" / instance.group / f"{instance.id}.json"
    previous = read_json(path) if path.is_file() else {}
    if not isinstance(previous, dict):
        previous = {}
    errors = _errors(previous)
    errors[command] = {
        "command": command,
        "step": step,
        "time": datetime.now(timezone.utc).isoformat(),
        "message": redact_logs(str(error)),
    }
    previous["errors"] = errors
    write_json(path, previous)


def _errors(data: dict) -> dict:
    errors = dict(data.get("errors", {}))
    legacy = data.pop("error", None)
    if legacy:
        errors.setdefault(legacy.get("command", "legacy"), legacy)
    return errors


def _clean(root: Path, keep: int) -> None:
    uploaded = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder.name.endswith(".partial"):
            continue
        try:
            data = manifest.read(folder)
        except BackupError:
            continue
        if data.get("upload", {}).get("ok"):
            uploaded.append(folder)
    for folder in sorted(uploaded, reverse=True)[keep:]:
        shutil.rmtree(folder)


def _read_state(config: Config, instance: Instance) -> dict[str, Any]:
    path = config.host.state_dir / "state" / instance.group / f"{instance.id}.json"
    try:
        data = read_json(path) if path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _verification(state: dict[str, Any], backup_id: str, snapshot: str | None) -> dict[str, Any]:
    records = state.get("verifications") if isinstance(state.get("verifications"), dict) else {}
    keys = [f"backup:{backup_id}"]
    if snapshot is not None:
        keys.append(f"snapshot:{snapshot}")
    matches = [records[key] for key in keys if isinstance(records.get(key), dict)]
    if matches:
        record = max(matches, key=lambda item: _sort_time(_string(item.get("time")) or ""))
        if record.get("ok") is True:
            return {"state": "verified", "time": _string(record.get("time")), "error": None}
        return {
            "state": "failed",
            "time": _string(record.get("time")),
            "error": redact_logs(_string(record.get("error")) or "verification failed"),
        }
    restore = state.get("restore") if isinstance(state.get("restore"), dict) else {}
    selected = {backup_id, snapshot} - {None}
    if restore.get("ok") is True and restore.get("backup") in selected:
        return {"state": "verified", "time": _string(restore.get("time")), "error": None}
    errors = state.get("errors") if isinstance(state.get("errors"), dict) else {}
    failure = errors.get("restore-check") if isinstance(errors.get("restore-check"), dict) else {}
    if failure and failure.get("backup") in selected:
        return {
            "state": "failed",
            "time": _string(failure.get("time")),
            "error": redact_logs(_string(failure.get("message")) or "verification failed"),
        }
    return {"state": "unverified", "time": None, "error": None}


def _matches(config: Config, instance: Instance, data: dict[str, Any]) -> bool:
    return all(
        data.get(key) == value
        for key, value in {
            "host": config.host.id,
            "group": instance.group,
            "instance": instance.id,
            "engine": instance.engine,
        }.items()
    )


def _snapshot_backup(snapshot: dict[str, Any]) -> str | None:
    paths = snapshot.get("paths")
    if not isinstance(paths, list) or len(paths) != 1 or not isinstance(paths[0], str):
        return None
    return Path(paths[0]).name or None


def _run_time(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def _sort_time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
