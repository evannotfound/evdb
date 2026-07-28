from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .. import docker
from ..errors import BackupError, ConfigError
from ..files import require_file
from ..models import KV, Database
from ..run import run
from . import kv


def validate(settings: KV) -> None:
    from ..config import validate_image

    if not isinstance(settings, KV) or settings.engine != "redis":
        raise ConfigError("Redis settings must use engine redis")
    validate_image(settings.image, "redis image")
    validate_image(settings.http.image, "HTTP image")
    if settings.mode not in {"durable", "cache"}:
        raise ConfigError("Redis mode must be durable or cache")
    if settings.memory is not None or settings.threads is not None:
        raise ConfigError("Redis does not accept Dragonfly memory or thread settings")
    _validate_http(settings)


def files(database: Database) -> dict[str, str]:
    settings = database.settings
    persistence = "save 900 1\nsave 300 10\nsave 60 10000\n" if database.durable else 'save ""\n'
    values = {
        "redis.conf": (
            "bind 0.0.0.0\n"
            "port 6379\n"
            "dir /data\n"
            "dbfilename dump.rdb\n"
            "appendonly no\n"
            f"{persistence}"
            f"requirepass {json.dumps(database.credentials.password)}\n"
        )
    }
    if settings.http.enabled:
        values["http.env"] = _http_env(database)
    return values


def services(database: Database) -> dict[str, Any]:
    primary = database.service("primary")
    service: dict[str, Any] = {
        "image": database.settings.image,
        "container_name": primary,
        "restart": "unless-stopped",
        "command": ["/usr/local/bin/redis-server", "/run/secrets/redis.conf"],
        "volumes": [
            f"{database.data}:/data",
            f"{database.generated / 'redis.conf'}:/run/secrets/redis.conf:ro",
        ],
        "cap_drop": ["ALL"],
        "cap_add": ["DAC_OVERRIDE"],
        "security_opt": ["no-new-privileges:true"],
        "networks": {docker.NETWORK: {"aliases": [primary]}},
        "labels": docker.route(database, 6379),
    }
    values = {primary: service}
    if database.settings.http.enabled:
        values[database.service("http")] = _http_service(database)
    return values


def health(database: Database, *, timeout: int = 10) -> bool:
    return kv.health(database.service("primary"), database.credentials.password, timeout=timeout)


def info(database: Database) -> dict[str, Any]:
    facts = kv.info(database.service("primary"), database.credentials.password, "server")
    return {
        "version": facts.get("redis_version") or "unknown",
        "mode": database.settings.mode,
        "http": database.settings.http.enabled,
        "http_connections": database.settings.http.connections,
    }


def backup(database: Database, folder: Path, _run_id: str) -> dict[str, Any]:
    password = database.credentials.password
    container = database.service("primary")
    before = int(kv.text(container, password, ["LASTSAVE"]))
    deadline = time.monotonic() + 5
    while int(time.time()) <= before:
        if time.monotonic() >= deadline:
            raise BackupError("Redis clock did not advance before BGSAVE")
        time.sleep(0.1)
    kv.text(container, password, ["BGSAVE"], timeout=60)
    _wait(container, password, before, database.identity)
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
            "redis-check-rdb",
            "/dump.rdb",
        ],
        timeout=600,
    )
    return {"format": "redis-rdb-v1", **kv.facts(container, password), "files": ["dump.rdb"]}


def _wait(container: str, password: str, before: int, identity: str) -> None:
    deadline = time.monotonic() + 1800
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


def _validate_http(settings: KV) -> None:
    if type(settings.http.enabled) is not bool or settings.http.connections < 1:
        raise ConfigError("HTTP settings are invalid")


def _http_env(database: Database) -> str:
    token = database.credentials.http_token
    if not token:
        raise ConfigError(f"{database.identity}: HTTP token is missing")
    connection = (
        f"redis://default:{quote(database.credentials.password, safe='')}@"
        f"{database.service('primary')}:6379"
    )
    return f"SRH_TOKEN={json.dumps(token)}\nSRH_CONNECTION_STRING={json.dumps(connection)}\n"


def _http_service(database: Database) -> dict[str, Any]:
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
