import pytest

from evanovation_db.backup import redis
from evanovation_db.errors import BackupError


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
