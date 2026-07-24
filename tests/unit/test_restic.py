import json

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
                "not-json",
                json.dumps({"message_type": "summary", "snapshot_id": "abc"}),
            ]
        ),
        "",
    )

    assert restic._snapshot(result) == "abc"


def test_upload_rejects_nonzero_even_with_snapshot(config, tmp_path, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    (tmp_path / "data").write_bytes(b"backup")
    manifest.write(
        tmp_path,
        {"status": "complete", "files": manifest.files(tmp_path, ["data"])},
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
        restic.upload(config.host, instance, tmp_path)


def test_forget_uses_stable_tag_groups(config, monkeypatch):
    seen = {}

    def fake_run(host, group, args, **kwargs):
        seen["args"] = args
        return Result(("restic",), 0, "", "")

    monkeypatch.setattr(restic, "_run", fake_run)

    restic.forget(config.host, "postgres")

    assert seen["args"][-3:] == ["--group-by", "tags", "--dry-run"]


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
