from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from fixtures.containers import (  # noqa: E402
    DRAGONFLY_IMAGE,
    HTTP_IMAGE,
    REDIS_IMAGE,
    container,
    docker_exec,
    http_port,
    network,
    port_bindings,
    require_image,
    unique_name,
    wait_exec,
)


def test_serverless_http_auth_and_redis_dragonfly_isolation():
    for image in (REDIS_IMAGE, DRAGONFLY_IMAGE, HTTP_IMAGE):
        require_image(image)

    redis_name = unique_name("http-redis")
    dragonfly_name = unique_name("http-dragonfly")
    redis_http = unique_name("http-proxy-redis")
    dragonfly_http = unique_name("http-proxy-dragonfly")
    redis_token = "local-http-token-redis"
    dragonfly_token = "local-http-token-dragonfly"

    with (
        network() as network_name,
        container(
            REDIS_IMAGE,
            redis_name,
            ["redis-server", "--save", "", "--appendonly", "no"],
            network_name=network_name,
        ),
        container(
            DRAGONFLY_IMAGE,
            dragonfly_name,
            ["dragonfly", "--primary_port_http_enabled=false"],
            network_name=network_name,
            memory="2g",
        ),
    ):
        wait_exec(redis_name, ["redis-cli", "PING"])
        wait_exec(dragonfly_name, ["redis-cli", "PING"])
        with (
            container(
                HTTP_IMAGE,
                redis_http,
                env={
                    "SRH_MODE": "env",
                    "SRH_TOKEN": redis_token,
                    "SRH_CONNECTION_STRING": f"redis://{redis_name}:6379",
                    "SRH_MAX_CONNECTIONS": "2",
                },
                network_name=network_name,
                publish_http=True,
            ),
            container(
                HTTP_IMAGE,
                dragonfly_http,
                env={
                    "SRH_MODE": "env",
                    "SRH_TOKEN": dragonfly_token,
                    "SRH_CONNECTION_STRING": f"redis://{dragonfly_name}:6379",
                    "SRH_MAX_CONNECTIONS": "2",
                },
                network_name=network_name,
                publish_http=True,
            ),
        ):
            redis_port = http_port(redis_http)
            dragonfly_port = http_port(dragonfly_http)
            _wait_http(redis_port, redis_token)
            _wait_http(dragonfly_port, dragonfly_token)

            missing, _ = _request(redis_port, ["SET", "blocked", "missing"])
            wrong, _ = _request(
                dragonfly_port,
                ["SET", "blocked", "wrong"],
                "wrong-token",
            )
            assert missing in {400, 401, 403}
            assert wrong in {400, 401, 403}
            assert _redis(redis_name, ["GET", "blocked"]) == ""
            assert _redis(dragonfly_name, ["GET", "blocked"]) == ""

            assert _request(redis_port, ["SET", "scope", "redis"], redis_token) == (
                200,
                {"result": "OK"},
            )
            assert _request(
                dragonfly_port,
                ["SET", "scope", "dragonfly"],
                dragonfly_token,
            ) == (200, {"result": "OK"})
            assert _request(redis_port, ["GET", "scope"], redis_token) == (
                200,
                {"result": "redis"},
            )
            assert _request(
                dragonfly_port,
                ["GET", "scope"],
                dragonfly_token,
            ) == (200, {"result": "dragonfly"})

            assert _redis(redis_name, ["GET", "scope"]) == "redis"
            assert _redis(dragonfly_name, ["GET", "scope"]) == "dragonfly"
            assert redis_port != dragonfly_port
            _assert_loopback(redis_http)
            _assert_loopback(dragonfly_http)
            assert not any(port_bindings(redis_name).values())
            assert not any(port_bindings(dragonfly_name).values())


def _wait_http(port: int, token: str) -> None:
    deadline = time.monotonic() + 60
    last = "HTTP service did not respond"
    while time.monotonic() < deadline:
        try:
            status, body = _request(port, ["PING"], token)
            if status == 200 and body == {"result": "PONG"}:
                return
            last = f"HTTP {status}: {body}"
        except OSError as exc:
            last = str(exc)
        time.sleep(0.25)
    raise AssertionError(f"serverless Redis HTTP did not become ready: {last}")


def _request(port: int, body: list[str], token: str | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/",
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else {}


def _redis(name: str, args: list[str]) -> str:
    return docker_exec(name, ["redis-cli", "--raw", *args]).stdout.strip()


def _assert_loopback(name: str) -> None:
    bindings = port_bindings(name).get("80/tcp") or []
    assert len(bindings) == 1
    assert bindings[0]["HostIp"] == "127.0.0.1"
