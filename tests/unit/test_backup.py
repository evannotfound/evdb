import json
from contextlib import contextmanager
from dataclasses import replace

import pytest

from evanovation_db import backup, restic
from evanovation_db.config import resolve_state, write_state
from evanovation_db.errors import BackupError, ResticError

DIGEST = "sha256:" + "a" * 64


class Engine:
    @staticmethod
    def backup(config, database, folder, run_id, state):
        del config, database, run_id, state
        (folder / "data.bin").write_bytes(b"checked data")
        return {
            "format": "test-v1",
            "version": "16.1",
            "facts": {"objects": 1},
            "files": ["data.bin"],
        }


def _installed(config):
    state = resolve_state(config, resolver=lambda source: DIGEST)
    state = replace(
        state,
        roles={name: replace(role, installed=True) for name, role in state.roles.items()},
    )
    write_state(config, state)
    return state


def test_create_writes_checked_manifest_upload_and_project_role_path(config, monkeypatch):
    _installed(config)
    target = config.select("app-test-01/postgres")
    monkeypatch.setattr(backup, "_engine", lambda database: Engine)
    uploaded = {}

    def upload(_config, _target, folder):
        uploaded.update(backup.manifest_check(folder))
        return "snapshot-1"

    monkeypatch.setattr(restic, "upload", upload)

    result = backup.create(config, target, purpose="manual")
    folder = config.paths.role_backups(target.project, target.role) / result["backup"]
    record = backup.manifest_check(folder)

    assert folder.parent == config.paths.backups / "app-test-01/postgres"
    assert record["host"] == config.host.id
    assert record["project"] == "app-test-01"
    assert record["role"] == "postgres"
    assert record["engine"] == "postgres"
    assert record["backup"] == result["backup"]
    assert record["purpose"] == "manual"
    assert record["format"] == "test-v1"
    assert record["upload"]["snapshot"] == "snapshot-1"
    assert uploaded["backup"] == result["backup"]
    assert uploaded["upload"] == {"ok": True, "backup": result["backup"]}
    assert not list(folder.parent.glob(".*.upload.partial"))


def test_upload_failure_keeps_complete_local_backup_records_error_and_logs_redacted_identity(
    config, monkeypatch, capsys
):
    state = _installed(config)
    target = config.select("app-test-01/postgres")
    monkeypatch.setattr(backup, "_engine", lambda database: Engine)
    monkeypatch.setattr(
        restic,
        "upload",
        lambda *args: (_ for _ in ()).throw(ResticError("token=top-secret upload unavailable")),
    )

    with pytest.raises(ResticError, match="upload unavailable"):
        backup.create(config, target)

    folders = [path for path in config.paths.role_backups(target.project, target.role).iterdir()]
    assert len(folders) == 1
    assert not folders[0].name.endswith(".partial")
    record = backup.manifest_check(folders[0])
    assert record["upload"] == {"ok": False, "backup": record["backup"]}
    operations = backup.load_state(config).roles[target.identity].operations
    assert operations["errors"]["backup"]["step"] == "upload"
    assert state.roles[target.identity].operations == {}
    text = config.paths.machine_state.read_text()
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    failed = next(
        event
        for event in events
        if event["event"] == "backup_operation" and event["result"] == "failed"
    )
    assert failed["host"] == config.host.id
    assert (failed["project"], failed["role"], failed["engine"]) == (
        target.project,
        target.role,
        target.engine,
    )
    assert "top-secret" not in json.dumps(failed) + text


def test_cache_backup_is_rejected_before_engine_or_restic(config, monkeypatch):
    _installed(config)
    target = config.select("app-test-01/kv")
    monkeypatch.setattr(
        backup,
        "_engine",
        lambda database: (_ for _ in ()).throw(AssertionError("engine called")),
    )

    with pytest.raises(BackupError, match="cache database"):
        backup.create(config, target)


def test_manifest_rejects_tampering_and_unsafe_file_names(config, tmp_path):
    folder = tmp_path / "backup"
    folder.mkdir()
    (folder / "data").write_text("one")
    files = backup.manifest_files(folder, ["data"])
    record = {
        "status": "complete",
        "backup": folder.name,
        "host": config.host.id,
        "project": "app-test-01",
        "role": "postgres",
        "engine": "postgres",
        "source_image": "postgres:16",
        "image": f"postgres:16@{DIGEST}",
        "started": "2026-01-01T00:00:00+00:00",
        "finished": "2026-01-01T00:01:00+00:00",
        "version": "16.1",
        "format": "test-v1",
        "purpose": "manual",
        "facts": {},
        "files": files,
        "checks": ["size", "sha256", "postgres"],
        "upload": {"ok": False, "backup": folder.name},
    }
    backup.manifest_write(folder, record)
    assert backup.manifest_check(folder) == record

    (folder / "data").write_text("changed")
    with pytest.raises(BackupError, match="changed"):
        backup.manifest_check(folder)
    with pytest.raises(BackupError, match="unsafe"):
        backup.manifest_files(folder, ["../data"])


def test_history_merges_local_and_remote_and_rejects_cross_role(config, monkeypatch):
    _installed(config)
    target = config.select("app-test-01/postgres")
    monkeypatch.setattr(backup, "_engine", lambda database: Engine)
    monkeypatch.setattr(restic, "upload", lambda *args: "snapshot-1")
    created = backup.create(config, target)
    monkeypatch.setattr(
        restic,
        "snapshots",
        lambda *args: [
            {
                "id": "snapshot-1",
                "time": created["finished"],
                "tags": [
                    f"host:{config.host.id}",
                    "project:app-test-01",
                    "role:postgres",
                    f"backup:{created['backup']}",
                ],
            }
        ],
    )

    rows = backup.history(config, target)

    assert rows[0]["source"] == "local+remote"
    assert backup.select(config, target, "snapshot-1")["backup"] == created["backup"]
    with pytest.raises(BackupError):
        backup.select(config, target, "other-role-snapshot")


def test_backup_test_attaches_result_to_exact_identities(config, monkeypatch):
    _installed(config)
    target = config.select("app-test-01/postgres")
    monkeypatch.setattr(backup, "_engine", lambda database: Engine)
    monkeypatch.setattr(restic, "upload", lambda *args: "snapshot-1")
    monkeypatch.setattr(restic, "snapshots", lambda *args: [])
    created = backup.create(config, target)
    entered = []

    @contextmanager
    def operation(*args, **kwargs):
        entered.append((args, kwargs))
        yield

    monkeypatch.setattr(backup, "operation", operation)

    import evanovation_db.restore as restore

    monkeypatch.setattr(
        restore,
        "verify",
        lambda *args, **kwargs: {"result": {"objects": 1}},
    )
    result = backup.test(config, target, created["backup"])
    operations = backup.load_state(config).roles[target.identity].operations

    assert result["ok"]
    assert entered[0][0][1] == target
    assert entered[0][1]["timeout"] == config.host.timeouts["restore"]
    assert operations["verifications"][f"backup:{created['backup']}"]["ok"]


def test_due_uses_latest_successful_verification_not_latest_failed_attempt(config):
    second = replace(config.projects[0], id="other-test-01", kv=None)
    config = replace(config, projects=(config.projects[0], second))
    state = _installed(config)
    first_id = "app-test-01/postgres"
    second_id = "other-test-01/postgres"
    first = state.roles[first_id]
    second_role = state.roles[second_id]
    state = replace(
        state,
        roles={
            **state.roles,
            first_id: replace(
                first,
                operations={
                    "backup_test": {
                        "ok": False,
                        "time": "2026-07-20T00:00:00+00:00",
                    },
                    "verifications": {
                        "backup:first": {
                            "ok": True,
                            "time": "2026-01-01T00:00:00+00:00",
                        }
                    },
                },
            ),
            second_id: replace(
                second_role,
                operations={
                    "backup_test": {
                        "ok": True,
                        "time": "2026-06-01T00:00:00+00:00",
                    }
                },
            ),
        },
    )
    write_state(config, state)

    assert backup.due(config).identity == first_id


def test_materialize_cleans_fixed_staging_on_download_interrupt(config, monkeypatch):
    target = config.select("app-test-01/postgres")
    staging = config.paths.restores / "download-app-test-01-postgres"
    staging.mkdir(parents=True)
    (staging / "stale").write_text("old")
    selected = {
        "backup": "remote-backup",
        "snapshot": "snapshot-1",
        "local": False,
        "remote": True,
    }
    monkeypatch.setattr(
        restic,
        "restore",
        lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        backup.materialize(config, target, selected)

    assert not staging.exists()


def test_local_retention_keeps_two_uploaded_and_every_unuploaded(config, monkeypatch):
    _installed(config)
    target = config.select("app-test-01/postgres")
    monkeypatch.setattr(backup, "_engine", lambda database: Engine)
    snapshots = iter(["one", "two", "three"])
    monkeypatch.setattr(restic, "upload", lambda *args: next(snapshots))

    for _ in range(3):
        backup.create(config, target)

    folders = list(config.paths.role_backups(target.project, target.role).iterdir())
    assert len(folders) == 2


def test_manifest_and_state_are_secret_free(config, monkeypatch):
    _installed(config)
    target = config.select("app-test-01/postgres")
    monkeypatch.setattr(backup, "_engine", lambda database: Engine)
    monkeypatch.setattr(restic, "upload", lambda *args: "snapshot-1")
    result = backup.create(config, target)

    text = json.dumps(result) + config.paths.machine_state.read_text()
    assert "private-value" not in text
    assert "password" not in text.lower()
