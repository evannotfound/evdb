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
        backup_time = _time(upload.get("time"))
        restore_time = _time(restore.get("time"))
        backup_stale = backup_time is None or current - backup_time > timedelta(hours=26)
        restore_stale = restore_time is None or current - restore_time > timedelta(days=30)
        failed = failed or backup_stale or restore_stale
        rows.append(
            {
                "group": instance.group,
                "instance": instance.id,
                "engine": instance.engine,
                "backup": backup_time.isoformat() if backup_time else None,
                "snapshot": upload.get("snapshot"),
                "backup_stale": backup_stale,
                "restore": restore_time.isoformat() if restore_time else None,
                "restore_stale": restore_stale,
                "error": data.get("error"),
            }
        )
    return rows, failed


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    result = datetime.fromisoformat(value)
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
