from __future__ import annotations

import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .. import docker, manifest
from ..config import Config, Instance
from ..errors import RestoreError
from ..files import read_json, write_json
from ..log import write as log
from . import kv, postgres


def restore(config: Config, instance: Instance, folder: str | Path | None = None) -> dict:
    backup = Path(folder) if folder else _latest(config, instance)
    _safe(config, instance, backup)
    manifest.check(backup)
    name = f"evdb-restore-{instance.group}-{instance.id}-{uuid.uuid4().hex[:8]}"
    work = backup.parent / f".{name}-data"
    clock = time.monotonic()
    log(
        "restore_start",
        host=config.host.id,
        group=instance.group,
        instance=instance.id,
        command="restore-check",
        result="running",
    )
    try:
        if instance.engine == "postgres":
            result = postgres.restore(config.host, instance, backup, name)
        else:
            result = kv.restore(config.host, instance, backup, name)
        state = _state_path(config, instance)
        data = read_json(state) if state.is_file() else {}
        data["restore"] = {
            "ok": True,
            "time": datetime.now(timezone.utc).isoformat(),
            "backup": backup.name,
            "result": result,
        }
        data.pop("error", None)
        write_json(state, data)
        log(
            "restore_done",
            host=config.host.id,
            group=instance.group,
            instance=instance.id,
            command="restore-check",
            step="complete",
            result="success",
            duration=round(time.monotonic() - clock, 3),
        )
        return result
    except Exception as exc:
        state = _state_path(config, instance)
        data = read_json(state) if state.is_file() else {}
        data["error"] = {
            "command": "restore-check",
            "step": "restore",
            "time": datetime.now(timezone.utc).isoformat(),
            "message": str(exc),
        }
        write_json(state, data)
        log(
            "restore_failed",
            host=config.host.id,
            group=instance.group,
            instance=instance.id,
            command="restore-check",
            step="restore",
            result="failed",
            duration=round(time.monotonic() - clock, 3),
            error=str(exc),
        )
        raise
    finally:
        docker.remove(name)
        shutil.rmtree(work, ignore_errors=True)


def due(config: Config) -> Instance:
    ranked = []
    for instance in config.instances:
        if not instance.durable:
            continue
        path = _state_path(config, instance)
        data = read_json(path) if path.is_file() else {}
        value = data.get("restore", {}).get("time", "")
        ranked.append((value, instance.group, instance.id, instance))
    if not ranked:
        raise RestoreError("no durable instances")
    return min(ranked)[3]


def _latest(config: Config, instance: Instance) -> Path:
    root = config.host.backup_dir / instance.group / instance.id
    folders = [
        item for item in root.iterdir() if item.is_dir() and not item.name.endswith(".partial")
    ]
    if not folders:
        raise RestoreError(f"no local backup for {instance.group}/{instance.id}")
    return max(folders)


def _safe(config: Config, instance: Instance, folder: Path) -> None:
    resolved = folder.resolve()
    live = instance.data.resolve()
    if resolved == live or live in resolved.parents or resolved in live.parents:
        raise RestoreError("restore path overlaps live data")
    if not resolved.is_dir():
        raise RestoreError(f"backup folder does not exist: {resolved}")
    for item in config.instances:
        if resolved == item.data.resolve():
            raise RestoreError("restore path is a live data path")


def _state_path(config: Config, instance: Instance) -> Path:
    return config.host.state_dir / "state" / instance.group / f"{instance.id}.json"
