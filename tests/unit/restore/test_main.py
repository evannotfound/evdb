import os
import shutil
import signal
from datetime import datetime, timezone

import pytest

from evanovation_db.errors import RestoreError
from evanovation_db.files import read_json, write_json
from evanovation_db.manifest import write as write_manifest
from evanovation_db.restore import main
from evanovation_db.restore.main import _identity, _safe, due


def test_restore_rejects_live_path(config):
    instance = config.get("postgres", "test-dev-01")

    with pytest.raises(RestoreError, match="overlaps live data"):
        _safe(config, instance.data)


def test_restore_rejects_nested_live_path(config):
    instance = config.get("kv", "test-dev-01")

    with pytest.raises(RestoreError, match="overlaps live data"):
        _safe(config, instance.data / "backup")


def test_restore_rejects_wrong_manifest_identity(config):
    instance = config.get("postgres", "test-dev-01")

    with pytest.raises(RestoreError, match="backup instance"):
        _identity(
            config,
            instance,
            {
                "host": config.host.id,
                "group": "postgres",
                "instance": "other",
                "engine": "postgres",
            },
        )


def test_restore_rejects_wrong_image(config):
    instance = config.get("postgres", "test-dev-01")

    with pytest.raises(RestoreError, match="backup image"):
        _identity(
            config,
            instance,
            {
                "host": config.host.id,
                "group": instance.group,
                "instance": instance.id,
                "engine": instance.engine,
                "image": "postgres:wrong@sha256:" + "0" * 64,
            },
        )


def test_restore_rejects_wrong_host(config):
    instance = config.get("postgres", "test-dev-01")

    with pytest.raises(RestoreError, match="backup host"):
        _identity(
            config,
            instance,
            {
                "host": "other-host",
                "group": instance.group,
                "instance": instance.id,
                "engine": instance.engine,
                "image": instance.target["image"],
            },
        )


def test_due_selects_instance_without_restore(config):
    first = config.instances[0]
    path = config.host.state_dir / "state" / first.group / f"{first.id}.json"
    write_json(path, {"restore": {"time": datetime.now(timezone.utc).isoformat()}})

    selected = due(config)

    assert (selected.group, selected.id) != (first.group, first.id)


def test_snapshot_restore_uses_private_staging(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    seen = {}

    def fake_snapshot(host, selected, snapshot, target):
        seen["snapshot"] = snapshot
        seen["staging"] = target.parent
        target.mkdir(parents=True)
        return target

    monkeypatch.setattr(main.restic, "restore", fake_snapshot)
    monkeypatch.setattr(main, "_restore", lambda current, selected, backup: {"ok": True})

    result = main.restore(config, instance, snapshot="snapshot-id")

    assert result == {"ok": True}
    assert seen["snapshot"] == "snapshot-id"
    assert not seen["staging"].exists()


def test_cleanup_failure_is_recorded_as_restore_failure(config, tmp_path, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    folder = tmp_path / "complete"
    folder.mkdir()
    write_manifest(
        folder,
        {
            "status": "complete",
            "host": config.host.id,
            "group": instance.group,
            "instance": instance.id,
            "engine": instance.engine,
            "image": instance.target["image"],
            "files": [],
        },
    )
    monkeypatch.setattr(main.postgres, "restore", lambda *args: {"ok": True})
    monkeypatch.setattr(
        main,
        "_cleanup",
        lambda *args: (_ for _ in ()).throw(RestoreError("container still running")),
    )

    with pytest.raises(RestoreError, match="container still running"):
        main._restore(config, instance, folder)

    state = config.host.state_dir / "state/postgres/test-dev-01.json"
    data = read_json(state)
    assert "container still running" in data["errors"]["restore-check"]["message"]
    assert "restore" not in data


def test_successful_restore_preserves_backup_failure(config, tmp_path, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    folder = _backup(config, instance, tmp_path / "complete")
    state = config.host.state_dir / "state/postgres/test-dev-01.json"
    write_json(state, {"error": {"command": "backup", "message": "upload failed"}})
    monkeypatch.setattr(main.postgres, "restore", lambda *args: {"ok": True})
    monkeypatch.setattr(main, "_cleanup", lambda *args: None)

    main._restore(config, instance, folder)

    assert read_json(state)["errors"] == {
        "backup": {"command": "backup", "message": "upload failed"}
    }


def test_signal_during_container_cleanup_is_deferred(config, tmp_path, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    folder = _backup(config, instance, tmp_path / "complete")
    monkeypatch.setattr(main.postgres, "restore", lambda *args: {"ok": True})

    def interrupt_cleanup(*args):
        os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(main, "_cleanup", interrupt_cleanup)

    with pytest.raises(RestoreError, match="interrupted by signal"):
        main._restore(config, instance, folder)

    state = config.host.state_dir / "state/postgres/test-dev-01.json"
    assert "interrupted by signal" in read_json(state)["errors"]["restore-check"]["message"]


def test_signal_during_snapshot_cleanup_is_deferred(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    seen = {}
    remove = shutil.rmtree

    def fake_snapshot(host, selected, snapshot, target):
        target.mkdir(parents=True)
        seen["staging"] = target.parent
        return target

    def interrupt_remove(path):
        os.kill(os.getpid(), signal.SIGTERM)
        remove(path)

    monkeypatch.setattr(main.restic, "restore", fake_snapshot)
    monkeypatch.setattr(main, "_restore", lambda *args: {"ok": True})
    monkeypatch.setattr(main.shutil, "rmtree", interrupt_remove)

    with pytest.raises(RestoreError, match="interrupted by signal"):
        main.restore(config, instance, snapshot="snapshot-id")

    assert not seen["staging"].exists()
    state = config.host.state_dir / "state/postgres/test-dev-01.json"
    assert "staging-cleanup" in read_json(state)["errors"]["restore-check"]["step"]


def test_snapshot_download_failure_is_recorded(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    monkeypatch.setattr(
        main.restic,
        "restore",
        lambda *args: (_ for _ in ()).throw(RestoreError("download failed")),
    )

    with pytest.raises(RestoreError, match="download failed"):
        main.restore(config, instance, snapshot="snapshot-id")

    state = config.host.state_dir / "state/postgres/test-dev-01.json"
    error = read_json(state)["errors"]["restore-check"]
    assert error["step"] == "snapshot"
    assert error["message"] == "download failed"


def test_missing_local_backup_is_recorded_as_restore_failure(config):
    instance = config.get("postgres", "test-dev-01")

    with pytest.raises(RestoreError, match="no local backup"):
        main.restore(config, instance)

    state = config.host.state_dir / "state/postgres/test-dev-01.json"
    error = read_json(state)["errors"]["restore-check"]
    assert error["step"] == "restore"


def test_wrong_host_snapshot_is_recorded(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")

    def fake_snapshot(host, selected, snapshot, target):
        target.mkdir(parents=True)
        write_manifest(
            target,
            {
                "status": "complete",
                "host": "other-host",
                "group": instance.group,
                "instance": instance.id,
                "engine": instance.engine,
                "image": instance.target["image"],
                "files": [],
            },
        )
        return target

    monkeypatch.setattr(main.restic, "restore", fake_snapshot)

    with pytest.raises(RestoreError, match="backup host"):
        main.restore(config, instance, snapshot="snapshot-id")

    state = config.host.state_dir / "state/postgres/test-dev-01.json"
    assert "backup host" in read_json(state)["errors"]["restore-check"]["message"]


def _backup(config, instance, folder):
    folder.mkdir()
    write_manifest(
        folder,
        {
            "status": "complete",
            "host": config.host.id,
            "group": instance.group,
            "instance": instance.id,
            "engine": instance.engine,
            "image": instance.target["image"],
            "files": [],
        },
    )
    return folder
