import json
from datetime import date

import pytest

from evanovation_db import manifest, restic
from evanovation_db.errors import ResticError
from evanovation_db.run import Result


def test_snapshot_reads_final_summary():
    result = Result(
        ("restic",),
        0,
        "\n".join(
            [
                json.dumps({"message_type": "status"}),
                json.dumps({"message_type": "summary", "snapshot_id": "abc"}),
            ]
        ),
        "",
    )

    assert restic._snapshot(result) == "abc"


def test_snapshot_rejects_invalid_json():
    result = Result(("restic",), 0, "not-json", "")

    with pytest.raises(ResticError, match="invalid Restic JSON"):
        restic._snapshot(result)


def test_snapshot_requires_final_summary():
    result = Result(
        ("restic",),
        0,
        "\n".join(
            [
                json.dumps({"message_type": "summary", "snapshot_id": "early"}),
                json.dumps({"message_type": "status"}),
            ]
        ),
        "",
    )

    with pytest.raises(ResticError, match="final summary"):
        restic._snapshot(result)


def test_upload_rejects_nonzero_even_with_snapshot(config, tmp_path, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    folder = tmp_path / "complete"
    folder.mkdir()
    (folder / "data").write_bytes(b"backup")
    manifest.write(
        folder,
        {"status": "complete", "files": manifest.files(folder, ["data"])},
    )
    monkeypatch.setattr(
        restic,
        "_run",
        lambda *args, **kwargs: Result(
            ("restic",),
            3,
            json.dumps({"message_type": "summary", "snapshot_id": "bad"}),
            "incomplete",
        ),
    )

    with pytest.raises(ResticError, match="upload failed"):
        restic.upload(config.host, instance, folder)


def test_forget_uses_stable_tag_groups(config, monkeypatch):
    seen = {}

    def fake_run(host, group, args, **kwargs):
        seen.setdefault("args", []).append(args)
        if args == ["cat", "config"]:
            return Result(("restic",), 0, '{"version":1}', "")
        return Result(("restic",), 0, "", "")

    monkeypatch.setattr(restic, "_run", fake_run)

    restic.forget(config.host, "postgres")

    assert seen["args"][1][-3:] == ["--group-by", "tags", "--dry-run"]


def test_init_uses_repository_v1(config, monkeypatch):
    seen = {}

    def fake_run(host, group, args, **kwargs):
        seen["args"] = args
        return Result(("restic",), 0, "", "")

    monkeypatch.setattr(restic, "_run", fake_run)

    restic.init(config.host, "postgres")

    assert seen["args"] == ["init", "--repository-version", "1"]


def test_check_rejects_repository_v2(config, monkeypatch):
    monkeypatch.setattr(
        restic,
        "_run",
        lambda host, group, args, **kwargs: Result(("restic",), 0, '{"version":2}', ""),
    )

    with pytest.raises(ResticError, match="v1"):
        restic.check(config.host, "postgres")


def test_same_repository_uses_same_lock(config):
    config.host.repos["kv"] = config.host.repos["postgres"]

    assert restic._repo_lock(config.host, "postgres") == restic._repo_lock(config.host, "kv")


def test_rotating_check_part_is_deterministic(config):
    first = restic.check_part(config.host, today=date(2026, 1, 1))
    next_week = restic.check_part(config.host, today=date(2026, 1, 8))

    assert 1 <= first <= config.host.retention["data_parts"]
    assert next_week == first % config.host.retention["data_parts"] + 1


def test_restore_finds_checked_backup(config, tmp_path, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    target = tmp_path / "staging"

    def fake_run(host, group, args, **kwargs):
        if args == ["cat", "config"]:
            return Result(("restic",), 0, '{"version":1}', "")
        folder = target / "source/backup"
        folder.mkdir(parents=True)
        (folder / "data").write_bytes(b"backup")
        manifest.write(
            folder,
            {"status": "complete", "files": manifest.files(folder, ["data"])},
        )
        return Result(("restic",), 0, "", "")

    monkeypatch.setattr(restic, "_run", fake_run)

    folder = restic.restore(config.host, instance, "snapshot", target)

    assert folder == (target / "source/backup").resolve()
