from __future__ import annotations

import hashlib
import json
import shutil
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config, Database
from .errors import ResticError
from .files import write_text
from .lock import lock
from .log import sanitize
from .log import write as log_write
from .run import Result, run


def init(config: Config, role: str) -> Result:
    with lock(_repo_lock(config, role), timeout=300):
        return _run(config, role, ["init", "--repository-version", "1"])


def upload(config: Config, database: Database, folder: Path) -> str:
    from .backup import manifest_check

    started = time.monotonic()
    log_write(
        "restic_operation",
        host=config.host.id,
        project=database.project,
        role=database.role,
        engine=database.engine,
        command="restic upload",
        step="start",
        result="started",
    )
    try:
        record = manifest_check(folder)
        expected = {
            "host": config.host.id,
            "project": database.project,
            "role": database.role,
            "engine": database.engine,
        }
        for name, value in expected.items():
            if record.get(name) != value:
                raise ResticError(f"backup {name} does not match {value}")
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
        with lock(_repo_lock(config, database.role), timeout=3600):
            result = _run(config, database.role, args, check=False)
        if result.code != 0:
            detail = sanitize(result.err.strip())
            raise ResticError(f"Restic upload failed ({result.code}): {detail}")
        snapshot = _snapshot(result)
        log_write(
            "restic_operation",
            host=config.host.id,
            project=database.project,
            role=database.role,
            engine=database.engine,
            command="restic upload",
            step="complete",
            result="success",
            backup=record["backup"],
            snapshot=snapshot,
            duration=round(time.monotonic() - started, 3),
        )
        return snapshot
    except BaseException as exc:
        log_write(
            "restic_operation",
            host=config.host.id,
            project=database.project,
            role=database.role,
            engine=database.engine,
            command="restic upload",
            step="complete",
            result="failed",
            duration=round(time.monotonic() - started, 3),
            error=str(exc),
        )
        raise


def forget(config: Config, database: Database, *, dry_run: bool = True) -> Result:
    command = "restic retention dry-run" if dry_run else "restic retention"
    return _maintenance(
        config,
        database.role,
        command,
        lambda: _forget(config, database, dry_run=dry_run),
        database=database,
    )


def _forget(config: Config, database: Database, *, dry_run: bool) -> Result:
    args = [
        "forget",
        "--keep-daily",
        str(config.host.backup.retention["daily"]),
        "--keep-weekly",
        str(config.host.backup.retention["weekly"]),
        "--keep-monthly",
        str(config.host.backup.retention["monthly"]),
        "--group-by",
        "host",
        "--host",
        config.host.id,
        "--tag",
        ",".join(sorted(_tags(config, database))),
    ]
    if dry_run:
        args.append("--dry-run")
    with lock(_repo_lock(config, database.role), timeout=3600):
        _require_v1(config, database.role)
        review, signature = _retention_review(config, database)
        if not dry_run:
            try:
                approved = review.read_text().strip()
            except OSError:
                approved = ""
            if approved != signature:
                raise ResticError(
                    f"retention requires a reviewed dry run; run "
                    f"evdb backup retention {database.identity} --dry-run"
                )
        result = _run(config, database.role, args)
        return result


def approve_retention(config: Config, database: Database) -> None:
    review, signature = _retention_review(config, database)
    write_text(review, signature + "\n", mode=0o600)


def prune(config: Config, role: str) -> Result:
    return _maintenance(config, role, "restic prune", lambda: _prune(config, role))


def _prune(config: Config, role: str) -> Result:
    with lock(_repo_lock(config, role), timeout=3600):
        _require_v1(config, role)
        return _run(config, role, ["prune"], timeout=24 * 3600)


def check(
    config: Config,
    role: str,
    *,
    part: int | None = None,
    rotate: bool = False,
) -> Result:
    if part is not None and rotate:
        raise ResticError("check accepts either part or rotate")
    if rotate:
        part = check_part(config)
    args = ["check"]
    if part is not None:
        total = config.host.backup.retention["data_parts"]
        if not 1 <= part <= total:
            raise ResticError(f"check part must be between 1 and {total}")
        args.append(f"--read-data-subset={part}/{total}")
    return _maintenance(config, role, "restic check", lambda: _check(config, role, args))


def _check(config: Config, role: str, args: list[str]) -> Result:
    with lock(_repo_lock(config, role), timeout=3600):
        _require_v1(config, role)
        return _run(config, role, args, timeout=24 * 3600)


def _maintenance(config: Config, role: str, command: str, call, *, database=None) -> Result:
    started = time.monotonic()
    fields = {
        "host": config.host.id,
        "project": database.project if database else None,
        "role": database.role if database else role,
        "engine": database.engine if database else None,
        "command": command,
    }
    log_write("restic_operation", **fields, step="start", result="started")
    try:
        result = call()
    except BaseException as exc:
        log_write(
            "restic_operation",
            **fields,
            step="complete",
            result="failed",
            duration=round(time.monotonic() - started, 3),
            error=str(exc),
        )
        raise
    log_write(
        "restic_operation",
        **fields,
        step="complete",
        result="success",
        duration=round(time.monotonic() - started, 3),
    )
    return result


def check_part(config: Config, *, today: date | None = None) -> int:
    total = config.host.backup.retention["data_parts"]
    current = today or date.today()
    return (current.toordinal() // 7) % total + 1


def restore(config: Config, database: Database, snapshot: str, target: Path) -> Path:
    from .backup import MANIFEST, manifest_check

    started = time.monotonic()
    created = False
    log_write(
        "restic_operation",
        host=config.host.id,
        project=database.project,
        role=database.role,
        engine=database.engine,
        command="restic restore",
        step="start",
        result="started",
        snapshot=snapshot,
    )
    try:
        selected = select_snapshot(config, database, snapshot)
        target.mkdir(parents=True, exist_ok=False, mode=0o700)
        created = True
        with lock(_repo_lock(config, database.role), timeout=3600):
            _require_v1(config, database.role)
            _run(
                config,
                database.role,
                ["restore", selected["id"], "--target", str(target)],
                timeout=24 * 3600,
            )
        manifests = list(target.rglob(MANIFEST))
        if len(manifests) != 1:
            raise ResticError(f"snapshot must contain one {MANIFEST}")
        folder = manifests[0].parent.resolve()
        root = target.resolve()
        if folder != root and root not in folder.parents:
            raise ResticError("restored backup escaped staging directory")
        record = manifest_check(folder)
        tags = selected.get("tags", [])
        backup_tags = [
            tag.removeprefix("backup:")
            for tag in tags
            if isinstance(tag, str) and tag.startswith("backup:")
        ]
        if len(backup_tags) != 1 or backup_tags[0] != record["backup"]:
            raise ResticError("snapshot backup identity does not match its manifest")
        log_write(
            "restic_operation",
            host=config.host.id,
            project=database.project,
            role=database.role,
            engine=database.engine,
            command="restic restore",
            step="complete",
            result="success",
            backup=record["backup"],
            snapshot=selected["id"],
            duration=round(time.monotonic() - started, 3),
        )
        return folder
    except BaseException as exc:
        if created:
            shutil.rmtree(target, ignore_errors=True)
        log_write(
            "restic_operation",
            host=config.host.id,
            project=database.project,
            role=database.role,
            engine=database.engine,
            command="restic restore",
            step="complete",
            result="failed",
            snapshot=snapshot,
            duration=round(time.monotonic() - started, 3),
            error=str(exc),
        )
        raise


def snapshots(config: Config, database: Database) -> list[dict[str, Any]]:
    tags = _tags(config, database)
    with lock(_repo_lock(config, database.role), timeout=300):
        result = _run(
            config,
            database.role,
            ["snapshots", "--json", "--tag", ",".join(sorted(tags))],
        )
    engine_tag = f"engine:{database.engine}"
    return [
        item
        for item in _snapshots(result, tags)
        if isinstance(item.get("tags"), list)
        and engine_tag in item["tags"]
        and sum(isinstance(tag, str) and tag.startswith("backup:") for tag in item["tags"]) == 1
    ]


def select_snapshot(config: Config, database: Database, snapshot: str) -> dict[str, Any]:
    if not isinstance(snapshot, str) or not snapshot or snapshot.strip() != snapshot:
        raise ResticError("snapshot id must be a non-empty exact id")
    if snapshot == "latest":
        items = snapshots(config, database)
        ranked = [
            (_snapshot_time(item.get("time")), item.get("id"), item)
            for item in items
            if _snapshot_time(item.get("time")) is not None and isinstance(item.get("id"), str)
        ]
        if not ranked:
            raise ResticError(f"no snapshot exists for {config.host.id}/{database.identity}")
        return max(ranked, key=lambda item: (item[0], item[1]))[2]
    with lock(_repo_lock(config, database.role), timeout=300):
        result = _run(
            config,
            database.role,
            ["snapshots", "--json", snapshot],
            check=False,
        )
    if result.code != 0:
        raise ResticError(f"snapshot is missing or ambiguous: {snapshot}")
    matches = [item for item in _snapshots(result) if item.get("id") == snapshot]
    if len(matches) != 1:
        raise ResticError(f"snapshot id must match one exact snapshot: {snapshot}")
    tags = matches[0].get("tags")
    required = _tags(config, database) | {f"engine:{database.engine}"}
    backup_tags = (
        [tag for tag in tags if isinstance(tag, str) and tag.startswith("backup:")]
        if isinstance(tags, list)
        else []
    )
    if not isinstance(tags, list) or not required.issubset(tags) or len(backup_tags) != 1:
        raise ResticError(f"snapshot does not belong to {config.host.id}/{database.identity}")
    return matches[0]


def _run(
    config: Config,
    role: str,
    args: list[str],
    *,
    timeout: int = 7200,
    check: bool = True,
) -> Result:
    if role not in {"postgres", "kv"}:
        raise ResticError(f"unknown repository role: {role}")
    repo = config.host.backup.repos[role]
    password = config.paths.secrets / "restic-password"
    rclone = config.paths.rclone / "rclone.conf"
    command = ["restic", "-r", repo, "--password-file", str(password), *args]
    return run(
        command,
        timeout=timeout,
        env={"RCLONE_CONFIG": str(rclone)},
        secrets=(repo, *_protected_values(password, rclone)),
        check=check,
    )


def _snapshot(result: Result) -> str:
    items = []
    for number, line in enumerate(result.out.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ResticError(f"invalid Restic JSON on line {number}") from exc
    if not items or items[-1].get("message_type") != "summary":
        raise ResticError("Restic output has no final summary")
    snapshot = items[-1].get("snapshot_id")
    if not snapshot:
        raise ResticError("Restic final summary has no snapshot id")
    return str(snapshot)


def _snapshots(result: Result, required: set[str] | None = None) -> list[dict[str, Any]]:
    try:
        data = json.loads(result.out)
    except json.JSONDecodeError as exc:
        raise ResticError("invalid Restic snapshots JSON") from exc
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ResticError("invalid Restic snapshots JSON")
    if required is None:
        return data
    return [
        item
        for item in data
        if isinstance(item.get("tags"), list) and required.issubset(item["tags"])
    ]


def _tags(config: Config, database: Database) -> set[str]:
    return {
        f"host:{config.host.id}",
        f"project:{database.project}",
        f"role:{database.role}",
    }


def _snapshot_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo is not None else result.replace(tzinfo=timezone.utc)


def _protected_values(*paths: Path) -> tuple[str, ...]:
    values = []
    for path in paths:
        try:
            text = path.read_text()
        except OSError:
            continue
        if text.strip():
            values.append(text.strip())
        for line in text.splitlines():
            if "=" in line and line.split("=", 1)[1].strip():
                values.append(line.split("=", 1)[1].strip())
    return tuple(dict.fromkeys(values))


def _repo_lock(config: Config, role: str) -> Path:
    repo = config.host.backup.repos[role]
    digest = hashlib.sha256(repo.encode()).hexdigest()[:16]
    return config.paths.locks / f"restic-{digest}.lock"


def _retention_review(config: Config, database: Database) -> tuple[Path, str]:
    data = {
        "repository": config.host.backup.repos[database.role],
        "tags": sorted(_tags(config, database)),
        "retention": {
            name: config.host.backup.retention[name] for name in ("daily", "weekly", "monthly")
        },
    }
    signature = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    path = config.paths.state / "retention-reviews" / f"{database.project}-{database.role}.reviewed"
    return path, signature


def _require_v1(config: Config, role: str) -> None:
    value = _run(config, role, ["cat", "config"])
    try:
        version = json.loads(value.out).get("version")
    except (AttributeError, json.JSONDecodeError) as exc:
        raise ResticError("invalid repository config") from exc
    if version != 1:
        raise ResticError("repository format must be v1")
