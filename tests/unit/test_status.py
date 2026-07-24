from datetime import datetime, timedelta, timezone

from evanovation_db.files import write_json
from evanovation_db.status import get


def test_status_marks_old_backup_and_restore(config):
    now = datetime.now(timezone.utc)
    instance = config.get("postgres", "test-dev-01")
    path = config.host.state_dir / "state/postgres/test-dev-01.json"
    write_json(
        path,
        {
            "backup": {
                "upload": {"time": (now - timedelta(hours=27)).isoformat(), "snapshot": "x"}
            },
            "restore": {"time": (now - timedelta(days=31)).isoformat()},
        },
    )

    rows, failed = get(config, now=now)
    row = next(
        item for item in rows if item["group"] == "postgres" and item["instance"] == instance.id
    )

    assert failed
    assert row["backup_stale"]
    assert row["restore_stale"]
