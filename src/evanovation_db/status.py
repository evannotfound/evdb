from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import Config
from .files import read_json


def get(config: Config, *, now: datetime | None = None) -> tuple[list[dict], bool]:
    current = now or datetime.now(timezone.utc)
    rows = []
    failed = False
    for instance in config.instances:
        if not instance.durable:
            continue
        path = config.host.state_dir / "state" / instance.group / f"{instance.id}.json"
        data = read_json(path) if path.is_file() else {}
        backup = data.get("backup", {})
        upload = backup.get("upload", {})
        restore = data.get("restore", {})
        local_time = _time(backup.get("finished"))
        upload_time = _time(upload.get("time"))
        restore_time = _time(restore.get("time"))
        backup_stale = upload_time is None or current - upload_time > timedelta(hours=26)
        restore_stale = restore_time is None or current - restore_time > timedelta(days=30)
        errors = data.get("errors", {})
        if data.get("error"):
            errors = {**errors, "legacy": data["error"]}
        error = errors or None
        failed = failed or backup_stale or restore_stale or bool(error)
        rows.append(
            {
                "group": instance.group,
                "instance": instance.id,
                "engine": instance.engine,
                "backup": local_time.isoformat() if local_time else None,
                "upload": upload_time.isoformat() if upload_time else None,
                "upload_ok": bool(upload.get("ok")),
                "snapshot": upload.get("snapshot"),
                "backup_stale": backup_stale,
                "restore": restore_time.isoformat() if restore_time else None,
                "restore_ok": bool(restore.get("ok")),
                "restore_stale": restore_stale,
                "error": error,
            }
        )
    return rows, failed


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    result = datetime.fromisoformat(value)
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
