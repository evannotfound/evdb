from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import docker
from ..config import Host, Instance
from ..files import require_file
from ..secrets import read as read_secret
from . import kv


def backup(host: Host, instance: Instance, folder: Path, run_id: str) -> dict[str, Any]:
    password = read_secret(host, instance, "password")
    name = f"evdb-{run_id}"
    source = f"/data/{name}.rdb"
    try:
        kv.command(instance, password, ["SAVE", "RDB", name], timeout=1800)
        docker.copy(f"{instance.container}:{source}", folder / "dump.rdb", timeout=1800)
        require_file(folder / "dump.rdb")
        facts = kv.facts(instance, password)
    finally:
        docker.exec(instance.container, ["rm", "-f", "--", source], timeout=60)
    return {**facts, "files": ["dump.rdb"]}
