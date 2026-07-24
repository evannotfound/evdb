from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .. import docker
from ..config import Host, Instance
from ..errors import BackupError
from ..files import require_file
from ..run import run
from ..secrets import read as read_secret
from . import kv


def backup(host: Host, instance: Instance, folder: Path, run_id: str) -> dict[str, Any]:
    del run_id
    password = read_secret(host, instance, "password")
    before = int(kv.command(instance, password, ["LASTSAVE"]))
    _wait_for_new_second(before)
    kv.command(instance, password, ["BGSAVE"], timeout=60)
    _wait(instance, password, before)

    source = str(instance.settings.get("rdb", "/data/dump.rdb"))
    target = folder / "dump.rdb"
    docker.copy(f"{instance.container}:{source}", target, timeout=1800)
    require_file(target)
    _check(instance, target)
    return {**kv.facts(instance, password), "files": ["dump.rdb"]}


def _wait_for_new_second(before: int, timeout: int = 5) -> None:
    deadline = time.monotonic() + timeout
    while int(time.time()) <= before:
        if time.monotonic() >= deadline:
            raise BackupError("Redis clock did not advance before BGSAVE")
        time.sleep(0.1)


def _wait(instance: Instance, password: str, before: int, timeout: int = 1800) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = kv._info(kv.command(instance, password, ["INFO", "persistence"]))
        running = info.get("rdb_bgsave_in_progress") == "1"
        status = info.get("rdb_last_bgsave_status")
        saved = int(info.get("rdb_last_save_time", "0"))
        if not running and status == "ok" and saved > before:
            return
        if not running and status == "err":
            raise BackupError(f"{instance.id}: Redis background save failed")
        time.sleep(0.5)
    raise BackupError(f"{instance.id}: Redis background save timed out")


def _check(instance: Instance, path: Path) -> None:
    run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--volume",
            f"{path.resolve()}:/dump.rdb:ro",
            str(instance.target["image"]),
            "redis-check-rdb",
            "/dump.rdb",
        ],
        timeout=600,
    )
