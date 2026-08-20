from __future__ import annotations

import shutil
import socket
import ssl
import time
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from evdb import host
from evdb.run import run

REDIS_IMAGE = "redis:7.2.5"
TRAEFIK_IMAGE = "traefik:v3.7.8"
PEBBLE_IMAGE = (
    "ghcr.io/letsencrypt/pebble@"
    "sha256:ddf230642b1a584f519f32e347de1b05a6e4c1f6c35c1863b33effeab5f78199"
)
CHALLENGE_IMAGE = (
    "ghcr.io/letsencrypt/pebble-challtestsrv@"
    "sha256:12ce21884def456bcf9786542113949e1f19dc7738d2c70e156c2d0c38a1405b"
)
PYTHON_IMAGE = "python@sha256:05b2b8b732ecd268fee8727a369f936f022d1321b59befd13c30ede22769dcdc"


def test_traefik_wildcard_tls_sni_isolates_two_disposable_redis_backends(tmp_path):
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
            "/CN=*.test.invalid",
            "-addext",
            "subjectAltName=DNS:*.test.invalid",
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


def test_traefik_obtains_wildcard_from_disposable_acme(config, tmp_path):
    _require_docker()
    suffix = uuid.uuid4().hex[:12]
    network = f"evdb-acme-it-{suffix}"
    challenge = f"evdb-acme-it-challenge-{suffix}"
    bridge = f"evdb-acme-it-bridge-{suffix}"
    pebble = f"evdb-acme-it-pebble-{suffix}"
    proxy = f"evdb-acme-it-proxy-{suffix}"
    created_network = False
    selected = replace(config, paths=replace(config.paths, state=tmp_path / "state"))
    acme = selected.paths.traefik / "acme/acme.json"
    acme.parent.mkdir(parents=True)
    acme.write_text("{}\n")
    acme.chmod(0o600)
    dynamic = selected.paths.traefik / "tls.yml"
    dynamic.write_text(
        yaml.safe_dump(
            {
                "tls": {
                    "stores": {
                        "default": {
                            "defaultGeneratedCert": {
                                "resolver": "evdb",
                                "domain": {"main": host.wildcard(selected)},
                            }
                        }
                    }
                }
            },
            sort_keys=False,
        )
    )
    ca = Path(__file__).parents[1] / "fixtures/pebble.minica.pem"

    try:
        run(["docker", "network", "create", network], timeout=60)
        created_network = True
        run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                challenge,
                "--network",
                network,
                CHALLENGE_IMAGE,
                "-defaultIPv6",
                "",
                "-defaultIPv4",
                "127.0.0.1",
            ],
            timeout=600,
        )
        bridge_script = Path(__file__).parents[1] / "fixtures/dns_bridge.py"
        run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                bridge,
                "--network",
                network,
                "--network-alias",
                "dnsbridge",
                "--env",
                f"CHALLENGE_URL=http://{challenge}:8055",
                "--volume",
                f"{bridge_script}:/bridge.py:ro",
                PYTHON_IMAGE,
                "python",
                "/bridge.py",
            ],
            timeout=600,
        )
        run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                pebble,
                "--network",
                network,
                "--network-alias",
                "pebble",
                PEBBLE_IMAGE,
                "-config",
                "test/config/pebble-config.json",
                "-strict",
                "-dnsserver",
                f"{challenge}:8053",
            ],
            timeout=600,
        )
        run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                proxy,
                "--network",
                network,
                "--env",
                "HTTPREQ_ENDPOINT=http://dnsbridge:8080",
                "--env",
                "LEGO_CA_CERTIFICATES=/pebble.minica.pem",
                "--volume",
                f"{dynamic}:/config/tls.yml:ro",
                "--volume",
                f"{acme}:/acme.json",
                "--volume",
                f"{ca}:/pebble.minica.pem:ro",
                TRAEFIK_IMAGE,
                "--providers.file.filename=/config/tls.yml",
                "--providers.file.watch=false",
                "--certificatesresolvers.evdb.acme.email=ops@example.com",
                "--certificatesresolvers.evdb.acme.storage=/acme.json",
                "--certificatesresolvers.evdb.acme.caserver=https://pebble:14000/dir",
                "--certificatesresolvers.evdb.acme.dnschallenge=true",
                "--certificatesresolvers.evdb.acme.dnschallenge.provider=httpreq",
                f"--certificatesresolvers.evdb.acme.dnschallenge.resolvers={challenge}:8053",
                "--certificatesresolvers.evdb.acme.dnschallenge.propagation.disableANSChecks=true",
            ],
            timeout=600,
        )

        deadline = time.monotonic() + 120
        while not host.certificate_ready(selected):
            if time.monotonic() >= deadline:
                logs = run(["docker", "logs", proxy], timeout=30, check=False).out
                pytest.fail(f"disposable wildcard was not issued: {logs}")
            time.sleep(1)
    finally:
        for container in (proxy, pebble, bridge, challenge):
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
    if shutil.which("docker") is None:
        pytest.skip("Docker is unavailable")
    result = run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=30, check=False)
    if result.code:
        pytest.skip(f"Docker daemon is unavailable: {result.err.strip() or result.out.strip()}")
