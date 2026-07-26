from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .. import docker
from ..errors import RestoreError


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
    return _info(text(container, password, ["INFO", section]))


def facts(
    container: str,
    password: str,
    *,
    sample_limit: int = 5,
) -> dict[str, Any]:
    server = info(container, password, "server")
    keyspace = info(container, password, "keyspace")
    databases: dict[str, int] = {}
    samples = []
    for name, value in keyspace.items():
        if not name.startswith("db"):
            continue
        database = name[2:]
        values = dict(part.split("=", 1) for part in value.split(",") if "=" in part)
        databases[database] = int(values.get("keys", 0))
        if len(samples) < sample_limit:
            keys = text(container, password, ["-n", database, "--scan"]).splitlines()
            for key in keys[: sample_limit - len(samples)]:
                samples.append(sample(container, password, database, key))
    version = server.get("dragonfly_version") or server.get("redis_version") or "unknown"
    return {
        "version": version,
        "databases": databases,
        "keys": sum(databases.values()),
        "samples": samples,
    }


def sample(container: str, password: str, database: str, key: str) -> dict[str, Any]:
    kind = text(container, password, ["-n", database, "TYPE", key])
    args = {
        "string": ["GET", key],
        "set": ["SMEMBERS", key],
        "hash": ["HGETALL", key],
        "list": ["LRANGE", key, "0", "-1"],
        "zset": ["ZRANGE", key, "0", "-1", "WITHSCORES"],
    }.get(kind)
    value = text(container, password, ["-n", database, *(args or ["DUMP", key])])
    ttl = int(text(container, password, ["-n", database, "PTTL", key]))
    return {
        "database": int(database),
        "key": key,
        "type": kind,
        "sha256": hashlib.sha256(_canonical(kind, value).encode()).hexdigest(),
        "ttl_ms": ttl,
    }


def restore(
    image: str,
    folder: Path,
    name: str,
    work: Path,
    args: Sequence[str],
    expected: dict[str, Any],
    *,
    files: Sequence[str] = ("dump.rdb",),
) -> dict[str, Any]:
    if work.exists() and (not work.is_dir() or any(work.iterdir())):
        raise RestoreError("KV restore candidate directory is not empty")
    work.mkdir(mode=0o700, exist_ok=True)
    for item in files:
        source = folder / item
        target = work / item
        target.write_bytes(source.read_bytes())
        target.chmod(0o600)
    docker.start(
        image,
        name,
        args,
        mounts=[(work, "/data", False)],
        memory="3g",
        network="none",
        timeout=300,
    )
    _wait(name)
    current = facts(name, "", sample_limit=0)
    current["samples"] = [
        sample(name, "", str(item["database"]), item["key"]) for item in expected.get("samples", [])
    ]
    if current["databases"] != expected.get("databases") or current["keys"] != expected.get("keys"):
        raise RestoreError("KV key counts differ after restore")
    _check_samples(current["samples"], expected.get("samples", []))
    return current


def _wait(name: str, timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if health(name, ""):
            return
        time.sleep(1)
    raise RestoreError("KV restore container did not become ready")


def _check_samples(current: list[dict], expected: list[dict]) -> None:
    wanted = {(item["database"], item["key"]): item for item in expected}
    found = {(item["database"], item["key"]): item for item in current}
    for key, item in wanted.items():
        other = found.get(key)
        if not other or other["type"] != item["type"] or other["sha256"] != item["sha256"]:
            raise RestoreError(f"KV sample differs after restore: db{key[0]}/{key[1]}")
        if item["ttl_ms"] > 0 and other["ttl_ms"] == -1:
            raise RestoreError(f"KV TTL was lost after restore: db{key[0]}/{key[1]}")


def _canonical(kind: str, value: str) -> str:
    lines = value.splitlines()
    if kind == "set":
        return json.dumps(sorted(lines), separators=(",", ":"))
    if kind in {"hash", "zset"}:
        pairs = [lines[index : index + 2] for index in range(0, len(lines), 2)]
        return json.dumps(sorted(pairs), separators=(",", ":"))
    return value


def _info(value: str) -> dict[str, str]:
    result = {}
    for line in value.splitlines():
        if line and not line.startswith("#") and ":" in line:
            key, item = line.split(":", 1)
            result[key] = item
    return result
