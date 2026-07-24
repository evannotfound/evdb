from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import docker
from ..config import Host, Instance
from ..errors import BackupError
from ..files import require_file
from ..secrets import read as read_secret
from . import kv


def backup(host: Host, instance: Instance, folder: Path, run_id: str) -> dict[str, Any]:
    password = read_secret(host, instance, "password")
    name = f"evdb-{run_id}"
    source = f"/data/{name}.rdb"
    existing = docker.exec(instance.container, ["stat", source], timeout=60, check=False)
    if existing.code == 0:
        raise BackupError(f"{instance.id}: Dragonfly backup source already exists")
    error = None
    try:
        kv.command(instance, password, ["SAVE", "RDB", name], timeout=1800)
        docker.copy(f"{instance.container}:{source}", folder / "dump.rdb", timeout=1800)
        require_file(folder / "dump.rdb")
        facts = kv.facts(instance, password)
    except Exception as exc:
        error = exc
        raise
    finally:
        cleanup = docker.exec(
            instance.container,
            ["rm", "-f", "--", source],
            timeout=60,
            check=False,
        )
        if cleanup.code != 0 and error is None:
            raise BackupError(f"{instance.id}: Dragonfly backup source cleanup failed")
    return {**facts, "files": ["dump.rdb"]}
