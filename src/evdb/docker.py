from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .errors import CommandError, ConfigError
from .files import write_text
from .run import Result, run

NETWORK = "evdb"
NETWORK_LABEL = "com.evanovation.evdb.network"
TRAEFIK_PROJECT = "evdb-traefik"
TRAEFIK_CONTAINER = "evdb-traefik"


def state(name: str, *, timeout: int = 30, health: bool = False) -> dict[str, Any]:
    result = run(["docker", "inspect", name], timeout=timeout, check=False)
    value = {"running": False, "healthy": False if health else None, "image": None}
    if result.code != 0:
        detail = result.err.strip() or result.out.strip() or "no output"
        lowered = detail.lower()
        if "no such object:" in lowered or "no such container:" in lowered:
            return value
        raise CommandError(f"Docker inspection failed for {name} ({result.code}): {detail}")
    try:
        items = json.loads(result.out)
        item = items[0]
        current = item["State"]
        details = item["Config"]
        if not isinstance(items, list) or not isinstance(item, dict):
            raise TypeError
        if not isinstance(current, dict) or not isinstance(details, dict):
            raise TypeError
        value.update(
            running=current.get("Running") is True,
            image=details.get("Image"),
        )
        if health:
            value["healthy"] = (
                value["running"] and current.get("Health", {}).get("Status") == "healthy"
            )
    except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
        raise CommandError(f"invalid Docker inspection for {name}") from exc
    return value


def exec(
    container: str,
    args: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    user: str | None = None,
    timeout: int = 300,
    secrets: Sequence[str] = (),
    stdout=None,
    check: bool = True,
) -> Result:
    command = ["docker", "exec"]
    if user:
        command.extend(["--user", user])
    for key in env or {}:
        command.extend(["--env", key])
    command.extend([container, *args])
    return run(command, timeout=timeout, env=env, secrets=secrets, stdout=stdout, check=check)


def copy(source: str, target: str | Path, *, timeout: int = 300) -> None:
    run(["docker", "cp", source, str(target)], timeout=timeout)


def start(
    image: str,
    name: str,
    args: Sequence[str] = (),
    *,
    mounts: Sequence[tuple[Path, str, bool]] = (),
    memory: str = "1g",
    timeout: int = 300,
) -> str:
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--memory",
        memory,
        "--network",
        "none",
        "--security-opt",
        "no-new-privileges",
    ]
    for source, target, read_only in mounts:
        command.extend(["--volume", f"{source}:{target}{':ro' if read_only else ''}"])
    command.extend([image, *args])
    return run(command, timeout=timeout).out.strip()


def remove(name: str, *, timeout: int = 60) -> None:
    result = run(["docker", "rm", "--force", "--volumes", name], timeout=timeout, check=False)
    if result.code != 0 and "no such" not in result.err.lower():
        raise CommandError(result.err.strip() or f"failed to remove container {name}")


def compose_command(path: str | Path, project: str, *args: str) -> list[str]:
    return ["docker", "compose", "-f", str(path), "--project-name", project, *args]


def write_compose(path: str | Path, value: dict[str, Any]) -> None:
    write_text(path, yaml.safe_dump(value, sort_keys=False), mode=0o640)


def validate_compose(
    path: str | Path,
    project: str,
    *,
    timeout: int = 300,
    secrets: Sequence[str] = (),
) -> None:
    run(
        compose_command(path, project, "config", "--quiet", "--no-env-resolution"),
        timeout=timeout,
        secrets=secrets,
    )


def up(path: Path, project: str, *, timeout: int, secrets: Sequence[str] = ()) -> None:
    run(
        compose_command(path, project, "up", "-d", "--remove-orphans"),
        timeout=timeout,
        secrets=secrets,
    )


def stop(path: Path, project: str, *, timeout: int, secrets: Sequence[str] = ()) -> None:
    run(compose_command(path, project, "stop"), timeout=timeout, secrets=secrets)


def restart(
    path: Path,
    project: str,
    service: str,
    *,
    timeout: int,
    secrets: Sequence[str] = (),
) -> None:
    run(
        compose_command(path, project, "restart", service),
        timeout=timeout,
        secrets=secrets,
    )


def logs(path: Path, project: str, lines: int, *, timeout: int, secrets: Sequence[str]) -> str:
    return run(
        compose_command(path, project, "logs", "--no-color", "--tail", str(lines)),
        timeout=timeout,
        secrets=secrets,
    ).out


def ensure_network(*, timeout: int = 60) -> None:
    result = run(["docker", "network", "inspect", NETWORK], timeout=timeout, check=False)
    if result.code == 0:
        try:
            labels = json.loads(result.out)[0].get("Labels", {}) or {}
        except (json.JSONDecodeError, IndexError, TypeError) as exc:
            raise ConfigError("existing evdb Docker network is invalid") from exc
        if labels.get(NETWORK_LABEL) != "true":
            raise ConfigError("existing evdb Docker network is not owned by evdb")
        return
    detail = (result.err or result.out).lower()
    if detail and "not found" not in detail and "no such network" not in detail:
        raise ConfigError("evdb Docker network inspection failed")
    run(
        ["docker", "network", "create", "--label", f"{NETWORK_LABEL}=true", NETWORK],
        timeout=timeout,
    )


def healthcheck(test: list[str]) -> dict[str, Any]:
    return {
        "test": test,
        "interval": "10s",
        "timeout": "5s",
        "retries": 12,
        "start_period": "10s",
    }


def route(database, service_port: int) -> dict[str, str]:
    key = f"{database.role}-{database.project}"
    entrypoint = "postgres" if database.role == "postgres" else "kv"
    return {
        "traefik.enable": "true",
        "traefik.docker.network": NETWORK,
        f"traefik.tcp.routers.{key}.entrypoints": entrypoint,
        f"traefik.tcp.routers.{key}.rule": f"HostSNI(`{database.domain}`)",
        f"traefik.tcp.routers.{key}.tls": "true",
        f"traefik.tcp.services.{key}.loadbalancer.server.port": str(service_port),
    }
