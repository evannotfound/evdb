from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

from .. import docker
from ..errors import ConfigError
from ..models import KV, Database


def command(
    container: str,
    password: str,
    args: Sequence[str],
    *,
    timeout: int = 60,
    check: bool = True,
):
    env = {"REDISCLI_AUTH": password} if password else None
    return docker.exec(
        container,
        ["redis-cli", "--raw", *args],
        env=env,
        secrets=(password,) if password else (),
        timeout=timeout,
        check=check,
    )


def text(container: str, password: str, args: Sequence[str], *, timeout: int = 60) -> str:
    return command(container, password, args, timeout=timeout).out.strip()


def health(container: str, password: str, *, timeout: int = 10) -> bool:
    result = command(container, password, ["PING"], timeout=timeout, check=False)
    return result.code == 0 and result.out.strip() == "PONG"


def info(container: str, password: str, section: str = "all") -> dict[str, str]:
    result = {}
    for line in text(container, password, ["INFO", section]).splitlines():
        if line and not line.startswith("#") and ":" in line:
            key, value = line.split(":", 1)
            result[key] = value
    return result


def facts(container: str, password: str) -> dict:
    server = info(container, password, "server")
    keyspace = info(container, password, "keyspace")
    databases = {}
    for name, value in keyspace.items():
        if name.startswith("db"):
            fields = dict(part.split("=", 1) for part in value.split(",") if "=" in part)
            databases[name[2:]] = int(fields.get("keys", 0))
    return {
        "version": server.get("dragonfly_version") or server.get("redis_version") or "unknown",
        "databases": databases,
        "keys": sum(databases.values()),
    }


def usage(container: str, password: str) -> dict[str, Any]:
    memory = info(container, password, "memory")
    keyspace = info(container, password, "keyspace")
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
