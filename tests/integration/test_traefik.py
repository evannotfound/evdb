from __future__ import annotations

import shutil
import socket
import ssl
import time
import uuid

import pytest
import yaml

from evdb.run import run

REDIS_IMAGE = "redis:7.2.5"
TRAEFIK_IMAGE = "traefik:v3.7.8"


def test_traefik_tls_sni_isolates_two_disposable_redis_backends(tmp_path):
    _require_docker()
    if shutil.which("openssl") is None:
        pytest.fail("openssl is required for the Traefik integration")
    suffix = uuid.uuid4().hex[:12]
    network = f"evdb-traefik-it-{suffix}"
    proxy = f"evdb-traefik-it-proxy-{suffix}"
    hosts = {
        f"alpha-{suffix}.test.invalid": (f"evdb-traefik-it-alpha-{suffix}", "alpha"),
        f"beta-{suffix}.test.invalid": (f"evdb-traefik-it-beta-{suffix}", "beta"),
    }
    containers = [name for name, _value in hosts.values()]
    containers.append(proxy)
    created_network = False
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    dynamic = tmp_path / "dynamic.yml"
    hostnames = tuple(hosts)
    run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            f"/CN={hostnames[0]}",
            "-addext",
            f"subjectAltName=DNS:{hostnames[0]},DNS:{hostnames[1]}",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        timeout=60,
    )
    dynamic.write_text(
        yaml.safe_dump(
            {
                "tls": {
                    "certificates": [{"certFile": "/config/cert.pem", "keyFile": "/config/key.pem"}]
                },
                "tcp": {
                    "routers": {
                        value: {
                            "entryPoints": ["redis"],
                            "rule": f"HostSNI(`{hostname}`)",
                            "service": value,
                            "tls": {},
                        }
                        for hostname, (_container, value) in hosts.items()
                    },
                    "services": {
                        value: {"loadBalancer": {"servers": [{"address": f"{container}:6379"}]}}
                        for container, value in hosts.values()
                    },
                },
            },
            sort_keys=False,
        )
    )

    try:
        run(["docker", "network", "create", network], timeout=60)
        created_network = True
        for container, value in hosts.values():
            run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    container,
                    "--network",
                    network,
                    REDIS_IMAGE,
                ],
                timeout=600,
            )
            _redis_set(container, value)
        run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                proxy,
                "--network",
                network,
                "--publish",
                "127.0.0.1::8443/tcp",
                "--volume",
                f"{tmp_path}:/config:ro",
                TRAEFIK_IMAGE,
                "--entrypoints.redis.address=:8443",
                "--providers.file.filename=/config/dynamic.yml",
                "--providers.file.watch=false",
            ],
            timeout=600,
        )
        port = int(
            run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    '{{(index (index .NetworkSettings.Ports "8443/tcp") 0).HostPort}}',
                    proxy,
                ],
                timeout=30,
            ).out.strip()
        )

        assert {hostname: _tls_get(port, hostname) for hostname in hosts} == {
            hostname: value for hostname, (_container, value) in hosts.items()
        }
    finally:
        for container in reversed(containers):
            run(["docker", "rm", "--force", "--volumes", container], timeout=120, check=False)
        if created_network:
            run(["docker", "network", "rm", network], timeout=60, check=False)


def _redis_set(container: str, value: str) -> None:
    deadline = time.monotonic() + 30
    while True:
        result = run(
            ["docker", "exec", container, "redis-cli", "SET", "route", value],
            timeout=10,
            check=False,
        )
        if result.code == 0 and result.out.strip() == "OK":
            return
        if time.monotonic() >= deadline:
            pytest.fail(result.err.strip() or f"Redis did not become ready: {container}")
        time.sleep(0.2)


def _tls_get(port: int, hostname: str) -> str:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    deadline = time.monotonic() + 30
    error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with (
                socket.create_connection(("127.0.0.1", port), timeout=2) as plain,
                context.wrap_socket(plain, server_hostname=hostname) as secure,
            ):
                secure.sendall(b"*2\r\n$3\r\nGET\r\n$5\r\nroute\r\n")
                with secure.makefile("rb") as source:
                    size = source.readline()
                    if not size.startswith(b"$"):
                        raise ValueError(f"unexpected Redis response: {size!r}")
                    value = source.read(int(size[1:]))
                    if source.read(2) != b"\r\n":
                        raise ValueError("incomplete Redis response")
                    return value.decode()
        except (OSError, ssl.SSLError, UnicodeError, ValueError) as exc:
            error = exc
            time.sleep(0.2)
    pytest.fail(f"Traefik TLS route for {hostname} did not become ready: {error}")


def _require_docker() -> None:
    if socket.gethostname().split(".", 1)[0] == "montreal-01":
        pytest.skip("disposable Docker tests are forbidden on montreal-01")
    if shutil.which("docker") is None:
        pytest.skip("Docker is unavailable")
    result = run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=30, check=False)
    if result.code:
        pytest.skip(f"Docker daemon is unavailable: {result.err.strip() or result.out.strip()}")
