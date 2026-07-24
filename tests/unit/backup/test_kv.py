from evanovation_db.backup import kv
from evanovation_db.backup.kv import _canonical
from evanovation_db.run import Result


def test_hash_sample_preserves_field_value_pairs():
    first = _canonical("hash", "a\n1\nb\n2")
    reordered = _canonical("hash", "b\n2\na\n1")
    changed = _canonical("hash", "a\n2\nb\n1")

    assert first == reordered
    assert first != changed


def test_set_sample_ignores_member_order():
    assert _canonical("set", "a\nb") == _canonical("set", "b\na")


def test_redis_auth_is_environment_only(config, monkeypatch):
    instance = config.get("kv", "xai-server-prod-01")
    seen = {}

    def fake_exec(container, args, **kwargs):
        seen["args"] = args
        seen["env"] = kwargs["env"]
        seen["secrets"] = kwargs["secrets"]
        return Result(tuple(args), 0, "PONG\n", "")

    monkeypatch.setattr(kv.docker, "exec", fake_exec)

    assert kv.command(instance, "top-secret", ["PING"]) == "PONG"
    assert "top-secret" not in seen["args"]
    assert seen["env"] == {"REDISCLI_AUTH": "top-secret"}
    assert seen["secrets"] == ["top-secret"]
