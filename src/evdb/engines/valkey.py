from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .. import docker
from ..errors import ConfigError
from ..models import KV, Database
from . import kv


def validate(settings: KV) -> None:
    from ..config import validate_image

    if not isinstance(settings, KV) or settings.engine != "valkey":
        raise ConfigError("Valkey settings must use engine valkey")
    validate_image(settings.image, "Valkey image")
    validate_image(settings.http.image, "HTTP image")
    if settings.mode not in {"durable", "cache"}:
        raise ConfigError("Valkey mode must be durable or cache")
    if not isinstance(settings.memory, str) or not re.fullmatch(
        r"[1-9][0-9]*(?:kb|mb|gb)", settings.memory
    ):
        raise ConfigError("Valkey memory must be a positive kb, mb, or gb value")
    if settings.threads is not None:
        raise ConfigError("Valkey does not accept Dragonfly thread settings")
    if (
        settings.cluster_mode != "off"
        or settings.lock_on_hashtags is not False
        or settings.snapshot_cron != "off"
    ):
        raise ConfigError("Valkey does not accept Dragonfly cluster or snapshot settings")
    kv.validate_http(settings)


def files(database: Database) -> dict[str, str]:
    values = {
        "valkey.conf": (
            "bind 0.0.0.0\n"
            "port 6379\n"
            "dir /data\n"
            "dbfilename dump.rdb\n"
            'save ""\n'
            f"appendonly {'yes' if database.durable else 'no'}\n"
            "appendfsync everysec\n"
            f"maxmemory-policy {'noeviction' if database.durable else 'allkeys-lru'}\n"
            f"requirepass {json.dumps(database.credentials.password)}\n"
        )
    }
    if database.settings.http.enabled:
        values["http.env"] = kv.http_env(database)
    return values


def services(database: Database) -> dict[str, Any]:
    primary = database.service("primary")
    values = {
        primary: {
            "image": database.image,
            "container_name": primary,
            "restart": "unless-stopped",
            "stop_grace_period": "60s",
            "command": [
                "/usr/local/bin/valkey-server",
                "/run/secrets/valkey.conf",
                "--maxmemory",
                database.settings.memory,
            ],
            "volumes": [
                f"{database.data}:/data",
                f"{database.generated / 'valkey.conf'}:/run/secrets/valkey.conf:ro",
            ],
            "cap_drop": ["ALL"],
            "cap_add": ["DAC_OVERRIDE"],
            "security_opt": ["no-new-privileges:true"],
            "networks": {docker.NETWORK: {"aliases": [primary]}},
            "labels": docker.route(database, 6379),
        }
    }
    if database.settings.http.enabled:
        values[database.service("http")] = kv.http_service(database)
    return values


def health(database: Database, *, timeout: int = 10) -> bool:
    return kv.health(
        database.service("primary"),
        database.credentials.password,
        engine="valkey",
        timeout=timeout,
    )


def info(database: Database) -> dict[str, Any]:
    server = kv.info(
        database.service("primary"), database.credentials.password, "server", engine="valkey"
    )
    return {
        "version": server.get("valkey_version") or "unknown",
        "mode": database.settings.mode,
        "memory": database.settings.memory,
        "http": database.settings.http.enabled,
        "http_connections": database.settings.http.connections,
        "data": kv.usage(
            database.service("primary"), database.credentials.password, engine="valkey"
        ),
    }


def backup(database: Database, folder: Path, _run_id: str) -> dict[str, Any]:
    return kv.backup(database, folder)
