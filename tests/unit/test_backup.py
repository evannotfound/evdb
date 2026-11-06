import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event, get_ident
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from evdb import backup, database, status
from evdb.config import defaults
from evdb.engines import dragonfly, kv
from evdb.errors import BackupError, CommandError, ResticError
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
    saved = backup.catalog(config)
    assert saved["snapshots"][0]["id"] == "snapshot-1"
    assert saved["attempts"][target.identity]["ok"]
    assert saved["checked_at"] is None
    assert (config.paths.state / "backup-catalog.json").stat().st_mode & 0o777 == 0o600


def test_completed_backup_handoff_is_recursive_and_rejects_symlinks(config, tmp_path):
    operator = backup.repository_owner(config.host.backup)
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
    assert not backup.catalog(config)["attempts"][target.identity]["ok"]


@pytest.mark.parametrize("engine", ["redis", "dragonfly", "valkey"])
def test_cache_backup_never_calls_engine(config, monkeypatch, engine):
    target = config.select("app-test-01/kv")
    target = target.__class__(
        target.project,
        target.role,
        replace(defaults("kv", target.settings.data_root, engine), mode="cache"),
        target.host,
        target.paths,
        target.credentials,
    )
    monkeypatch.setattr(backup, "get", lambda engine: pytest.fail("engine called"))
    with pytest.raises(BackupError, match="disabled"):
        backup.create(config, target)


def test_dragonfly_backup_waits_for_scheduled_snapshot(config, tmp_path, monkeypatch):
    target = config.select("app-test-01/kv")
    target = replace(target, settings=defaults("kv", target.settings.data_root, "dragonfly"))
    files = ("evdb-test-0000.dfs", "evdb-test-summary.dfs")
    replies = iter(["ERR SAVING - can not save database", "OK"])
    sources = iter([(), files, files])
    saved = []
    cleaned = []

    def save(container, password, args, **kwargs):
        saved.append(args)
        return next(replies)

    monkeypatch.setattr(dragonfly.kv, "text", save)
    monkeypatch.setattr(dragonfly, "_sources", lambda *args: next(sources))
    monkeypatch.setattr(dragonfly, "_cleanup", lambda container, names: cleaned.append(names))
    monkeypatch.setattr(dragonfly, "_check", lambda *args: {"keys": 1})
    monkeypatch.setattr(
        dragonfly.docker, "copy", lambda source, dest, **kw: dest.write_bytes(b"dfs")
    )
    monkeypatch.setattr(dragonfly.time, "sleep", lambda seconds: None)

    result = dragonfly.backup(target, tmp_path, "test")

    assert saved == [["SAVE", "DF", "evdb-test"], ["SAVE", "DF", "evdb-test"]]
    assert result["files"] == list(files)
    assert result["keys"] == 1
    assert cleaned == [files]


@pytest.mark.parametrize(
    ("reply", "message"),
    [("ERR disk full", "SAVE failed"), ("ERR SAVING - can not save database", "still in progress")],
)
def test_dragonfly_backup_does_not_publish_on_save_error(
    config, tmp_path, monkeypatch, reply, message
):
    target = config.select("app-test-01/kv")
    target = replace(target, settings=defaults("kv", target.settings.data_root, "dragonfly"))
    clock = iter([0, 1801])
    monkeypatch.setattr(dragonfly.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(dragonfly.kv, "text", lambda *a, **kw: reply)
    monkeypatch.setattr(dragonfly, "_sources", lambda *args: ())
    monkeypatch.setattr(dragonfly, "_check", lambda *args: pytest.fail("failed snapshot checked"))
    monkeypatch.setattr(
        dragonfly.docker, "copy", lambda *a, **kw: pytest.fail("failed snapshot copied")
    )

    with pytest.raises(BackupError, match=message):
        dragonfly.backup(target, tmp_path, "test")


def test_create_all_continues_and_reports_failures(config, monkeypatch):
    calls = []

    def create(_config, database, **kwargs):
        calls.append(database.identity)
        if database.role == "postgres":
            raise BackupError("postgres failed")
        return {"backup": "kv-backup", "snapshot": "kv-snapshot"}

    monkeypatch.setattr(backup, "create", create)
    monkeypatch.setattr(backup, "refresh", lambda current: calls.append("refresh"))
    values = backup.create_all(config)

    assert calls == ["app-test-01/postgres", "app-test-01/kv", "refresh"]
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
    owner = backup.repository_owner(config.host.backup)
    assert seen["descriptor_mode"] == 0o400
    assert seen["descriptor_owner"] == (owner.uid, owner.gid)
    assert seen["password_path"] == f"/proc/self/fd/{seen['descriptor']}"
    assert seen["args"][0] == "/usr/bin/restic"
    assert "rclone.program=/usr/bin/rclone" in seen["args"]
    assert config.secrets.restic_password not in seen["args"]
    assert config.secrets.restic_password not in seen["env"].values()
    assert "RESTIC_PASSWORD" not in seen["env"]
    assert seen["env"] == {
        "HOME": str(backup.repository_owner(config.host.backup).home),
        "USER": backup.repository_owner(config.host.backup).name,
        "LOGNAME": backup.repository_owner(config.host.backup).name,
        "RCLONE_CONFIG": str(config.host.backup.rclone_config),
        "RESTIC_CACHE_DIR": str(backup.repository_owner(config.host.backup).home / ".cache/restic"),
    }
    assert seen["kwargs"]["replace_env"] is True
    assert seen["kwargs"]["user"] == owner.uid
    assert seen["kwargs"]["group"] == owner.gid
    assert seen["kwargs"]["extra_groups"] == owner.groups
    with pytest.raises(OSError):
        os.fstat(seen["descriptor"])


def test_local_restic_omits_every_rclone_input(config, tmp_path, monkeypatch):
    local = replace(
        config,
        host=replace(
            config.host,
            backup=replace(
                config.host.backup,
                repository=str(tmp_path / "repository"),
                rclone_config=None,
            ),
        ),
    )
    seen = {}

    def run(args, **kwargs):
        seen.update(args=args, env=kwargs["env"])
        return Result(tuple(args), 0, '{"version":1}\n', "")

    monkeypatch.setattr(backup, "run", run)
    monkeypatch.setattr(backup.os, "geteuid", lambda: 0)

    backup._restic(local, ["cat", "config"])

    assert not any("rclone" in value for value in seen["args"])
    assert "RCLONE_CONFIG" not in seen["env"]
    assert seen["args"][seen["args"].index("-r") + 1] == str(tmp_path / "repository")


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
    monkeypatch.setattr(backup, "refresh", lambda current: None)

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
    saved = backup.catalog(config)
    saved["snapshots"][0]["time"] = snapshot_time.isoformat()
    backup.write_json(config.paths.state / "backup-catalog.json", saved)
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


def _snapshot(config, target, *, identity="snapshot-1", hours=0):
    return {
        "id": identity,
        "time": (datetime.now(UTC) - timedelta(hours=hours)).isoformat(),
        "tags": sorted(backup._identity_tags(config, target))
        + [f"engine:{target.engine}", "backup:backup-1", "purpose:manual"],
    }


def test_history_reads_catalog_without_remote_calls_or_dump_hashing(config, monkeypatch):
    target = config.select("app-test-01/postgres")
    _ready(config, target)
    monkeypatch.setattr(backup, "get", lambda engine: Engine)
    monkeypatch.setattr(backup, "_handoff", lambda *args: None)
    monkeypatch.setattr(backup, "_upload", lambda *args: "snapshot-1")
    created = backup.create(config, target)
    monkeypatch.setattr(backup, "_restic", lambda *a, **kw: pytest.fail("remote request"))
    monkeypatch.setattr(backup, "file_hash", lambda *a: pytest.fail("dump hashed"))

    rows = backup.history(config, target)

    assert rows[0]["source"] == "local+remote"
    assert rows[0]["snapshot"] == "snapshot-1"
    shutil.rmtree(created["folder"])
    assert backup.history(config, target)[0]["source"] == "remote"


def test_history_presence_does_not_replace_integrity_checks(config, monkeypatch):
    target = config.select("app-test-01/postgres")
    _ready(config, target)
    monkeypatch.setattr(backup, "get", lambda engine: Engine)
    monkeypatch.setattr(backup, "_handoff", lambda *args: None)
    monkeypatch.setattr(backup, "_upload", lambda *args: "snapshot-1")
    created = backup.create(config, target)
    folder = backup.Path(created["folder"])
    (folder / "data.bin").write_bytes(b"changed data")

    assert backup.history(config, target)[0]["local"]
    with pytest.raises(BackupError, match="backup file changed"):
        backup.manifest_check(folder)


def test_missing_catalog_is_local_unknown_and_other_repository_is_ignored(config, monkeypatch):
    target = config.select("app-test-01/postgres")
    monkeypatch.setattr(backup, "_restic", lambda *a, **kw: pytest.fail("remote request"))
    assert backup.history(config, target) == []
    value, errors = status._backup(config, target)
    assert value["state"] == "unknown"
    assert {item["code"] for item in errors} == {"backup_unknown"}
    saved = backup.catalog(config)
    saved.update(repository="other", snapshots=[_snapshot(config, target)], checked_at="yesterday")
    backup.write_json(config.paths.state / "backup-catalog.json", saved)
    assert backup.catalog(config)["checked_at"] is None
    assert backup.history(config, target) == []


def test_refresh_replaces_inventory_once_and_preserves_attempts(config, monkeypatch):
    target = config.select("app-test-01/postgres")
    saved = backup.catalog(config)
    saved["snapshots"] = [_snapshot(config, target, identity="removed")]
    backup.write_json(config.paths.state / "backup-catalog.json", saved)
    calls = []
    snapshot = _snapshot(config, target)

    def restic(current, args, **kwargs):
        calls.append(args)
        backup._record(config, target, error="attempt failed while refreshing")
        return Result(("restic",), 0, json.dumps([snapshot]), "")

    monkeypatch.setattr(backup, "_restic", restic)
    refreshed = backup.refresh(config)

    assert calls == [["snapshots", "--json", "--tag", f"host:{config.host.id}"]]
    assert refreshed["snapshots"] == [snapshot]
    assert refreshed["checked_at"]
    assert refreshed["refresh_error"] is None
    assert refreshed["attempts"][target.identity]["error"] == "attempt failed while refreshing"
    assert backup.catalog(config) == refreshed


def test_failed_refresh_retains_inventory_and_redacts_error(config, monkeypatch):
    target = config.select("app-test-01/postgres")
    saved = backup.catalog(config)
    saved.update(
        snapshots=[_snapshot(config, target)],
        checked_at=datetime.now(UTC).isoformat(),
        repository_checked_at=datetime.now(UTC).isoformat(),
    )
    backup.write_json(config.paths.state / "backup-catalog.json", saved)
    secret = config.secrets.restic_password
    monkeypatch.setattr(
        backup,
        "_restic",
        lambda *a, **kw: (_ for _ in ()).throw(ResticError(f"OneDrive failed {secret}")),
    )

    with pytest.raises(ResticError, match="inventory refresh failed"):
        backup.refresh(config)

    failed = backup.catalog(config)
    assert failed["checked_at"] == saved["checked_at"]
    assert failed["snapshots"] == saved["snapshots"]
    assert secret not in json.dumps(failed)
    assert "<redacted>" in failed["refresh_error"]["error"]
    value, errors = status._backup(config, target)
    assert value["state"] == "current" and not errors
    repository_errors = []
    repository = status._repository(config, repository_errors)
    assert repository["ready"] is True and repository["refresh_error"]
    assert repository_errors[0]["code"] == "repository_assessment_failed"


def test_catalog_backup_age_is_recomputed_and_failures_keep_success(config):
    target = config.select("app-test-01/postgres")
    saved = backup.catalog(config)
    saved.update(snapshots=[_snapshot(config, target, hours=30)], checked_at=None)
    backup.write_json(config.paths.state / "backup-catalog.json", saved)
    backup._record(config, target, error="latest attempt failed")

    value, errors = status._backup(config, target)

    assert value["state"] == "stale"
    assert value["snapshot"] == "snapshot-1"
    assert value["last_attempt"]["error"] == "latest attempt failed"
    assert {item["code"] for item in errors} == {"backup_stale", "backup_failed"}


def test_concurrent_catalog_updates_preserve_each_role(config):
    targets = config.databases
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(backup._record, config, target, error=f"failed {target.identity}")
            for target in targets
        ]
        for future in futures:
            future.result()

    assert set(backup.catalog(config)["attempts"]) == {target.identity for target in targets}


def test_failed_refresh_cannot_overwrite_a_newer_success(config, monkeypatch):
    original_lock = backup.lock
    publishing_failure = Event()
    release_failure = Event()
    second_listing = Event()
    failed_thread = []

    @contextmanager
    def locking(path, **kwargs):
        if path.name == "backup-catalog.lock" and failed_thread == [get_ident()]:
            publishing_failure.set()
            assert release_failure.wait(2)
        with original_lock(path, **kwargs):
            yield

    def restic(*args, **kwargs):
        if not failed_thread:
            failed_thread.append(get_ident())
            raise ResticError("older refresh failed")
        second_listing.set()
        return Result(("restic",), 0, "[]", "")

    monkeypatch.setattr(backup, "lock", locking)
    monkeypatch.setattr(backup, "_restic", restic)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(backup.refresh, config)
        try:
            assert publishing_failure.wait(1)
            second = pool.submit(backup.refresh, config)
            assert not second_listing.wait(0.2)
        finally:
            release_failure.set()
        with pytest.raises(ResticError, match="older refresh failed"):
            first.result(timeout=2)
        assert second.result(timeout=2)["refresh_error"] is None

    assert backup.catalog(config)["checked_at"]
    assert backup.catalog(config)["refresh_error"] is None


def test_create_all_reports_refresh_failure_and_empty_host_skips_refresh(config, monkeypatch):
    monkeypatch.setattr(
        backup, "create", lambda *a, **kw: {"backup": "backup-1", "snapshot": "snapshot-1"}
    )
    calls = []

    def refresh(current):
        calls.append(current)
        raise ResticError("inventory unavailable")

    monkeypatch.setattr(backup, "refresh", refresh)
    values = backup.create_all(config)
    assert values["host/repository"] == {"ok": False, "error": "inventory unavailable"}
    assert all(values[target.identity]["ok"] for target in config.databases)
    assert backup.create_all(replace(config, projects=())) == {}
    assert calls == [config]


def _persistence(*, running=0, saved=100, status="ok", saves=1, rewriting=0):
    return {
        "rdb_bgsave_in_progress": str(running),
        "rdb_last_save_time": str(saved),
        "rdb_last_bgsave_status": status,
        "rdb_saves": str(saves),
        "aof_rewrite_in_progress": str(rewriting),
    }


def _rdb(config, monkeypatch, engine, states, *, reply="Background saving started", bad=False):
    target = config.select("app-test-01/kv")
    target = replace(target, settings=defaults("kv", target.settings.data_root, engine))
    _ready(config, target)
    calls = []
    pending = iter(states)
    current = {}
    clock = [0]

    def sleep(seconds):
        clock[0] += 60

    monkeypatch.setattr(
        kv, "time", SimpleNamespace(monotonic=lambda: clock[0], time=lambda: 102, sleep=sleep)
    )

    def execute(container, args, **kwargs):
        nonlocal current
        assert kwargs["env"] == {f"{engine.upper()}CLI_AUTH": target.credentials.password}
        command = args[2:]
        calls.append(command)
        if command == ["INFO", "persistence"]:
            current = next(pending, current)
            out = "\n".join(f"{key}:{value}" for key, value in current.items())
        elif command == ["LASTSAVE"]:
            out = current["rdb_last_save_time"]
        elif command[0] == "BGSAVE":
            out = reply
        elif command == ["INFO", "server"]:
            out = "redis_version:7.2.5\n" + ("valkey_version:9.1.3\n" if engine == "valkey" else "")
        elif command == ["INFO", "keyspace"]:
            out = "db0:keys=2,expires=0\n"
        else:
            pytest.fail(f"unexpected command: {command}")
        return Result(tuple(args), 0, out, "")

    def copy(source, dest, **kwargs):
        calls.append("copy")
        dest.write_bytes(b"RDB snapshot")

    def check(args, **kwargs):
        calls.append(args)
        if bad:
            raise CommandError("invalid RDB checksum")
        return Result(tuple(args), 0, "OK", "")

    monkeypatch.setattr(kv.docker, "exec", execute)
    monkeypatch.setattr(kv.docker, "copy", copy)
    monkeypatch.setattr(kv, "run", check)
    monkeypatch.setattr(backup, "_handoff", lambda *args: None)
    monkeypatch.setattr(backup, "_upload", lambda *args: calls.append("upload") or "snapshot")
    return target, calls


@pytest.mark.parametrize("engine", ["redis", "valkey"])
def test_rdb_backup_waits_for_existing_save_then_checks_fresh_snapshot(config, monkeypatch, engine):
    target, calls = _rdb(
        config,
        monkeypatch,
        engine,
        [
            _persistence(running=1, saved=99),
            _persistence(saved=100),
            _persistence(running=1, saves=2),
            _persistence(saved=101, saves=2),
        ],
    )
    created = backup.create(config, target)
    record = backup.manifest_check(created["folder"])
    assert calls[:3] == [["INFO", "persistence"], ["INFO", "persistence"], ["LASTSAVE"]]
    assert calls[3] == (["BGSAVE", "SCHEDULE"] if engine == "valkey" else ["BGSAVE"])
    checker = next(call for call in calls if isinstance(call, list) and call[0] == "docker")
    assert checker[-3:] == [target.image, f"{engine}-check-rdb", "/dump.rdb"]
    assert calls.index("copy") > 5
    assert calls[-1] == "upload"
    assert record["format"] == f"{engine}-rdb-v1"
    assert record["engine"] == engine
    assert record["version"] == ("9.1.3" if engine == "valkey" else "7.2.5")
    record["format"] = "redis-rdb-v1" if engine == "valkey" else "valkey-rdb-v1"
    backup.manifest_write(created["folder"], record)
    with pytest.raises(BackupError, match="format does not match"):
        backup.manifest_check(created["folder"])


def test_valkey_scheduled_save_waits_through_aof_rewrite_and_old_failure(config, monkeypatch):
    target, calls = _rdb(
        config,
        monkeypatch,
        "valkey",
        [
            _persistence(status="err", rewriting=1),
            _persistence(status="err", rewriting=1),
            _persistence(status="err"),
            _persistence(running=1, status="err", saves=2),
            _persistence(saved=101, saves=2),
        ],
        reply="Background saving scheduled",
    )
    created = backup.create(config, target)
    assert backup.manifest_check(created["folder"])["format"] == "valkey-rdb-v1"
    assert calls[:3] == [["INFO", "persistence"], ["LASTSAVE"], ["BGSAVE", "SCHEDULE"]]
    assert calls[3:7] == [["INFO", "persistence"]] * 4
    assert calls[7] == "copy"


@pytest.mark.parametrize("failure", ["rejected", "failed", "scheduled-failed", "stale", "checker"])
def test_valkey_backup_failures_never_publish_or_upload(config, monkeypatch, failure):
    states = [_persistence()]
    reply = "Background saving started"
    if failure == "rejected":
        reply = "ERR Background save already in progress"
    elif failure in {"failed", "scheduled-failed"}:
        states.append(_persistence(status="err", saves=2))
        if failure == "scheduled-failed":
            states[0] = _persistence(status="err", rewriting=1)
            reply = "Background saving scheduled"
    elif failure == "stale":
        reply = "Background saving scheduled"
    else:
        states.append(_persistence(saved=101, saves=2))
    target, calls = _rdb(
        config, monkeypatch, "valkey", states, reply=reply, bad=failure == "checker"
    )
    with pytest.raises((BackupError, CommandError), match="failed|timed out|invalid RDB"):
        backup.create(config, target)
    assert "upload" not in calls
    if failure != "checker":
        assert "copy" not in calls
    assert list(config.paths.role_backups(target.project, target.role).iterdir()) == []
