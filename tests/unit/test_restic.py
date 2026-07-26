import json
from dataclasses import replace
from datetime import date

import pytest

from evanovation_db import backup, restic
from evanovation_db.config import resolve_state, write_state
from evanovation_db.errors import ResticError
from evanovation_db.run import Result

DIGEST = "sha256:" + "a" * 64


def _ready(config):
    state = resolve_state(config, resolver=lambda source: DIGEST)
    state = replace(
        state,
        roles={name: replace(role, installed=True) for name, role in state.roles.items()},
    )
    write_state(config, state)
    config.paths.secrets.mkdir(parents=True)
    (config.paths.secrets / "restic-password").write_text("private\n")
    (config.paths.secrets / "restic-password").chmod(0o600)
    config.paths.rclone.mkdir(parents=True)
    (config.paths.rclone / "rclone.conf").write_text("token=private-token\n")


def test_snapshots_use_stable_project_role_tags(config, monkeypatch):
    _ready(config)
    target = config.select("app-test-01/postgres")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return Result(
            tuple(args),
            0,
            json.dumps(
                [
                    {
                        "id": "snap",
                        "tags": [
                            f"host:{config.host.id}",
                            "project:app-test-01",
                            "role:postgres",
                            "engine:postgres",
                            "backup:backup-1",
                        ],
                    }
                ]
            ),
            "",
        )

    monkeypatch.setattr(restic, "run", fake_run)

    assert restic.snapshots(config, target)[0]["id"] == "snap"
    command = calls[0][0]
    assert "host:test-01,project:app-test-01,role:postgres" in command
    assert "engine:postgres" not in command[-1]


def test_exact_snapshot_rejects_cross_role_tags(config, monkeypatch):
    _ready(config)
    target = config.select("app-test-01/postgres")
    output = json.dumps(
        [
            {
                "id": "snap",
                "tags": ["host:test-01", "project:app-test-01", "role:kv"],
            }
        ]
    )
    monkeypatch.setattr(
        restic,
        "run",
        lambda args, **kwargs: Result(tuple(args), 0, output, ""),
    )

    with pytest.raises(ResticError, match="does not belong"):
        restic.select_snapshot(config, target, "snap")


def test_upload_parser_requires_success_and_final_summary():
    assert (
        restic._snapshot(
            Result(("restic",), 0, '{"message_type":"summary","snapshot_id":"abc"}\n', "")
        )
        == "abc"
    )
    with pytest.raises(ResticError, match="final summary"):
        restic._snapshot(Result(("restic",), 0, '{"message_type":"status"}\n', ""))


def test_upload_tags_checked_manifest_identity_not_staging_name(
    config, tmp_path, monkeypatch, capsys
):
    _ready(config)
    target = config.select("app-test-01/postgres")
    folder = tmp_path / "temporary-upload-view"
    folder.mkdir()
    (folder / "data.bin").write_bytes(b"checked")
    backup.manifest_write(
        folder,
        {
            "status": "complete",
            "backup": "backup-identity",
            "host": config.host.id,
            "project": target.project,
            "role": target.role,
            "engine": target.engine,
            "source_image": target.image,
            "image": f"postgres:16@{DIGEST}",
            "started": "2026-01-01T00:00:00+00:00",
            "finished": "2026-01-01T00:01:00+00:00",
            "version": "16.1",
            "format": "postgres-custom-v1",
            "purpose": "manual",
            "facts": {},
            "files": backup.manifest_files(folder, ["data.bin"]),
            "checks": ["size", "sha256", "postgres"],
            "upload": {"ok": True, "backup": "backup-identity"},
        },
    )
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return Result(
            tuple(args),
            0,
            '{"message_type":"summary","snapshot_id":"snapshot-1"}\n',
            "",
        )

    monkeypatch.setattr(restic, "run", fake_run)

    assert restic.upload(config, target, folder) == "snapshot-1"
    assert "backup:backup-identity" in calls[0]
    assert "purpose:manual" in calls[0]
    assert "backup:temporary-upload-view" not in calls[0]
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    complete = next(
        event
        for event in events
        if event["event"] == "restic_operation" and event["result"] == "success"
    )
    assert (complete["project"], complete["role"], complete["engine"]) == (
        target.project,
        target.role,
        target.engine,
    )
    assert complete["backup"] == "backup-identity"


def test_retention_groups_only_filtered_role_not_mutable_metadata(config, monkeypatch):
    _ready(config)
    target = config.select("app-test-01/postgres")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        output = '{"version":1}' if args[-2:] == ["cat", "config"] else ""
        return Result(tuple(args), 0, output, "")

    monkeypatch.setattr(restic, "run", fake_run)
    restic.forget(config, target, dry_run=True)
    command = calls[-1]

    assert command[command.index("--group-by") + 1] == "host"
    tags = command[command.index("--tag") + 1]
    assert tags == "host:test-01,project:app-test-01,role:postgres"
    assert "engine:" not in tags and "backup:" not in tags

    restic.approve_retention(config, target)
    restic.forget(config, target, dry_run=False)
    assert "--dry-run" not in calls[-1]


def test_retention_requires_matching_reviewed_dry_run(config, monkeypatch):
    _ready(config)
    target = config.select("app-test-01/postgres")

    def fake_run(args, **kwargs):
        output = '{"version":1}' if args[-2:] == ["cat", "config"] else ""
        return Result(tuple(args), 0, output, "")

    monkeypatch.setattr(restic, "run", fake_run)

    with pytest.raises(ResticError, match="reviewed dry run"):
        restic.forget(config, target, dry_run=False)


def test_upload_rejects_manifest_for_another_role(config, tmp_path, monkeypatch):
    _ready(config)
    target = config.select("app-test-01/postgres")
    folder = tmp_path / "wrong-role"
    folder.mkdir()
    (folder / "data.bin").write_bytes(b"checked")
    record = {
        "status": "complete",
        "backup": "wrong-role",
        "host": config.host.id,
        "project": target.project,
        "role": "kv",
        "engine": "redis",
        "source_image": "redis:7.2.5",
        "image": f"redis:7.2.5@{DIGEST}",
        "started": "2026-01-01T00:00:00+00:00",
        "finished": "2026-01-01T00:01:00+00:00",
        "version": "7.2.5",
        "format": "redis-rdb-v1",
        "purpose": "manual",
        "facts": {},
        "files": backup.manifest_files(folder, ["data.bin"]),
        "checks": ["size", "sha256", "redis"],
        "upload": {"ok": True, "backup": "wrong-role"},
    }
    backup.manifest_write(folder, record)
    monkeypatch.setattr(
        restic,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Restic started")),
    )

    with pytest.raises(ResticError, match="backup role does not match"):
        restic.upload(config, target, folder)


def test_repository_check_rotation_is_stable(config):
    first = restic.check_part(config, today=date(2026, 1, 1))
    second = restic.check_part(config, today=date(2026, 1, 8))
    total = config.host.backup.retention["data_parts"]

    assert second == first % total + 1


def test_restore_interrupt_removes_only_new_target(config, tmp_path, monkeypatch):
    _ready(config)
    target = config.select("app-test-01/postgres")
    destination = tmp_path / "download"
    monkeypatch.setattr(
        restic,
        "select_snapshot",
        lambda *args: {
            "id": "snapshot-1",
            "tags": [
                f"host:{config.host.id}",
                "project:app-test-01",
                "role:postgres",
                "engine:postgres",
                "backup:backup-1",
            ],
        },
    )
    monkeypatch.setattr(restic, "_require_v1", lambda *args: None)
    monkeypatch.setattr(
        restic,
        "_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        restic.restore(config, target, "snapshot-1", destination)

    assert not destination.exists()
