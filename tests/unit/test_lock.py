import pytest

from evdb.errors import LockError
from evdb.lock import lock


def test_lock_blocks_second_holder(tmp_path):
    path = tmp_path / "test.lock"

    with lock(path), pytest.raises(LockError), lock(path):
        pass


def test_lock_is_released(tmp_path):
    path = tmp_path / "test.lock"

    with lock(path):
        pass


def test_shared_locks_coexist_but_block_exclusive_holder(tmp_path):
    path = tmp_path / "test.lock"

    with lock(path, shared=True), lock(path, shared=True), pytest.raises(LockError), lock(path):
        pass
    with lock(path):
        pass
