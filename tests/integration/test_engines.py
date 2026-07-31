from __future__ import annotations

import json
import shutil
import socket
import urllib.error
import urllib.request
import uuid
from dataclasses import replace

import pytest

from evdb import backup, database, docker
from evdb.config import defaults, require_valid
from evdb.engines import dragonfly, kv
from evdb.models import Config, Project, ProjectSecrets, RoleSecrets, http_port
from evdb.run import run


def test_postgres_and_private_pgbouncer_run_in_disposable_compose(config, tmp_path, monkeypatch):
    _require_docker()
    monkeypatch.setattr(backup, "_upload", lambda *args: "integration-snapshot")
    monkeypatch.setattr(backup, "_handoff", lambda *args: None)
    project = _project("postgres")
    password = 'pool password "quoted" \\ slash'
    selected = _config(
        config,
        tmp_path,
        (Project(project, postgres=defaults("postgres")),),
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
                    "default",
                    "-d",
                    "postgres",
                    "-c",
                    "SELECT 1",
                ],
                env={"PGPASSWORD": password},
                secrets=(password,),
                timeout=60,
            )
            assert result.out.strip() == "1"
            created = backup.create(selected, target)
            manifest = backup.manifest_check(created["folder"])
            assert manifest["format"] == "postgres-custom-v1"
            assert manifest["checks"] == ["size", "sha256", "postgres"]
            assert {item["name"] for item in manifest["files"]} >= {"globals.sql"}
        finally:
            _clean(target, "/var/lib/postgresql/data")


def test_redis_and_dragonfly_http_are_authenticated_and_isolated(config, tmp_path, monkeypatch):
    _require_docker()
    monkeypatch.setattr(backup, "_upload", lambda *args: "integration-snapshot")
    monkeypatch.setattr(backup, "_handoff", lambda *args: None)
    used = set()
    projects = []
    secrets = []
    credentials = {}
    for engine in ("redis", "dragonfly"):
        project = _http_project(engine, used)
        password = f"local-{engine}-password"
        token = f"local-{engine}-token"
        projects.append(Project(project, kv=defaults("kv", engine)))
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
                    kv.text(target.service("primary"), password, ["GET", "scope"]) == target.engine
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
            assert len({target.http_port for target in targets}) == 2
        finally:
            for target in targets:
                _clean(target, "/data")


def _config(base, tmp_path, projects, secrets) -> Config:
    selected = replace(
        base,
        paths=replace(base.paths, state=tmp_path / "state"),
        projects=projects,
        secrets=replace(base.secrets, projects=secrets),
    )
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
    if socket.gethostname().split(".", 1)[0] == "montreal-01":
        pytest.skip("disposable Docker tests are forbidden on montreal-01")
    if shutil.which("docker") is None:
        pytest.skip("Docker is unavailable")
    result = run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=30, check=False)
    if result.code:
        pytest.skip(f"Docker daemon is unavailable: {result.err.strip() or result.out.strip()}")
