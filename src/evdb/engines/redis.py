from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .. import docker, secrets
from ..config import Config, Database, MachineState
from ..errors import BackupError
from ..files import require_file
from ..run import run
from . import kv


def health(container: str, password: str, *, timeout: int = 10) -> bool:
    return kv.health(container, password, timeout=timeout)


def backup(
    config: Config,
    database: Database,
    folder: Path,
    run_id: str,
    state: MachineState,
) -> dict[str, Any]:
    del run_id
    password = secrets.read(config, database, "password")
    container = _container(database)
    before = int(kv.text(container, password, ["LASTSAVE"]))
    _wait_for_new_second(before)
    kv.text(container, password, ["BGSAVE"], timeout=60)
    _wait(container, password, before, database.identity)
    target = folder / "dump.rdb"
    docker.copy(f"{container}:/data/dump.rdb", target, timeout=1800)
    require_file(target)
    _check(state.roles[database.identity].images["primary"].image, target)
    return {"format": "redis-rdb-v1", **kv.facts(container, password), "files": ["dump.rdb"]}


def restore(config, database, folder, name, work, state, record):
    del config
    image = state.roles[database.identity].images["primary"].image
    args = [
        "redis-server",
        "--dir",
        "/data",
        "--dbfilename",
        "dump.rdb",
        "--protected-mode",
        "no",
    ]
    return kv.restore(image, folder, name, work, args, record.get("facts", {}))


def _wait_for_new_second(before: int, timeout: int = 5) -> None:
    deadline = time.monotonic() + timeout
    while int(time.time()) <= before:
        if time.monotonic() >= deadline:
            raise BackupError("Redis clock did not advance before BGSAVE")
        time.sleep(0.1)


def _wait(container: str, password: str, before: int, identity: str, timeout: int = 1800) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = kv.info(container, password, "persistence")
        running = info.get("rdb_bgsave_in_progress") == "1"
        status = info.get("rdb_last_bgsave_status")
        saved = int(info.get("rdb_last_save_time", "0"))
        if not running and status == "ok" and saved > before:
            return
        if not running and status == "err":
            raise BackupError(f"{identity}: Redis background save failed")
        time.sleep(0.5)
    raise BackupError(f"{identity}: Redis background save timed out")


def _check(image: str, path: Path) -> None:
    run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--volume",
            f"{path.resolve()}:/dump.rdb:ro",
            image,
            "redis-check-rdb",
            "/dump.rdb",
        ],
        timeout=600,
    )


def _container(database: Database) -> str:
    return f"evdb-{database.project}-{database.role}-primary"
