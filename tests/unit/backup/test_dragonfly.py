from evanovation_db.backup import dragonfly
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
    monkeypatch.setattr(
        dragonfly.docker,
        "exec",
        lambda *args, **kwargs: Result(("rm",), 0, "", ""),
    )

    result = dragonfly.backup(config.host, instance, tmp_path, "20260724")

    assert calls == [["SAVE", "RDB", "evdb-20260724"]]
    assert (tmp_path / "dump.rdb").read_bytes() == b"rdb"
    assert result["files"] == ["dump.rdb"]
