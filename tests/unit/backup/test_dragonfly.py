import pytest

from evanovation_db.backup import dragonfly
from evanovation_db.errors import BackupError
from evanovation_db.run import Result


def test_dragonfly_uses_unique_rdb_name(config, tmp_path, monkeypatch):
    instance = config.get("kv", "test-dev-01")
    calls = []
    monkeypatch.setenv("EVANOVATION_DB_SECRET_KV_TEST_DEV_01_PASSWORD", "secret")

    def fake_command(selected, password, args, **kwargs):
        calls.append(args)
        return "OK"

    def fake_copy(source, target, **kwargs):
        target.write_bytes(b"rdb")

    monkeypatch.setattr(dragonfly.kv, "command", fake_command)
    monkeypatch.setattr(dragonfly.kv, "facts", lambda *args: {"version": "1.34.1", "keys": 1})
    monkeypatch.setattr(dragonfly.docker, "copy", fake_copy)
    monkeypatch.setattr(dragonfly.docker, "exec", _absent_then_removed)

    result = dragonfly.backup(config.host, instance, tmp_path, "20260724")

    assert calls == [["SAVE", "RDB", "evdb-20260724"]]
    assert (tmp_path / "dump.rdb").read_bytes() == b"rdb"
    assert result["files"] == ["dump.rdb"]


def test_dragonfly_rejects_existing_source(config, tmp_path, monkeypatch):
    instance = config.get("kv", "test-dev-01")
    monkeypatch.setenv("EVANOVATION_DB_SECRET_KV_TEST_DEV_01_PASSWORD", "secret")
    monkeypatch.setattr(
        dragonfly.docker,
        "exec",
        lambda *args, **kwargs: Result(("stat",), 0, "exists", ""),
    )

    with pytest.raises(BackupError, match="already exists"):
        dragonfly.backup(config.host, instance, tmp_path, "collision")


def test_dragonfly_failed_save_still_cleans_source(config, tmp_path, monkeypatch):
    instance = config.get("kv", "test-dev-01")
    calls = []
    monkeypatch.setenv("EVANOVATION_DB_SECRET_KV_TEST_DEV_01_PASSWORD", "secret")

    def fake_exec(container, args, **kwargs):
        calls.append(args)
        return Result(tuple(args), 1 if args[0] == "stat" else 0, "", "missing")

    monkeypatch.setattr(dragonfly.docker, "exec", fake_exec)
    monkeypatch.setattr(
        dragonfly.kv,
        "command",
        lambda *args, **kwargs: (_ for _ in ()).throw(BackupError("save failed")),
    )

    with pytest.raises(BackupError, match="save failed"):
        dragonfly.backup(config.host, instance, tmp_path, "failed")

    assert calls[-1][0] == "rm"


def test_dragonfly_empty_copy_fails_and_cleans(config, tmp_path, monkeypatch):
    instance = config.get("kv", "test-dev-01")
    calls = []
    monkeypatch.setenv("EVANOVATION_DB_SECRET_KV_TEST_DEV_01_PASSWORD", "secret")

    def fake_exec(container, args, **kwargs):
        calls.append(args)
        return Result(tuple(args), 1 if args[0] == "stat" else 0, "", "missing")

    monkeypatch.setattr(dragonfly.docker, "exec", fake_exec)
    monkeypatch.setattr(dragonfly.kv, "command", lambda *args, **kwargs: "OK")
    monkeypatch.setattr(dragonfly.docker, "copy", lambda source, target, **kwargs: target.touch())

    with pytest.raises(BackupError, match="missing or empty"):
        dragonfly.backup(config.host, instance, tmp_path, "empty")

    assert calls[-1][0] == "rm"


def test_dragonfly_copy_failure_keeps_original_error(config, tmp_path, monkeypatch):
    instance = config.get("kv", "test-dev-01")
    calls = []
    monkeypatch.setenv("EVANOVATION_DB_SECRET_KV_TEST_DEV_01_PASSWORD", "secret")

    def fake_exec(container, args, **kwargs):
        calls.append(args)
        return Result(tuple(args), 1 if args[0] == "stat" else 0, "", "missing")

    monkeypatch.setattr(dragonfly.docker, "exec", fake_exec)
    monkeypatch.setattr(dragonfly.kv, "command", lambda *args, **kwargs: "OK")
    monkeypatch.setattr(
        dragonfly.docker,
        "copy",
        lambda *args, **kwargs: (_ for _ in ()).throw(BackupError("copy failed")),
    )

    with pytest.raises(BackupError, match="copy failed"):
        dragonfly.backup(config.host, instance, tmp_path, "copy")

    assert calls[-1][0] == "rm"


def test_dragonfly_cleanup_failure_fails_backup(config, tmp_path, monkeypatch):
    instance = config.get("kv", "test-dev-01")
    monkeypatch.setenv("EVANOVATION_DB_SECRET_KV_TEST_DEV_01_PASSWORD", "secret")

    def fake_exec(container, args, **kwargs):
        return Result(tuple(args), 1, "", "failed")

    monkeypatch.setattr(dragonfly.docker, "exec", fake_exec)
    monkeypatch.setattr(dragonfly.kv, "command", lambda *args, **kwargs: "OK")
    monkeypatch.setattr(dragonfly.kv, "facts", lambda *args: {"version": "1.34.1"})
    monkeypatch.setattr(
        dragonfly.docker,
        "copy",
        lambda source, target, **kwargs: target.write_bytes(b"rdb"),
    )

    with pytest.raises(BackupError, match="cleanup failed"):
        dragonfly.backup(config.host, instance, tmp_path, "cleanup")


def _absent_then_removed(container, args, **kwargs):
    return Result(tuple(args), 1 if args[0] == "stat" else 0, "", "missing")
