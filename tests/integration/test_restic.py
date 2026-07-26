from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evanovation_db import backup, restic
from evanovation_db.errors import ResticError
from tests.fixtures.containers import require_binary

DIGEST = "sha256:" + "a" * 64


def test_local_restic_isolates_two_roles_in_one_project(config, tmp_path):
    shared = tmp_path / "shared-repository"
    config = _repos(config, postgres=str(shared), kv=str(shared))
    _credentials(config)
    require_binary("restic")
    restic.init(config, "postgres")
    postgres = config.select("app-test-01/postgres")
    kv = config.select("app-test-01/kv")
    pg_folder = _backup(config, postgres, tmp_path / "postgres-backup")
    kv_folder = _backup(config, kv, tmp_path / "kv-backup")

    pg_snapshot = restic.upload(config, postgres, pg_folder)
    kv_snapshot = restic.upload(config, kv, kv_folder)
    pg_rows = restic.snapshots(config, postgres)
    kv_rows = restic.snapshots(config, kv)
    restored = restic.restore(config, postgres, pg_snapshot, tmp_path / "restored")

    assert {item["id"] for item in pg_rows} == {pg_snapshot}
    assert {item["id"] for item in kv_rows} == {kv_snapshot}
    assert set(pg_rows[0]["tags"]) >= {
        f"host:{config.host.id}",
        "project:app-test-01",
        "role:postgres",
        "engine:postgres",
        f"backup:{pg_folder.name}",
    }
    assert backup.manifest_check(restored)["project"] == "app-test-01"
    with pytest.raises(ResticError, match="does not belong"):
        restic.select_snapshot(config, kv, pg_snapshot)


def test_local_restic_failure_keeps_completed_backup(config, tmp_path):
    require_binary("restic")
    config = _repos(config, postgres=str(tmp_path / "not-initialized"))
    _credentials(config)
    target = config.select("app-test-01/postgres")
    folder = _backup(config, target, tmp_path / "complete", uploaded=False)
    upload = tmp_path / "upload" / folder.name
    shutil.copytree(folder, upload)
    record = backup.manifest_read(upload)
    backup.manifest_write(upload, {**record, "upload": {"ok": True, "backup": folder.name}})

    with pytest.raises(ResticError, match="upload failed"):
        restic.upload(config, target, upload)

    assert backup.manifest_check(folder)["upload"] == {"ok": False, "backup": folder.name}
    assert not Path(config.host.backup.repos["postgres"]).exists()


def test_local_restic_retention_prune_and_subset_check(config, tmp_path):
    require_binary("restic")
    config = _repos(config, postgres=str(tmp_path / "repository"))
    config = replace(
        config,
        host=replace(
            config.host,
            backup=replace(
                config.host.backup,
                retention={"daily": 1, "weekly": 1, "monthly": 1, "data_parts": 2},
            ),
        ),
    )
    _credentials(config)
    restic.init(config, "postgres")
    target = config.select("app-test-01/postgres")
    folder = tmp_path / "history"
    folder.mkdir()
    (folder / "data").write_text("first")
    now = datetime.now(timezone.utc)
    for days in (100, 40, 8, 0):
        (folder / "data").write_text(str(days))
        restic._run(
            config,
            "postgres",
            [
                "backup",
                str(folder),
                "--time",
                (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S"),
                "--tag",
                f"host:{config.host.id}",
                "--tag",
                f"project:{target.project}",
                "--tag",
                f"role:{target.role}",
                "--tag",
                f"engine:{target.engine}",
                "--tag",
                f"backup:history-{days}",
            ],
        )

    before = restic.snapshots(config, target)
    restic.forget(config, target, dry_run=False)
    after = restic.snapshots(config, target)
    restic.prune(config, "postgres")
    result = restic.check(config, "postgres", part=1)

    assert len(before) == 4
    assert 0 < len(after) < len(before)
    assert result.code == 0


def test_integration_backup_fixture_matches_canonical_manifest(config, tmp_path):
    target = config.select("app-test-01/postgres")
    folder = _backup(config, target, tmp_path / "canonical", uploaded=False)

    record = backup.manifest_check(folder)

    assert record["backup"] == folder.name
    assert record["upload"] == {"ok": False, "backup": folder.name}


def _repos(config, **values):
    repos = {**config.host.backup.repos, **values}
    return replace(
        config,
        host=replace(config.host, backup=replace(config.host.backup, repos=repos)),
    )


def _credentials(config):
    config.paths.secrets.mkdir(parents=True, exist_ok=True)
    (config.paths.secrets / "restic-password").write_text("local-integration-password\n")
    (config.paths.secrets / "restic-password").chmod(0o600)
    config.paths.rclone.mkdir(parents=True, exist_ok=True)
    (config.paths.rclone / "rclone.conf").write_text("")


def _backup(config, target, folder, *, uploaded=True):
    folder.mkdir(mode=0o700)
    (folder / "data").write_bytes(b"local integration backup")
    backup.manifest_write(
        folder,
        {
            "status": "complete",
            "backup": folder.name,
            "host": config.host.id,
            "project": target.project,
            "role": target.role,
            "engine": target.engine,
            "source_image": target.image,
            "image": f"{target.image}@{DIGEST}",
            "started": "2026-01-01T00:00:00+00:00",
            "finished": "2026-01-01T00:01:00+00:00",
            "version": "1.0",
            "format": "integration-v1",
            "purpose": "manual",
            "facts": {},
            "files": backup.manifest_files(folder, ["data"]),
            "checks": ["size", "sha256", target.engine],
            "upload": {"ok": uploaded, "backup": folder.name},
        },
    )
    return folder
