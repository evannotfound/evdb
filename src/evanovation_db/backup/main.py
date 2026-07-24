from __future__ import annotations

import shutil
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from .. import manifest, restic
from ..config import Config, Instance
from ..errors import BackupError
from ..files import finish, private_dir, read_json, require_space, write_json
from ..lock import lock
from ..log import write as log
from . import dragonfly, postgres, redis

ENGINES = {"postgres": postgres.backup, "redis": redis.backup, "dragonfly": dragonfly.backup}


def backup(config: Config, instance: Instance, *, upload: bool = True) -> Path:
    host = config.host
    group_root = private_dir(host.backup_dir / instance.group / instance.id)
    require_space(group_root, host.min_free_gb)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    partial = group_root / f"{run_id}.partial"
    started = datetime.now(timezone.utc)
    clock = time.monotonic()

    with lock(host.lock_dir / f"{instance.group}-{instance.id}.lock"):
        private_dir(partial)
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
                error=str(exc),
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
        key = f"{instance.group}/{instance.id}"
        try:
            results[key] = str(backup(config, instance))
        except Exception as exc:
            results[key] = f"error: {exc}"
    return results


def _state(root: Path, instance: Instance, data: dict) -> None:
    path = root / "state" / instance.group / f"{instance.id}.json"
    previous = read_json(path) if path.is_file() else {}
    previous["backup"] = data
    previous.pop("error", None)
    write_json(path, previous)


def _failure(root: Path, instance: Instance, command: str, step: str, error: Exception) -> None:
    path = root / "state" / instance.group / f"{instance.id}.json"
    previous = read_json(path) if path.is_file() else {}
    previous["error"] = {
        "command": command,
        "step": step,
        "time": datetime.now(timezone.utc).isoformat(),
        "message": str(error),
    }
    write_json(path, previous)


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
