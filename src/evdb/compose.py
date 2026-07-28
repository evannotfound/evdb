from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from .config import Config, Database, MachineState
from .errors import ConfigError
from .files import write_text
from .run import run

NETWORK = "evdb"
NETWORK_LABEL = "com.evanovation.evdb.network"
CONTRACT_LABEL = "com.evanovation.evdb.contract"
TRAEFIK_PROJECT = "evdb-traefik"
TRAEFIK_CONTAINER = "evdb-traefik"


def service_hash(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def database(config: Config, target: Database, state: MachineState) -> dict[str, Any]:
    role = _role_state(target, state)
    primary_name = _name(target, "primary")
    primary: dict[str, Any] = {
        "image": role.images["primary"].image,
        "container_name": primary_name,
        "restart": "unless-stopped",
        "volumes": [f"{target.data}:{_data_mount(target)}"],
        "networks": {NETWORK: {"aliases": [primary_name]}},
    }
    services: dict[str, Any] = {primary_name: primary}
    route_service = primary

    if target.role == "postgres":
        _postgres(config, target, role.images, services, primary)
        if target.settings.pgbouncer.enabled:
            route_service = services[_name(target, "pgbouncer")]
    else:
        _kv(config, target, role, services, primary)

    route_service.setdefault("labels", {}).update(_route(target))
    result = {
        "name": target.compose_project,
        "services": services,
        "networks": {NETWORK: {"external": True, "name": NETWORK}},
    }
    for name, service in services.items():
        contract: dict[str, Any] = {
            "project": target.compose_project,
            "service": name,
            "definition": service,
            "networks": result["networks"],
        }
        if name == primary_name and target.role == "kv":
            contract["engine_settings"] = {
                "mode": target.settings.mode,
                "memory": target.settings.memory,
                "threads": target.settings.threads,
            }
        elif name == _name(target, "pgbouncer"):
            contract["pool_config"] = pool_config(target)
        service.setdefault("labels", {})[CONTRACT_LABEL] = service_hash(contract)
    return result


def traefik(config: Config, state: MachineState) -> dict[str, Any]:
    try:
        image = state.images["traefik"].image
    except KeyError as exc:
        raise ConfigError("Traefik image has not been resolved") from exc
    provider = config.host.routing.dns_provider
    result = {
        "name": TRAEFIK_PROJECT,
        "services": {
            "traefik": {
                "image": image,
                "container_name": TRAEFIK_CONTAINER,
                "restart": "unless-stopped",
                "command": [
                    "--providers.docker=true",
                    "--providers.docker.exposedbydefault=false",
                    f"--providers.docker.network={NETWORK}",
                    "--ping=true",
                    "--entrypoints.postgres.address=:5432",
                    "--entrypoints.kv.address=:6379",
                    "--entrypoints.postgres.http.tls.certresolver=evdb",
                    "--entrypoints.kv.http.tls.certresolver=evdb",
                    f"--certificatesresolvers.evdb.acme.email={config.host.routing.acme_email}",
                    "--certificatesresolvers.evdb.acme.storage=/acme/acme.json",
                    "--certificatesresolvers.evdb.acme.dnschallenge=true",
                    f"--certificatesresolvers.evdb.acme.dnschallenge.provider={provider}",
                ],
                "env_file": [str(config.paths.traefik / "dns.env")],
                "ports": ["5432:5432/tcp", "6379:6379/tcp"],
                "healthcheck": _healthcheck(["CMD", "traefik", "healthcheck", "--ping"]),
                "volumes": [
                    "/var/run/docker.sock:/var/run/docker.sock:ro",
                    f"{config.paths.traefik}/acme:/acme",
                ],
                "networks": [NETWORK],
            }
        },
        "networks": {NETWORK: {"external": True, "name": NETWORK}},
    }
    contract = service_hash(result)
    result["services"]["traefik"]["labels"] = {CONTRACT_LABEL: contract}
    return result


def pool_config(target: Database) -> str:
    if target.role != "postgres":
        raise ConfigError("PgBouncer configuration requires Postgres")
    pool = target.settings.pgbouncer
    return (
        "[databases]\n"
        f"* = host={_name(target, 'primary')} port=5432\n\n"
        "[pgbouncer]\n"
        "listen_addr = 0.0.0.0\n"
        "listen_port = 5432\n"
        "auth_type = plain\n"
        "auth_file = /run/secrets/pgbouncer-users\n"
        f"max_client_conn = {pool.max_clients}\n"
        f"default_pool_size = {pool.pool_size}\n"
        f"reserve_pool_size = {pool.reserve_size}\n"
        "ignore_startup_parameters = extra_float_digits\n"
    )


def write(path: str | Path, value: dict[str, Any]) -> None:
    text = yaml.safe_dump(value, sort_keys=False)
    write_text(path, text, mode=0o640)


def command(path: str | Path, project: str, *args: str) -> list[str]:
    return ["docker", "compose", "-f", str(path), "--project-name", project, *args]


def validate(
    path: str | Path,
    project: str,
    *,
    timeout: int = 300,
    secrets: tuple[str, ...] = (),
) -> None:
    run(
        command(path, project, "config", "--quiet", "--no-env-resolution"),
        timeout=timeout,
        secrets=secrets,
    )


def ensure_network(*, timeout: int = 60) -> None:
    if _network_exists(timeout=timeout):
        return
    run(
        ["docker", "network", "create", "--label", f"{NETWORK_LABEL}=true", NETWORK],
        timeout=timeout,
    )


def _network_exists(*, timeout: int = 60) -> bool:
    result = run(["docker", "network", "inspect", NETWORK], timeout=timeout, check=False)
    if result.code != 0:
        detail = (result.err or result.out).lower()
        if detail and "not found" not in detail and "no such network" not in detail:
            raise ConfigError("evdb Docker network inspection failed")
        return False
    try:
        items = json.loads(result.out)
        labels = items[0]["Labels"]
    except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
        raise ConfigError("existing evdb Docker network is invalid") from exc
    if labels.get(NETWORK_LABEL) != "true":
        raise ConfigError("existing evdb Docker network is not owned by evdb")
    return True


def expected_services(value: dict[str, Any], target: Database) -> dict[str, dict[str, Any]]:
    result = {}
    for name, service in value["services"].items():
        result[name] = {
            "container": service["container_name"],
            "image": service["image"],
            "health": "engine" if name == _name(target, "primary") else "docker",
            "contract": service["labels"][CONTRACT_LABEL],
        }
    return result


def _postgres(config, target, images, services, primary) -> None:
    secret_dir = config.paths.role_secrets(target.project, target.role)
    primary["environment"] = {
        "POSTGRES_USER": target.settings.user,
        "POSTGRES_DB": target.settings.database,
        "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres-password",
    }
    primary["volumes"].append(f"{secret_dir}/password:/run/secrets/postgres-password:ro")
    if not target.settings.pgbouncer.enabled:
        return
    name = _name(target, "pgbouncer")
    services[name] = {
        "image": images["pgbouncer"].image,
        "container_name": name,
        "restart": "unless-stopped",
        "user": _managed_user(config, target),
        "command": ["pgbouncer", "/etc/pgbouncer/pgbouncer.ini"],
        "depends_on": [_name(target, "primary")],
        "healthcheck": _healthcheck(
            [
                "CMD",
                "pg_isready",
                "-h",
                "127.0.0.1",
                "-p",
                "5432",
                "-U",
                target.settings.user,
                "-d",
                target.settings.database,
            ]
        ),
        "volumes": [
            f"{config.paths.role_config(target.project, target.role)}/pgbouncer.ini:"
            "/etc/pgbouncer/pgbouncer.ini:ro",
            f"{secret_dir}/pgbouncer-users:/run/secrets/pgbouncer-users:ro",
        ],
        "networks": {NETWORK: {"aliases": [name]}},
    }


def _managed_user(config: Config, target: Database) -> str:
    """Match PgBouncer to the owner of its private bind-mounted files."""
    owners = {
        _owner(config.paths.role_config(target.project, target.role)),
        _owner(config.paths.role_secrets(target.project, target.role)),
    }
    if len(owners) != 1:
        raise ConfigError(f"{target.identity}: managed file owners differ")
    uid, gid = owners.pop()
    return f"{uid}:{gid}"


def _owner(path: Path) -> tuple[int, int]:
    current = path
    while not current.exists():
        if current.parent == current:
            break
        current = current.parent
    stat = current.stat()
    return stat.st_uid, stat.st_gid


def _kv(config, target, role, services, primary) -> None:
    secret_dir = config.paths.role_secrets(target.project, target.role)
    if target.engine == "redis":
        primary["command"] = ["/usr/local/bin/redis-server", "/run/secrets/redis.conf"]
        primary["volumes"].append(f"{secret_dir}/redis.conf:/run/secrets/redis.conf:ro")
    else:
        primary["command"] = [
            "/usr/local/bin/dragonfly",
            "--logtostderr",
            "--flagfile=/run/secrets/dragonfly.flags",
        ]
        primary["volumes"].append(f"{secret_dir}/dragonfly.flags:/run/secrets/dragonfly.flags:ro")
    primary["cap_drop"] = ["ALL"]
    primary["cap_add"] = ["DAC_OVERRIDE"]
    primary["security_opt"] = ["no-new-privileges:true"]
    if not target.settings.http.enabled:
        return
    if role.http_port is None:
        raise ConfigError(f"{target.identity}: HTTP port has not been allocated")
    name = _name(target, "http")
    services[name] = {
        "image": role.images["http"].image,
        "container_name": name,
        "restart": "unless-stopped",
        "env_file": [str(secret_dir / "http.env")],
        "environment": {
            "SRH_MODE": "env",
            "SRH_MAX_CONNECTIONS": str(target.settings.http.connections),
        },
        "depends_on": [_name(target, "primary")],
        "healthcheck": _healthcheck(["CMD", "wget", "--spider", "--quiet", "http://127.0.0.1:80/"]),
        "ports": [f"127.0.0.1:{role.http_port}:80"],
        "networks": {NETWORK: {"aliases": [name]}},
    }


def _route(target: Database) -> dict[str, str]:
    key = f"{target.role}-{target.project}"
    entrypoint = "postgres" if target.role == "postgres" else "kv"
    return {
        "traefik.enable": "true",
        "traefik.docker.network": NETWORK,
        f"traefik.tcp.routers.{key}.entrypoints": entrypoint,
        f"traefik.tcp.routers.{key}.rule": f"HostSNI(`{target.domain}`)",
        f"traefik.tcp.routers.{key}.tls": "true",
        f"traefik.tcp.routers.{key}.tls.certresolver": "evdb",
        f"traefik.tcp.services.{key}.loadbalancer.server.port": str(target.port),
    }


def _role_state(target: Database, state: MachineState):
    try:
        role = state.roles[target.identity]
    except KeyError as exc:
        raise ConfigError(f"{target.identity}: machine state is missing") from exc
    if role.engine != target.engine:
        raise ConfigError(f"{target.identity}: machine state engine differs")
    if "primary" not in role.images:
        raise ConfigError(f"{target.identity}: primary image has not been resolved")
    return role


def _name(target: Database, service: str) -> str:
    return f"evdb-{target.project}-{target.role}-{service}"


def _data_mount(target: Database) -> str:
    return "/var/lib/postgresql/data" if target.role == "postgres" else "/data"


def _healthcheck(test: list[str]) -> dict[str, Any]:
    return {
        "test": test,
        "interval": "10s",
        "timeout": "5s",
        "retries": 12,
        "start_period": "10s",
    }
