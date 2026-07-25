from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from . import manifest
from .config import Host, Instance
from .errors import ResticError
from .lock import lock
from .run import Result, run
from .secrets import path as secret_path


def init(host: Host, group: str) -> Result:
    with lock(_repo_lock(host, group), timeout=300):
        return _run(host, group, ["init", "--repository-version", "1"])


def upload(host: Host, instance: Instance, folder: Path) -> str:
    manifest.check(folder)
    with lock(_repo_lock(host, instance.group), timeout=3600):
        result = _run(
            host,
            instance.group,
            [
                "backup",
                str(folder),
                "--json",
                "--host",
                host.id,
                "--tag",
                f"host:{host.id}",
                "--tag",
                f"engine:{instance.engine}",
                "--tag",
                f"instance:{instance.id}",
            ],
            check=False,
        )
    if result.code != 0:
        raise ResticError(f"Restic upload failed ({result.code}): {result.err.strip()}")
    return _snapshot(result)


def forget(host: Host, group: str, *, dry_run: bool = True) -> Result:
    args = [
        "forget",
        "--keep-daily",
        str(host.retention["daily"]),
        "--keep-weekly",
        str(host.retention["weekly"]),
        "--keep-monthly",
        str(host.retention["monthly"]),
        "--group-by",
        "tags",
    ]
    if dry_run:
        args.append("--dry-run")
    with lock(_repo_lock(host, group), timeout=3600):
        _require_v1(host, group)
        return _run(host, group, args)


def prune(host: Host, group: str) -> Result:
    with lock(_repo_lock(host, group), timeout=3600):
        _require_v1(host, group)
        return _run(host, group, ["prune"], timeout=24 * 3600)


def check(
    host: Host,
    group: str,
    *,
    part: int | None = None,
    rotate: bool = False,
) -> Result:
    if part is not None and rotate:
        raise ResticError("check accepts either part or rotate")
    if rotate:
        part = check_part(host)
    args = ["check"]
    if part is not None:
        total = int(host.retention["data_parts"])
        if not 1 <= part <= total:
            raise ResticError(f"check part must be between 1 and {total}")
        args.append(f"--read-data-subset={part}/{total}")
    with lock(_repo_lock(host, group), timeout=3600):
        _require_v1(host, group)
        return _run(host, group, args, timeout=24 * 3600)


def check_part(host: Host, *, today: date | None = None) -> int:
    total = int(host.retention["data_parts"])
    if total < 1:
        raise ResticError("data_parts must be positive")
    current = today or date.today()
    return (current.toordinal() // 7) % total + 1


def restore(host: Host, instance: Instance, snapshot: str, target: Path) -> Path:
    selected = select_snapshot(host, instance, snapshot)
    target.mkdir(parents=True, exist_ok=False, mode=0o700)
    with lock(_repo_lock(host, instance.group), timeout=3600):
        _require_v1(host, instance.group)
        _run(
            host,
            instance.group,
            ["restore", selected["id"], "--target", str(target)],
            timeout=24 * 3600,
        )
    return _find_backup(target)


def snapshots(host: Host, instance: Instance) -> list[dict[str, Any]]:
    tags = _tags(host, instance)
    with lock(_repo_lock(host, instance.group), timeout=300):
        result = _run(
            host,
            instance.group,
            ["snapshots", "--json", "--tag", ",".join(sorted(tags))],
        )
    return _snapshots(result, tags)


def select_snapshot(host: Host, instance: Instance, snapshot: str) -> dict[str, Any]:
    if not isinstance(snapshot, str) or not snapshot or snapshot.strip() != snapshot:
        raise ResticError("snapshot id must be a non-empty exact id")
    if snapshot == "latest":
        items = snapshots(host, instance)
        if not items:
            raise ResticError(f"no snapshot exists for {host.id}/{instance.selector}")
        ranked = []
        for item in items:
            snapshot_id = item.get("id")
            timestamp = _snapshot_time(item.get("time"))
            if not isinstance(snapshot_id, str) or not snapshot_id or timestamp is None:
                continue
            ranked.append((timestamp, snapshot_id, item))
        if not ranked:
            raise ResticError(f"no valid snapshot exists for {host.id}/{instance.selector}")
        return max(ranked, key=lambda item: (item[0], item[1]))[2]
    with lock(_repo_lock(host, instance.group), timeout=300):
        result = _run(
            host,
            instance.group,
            ["snapshots", "--json", snapshot],
            check=False,
        )
    if result.code != 0:
        raise ResticError(f"snapshot is missing or ambiguous: {snapshot}")
    items = _snapshots(result)
    matches = [item for item in items if item.get("id") == snapshot]
    if len(matches) != 1:
        raise ResticError(f"snapshot id must match one exact snapshot: {snapshot}")
    expected = _tags(host, instance)
    tags = matches[0].get("tags")
    if not isinstance(tags, list) or not expected.issubset(tags):
        raise ResticError(f"snapshot does not belong to {host.id}/{instance.selector}")
    return matches[0]


def _run(
    host: Host,
    group: str,
    args: list[str],
    *,
    timeout: int = 7200,
    check: bool = True,
) -> Result:
    repo = host.repos[group]
    password = secret_path(host, None, "restic_password")
    rclone = host.state_dir / "rclone" / "rclone.conf"
    command = ["restic", "-r", repo, "--password-file", str(password), *args]
    protected = _protected_values(password, rclone)
    return run(
        command,
        timeout=timeout,
        env={"RCLONE_CONFIG": str(rclone)},
        secrets=protected,
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


def _snapshots(result: Result, required_tags: set[str] | None = None) -> list[dict[str, Any]]:
    try:
        data = json.loads(result.out)
    except json.JSONDecodeError as exc:
        raise ResticError("invalid Restic snapshots JSON") from exc
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ResticError("invalid Restic snapshots JSON")
    if required_tags is None:
        return data
    return [
        item
        for item in data
        if isinstance(item.get("tags"), list) and required_tags.issubset(item["tags"])
    ]


def _tags(host: Host, instance: Instance) -> set[str]:
    return {f"host:{host.id}", f"engine:{instance.engine}", f"instance:{instance.id}"}


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
            if "=" in line:
                value = line.split("=", 1)[1].strip()
                if value:
                    values.append(value)
    return tuple(dict.fromkeys(values))


def _repo_lock(host: Host, group: str) -> Path:
    repo = host.repos[group]
    digest = hashlib.sha256(repo.encode()).hexdigest()[:16]
    return host.lock_dir / f"restic-{digest}.lock"


def _require_v1(host: Host, group: str) -> None:
    config = _run(host, group, ["cat", "config"])
    try:
        version = json.loads(config.out).get("version")
    except (AttributeError, json.JSONDecodeError) as exc:
        raise ResticError("invalid repository config") from exc
    if version != 1:
        raise ResticError("repository format must be v1")


def _find_backup(target: Path) -> Path:
    manifests = list(target.rglob(manifest.NAME))
    if len(manifests) != 1:
        raise ResticError(f"snapshot must contain one {manifest.NAME}")
    folder = manifests[0].parent.resolve()
    root = target.resolve()
    if folder != root and root not in folder.parents:
        raise ResticError("restored backup escaped staging directory")
    manifest.check(folder)
    return folder
