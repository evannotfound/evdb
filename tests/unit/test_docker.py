from evanovation_db import docker
from evanovation_db.run import Result


def test_exec_builds_argument_array(monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Result(tuple(args), 0, "ok", "")

    monkeypatch.setattr(docker, "run", fake_run)

    docker.exec("db", ["redis-cli", "PING"], env={"REDISCLI_AUTH": "secret"}, secrets=["secret"])

    assert seen["args"] == [
        "docker",
        "exec",
        "--env",
        "REDISCLI_AUTH",
        "db",
        "redis-cli",
        "PING",
    ]
    assert seen["kwargs"]["env"] == {"REDISCLI_AUTH": "secret"}
    assert seen["kwargs"]["secrets"] == ["secret"]


def test_remove_is_idempotent(monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Result(tuple(args), 1, "", "missing")

    monkeypatch.setattr(docker, "run", fake_run)

    docker.remove("missing")

    assert seen["args"] == ["docker", "rm", "--force", "--volumes", "missing"]
    assert seen["kwargs"]["check"] is False
