from __future__ import annotations

import shutil
import sys
from dataclasses import replace
from pathlib import Path

from evanovation_db import manifest
from evanovation_db.backup import backup, kv
from evanovation_db.backup import dragonfly as dragonfly_backup
from evanovation_db.restore import kv as kv_restore
from evanovation_db.restore import postgres as postgres_restore

sys.path.insert(0, str(Path(__file__).parents[1]))

from fixtures.containers import (  # noqa: E402
    DRAGONFLY_IMAGE,
    POSTGRES_IMAGE,
    REDIS_IMAGE,
    command,
    container,
    docker_exec,
    remove,
    require_image,
    unique_name,
    wait_exec,
)


def test_postgres_backup_and_restore_multiple_databases(config, tmp_path):
    require_image(POSTGRES_IMAGE)
    source = unique_name("postgres")
    password = "local-postgres-integration"
    instance = replace(
        config.get("postgres", "test-dev-01"),
        container=source,
        data=tmp_path / "postgres-live",
    )

    with container(
        POSTGRES_IMAGE,
        source,
        env={
            "POSTGRES_USER": "default",
            "POSTGRES_PASSWORD": password,
            "POSTGRES_DB": "postgres",
        },
        memory="2g",
    ):
        wait_exec(source, ["pg_isready", "-U", "default", "-d", "postgres"])
        docker_exec(
            source,
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "default", "-d", "postgres"],
            input_text="CREATE ROLE reporting LOGIN PASSWORD 'local-role-password';\n",
        )
        docker_exec(source, ["createdb", "-U", "default", "app"])
        docker_exec(source, ["createdb", "-U", "default", "odd database"])
        docker_exec(
            source,
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "default", "-d", "app"],
            input_text=(
                "CREATE SCHEMA inventory AUTHORIZATION reporting;\n"
                "CREATE TABLE inventory.items (id integer PRIMARY KEY, name text NOT NULL);\n"
                "INSERT INTO inventory.items VALUES (1, 'alpha'), (2, 'beta');\n"
            ),
        )
        docker_exec(
            source,
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

        folder = backup(config, instance, upload=False)

    data = manifest.check(folder)
    assert set(data["facts"]["databases"]) == {"postgres", "app", "odd database"}
    assert (folder / "databases/odd%20database.dump").stat().st_size > 0
    globals_sql = (folder / "globals.sql").read_text()
    assert "ROLE reporting" in globals_sql
    assert "SCRAM-SHA-256" in globals_sql

    restored = unique_name("postgres-restore")
    try:
        result = postgres_restore.restore(config.host, instance, folder, restored)
        assert set(result["databases"]) == {"postgres", "app", "odd database"}
        items = docker_exec(
            restored,
            [
                "psql",
                "-X",
                "-A",
                "-t",
                "-U",
                "restore_admin",
                "-d",
                "app",
                "-c",
                "SELECT string_agg(name, ',' ORDER BY id) FROM inventory.items",
            ],
        )
        assert items.stdout.strip() == "alpha,beta"
        note = docker_exec(
            restored,
            [
                "psql",
                "-X",
                "-A",
                "-t",
                "-U",
                "restore_admin",
                "-d",
                "odd database",
                "-c",
                "SELECT body FROM notes WHERE id = 1",
            ],
        )
        assert note.stdout.strip() == "space-name-database"
        role = docker_exec(
            restored,
            [
                "psql",
                "-X",
                "-A",
                "-t",
                "-F",
                "|",
                "-U",
                "restore_admin",
                "-d",
                "postgres",
                "-c",
                (
                    "SELECT rolcanlogin, rolpassword LIKE 'SCRAM-SHA-256%' "
                    "FROM pg_authid WHERE rolname = 'reporting'"
                ),
            ],
        )
        assert role.stdout.strip() == "t|t"
    finally:
        remove(restored)


def test_redis_bgsave_backup_and_restore_types_databases_and_ttl(config, tmp_path, monkeypatch):
    require_image(REDIS_IMAGE)
    source = unique_name("redis")
    password = "local-redis-integration"
    redis_config = tmp_path / "redis.conf"
    redis_config.write_text(
        "\n".join(
            [
                "dir /data",
                "dbfilename dump.rdb",
                "appendonly no",
                'save ""',
                "protected-mode no",
                f"requirepass {password}",
                "",
            ]
        )
    )
    redis_config.chmod(0o644)
    instance = replace(
        config.get("kv", "xai-server-prod-01"),
        container=source,
        data=tmp_path / "redis-live",
    )
    secret_env = "EVANOVATION_DB_SECRET_KV_XAI_SERVER_PROD_01_PASSWORD"
    monkeypatch.setenv(secret_env, password)
    auth = {"REDISCLI_AUTH": password}

    with container(
        REDIS_IMAGE,
        source,
        ["redis-server", "/run/redis/redis.conf"],
        mounts=[(redis_config, "/run/redis/redis.conf", True)],
    ):
        wait_exec(source, ["redis-cli", "--raw", "PING"], env=auth)
        kv.command(instance, password, ["SET", "message", "stale"])
        kv.command(instance, password, ["SAVE"])
        kv.command(instance, password, ["SET", "message", "fresh"])
        kv.command(instance, password, ["SADD", "colors", "red", "green", "blue"])
        kv.command(instance, password, ["-n", "1", "HSET", "user", "name", "Ada", "id", "7"])
        kv.command(instance, password, ["-n", "2", "SET", "expires", "still-here"])
        kv.command(instance, password, ["-n", "2", "PEXPIRE", "expires", "180000"])

        folder = backup(config, instance, upload=False)

    data = manifest.check(folder)
    assert data["facts"]["databases"] == {"0": 2, "1": 1, "2": 1}
    assert data["facts"]["keys"] == 4
    assert {sample["type"] for sample in data["facts"]["samples"]} == {
        "string",
        "set",
        "hash",
    }
    assert any(sample["ttl_ms"] > 0 for sample in data["facts"]["samples"])

    restored = unique_name("redis-restore")
    try:
        result = kv_restore.restore(config.host, instance, folder, restored)
        assert result["databases"] == {"0": 2, "1": 1, "2": 1}
        assert _redis(restored, ["GET", "message"]) == "fresh"
        assert set(_redis(restored, ["SMEMBERS", "colors"]).splitlines()) == {
            "red",
            "green",
            "blue",
        }
        assert _redis(restored, ["-n", "1", "HGET", "user", "name"]) == "Ada"
        assert _redis(restored, ["-n", "1", "HGET", "user", "id"]) == "7"
        assert _redis(restored, ["-n", "2", "GET", "expires"]) == "still-here"
        ttl = int(_redis(restored, ["-n", "2", "PTTL", "expires"]))
        assert 0 < ttl <= 180000
    finally:
        _remove_restore(restored, folder)


def test_dragonfly_unique_rdb_ignores_stale_dump_and_dfs(config, tmp_path, monkeypatch):
    require_image(DRAGONFLY_IMAGE)
    source = unique_name("dragonfly")
    data_dir = tmp_path / "dragonfly-data"
    data_dir.mkdir(mode=0o700)
    instance = replace(
        config.get("kv", "test-dev-01"),
        container=source,
        data=data_dir,
    )
    stale_rdb = tmp_path / "stale-default.rdb"
    stale_dfs = tmp_path / "fresh-summary.dfs"
    stale_rdb.write_bytes(b"stale-default-rdb")
    stale_dfs.write_bytes(b"fresh-dfs-snapshot")
    monkeypatch.setenv("EVANOVATION_DB_SECRET_KV_TEST_DEV_01_PASSWORD", "")
    copied = []
    real_copy = dragonfly_backup.docker.copy

    def record_copy(source_path, target, **kwargs):
        copied.append(source_path)
        real_copy(source_path, target, **kwargs)

    monkeypatch.setattr(dragonfly_backup.docker, "copy", record_copy)

    with container(
        DRAGONFLY_IMAGE,
        source,
        [
            "dragonfly",
            "--dir=/data",
            "--dbfilename=dump",
            "--primary_port_http_enabled=false",
        ],
        memory="3g",
    ):
        wait_exec(source, ["redis-cli", "PING"])
        kv.command(instance, "", ["SET", "message", "dragonfly"])
        kv.command(instance, "", ["SADD", "colors", "cyan", "magenta"])
        kv.command(instance, "", ["-n", "1", "HSET", "user", "name", "Grace"])
        kv.command(instance, "", ["SET", "expires", "temporary"])
        kv.command(instance, "", ["PEXPIRE", "expires", "180000"])
        command(["docker", "cp", str(stale_rdb), f"{source}:/data/dump.rdb"])
        command(
            [
                "docker",
                "cp",
                str(stale_dfs),
                f"{source}:/data/dump-0001-summary.dfs",
            ]
        )

        folder = backup(config, instance, upload=False)

        assert len(copied) == 1
        source_path = copied[0].split(":", 1)[1]
        assert source_path.startswith("/data/evdb-")
        assert source_path.endswith(".rdb")
        assert docker_exec(source, ["stat", source_path], check=False).returncode != 0
        observed_rdb = tmp_path / "observed-stale.rdb"
        observed_dfs = tmp_path / "observed-summary.dfs"
        command(["docker", "cp", f"{source}:/data/dump.rdb", str(observed_rdb)])
        command(
            [
                "docker",
                "cp",
                f"{source}:/data/dump-0001-summary.dfs",
                str(observed_dfs),
            ]
        )
        assert observed_rdb.read_bytes() == b"stale-default-rdb"
        assert observed_dfs.read_bytes() == b"fresh-dfs-snapshot"

    data = manifest.check(folder)
    assert (folder / "dump.rdb").read_bytes() != b"stale-default-rdb"
    assert data["facts"]["databases"] == {"0": 3, "1": 1}
    assert data["facts"]["keys"] == 4

    restored = unique_name("dragonfly-restore")
    try:
        result = kv_restore.restore(config.host, instance, folder, restored)
        assert result["databases"] == {"0": 3, "1": 1}
        assert _redis(restored, ["GET", "message"]) == "dragonfly"
        assert set(_redis(restored, ["SMEMBERS", "colors"]).splitlines()) == {
            "cyan",
            "magenta",
        }
        assert _redis(restored, ["-n", "1", "HGET", "user", "name"]) == "Grace"
        assert _redis(restored, ["GET", "expires"]) == "temporary"
        assert int(_redis(restored, ["PTTL", "expires"])) > 0
    finally:
        _remove_restore(restored, folder)

    assert "1.34.1" in data["version"]


def _redis(name: str, args: list[str]) -> str:
    return docker_exec(name, ["redis-cli", "--raw", *args]).stdout.strip()


def _remove_restore(name: str, folder: Path) -> None:
    work = folder.parent / f".{name}-data"
    docker_exec(name, ["chmod", "-R", "a+rwX", "/data"], check=False)
    remove(name)
    shutil.rmtree(work, ignore_errors=True)
