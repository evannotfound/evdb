from pathlib import Path

import pytest

from evanovation_db import manifest
from evanovation_db.backup import main
from evanovation_db.errors import ResticError


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
    assert "offline" in state.read_text()


def test_backup_all_keeps_running(config, monkeypatch):
    first = config.get("postgres", "test-dev-01")
    second = config.get("kv", "test-dev-01")

    def fake_backup(current, instance):
        if instance.group == "postgres":
            raise RuntimeError("broken")
        return Path("/ok")

    monkeypatch.setattr(main, "backup", fake_backup)

    result = main.backup_all(config, [first, second])

    assert result["postgres/test-dev-01"].startswith("error:")
    assert result["kv/test-dev-01"] == "/ok"
