from __future__ import annotations

import hashlib
from typing import Any

from .. import docker
from ..config import Instance


def command(
    instance: Instance,
    password: str,
    args: list[str],
    *,
    timeout: int = 60,
) -> str:
    env = {"REDISCLI_AUTH": password} if password else None
    result = docker.exec(
        instance.container,
        ["redis-cli", "--raw", *args],
        env=env,
        timeout=timeout,
        secrets=[password] if password else (),
    )
    return result.out.strip()


def facts(instance: Instance, password: str, *, sample_limit: int = 5) -> dict[str, Any]:
    server = _info(command(instance, password, ["INFO", "server"]))
    keyspace = _info(command(instance, password, ["INFO", "keyspace"]))
    databases: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    for name, value in keyspace.items():
        if not name.startswith("db"):
            continue
        database = name[2:]
        values = dict(part.split("=", 1) for part in value.split(",") if "=" in part)
        databases[database] = int(values.get("keys", 0))
        if len(samples) < sample_limit:
            keys = command(instance, password, ["-n", database, "--scan"]).splitlines()
            for key in keys[: sample_limit - len(samples)]:
                samples.append(_sample(instance, password, database, key))
    version = server.get("dragonfly_version") or server.get("redis_version") or "unknown"
    return {
        "version": version,
        "databases": databases,
        "keys": sum(databases.values()),
        "samples": samples,
    }


def _sample(instance: Instance, password: str, database: str, key: str) -> dict[str, Any]:
    kind = command(instance, password, ["-n", database, "TYPE", key])
    args = {
        "string": ["GET", key],
        "set": ["SMEMBERS", key],
        "hash": ["HGETALL", key],
        "list": ["LRANGE", key, "0", "-1"],
        "zset": ["ZRANGE", key, "0", "-1", "WITHSCORES"],
    }.get(kind)
    value = command(instance, password, ["-n", database, *(args or ["DUMP", key])])
    if kind in {"set", "hash", "zset"}:
        value = "\n".join(sorted(value.splitlines()))
    ttl = int(command(instance, password, ["-n", database, "PTTL", key]))
    return {
        "database": int(database),
        "key": key,
        "type": kind,
        "sha256": hashlib.sha256(value.encode()).hexdigest(),
        "ttl_ms": ttl,
    }


def _info(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        if line and not line.startswith("#") and ":" in line:
            key, value = line.split(":", 1)
            result[key] = value
    return result
