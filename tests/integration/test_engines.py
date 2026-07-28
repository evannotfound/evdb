from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

from evdb import backup, compose, database, restore, secrets
from evdb.config import resolve_state, write_state
from evdb.engines import dragonfly, kv
from tests.fixtures.containers import (
    DRAGONFLY_IMAGE,
    PGBOUNCER_IMAGE,
    POSTGRES_IMAGE,
    REDIS_IMAGE,
    command,
    container,
    docker_exec,
    network,
    require_image,
    wait_exec,
)

DIGEST = "sha256:" + "a" * 64


def test_postgres_backup_and_restore_multiple_databases(config):
    require_image(POSTGRES_IMAGE)
    config, target = _postgres_config(config)
    state = _ready(config, target, POSTGRES_IMAGE)
    name = _container_name(target)

    with container(
        POSTGRES_IMAGE,
        name,
        env={
            "POSTGRES_USER": "default",
            "POSTGRES_PASSWORD": "local-postgres-integration",
            "POSTGRES_DB": "postgres",
        },
        memory="2g",
    ):
        wait_exec(name, ["pg_isready", "-U", "default", "-d", "postgres"])
        docker_exec(
            name,
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "default", "-d", "postgres"],
            input_text="CREATE ROLE reporting LOGIN PASSWORD 'local-role-password';\n",
        )
        docker_exec(name, ["createdb", "-U", "default", "app"])
        docker_exec(name, ["createdb", "-U", "default", "odd database"])
        docker_exec(
            name,
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "default", "-d", "app"],
            input_text=(
                "CREATE SCHEMA inventory AUTHORIZATION reporting;\n"
                "CREATE TABLE inventory.items (id integer PRIMARY KEY, name text NOT NULL);\n"
                "INSERT INTO inventory.items VALUES (1, 'alpha'), (2, 'beta');\n"
            ),
        )
        docker_exec(
            name,
            [
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "default",
                "-d",
                "odd database",
            ],
            input_text=(
                "CREATE TABLE notes (id integer PRIMARY KEY, body text NOT NULL);\n"
                "INSERT INTO notes VALUES (1, 'space-name-database');\n"
            ),
        )
        created = backup.create(config, target, upload=False, state=state)

    folder = Path(created["folder"])
    record = backup.manifest_check(folder)
    checked = restore.verify(config, target, folder, state=state)

    assert set(record["facts"]["databases"]) == {"postgres", "app", "odd database"}
    assert (folder / "databases/odd%20database.dump").stat().st_size > 0
    assert "ROLE reporting" in (folder / "globals.sql").read_text()
    assert "SCRAM-SHA-256" in (folder / "globals.sql").read_text()
    assert set(checked["result"]["databases"]) == {"postgres", "app", "odd database"}
    assert checked["result"]["objects"]["app"] >= 1
    assert checked["result"]["objects"]["odd database"] >= 1


def test_postgres_pgbouncer_uses_private_generated_files(config, tmp_path):
    require_image(POSTGRES_IMAGE)
    require_image(PGBOUNCER_IMAGE)
    config, target, state = _postgres_pgbouncer_config(config)
    password = 'pool password "quoted" \\ slash'
    secrets.write(secrets.render(config, target, secrets.Credentials(password)))
    config.paths.role_config(target.project, target.role).mkdir(parents=True, exist_ok=True)
    pgbouncer_ini = config.paths.role_config(target.project, target.role) / "pgbouncer.ini"
    pgbouncer_ini.write_text(compose.pool_config(target))
    pgbouncer_ini.chmod(0o640)
    target.data.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = compose.database(config, target, state)
    path = tmp_path / "compose.yaml"

    with network() as network_name:
        data["networks"][compose.NETWORK]["name"] = network_name
        compose.write(path, data)
        try:
            command(compose.command(path, target.compose_project, "up", "-d"), timeout=300)
            primary = f"evdb-{target.project}-{target.role}-primary"
            pooler = f"evdb-{target.project}-{target.role}-pgbouncer"
            wait_exec(primary, ["pg_isready", "-U", "default", "-d", "postgres"])
            wait_exec(
                pooler,
                [
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-p",
                    "5432",
                    "-U",
                    "default",
                    "-d",
                    "postgres",
                ],
            )
            result = docker_exec(
                pooler,
                [
                    "psql",
                    "-X",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-h",
                    "127.0.0.1",
                    "-p",
                    "5432",
                    "-U",
                    "default",
                    "-d",
                    "postgres",
                    "-c",
                    "SELECT 1",
                ],
                env={"PGPASSWORD": password},
                timeout=60,
            )
            assert "(1 row)" in result.stdout
            database.health(config, target, state=state, timeout=60)
        except BaseException as exc:
            logs = command(
                compose.command(path, target.compose_project, "logs", "--no-color"),
                check=False,
            )
            raise AssertionError(logs.stdout or logs.stderr) from exc
        finally:
            command(compose.command(path, target.compose_project, "down", "--volumes"), check=False)


def test_redis_backup_and_restore_types_databases_and_ttl(config):
    require_image(REDIS_IMAGE)
    config, target = _kv_config(config, "redis", REDIS_IMAGE)
    state = _ready(config, target, REDIS_IMAGE)
    password = "local-redis-integration"
    secrets.ensure(config, target, generate=lambda: password)
    name = _container_name(target)
    auth = {"REDISCLI_AUTH": password}

    with container(
        REDIS_IMAGE,
        name,
        ["/usr/local/bin/redis-server", "/run/secrets/redis.conf"],
        mounts=[(secrets.path(config, target, "redis.conf"), "/run/secrets/redis.conf", True)],
    ):
        wait_exec(name, ["redis-cli", "--raw", "PING"], env=auth)
        kv.text(name, password, ["SET", "message", "stale"])
        kv.text(name, password, ["SAVE"])
        kv.text(name, password, ["SET", "message", "fresh"])
        kv.text(name, password, ["SADD", "colors", "red", "green", "blue"])
        kv.text(name, password, ["-n", "1", "HSET", "user", "name", "Ada", "id", "7"])
        kv.text(name, password, ["-n", "2", "SET", "expires", "still-here"])
        kv.text(name, password, ["-n", "2", "PEXPIRE", "expires", "180000"])
        created = backup.create(config, target, upload=False, state=state)

    record = backup.manifest_check(created["folder"])
    checked = restore.verify(config, target, created["folder"], state=state)

    assert record["facts"]["databases"] == {"0": 2, "1": 1, "2": 1}
    assert record["facts"]["keys"] == 4
    assert {sample["type"] for sample in record["facts"]["samples"]} == {
        "string",
        "set",
        "hash",
    }
    assert any(sample["ttl_ms"] > 0 for sample in record["facts"]["samples"])
    assert checked["result"]["databases"] == {"0": 2, "1": 1, "2": 1}


def test_dragonfly_uses_one_native_snapshot_generation_and_restores_it(
    config, tmp_path, monkeypatch
):
    require_image(DRAGONFLY_IMAGE)
    config, target = _kv_config(config, "dragonfly", DRAGONFLY_IMAGE)
    state = _ready(config, target, DRAGONFLY_IMAGE)
    password = "local-dragonfly-integration"
    secrets.ensure(config, target, generate=lambda: password)
    name = _container_name(target)
    auth = {"REDISCLI_AUTH": password}
    stale_rdb = tmp_path / "stale-default.rdb"
    stale_summary = tmp_path / "stale-summary.dfs"
    stale_shard = tmp_path / "stale-0000.dfs"
    stale_rdb.write_bytes(b"stale-default-rdb")
    stale_summary.write_bytes(b"stale-summary")
    stale_shard.write_bytes(b"stale-shard")
    copied = []
    real_copy = dragonfly.docker.copy

    def record_copy(source, target_path, **kwargs):
        copied.append(source)
        return real_copy(source, target_path, **kwargs)

    monkeypatch.setattr(dragonfly.docker, "copy", record_copy)

    with container(
        DRAGONFLY_IMAGE,
        name,
        [
            "/usr/local/bin/dragonfly",
            "--logtostderr",
            "--flagfile=/run/secrets/dragonfly.flags",
        ],
        mounts=[
            (secrets.path(config, target, "dragonfly.flags"), "/run/secrets/dragonfly.flags", True)
        ],
        memory="3g",
    ):
        wait_exec(name, ["redis-cli", "PING"], env=auth)
        kv.text(name, password, ["SET", "message", "dragonfly"])
        kv.text(name, password, ["SADD", "colors", "cyan", "magenta"])
        kv.text(name, password, ["-n", "1", "HSET", "user", "name", "Grace"])
        kv.text(name, password, ["SET", "expires", "temporary"])
        kv.text(name, password, ["PEXPIRE", "expires", "180000"])
        command(["docker", "cp", str(stale_rdb), f"{name}:/data/dump.rdb"])
        command(["docker", "cp", str(stale_summary), f"{name}:/data/stale-summary.dfs"])
        command(["docker", "cp", str(stale_shard), f"{name}:/data/stale-0000.dfs"])
        created = backup.create(config, target, upload=False, state=state)

        assert len(copied) == 2
        sources = {item.split(":", 1)[1] for item in copied}
        assert all(item.startswith("/data/evdb-") and item.endswith(".dfs") for item in sources)
        assert any(item.endswith("-summary.dfs") for item in sources)
        assert any(item.endswith("-0000.dfs") for item in sources)
        assert all(
            docker_exec(name, ["stat", item], check=False).returncode != 0 for item in sources
        )
        assert docker_exec(name, ["stat", "/data/stale-summary.dfs"]).returncode == 0
        assert docker_exec(name, ["stat", "/data/stale-0000.dfs"]).returncode == 0

    record = backup.manifest_check(created["folder"])
    checked = restore.verify(config, target, created["folder"], state=state)

    files = {item["name"] for item in record["files"]}
    assert record["format"] == "dragonfly-dfs-v1"
    assert files == {
        f"{record['facts']['snapshot_base']}-summary.dfs",
        f"{record['facts']['snapshot_base']}-0000.dfs",
    }
    assert record["facts"]["databases"] == {"0": 3, "1": 1}
    assert record["facts"]["keys"] == 4
    assert checked["result"]["databases"] == {"0": 3, "1": 1}
    assert "1.34.1" in record["version"]


def _postgres_config(config):
    project = replace(config.projects[0], id=_project_id("postgres"), kv=None)
    selected = replace(config, projects=(project,))
    return selected, selected.select(f"{project.id}/postgres")


def _postgres_pgbouncer_config(config):
    source = config.projects[0].postgres
    settings = replace(
        source,
        image=POSTGRES_IMAGE.split("@", 1)[0],
        pgbouncer=replace(
            source.pgbouncer,
            enabled=True,
            image=PGBOUNCER_IMAGE.split("@", 1)[0],
        ),
    )
    project = replace(config.projects[0], id=_project_id("pgbouncer"), postgres=settings, kv=None)
    selected = replace(config, projects=(project,))
    target = selected.select(f"{project.id}/postgres")
    digests = {
        target.settings.image: POSTGRES_IMAGE.split("@", 1)[1],
        target.settings.pgbouncer.image: PGBOUNCER_IMAGE.split("@", 1)[1],
    }
    state = resolve_state(selected, resolver=lambda source: digests.get(source, DIGEST))
    role = replace(state.roles[target.identity], installed=True)
    state = replace(state, roles={**state.roles, target.identity: role})
    write_state(selected, state)
    return selected, target, state


def _kv_config(config, engine, image):
    project = config.projects[0]
    settings = replace(
        project.kv,
        engine=engine,
        image=image.split("@", 1)[0],
        mode="durable",
        http=replace(project.kv.http, enabled=False),
        memory="256mb" if engine == "dragonfly" else None,
        threads=1 if engine == "dragonfly" else None,
    )
    project = replace(project, id=_project_id(engine), postgres=None, kv=settings)
    selected = replace(config, projects=(project,))
    return selected, selected.select(f"{project.id}/kv")


def _ready(config, target, image):
    digest = image.split("@", 1)[1]
    state = resolve_state(
        config,
        resolver=lambda source: digest if source == target.image else DIGEST,
    )
    role = replace(state.roles[target.identity], installed=True)
    state = replace(state, roles={**state.roles, target.identity: role})
    write_state(config, state)
    return state


def _project_id(kind):
    return f"{kind}-{uuid.uuid4().hex[:8]}-test-01"


def _container_name(target):
    return f"evdb-{target.project}-{target.role}-primary"
