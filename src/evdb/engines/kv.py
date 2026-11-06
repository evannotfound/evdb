from __future__ import annotations

import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .. import docker
from ..errors import BackupError, ConfigError
from ..files import require_file
from ..models import KV, Database
from ..run import run


def command(
    container: str,
    password: str,
    args: Sequence[str],
    *,
    engine: str,
    timeout: int = 60,
    check: bool = True,
):
    cli = "valkey-cli" if engine == "valkey" else "redis-cli"
    auth = "VALKEYCLI_AUTH" if engine == "valkey" else "REDISCLI_AUTH"
    env = {auth: password} if password else None
    return docker.exec(
        container,
        [cli, "--raw", *args],
        env=env,
        secrets=(password,) if password else (),
        timeout=timeout,
        check=check,
    )


def text(
    container: str, password: str, args: Sequence[str], *, engine: str, timeout: int = 60
) -> str:
    return command(container, password, args, engine=engine, timeout=timeout).out.strip()


def health(container: str, password: str, *, engine: str, timeout: int = 10) -> bool:
    result = command(container, password, ["PING"], engine=engine, timeout=timeout, check=False)
    return result.code == 0 and result.out.strip() == "PONG"


def info(container: str, password: str, section: str = "all", *, engine: str) -> dict[str, str]:
    result = {}
    for line in text(container, password, ["INFO", section], engine=engine).splitlines():
        if line and not line.startswith("#") and ":" in line:
            key, value = line.split(":", 1)
            result[key] = value
    return result


def facts(container: str, password: str, *, engine: str) -> dict:
    server = info(container, password, "server", engine=engine)
    keyspace = info(container, password, "keyspace", engine=engine)
    databases = {}
    for name, value in keyspace.items():
        if name.startswith("db"):
            fields = dict(part.split("=", 1) for part in value.split(",") if "=" in part)
            databases[name[2:]] = int(fields.get("keys", 0))
    return {
        "version": server.get("valkey_version")
        or server.get("dragonfly_version")
        or server.get("redis_version")
        or "unknown",
        "databases": databases,
        "keys": sum(databases.values()),
    }


def usage(container: str, password: str, *, engine: str) -> dict[str, Any]:
    memory = info(container, password, "memory", engine=engine)
    keyspace = info(container, password, "keyspace", engine=engine)
    dataset = memory.get("used_memory_dataset") or memory.get("used_memory")
    keys = 0
    for name, value in keyspace.items():
        if not name.startswith("db"):
            continue
        fields = dict(part.split("=", 1) for part in value.split(",") if "=" in part)
        keys += int(fields.get("keys", 0))
    return {
        "available": dataset is not None,
        "dataset_bytes": int(dataset) if dataset is not None else None,
        "keys": keys,
    }


def backup(database: Database, folder: Path) -> dict[str, Any]:
    engine = database.engine
    container = database.service("primary")
    password = database.credentials.password
    # An existing save cannot represent a snapshot requested by this backup.
    previous = _wait_rdb(database)
    before = int(text(container, password, ["LASTSAVE"], engine=engine))
    deadline = time.monotonic() + 5
    while int(time.time()) <= before:
        if time.monotonic() >= deadline:
            raise BackupError(f"{database.identity}: {engine} clock did not advance before BGSAVE")
        time.sleep(0.1)
    args = ["BGSAVE", "SCHEDULE"] if engine == "valkey" else ["BGSAVE"]
    reply = text(container, password, args, engine=engine, timeout=60)
    # CLI exit status alone does not reliably distinguish server error replies.
    if reply not in {"Background saving started", "Background saving scheduled"}:
        raise BackupError(f"{database.identity}: {engine} BGSAVE failed: {reply}")
    _wait_rdb(
        database,
        before=before,
        previous=previous,
        started=reply == "Background saving started",
    )
    target = folder / "dump.rdb"
    docker.copy(f"{container}:/data/dump.rdb", target, timeout=1800)
    require_file(target)
    run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--volume",
            f"{target.resolve()}:/dump.rdb:ro",
            database.image,
            f"{engine}-check-rdb",
            "/dump.rdb",
        ],
        timeout=600,
    )
    return {
        "format": f"{engine}-rdb-v1",
        **facts(container, password, engine=engine),
        "files": ["dump.rdb"],
    }


def _wait_rdb(
    database: Database,
    *,
    before: int | None = None,
    previous: dict[str, str] | None = None,
    started: bool = False,
) -> dict[str, str]:
    deadline = time.monotonic() + 1800
    previous = previous or {}
    while time.monotonic() < deadline:
        current = info(
            database.service("primary"),
            database.credentials.password,
            "persistence",
            engine=database.engine,
        )
        running = current.get("rdb_bgsave_in_progress") == "1"
        status = current.get("rdb_last_bgsave_status")
        started = (
            started
            or running
            or int(current.get("rdb_saves", "0")) > int(previous.get("rdb_saves", "0"))
        )
        if not running:
            if before is None:
                return current
            if status == "ok" and int(current.get("rdb_last_save_time", "0")) > before:
                return current
            # A scheduled save can retain an old error until the AOF rewrite finishes.
            if status == "err" and (started or previous.get("rdb_last_bgsave_status") != "err"):
                raise BackupError(f"{database.identity}: {database.engine} background save failed")
        time.sleep(0.5)
    raise BackupError(f"{database.identity}: {database.engine} background save timed out")


def validate_http(settings: KV) -> None:
    if type(settings.http.enabled) is not bool or settings.http.connections < 1:
        raise ConfigError("HTTP settings are invalid")


def http_env(database: Database) -> str:
    token = database.credentials.http_token
    if not token:
        raise ConfigError(f"{database.identity}: HTTP token is missing")
    connection = (
        f"redis://default:{quote(database.credentials.password, safe='')}@"
        f"{database.service('primary')}:6379"
    )
    return f"SRH_TOKEN={json.dumps(token)}\nSRH_CONNECTION_STRING={json.dumps(connection)}\n"


def http_service(database: Database) -> dict[str, Any]:
    name = database.service("http")
    return {
        "image": database.settings.http.image,
        "container_name": name,
        "restart": "unless-stopped",
        "env_file": [str(database.generated / "http.env")],
        "environment": {
            "SRH_MODE": "env",
            "SRH_MAX_CONNECTIONS": str(database.settings.http.connections),
        },
        "depends_on": [database.service("primary")],
        "healthcheck": docker.healthcheck(
            ["CMD", "wget", "--spider", "--quiet", "http://127.0.0.1:80/"]
        ),
        "ports": [f"127.0.0.1:{database.http_port}:80"],
        "networks": {docker.NETWORK: {"aliases": [name]}},
    }
