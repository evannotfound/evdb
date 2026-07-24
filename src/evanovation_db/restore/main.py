from __future__ import annotations

import os
import shutil
import signal
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import FrameType

from .. import docker, manifest, restic
from ..config import Config, Instance
from ..errors import RestoreError
from ..files import private_dir, read_json, write_json
from ..lock import lock
from ..log import write as log
from . import kv, postgres


def restore(
    config: Config,
    instance: Instance,
    folder: str | Path | None = None,
    *,
    snapshot: str | None = None,
) -> dict:
    if folder is not None and snapshot is not None:
        raise RestoreError("choose a local folder or Restic snapshot")
    path = config.host.lock_dir / f"{instance.group}-{instance.id}.lock"
    with lock(path), _interrupts():
        staging = None
        result = None
        failure = None
        failure_step = "restore"
        started = False
        try:
            if snapshot is not None:
                root = private_dir(config.host.state_dir / "restore")
                staging = Path(tempfile.mkdtemp(prefix="snapshot-", dir=root))
                backup = restic.restore(config.host, instance, snapshot, staging / "files")
            else:
                backup = Path(folder) if folder else _latest(config, instance)
            started = True
            result = _restore(config, instance, backup)
        except BaseException as exc:
            failure = exc
            failure_step = "restore" if started or snapshot is None else "snapshot"
        if staging is not None:
            try:
                with _defer_interrupts():
                    shutil.rmtree(staging)
            except BaseException as exc:
                failure = (
                    RestoreError(f"{failure}; staging cleanup failed: {exc}")
                    if failure is not None
                    else exc
                )
                failure_step = "staging-cleanup"
        if failure is not None:
            _record_error(config, instance, failure_step, failure)
            raise failure
        assert result is not None
        return result


def _restore(config: Config, instance: Instance, backup: Path) -> dict:
    _safe(config, backup)
    data = manifest.check(backup)
    _identity(config, instance, data)
    name = f"evdb-restore-{instance.group}-{instance.id}-{uuid.uuid4().hex[:12]}"
    if name in {item.container for item in config.instances}:
        raise RestoreError("restore container name overlaps a live container")
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
    result = None
    failure = None
    try:
        if instance.engine == "postgres":
            result = postgres.restore(config.host, instance, backup, name)
        else:
            result = kv.restore(config.host, instance, backup, name)
    except BaseException as exc:
        failure = exc
    try:
        with _defer_interrupts():
            _cleanup(name, work)
    except BaseException as exc:
        failure = RestoreError(f"{failure}; cleanup failed: {exc}") if failure is not None else exc

    if failure is not None:
        _record_error(config, instance, "restore", failure)
        log(
            "restore_failed",
            host=config.host.id,
            group=instance.group,
            instance=instance.id,
            command="restore-check",
            step="restore",
            result="failed",
            duration=round(time.monotonic() - clock, 3),
            error=str(failure),
        )
        raise failure

    state = _state_path(config, instance)
    data = read_json(state) if state.is_file() else {}
    data["restore"] = {
        "ok": True,
        "time": datetime.now(timezone.utc).isoformat(),
        "backup": backup.name,
        "result": result,
    }
    errors = _errors(data)
    errors.pop("restore-check", None)
    if errors:
        data["errors"] = errors
    else:
        data.pop("errors", None)
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
    assert result is not None
    return result


def _cleanup(name: str, work: Path) -> None:
    if work.exists():
        docker.exec(
            name,
            ["chown", "-R", f"{os.getuid()}:{os.getgid()}", "/data"],
            timeout=60,
            check=False,
        )
    docker.remove(name)
    if work.exists():
        shutil.rmtree(work)


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
    if not root.is_dir():
        raise RestoreError(f"no local backup for {instance.group}/{instance.id}")
    folders = [
        item for item in root.iterdir() if item.is_dir() and not item.name.endswith(".partial")
    ]
    if not folders:
        raise RestoreError(f"no local backup for {instance.group}/{instance.id}")
    return max(folders)


def _safe(config: Config, folder: Path) -> None:
    resolved = folder.resolve()
    for item in config.instances:
        live = item.data.resolve()
        if resolved == live or live in resolved.parents or resolved in live.parents:
            raise RestoreError("restore path overlaps live data")
    if not resolved.is_dir():
        raise RestoreError(f"backup folder does not exist: {resolved}")


def _identity(config: Config, instance: Instance, data: dict) -> None:
    expected = {
        "host": config.host.id,
        "group": instance.group,
        "instance": instance.id,
        "engine": instance.engine,
        "image": instance.target["image"],
    }
    for key, value in expected.items():
        if data.get(key) != value:
            raise RestoreError(f"backup {key} does not match {value}")


def _state_path(config: Config, instance: Instance) -> Path:
    return config.host.state_dir / "state" / instance.group / f"{instance.id}.json"


def _record_error(config: Config, instance: Instance, step: str, error: BaseException) -> None:
    state = _state_path(config, instance)
    data = read_json(state) if state.is_file() else {}
    errors = _errors(data)
    errors["restore-check"] = {
        "command": "restore-check",
        "step": step,
        "time": datetime.now(timezone.utc).isoformat(),
        "message": str(error),
    }
    data["errors"] = errors
    write_json(state, data)


def _errors(data: dict) -> dict:
    errors = dict(data.get("errors", {}))
    legacy = data.pop("error", None)
    if legacy:
        errors.setdefault(legacy.get("command", "legacy"), legacy)
    return errors


@contextmanager
def _interrupts():
    previous = {}

    def stop(signum: int, frame: FrameType | None) -> None:
        del frame
        raise RestoreError(f"restore interrupted by signal {signum}")

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, stop)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


@contextmanager
def _defer_interrupts():
    previous = {}
    pending = []

    def defer(signum: int, frame: FrameType | None) -> None:
        del frame
        pending.append(signum)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, defer)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    if pending:
        raise RestoreError(f"restore interrupted by signal {pending[0]}")
