import pytest

from evanovation_db.errors import BackupError
from evanovation_db.manifest import check, files, write


def test_manifest_checks_files(tmp_path):
    (tmp_path / "dump.rdb").write_bytes(b"backup")
    data = {"status": "complete", "files": files(tmp_path, ["dump.rdb"])}
    write(tmp_path, data)

    assert check(tmp_path) == data


def test_changed_file_fails(tmp_path):
    target = tmp_path / "dump.rdb"
    target.write_bytes(b"backup")
    write(tmp_path, {"status": "complete", "files": files(tmp_path, ["dump.rdb"])})
    target.write_bytes(b"changed")

    with pytest.raises(BackupError, match="changed"):
        check(tmp_path)


def test_incomplete_manifest_fails(tmp_path):
    write(tmp_path, {"status": "partial", "files": []})

    with pytest.raises(BackupError, match="not complete"):
        check(tmp_path)


def test_unlisted_file_fails(tmp_path):
    (tmp_path / "dump.rdb").write_bytes(b"backup")
    write(tmp_path, {"status": "complete", "files": files(tmp_path, ["dump.rdb"])})
    (tmp_path / "unexpected").write_bytes(b"extra")

    with pytest.raises(BackupError, match="unlisted file"):
        check(tmp_path)
