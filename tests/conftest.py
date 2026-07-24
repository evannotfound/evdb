from dataclasses import replace
from pathlib import Path

import pytest

from evanovation_db.config import Config, load

ROOT = Path(__file__).parents[1]


@pytest.fixture
def config(tmp_path):
    source = load(ROOT / "config/montreal-01")
    host = replace(
        source.host,
        config_dir=tmp_path / "etc",
        state_dir=tmp_path / "state",
        backup_dir=tmp_path / "backups",
        lock_dir=tmp_path / "locks",
        min_free_gb=0,
        repos={"postgres": str(tmp_path / "pg-repo"), "kv": str(tmp_path / "kv-repo")},
        secrets={"restic_password": str(tmp_path / "restic-password")},
    )
    (tmp_path / "restic-password").write_text("test-password")
    return Config(host, source.instances)
