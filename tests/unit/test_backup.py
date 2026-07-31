import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest

from evdb import backup, database, status
from evdb.errors import BackupError, ResticError
from evdb.run import Result


class Engine:
    @staticmethod
    def backup(database, folder, run_id):
        del database, run_id
        (folder / "data.bin").write_bytes(b"checked data")
        return {
            "format": "postgres-custom-v1",
            "version": "16.1",
            "objects": 1,
            "files": ["data.bin"],
        }


def _ready(config, target):
    target.compose.parent.mkdir(parents=True, exist_ok=True)
    target.compose.write_text("services: {}\n")


def test_checked_backup_manifest_upload_and_local_cleanup(config, monkeypatch):
    target = config.select("app-test-01/postgres")
    _ready(config, target)
    monkeypatch.setattr(backup, "get", lambda engine: Engine)
    uploaded = []
    handed = []
    monkeypatch.setattr(backup, "_handoff", lambda folder, owner: handed.append((folder, owner)))
    monkeypatch.setattr(
        backup,
        "_upload",
        lambda config, database, folder: uploaded.append(folder) or "snapshot-1",
    )

    value = backup.create(config, target)
    record = backup.manifest_check(value["folder"])

    assert record["image"] == target.image
    assert record["checks"] == ["size", "sha256", "postgres"]
    assert record["upload"]["snapshot"] == "snapshot-1"
    assert uploaded == [backup.Path(value["folder"])]
    assert [folder for folder, _owner in handed] == [
        backup.Path(value["folder"]),
        backup.Path(value["folder"]),
    ]
    folder = backup.Path(value["folder"])
    assert config.paths.state.stat().st_mode & 0o777 == 0o711
    assert config.paths.backups.stat().st_mode & 0o777 == 0o711
    assert folder.parent.stat().st_mode & 0o777 == 0o711
    assert "verification" not in json.dumps(record).lower()
    assert "machine" not in json.dumps(record).lower()


def test_completed_backup_handoff_is_recursive_and_rejects_symlinks(config, tmp_path):
    operator = backup.rclone_owner(config.host.backup.rclone_config)
    folder = tmp_path / "complete"
    nested = folder / "nested"
    nested.mkdir(parents=True)
    (folder / "data.bin").write_bytes(b"data")
    (nested / "part.bin").write_bytes(b"part")

    backup._handoff(folder, operator)

    assert folder.stat().st_mode & 0o777 == 0o500
    assert nested.stat().st_mode & 0o777 == 0o500
    assert all(path.stat().st_mode & 0o777 == 0o400 for path in folder.rglob("*.bin"))
    assert all(
        path.stat().st_uid == operator.uid for path in (folder, nested, *folder.rglob("*.bin"))
    )

    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    (unsafe / "linked").symlink_to(folder / "data.bin")
    with pytest.raises(BackupError, match="cannot be handed"):
        backup._handoff(unsafe, operator)


def test_upload_failure_keeps_complete_local_folder(config, monkeypatch):
    target = config.select("app-test-01/postgres")
    _ready(config, target)
    monkeypatch.setattr(backup, "get", lambda engine: Engine)
    monkeypatch.setattr(backup, "_handoff", lambda *args: None)
    monkeypatch.setattr(
        backup,
        "_upload",
        lambda *args: (_ for _ in ()).throw(ResticError("remote unavailable")),
    )

    with pytest.raises(ResticError, match="repository.*local backup retained"):
        backup.create(config, target)

    folders = [item for item in target.paths.role_backups(target.project, target.role).iterdir()]
    assert len(folders) == 1
    assert backup.manifest_check(folders[0])["upload"]["ok"] is False


def test_cache_backup_never_calls_engine(config, monkeypatch):
    target = config.select("app-test-01/kv")
    target = target.__class__(
        target.project,
        target.role,
        target.settings.__class__(
            target.settings.engine,
            target.settings.image,
            "cache",
            target.settings.http,
        ),
        target.host,
        target.paths,
        target.credentials,
    )
    monkeypatch.setattr(backup, "get", lambda engine: pytest.fail("engine called"))
    with pytest.raises(BackupError, match="disabled"):
        backup.create(config, target)


def test_create_all_continues_and_reports_failures(config, monkeypatch):
    calls = []

    def create(_config, database, **kwargs):
        calls.append(database.identity)
        if database.role == "postgres":
            raise BackupError("postgres failed")
        return {"backup": "kv-backup", "snapshot": "kv-snapshot"}

    monkeypatch.setattr(backup, "create", create)
    values = backup.create_all(config)

    assert calls == ["app-test-01/postgres", "app-test-01/kv"]
    assert not values["app-test-01/postgres"]["ok"]
    assert values["app-test-01/kv"]["ok"]


def test_create_all_does_not_hide_unexpected_faults(config, monkeypatch):
    monkeypatch.setattr(
        backup,
        "create",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bug")),
    )

    with pytest.raises(RuntimeError, match="bug"):
        backup.create_all(config)


@pytest.mark.parametrize("code", [1, 2, 3, 11])
def test_repository_init_only_accepts_documented_missing_exit(config, monkeypatch, code):
    monkeypatch.setattr(backup, "require_version", lambda: (0, 17, 0))
    calls = []

    def restic(_config, args, **kwargs):
        calls.append(args)
        return Result(("restic",), code, "", "access denied")

    monkeypatch.setattr(backup, "_restic", restic)
    with pytest.raises(ResticError, match=config.host.backup.repository):
        backup.initialize(config)
    assert calls == [["cat", "config"]]


def test_repository_missing_exit_initializes_format_one_and_verifies(config, monkeypatch):
    monkeypatch.setattr(backup, "require_version", lambda: (0, 17, 0))
    results = iter(
        [
            Result(("restic",), 10, "", "missing"),
            Result(("restic",), 0, "", ""),
            Result(("restic",), 0, '{"version":1}\n', ""),
        ]
    )
    calls = []
    monkeypatch.setattr(
        backup,
        "_restic",
        lambda config, args, **kwargs: calls.append(args) or next(results),
    )

    backup.initialize(config)

    assert calls == [
        ["cat", "config"],
        ["init", "--repository-version", "1"],
        ["cat", "config"],
    ]
    assert not any(call[:2] == ["rclone", "mkdir"] for call in calls)


def test_restic_uses_operator_identity_and_memory_password_descriptor(config, monkeypatch):
    seen = {}

    def run(args, **kwargs):
        password_path = args[args.index("--password-file") + 1]
        descriptor = kwargs["pass_fds"][0]
        details = os.fstat(descriptor)
        seen.update(
            args=args,
            env=kwargs["env"],
            password_path=password_path,
            descriptor=descriptor,
            descriptor_mode=details.st_mode & 0o777,
            descriptor_owner=(details.st_uid, details.st_gid),
            password=backup.Path(password_path).read_text(),
            kwargs=kwargs,
        )
        return Result(tuple(args), 0, '{"version":1}\n', "")

    monkeypatch.setattr(backup, "run", run)
    monkeypatch.setattr(backup.os, "geteuid", lambda: 0)

    result = backup._restic(config, ["cat", "config"])

    assert result.code == 0
    assert seen["password"] == config.secrets.restic_password + "\n"
    owner = backup.rclone_owner(config.host.backup.rclone_config)
    assert seen["descriptor_mode"] == 0o400
    assert seen["descriptor_owner"] == (owner.uid, owner.gid)
    assert seen["password_path"] == f"/proc/self/fd/{seen['descriptor']}"
    assert seen["args"][0] == "/usr/bin/restic"
    assert "rclone.program=/usr/bin/rclone" in seen["args"]
    assert config.secrets.restic_password not in seen["args"]
    assert config.secrets.restic_password not in seen["env"].values()
    assert "RESTIC_PASSWORD" not in seen["env"]
    assert seen["env"] == {
        "HOME": str(backup.rclone_owner(config.host.backup.rclone_config).home),
        "USER": backup.rclone_owner(config.host.backup.rclone_config).name,
        "LOGNAME": backup.rclone_owner(config.host.backup.rclone_config).name,
        "RCLONE_CONFIG": str(config.host.backup.rclone_config),
        "RESTIC_CACHE_DIR": str(
            backup.rclone_owner(config.host.backup.rclone_config).home / ".cache/restic"
        ),
    }
    assert seen["kwargs"]["replace_env"] is True
    assert seen["kwargs"]["user"] == owner.uid
    assert seen["kwargs"]["group"] == owner.gid
    assert seen["kwargs"]["extra_groups"] == owner.groups
    with pytest.raises(OSError):
        os.fstat(seen["descriptor"])


def test_create_all_redacts_exact_and_encoded_credentials(config, monkeypatch):
    secret = "rclone value"
    encoded = quote(secret, safe="")
    config.host.backup.rclone_config.write_text(f"[remote]\ntype = local\ntoken = {secret}\n")
    monkeypatch.setattr(
        backup,
        "create",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            BackupError(f"repository visible {secret} {encoded}")
        ),
    )

    values = backup.create_all(config)

    assert values
    for value in values.values():
        assert secret not in value["error"]
        assert encoded not in value["error"]
        assert "repository visible" in value["error"]
        assert "<redacted>" in value["error"]


def test_cleanup_counts_only_valid_matching_uploaded_manifests(config, monkeypatch):
    target = config.select("app-test-01/postgres")
    _ready(config, target)
    clean = backup._clean
    monkeypatch.setattr(backup, "get", lambda engine: Engine)
    monkeypatch.setattr(backup, "_upload", lambda *args: "snapshot")
    monkeypatch.setattr(backup, "_handoff", lambda *args: None)
    monkeypatch.setattr(backup, "_clean", lambda *args, **kwargs: None)
    root = config.paths.role_backups(target.project, target.role)
    folders = []
    for _number in range(4):
        value = backup.create(config, target)
        folder = root / value["backup"]
        folders.append(folder)
    malformed = root / "malformed"
    malformed.mkdir()
    backup.manifest_write(malformed, {"upload": {"ok": True}})
    foreign = root / "foreign"
    shutil.copytree(folders[0], foreign)
    record = backup.manifest_read(foreign)
    record.update(backup="foreign", project="other-prod-01")
    record["upload"].update(backup="foreign")
    backup.manifest_write(foreign, record)

    clean(config, target, root, keep=2)

    assert not folders[0].exists() and not folders[1].exists()
    assert folders[2].exists() and folders[3].exists()
    assert malformed.exists() and foreign.exists()


def test_matched_remote_snapshot_time_drives_history_and_freshness(config, monkeypatch):
    target = config.select("app-test-01/postgres")
    _ready(config, target)
    monkeypatch.setattr(backup, "get", lambda engine: Engine)
    monkeypatch.setattr(backup, "_handoff", lambda *args: None)
    monkeypatch.setattr(backup, "_upload", lambda *args: "snapshot-after-long-upload")
    created = backup.create(config, target)
    record = backup.manifest_read(created["folder"])
    snapshot_time = datetime.now(UTC)
    local_finish = snapshot_time - timedelta(hours=30)
    record["finished"] = local_finish.isoformat()
    record["upload"] = {
        "ok": True,
        "backup": record["backup"],
        "snapshot": "snapshot-after-long-upload",
        "time": snapshot_time.isoformat(),
    }
    backup.manifest_write(created["folder"], record)
    monkeypatch.setattr(
        backup,
        "snapshots",
        lambda *args: [
            {
                "id": "snapshot-after-long-upload",
                "time": snapshot_time.isoformat(),
                "tags": [f"backup:{record['backup']}", "purpose:manual"],
            }
        ],
    )
    monkeypatch.setattr(
        database,
        "observe",
        lambda *args: {"running": True, "healthy": True, "health": "healthy"},
    )

    rows = backup.history(config, target)
    value, errors = status._database(config, target)

    assert rows[0]["source"] == "local+remote"
    assert rows[0]["time"] == snapshot_time.isoformat()
    assert value["latest_backup"]["time"] == snapshot_time.isoformat()
    assert value["latest_backup"]["state"] == "current"
    assert not any(item["code"] == "backup_stale" for item in errors)
