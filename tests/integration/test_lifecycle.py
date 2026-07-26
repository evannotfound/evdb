from __future__ import annotations

import uuid
from dataclasses import replace

from evanovation_db import compose, database, secrets
from evanovation_db.config import dump, resolve_state, write_state
from evanovation_db.engines import redis
from evanovation_db.errors import DatabaseError
from tests.fixtures.containers import REDIS_IMAGE, command, docker_exec, require_image, wait_exec

DIGEST = REDIS_IMAGE.split("@", 1)[1]


def test_direct_lifecycle_preserves_data_and_managed_files(config, tmp_path, monkeypatch):
    require_image(REDIS_IMAGE)
    config, target, state = _database(config, tmp_path)
    password = "disposable-lifecycle-password"
    secrets.ensure(config, target, generate=lambda: password)
    target.data.mkdir(parents=True, mode=0o700)
    target.data.chmod(0o777)
    compose.write(target.compose, _compose(config, target, state))
    config.paths.source.parent.mkdir(parents=True, exist_ok=True)
    config.paths.source.write_text(dump(config))
    marker = config.paths.backups / "operator-marker"
    marker.parent.mkdir(parents=True)
    marker.write_text("preserve\n")
    before = {
        config.paths.source: config.paths.source.read_bytes(),
        secrets.path(config, target, "password"): secrets.path(
            config, target, "password"
        ).read_bytes(),
        marker: marker.read_bytes(),
    }
    name = f"evdb-{target.project}-{target.role}-primary"
    auth = {"REDISCLI_AUTH": password}

    def health(*args, **kwargs):
        wait_exec(name, ["redis-cli", "PING"], env=auth)
        assert redis.health(name, password)

    monkeypatch.setattr(database, "health", health)

    try:
        database.start(config, target, state=state)
        docker_exec(name, ["redis-cli", "SET", "key", "value"], env=auth)
        docker_exec(name, ["redis-cli", "SAVE"], env=auth)
        database.stop(config, target, state=state)
        database.start(config, target, state=state)
        assert docker_exec(name, ["redis-cli", "GET", "key"], env=auth).stdout.strip() == "value"
        database.restart(config, target, state=state)
        assert docker_exec(name, ["redis-cli", "GET", "key"], env=auth).stdout.strip() == "value"
        assert "Ready to accept connections" in database.logs(config, target, lines=50)
    finally:
        command(
            ["docker", "compose", "-f", str(target.compose), "-p", target.compose_project, "down"],
            check=False,
        )

    assert all(path.read_bytes() == value for path, value in before.items())


def test_settings_transaction_health_gate_and_failed_recovery(config, tmp_path, monkeypatch):
    require_image(REDIS_IMAGE)
    project_id = f"settings-{uuid.uuid4().hex[:8]}-test-01"
    config = replace(
        config,
        host=replace(config.host, data_root=tmp_path / "settings-data"),
        projects=(),
    )
    state = resolve_state(config, resolver=lambda source: DIGEST)
    write_state(config, state)
    config.paths.source.parent.mkdir(parents=True, exist_ok=True)
    config.paths.source.write_text(dump(config))
    password = "disposable-settings-password"
    added = database.prepare_add(
        config,
        project_id,
        "kv",
        engine="redis",
        state=state,
        resolver=lambda source: DIGEST,
        generate=lambda: password,
    )
    added = replace(
        added,
        compose_data=_compose(added.after, added.database, added.after_state),
    )
    target = added.database
    name = f"evdb-{project_id}-kv-primary"
    auth = {"REDISCLI_AUTH": password}

    def healthy(*args, **kwargs):
        wait_exec(name, ["redis-cli", "PING"], env=auth)

    try:
        add_result = database.commit(added, check_health=healthy)
        assert add_result["status"] == "healthy"

        config = added.after
        target = added.database
        state = added.after_state
        change = database.prepare_configure(
            config,
            target.identity,
            {"mode": "cache"},
            state=state,
            resolver=lambda source: DIGEST,
        )
        change = replace(
            change,
            compose_data=_compose(change.after, change.database, change.after_state),
        )
        result = database.commit(
            change,
            backup_create=lambda *args, **kwargs: {"snapshot": "safety-snapshot"},
            check_health=healthy,
        )
        assert result["safety_snapshot"] == "safety-snapshot"
        docker_exec(name, ["redis-cli", "SET", "preserved", "yes"], env=auth)

        active = change.after
        active_target = change.database
        active_state = change.after_state
        prior_source = active.paths.source.read_bytes()
        prior_compose = active_target.compose.read_bytes()
        failed = database.prepare_configure(
            active,
            active_target.identity,
            {"mode": "durable"},
            state=active_state,
            resolver=lambda source: DIGEST,
        )
        failed = replace(
            failed,
            compose_data=_compose(failed.after, failed.database, failed.after_state),
        )
        checks = 0

        def fail_candidate(*args, **kwargs):
            nonlocal checks
            checks += 1
            healthy()
            if checks == 1:
                raise DatabaseError("candidate unhealthy")

        try:
            database.commit(failed, check_health=fail_candidate)
        except DatabaseError as exc:
            assert "prior service recovered" in str(exc)
        else:
            raise AssertionError("failed settings candidate unexpectedly succeeded")

        assert checks == 2
        assert active.paths.source.read_bytes() == prior_source
        assert active_target.compose.read_bytes() == prior_compose
        assert (
            docker_exec(name, ["redis-cli", "GET", "preserved"], env=auth).stdout.strip() == "yes"
        )
    finally:
        command(
            ["docker", "compose", "-f", str(target.compose), "-p", target.compose_project, "down"],
            check=False,
        )


def _database(config, tmp_path):
    project = config.projects[0]
    project_id = f"lifecycle-{uuid.uuid4().hex[:8]}-test-01"
    settings = replace(
        project.kv,
        engine="redis",
        image="redis:7.2.5",
        mode="durable",
        http=replace(project.kv.http, enabled=False),
    )
    project = replace(project, id=project_id, postgres=None, kv=settings)
    config = replace(
        config,
        host=replace(config.host, data_root=tmp_path / "data"),
        projects=(project,),
    )
    target = config.select(f"{project_id}/kv")
    state = resolve_state(config, resolver=lambda source: DIGEST)
    role = replace(state.roles[target.identity], installed=True)
    state = replace(state, roles={target.identity: role})
    write_state(config, state)
    return config, target, state


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
