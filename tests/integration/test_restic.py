import os
import pwd
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from evdb import backup
from evdb import config as config_module
from evdb.files import managed_dir
from evdb.models import BackupSettings, Config, Host, Paths, Routing, Secrets
from evdb.run import run

RESTIC = shutil.which("restic")
RCLONE = shutil.which("rclone")


@pytest.mark.skipif(
    RESTIC is None or RCLONE is None,
    reason="Restic and rclone are required",
)
def test_missing_rclone_local_repository_is_initialized_without_mkdir(
    config, tmp_path, monkeypatch
):
    monkeypatch.setattr(backup, "RESTIC", Path(RESTIC))
    monkeypatch.setattr(backup, "RCLONE", Path(RCLONE))
    root = tmp_path / "remote"
    repository = root / "initially-absent/repository"
    home = tmp_path / "operator-home"
    rclone = home / ".config/rclone/rclone.conf"
    rclone.parent.mkdir(parents=True)
    rclone.write_text("[local]\ntype = local\n")
    rclone.chmod(0o600)
    account = pwd.getpwuid(os.getuid())
    operator = pwd.struct_passwd(
        (
            account.pw_name,
            account.pw_passwd,
            account.pw_uid,
            account.pw_gid,
            account.pw_gecos,
            str(home),
            account.pw_shell,
        )
    )
    monkeypatch.setattr(config_module.pwd, "getpwuid", lambda uid: operator)
    config = replace(
        config,
        host=replace(
            config.host,
            backup=replace(
                config.host.backup,
                repository=f"rclone:local:{repository}",
                rclone_config=rclone,
            ),
        ),
    )

    assert not repository.exists()
    backup.initialize(config)

    assert repository.is_dir()
    assert (repository / "config").is_file()
    assert backup.repository_ready(config)
    assert not list(config.paths.locks.glob(".restic-password.*"))


@pytest.mark.skipif(
    os.geteuid() != 0 or RESTIC is None or RCLONE is None,
    reason="root plus Restic and rclone are required",
)
def test_root_drops_repository_work_to_rclone_owner(monkeypatch):
    account = pwd.getpwnam("nobody")
    root = Path(tempfile.mkdtemp(prefix="evdb-root-", dir="/tmp"))
    real_getpwuid = pwd.getpwuid
    try:
        root.chmod(0o711)
        home = root / "home"
        rclone = home / ".config/rclone/rclone.conf"
        rclone.parent.mkdir(parents=True)
        for path in (home, home / ".config", rclone.parent):
            os.chown(path, account.pw_uid, account.pw_gid)
            path.chmod(0o700)
        rclone.write_text("[local]\ntype = local\n")
        os.chown(rclone, account.pw_uid, account.pw_gid)
        rclone.chmod(0o600)
        remote = root / "remote"
        remote.mkdir()
        os.chown(remote, account.pw_uid, account.pw_gid)
        remote.chmod(0o700)
        operator = pwd.struct_passwd(
            (
                account.pw_name,
                account.pw_passwd,
                account.pw_uid,
                account.pw_gid,
                account.pw_gecos,
                str(home),
                account.pw_shell,
            )
        )
        monkeypatch.setattr(
            config_module.pwd,
            "getpwuid",
            lambda uid: operator if uid == account.pw_uid else real_getpwuid(uid),
        )
        monkeypatch.setattr(backup, "RESTIC", Path(RESTIC))
        monkeypatch.setattr(backup, "RCLONE", Path(RCLONE))
        config = Config(
            Host(
                "privilege-test-01",
                "storage.example.com",
                BackupSettings(
                    f"rclone:local:{remote / 'repository'}",
                    rclone,
                    min_free_gb=0,
                ),
                Routing("ops@example.com", "testdns", "traefik:v3.7.8"),
            ),
            (),
            Secrets("disposable-restic-password", ()),
            Paths(config=root / "etc/evdb", state=root / "state"),
        )

        backup.initialize(config)
        role = config.paths.backups / "app-test-01/postgres"
        for path in (config.paths.state, config.paths.backups, role.parent, role):
            managed_dir(path, 0o711)
        folder = role / "complete"
        folder.mkdir(mode=0o700)
        (folder / "data.bin").write_bytes(b"checked data")
        selected = backup.rclone_owner(rclone)
        backup._handoff(folder, selected)
        database_file = config.paths.databases / "private/data.bin"
        database_file.parent.mkdir(parents=True, mode=0o700)
        database_file.write_bytes(b"database data")
        database_file.chmod(0o600)

        result = backup._restic(config, ["backup", str(folder), "--json"])

        assert result.code == 0
        assert (remote / "repository/config").stat().st_uid == account.pw_uid
        identity = {
            "user": selected.uid,
            "group": selected.gid,
            "extra_groups": selected.groups,
        }
        run(["/usr/bin/test", "-r", str(folder / "data.bin")], **identity)
        run(["/usr/bin/test", "!", "-r", str(database_file)], **identity)
    finally:
        shutil.rmtree(root, ignore_errors=True)
