from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import docker
from ..errors import ConfigError
from ..models import KV, Database
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
    if (
        settings.cluster_mode != "off"
        or settings.lock_on_hashtags is not False
        or settings.snapshot_cron != "off"
    ):
        raise ConfigError("Redis does not accept Dragonfly cluster or snapshot settings")
    kv.validate_http(settings)


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
        values["http.env"] = kv.http_env(database)
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
        values[database.service("http")] = kv.http_service(database)
    return values


def health(database: Database, *, timeout: int = 10) -> bool:
    return kv.health(
        database.service("primary"), database.credentials.password, engine="redis", timeout=timeout
    )


def info(database: Database) -> dict[str, Any]:
    facts = kv.info(
        database.service("primary"), database.credentials.password, "server", engine="redis"
    )
    return {
        "version": facts.get("redis_version") or "unknown",
        "mode": database.settings.mode,
        "http": database.settings.http.enabled,
        "http_connections": database.settings.http.connections,
        "data": kv.usage(
            database.service("primary"), database.credentials.password, engine="redis"
        ),
    }


def backup(database: Database, folder: Path, _run_id: str) -> dict[str, Any]:
    return kv.backup(database, folder)
