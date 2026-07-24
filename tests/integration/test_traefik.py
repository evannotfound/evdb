from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from fixtures.containers import (  # noqa: E402
    POSTGRES_IMAGE,
    REDIS_IMAGE,
    TRAEFIK_IMAGE,
    command,
    container,
    docker_exec,
    network,
    port_bindings,
    require_image,
    unique_name,
    wait_exec,
)


def test_traefik_tls_sni_isolates_two_postgres_and_two_kv_backends():
    for image in (POSTGRES_IMAGE, REDIS_IMAGE, TRAEFIK_IMAGE):
        require_image(image)
    names = {
        "pg1": unique_name("sni-pg1"),
        "pg2": unique_name("sni-pg2"),
        "kv1": unique_name("sni-kv1"),
        "kv2": unique_name("sni-kv2"),
        "traefik": unique_name("sni-traefik"),
    }
    domains = {
        "pg1": "one.postgres.test",
        "pg2": "two.postgres.test",
        "kv1": "one.kv.test",
        "kv2": "two.kv.test",
    }

    with (
        network() as network_name,
        container(
            POSTGRES_IMAGE,
            names["pg1"],
            env={"POSTGRES_USER": "default", "POSTGRES_HOST_AUTH_METHOD": "trust"},
            network_name=network_name,
            labels=_labels("postgres", "pg1", domains["pg1"], network_name, 5432),
            memory="1g",
        ),
        container(
            POSTGRES_IMAGE,
            names["pg2"],
            env={"POSTGRES_USER": "default", "POSTGRES_HOST_AUTH_METHOD": "trust"},
            network_name=network_name,
            labels=_labels("postgres", "pg2", domains["pg2"], network_name, 5432),
            memory="1g",
        ),
        container(
            REDIS_IMAGE,
            names["kv1"],
            ["redis-server", "--save", "", "--appendonly", "no"],
            network_name=network_name,
            labels=_labels("redis", "kv1", domains["kv1"], network_name, 6379),
        ),
        container(
            REDIS_IMAGE,
            names["kv2"],
            ["redis-server", "--save", "", "--appendonly", "no"],
            network_name=network_name,
            labels=_labels("redis", "kv2", domains["kv2"], network_name, 6379),
        ),
    ):
        for name in (names["pg1"], names["pg2"]):
            wait_exec(name, ["pg_isready", "-U", "default", "-d", "postgres"])
        for name in (names["kv1"], names["kv2"]):
            wait_exec(name, ["redis-cli", "PING"])
        _retry(
            [
                "docker",
                "exec",
                names["pg1"],
                "psql",
                "-U",
                "default",
                "-d",
                "postgres",
                "-c",
                "CREATE TABLE IF NOT EXISTS route(value text); "
                "TRUNCATE route; INSERT INTO route VALUES ('postgres-one')",
            ],
        )
        _retry(
            [
                "docker",
                "exec",
                names["pg2"],
                "psql",
                "-U",
                "default",
                "-d",
                "postgres",
                "-c",
                "CREATE TABLE IF NOT EXISTS route(value text); "
                "TRUNCATE route; INSERT INTO route VALUES ('postgres-two')",
            ],
        )
        docker_exec(names["kv1"], ["redis-cli", "SET", "route", "redis-one"])
        docker_exec(names["kv2"], ["redis-cli", "SET", "route", "redis-two"])

        with container(
            TRAEFIK_IMAGE,
            names["traefik"],
            [
                "--providers.docker=true",
                "--providers.docker.exposedbydefault=false",
                f"--providers.docker.network={network_name}",
                "--entrypoints.postgres.address=:5432",
                "--entrypoints.redis.address=:6379",
            ],
            mounts=[(Path("/var/run/docker.sock"), "/var/run/docker.sock", True)],
            network_name=network_name,
            publish=["127.0.0.1::5432", "127.0.0.1::6379"],
        ):
            postgres_port = _port(names["traefik"], "5432/tcp")
            redis_port = _port(names["traefik"], "6379/tcp")
            assert _postgres(domains["pg1"], postgres_port) == "postgres-one"
            assert _postgres(domains["pg2"], postgres_port) == "postgres-two"
            assert _redis(domains["kv1"], redis_port) == "redis-one"
            assert _redis(domains["kv2"], redis_port) == "redis-two"


def _labels(entrypoint: str, key: str, domain: str, network_name: str, port: int) -> dict:
    return {
        "traefik.enable": "true",
        "traefik.docker.network": network_name,
        f"traefik.tcp.routers.{key}.entrypoints": entrypoint,
        f"traefik.tcp.routers.{key}.rule": f"HostSNI(`{domain}`)",
        f"traefik.tcp.routers.{key}.tls": "true",
        f"traefik.tcp.services.{key}.loadbalancer.server.port": str(port),
    }


def _port(name: str, key: str) -> int:
    bindings = port_bindings(name)[key] or []
    assert len(bindings) == 1
    assert bindings[0]["HostIp"] == "127.0.0.1"
    return int(bindings[0]["HostPort"])


def _postgres(domain: str, port: int) -> str:
    result = _retry(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "host",
            "--add-host",
            f"{domain}:127.0.0.1",
            POSTGRES_IMAGE,
            "psql",
            f"postgresql://default@{domain}:{port}/postgres?sslmode=require",
            "-X",
            "-A",
            "-t",
            "-c",
            "SELECT value FROM route",
        ]
    )
    return result.stdout.strip()


def _redis(domain: str, port: int) -> str:
    result = _retry(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "host",
            "--add-host",
            f"{domain}:127.0.0.1",
            REDIS_IMAGE,
            "redis-cli",
            "--tls",
            "--insecure",
            "--sni",
            domain,
            "-h",
            domain,
            "-p",
            str(port),
            "--raw",
            "GET",
            "route",
        ]
    )
    return result.stdout.strip()


def _retry(args: list[str]):
    deadline = time.monotonic() + 60
    result = None
    while time.monotonic() < deadline:
        result = command(args, check=False, timeout=30)
        if result.returncode == 0:
            return result
        time.sleep(0.5)
    assert result is not None
    raise AssertionError(result.stderr or result.stdout)
