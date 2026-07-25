import pytest

from evanovation_db import manifest
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


def test_kv_candidate_uses_only_persistent_candidate_mount(config, tmp_path, monkeypatch):
    instance = config.get("kv", "xai-server-prod-01")
    folder = tmp_path / "backup"
    folder.mkdir()
    (folder / "dump.rdb").write_bytes(b"rdb")
    manifest.write(
        folder,
        {
            "status": "complete",
            "facts": {"databases": {}, "keys": 0, "samples": []},
            "files": manifest.files(folder, ["dump.rdb"]),
        },
    )
    candidate = tmp_path / "candidate"
    started = {}
    monkeypatch.setattr(kv, "_wait", lambda name: None)
    monkeypatch.setattr(
        kv.kv_backup,
        "facts",
        lambda selected, password, sample_limit=0: {
            "databases": {},
            "keys": 0,
            "samples": [],
        },
    )

    def start(image, name, *args, **kwargs):
        started.update({"image": image, "name": name, **kwargs})
        return name

    monkeypatch.setattr(kv.docker, "start", start)

    kv.restore(config.host, instance, folder, "candidate-container", candidate)

    assert started["image"] == instance.image
    assert started["network"] == "none"
    assert started["mounts"] == [(candidate, "/data", False)]
    assert instance.data not in {source for source, _, _ in started["mounts"]}
