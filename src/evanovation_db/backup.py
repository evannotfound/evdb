from __future__ import annotations

import os
import shutil
import time
from contextlib import nullcontext, suppress
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from . import restic
from .config import Config, Database, MachineState, load_state, write_state
from .engines import dragonfly, postgres, redis
from .errors import BackupError
from .files import finish, private_dir, read_json, require_file, require_space, write_json
from .files import hash as file_hash
from .images import validate_source
from .lock import operation
from .log import sanitize
from .log import write as log_write

MANIFEST = "backup.json"


def create(
    config: Config,
    database: Database,
    *,
    purpose: str = "scheduled",
    upload: bool = True,
    lock_held: bool = False,
    state: MachineState | None = None,
) -> dict[str, Any]:
    if not database.durable:
        raise BackupError(f"backups are disabled for cache database {database.identity}")
    if purpose not in {"scheduled", "manual", "safety"}:
        raise BackupError(f"invalid backup purpose: {purpose}")
    started_at = time.monotonic()
    log_write(
        "backup_operation",
        host=config.host.id,
        project=database.project,
        role=database.role,
        engine=database.engine,
        command="backup create",
        step="start",
        result="started",
        purpose=purpose,
    )
    try:
        current = state or load_state(config)
        role = current.roles.get(database.identity)
        if role is None or not role.installed:
            raise BackupError(f"database is not installed: {database.identity}")
        root = private_dir(config.paths.role_backups(database.project, database.role))
        require_space(root, config.host.backup.min_free_gb)
    except BaseException as exc:
        with suppress(Exception):
            _error(config, database, "backup", "preflight", exc)
        log_write(
            "backup_operation",
            host=config.host.id,
            project=database.project,
            role=database.role,
            engine=database.engine,
            command="backup create",
            step="preflight",
            result="failed",
            purpose=purpose,
            duration=round(time.monotonic() - started_at, 3),
            error=str(exc),
        )
        raise
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    partial = root / f"{run_id}.partial"
    context = nullcontext() if lock_held else operation(config, database)
    with context:
        private_dir(partial)
        started = datetime.now(timezone.utc).isoformat()
        step = "engine"
        try:
            facts = _engine(database).backup(config, database, partial, run_id, current)
            names = facts.pop("files")
            data = {
                "status": "complete",
                "backup": run_id,
                "host": config.host.id,
                "project": database.project,
                "role": database.role,
                "engine": database.engine,
                "source_image": database.image,
                "image": role.images["primary"].image,
                "started": started,
                "finished": datetime.now(timezone.utc).isoformat(),
                "version": facts.pop("version"),
                "format": facts.pop("format"),
                "purpose": purpose,
                "facts": facts,
                "files": manifest_files(partial, names),
                "checks": ["size", "sha256", database.engine],
                "upload": {"ok": False, "backup": run_id},
            }
            manifest_write(partial, data)
            folder = finish(partial)
            _record(config, database, "backup", _summary(data, folder.name))
            if upload:
                step = "upload"
                upload_root = root / f".{run_id}.upload.partial"
                try:
                    upload_folder = upload_root / run_id
                    shutil.copytree(folder, upload_folder, copy_function=os.link)
                    manifest_write(
                        upload_folder,
                        {
                            **data,
                            "upload": {"ok": True, "backup": run_id},
                        },
                    )
                    snapshot = restic.upload(config, database, upload_folder)
                except Exception as exc:
                    _error(config, database, "backup", "upload", exc, backup=folder.name)
                    raise
                finally:
                    shutil.rmtree(upload_root, ignore_errors=True)
                data["upload"] = {
                    "ok": True,
                    "backup": run_id,
                    "snapshot": snapshot,
                    "time": datetime.now(timezone.utc).isoformat(),
                }
                manifest_write(folder, data)
                _record(config, database, "backup", _summary(data, folder.name))
                _record(config, database, "upload", dict(data["upload"], backup=folder.name))
                _clean(root, keep=2)
            log_write(
                "backup_operation",
                host=config.host.id,
                project=database.project,
                role=database.role,
                engine=database.engine,
                command="backup create",
                step="complete",
                result="success",
                purpose=purpose,
                backup=folder.name,
                snapshot=data["upload"].get("snapshot"),
                duration=round(time.monotonic() - started_at, 3),
            )
            return {
                **data,
                "backup": folder.name,
                "folder": str(folder),
                "snapshot": data["upload"].get("snapshot"),
            }
        except Exception as exc:
            if partial.exists():
                _error(config, database, "backup", "engine", exc)
            log_write(
                "backup_operation",
                host=config.host.id,
                project=database.project,
                role=database.role,
                engine=database.engine,
                command="backup create",
                step=step,
                result="failed",
                purpose=purpose,
                duration=round(time.monotonic() - started_at, 3),
                error=str(exc),
            )
            raise


def create_all(config: Config) -> dict[str, str]:
    results = {}
    for database in config.databases:
        if not database.durable:
            continue
        try:
            result = create(config, database)
            results[database.identity] = result["backup"]
        except Exception as exc:
            results[database.identity] = f"error: {_message(exc)}"
    return results


def history(config: Config, database: Database) -> list[dict[str, Any]]:
    if not database.durable:
        raise BackupError(f"backups are disabled for {database.identity}")
    state = load_state(config).roles.get(database.identity)
    operations = state.operations if state else {}
    rows = []
    root = config.paths.role_backups(database.project, database.role)
    if root.is_dir():
        for folder in root.iterdir():
            if not folder.is_dir() or folder.name.endswith(".partial"):
                continue
            try:
                data = manifest_check(folder)
            except (BackupError, OSError, ValueError):
                continue
            if not _matches(config, database, data):
                continue
            if data["backup"] != folder.name:
                continue
            upload = data["upload"]
            snapshot = upload.get("snapshot") if upload.get("ok") else None
            rows.append(
                {
                    "backup": folder.name,
                    "time": data["finished"],
                    "purpose": data["purpose"],
                    "snapshot": snapshot,
                    "local": True,
                    "remote": False,
                    "verification": _verification(operations, folder.name, snapshot),
                }
            )
    by_snapshot = {row["snapshot"]: row for row in rows if row["snapshot"]}
    by_backup = {row["backup"]: row for row in rows}
    for snapshot in restic.snapshots(config, database):
        snapshot_id = snapshot.get("id")
        timestamp = snapshot.get("time")
        if not isinstance(snapshot_id, str) or not isinstance(timestamp, str):
            continue
        backup_id = _snapshot_backup(snapshot) or snapshot_id
        row = by_snapshot.get(snapshot_id) or by_backup.get(backup_id)
        if row is None:
            row = {
                "backup": backup_id,
                "time": timestamp,
                "purpose": _snapshot_tag(snapshot, "purpose") or "unknown",
                "snapshot": snapshot_id,
                "local": False,
                "remote": True,
                "verification": _verification(operations, backup_id, snapshot_id),
            }
            rows.append(row)
        else:
            row["snapshot"] = snapshot_id
            row["remote"] = True
    for row in rows:
        row["source"] = (
            "local+remote"
            if row["local"] and row["remote"]
            else ("local" if row["local"] else "remote")
        )
    return sorted(rows, key=lambda item: _time(item["time"]), reverse=True)


def select(config: Config, database: Database, value: str | None) -> dict[str, Any]:
    rows = history(config, database)
    if value in {None, "latest"}:
        if not rows:
            raise BackupError(f"no completed backup for {database.identity}")
        return rows[0]
    if not value or value.strip() != value or "/" in value:
        raise BackupError("backup selector must be an exact backup or snapshot id")
    matches = [row for row in rows if value in {row["backup"], row["snapshot"]}]
    if len(matches) != 1:
        raise BackupError(f"backup does not uniquely exist for {database.identity}: {value}")
    return matches[0]


def test(config: Config, database: Database, value: str | None = None) -> dict[str, Any]:
    started = time.monotonic()
    fields = {
        "host": config.host.id,
        "project": database.project,
        "role": database.role,
        "engine": database.engine,
        "command": "backup test",
    }
    log_write("backup_operation", **fields, step="start", result="started")
    try:
        result = _test(config, database, value)
    except BaseException as exc:
        log_write(
            "backup_operation",
            **fields,
            step="complete",
            result="failed",
            duration=round(time.monotonic() - started, 3),
            error=str(exc),
        )
        raise
    log_write(
        "backup_operation",
        **fields,
        step="complete",
        result="success",
        duration=round(time.monotonic() - started, 3),
    )
    return result


def _test(config: Config, database: Database, value: str | None = None) -> dict[str, Any]:
    from .restore import verify

    with operation(config, database, timeout=config.host.timeouts["restore"]):
        selected = select(config, database, value)
        folder, staging = materialize(config, database, selected)
        try:
            result = verify(config, database, folder, keep=False)
            record = {
                "ok": True,
                "time": datetime.now(timezone.utc).isoformat(),
                "backup": selected["backup"],
                "snapshot": selected["snapshot"],
                "result": result["result"],
            }
            _verification_record(config, database, record)
            return record
        except Exception as exc:
            record = {
                "ok": False,
                "time": datetime.now(timezone.utc).isoformat(),
                "backup": selected["backup"],
                "snapshot": selected["snapshot"],
                "error": _message(exc),
            }
            _verification_record(config, database, record)
            raise
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)


def due(config: Config) -> Database:
    state = load_state(config)
    ranked = []
    for database in config.databases:
        if not database.durable:
            continue
        role = state.roles.get(database.identity)
        operations = role.operations if role else {}
        records = list(operations.get("verifications", {}).values())
        records.append(operations.get("backup_test", {}))
        successful = [
            record.get("time", "")
            for record in records
            if isinstance(record, dict) and record.get("ok") is True
        ]
        latest = max(successful, key=_time, default="")
        ranked.append((_time(latest), database.identity, database))
    if not ranked:
        raise BackupError("no durable databases")
    return min(ranked)[2]


def materialize(
    config: Config, database: Database, selected: dict[str, Any]
) -> tuple[Path, Path | None]:
    root = config.paths.role_backups(database.project, database.role)
    local = root / selected["backup"]
    if selected["local"] and local.is_dir():
        if manifest_check(local).get("backup") != selected["backup"]:
            raise BackupError("selected local backup identity does not match its manifest")
        return local, None
    snapshot = selected.get("snapshot")
    if not isinstance(snapshot, str):
        raise BackupError("selected backup is not available")
    restore_root = config.paths.restores
    if restore_root.is_symlink():
        raise BackupError(f"restore staging root must not be a symlink: {restore_root}")
    private_dir(restore_root)
    staging = restore_root / f"download-{database.project}-{database.role}"
    if staging.is_symlink() or (staging.exists() and not staging.is_dir()):
        raise BackupError(f"restore download staging is unsafe: {staging}")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(mode=0o700)
    try:
        folder = restic.restore(config, database, snapshot, staging / "files")
        if manifest_check(folder).get("backup") != selected["backup"]:
            raise BackupError("selected snapshot identity does not match its manifest")
        return folder, staging
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def manifest_write(folder: str | Path, data: dict[str, Any]) -> Path:
    path = Path(folder) / MANIFEST
    write_json(path, data)
    return path


def manifest_read(folder: str | Path) -> dict[str, Any]:
    path = Path(folder) / MANIFEST
    if not path.is_file():
        raise BackupError(f"missing {MANIFEST}: {folder}")
    data = read_json(path)
    if not isinstance(data, dict):
        raise BackupError(f"invalid {MANIFEST}: {folder}")
    return data


def manifest_files(folder: str | Path, names: list[str]) -> list[dict[str, Any]]:
    root = Path(folder)
    result = []
    seen = set()
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or name in seen or not name:
            raise BackupError(f"unsafe backup file name: {name}")
        seen.add(name)
        target = require_file(root / name)
        result.append({"name": name, "size": target.stat().st_size, "sha256": file_hash(target)})
    return result


def manifest_check(folder: str | Path) -> dict[str, Any]:
    return _manifest_check(folder, verify_files=True)


def manifest_contract(folder: str | Path) -> dict[str, Any]:
    return _manifest_check(folder, verify_files=False)


def _manifest_check(folder: str | Path, *, verify_files: bool) -> dict[str, Any]:
    root = Path(folder)
    data = manifest_read(root)
    required = {
        "status",
        "backup",
        "host",
        "project",
        "role",
        "engine",
        "source_image",
        "image",
        "started",
        "finished",
        "version",
        "format",
        "purpose",
        "facts",
        "files",
        "checks",
        "upload",
    }
    if set(data) != required or data.get("status") != "complete":
        raise BackupError(f"invalid or incomplete {MANIFEST}: {root}")
    if data["role"] not in {"postgres", "kv"} or data["engine"] not in {
        "postgres",
        "redis",
        "dragonfly",
    }:
        raise BackupError(f"invalid backup role or engine: {root}")
    if data["purpose"] not in {"scheduled", "manual", "safety"}:
        raise BackupError(f"invalid backup purpose: {root}")
    expected_format = {
        "postgres": "postgres-custom-v1",
        "redis": "redis-rdb-v1",
        "dragonfly": "dragonfly-dfs-v1",
    }[data["engine"]]
    if data["format"] != expected_format:
        raise BackupError(f"backup format does not match engine: {root}")
    if not all(
        isinstance(data[name], str) and data[name]
        for name in ("host", "project", "role", "source_image", "image")
    ):
        raise BackupError(f"invalid backup identity or image: {root}")
    try:
        validate_source(data["source_image"], "backup source image")
    except Exception as exc:
        raise BackupError(f"invalid backup source image: {root}") from exc
    digest = data["image"].rsplit("@sha256:", 1)
    if (
        len(digest) != 2
        or not digest[0]
        or len(digest[1]) != 64
        or any(character not in "0123456789abcdef" for character in digest[1])
    ):
        raise BackupError(f"invalid locked backup image: {root}")
    if not all(isinstance(data[name], str) and data[name] for name in ("started", "finished")):
        raise BackupError(f"invalid backup timestamps: {root}")
    if not isinstance(data["version"], str) or not isinstance(data["facts"], dict):
        raise BackupError(f"invalid backup version or facts: {root}")
    if not isinstance(data["checks"], list) or not all(
        isinstance(item, str) for item in data["checks"]
    ):
        raise BackupError(f"invalid backup checks: {root}")
    backup_id = data["backup"]
    if (
        not isinstance(backup_id, str)
        or not backup_id
        or PurePosixPath(backup_id).name != backup_id
    ):
        raise BackupError(f"invalid backup identity: {root}")
    if (data["role"] == "postgres") != (data["engine"] == "postgres"):
        raise BackupError(f"backup role and engine do not match: {root}")
    if not isinstance(data["files"], list) or not isinstance(data["upload"], dict):
        raise BackupError(f"invalid backup files or upload: {root}")
    upload = data["upload"]
    if (
        not isinstance(upload.get("ok"), bool)
        or upload.get("backup") != backup_id
        or not set(upload).issubset({"ok", "backup", "snapshot", "time"})
        or ("snapshot" in upload and not isinstance(upload["snapshot"], str))
        or ("time" in upload and not isinstance(upload["time"], str))
    ):
        raise BackupError(f"invalid backup upload record: {root}")
    recorded = set()
    for item in data["files"]:
        if not isinstance(item, dict) or set(item) != {"name", "size", "sha256"}:
            raise BackupError(f"invalid backup file record: {root}")
        name = item["name"]
        size = item["size"]
        checksum = item["sha256"]
        path = PurePosixPath(name) if isinstance(name, str) else PurePosixPath("..")
        if (
            path.is_absolute()
            or ".." in path.parts
            or name in recorded
            or type(size) is not int
            or size < 0
            or not isinstance(checksum, str)
            or len(checksum) != 64
            or any(character not in "0123456789abcdef" for character in checksum)
        ):
            raise BackupError(f"unsafe backup file record: {name}")
        recorded.add(name)
        if verify_files:
            target = require_file(root / name)
            if target.stat().st_size != item["size"] or file_hash(target) != item["sha256"]:
                raise BackupError(f"backup file changed: {name}")
    if verify_files:
        actual = {
            str(path.relative_to(root))
            for path in root.rglob("*")
            if path.is_file() and path.name != MANIFEST
        }
        if actual - recorded:
            raise BackupError(f"backup has unlisted file: {min(actual - recorded)}")
    return data


def _engine(database: Database):
    if database.engine == "postgres":
        return postgres
    if database.engine == "redis":
        return redis
    return dragonfly


def _record(config: Config, database: Database, name: str, value: dict[str, Any]) -> None:
    state = load_state(config)
    role = state.roles[database.identity]
    operations = dict(role.operations)
    operations[name] = value
    errors = dict(operations.get("errors", {}))
    errors.pop(name, None)
    if errors:
        operations["errors"] = errors
    else:
        operations.pop("errors", None)
    roles = {**state.roles, database.identity: replace(role, operations=operations)}
    write_state(config, replace(state, roles=roles))


def _error(
    config: Config,
    database: Database,
    command: str,
    step: str,
    error: Exception,
    **fields,
) -> None:
    state = load_state(config)
    role = state.roles[database.identity]
    operations = dict(role.operations)
    errors = dict(operations.get("errors", {}))
    errors[command] = {
        "command": command,
        "step": step,
        "time": datetime.now(timezone.utc).isoformat(),
        "message": _message(error),
        **fields,
    }
    operations["errors"] = errors
    write_state(
        config,
        replace(
            state,
            roles={**state.roles, database.identity: replace(role, operations=operations)},
        ),
    )


def _verification_record(config: Config, database: Database, record: dict[str, Any]) -> None:
    state = load_state(config)
    role = state.roles[database.identity]
    operations = dict(role.operations)
    records = dict(operations.get("verifications", {}))
    for kind in ("backup", "snapshot"):
        value = record.get(kind)
        if value:
            records[f"{kind}:{value}"] = record
    operations["verifications"] = records
    operations["backup_test"] = record
    write_state(
        config,
        replace(
            state,
            roles={**state.roles, database.identity: replace(role, operations=operations)},
        ),
    )


def _verification(operations: dict, backup_id: str, snapshot: str | None) -> dict[str, Any]:
    records = operations.get("verifications", {})
    matches = [records.get(f"backup:{backup_id}")]
    if snapshot:
        matches.append(records.get(f"snapshot:{snapshot}"))
    values = [item for item in matches if isinstance(item, dict)]
    if not values:
        return {"state": "unverified", "time": None, "error": None}
    record = max(values, key=lambda item: _time(item.get("time", "")))
    return {
        "state": "verified" if record.get("ok") else "failed",
        "time": record.get("time"),
        "error": record.get("error"),
    }


def _summary(data: dict[str, Any], backup_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "backup": backup_id,
        "time": data["finished"],
        "purpose": data["purpose"],
        "engine": data["engine"],
        "upload": data["upload"],
    }


def _clean(root: Path, keep: int) -> None:
    uploaded = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder.name.endswith(".partial"):
            continue
        try:
            if manifest_read(folder).get("upload", {}).get("ok"):
                uploaded.append(folder)
        except BackupError:
            continue
    for folder in sorted(uploaded, reverse=True)[keep:]:
        shutil.rmtree(folder)


def _matches(config: Config, database: Database, data: dict[str, Any]) -> bool:
    return all(
        data.get(name) == value
        for name, value in {
            "host": config.host.id,
            "project": database.project,
            "role": database.role,
            "engine": database.engine,
        }.items()
    )


def _snapshot_backup(snapshot: dict[str, Any]) -> str | None:
    value = _snapshot_tag(snapshot, "backup")
    if value:
        return value
    paths = snapshot.get("paths")
    if isinstance(paths, list) and len(paths) == 1 and isinstance(paths[0], str):
        return Path(paths[0]).name or None
    return None


def _snapshot_tag(snapshot: dict[str, Any], name: str) -> str | None:
    tags = snapshot.get("tags")
    if not isinstance(tags, list):
        return None
    prefix = f"{name}:"
    matches = [
        item.removeprefix(prefix)
        for item in tags
        if isinstance(item, str) and item.startswith(prefix)
    ]
    return matches[0] if len(matches) == 1 else None


def _time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _message(error: BaseException) -> str:
    value = sanitize(str(error).replace("\x00", ""))
    return value[:500] or error.__class__.__name__
