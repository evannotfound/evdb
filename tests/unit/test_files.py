import json

import pytest

from evanovation_db import files
from evanovation_db.errors import BackupError
from evanovation_db.files import (
    finish,
    hash,
    private_dir,
    require_file,
    require_space,
    write_json,
    write_text,
)


def test_private_dir_and_atomic_json(tmp_path):
    folder = private_dir(tmp_path / "private")
    target = folder / "state.json"

    write_json(target, {"ok": True})

    assert folder.stat().st_mode & 0o777 == 0o700
    assert target.stat().st_mode & 0o777 == 0o600
    assert json.loads(target.read_text()) == {"ok": True}


def test_atomic_writes_preserve_target_or_parent_numeric_ownership(tmp_path, monkeypatch):
    parent = private_dir(tmp_path / "managed")
    existing = parent / "existing"
    existing.write_text("old")
    owners = []
    directories = []
    monkeypatch.setattr(files.os, "fchown", lambda fd, uid, gid: owners.append((uid, gid)))
    monkeypatch.setattr(
        files.os,
        "chown",
        lambda path, uid, gid: directories.append((path, uid, gid)),
    )

    write_text(existing, "new", mode=0o640)
    write_text(parent / "new", "value", mode=0o600)
    write_text(parent / "nested/deeper/value", "nested", mode=0o600)

    expected = (parent.stat().st_uid, parent.stat().st_gid)
    assert owners == [expected, expected, expected]
    assert [item[0] for item in directories] == [parent / "nested", parent / "nested/deeper"]
    assert all(item[1:] == expected for item in directories)
    assert existing.stat().st_mode & 0o777 == 0o640


def test_hash_and_require_file(tmp_path):
    target = tmp_path / "data"
    target.write_bytes(b"hello")

    assert require_file(target) == target
    assert hash(target) == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_empty_file_fails(tmp_path):
    target = tmp_path / "empty"
    target.touch()

    with pytest.raises(BackupError, match="empty"):
        require_file(target)


def test_finish_partial_folder(tmp_path):
    partial = private_dir(tmp_path / "run.partial")

    result = finish(partial)

    assert result.name == "run"
    assert result.is_dir()


def test_space_check_fails(tmp_path):
    with pytest.raises(BackupError, match="free space"):
        require_space(tmp_path, 10**9)
