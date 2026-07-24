import json

import pytest

from evanovation_db.errors import BackupError
from evanovation_db.files import finish, hash, private_dir, require_file, require_space, write_json


def test_private_dir_and_atomic_json(tmp_path):
    folder = private_dir(tmp_path / "private")
    target = folder / "state.json"

    write_json(target, {"ok": True})

    assert folder.stat().st_mode & 0o777 == 0o700
    assert target.stat().st_mode & 0o777 == 0o600
    assert json.loads(target.read_text()) == {"ok": True}


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
