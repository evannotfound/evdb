from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from functools import cache
from pathlib import Path

import pytest

POSTGRES_IMAGE = (
    "postgres:16@sha256:46aa2ee5d664b275f05d1a963b30fff60fb422b4b594d509765c42db46d48881"
)
REDIS_IMAGE = "redis:7.2.5@sha256:f5ef9e24a9ef3b7cc552ae0cbc3cbade4f2877502683496c5d775605ae071412"
DRAGONFLY_IMAGE = (
    "docker.dragonflydb.io/dragonflydb/dragonfly:v1.34.1"
    "@sha256:99eb2dd14ca00b983c302d0a15d15176dccd5a9e85a375bcc5157e1cadd73099"
)
HTTP_IMAGE = (
    "hiett/serverless-redis-http"
    "@sha256:5b0bb9239fce53abf87b2018a7a0deb9ec7bd900c5360738fe5fbeeb426f9150"
)
TRAEFIK_IMAGE = (
    "traefik:v3.7.8@sha256:4299bbed850421258fc5448c2e0e6ad350981d4d335a68de11b92448aedbefe5"
)


def command(
    args: Sequence[str],
    *,
    check: bool = True,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    command_env = os.environ.copy()
    command_env.update(env or {})
    result = subprocess.run(
        [str(item) for item in args],
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
        env=command_env,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise AssertionError(f"command failed ({result.returncode}): {args[0]}: {detail}")
    return result


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        pytest.skip(f"{name} executable unavailable on PATH")
    return path


@cache
def require_docker() -> None:
    require_binary("docker")
    result = command(["docker", "info", "--format", "{{.ServerVersion}}"], check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "daemon did not respond"
        pytest.skip(f"Docker daemon unavailable: {detail}")


@cache
def require_image(image: str) -> str:
    require_docker()
    inspect = command(["docker", "image", "inspect", image], check=False, timeout=30)
    if inspect.returncode == 0:
        return image
    pull = command(["docker", "pull", image], check=False, timeout=600)
    if pull.returncode != 0:
        detail = pull.stderr.strip() or pull.stdout.strip() or "pull failed"
        pytest.skip(f"Docker image unavailable: {image}: {detail}")
    return image


def unique_name(kind: str) -> str:
    return f"evdb-it-{kind}-{uuid.uuid4().hex[:12]}"


@contextmanager
def network() -> Iterator[str]:
    require_docker()
    name = unique_name("net")
    command(["docker", "network", "create", name])
    try:
        yield name
    finally:
        command(["docker", "network", "rm", name], check=False, timeout=60)


@contextmanager
def container(
    image: str,
    name: str,
    args: Sequence[str] = (),
    *,
    env: Mapping[str, str] | None = None,
    mounts: Sequence[tuple[Path, str, bool]] = (),
    network_name: str = "none",
    publish_http: bool = False,
    publish: Sequence[str] = (),
    labels: Mapping[str, str] | None = None,
    memory: str = "1g",
) -> Iterator[str]:
    require_image(image)
    run_args = [
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        "--network",
        network_name,
        "--memory",
        memory,
        "--security-opt",
        "no-new-privileges",
    ]
    if publish_http:
        run_args.extend(["--publish", "127.0.0.1::80"])
    for value in publish:
        run_args.extend(["--publish", value])
    for key, value in (labels or {}).items():
        run_args.extend(["--label", f"{key}={value}"])
    for source, target, read_only in mounts:
        value = f"{source.resolve()}:{target}"
        if read_only:
            value += ":ro"
        run_args.extend(["--volume", value])
    for key in env or {}:
        run_args.extend(["--env", key])
    run_args.append(image)
    run_args.extend(args)
    command(run_args, env=env, timeout=300)
    try:
        yield name
    finally:
        remove(name)


def remove(name: str) -> None:
    command(
        ["docker", "rm", "--force", "--volumes", name],
        check=False,
        timeout=60,
    )


def docker_exec(
    name: str,
    args: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    input_text: str | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    run_args = ["docker", "exec"]
    if input_text is not None:
        run_args.append("--interactive")
    for key in env or {}:
        run_args.extend(["--env", key])
    run_args.append(name)
    run_args.extend(args)
    return command(
        run_args,
        check=check,
        env=env,
        input_text=input_text,
        timeout=timeout,
    )


def wait_exec(name: str, args: Sequence[str], *, env: Mapping[str, str] | None = None) -> None:
    deadline = time.monotonic() + 120
    last = "container did not respond"
    while time.monotonic() < deadline:
        result = docker_exec(name, args, env=env, check=False, timeout=10)
        if result.returncode == 0:
            return
        last = result.stderr.strip() or result.stdout.strip() or last
        time.sleep(0.5)
    pytest.fail(f"container {name} did not become ready: {last}")


def port_bindings(name: str) -> dict[str, list[dict[str, str]] | None]:
    result = command(
        ["docker", "inspect", name, "--format", "{{json .NetworkSettings.Ports}}"],
        timeout=30,
    )
    return json.loads(result.stdout)


def http_port(name: str) -> int:
    bindings = port_bindings(name).get("80/tcp") or []
    if len(bindings) != 1 or bindings[0].get("HostIp") != "127.0.0.1":
        pytest.fail(f"container {name} does not have one loopback HTTP binding")
    return int(bindings[0]["HostPort"])
