from __future__ import annotations

import os
import uuid
from dataclasses import replace

from evdb import backup, compose, restic, restore, secrets
from evdb.config import resolve_state, write_state
from evdb.engines import kv
from tests.fixtures.containers import (
    REDIS_IMAGE,
    command,
    container,
    docker_exec,
    require_binary,
    require_image,
    unique_name,
    wait_exec,
)

DIGEST = REDIS_IMAGE.split("@", 1)[1]


def test_one_command_restore_verifies_backs_up_and_swaps_atomically(config, tmp_path, monkeypatch):
    require_binary("restic")
    require_image(REDIS_IMAGE)
    config, target, state = _database(config, tmp_path)
    password = "disposable-restore-password"
    secrets.ensure(config, target, generate=lambda: password)
    _restic(config)
    restic.init(config, "kv")
    selected = _selected_backup(config, target)
    target.data.mkdir(parents=True, mode=0o700)
    target.data.chmod(0o777)
    compose.write(target.compose, _compose(config, target, state))
    name = f"evdb-{target.project}-{target.role}-primary"
    auth = {"REDISCLI_AUTH": password}

    def health(*args, **kwargs):
        wait_exec(name, ["redis-cli", "PING"], env=auth)

    monkeypatch.setattr(restore.databases, "health", health)

    try:
        command(
            [
                "docker",
                "compose",
                "-f",
                str(target.compose),
                "-p",
                target.compose_project,
                "up",
                "-d",
            ]
        )
        health()
        docker_exec(name, ["redis-cli", "SET", "live-key", "prior"], env=auth)
        docker_exec(name, ["redis-cli", "SAVE"], env=auth)

        result = restore.restore(config, target, selected.name, yes=True, state=state)

        assert result["status"] == "healthy"
        assert result["backup"] == selected.name
        assert result["safety_snapshot"]
        assert _redis(name, auth, ["GET", "restored-key"]) == "candidate"
        assert _redis(name, auth, ["GET", "live-key"]) == ""
        assert not list(target.data.parent.glob(".data.prior-*"))
        assert (
            restic.select_snapshot(config, target, result["safety_snapshot"])["id"]
            == result["safety_snapshot"]
        )
    finally:
        docker_exec(name, ["chmod", "-R", "a+rwX", "/data"], check=False)
        command(
            ["docker", "compose", "-f", str(target.compose), "-p", target.compose_project, "down"],
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


def _database(config, tmp_path):
    project = config.projects[0]
    project_id = f"restore-{uuid.uuid4().hex[:8]}-test-01"
    settings = replace(
        project.kv,
        engine="redis",
        image="redis:7.2.5",
        mode="durable",
        http=replace(project.kv.http, enabled=False),
    )
    project = replace(project, id=project_id, postgres=None, kv=settings)
    host = replace(
        config.host,
        data_root=tmp_path / "data",
        backup=replace(
            config.host.backup,
            repos={"postgres": str(tmp_path / "pg-repo"), "kv": str(tmp_path / "kv-repo")},
        ),
    )
    config = replace(config, host=host, projects=(project,))
    target = config.select(f"{project_id}/kv")
    state = resolve_state(config, resolver=lambda source: DIGEST)
    role = replace(state.roles[target.identity], installed=True)
    state = replace(state, roles={target.identity: role})
    write_state(config, state)
    return config, target, state


def _restic(config):
    config.paths.secrets.mkdir(parents=True, exist_ok=True)
    (config.paths.secrets / "restic-password").write_text("local-restore-restic\n")
    (config.paths.secrets / "restic-password").chmod(0o600)
    config.paths.rclone.mkdir(parents=True, exist_ok=True)
    (config.paths.rclone / "rclone.conf").write_text("")


def _selected_backup(config, target):
    folder = config.paths.role_backups(target.project, target.role) / "selected-backup"
    folder.mkdir(parents=True, mode=0o700)
    source = unique_name("restore-source")
    with container(REDIS_IMAGE, source):
        wait_exec(source, ["redis-cli", "PING"])
        docker_exec(source, ["redis-cli", "SET", "restored-key", "candidate"])
        docker_exec(source, ["redis-cli", "SAVE"])
        facts = kv.facts(source, "")
        command(["docker", "cp", f"{source}:/data/dump.rdb", str(folder / "dump.rdb")])
    version = facts.pop("version")
    backup.manifest_write(
        folder,
        {
            "status": "complete",
            "backup": folder.name,
            "host": config.host.id,
            "project": target.project,
            "role": target.role,
            "engine": target.engine,
            "source_image": target.image,
            "image": f"{target.image}@{DIGEST}",
            "started": "2026-01-01T00:00:00+00:00",
            "finished": "2026-01-01T00:01:00+00:00",
            "version": version,
            "format": "redis-rdb-v1",
            "purpose": "manual",
            "facts": facts,
            "files": backup.manifest_files(folder, ["dump.rdb"]),
            "checks": ["size", "sha256", "redis"],
            "upload": {"ok": False, "backup": folder.name},
        },
    )
    return folder


def _compose(config, target, state):
    name = f"evdb-{target.project}-{target.role}-primary"
    return {
        "name": target.compose_project,
        "services": {
            "primary": {
                "image": state.roles[target.identity].images["primary"].image,
                "container_name": name,
                "network_mode": "none",
                "command": ["/usr/local/bin/redis-server", "/run/secrets/redis.conf"],
                "volumes": [
                    f"{target.data}:/data",
                    f"{secrets.path(config, target, 'redis.conf')}:/run/secrets/redis.conf:ro",
                ],
            }
        },
    }


def _redis(name, env, args):
    return docker_exec(name, ["redis-cli", "--raw", *args], env=env).stdout.strip()
