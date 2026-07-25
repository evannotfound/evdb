from __future__ import annotations

import shutil
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from evanovation_db import deployment, lifecycle
from evanovation_db.config import Config, load, load_lock, render_runtime, write_lock
from evanovation_db.files import hash as file_hash
from evanovation_db.files import write_json, write_text

sys.path.insert(0, str(Path(__file__).parents[1]))

from fixtures.containers import (  # noqa: E402
    REDIS_IMAGE,
    command,
    require_docker,
    unique_name,
)

ROOT = Path(__file__).parents[2]


def test_disposable_lifecycle_preserves_data_and_managed_files(tmp_path, monkeypatch):
    require_docker()
    if shutil.which("docker") is None:
        pytest.skip("docker is unavailable")
    if command(["docker", "compose", "version"], check=False).returncode != 0:
        pytest.skip("docker compose is unavailable")
    if command(["docker", "image", "inspect", REDIS_IMAGE], check=False).returncode != 0:
        pytest.skip("locked Redis image is not already present; test does not pull images")

    source = load(ROOT / "tests/fixtures/config/minimal")
    name = unique_name("lifecycle")
    password = "disposable-lifecycle-password"
    data = tmp_path / "data"
    data.mkdir()
    data.chmod(0o777)
    secret = tmp_path / "etc/secrets/kv-cache-dev-01.password"
    redis_config = tmp_path / "etc/secrets/kv-cache-dev-01.conf"
    write_text(secret, password + "\n")
    write_text(
        redis_config,
        f"bind 0.0.0.0\nprotected-mode no\ndir /data\nrequirepass {password}\n",
    )
    redis_config.chmod(0o644)
    host = replace(
        source.host,
        config_dir=tmp_path / "etc",
        state_dir=tmp_path / "state",
        backup_dir=tmp_path / "backups",
        lock_dir=tmp_path / "locks",
    )
    base = source.select("redis/cache-dev-01")
    http = {**base.http, "enabled": False, "port": None}
    instance = replace(
        base,
        image=REDIS_IMAGE,
        project=name,
        container=name,
        data=data,
        secrets={"password": str(secret)},
        http=http,
    )
    config = Config(host, (instance,))
    bundle = deployment.build(
        config,
        load_lock(ROOT / "tests/fixtures/config/minimal/host.lock.json"),
        file_hash(ROOT / "tests/fixtures/config/minimal/host.yml"),
    )
    release = tmp_path / "opt/releases/release-test"
    compose_name = "compose/kv/cache-dev-01.yml"
    release_manifest = deepcopy(bundle.manifest)
    release_manifest["id"] = "release-test"
    database = release_manifest["databases"][instance.selector]
    database["compose"] = compose_name
    compose = {
        "name": name,
        "services": {
            "redis": {
                "image": REDIS_IMAGE,
                "container_name": name,
                "network_mode": "none",
                "command": ["redis-server", "/run/secrets/redis.conf"],
                "labels": {deployment.CONTRACT_LABEL: database["service_hash"]},
                "volumes": [
                    f"{data}:/data",
                    f"{redis_config}:/run/secrets/redis.conf:ro",
                ],
            }
        },
    }
    write_text(release / compose_name, yaml.safe_dump(compose, sort_keys=False))
    write_json(release / deployment.MANIFEST, release_manifest)
    current = tmp_path / "opt/current"
    current.symlink_to(release)
    monkeypatch.setattr(deployment, "ROOT", tmp_path / "opt")

    markers = []
    for path in (
        tmp_path / "source/host.yml",
        tmp_path / "etc/runtime-marker",
        tmp_path / "backups/history",
        tmp_path / "state/release-history",
    ):
        write_text(path, str(path))
        markers.append((path, path.read_bytes()))
    release_before = (release / deployment.MANIFEST).read_bytes()
    auth = {"REDISCLI_AUTH": password}

    try:
        lifecycle.execute(config, instance, "start", {})
        command(
            ["docker", "exec", "--env", "REDISCLI_AUTH", name, "redis-cli", "SET", "key", "value"],
            env=auth,
        )
        lifecycle.execute(config, instance, "stop", {})
        lifecycle.execute(config, instance, "start", {})
        result = command(
            ["docker", "exec", "--env", "REDISCLI_AUTH", name, "redis-cli", "GET", "key"],
            env=auth,
        )
        assert result.stdout.strip() == "value"
        lifecycle.execute(config, instance, "restart", {})
        result = command(
            ["docker", "exec", "--env", "REDISCLI_AUTH", name, "redis-cli", "GET", "key"],
            env=auth,
        )
        assert result.stdout.strip() == "value"
    finally:
        command(
            ["docker", "compose", "-f", str(release / compose_name), "down"],
            check=False,
        )

    assert all(path.read_bytes() == value for path, value in markers)
    assert secret.read_text() == password + "\n"
    assert (release / deployment.MANIFEST).read_bytes() == release_before


def test_disposable_compose_rollback_preserves_live_data_path(tmp_path, monkeypatch):
    require_docker()
    if shutil.which("docker") is None:
        pytest.skip("docker is unavailable")
    if command(["docker", "compose", "version"], check=False).returncode != 0:
        pytest.skip("docker compose is unavailable")
    if command(["docker", "image", "inspect", REDIS_IMAGE], check=False).returncode != 0:
        pytest.skip("locked Redis image is not already present; test does not pull images")

    source = load(ROOT / "tests/fixtures/config/minimal")
    name = unique_name("release-rollback")
    password = "disposable-release-rollback-password"
    data = tmp_path / "data"
    data.mkdir()
    data.chmod(0o777)
    marker = data / "operator-marker"
    marker.write_text("must remain in place\n")
    marker_before = (marker.stat().st_ino, marker.read_bytes())
    secret = tmp_path / "etc/secrets/kv-cache-dev-01.password"
    redis_config = tmp_path / "etc/secrets/kv-cache-dev-01.conf"
    write_text(secret, password + "\n")
    write_text(
        redis_config,
        f"bind 0.0.0.0\nprotected-mode no\ndir /data\nrequirepass {password}\n",
    )
    redis_config.chmod(0o644)
    host = replace(
        source.host,
        data_root=tmp_path / "data-root",
        config_dir=tmp_path / "etc",
        state_dir=tmp_path / "state",
        backup_dir=tmp_path / "backups",
        lock_dir=tmp_path / "locks",
    )
    http = {**source.select("redis/cache-dev-01").http, "enabled": False, "port": None}
    instance = replace(
        source.select("redis/cache-dev-01"),
        image=REDIS_IMAGE,
        project=name,
        container=name,
        data=data,
        secrets={"password": str(secret)},
        http=http,
    )
    config = Config(host, (instance,))
    bundle = deployment.build(
        config,
        load_lock(ROOT / "tests/fixtures/config/minimal/host.lock.json"),
        file_hash(ROOT / "tests/fixtures/config/minimal/host.yml"),
    )
    compose_name = "compose/kv/cache-dev-01.json"
    base_compose = {
        "name": name,
        "services": {
            "redis": {
                "image": REDIS_IMAGE,
                "container_name": name,
                "network_mode": "none",
                "command": ["redis-server", "/run/secrets/redis.conf"],
                "volumes": [
                    f"{data}:/data",
                    f"{redis_config}:/run/secrets/redis.conf:ro",
                ],
            }
        },
    }
    root = tmp_path / "opt"
    monkeypatch.setattr(deployment, "ROOT", root)
    monkeypatch.setattr(deployment, "UNIT_DIR", tmp_path / "systemd")
    monkeypatch.setattr(deployment, "_reload_units", lambda config: None)
    prior_manifest = deepcopy(bundle.manifest)
    prior_manifest["id"] = "release-prior"
    prior_manifest["databases"][instance.selector].update(
        {
            "compose": compose_name,
            "service_hash": "8" * 64,
            "project": name,
            "container": name,
            "image": REDIS_IMAGE,
        }
    )
    current_manifest = deepcopy(prior_manifest)
    current_manifest["id"] = "release-current"
    current_manifest["databases"][instance.selector]["service_hash"] = "9" * 64
    prior = _write_rollback_release(
        config,
        bundle,
        root,
        prior_manifest,
        compose_name,
        {
            **base_compose,
            "services": {
                "redis": {**base_compose["services"]["redis"], "environment": {"RELEASE": "prior"}}
            },
        },
    )
    current = _write_rollback_release(
        config,
        bundle,
        root,
        current_manifest,
        compose_name,
        {
            **base_compose,
            "services": {
                "redis": {
                    **base_compose["services"]["redis"],
                    "environment": {"RELEASE": "current"},
                }
            },
        },
    )
    _mark_release_active(config, prior_manifest, None)
    _mark_release_active(config, current_manifest, prior.name)
    link = root / "current"
    link.symlink_to(current)
    auth = {"REDISCLI_AUTH": password}

    try:
        command(["docker", "compose", "-f", str(current / compose_name), "up", "-d"])
        command(
            ["docker", "exec", "--env", "REDISCLI_AUTH", name, "redis-cli", "SET", "key", "value"],
            env=auth,
        )
        command(
            ["docker", "exec", "--env", "REDISCLI_AUTH", name, "redis-cli", "SAVE"],
            env=auth,
        )

        result = deployment.rollback(
            config,
            {"release": prior.name, "expected": current.name},
        )

        value = command(
            ["docker", "exec", "--env", "REDISCLI_AUTH", name, "redis-cli", "GET", "key"],
            env=auth,
        )
        assert result["release"] == prior.name
        assert value.stdout.strip() == "value"
        assert (marker.stat().st_ino, marker.read_bytes()) == marker_before
        assert link.resolve() == prior
    finally:
        command(["docker", "compose", "-f", str(prior / compose_name), "down"], check=False)


def _write_rollback_release(config, bundle, root, manifest, compose_name, compose):
    release = root / "releases" / manifest["id"]
    database = manifest["databases"][next(iter(manifest["databases"]))]
    database["labels"] = {deployment.CONTRACT_LABEL: database["service_hash"]}
    service = next(iter(compose["services"].values()))
    service["labels"] = {deployment.CONTRACT_LABEL: database["service_hash"]}
    render_runtime(config, release / "runtime")
    write_lock(release / "host.lock.json", bundle.lock)
    for name in deployment.UNIT_NAMES:
        write_text(release / "systemd" / name, bundle.files[f"systemd/{name}"], mode=0o640)
    write_json(release / compose_name, compose, mode=0o644)
    write_json(release / deployment.MANIFEST, manifest, mode=0o644)
    return release


def _mark_release_active(config, manifest, predecessor):
    deployment._record_stage(
        config,
        {
            "manifest": manifest,
            "expected": predecessor,
            "plan": {"actions": [], "blocked": []},
        },
    )
    deployment._record_activation(
        config,
        manifest["id"],
        predecessor,
        operation_name="apply",
    )
