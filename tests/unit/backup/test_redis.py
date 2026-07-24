import pytest

from evanovation_db.backup import redis
from evanovation_db.errors import BackupError, CommandError


def test_wait_accepts_new_successful_save(config, monkeypatch):
    instance = config.get("kv", "xai-server-prod-01")
    values = iter(
        [
            "rdb_bgsave_in_progress:1\nrdb_last_bgsave_status:ok\nrdb_last_save_time:1",
            "rdb_bgsave_in_progress:0\nrdb_last_bgsave_status:ok\nrdb_last_save_time:2",
        ]
    )
    monkeypatch.setattr(redis.kv, "command", lambda *args, **kwargs: next(values))
    monkeypatch.setattr(redis.time, "sleep", lambda value: None)

    redis._wait(instance, "secret", 1, timeout=1)


def test_wait_rejects_failed_save(config, monkeypatch):
    instance = config.get("kv", "xai-server-prod-01")
    monkeypatch.setattr(
        redis.kv,
        "command",
        lambda *args, **kwargs: (
            "rdb_bgsave_in_progress:0\nrdb_last_bgsave_status:err\nrdb_last_save_time:1"
        ),
    )

    with pytest.raises(BackupError, match="failed"):
        redis._wait(instance, "secret", 1, timeout=1)


def test_wait_accepts_fast_save(config, monkeypatch):
    instance = config.get("kv", "xai-server-prod-01")
    monkeypatch.setattr(
        redis.kv,
        "command",
        lambda *args, **kwargs: (
            "rdb_bgsave_in_progress:0\nrdb_last_bgsave_status:ok\nrdb_last_save_time:2"
        ),
    )

    redis._wait(instance, "secret", 1, timeout=1)


def test_wait_rejects_unchanged_save_time(config, monkeypatch):
    instance = config.get("kv", "xai-server-prod-01")
    times = iter([0.0, 2.0])
    monkeypatch.setattr(redis.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        redis.kv,
        "command",
        lambda *args, **kwargs: (
            "rdb_bgsave_in_progress:0\nrdb_last_bgsave_status:ok\nrdb_last_save_time:1"
        ),
    )

    with pytest.raises(BackupError, match="timed out"):
        redis._wait(instance, "secret", 1, timeout=1)


def test_pre_save_clock_wait_is_bounded(monkeypatch):
    times = iter([0.0, 2.0])
    monkeypatch.setattr(redis.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(redis.time, "time", lambda: 10)
    monkeypatch.setattr(redis.time, "sleep", lambda value: None)

    with pytest.raises(BackupError, match="clock did not advance"):
        redis._wait_for_new_second(10, timeout=1)


def test_redis_checker_failure_rejects_rdb(config, tmp_path, monkeypatch):
    instance = config.get("kv", "xai-server-prod-01")
    path = tmp_path / "dump.rdb"
    path.write_bytes(b"damaged")
    monkeypatch.setattr(
        redis,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(CommandError("invalid RDB")),
    )

    with pytest.raises(CommandError, match="invalid RDB"):
        redis._check(instance, path)
