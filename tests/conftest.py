from dataclasses import replace
from pathlib import Path

import pytest

from evanovation_db.config import Paths, load

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests/fixtures/config"


@pytest.fixture
def paths(tmp_path):
    return Paths(
        config=tmp_path / "etc/evdb",
        state=tmp_path / "var/lib/evdb",
        tool=tmp_path / "opt/evdb",
    )


@pytest.fixture
def config(tmp_path, paths):
    source = load(FIXTURES / "combined", paths=paths)
    host = replace(
        source.host,
        data_root=tmp_path / "data",
        backup=replace(
            source.host.backup,
            repos={"postgres": str(tmp_path / "pg-repo"), "kv": str(tmp_path / "kv-repo")},
            min_free_gb=0,
        ),
    )
    return replace(source, host=host)
