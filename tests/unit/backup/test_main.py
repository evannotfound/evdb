from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evanovation_db import manifest, status
from evanovation_db.backup import main
from evanovation_db.errors import BackupError, ResticError
from evanovation_db.files import read_json, write_json


def test_backup_finishes_uploads_and_writes_state(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")

    def fake_engine(host, selected, folder, run_id):
        assert host == config.host
        assert selected == instance
        assert run_id
        (folder / "data").write_bytes(b"backup")
        return {"version": "16", "files": ["data"], "databases": ["postgres"]}

    monkeypatch.setitem(main.ENGINES, "postgres", fake_engine)
    monkeypatch.setattr(main.restic, "upload", lambda host, selected, folder: "snapshot-id")

    folder = main.backup(config, instance)

    data = manifest.check(folder)
    assert data["upload"]["snapshot"] == "snapshot-id"
    assert (config.host.state_dir / "state/postgres/test-dev-01.json").is_file()


def test_failed_upload_keeps_completed_backup(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")

    def fake_engine(host, selected, folder, run_id):
        (folder / "data").write_bytes(b"backup")
        return {"version": "16", "files": ["data"]}

    def fail(*args):
        raise ResticError("offline")

    monkeypatch.setitem(main.ENGINES, "postgres", fake_engine)
    monkeypatch.setattr(main.restic, "upload", fail)

    with pytest.raises(ResticError, match="offline"):
        main.backup(config, instance)

    folders = list((config.host.backup_dir / "postgres/test-dev-01").iterdir())
    assert len(folders) == 1
    assert not folders[0].name.endswith(".partial")
    state = config.host.state_dir / "state/postgres/test-dev-01.json"
    state_text = state.read_text()
    assert "offline" in state_text
    assert '"backup"' in state_text


def test_failed_new_upload_preserves_recent_success_and_latest_local_backup(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    state = config.host.state_dir / "state/postgres/test-dev-01.json"
    uploaded = datetime.now(timezone.utc) - timedelta(hours=1)
    previous_upload = {
        "ok": True,
        "time": uploaded.isoformat(),
        "snapshot": "snapshot-previous",
    }
    write_json(
        state,
        {
            "backup": {
                "finished": (uploaded - timedelta(minutes=1)).isoformat(),
                "upload": previous_upload,
            }
        },
    )

    def fake_engine(host, selected, folder, run_id):
        (folder / "data").write_bytes(b"new backup")
        return {"version": "16.9", "files": ["data"]}

    monkeypatch.setitem(main.ENGINES, "postgres", fake_engine)
    monkeypatch.setattr(
        main.restic,
        "upload",
        lambda *args: (_ for _ in ()).throw(
            ResticError("offline rediss://default:private-password@example.test")
        ),
    )

    with pytest.raises(ResticError, match="offline"):
        main.backup(config, instance)

    data = read_json(state)
    assert data["backup"]["finished"] != (uploaded - timedelta(minutes=1)).isoformat()
    assert data["backup"]["upload"] == {"ok": False}
    assert data["upload"] == previous_upload
    assert data["errors"]["backup"]["step"] == "upload"
    assert "private-password" not in data["errors"]["backup"]["message"]
    facts = status._operation_facts(config, instance)
    assessment = status._backup_assessment(config, True, facts, datetime.now(timezone.utc))
    assert assessment["state"] == "fresh"
    assert assessment["snapshot"] == "snapshot-previous"
    assert assessment["upload_ok"] is True


def test_backup_all_keeps_running(config, monkeypatch):
    first = config.get("postgres", "test-dev-01")
    second = config.get("kv", "test-dev-01")

    def fake_engine(host, instance, folder, run_id):
        if instance == first:
            raise RuntimeError("broken")
        (folder / "data").write_bytes(b"backup")
        return {"version": "test", "files": ["data"]}

    monkeypatch.setitem(main.ENGINES, "postgres", fake_engine)
    monkeypatch.setitem(main.ENGINES, "dragonfly", fake_engine)
    monkeypatch.setattr(
        main.restic,
        "upload",
        lambda host, instance, folder: f"snapshot-{instance.group}",
    )

    result = main.backup_all(config, [first, second])

    assert result["postgres/test-dev-01"].startswith("error:")
    second_folder = Path(result["kv/test-dev-01"])
    assert second_folder.is_dir()
    assert manifest.check(second_folder)["upload"]["snapshot"] == "snapshot-kv"


def test_interrupted_backup_stays_partial(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")

    def interrupt(host, selected, folder, run_id):
        (folder / "data").write_bytes(b"partial")
        raise KeyboardInterrupt

    monkeypatch.setitem(main.ENGINES, "postgres", interrupt)

    with pytest.raises(KeyboardInterrupt):
        main.backup(config, instance, upload=False)

    folders = list((config.host.backup_dir / "postgres/test-dev-01").iterdir())
    assert len(folders) == 1
    assert folders[0].name.endswith(".partial")
    with pytest.raises(BackupError, match="missing backup.json"):
        manifest.check(folders[0])


def test_stale_partial_is_not_reused(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    class Clock:
        @classmethod
        def now(cls, tz):
            assert tz == timezone.utc
            return fixed

    root = config.host.backup_dir / "postgres/test-dev-01"
    stale = root / "20260102T030405000000Z.partial"
    stale.mkdir(parents=True)
    (stale / "old").write_bytes(b"stale")
    monkeypatch.setattr(main, "datetime", Clock)

    with pytest.raises(BackupError, match="partial backup already exists"):
        main.backup(config, instance, upload=False)

    assert (stale / "old").read_bytes() == b"stale"


def test_low_space_stops_before_engine(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    called = False

    def engine(*args):
        nonlocal called
        called = True

    monkeypatch.setitem(main.ENGINES, "postgres", engine)
    monkeypatch.setattr(
        main,
        "require_space",
        lambda path, minimum: (_ for _ in ()).throw(BackupError("low space")),
    )

    with pytest.raises(BackupError, match="low space"):
        main.backup(config, instance)

    assert not called


def test_successful_backup_preserves_restore_failure(config):
    instance = config.get("postgres", "test-dev-01")
    state = config.host.state_dir / "state/postgres/test-dev-01.json"
    write_json(
        state,
        {"error": {"command": "restore-check", "message": "restore failed"}},
    )

    main._state(config.host.state_dir, instance, {"status": "complete"})

    assert read_json(state)["errors"] == {
        "restore-check": {"command": "restore-check", "message": "restore failed"}
    }


def test_history_merges_local_and_remote_and_keeps_remote_only(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    local_id = "20260724T100000000000Z"
    local = _history_backup(config, instance, local_id, "2026-07-24T10:00:00+00:00", "snap-local")
    write_json(
        config.host.state_dir / "state/postgres/test-dev-01.json",
        {
            "restore": {
                "ok": True,
                "time": "2026-07-24T12:00:00+00:00",
                "backup": local_id,
            }
        },
    )
    monkeypatch.setattr(
        main.restic,
        "snapshots",
        lambda host, selected: [
            {
                "id": "snap-local",
                "time": "2026-07-24T10:01:00+00:00",
                "paths": [str(local)],
            },
            {
                "id": "snap-remote",
                "time": "2026-07-25T10:00:00+00:00",
                "paths": [str(local.parent / "20260725T100000000000Z")],
            },
        ],
    )

    rows = main.history(config, instance)

    assert [row["snapshot"] for row in rows] == ["snap-remote", "snap-local"]
    assert rows[0]["source"] == "remote"
    assert rows[0]["local"] is False
    assert rows[1]["source"] == "local+remote"
    assert rows[1]["verification"]["state"] == "verified"


def test_history_attaches_failed_verification_to_exact_backup(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    backup_id = "20260725T100000000000Z"
    _history_backup(config, instance, backup_id, "2026-07-25T10:00:00+00:00", None)
    write_json(
        config.host.state_dir / "state/postgres/test-dev-01.json",
        {
            "errors": {
                "restore-check": {
                    "command": "restore-check",
                    "backup": backup_id,
                    "time": "2026-07-25T11:00:00+00:00",
                    "message": "integrity failed",
                }
            }
        },
    )
    monkeypatch.setattr(main.restic, "snapshots", lambda host, selected: [])

    row = main.history(config, instance)[0]

    assert row["verification"] == {
        "state": "failed",
        "time": "2026-07-25T11:00:00+00:00",
        "error": "integrity failed",
    }


def test_select_backup_defaults_latest_and_requires_exact_identity(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    rows = [
        {
            "backup": "new",
            "snapshot": "snapshot-new",
            "time": "2026-07-25T10:00:00+00:00",
        },
        {
            "backup": "old",
            "snapshot": "snapshot-old",
            "time": "2026-07-24T10:00:00+00:00",
        },
    ]
    monkeypatch.setattr(main, "history", lambda current, selected: rows)

    assert main.select_backup(config, instance, None)["backup"] == "new"
    assert main.select_backup(config, instance, "snapshot-old")["backup"] == "old"
    with pytest.raises(BackupError, match="does not exist"):
        main.select_backup(config, instance, "snapshot-other-database")
    with pytest.raises(BackupError, match="exact"):
        main.select_backup(config, instance, "../new")


def test_backup_all_skips_ineligible_database_and_redacts_url_password(config, monkeypatch):
    durable = next(item for item in config.instances if item.durable)
    cache = replace(durable, id="cache-dev-99", durable=False, backup={"enabled": False})
    monkeypatch.setattr(
        main,
        "backup",
        lambda current, selected: (_ for _ in ()).throw(
            BackupError("failed rediss://default:private-password@example.test")
        ),
    )

    result = main.backup_all(config, [durable, cache])

    assert list(result) == [f"{durable.group}/{durable.id}"]
    assert "private-password" not in result[f"{durable.group}/{durable.id}"]
    assert "<redacted>" in result[f"{durable.group}/{durable.id}"]


def _history_backup(config, instance, name, finished, snapshot):
    folder = config.host.backup_dir / instance.group / instance.id / name
    folder.mkdir(parents=True)
    (folder / "data").write_bytes(b"backup")
    upload = {"ok": bool(snapshot)}
    if snapshot:
        upload.update({"snapshot": snapshot, "time": finished})
    manifest.write(
        folder,
        {
            "status": "complete",
            "host": config.host.id,
            "group": instance.group,
            "instance": instance.id,
            "engine": instance.engine,
            "image": instance.image,
            "finished": finished,
            "files": manifest.files(folder, ["data"]),
            "upload": upload,
        },
    )
    return folder
