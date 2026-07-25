import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from evanovation_db import deployment
from evanovation_db.config import Config, Instance, load, load_lock
from evanovation_db.files import hash as file_hash

ROOT = Path(__file__).parents[2]
CONFIG = load(ROOT / "config/montreal-01")


def render(instance: Instance) -> dict:
    name = f"compose/{instance.group}/{instance.id}.json"
    return json.loads(deployment.compose(Config(CONFIG.host, (instance,)))[name])


def traefik() -> dict:
    return json.loads(deployment.compose(CONFIG)["compose/traefik/compose.json"])


def labels(data: dict) -> dict:
    return next(
        service["labels"]
        for service in data["services"].values()
        if any(key.startswith("traefik.tcp.") for key in service.get("labels", {}))
    )


def service(data: dict, container: str) -> dict:
    return next(item for item in data["services"].values() if item["container_name"] == container)


def test_every_instance_renders_with_its_direct_pinned_engine_image():
    for instance in CONFIG.instances:
        data = render(instance)
        database = service(data, instance.container)

        assert database["image"] == instance.image
        assert "@sha256:" in database["image"]
        assert database["container_name"] == instance.container
        assert database["volumes"][0].startswith(f"{instance.data}:")


def test_every_managed_service_has_manifest_contract_label_and_identity():
    bundle = deployment.build(
        CONFIG,
        load_lock(ROOT / "config/montreal-01/host.lock.json"),
        file_hash(ROOT / "config/montreal-01/host.yml"),
    )
    for instance in CONFIG.instances:
        services = render(instance)["services"]
        expected = bundle.manifest["databases"][instance.selector]
        assert set(services) == set(expected["services"])
        for name, current in services.items():
            assert current["labels"][deployment.CONTRACT_LABEL] == expected["service_hash"]
            assert expected["services"][name]["container"] == current["container_name"]
            assert expected["services"][name]["image"] == current["image"]
        assert expected["labels"] == {deployment.CONTRACT_LABEL: expected["service_hash"]}
    proxy = traefik()["services"]["traefik"]
    expected = bundle.manifest["infrastructure"]["traefik"]
    assert proxy["labels"][deployment.CONTRACT_LABEL] == expected["service_hash"]


def test_redis_instances_stay_redis_and_use_private_config():
    redis = [item for item in CONFIG.instances if item.engine == "redis"]

    assert len(redis) == 4
    for instance in redis:
        database = service(render(instance), instance.container)
        assert database["image"].startswith("redis:7.2.5@sha256:")
        assert database["command"] == ["redis-server", "/run/secrets/redis.conf"]
        assert "/run/secrets/redis.conf:ro" in database["volumes"][1]


def test_database_services_publish_no_native_host_ports():
    for instance in CONFIG.instances:
        for item in render(instance)["services"].values():
            if item["container_name"] != f"{instance.id}-http-1":
                assert "ports" not in item

    proxy = traefik()["services"]["traefik"]
    assert set(proxy["ports"]) == {"5432:5432/tcp", "6379:6379/tcp"}
    assert "@sha256:" in proxy["image"]


def test_sni_routes_use_instance_specific_backends():
    routes = {}
    for instance in CONFIG.instances:
        data = render(instance)
        route_labels = labels(data)
        prefix = "pg" if instance.engine == "postgres" else "kv"
        rule = route_labels[f"traefik.tcp.routers.{prefix}-{instance.id}.rule"]
        backend = next(
            current
            for current in data["services"].values()
            if current.get("labels") == route_labels
        )

        assert rule == f"HostSNI(`{instance.domain}`)"
        assert backend["container_name"].startswith(instance.id)
        assert backend["container_name"] not in {"postgres", "redis", "pgbouncer"}
        routes[instance.domain] = backend["container_name"]

    assert len(routes) == 25
    assert len(set(routes.values())) == 25


def test_http_sidecars_are_loopback_only_and_instance_specific():
    for instance in (item for item in CONFIG.instances if item.group == "kv"):
        sidecar = service(render(instance), f"{instance.id}-http-1")

        assert sidecar["ports"] == [f"127.0.0.1:{instance.http['port']}:80"]
        assert sidecar["container_name"] == f"{instance.id}-http-1"
        assert sidecar["environment"] == {
            "SRH_MODE": "env",
            "SRH_MAX_CONNECTIONS": str(instance.http["max_connections"]),
        }
        assert sidecar["env_file"] == [f"/etc/evanovation-db/secrets/kv-{instance.id}-http.env"]
        assert sidecar["labels"][deployment.CONTRACT_LABEL]


def test_infrastructure_sidecars_have_concrete_image_available_healthchecks():
    proxy = traefik()["services"]["traefik"]
    assert "--ping=true" in proxy["command"]
    assert proxy["healthcheck"]["test"] == ["CMD", "traefik", "healthcheck", "--ping"]

    postgres = next(item for item in CONFIG.instances if item.settings.get("pgbouncer") is True)
    pooler = service(render(postgres), f"{postgres.id}-pgbouncer-1")
    assert pooler["healthcheck"]["test"] == [
        "CMD",
        "pg_isready",
        "-h",
        "127.0.0.1",
        "-p",
        "5432",
    ]

    kv = next(item for item in CONFIG.instances if item.group == "kv")
    http = service(render(kv), f"{kv.id}-http-1")
    assert http["healthcheck"]["test"] == [
        "CMD",
        "wget",
        "--spider",
        "--quiet",
        "http://127.0.0.1:80/",
    ]
    for current in (proxy, pooler, http):
        assert current["healthcheck"]["interval"] == "10s"
        assert current["healthcheck"]["timeout"] == "5s"
        assert current["healthcheck"]["retries"] == 12


def test_per_instance_http_choices_change_only_the_sidecar():
    instance = next(item for item in CONFIG.instances if item.group == "kv")
    disabled = replace(instance, http={**instance.http, "enabled": False, "port": None})

    assert f"{instance.id}-http" not in render(disabled)["services"]


def test_postgres_uses_password_file_and_private_pgbouncer_users():
    for instance in (item for item in CONFIG.instances if item.engine == "postgres"):
        services = render(instance)["services"]
        assert services[f"{instance.id}-postgres"]["environment"]["POSTGRES_PASSWORD_FILE"] == (
            "/run/secrets/postgres-password"
        )
        if instance.settings["pgbouncer"]:
            mounts = services[f"{instance.id}-pgbouncer"]["volumes"]
            assert any("/run/secrets/pgbouncer-users:ro" in mount for mount in mounts)


def test_external_network_has_no_shared_database_aliases():
    for instance in CONFIG.instances:
        names = set(render(instance)["services"])
        assert not names.intersection({"postgres", "pgbouncer", "redis", "http"})


def test_normalized_services_remain_unlimited():
    limited = {"mem_limit", "mem_reservation", "cpus", "cpu_shares", "cpuset", "pids_limit"}
    services = []
    for instance in CONFIG.instances:
        services.extend(render(instance)["services"].values())
    services.extend(traefik()["services"].values())

    assert CONFIG.host.resources == {"traefik": "unlimited"}
    assert all(not limited.intersection(item) for item in services)


def test_migration_target_compatibility_and_jinja_compose_are_removed():
    assert not hasattr(Instance, "target")
    assert not list((ROOT / "compose").glob("*.j2"))
    assert '"target"' not in json.dumps(deployment.compose(CONFIG), sort_keys=True)


@pytest.mark.parametrize("engine", ["postgres", "redis", "dragonfly", "traefik"])
def test_generated_compose_passes_docker_validation(engine, tmp_path):
    if engine == "traefik":
        data = traefik()
    else:
        instance = next(item for item in CONFIG.instances if item.engine == engine)
        data = render(instance)
    env_file = tmp_path / "http.env"
    env_file.write_text("SRH_TOKEN=test\nSRH_CONNECTION_STRING=redis://test\n")
    for item in data["services"].values():
        if "env_file" in item:
            item["env_file"] = [str(env_file)]
    path = tmp_path / f"{engine}.json"
    path.write_text(json.dumps(data))

    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(path),
            "config",
            "--quiet",
            "--no-env-resolution",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
