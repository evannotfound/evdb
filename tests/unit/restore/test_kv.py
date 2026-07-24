import pytest

from evanovation_db.errors import RestoreError
from evanovation_db.restore import kv
from evanovation_db.run import Result


def test_restore_rejects_changed_sample():
    expected = [{"database": 0, "key": "x", "type": "string", "sha256": "a", "ttl_ms": -1}]
    current = [{"database": 0, "key": "x", "type": "string", "sha256": "b", "ttl_ms": -1}]

    with pytest.raises(RestoreError, match="sample differs"):
        kv._check_samples(current, expected)


def test_restore_rejects_lost_ttl():
    expected = [{"database": 0, "key": "x", "type": "string", "sha256": "a", "ttl_ms": 10}]
    current = [{"database": 0, "key": "x", "type": "string", "sha256": "a", "ttl_ms": -1}]

    with pytest.raises(RestoreError, match="TTL was lost"):
        kv._check_samples(current, expected)


def test_restore_startup_wait_times_out(monkeypatch):
    times = iter([0.0, 2.0])
    monkeypatch.setattr(kv.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        kv.docker,
        "exec",
        lambda *args, **kwargs: Result(("redis-cli",), 1, "", "not ready"),
    )

    with pytest.raises(RestoreError, match="did not become ready"):
        kv._wait("restore", timeout=1)
