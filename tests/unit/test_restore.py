import json
from dataclasses import replace

import pytest

from evanovation_db import backup, restore
from evanovation_db.config import resolve_state, write_state
from evanovation_db.errors import CommandError, RestoreError
from evanovation_db.run import Result

DIGEST = "sha256:" + "a" * 64


class Engine:
    @staticmethod
    def restore(config, database, folder, name, work, state, record):
        del config, database, folder, name, state, record
        work.mkdir()
        (work / "restored").write_text("new")
        return {"objects": 1}


def _ready(config):
    state = resolve_state(config, resolver=lambda source: DIGEST)
    state = replace(
        state,
        roles={name: replace(role, installed=True) for name, role in state.roles.items()},
    )
    write_state(config, state)
    return state


def _backup(config, target, tmp_path, *, source_image="postgres:16", version="16.1"):
    folder = tmp_path / "selected-backup"
    folder.mkdir()
    (folder / "data.bin").write_text("backup")
    record = {
        "status": "complete",
        "backup": folder.name,
        "host": config.host.id,
        "project": target.project,
        "role": target.role,
        "engine": target.engine,
        "source_image": source_image,
        "image": f"postgres:16@{DIGEST}",
        "started": "2026-01-01T00:00:00+00:00",
        "finished": "2026-01-01T00:01:00+00:00",
        "version": version,
        "format": "test-v1",
        "purpose": "manual",
        "facts": {},
        "files": backup.manifest_files(folder, ["data.bin"]),
        "checks": ["size", "sha256", "postgres"],
        "upload": {
            "ok": True,
            "backup": folder.name,
            "snapshot": "selected-snapshot",
        },
    }
    backup.manifest_write(folder, record)
    return folder


def test_verify_uses_private_engine_candidate_and_cleans_it(config, tmp_path, monkeypatch):
    state = _ready(config)
    target = config.select("app-test-01/postgres")
    folder = _backup(config, target, tmp_path)
    monkeypatch.setattr(restore, "_engine", lambda database: Engine)
    monkeypatch.setattr(restore.docker, "remove", lambda name: None)

    result = restore.verify(config, target, folder, state=state)

    assert result["result"] == {"objects": 1}
    assert not list(target.data.parent.glob(".data.candidate-*"))


def test_verify_rejects_engine_major_before_start(config, tmp_path, monkeypatch):
    state = _ready(config)
    target = config.select("app-test-01/postgres")
    folder = _backup(config, target, tmp_path, source_image="postgres:17")
    monkeypatch.setattr(
        restore,
        "_engine",
        lambda database: (_ for _ in ()).throw(AssertionError("engine started")),
    )

    with pytest.raises(RestoreError, match="majors must match"):
        restore.verify(config, target, folder, state=state)


def test_verify_rejects_recorded_engine_version_mismatch_before_start(
    config, tmp_path, monkeypatch
):
    state = _ready(config)
    target = config.select("app-test-01/postgres")
    folder = _backup(config, target, tmp_path, version="17.1")
    monkeypatch.setattr(
        restore,
        "_engine",
        lambda database: (_ for _ in ()).throw(AssertionError("engine started")),
    )

    with pytest.raises(RestoreError, match="majors must match"):
        restore.verify(config, target, folder, state=state)


def test_verify_interrupt_removes_container_and_candidate(config, tmp_path, monkeypatch):
    state = _ready(config)
    target = config.select("app-test-01/postgres")
    folder = _backup(config, target, tmp_path)
    removed = []

    class InterruptedEngine:
        @staticmethod
        def restore(config, database, folder, name, work, state, record):
            del config, database, folder, name, state, record
            work.mkdir()
            (work / "partial").write_text("new")
            raise KeyboardInterrupt()

    monkeypatch.setattr(restore, "_engine", lambda database: InterruptedEngine)
    monkeypatch.setattr(restore.docker, "remove", removed.append)

    with pytest.raises(KeyboardInterrupt):
        restore.verify(config, target, folder, state=state)

    assert len(removed) == 1
    assert not list(target.data.parent.glob(".data.candidate-*"))


def _workflow(config, tmp_path, monkeypatch):
    state = _ready(config)
    target = config.select("app-test-01/postgres")
    target.data.mkdir(parents=True)
    (target.data / "live").write_text("old")
    folder = _backup(config, target, tmp_path)
    selected = {
        "backup": folder.name,
        "snapshot": "selected-snapshot",
        "local": True,
        "remote": True,
    }
    monkeypatch.setattr(backup, "select", lambda *args: selected)
    monkeypatch.setattr(backup, "materialize", lambda *args: (folder, None))

    def checked(*args, **kwargs):
        candidate = target.data.parent / ".data.candidate-test"
        candidate.mkdir()
        (candidate / "restored").write_text("new")
        return {
            "candidate": restore.Candidate(
                candidate,
                folder.name,
                "selected-snapshot",
                backup.manifest_read(folder),
                {"objects": 1},
            ),
            "result": {"objects": 1},
        }

    monkeypatch.setattr(restore, "verify", checked)
    monkeypatch.setattr(
        backup,
        "create",
        lambda *args, **kwargs: {"backup": "safety", "snapshot": "safety-snapshot"},
    )
    calls = []
    monkeypatch.setattr(
        restore,
        "run",
        lambda args, **kwargs: calls.append(args) or Result(tuple(args), 0, "", ""),
    )
    return state, target, calls


def test_live_restore_swaps_atomically_after_confirmation(config, tmp_path, monkeypatch):
    state, target, calls = _workflow(config, tmp_path, monkeypatch)
    monkeypatch.setattr(restore.databases, "health", lambda *args, **kwargs: None)

    result = restore.restore(config, target, "selected-backup", yes=True, state=state)

    assert result["status"] == "healthy"
    assert (target.data / "restored").read_text() == "new"
    assert not (target.data / "live").exists()
    assert not list(target.data.parent.glob(".data.prior-*"))
    assert calls[0][-1] == "stop"
    assert calls[1][-3:] == ["up", "-d", "--remove-orphans"]


def test_decline_keeps_live_data_and_removes_candidate(config, tmp_path, monkeypatch):
    state, target, calls = _workflow(config, tmp_path, monkeypatch)

    result = restore.restore(
        config,
        target,
        "selected-backup",
        confirm=lambda preview: False,
        state=state,
    )

    assert result["status"] == "cancelled"
    assert (target.data / "live").read_text() == "old"
    assert not list(target.data.parent.glob(".data.candidate-*"))
    assert calls == []


def test_failed_restore_recovers_prior_and_preserves_failed(config, tmp_path, monkeypatch):
    state, target, _calls = _workflow(config, tmp_path, monkeypatch)
    checks = 0

    def health(*args, **kwargs):
        nonlocal checks
        checks += 1
        if checks == 1:
            raise RestoreError("restored unhealthy")

    monkeypatch.setattr(restore.databases, "health", health)

    with pytest.raises(RestoreError, match="prior data recovered"):
        restore.restore(config, target, yes=True, state=state)

    assert (target.data / "live").read_text() == "old"
    assert list(target.data.parent.glob(".data.failed-*"))


def test_failed_automatic_recovery_preserves_paths_and_transaction(config, tmp_path, monkeypatch):
    state, target, _calls = _workflow(config, tmp_path, monkeypatch)
    monkeypatch.setattr(
        restore.databases,
        "health",
        lambda *args, **kwargs: (_ for _ in ()).throw(RestoreError("unhealthy")),
    )

    with pytest.raises(RestoreError, match="automatic recovery failed") as caught:
        restore.restore(config, target, yes=True, state=state)

    assert "prior=" in str(caught.value) and "failed=" in str(caught.value)
    assert (target.data / "live").read_text() == "old"
    assert list(target.data.parent.glob(".data.failed-*"))
    assert list(config.paths.restores.glob("*/transaction.json"))


def test_safety_upload_failure_never_stops_live_service(config, tmp_path, monkeypatch):
    state, target, calls = _workflow(config, tmp_path, monkeypatch)
    monkeypatch.setattr(
        backup,
        "create",
        lambda *args, **kwargs: (_ for _ in ()).throw(RestoreError("upload failed")),
    )

    with pytest.raises(RestoreError, match="upload failed"):
        restore.restore(config, target, yes=True, state=state)

    assert calls == []
    assert (target.data / "live").read_text() == "old"


def test_compose_stop_failure_aborts_before_any_rename_and_logs_redacted_identity(
    config, tmp_path, monkeypatch, capsys
):
    state, target, calls = _workflow(config, tmp_path, monkeypatch)

    def fail_stop(args, **kwargs):
        calls.append(args)
        if args[-1] == "stop":
            raise CommandError("token=restore-secret stop failed")
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(restore, "run", fail_stop)

    with pytest.raises(CommandError, match="stop failed"):
        restore.restore(config, target, yes=True, state=state)

    assert (target.data / "live").read_text() == "old"
    assert not list(target.data.parent.glob(".data.prior-*"))
    assert not list(target.data.parent.glob(".data.failed-*"))
    assert not list(target.data.parent.glob(".data.candidate-*"))
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    failed = next(
        event
        for event in events
        if event["event"] == "restore_operation" and event["result"] == "failed"
    )
    assert (failed["project"], failed["role"], failed["engine"]) == (
        target.project,
        target.role,
        target.engine,
    )
    assert "restore-secret" not in json.dumps(failed)


def test_recovery_stop_failure_preserves_exact_prior_and_failed_paths(
    config, tmp_path, monkeypatch
):
    state, target, calls = _workflow(config, tmp_path, monkeypatch)
    stops = 0

    def fail_recovery_stop(args, **kwargs):
        nonlocal stops
        calls.append(args)
        if args[-1] == "stop":
            stops += 1
            if stops == 2:
                raise CommandError("recovery stop failed")
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(restore, "run", fail_recovery_stop)
    monkeypatch.setattr(
        restore.databases,
        "health",
        lambda *args, **kwargs: (_ for _ in ()).throw(RestoreError("restored unhealthy")),
    )

    with pytest.raises(RestoreError, match="automatic recovery failed") as caught:
        restore.restore(config, target, yes=True, state=state)

    prior = next(target.data.parent.glob(".data.prior-*"))
    assert (prior / "live").read_text() == "old"
    assert (target.data / "restored").read_text() == "new"
    assert f"prior={prior}" in str(caught.value)
    assert f"failed={target.data}" in str(caught.value)
    assert list(config.paths.restores.glob("*/transaction.json"))
