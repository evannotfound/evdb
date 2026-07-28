from __future__ import annotations

from collections.abc import Sequence

from .. import docker


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
