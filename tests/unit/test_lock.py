import pytest

from evanovation_db.errors import LockError
from evanovation_db.lock import lock


def test_lock_blocks_second_holder(tmp_path):
    path = tmp_path / "test.lock"

    with lock(path), pytest.raises(LockError), lock(path):
        pass


def test_lock_is_released(tmp_path):
    path = tmp_path / "test.lock"

    with lock(path):
        pass
    with lock(path):
        pass
