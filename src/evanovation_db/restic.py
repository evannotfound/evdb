from __future__ import annotations

import json
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
    snapshot = _snapshot(result)
    if result.code != 0 or not snapshot:
        raise ResticError(f"Restic upload failed ({result.code}): {result.err.strip()}")
    return snapshot


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
        return _run(host, group, args)


def prune(host: Host, group: str) -> Result:
    with lock(_repo_lock(host, group), timeout=3600):
        return _run(host, group, ["prune"], timeout=24 * 3600)


def check(host: Host, group: str, *, part: int | None = None) -> Result:
    args = ["check"]
    if part is not None:
        total = int(host.retention["data_parts"])
        if not 1 <= part <= total:
            raise ResticError(f"check part must be between 1 and {total}")
        args.append(f"--read-data-subset={part}/{total}")
    with lock(_repo_lock(host, group), timeout=3600):
        config = _run(host, group, ["cat", "config"])
        if json.loads(config.out).get("version") != 1:
            raise ResticError("repository format must be v1")
        return _run(host, group, args, timeout=24 * 3600)


def snapshots(host: Host, group: str, instance: str) -> list[dict[str, Any]]:
    with lock(_repo_lock(host, group), timeout=300):
        result = _run(host, group, ["snapshots", "--json", "--tag", f"instance:{instance}"])
    data = json.loads(result.out)
    return data if isinstance(data, list) else []


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
    return run(command, timeout=timeout, env={"RCLONE_CONFIG": str(rclone)}, check=check)


def _snapshot(result: Result) -> str | None:
    snapshot = None
    for line in result.out.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("message_type") == "summary":
            snapshot = item.get("snapshot_id")
    return snapshot


def _repo_lock(host: Host, group: str) -> Path:
    return host.lock_dir / f"restic-{group}.lock"
