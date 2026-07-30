from pathlib import Path

import pytest

from evdb.config import load
from evdb.models import Paths

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests/fixtures/config"


@pytest.fixture
def paths(tmp_path):
    return Paths(
        config=tmp_path / "etc/evdb",
        state=tmp_path / "var/lib/evdb",
    )


@pytest.fixture
def config(paths, tmp_path):
    paths.config.mkdir(parents=True)
    rclone = tmp_path / "rclone.conf"
    rclone.write_bytes((FIXTURE / "rclone.conf").read_bytes())
    rclone.chmod(0o600)
    text = (FIXTURE / "config.yml").read_text()
    text = text.replace("/tmp/evdb-repository", str(tmp_path / "repository"))
    text = text.replace("/tmp/evdb-rclone.conf", str(rclone))
    paths.source.write_text(text)
    paths.source.chmod(0o600)
    paths.secrets.write_bytes((FIXTURE / "secrets.yml").read_bytes())
    paths.secrets.chmod(0o600)
    return load(paths.source, paths=paths)
