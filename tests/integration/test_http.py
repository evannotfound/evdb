from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import replace

from evdb import compose, secrets
from evdb.config import resolve_state
from tests.fixtures.containers import (
    DRAGONFLY_IMAGE,
    HTTP_IMAGE,
    REDIS_IMAGE,
    command,
    docker_exec,
    port_bindings,
    require_image,
    unique_name,
    wait_exec,
)

DIGEST = "sha256:" + "a" * 64


def test_generated_http_compose_uses_private_secrets_and_isolates_backends(
    config, tmp_path, monkeypatch
):
    for image in (REDIS_IMAGE, DRAGONFLY_IMAGE, HTTP_IMAGE):
        require_image(image)

    config, targets = _config(config, tmp_path)
    ports = _ports(2)
    state = resolve_state(config, resolver=_digest)
    state = replace(
        state,
        roles={
            target.identity: replace(state.roles[target.identity], http_port=port)
            for target, port in zip(targets, ports, strict=True)
        },
    )
    network = unique_name("http-network")
    monkeypatch.setattr(compose, "NETWORK", network)
    command(["docker", "network", "create", network])
    values = {}
    try:
        for target in targets:
            password = f"local-{target.engine}-password"
            token = f"local-{target.engine}-token"
            generated = iter((password, token))
            secrets.ensure(config, target, generate=generated.__next__)
            target.data.mkdir(parents=True, mode=0o700)
            target.data.chmod(0o777)
            rendered = compose.database(config, target, state)
            compose.write(target.compose, rendered)
            values[target.engine] = (target, password, token, rendered)

            text = target.compose.read_text()
            assert password not in text and token not in text
            private = ("password", "http-token", "http.env")
            engine_file = "redis.conf" if target.engine == "redis" else "dragonfly.flags"
            assert all(
                secrets.path(config, target, name).stat().st_mode & 0o777 == 0o600
                for name in (*private, engine_file)
            )
            assert rendered["networks"] == {network: {"external": True, "name": network}}
            command(compose.command(target.compose, target.compose_project, "up", "-d"))

        for engine, (target, password, token, rendered) in values.items():
            primary = f"evdb-{target.project}-{target.role}-primary"
            http = f"evdb-{target.project}-{target.role}-http"
            auth = {"REDISCLI_AUTH": password}
            wait_exec(primary, ["redis-cli", "PING"], env=auth)
            port = _http_port(http)
            _wait_http(port, token)

            missing, _ = _request(port, ["SET", "blocked", "missing"])
            wrong, _ = _request(port, ["SET", "blocked", "wrong"], "wrong-token")
            assert missing in {400, 401, 403}
            assert wrong in {400, 401, 403}
            assert _redis(primary, password, ["GET", "blocked"]) == ""
            assert _request(port, ["SET", "scope", engine], token) == (200, {"result": "OK"})
            assert _request(port, ["GET", "scope"], token) == (200, {"result": engine})
            assert _redis(primary, password, ["GET", "scope"]) == engine
            assert rendered["services"][http]["ports"] == [f"127.0.0.1:{port}:80"]
            assert not any(port_bindings(primary).values())
        for engine, (target, password, _token, _rendered) in values.items():
            primary = f"evdb-{target.project}-{target.role}-primary"
            assert _redis(primary, password, ["GET", "scope"]) == engine
    finally:
        for target in targets:
            primary = f"evdb-{target.project}-{target.role}-primary"
            docker_exec(primary, ["chmod", "-R", "a+rwX", "/data"], check=False)
            command(compose.command(target.compose, target.compose_project, "down"), check=False)
        command(["docker", "network", "rm", network], check=False)


def _config(config, tmp_path):
    base = config.projects[0]
    http = replace(base.kv.http, enabled=True, image=HTTP_IMAGE)
    redis = replace(
        base,
        id=f"http-redis-{uuid.uuid4().hex[:8]}-test-01",
        postgres=None,
        kv=replace(base.kv, engine="redis", image="redis:7.2.5", mode="durable", http=http),
    )
    dragonfly = replace(
        base,
        id=f"http-dragonfly-{uuid.uuid4().hex[:8]}-test-01",
        postgres=None,
        kv=replace(
            base.kv,
            engine="dragonfly",
            image=DRAGONFLY_IMAGE.split("@", 1)[0],
            mode="durable",
            http=http,
            memory="256mb",
            threads=1,
        ),
    )
    selected = replace(
        config,
        host=replace(config.host, data_root=tmp_path / "data"),
        projects=(redis, dragonfly),
    )
    return selected, tuple(selected.databases)


def _digest(source):
    images = (REDIS_IMAGE, DRAGONFLY_IMAGE, HTTP_IMAGE)
    for image in images:
        if source == image.split("@", 1)[0] or source == image:
            return image.split("@", 1)[1]
    return DIGEST


def _ports(count):
    sockets = []
    try:
        for _ in range(count):
            current = socket.socket()
            current.bind(("127.0.0.1", 0))
            sockets.append(current)
        return [current.getsockname()[1] for current in sockets]
    finally:
        for current in sockets:
            current.close()


def _http_port(name):
    bindings = port_bindings(name).get("80/tcp") or []
    assert len(bindings) == 1
    assert bindings[0]["HostIp"] == "127.0.0.1"
    return int(bindings[0]["HostPort"])


def _wait_http(port, token):
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


def _request(port, body, token=None):
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


def _redis(name, password, args):
    return docker_exec(
        name,
        ["redis-cli", "--raw", *args],
        env={"REDISCLI_AUTH": password},
    ).stdout.strip()
