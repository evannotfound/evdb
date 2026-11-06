from __future__ import annotations

import json
import shutil
import socket
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from evdb import backup, database, docker
from evdb.config import defaults, require_valid, write
from evdb.engines import dragonfly, kv, postgres, valkey
from evdb.errors import BackupError
from evdb.models import Config, Project, ProjectSecrets, RoleSecrets, http_port
from evdb.run import run


def test_postgres_and_private_pgbouncer_run_in_disposable_compose(config, tmp_path, monkeypatch):
    _require_docker()
    monkeypatch.setattr(backup, "_upload", lambda *args: "integration-snapshot")
    monkeypatch.setattr(backup, "_handoff", lambda *args: None)
    project = _project("postgres")
    password = 'pool password "quoted" \\ slash'
    settings = defaults("postgres", config.host.data_roots[0])
    selected = _config(
        config,
        tmp_path,
        (
            Project(
                project,
                postgres=replace(
                    settings,
                    username="app_user",
                    database="app_db",
                    pgbouncer=replace(settings.pgbouncer, pool_size=1, reserve_size=1),
                ),
            ),
        ),
        (ProjectSecrets(project, postgres=RoleSecrets(password)),),
    )
    target = selected.select(f"{project}/postgres")

    with _network(monkeypatch) as network:
        del network
        try:
            database.render(selected, target)
            docker.up(
                target.compose,
                target.compose_project,
                timeout=600,
                secrets=(password,),
            )
            database.health(selected, target, timeout=120)
            result = docker.exec(
                target.service("pgbouncer"),
                [
                    "psql",
                    "-X",
                    "-A",
                    "-t",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-h",
                    "127.0.0.1",
                    "-p",
                    "5432",
                    "-U",
                    "app_user",
                    "-d",
                    "app_db",
                    "-c",
                    "SELECT 1",
                ],
                env={"PGPASSWORD": password},
                secrets=(password,),
                timeout=60,
            )
            assert result.out.strip() == "1"
            result = docker.exec(
                target.service("pgbouncer"),
                [
                    "psql",
                    "-XAt",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    "SELECT pg_backend_pid()",
                    "-c",
                    "\\! psql -XAt -v ON_ERROR_STOP=1 -c 'SELECT pg_backend_pid()'",
                    "-c",
                    "SELECT pg_backend_pid()",
                ],
                env={
                    "PGHOST": "127.0.0.1",
                    "PGUSER": "app_user",
                    "PGDATABASE": "app_db",
                    "PGPASSWORD": password,
                },
                secrets=(password,),
                timeout=30,
            )
            pids = result.out.splitlines()
            assert len(pids) == 3 and len(set(pids)) == 1, result.out
            result = docker.exec(
                target.service("primary"),
                [
                    "psql",
                    "-XAt",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "app_user",
                    "-d",
                    "app_db",
                    "-c",
                    "BEGIN; CREATE TEMP TABLE session_test AS SELECT 42 AS value; COMMIT;",
                    "-c",
                    "SELECT value FROM session_test",
                ],
                env={"PGPASSWORD": password},
                secrets=(password,),
                timeout=30,
            )
            assert result.out.splitlines()[-1] == "42"
            created = backup.create(selected, target)
            manifest = backup.manifest_check(created["folder"])
            assert manifest["format"] == "postgres-custom-v1"
            assert manifest["checks"] == ["size", "sha256", "postgres"]
            assert {item["name"] for item in manifest["files"]} >= {"globals.sql"}
        finally:
            _clean(target, "/var/lib/postgresql/data")


def test_kv_engines_http_are_authenticated_and_isolated(config, tmp_path, monkeypatch):
    _require_docker()
    monkeypatch.setattr(backup, "_upload", lambda *args: "integration-snapshot")
    monkeypatch.setattr(backup, "_handoff", lambda *args: None)
    used = set()
    projects = []
    secrets = []
    credentials = {}
    for engine in ("redis", "dragonfly", "valkey"):
        project = _http_project(engine, used)
        password = f"local-{engine}-password"
        token = f"local-{engine}-token"
        projects.append(Project(project, kv=defaults("kv", config.host.data_roots[0], engine)))
        secrets.append(ProjectSecrets(project, kv=RoleSecrets(password, token)))
        credentials[engine] = (password, token)
    selected = _config(config, tmp_path, tuple(projects), tuple(secrets))
    targets = tuple(selected.databases)

    with _network(monkeypatch):
        try:
            for target in targets:
                database.render(selected, target)
                docker.up(
                    target.compose,
                    target.compose_project,
                    timeout=600,
                    secrets=credentials[target.engine],
                )
            for target in targets:
                database.health(selected, target, timeout=120)
                password, token = credentials[target.engine]
                assert not kv.health(target.service("primary"), "", engine=target.engine)
                assert not kv.health(target.service("primary"), "wrong", engine=target.engine)
                status, body = _request(target.http_port, ["PING"], token)
                assert (status, body) == (200, {"result": "PONG"})
                assert _request(target.http_port, ["SET", "blocked", "value"])[0] in {
                    400,
                    401,
                    403,
                }
                assert _request(target.http_port, ["SET", "scope", target.engine], token) == (
                    200,
                    {"result": "OK"},
                )
                assert (
                    kv.text(
                        target.service("primary"), password, ["GET", "scope"], engine=target.engine
                    )
                    == target.engine
                )
                created = backup.create(selected, target)
                manifest = backup.manifest_check(created["folder"])
                assert manifest["checks"] == ["size", "sha256", target.engine]
                assert manifest["files"]
                if target.engine == "dragonfly":
                    assert not dragonfly._sources(
                        target.service("primary"), f"evdb-{manifest['backup']}"
                    )
                inspected = run(["docker", "inspect", target.service("http")], timeout=30)
                service = json.loads(inspected.out)[0]["NetworkSettings"]["Ports"]["80/tcp"]
                assert service == [{"HostIp": "127.0.0.1", "HostPort": str(target.http_port)}]
            for target in targets:
                assert (
                    kv.text(
                        target.service("primary"),
                        credentials[target.engine][0],
                        ["GET", "scope"],
                        engine=target.engine,
                    )
                    == target.engine
                )
                assert (
                    kv.text(
                        target.service("primary"),
                        credentials[target.engine][0],
                        ["GET", "blocked"],
                        engine=target.engine,
                    )
                    == ""
                )
            assert len({target.http_port for target in targets}) == 3
        finally:
            for target in targets:
                _clean(target, "/data")


@pytest.mark.parametrize("mode", ["durable", "cache"])
def test_valkey_persistence_memory_and_checked_rdb_recovery(config, tmp_path, monkeypatch, mode):
    _require_docker()
    monkeypatch.setattr(backup, "_upload", lambda *args: "integration-snapshot")
    monkeypatch.setattr(backup, "_handoff", lambda *args: None)
    project = _project("valkey")
    settings = defaults("kv", config.host.data_roots[0], "valkey")
    settings = replace(settings, mode=mode, http=replace(settings.http, enabled=False))
    password = 'local valkey "password" \\ slash'
    selected = _config(
        config,
        tmp_path,
        (Project(project, kv=settings),),
        (ProjectSecrets(project, kv=RoleSecrets(password)),),
    )
    write(selected)
    target = selected.select(f"{project}/kv")
    primary = target.service("primary")
    restored = f"{primary}-restore"

    def text(*args):
        return kv.text(primary, password, list(args), engine="valkey")

    with _network(monkeypatch):
        try:
            database.render(selected, target)
            assert (target.generated / "valkey.conf").stat().st_mode & 0o777 == 0o640
            docker.up(target.compose, target.compose_project, timeout=600, secrets=(password,))
            database.health(selected, target, timeout=120)
            aof = "yes" if mode == "durable" else "no"
            assert text("CONFIG", "GET", "appendonly") == f"appendonly\n{aof}"
            assert text("CONFIG", "GET", "save") == "save"
            assert text("CONFIG", "GET", "appendfsync") == "appendfsync\neverysec"
            policy = "noeviction" if mode == "durable" else "allkeys-lru"
            assert text("CONFIG", "GET", "maxmemory-policy") == f"maxmemory-policy\n{policy}"
            assert text("CONFIG", "GET", "maxmemory") == "maxmemory\n268435456"
            assert valkey.info(target)["version"] == "9.1.3"

            if mode == "cache":
                with pytest.raises(BackupError, match="disabled"):
                    backup.create(selected, target)
                assert not (target.data / "appendonlydir").exists()
            else:
                # WAITAOF covers previous writes on this same connection, not a new CLI call.
                result = run(
                    [
                        "docker",
                        "exec",
                        "-i",
                        "--env",
                        "VALKEYCLI_AUTH",
                        primary,
                        "valkey-cli",
                        "--raw",
                    ],
                    input="SET durable survived\nLPUSH queue job-1\nWAITAOF 1 0 10000\n",
                    env={"VALKEYCLI_AUTH": password},
                    secrets=(password,),
                    timeout=30,
                )
                assert result.out.splitlines() == ["OK", "1", "1", "0"]
                assert not (target.data / "dump.rdb").exists()
                run(["docker", "kill", "--signal", "KILL", primary], timeout=30)
                stopped = run(["docker", "inspect", "--format", "{{.State.ExitCode}}", primary])
                assert stopped.out.strip() == "137"
                run(["docker", "start", primary], timeout=30)
                database.health(selected, target, timeout=120)
                assert text("GET", "durable") == "survived"
                assert text("LRANGE", "queue", "0", "-1") == "job-1"
                assert not (target.data / "dump.rdb").exists()

            selected = database.configure(selected, target, {"memory": "384mb"})
            target = selected.select(target.identity)
            assert text("CONFIG", "GET", "maxmemory") == "maxmemory\n402653184"
            if mode == "durable":
                assert text("GET", "durable") == "survived"
                created = backup.create(selected, target)
                manifest = backup.manifest_check(created["folder"])
                assert manifest["format"] == "valkey-rdb-v1"
                assert manifest["version"] == "9.1.3"
                folder = tmp_path / "reload"
                folder.mkdir()
                shutil.copyfile(Path(created["folder"]) / "dump.rdb", folder / "dump.rdb")
                assert {path.name for path in folder.iterdir()} == {"dump.rdb"}
                docker.start(
                    target.image,
                    restored,
                    [
                        "/usr/local/bin/valkey-server",
                        "--dir",
                        "/data",
                        "--appendonly",
                        "no",
                        "--save",
                        "",
                    ],
                    mounts=[(folder, "/data", True)],
                )
                deadline = time.monotonic() + 30
                while not kv.health(restored, "", engine="valkey"):
                    assert time.monotonic() < deadline, "RDB reload did not become healthy"
                    time.sleep(0.2)
                assert kv.text(restored, "", ["GET", "durable"], engine="valkey") == "survived"
                assert (
                    kv.text(restored, "", ["LRANGE", "queue", "0", "-1"], engine="valkey")
                    == "job-1"
                )
        finally:
            docker.remove(restored)
            _clean(target, "/data")


def test_postgres_logical_major_upgrade_from_16_to_18(config, tmp_path, monkeypatch):
    _require_docker()
    monkeypatch.setattr(backup, "_upload", lambda *args: "integration-snapshot")
    monkeypatch.setattr(backup, "_handoff", lambda *args: None)
    project = _project("upgrade")
    data_root = tmp_path / "database-volume"
    base = replace(config, host=replace(config.host, data_roots=(data_root,)))
    selected = _config(
        base,
        tmp_path,
        (Project(project, postgres=defaults("postgres", data_root)),),
        (ProjectSecrets(project, postgres=RoleSecrets("upgrade-password")),),
    )
    write(selected)
    target = selected.select(f"{project}/postgres")

    with _network(monkeypatch):
        final = None
        try:
            database.render(selected, target)
            docker.up(
                target.compose, target.compose_project, timeout=600, secrets=("upgrade-password",)
            )
            database.health(selected, target, timeout=120)
            docker.exec(
                target.service("primary"),
                [
                    "psql",
                    "-X",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    "default",
                    "-d",
                    "postgres",
                    "-c",
                    "CREATE TABLE preserved(value text); INSERT INTO preserved VALUES ('yes'); "
                    "ALTER DATABASE postgres SET statement_timeout TO '7s'",
                ],
                timeout=60,
            )
            docker.exec(
                target.service("primary"),
                ["chmod", "-R", "a+rwX", "/var/lib/postgresql/data"],
                timeout=30,
            )

            final = database.configure(selected, target, {"postgres_version": 18})
            upgraded = final.select(target.identity)
            database.health(final, upgraded, timeout=120)
            result = docker.exec(
                upgraded.service("primary"),
                [
                    "psql",
                    "-X",
                    "-A",
                    "-t",
                    "-U",
                    "default",
                    "-d",
                    "postgres",
                    "-c",
                    "SELECT value FROM preserved",
                ],
                timeout=60,
            )
            assert upgraded.image == "postgres:18"
            assert result.out.strip() == "yes"
            setting = docker.exec(
                upgraded.service("primary"),
                [
                    "psql",
                    "-X",
                    "-A",
                    "-t",
                    "-U",
                    "default",
                    "-d",
                    "postgres",
                    "-c",
                    "SHOW statement_timeout",
                ],
                timeout=30,
            )
            assert setting.out.strip() == "7s"
            version = docker.exec(
                upgraded.service("primary"),
                ["cat", "/var/lib/postgresql/18/docker/PG_VERSION"],
                timeout=30,
            )
            assert version.out.strip() == "18"
        finally:
            cleanup = (final or selected).select(target.identity)
            _clean(cleanup, postgres.data_target(cleanup.image))


def test_fresh_postgres_18_prepares_parent_mount(config, tmp_path, monkeypatch):
    _require_docker()
    project = _project("postgres18")
    data_root = tmp_path / "database-volume"
    base = replace(
        config,
        host=replace(config.host, data_roots=(data_root,)),
        paths=replace(config.paths, state=tmp_path / "state"),
        projects=(),
        secrets=replace(config.secrets, projects=()),
    )
    data_root.parent.mkdir(parents=True, exist_ok=True)
    write(base)

    with _network(monkeypatch):
        created = None
        try:
            created = database.add(base, project, "postgres", postgres_version=18)
            target = created.select(f"{project}/postgres")
            database.health(created, target, timeout=120)
            version = docker.exec(
                target.service("primary"),
                ["cat", "/var/lib/postgresql/18/docker/PG_VERSION"],
                timeout=30,
            )
            assert version.out.strip() == "18"
        finally:
            if created is not None:
                target = created.select(f"{project}/postgres")
                _clean(target, postgres.data_target(target.image))


def _config(base, tmp_path, projects, secrets) -> Config:
    selected = replace(
        base,
        paths=replace(base.paths, state=tmp_path / "state"),
        projects=projects,
        secrets=replace(base.secrets, projects=secrets),
    )
    for root in selected.host.data_roots:
        root.parent.mkdir(parents=True, exist_ok=True)
    require_valid(selected)
    return selected


def _project(kind: str) -> str:
    return f"{kind}-{uuid.uuid4().hex[:8]}-test-01"


def _http_project(kind: str, used: set[int]) -> str:
    for _attempt in range(100):
        project = _project(kind)
        port = http_port(project)
        if port not in used and _port_available(port):
            used.add(port)
            return project
    pytest.skip("no derived HTTP loopback port is available")


def _port_available(port: int) -> bool:
    current = socket.socket()
    try:
        current.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        current.close()
    return True


class _network:
    def __init__(self, monkeypatch):
        self.name = f"evdb-it-{uuid.uuid4().hex[:12]}"
        self.monkeypatch = monkeypatch

    def __enter__(self):
        self.monkeypatch.setattr(docker, "NETWORK", self.name)
        run(["docker", "network", "create", self.name], timeout=60)
        return self.name

    def __exit__(self, *_error):
        run(["docker", "network", "rm", self.name], timeout=60, check=False)


def _clean(target, data_path: str) -> None:
    docker.exec(
        target.service("primary"),
        ["chmod", "-R", "a+rwX", data_path],
        timeout=30,
        check=False,
    )
    run(
        docker.compose_command(target.compose, target.compose_project, "down", "--volumes"),
        timeout=120,
        check=False,
    )


def _request(port: int, body: list[str], token: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/",
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        data = exc.read()
        return exc.code, json.loads(data) if data else {}


def _require_docker() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker is unavailable")
    result = run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=30, check=False)
    if result.code:
        pytest.skip(f"Docker daemon is unavailable: {result.err.strip() or result.out.strip()}")
