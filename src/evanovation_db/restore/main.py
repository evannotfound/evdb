from __future__ import annotations

import os
import re
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
from ..lifecycle import redact_logs
from ..lock import operation
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
    with operation(config.host, instance), _interrupts():
        staging = None
        result = None
        failure = None
        failure_step = "restore"
        started = False
        backup_id = None
        snapshot_id = snapshot if snapshot != "latest" else None
        try:
            if snapshot is not None:
                root = private_dir(config.host.state_dir / "restore")
                staging = Path(tempfile.mkdtemp(prefix="snapshot-", dir=root))
                backup = restic.restore(config.host, instance, snapshot, staging / "files")
            else:
                backup = Path(folder) if folder else _latest(config, instance)
            backup_id = backup.name
            started = True
            result = _restore(config, instance, backup, snapshot=snapshot_id)
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
            _record_error(
                config,
                instance,
                failure_step,
                failure,
                backup=backup_id,
                snapshot=snapshot_id,
            )
            raise failure
        assert result is not None
        return result


def _restore(
    config: Config, instance: Instance, backup: Path, *, snapshot: str | None = None
) -> dict:
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
            result = postgres.restore(config.host, instance, backup, name, work)
        else:
            result = kv.restore(config.host, instance, backup, name, work)
    except BaseException as exc:
        failure = exc
    try:
        with _defer_interrupts():
            _cleanup(name, work, instance.engine)
    except BaseException as exc:
        failure = RestoreError(f"{failure}; cleanup failed: {exc}") if failure is not None else exc

    if failure is not None:
        _record_error(
            config,
            instance,
            "restore",
            failure,
            backup=backup.name,
            snapshot=snapshot,
        )
        log(
            "restore_failed",
            host=config.host.id,
            group=instance.group,
            instance=instance.id,
            command="restore-check",
            step="restore",
            result="failed",
            duration=round(time.monotonic() - clock, 3),
            error=redact_logs(str(failure)),
        )
        raise failure

    state = _state_path(config, instance)
    data = read_json(state) if state.is_file() else {}
    if not isinstance(data, dict):
        data = {}
    restored = {
        "ok": True,
        "time": datetime.now(timezone.utc).isoformat(),
        "backup": backup.name,
        "result": result,
    }
    data["verifications"] = _record_verification(
        _verifications(data),
        {
            **restored,
            "snapshot": snapshot,
        },
    )
    data["restore"] = restored
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


def _cleanup(name: str, work: Path, engine: str) -> None:
    if work.exists():
        docker.exec(
            name,
            [
                "chown",
                "-R",
                f"{os.getuid()}:{os.getgid()}",
                "/var/lib/postgresql/data" if engine == "postgres" else "/data",
            ],
            user="0",
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
    }
    for key, value in expected.items():
        if data.get(key) != value:
            raise RestoreError(f"backup {key} does not match {value}")
    source = _source(data)
    target_major = _image_major(instance.image)
    if target_major < 1:
        raise RestoreError("locked target image major version is unavailable")
    _compatible(source, instance, target_major)


def _state_path(config: Config, instance: Instance) -> Path:
    return config.host.state_dir / "state" / instance.group / f"{instance.id}.json"


def _record_error(
    config: Config,
    instance: Instance,
    step: str,
    error: BaseException,
    *,
    backup: str | None = None,
    snapshot: str | None = None,
) -> None:
    state = _state_path(config, instance)
    data = read_json(state) if state.is_file() else {}
    if not isinstance(data, dict):
        data = {}
    now = datetime.now(timezone.utc).isoformat()
    message = redact_logs(str(error))
    data["verifications"] = _record_verification(
        _verifications(data),
        {
            "ok": False,
            "time": now,
            "backup": backup,
            "snapshot": snapshot,
            "error": message,
        },
    )
    errors = _errors(data)
    record = {
        "command": "restore-check",
        "step": step,
        "time": now,
        "message": message,
    }
    if backup:
        record["backup"] = backup
    if snapshot:
        record["snapshot"] = snapshot
    errors["restore-check"] = record
    data["errors"] = errors
    write_json(state, data)


def _errors(data: dict) -> dict:
    errors = dict(data.get("errors", {}))
    legacy = data.pop("error", None)
    if legacy:
        errors.setdefault(legacy.get("command", "legacy"), legacy)
    return errors


def _verifications(data: dict) -> dict[str, dict]:
    current = data.get("verifications")
    records = (
        {key: dict(value) for key, value in current.items() if isinstance(value, dict)}
        if isinstance(current, dict)
        else {}
    )
    restore = data.get("restore") if isinstance(data.get("restore"), dict) else {}
    if restore.get("ok") is True:
        records = _record_verification(
            records,
            {
                "ok": True,
                "time": restore.get("time"),
                "backup": restore.get("backup"),
                "snapshot": restore.get("snapshot"),
                "result": restore.get("result"),
            },
            overwrite=False,
        )
    errors = data.get("errors") if isinstance(data.get("errors"), dict) else {}
    failure = errors.get("restore-check") if isinstance(errors.get("restore-check"), dict) else {}
    if failure:
        records = _record_verification(
            records,
            {
                "ok": False,
                "time": failure.get("time"),
                "backup": failure.get("backup"),
                "snapshot": failure.get("snapshot"),
                "error": failure.get("message"),
            },
            overwrite=False,
        )
    return records


def _record_verification(
    records: dict[str, dict], record: dict, *, overwrite: bool = True
) -> dict[str, dict]:
    for kind in ("backup", "snapshot"):
        identity = record.get(kind)
        if not isinstance(identity, str) or not identity or identity == "latest":
            continue
        key = f"{kind}:{identity}"
        if overwrite or key not in records:
            records[key] = dict(record)
    return records


def _source(data: dict) -> dict[str, object]:
    engine = data.get("engine")
    version = data.get("version")
    image = data.get("image")
    if not isinstance(engine, str) or not engine:
        raise RestoreError("backup source engine is invalid")
    if not isinstance(version, str) or not version or version.lower() == "unknown":
        raise RestoreError("backup source version is unknown")
    if not isinstance(image, str) or not image:
        raise RestoreError("backup source image is invalid")
    major = _major(version) or _image_major(image)
    if major < 1:
        raise RestoreError("backup source major version is invalid")
    return {"engine": engine, "version": version, "major": major, "image": image}


def _compatible(source: dict[str, object], instance: Instance, target_major: int) -> None:
    if source.get("engine") != instance.engine:
        raise RestoreError(
            f"unsupported restore engine change from {source.get('engine')} to {instance.engine}"
        )
    source_major = source.get("major")
    if type(source_major) is not int or source_major < 1:
        raise RestoreError("backup source major version is invalid")
    if target_major < source_major:
        raise RestoreError(
            f"unsafe {instance.engine} restore from major {source_major} into older major "
            f"{target_major}"
        )


def _major(value: str) -> int:
    match = re.search(r"(?<![0-9])([0-9]+)(?:\.[0-9]+)?", value)
    return int(match.group(1)) if match is not None else 0


def _image_major(image: str) -> int:
    source = image.split("@", 1)[0]
    leaf = source.rsplit("/", 1)[-1]
    if ":" not in leaf:
        return 0
    return _major(leaf.rsplit(":", 1)[1])


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
