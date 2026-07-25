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
        if args == ["snapshots", "--json", "snapshot"]:
            return Result(
                ("restic",),
                0,
                json.dumps(
                    [
                        {
                            "id": "snapshot",
                            "tags": [
                                f"host:{host.id}",
                                f"engine:{instance.engine}",
                                f"instance:{instance.id}",
                            ],
                        }
                    ]
                ),
                "",
            )
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


def test_snapshots_requires_all_exact_identity_tags(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    seen = {}

    def fake_run(host, group, args, **kwargs):
        seen["args"] = args
        return Result(
            ("restic",),
            0,
            json.dumps(
                [
                    {
                        "id": "correct",
                        "tags": [
                            f"host:{host.id}",
                            f"engine:{instance.engine}",
                            f"instance:{instance.id}",
                        ],
                    },
                    {"id": "wrong-host", "tags": [f"instance:{instance.id}"]},
                ]
            ),
            "",
        )

    monkeypatch.setattr(restic, "_run", fake_run)

    snapshots = restic.snapshots(config.host, instance)

    assert [item["id"] for item in snapshots] == ["correct"]
    assert seen["args"] == [
        "snapshots",
        "--json",
        "--tag",
        f"engine:{instance.engine},host:{config.host.id},instance:{instance.id}",
    ]


def test_select_snapshot_rejects_cross_host_or_database(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    monkeypatch.setattr(
        restic,
        "_run",
        lambda *args, **kwargs: Result(
            ("restic",),
            0,
            json.dumps(
                [
                    {
                        "id": "snapshot-id",
                        "tags": [
                            "host:other-host",
                            f"engine:{instance.engine}",
                            "instance:other-database",
                        ],
                    }
                ]
            ),
            "",
        ),
    )

    with pytest.raises(ResticError, match="does not belong"):
        restic.select_snapshot(config.host, instance, "snapshot-id")


def test_select_snapshot_latest_uses_newest_exact_identity(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    monkeypatch.setattr(
        restic,
        "snapshots",
        lambda host, selected: [
            {"id": "older", "time": "2026-07-24T10:00:00Z"},
            {"id": "newest", "time": "2026-07-25T10:00:00+00:00"},
            {"id": "invalid", "time": "not-a-time"},
        ],
    )

    assert restic.select_snapshot(config.host, instance, "latest")["id"] == "newest"


def test_select_snapshot_latest_rejects_missing_exact_identity(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    monkeypatch.setattr(restic, "snapshots", lambda host, selected: [])

    with pytest.raises(ResticError, match="no snapshot exists"):
        restic.select_snapshot(config.host, instance, "latest")


def test_run_redacts_password_and_rclone_values_without_putting_them_in_arguments(
    config, monkeypatch
):
    rclone = config.host.state_dir / "rclone/rclone.conf"
    rclone.parent.mkdir(parents=True)
    rclone.write_text(
        "[remote]\ntype = s3\naccess_key_id = private-access\nsecret_access_key = private-secret\n"
    )
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(restic, "run", fake_run)

    restic._run(config.host, "postgres", ["snapshots"])

    command = " ".join(seen["args"])
    assert "test-password" not in command
    assert "private-access" not in command
    assert "private-secret" not in command
    assert {"test-password", "private-access", "private-secret"}.issubset(seen["kwargs"]["secrets"])
