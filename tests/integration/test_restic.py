import shutil
from dataclasses import replace

import pytest

from evdb import backup


@pytest.mark.skipif(
    shutil.which("restic") is None or shutil.which("rclone") is None,
    reason="Restic and rclone are required",
)
def test_missing_rclone_local_repository_is_initialized_without_mkdir(config, tmp_path):
    root = tmp_path / "remote"
    repository = root / "initially-absent/repository"
    config.paths.rclone.write_text("[local]\ntype = local\n")
    config = replace(
        config,
        host=replace(
            config.host,
            backup=replace(config.host.backup, repository=f"rclone:local:{repository}"),
        ),
    )

    assert not repository.exists()
    backup.initialize(config)

    assert repository.is_dir()
    assert (repository / "config").is_file()
    assert backup.repository_ready(config)
