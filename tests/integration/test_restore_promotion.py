from __future__ import annotations

import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from evanovation_db import deployment, manifest
from evanovation_db.backup import kv
from evanovation_db.config import Config, load, load_lock, render_runtime
from evanovation_db.files import hash as file_hash
from evanovation_db.files import write_json, write_text
from evanovation_db.restore import promotion

sys.path.insert(0, str(Path(__file__).parents[1]))

from fixtures.containers import (  # noqa: E402
    REDIS_IMAGE,
    command,
    container,
    docker_exec,
    require_docker,
    unique_name,
    wait_exec,
)

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests/fixtures/config/minimal"


def test_disposable_redis_candidate_and_atomic_promotion_without_image_pull(tmp_path, monkeypatch):
    require_docker()
    if command(["docker", "compose", "version"], check=False).returncode != 0:
        pytest.skip("docker compose is unavailable")
    if command(["docker", "image", "inspect", REDIS_IMAGE], check=False).returncode != 0:
        pytest.skip("locked Redis image is not already present; test does not pull images")

    source = load(FIXTURE)
    base = source.select("redis/cache-dev-01")
    name = unique_name("restore-promotion")
    password = "disposable-restore-promotion-password"
    data_root = tmp_path / "data-root"
    live = data_root / "kv" / base.id / "data"
    live.mkdir(parents=True)
    live.chmod(0o777)
    marker = live / "operator-marker"
    marker.write_text("prior-live-data\n")
    secret_dir = tmp_path / "etc/secrets"
    password_file = secret_dir / f"kv-{base.id}.password"
    redis_config = secret_dir / f"kv-{base.id}.conf"
    write_text(password_file, password + "\n")
    write_text(
        redis_config,
        f"bind 0.0.0.0\nprotected-mode no\ndir /data\ndbfilename dump.rdb\n"
        f"requirepass {password}\n",
    )
    redis_config.chmod(0o644)
    host = replace(
        source.host,
        data_root=data_root,
        config_dir=tmp_path / "etc",
        state_dir=tmp_path / "state",
        backup_dir=tmp_path / "backups",
        lock_dir=tmp_path / "locks",
    )
    http = {**base.http, "enabled": False, "port": None}
    instance = replace(
        base,
        image=REDIS_IMAGE,
        project=name,
        container=name,
        data=live,
        secrets={"password": str(password_file)},
        http=http,
    )
    config = Config(host, (instance,))
    bundle = deployment.build(
        config,
        load_lock(FIXTURE / "host.lock.json"),
        file_hash(FIXTURE / "host.yml"),
    )
    release = tmp_path / "opt/releases/release-test"
    compose_name = bundle.manifest["databases"][instance.selector]["compose"]
    compose = {
        "name": name,
        "services": {
            "redis": {
                "image": REDIS_IMAGE,
                "container_name": name,
                "network_mode": "none",
                "command": ["redis-server", "/run/secrets/redis.conf"],
                "volumes": [
                    f"{live}:/data",
                    f"{redis_config}:/run/secrets/redis.conf:ro",
                ],
            }
        },
    }
    render_runtime(config, release / "runtime")
    write_json(release / compose_name, compose, mode=0o644)
    release_manifest = {**bundle.manifest, "id": release.name}
    write_json(release / deployment.MANIFEST, release_manifest, mode=0o644)
    current = tmp_path / "opt/current"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(release)
    monkeypatch.setattr(deployment, "ROOT", tmp_path / "opt")

    backup = tmp_path / "snapshot-backup"
    snapshot_container = unique_name("restore-snapshot")
    with container(REDIS_IMAGE, snapshot_container):
        wait_exec(snapshot_container, ["redis-cli", "PING"])
        docker_exec(snapshot_container, ["redis-cli", "SET", "restored-key", "candidate"])
        docker_exec(snapshot_container, ["redis-cli", "SAVE"])
        snapshot_instance = replace(instance, container=snapshot_container)
        facts = kv.facts(snapshot_instance, "")
        backup.mkdir()
        command(["docker", "cp", f"{snapshot_container}:/data/dump.rdb", str(backup)])
    version = facts.pop("version")
    manifest.write(
        backup,
        {
            "status": "complete",
            "host": host.id,
            "group": instance.group,
            "instance": instance.id,
            "engine": instance.engine,
            "image": instance.image,
            "version": version,
            "facts": facts,
            "files": manifest.files(backup, ["dump.rdb"]),
        },
    )

    def select_snapshot(selected_host, selected_instance, value):
        assert value == "latest"
        return {"id": "snapshot-exact", "time": "2026-07-25T11:00:00+00:00"}

    def restore_snapshot(selected_host, selected_instance, snapshot, target):
        target.mkdir(parents=True)
        restored = target / backup.name
        shutil.copytree(backup, restored)
        return restored

    monkeypatch.setattr(promotion.restic, "select_snapshot", select_snapshot)
    monkeypatch.setattr(promotion.restic, "restore", restore_snapshot)
    auth = {"REDISCLI_AUTH": password}

    try:
        command(["docker", "compose", "-f", str(release / compose_name), "up", "-d"])
        wait_exec(name, ["redis-cli", "PING"], env=auth)
        docker_exec(name, ["redis-cli", "SET", "live-key", "prior"], env=auth)
        docker_exec(name, ["redis-cli", "SAVE"], env=auth)

        restored = promotion.create(config, instance, "latest")

        assert _redis(name, auth, ["GET", "live-key"]) == "prior"
        assert _redis(name, auth, ["GET", "restored-key"]) == ""
        candidate = promotion._candidate_path(instance, restored["restore_id"])
        assert candidate.parent == live.parent
        assert candidate.stat().st_dev == live.stat().st_dev
        candidate_owner = (candidate / "dump.rdb").stat().st_uid
        engine_owner = int(docker_exec(name, ["stat", "-c", "%u", "/data/dump.rdb"]).stdout.strip())
        assert candidate_owner == engine_owner
        assert candidate_owner != os.getuid()

        plan = promotion.plan(config, instance, restored["restore_id"])
        result = promotion.promote(
            config,
            instance,
            {
                "restore_id": restored["restore_id"],
                "expected_release": plan["active_release"],
                "manifest_hash": plan["manifest_hash"],
            },
        )

        assert _redis(name, auth, ["GET", "restored-key"]) == "candidate"
        assert _redis(name, auth, ["GET", "live-key"]) == ""
        retained = Path(result["retained"])
        assert retained.is_dir()
        assert (retained / "operator-marker").read_text() == "prior-live-data\n"
        assert promotion.retained(instance) == [str(retained)]
        assert promotion._candidate_record_dir(config, instance, restored["restore_id"]).is_dir()
    finally:
        command(
            ["docker", "compose", "-f", str(release / compose_name), "down"],
            check=False,
        )
        command(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0",
                "--volume",
                f"{tmp_path}:/cleanup",
                "--entrypoint",
                "chown",
                REDIS_IMAGE,
                "-R",
                f"{os.getuid()}:{os.getgid()}",
                "/cleanup",
            ],
            check=False,
        )


def _redis(name: str, env: dict[str, str], args: list[str]) -> str:
    return docker_exec(name, ["redis-cli", "--raw", *args], env=env).stdout.strip()
