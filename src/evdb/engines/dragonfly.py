from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .. import docker, secrets
from ..config import Config, Database, MachineState
from ..errors import BackupError, RestoreError
from ..files import require_file
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
    password = secrets.read(config, database, "password")
    container = _container(database)
    name = f"evdb-{run_id}"
    if _sources(container, name):
        raise BackupError(f"{database.identity}: Dragonfly backup source already exists")
    started = False
    error = None
    try:
        started = True
        kv.text(container, password, ["SAVE", "DF", name], timeout=1800)
        files = _snapshot(name, _sources(container, name))
        for item in files:
            docker.copy(f"{container}:/data/{item}", folder / item, timeout=1800)
            require_file(folder / item)
        facts = _facts(database, folder, name, run_id, state)
    except BaseException as exc:
        error = exc
        raise
    finally:
        if started:
            try:
                _cleanup(container, _sources(container, name))
            except BaseException as exc:
                if error is None:
                    raise BackupError(
                        f"{database.identity}: Dragonfly backup source cleanup failed"
                    ) from exc
    return {
        "format": "dragonfly-dfs-v1",
        **facts,
        "snapshot_base": name,
        "files": list(files),
    }


def restore(config, database, folder, name, work, state, record):
    del config
    image = state.roles[database.identity].images["primary"].image
    base = record.get("facts", {}).get("snapshot_base")
    if not isinstance(base, str):
        raise RestoreError("Dragonfly backup has no native snapshot basename")
    files = tuple(item.get("name") for item in record.get("files", []))
    if record.get("format") != "dragonfly-dfs-v1" or any(
        not isinstance(item, str) for item in files
    ):
        raise RestoreError("Dragonfly backup does not use the native snapshot format")
    try:
        files = _snapshot(base, files)
    except BackupError as exc:
        raise RestoreError(str(exc)) from exc
    args = [
        "dragonfly",
        "--dir=/data",
        f"--dbfilename={base}",
        "--primary_port_http_enabled=false",
    ]
    return kv.restore(
        image,
        folder,
        name,
        work,
        args,
        record.get("facts", {}),
        files=files,
    )


def _sources(container: str, base: str) -> tuple[str, ...]:
    result = docker.exec(
        container,
        [
            "find",
            "/data",
            "-maxdepth",
            "1",
            "-type",
            "f",
            "-name",
            f"{base}-*.dfs",
            "-printf",
            "%f\\n",
        ],
        timeout=60,
    )
    files = tuple(sorted(item for item in result.out.splitlines() if item))
    if any(Path(item).name != item or not item.startswith(f"{base}-") for item in files):
        raise BackupError("Dragonfly returned an unsafe snapshot filename")
    return files


def _snapshot(base: str, files: tuple[str, ...]) -> tuple[str, ...]:
    if not re.fullmatch(r"[A-Za-z0-9-]+", base):
        raise BackupError("Dragonfly snapshot basename is unsafe")
    summary = f"{base}-summary.dfs"
    shards = []
    for item in files:
        match = re.fullmatch(rf"{re.escape(base)}-([0-9]{{4}})\.dfs", item)
        if item == summary:
            continue
        if match is None:
            raise BackupError(f"Dragonfly snapshot contains an unexpected file: {item}")
        shards.append(int(match.group(1)))
    if files.count(summary) != 1 or not shards:
        raise BackupError("Dragonfly snapshot requires one summary and at least one shard")
    if sorted(shards) != list(range(max(shards) + 1)):
        raise BackupError("Dragonfly snapshot shard set is incomplete")
    return tuple(sorted(files))


def _cleanup(container: str, files: tuple[str, ...]) -> None:
    if not files:
        return
    result = docker.exec(
        container,
        ["rm", "-f", "--", *(f"/data/{item}" for item in files)],
        timeout=60,
        check=False,
    )
    if result.code != 0:
        raise BackupError("Dragonfly snapshot cleanup command failed")


def _facts(
    database: Database,
    folder: Path,
    base: str,
    run_id: str,
    state: MachineState,
) -> dict[str, Any]:
    name = f"evdb-backup-check-{database.project}-{database.role}-{run_id}"
    image = state.roles[database.identity].images["primary"].image
    args = [
        "/usr/local/bin/dragonfly",
        "--logtostderr",
        "--dir=/data",
        f"--dbfilename={base}",
        "--primary_port_http_enabled=false",
        f"--proactor_threads={database.settings.threads}",
        f"--maxmemory={database.settings.memory}",
    ]
    try:
        docker.start(
            image,
            name,
            args,
            mounts=[(folder, "/data", True)],
            memory="3g",
            network="none",
            timeout=300,
        )
        kv._wait(name)
        return kv.facts(name, "")
    finally:
        docker.remove(name)


def _container(database: Database) -> str:
    return f"evdb-{database.project}-{database.role}-primary"
