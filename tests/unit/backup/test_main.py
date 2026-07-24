from datetime import datetime, timezone
from pathlib import Path

import pytest

from evanovation_db import manifest
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
