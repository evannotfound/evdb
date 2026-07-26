from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from .errors import CommandError
from .run import Result, run


def inspect(name: str, *, timeout: int = 30) -> dict:
    result = run(["docker", "inspect", name], timeout=timeout)
    return json.loads(result.out)[0]


def state(name: str, *, timeout: int = 30, health: bool = False) -> dict:
    result = run(["docker", "inspect", name], timeout=timeout, check=False)
    value = {"running": False, "healthy": False if health else None, "image": None, "labels": {}}
    if result.code != 0:
        return value
    try:
        item = json.loads(result.out)[0]
        current = item.get("State", {})
        config = item.get("Config", {})
        health_data = current.get("Health", {})
        value.update(
            running=current.get("Running") is True,
            image=config.get("Image"),
            labels=config.get("Labels") or {},
        )
        if health:
            value["healthy"] = value["running"] and health_data.get("Status") == "healthy"
    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
        pass
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
    command.append(container)
    command.extend(args)
    return run(command, timeout=timeout, env=env, secrets=secrets, stdout=stdout, check=check)


def copy(source: str, target: str | Path, *, timeout: int = 300) -> None:
    run(["docker", "cp", source, str(target)], timeout=timeout)


def start(
    image: str,
    name: str,
    args: Sequence[str] = (),
    *,
    mounts: Sequence[tuple[Path, str, bool]] = (),
    env: Mapping[str, str] | None = None,
    memory: str = "1g",
    network: str = "none",
    timeout: int = 300,
    secrets: Sequence[str] = (),
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
        network,
        "--security-opt",
        "no-new-privileges",
    ]
    for source, target, read_only in mounts:
        value = f"{source}:{target}"
        if read_only:
            value += ":ro"
        command.extend(["--volume", value])
    for key in env or {}:
        command.extend(["--env", key])
    command.append(image)
    command.extend(args)
    result = run(command, timeout=timeout, env=env, secrets=secrets)
    return result.out.strip()


def stop(name: str, *, timeout: int = 60) -> None:
    run(["docker", "stop", "--time", "10", name], timeout=timeout, check=False)


def remove(name: str, *, timeout: int = 60) -> None:
    result = run(["docker", "rm", "--force", "--volumes", name], timeout=timeout, check=False)
    if result.code == 0 or "no such container" in result.err.lower():
        return
    probe = run(["docker", "container", "inspect", name], timeout=timeout, check=False)
    if probe.code != 0 and "no such" in probe.err.lower():
        return
    detail = result.err.strip() or result.out.strip() or "container removal could not be confirmed"
    raise CommandError(f"failed to remove container {name}: {detail}")
