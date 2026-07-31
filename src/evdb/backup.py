from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .config import protected, rclone_owner, validate_image
from .engines import get
from .errors import BackupError, ConfigError, Error, ResticError
from .files import (
    finish,
    managed_dir,
    private_dir,
    read_json,
    require_file,
    require_space,
    write_json,
)
from .files import hash as file_hash
from .lock import lock, operation
from .models import Config, Database, Operator
from .run import Result, redact, run

MANIFEST = "backup.json"
RESTIC_MINIMUM = (0, 17, 0)
MISSING_REPOSITORY = 10
RESTIC = Path("/usr/bin/restic")
RCLONE = Path("/usr/bin/rclone")


def initialize(config: Config) -> None:
    require_version()
    with lock(_repository_lock(config), timeout=300):
        result = _restic(config, ["cat", "config"], check=False, timeout=300)
        if result.code == MISSING_REPOSITORY:
            _restic(config, ["init", "--repository-version", "1"], timeout=300)
            result = _restic(config, ["cat", "config"], timeout=300)
        elif result.code != 0:
            detail = result.err.strip() or result.out.strip() or "no output"
            raise ResticError(
                f"repository {config.host.backup.repository} is unavailable "
                f"({result.code}): {detail}"
            )
        _require_format(config, result)


def require_version() -> None:
    result = run([str(RESTIC), "version"], timeout=30, check=False)
    match = re.search(r"restic ([0-9]+)\.([0-9]+)\.([0-9]+)", result.out)
    if result.code or not match:
        raise ResticError(
            "Restic version could not be determined; version 0.17 or newer is required"
        )
    version = tuple(int(value) for value in match.groups())
    if version < RESTIC_MINIMUM:
        raise ResticError("Restic 0.17 or newer is required")


def repository_ready(config: Config) -> bool:
    with lock(_repository_lock(config), timeout=30):
        result = _restic(config, ["cat", "config"], check=False, timeout=60)
    if result.code == MISSING_REPOSITORY:
        return False
    if result.code != 0:
        detail = result.err.strip() or result.out.strip() or "no output"
        raise ResticError(
            f"repository {config.host.backup.repository} is unavailable ({result.code}): {detail}"
        )
    _require_format(config, result)
    return True


def create(
    config: Config,
    database: Database,
    *,
    purpose: str = "manual",
) -> dict[str, Any]:
    if not database.durable:
        raise BackupError(f"backups are disabled for cache database {database.identity}")
    if purpose not in {"manual", "scheduled"}:
        raise BackupError(f"invalid backup purpose: {purpose}")
    if not database.compose.is_file():
        raise BackupError(f"database generated files are missing: {database.identity}")
    operator = rclone_owner(config.host.backup.rclone_config)
    root = _backup_root(config, database)
    require_space(root, config.host.backup.min_free_gb)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    partial = root / f"{run_id}.partial"
    with operation(config, database, timeout=config.host.timeouts["backup"]):
        private_dir(partial)
        folder = None
        started = datetime.now(UTC).isoformat()
        try:
            facts = get(database.engine).backup(database, partial, run_id)
            names = facts.pop("files")
            record = {
                "status": "complete",
                "backup": run_id,
                "host": config.host.id,
                "project": database.project,
                "role": database.role,
                "engine": database.engine,
                "image": database.image,
                "started": started,
                "finished": datetime.now(UTC).isoformat(),
                "version": facts.pop("version"),
                "format": facts.pop("format"),
                "purpose": purpose,
                "facts": facts,
                "files": manifest_files(partial, names),
                "checks": ["size", "sha256", database.engine],
                "upload": {"ok": False, "backup": run_id},
            }
            manifest_write(partial, record)
            folder = finish(partial)
            _handoff(folder, operator)
        except BaseException:
            shutil.rmtree(folder or partial, ignore_errors=True)
            raise
        try:
            snapshot = _upload(config, database, folder)
        except (Error, OSError) as exc:
            raise ResticError(
                f"{database.identity}: upload to {config.host.backup.repository} failed; "
                f"local backup retained at {folder}: {exc}"
            ) from exc
        record["upload"] = {
            "ok": True,
            "backup": run_id,
            "snapshot": snapshot,
            "time": datetime.now(UTC).isoformat(),
        }
        try:
            manifest_write(folder, record, mode=0o400, owner=(operator.uid, operator.gid))
        finally:
            _handoff(folder, operator)
        _clean(config, database, root, keep=2)
        return {
            **record,
            "folder": str(folder),
            "snapshot": record["upload"].get("snapshot"),
            "repository": config.host.backup.repository,
        }


def create_all(config: Config) -> dict[str, dict[str, Any]]:
    results = {}
    for database in config.databases:
        if not database.durable:
            continue
        try:
            value = create(config, database, purpose="scheduled")
            results[database.identity] = {
                "ok": True,
                "backup": value["backup"],
                "snapshot": value["snapshot"],
            }
        except (Error, OSError) as exc:
            results[database.identity] = {"ok": False, "error": _message(config, exc)}
    return results


def history(config: Config, database: Database) -> list[dict[str, Any]]:
    if not database.durable:
        raise BackupError(f"backups are disabled for {database.identity}")
    rows = []
    root = config.paths.role_backups(database.project, database.role)
    if root.is_dir():
        for folder in root.iterdir():
            if not folder.is_dir() or folder.name.endswith(".partial"):
                continue
            try:
                record = manifest_check(folder)
            except BackupError, OSError, ValueError:
                continue
            if not _matches(config, database, record) or record["backup"] != folder.name:
                continue
            upload = record["upload"]
            rows.append(
                {
                    "backup": folder.name,
                    "time": record["finished"],
                    "purpose": record["purpose"],
                    "snapshot": upload.get("snapshot") if upload.get("ok") else None,
                    "local": True,
                    "remote": False,
                }
            )
    by_snapshot = {row["snapshot"]: row for row in rows if row["snapshot"]}
    by_backup = {row["backup"]: row for row in rows}
    for snapshot in snapshots(config, database):
        snapshot_id = snapshot.get("id")
        timestamp = snapshot.get("time")
        if not isinstance(snapshot_id, str) or not isinstance(timestamp, str):
            continue
        backup_id = _tag(snapshot, "backup")
        purpose = _tag(snapshot, "purpose")
        if backup_id is None or purpose is None:
            continue
        row = by_snapshot.get(snapshot_id) or by_backup.get(backup_id)
        if row is None:
            row = {
                "backup": backup_id,
                "time": timestamp,
                "purpose": purpose,
                "snapshot": snapshot_id,
                "local": False,
                "remote": True,
            }
            rows.append(row)
        else:
            row.update(snapshot=snapshot_id, time=timestamp, remote=True)
    for row in rows:
        row["source"] = (
            "local+remote"
            if row["local"] and row["remote"]
            else ("local" if row["local"] else "remote")
        )
    return sorted(rows, key=lambda row: _time(row["time"]), reverse=True)


def snapshots(config: Config, database: Database) -> list[dict[str, Any]]:
    tags = _identity_tags(config, database)
    args = ["snapshots", "--json", "--tag", ",".join(sorted(tags))]
    with lock(_repository_lock(config), timeout=300):
        result = _restic(config, args, timeout=300)
    try:
        values = json.loads(result.out)
    except json.JSONDecodeError as exc:
        raise ResticError("invalid Restic snapshots JSON") from exc
    if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
        raise ResticError("invalid Restic snapshots JSON")
    required = tags | {f"engine:{database.engine}"}
    return [
        item
        for item in values
        if isinstance(item.get("tags"), list)
        and required.issubset(item["tags"])
        and sum(isinstance(tag, str) and tag.startswith("backup:") for tag in item["tags"]) == 1
        and sum(isinstance(tag, str) and tag.startswith("purpose:") for tag in item["tags"]) == 1
        and _tag(item, "purpose") in {"manual", "scheduled"}
    ]


def manifest_write(
    folder: str | Path,
    data: dict[str, Any],
    *,
    mode: int = 0o600,
    owner: tuple[int, int] | None = None,
) -> Path:
    path = Path(folder) / MANIFEST
    write_json(path, data, mode=mode, owner=owner)
    return path


def manifest_read(folder: str | Path) -> dict[str, Any]:
    path = Path(folder) / MANIFEST
    if not path.is_file() or path.is_symlink():
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
        if path.is_absolute() or ".." in path.parts or not name or name in seen:
            raise BackupError(f"unsafe backup file name: {name}")
        seen.add(name)
        target = require_file(root / name)
        result.append({"name": name, "size": target.stat().st_size, "sha256": file_hash(target)})
    return result


def manifest_check(folder: str | Path) -> dict[str, Any]:
    root = Path(folder)
    data = manifest_read(root)
    required = {
        "status",
        "backup",
        "host",
        "project",
        "role",
        "engine",
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
    formats = {
        "postgres": "postgres-custom-v1",
        "redis": "redis-rdb-v1",
        "dragonfly": "dragonfly-dfs-v1",
    }
    if data.get("format") != formats.get(data.get("engine")):
        raise BackupError(f"backup format does not match engine: {root}")
    if (data.get("role") == "postgres") != (data.get("engine") == "postgres"):
        raise BackupError(f"backup role does not match engine: {root}")
    if not all(
        isinstance(data.get(name), str) and data[name]
        for name in ("host", "project", "role", "engine", "image", "started", "finished", "version")
    ):
        raise BackupError(f"invalid backup identity, image, version, or time: {root}")
    try:
        validate_image(data["image"], "backup image")
    except ConfigError as exc:
        raise BackupError(f"invalid backup image: {root}") from exc
    if _time(data["started"]) == datetime.min.replace(tzinfo=UTC) or _time(
        data["finished"]
    ) == datetime.min.replace(tzinfo=UTC):
        raise BackupError(f"invalid backup timestamp: {root}")
    if not isinstance(data.get("facts"), dict) or not isinstance(data.get("checks"), list):
        raise BackupError(f"invalid backup facts or checks: {root}")
    if data.get("purpose") not in {"manual", "scheduled"}:
        raise BackupError(f"invalid backup purpose: {root}")
    backup_id = data.get("backup")
    if not isinstance(backup_id, str) or PurePosixPath(backup_id).name != backup_id:
        raise BackupError(f"invalid backup identity: {root}")
    upload = data.get("upload")
    if (
        not isinstance(upload, dict)
        or type(upload.get("ok")) is not bool
        or upload.get("backup") != backup_id
        or not set(upload).issubset({"ok", "backup", "snapshot", "time"})
        or ("snapshot" in upload and not isinstance(upload["snapshot"], str))
        or ("time" in upload and not isinstance(upload["time"], str))
    ):
        raise BackupError(f"invalid backup upload record: {root}")
    if not isinstance(data.get("files"), list):
        raise BackupError(f"invalid backup files: {root}")
    recorded = set()
    for item in data["files"]:
        if not isinstance(item, dict) or set(item) != {"name", "size", "sha256"}:
            raise BackupError(f"invalid backup file record: {root}")
        name, size, checksum = item["name"], item["size"], item["sha256"]
        path = PurePosixPath(name) if isinstance(name, str) else PurePosixPath("..")
        if (
            path.is_absolute()
            or ".." in path.parts
            or name in recorded
            or type(size) is not int
            or size < 0
            or not isinstance(checksum, str)
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
        ):
            raise BackupError(f"unsafe backup file record: {name}")
        recorded.add(name)
        target = require_file(root / name, mode=None)
        if target.stat().st_size != size or file_hash(target) != checksum:
            raise BackupError(f"backup file changed: {name}")
    actual = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.name != MANIFEST
    }
    if actual - recorded:
        raise BackupError(f"backup has unlisted file: {min(actual - recorded)}")
    return data


def _upload(config: Config, database: Database, folder: Path) -> str:
    record = manifest_check(folder)
    if not _matches(config, database, record):
        raise ResticError("backup identity does not match the selected database")
    tags = [
        f"host:{config.host.id}",
        f"project:{database.project}",
        f"role:{database.role}",
        f"engine:{database.engine}",
        f"backup:{record['backup']}",
        f"purpose:{record['purpose']}",
    ]
    args = ["backup", str(folder), "--json", "--host", config.host.id]
    for tag in tags:
        args.extend(["--tag", tag])
    with lock(_repository_lock(config), timeout=3600):
        result = _restic(config, args, check=False, timeout=7200)
    if result.code:
        detail = result.err.strip() or result.out.strip() or "no output"
        raise ResticError(
            f"Restic upload to {config.host.backup.repository} failed ({result.code}): {detail}"
        )
    return _snapshot_id(result)


def _restic(
    config: Config,
    args: list[str],
    *,
    timeout: int = 7200,
    check: bool = True,
) -> Result:
    operator = rclone_owner(config.host.backup.rclone_config)
    credentials = protected(config)
    descriptor = os.memfd_create("evdb-restic-password", os.MFD_CLOEXEC)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as password:
            password.write((config.secrets.restic_password + "\n").encode())
            password.flush()
            password.seek(0)
        os.fchown(descriptor, operator.uid, operator.gid)
        os.fchmod(descriptor, 0o400)
        identity = {}
        if os.geteuid() == 0:
            identity = {
                "user": operator.uid,
                "group": operator.gid,
                "extra_groups": operator.groups,
            }
        elif os.geteuid() != operator.uid:
            raise ResticError(
                f"Restic must run as root or rclone owner {operator.name} ({operator.uid})"
            )
        elif os.getegid() != operator.gid or set(os.getgroups()) - {operator.gid} != set(
            operator.groups
        ):
            raise ResticError(f"Restic process groups do not match rclone owner {operator.name}")
        return run(
            [
                str(RESTIC),
                "--password-file",
                f"/proc/self/fd/{descriptor}",
                "-o",
                f"rclone.program={RCLONE}",
                "-r",
                config.host.backup.repository,
                *args,
            ],
            timeout=timeout,
            env={
                "HOME": str(operator.home),
                "USER": operator.name,
                "LOGNAME": operator.name,
                "RCLONE_CONFIG": str(config.host.backup.rclone_config),
                "RESTIC_CACHE_DIR": str(operator.home / ".cache/restic"),
            },
            replace_env=True,
            pass_fds=(descriptor,),
            secrets=credentials,
            check=check,
            **identity,
        )
    finally:
        os.close(descriptor)


def _require_format(config: Config, result: Result) -> None:
    try:
        version = json.loads(result.out).get("version")
    except (AttributeError, json.JSONDecodeError) as exc:
        raise ResticError(
            f"repository {config.host.backup.repository} returned invalid config"
        ) from exc
    if version != 1:
        raise ResticError(
            f"repository {config.host.backup.repository} uses format {version}; "
            "v1 requires format 1"
        )


def _snapshot_id(result: Result) -> str:
    values = []
    for number, line in enumerate(result.out.splitlines(), 1):
        if line.strip():
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ResticError(f"invalid Restic JSON on line {number}") from exc
    snapshot = values[-1].get("snapshot_id") if values else None
    if not snapshot or values[-1].get("message_type") != "summary":
        raise ResticError("Restic output has no final snapshot summary")
    return str(snapshot)


def _repository_lock(config: Config) -> Path:
    digest = hashlib.sha256(config.host.backup.repository.encode()).hexdigest()[:16]
    return config.paths.locks / f"restic-{digest}.lock"


def _backup_root(config: Config, database: Database) -> Path:
    project = config.paths.backups / database.project
    root = project / database.role
    for folder in (config.paths.state, config.paths.backups, project, root):
        managed_dir(folder, 0o711)
    return root


def _handoff(folder: Path, operator: Operator) -> None:
    def failed(error: OSError) -> None:
        raise error

    try:
        for current, directories, files in os.walk(
            folder, topdown=False, onerror=failed, followlinks=False
        ):
            root = Path(current)
            for name in files:
                _readonly(root / name, operator, directory=False)
            for name in directories:
                _readonly(root / name, operator, directory=True)
        _readonly(folder, operator, directory=True)
    except OSError as exc:
        raise BackupError(f"completed backup cannot be handed to rclone owner: {folder}") from exc


def _readonly(path: Path, operator: Operator, *, directory: bool) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    if directory:
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        details = os.fstat(descriptor)
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected(details.st_mode):
            raise OSError(f"completed backup contains an unsafe path: {path}")
        os.fchown(descriptor, operator.uid, operator.gid)
        os.fchmod(descriptor, 0o500 if directory else 0o400)
    finally:
        os.close(descriptor)


def _identity_tags(config: Config, database: Database) -> set[str]:
    return {
        f"host:{config.host.id}",
        f"project:{database.project}",
        f"role:{database.role}",
    }


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


def _tag(snapshot: dict[str, Any], name: str) -> str | None:
    prefix = f"{name}:"
    values = [
        item.removeprefix(prefix)
        for item in snapshot.get("tags", [])
        if isinstance(item, str) and item.startswith(prefix)
    ]
    return values[0] if len(values) == 1 else None


def _clean(config: Config, database: Database, root: Path, keep: int) -> None:
    uploaded = []
    for folder in root.iterdir():
        if folder.is_dir() and not folder.name.endswith(".partial"):
            try:
                record = manifest_check(folder)
                upload = record["upload"]
                if (
                    record["backup"] == folder.name
                    and _matches(config, database, record)
                    and upload.get("ok") is True
                    and isinstance(upload.get("snapshot"), str)
                    and upload["snapshot"]
                ):
                    uploaded.append(folder)
            except BackupError, OSError, ValueError:
                pass
    for folder in sorted(uploaded, reverse=True)[keep:]:
        shutil.rmtree(folder)


def _time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except AttributeError, ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return result if result.tzinfo else result.replace(tzinfo=UTC)


def _message(config: Config, error: BaseException) -> str:
    text = redact(str(error).replace("\0", ""), protected(config))[:500]
    return text or error.__class__.__name__
