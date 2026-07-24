from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evanovation_db import manifest, restic
from evanovation_db.errors import ResticError

sys.path.insert(0, str(Path(__file__).parents[1]))

from fixtures.containers import command, require_binary  # noqa: E402


def test_local_restic_upload_returns_confirmed_snapshot_json(config, tmp_path):
    _init(config.host, "kv")
    instance = config.get("kv", "test-dev-01")
    folder = _backup(tmp_path / "complete")

    snapshot_id = restic.upload(config.host, instance, folder)
    snapshots = restic.snapshots(config.host, "kv", instance.id)
    restored = restic.restore(config.host, instance, snapshot_id, tmp_path / "restored")

    assert len(snapshot_id) == 64
    selected = next(item for item in snapshots if item["id"] == snapshot_id)
    assert selected["hostname"] == config.host.id
    assert set(selected["tags"]) == {
        f"host:{config.host.id}",
        "engine:dragonfly",
        f"instance:{instance.id}",
    }
    assert Path(config.host.repos["kv"]).is_relative_to(tmp_path)
    assert manifest.check(restored)["status"] == "complete"
    assert (restored / "data").read_bytes() == b"local integration backup"


def test_local_restic_failure_keeps_completed_backup(config, tmp_path):
    require_binary("restic")
    instance = config.get("postgres", "test-dev-01")
    folder = _backup(tmp_path / "complete")
    missing_repo = tmp_path / "not-initialized"
    host = replace(
        config.host,
        repos={**config.host.repos, "postgres": str(missing_repo)},
    )

    with pytest.raises(ResticError, match="upload failed"):
        restic.upload(host, instance, folder)

    assert manifest.check(folder)["status"] == "complete"
    assert not missing_repo.exists()


def test_local_restic_retention_prune_and_subset_check(config, tmp_path):
    require_binary("restic")
    host = replace(
        config.host,
        retention={"daily": 1, "weekly": 1, "monthly": 1, "data_parts": 2},
    )
    restic.init(host, "postgres")
    folder = tmp_path / "history"
    folder.mkdir()
    (folder / "data").write_text("first")
    now = datetime.now(timezone.utc)
    for days in (100, 40, 8, 0):
        (folder / "data").write_text(str(days))
        restic._run(
            host,
            "postgres",
            [
                "backup",
                str(folder),
                "--time",
                (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S"),
                "--tag",
                "host:local-test",
                "--tag",
                "engine:postgres",
                "--tag",
                "instance:test-dev-01",
            ],
        )

    before = restic.snapshots(host, "postgres", "test-dev-01")
    restic.forget(host, "postgres", dry_run=False)
    after = restic.snapshots(host, "postgres", "test-dev-01")
    restic.prune(host, "postgres")
    result = restic.check(host, "postgres", part=1)

    assert len(before) == 4
    assert 0 < len(after) < len(before)
    assert result.code == 0


def _init(host, group: str) -> None:
    binary = require_binary("restic")
    repo = Path(host.repos[group])
    password = Path(host.secrets["restic_password"])
    assert repo.is_relative_to(host.state_dir.parent)
    command(
        [
            binary,
            "--repo",
            str(repo),
            "--password-file",
            str(password),
            "init",
            "--repository-version",
            "1",
        ]
    )


def _backup(folder: Path) -> Path:
    folder.mkdir(mode=0o700)
    (folder / "data").write_bytes(b"local integration backup")
    manifest.write(
        folder,
        {
            "status": "complete",
            "files": manifest.files(folder, ["data"]),
        },
    )
    return folder
