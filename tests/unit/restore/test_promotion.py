import hashlib
import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from evanovation_db.config import Config
from evanovation_db.errors import CommandError, RestoreError
from evanovation_db.files import read_json
from evanovation_db.restore import promotion
from evanovation_db.run import Result

RESTORE_ID = "restore-20260725T120000000000Z-aaaaaaaaaaaa"
SNAPSHOT_TIME = "2026-07-25T11:00:00+00:00"


def test_create_persists_verified_candidate_and_immutable_record_without_touching_live_data(
    config, tmp_path, monkeypatch
):
    current, instance, release, release_manifest = _active(config, tmp_path, monkeypatch)
    live_marker = instance.data / "live"
    live_marker.write_text("unchanged")
    selected = []
    removed = []

    def select_snapshot(host, selected_instance, value):
        selected.append((selected_instance.selector, value))
        return {"id": "snapshot-exact", "time": SNAPSHOT_TIME}

    def restore_snapshot(host, selected_instance, snapshot, target):
        folder = target / "backup"
        folder.mkdir(parents=True)
        (folder / "dump.rdb").write_bytes(b"candidate-rdb")
        _write_backup(folder, current, instance)
        return folder

    def restore_engine(host, selected_instance, folder, name, candidate):
        assert selected_instance.image == instance.image
        assert candidate.parent == instance.data.parent
        assert candidate != instance.data
        (candidate / "dump.rdb").write_bytes((folder / "dump.rdb").read_bytes())
        return {"databases": {"0": 1}, "keys": 1, "samples": []}

    monkeypatch.setattr(promotion.restic, "select_snapshot", select_snapshot)
    monkeypatch.setattr(promotion.restic, "restore", restore_snapshot)
    monkeypatch.setattr(promotion.kv, "restore", restore_engine)
    access = []

    def finalize(container, args, **kwargs):
        access.append((container, args, kwargs))
        return Result(("docker",), 0, "", "")

    monkeypatch.setattr(promotion.docker, "exec", finalize)
    monkeypatch.setattr(promotion.docker, "remove", removed.append)

    result = promotion.create(current, instance, "latest")

    candidate = promotion._candidate_path(instance, result["restore_id"])
    record, backup = promotion._read_candidate_record(current, instance, result["restore_id"])
    record_root = promotion._candidate_record_dir(current, instance, result["restore_id"])
    assert result["promotable"] is True
    assert selected == [(instance.selector, "latest")]
    assert candidate.is_dir()
    assert (candidate / "dump.rdb").read_bytes() == b"candidate-rdb"
    assert live_marker.read_text() == "unchanged"
    assert record["identity"]["selector"] == instance.selector
    assert record["snapshot"]["id"] == "snapshot-exact"
    assert record["engine_image"] == instance.image
    assert record["source"] == {
        "engine": "redis",
        "version": "7.2.5",
        "major": 7,
        "image": instance.image,
    }
    assert record["verification"]["state"] == "verified"
    assert backup["instance"] == instance.id
    assert record_root.stat().st_mode & 0o222 == 0
    assert all(path.stat().st_mode & 0o222 == 0 for path in record_root.iterdir())
    assert len(removed) == 1
    assert not list((current.host.state_dir / "restore-downloads").iterdir())
    assert release.name == record["active_release"]
    assert release_manifest["databases"][instance.selector]["major"] == 7
    assert [item[1] for item in access] == [
        ["chmod", "0755", "/data"],
        ["chmod", "0644", "/data/dump.rdb"],
    ]
    assert all("chown" not in item[1] for item in access)


def test_create_failure_cleans_container_but_retains_failed_candidate_and_record(
    config, tmp_path, monkeypatch
):
    current, instance, _, _ = _active(config, tmp_path, monkeypatch)
    removed = []

    monkeypatch.setattr(
        promotion.restic,
        "select_snapshot",
        lambda *args: {"id": "snapshot-exact", "time": SNAPSHOT_TIME},
    )

    def restore_snapshot(host, selected_instance, snapshot, target):
        folder = target / "backup"
        folder.mkdir(parents=True)
        (folder / "dump.rdb").write_bytes(b"candidate-rdb")
        _write_backup(folder, current, instance)
        return folder

    def fail_restore(host, selected_instance, folder, name, candidate):
        (candidate / "dump.rdb").write_bytes(b"retained-failure")
        raise RestoreError("value verification failed")

    monkeypatch.setattr(promotion.restic, "restore", restore_snapshot)
    monkeypatch.setattr(promotion.kv, "restore", fail_restore)
    monkeypatch.setattr(
        promotion.docker,
        "exec",
        lambda *args, **kwargs: pytest.fail("candidate ownership must not be rewritten"),
    )
    monkeypatch.setattr(promotion.docker, "remove", removed.append)

    with pytest.raises(
        RestoreError, match=r"restore candidate restore-.*failed verification"
    ) as error:
        promotion.create(current, instance, "snapshot-exact")

    restore_id = str(error.value).split()[2]
    candidate = promotion._candidate_path(instance, restore_id)
    record, _ = promotion._read_candidate_record(current, instance, restore_id)
    assert (candidate / "dump.rdb").read_bytes() == b"retained-failure"
    assert record["verification"]["state"] == "failed"
    assert record["verification"]["promotable"] is False
    assert "value verification failed" in record["verification"]["error"]
    assert len(removed) == 1


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda record: record["identity"].update({"host": "other-host"}), "another typed"),
        (
            lambda record: record.update(
                {"created_at": (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()}
            ),
            "stale",
        ),
        (
            lambda record: record["verification"].update(
                {"state": "failed", "promotable": False, "error": "failed"}
            ),
            "not promotable",
        ),
        (lambda record: record.update({"active_release": "release-other"}), "another active"),
        (lambda record: record.update({"target_major": 8}), "incompatible"),
    ],
)
def test_plan_rejects_invalid_candidate_state_before_stop(
    config, tmp_path, monkeypatch, change, message
):
    current, instance, release, _ = _active(config, tmp_path, monkeypatch)
    record = _candidate_record(current, instance, release)
    change(record)
    _store_record(current, instance, record)

    with pytest.raises(RestoreError, match=message):
        promotion.plan(current, instance, RESTORE_ID)


def test_plan_rejects_changed_manifest_incomplete_data_and_other_filesystem(
    config, tmp_path, monkeypatch
):
    current, instance, release, _ = _active(config, tmp_path, monkeypatch)
    record = _candidate_record(current, instance, release)
    _store_record(current, instance, record)
    root = promotion._candidate_record_dir(current, instance, RESTORE_ID)
    manifest_path = root / "backup.json"
    root.chmod(0o700)
    manifest_path.chmod(0o600)
    manifest_path.write_text(manifest_path.read_text() + " ")
    manifest_path.chmod(0o400)
    root.chmod(0o500)

    with pytest.raises(RestoreError, match="manifest hash"):
        promotion.plan(current, instance, RESTORE_ID)

    _remove_record(root)
    record = _candidate_record(current, instance, release)
    _store_record(current, instance, record)
    candidate = promotion._candidate_path(instance, RESTORE_ID)
    (candidate / "dump.rdb").unlink()
    with pytest.raises(RestoreError, match="incomplete"):
        promotion.plan(current, instance, RESTORE_ID)

    (candidate / "dump.rdb").write_bytes(b"candidate")
    original_stat = type(candidate).stat

    def different_device(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == candidate:
            fields = list(value)
            fields[2] = value.st_dev + 1
            return os.stat_result(fields)
        return value

    monkeypatch.setattr(type(candidate), "stat", different_device)
    with pytest.raises(RestoreError, match="different filesystems"):
        promotion.plan(current, instance, RESTORE_ID)


def test_compatibility_rejection_happens_before_compose_stop(config, tmp_path, monkeypatch):
    current, instance, release, _ = _active(config, tmp_path, monkeypatch)
    record = _candidate_record(current, instance, release)
    record["source"]["major"] = 8
    record["source"]["version"] = "8.0.0"
    _store_record(current, instance, record)
    monkeypatch.setattr(
        promotion,
        "run",
        lambda *args, **kwargs: pytest.fail("compatibility must be checked before Compose stop"),
    )

    with pytest.raises(RestoreError, match="unsafe redis restore"):
        promotion.promote(
            current,
            instance,
            {
                "restore_id": RESTORE_ID,
                "expected_release": release.name,
                "manifest_hash": record["manifest_hash"],
            },
        )


def test_promotion_atomically_swaps_data_and_retains_prior_path(config, tmp_path, monkeypatch):
    current, instance, release, _ = _active(config, tmp_path, monkeypatch)
    record = _candidate_record(current, instance, release)
    _store_record(current, instance, record)
    calls = []
    monkeypatch.setattr(promotion, "_stamp", lambda: "20260725T130000000000Z")
    monkeypatch.setattr(
        promotion,
        "run",
        lambda args, **kwargs: calls.append(args) or Result(tuple(args), 0, "", ""),
    )
    monkeypatch.setattr(promotion.deployment, "health", lambda *args: None)

    result = promotion.promote(current, instance, _payload(record, release))

    retained = instance.data.with_name(f"data.retained-20260725T130000000000Z-{RESTORE_ID}")
    assert (instance.data / "dump.rdb").read_bytes() == b"candidate"
    assert (retained / "live-marker").read_text() == "prior"
    assert result["retained"] == str(retained)
    assert calls[0][-1] == "stop"
    assert calls[1][-2:] == ["up", "-d"]
    assert promotion._candidate_record_dir(current, instance, RESTORE_ID).is_dir()
    assert promotion.retained(instance) == [str(retained)]
    state = read_json(current.host.state_dir / f"state/{instance.group}/{instance.id}.json")
    assert state["promotion"]["status"] == "active"
    assert state["promotion"]["retained"] == [str(retained)]


def test_failed_promotion_restores_prior_data_and_retains_failed_candidate(
    config, tmp_path, monkeypatch
):
    current, instance, release, _ = _active(config, tmp_path, monkeypatch)
    record = _candidate_record(current, instance, release)
    _store_record(current, instance, record)
    calls = []
    health_calls = 0

    def health(*args):
        nonlocal health_calls
        health_calls += 1
        if health_calls == 1:
            raise RestoreError("candidate health failed")

    monkeypatch.setattr(promotion, "_stamp", lambda: "20260725T130000000000Z")
    monkeypatch.setattr(
        promotion,
        "run",
        lambda args, **kwargs: calls.append(args) or Result(tuple(args), 0, "", ""),
    )
    monkeypatch.setattr(promotion.deployment, "health", health)

    with pytest.raises(RestoreError, match="prior data was restored and is healthy"):
        promotion.promote(current, instance, _payload(record, release))

    failed = instance.data.with_name(f"data.failed-20260725T130000000000Z-{RESTORE_ID}")
    assert (instance.data / "live-marker").read_text() == "prior"
    assert (failed / "dump.rdb").read_bytes() == b"candidate"
    assert health_calls == 2
    assert [call[-1] for call in calls].count("stop") == 2
    state = read_json(current.host.state_dir / f"state/{instance.group}/{instance.id}.json")
    assert state["promotion"]["status"] == "failed_recovered"
    assert state["promotion"]["severity"] == "error"


@pytest.mark.parametrize("failed_rename", [1, 2])
def test_swap_rename_failure_restores_prior_data_before_returning(
    config, tmp_path, monkeypatch, failed_rename
):
    current, instance, release, _ = _active(config, tmp_path, monkeypatch)
    record = _candidate_record(current, instance, release)
    _store_record(current, instance, record)
    real_rename = promotion._rename
    rename_calls = 0

    def rename(source, target):
        nonlocal rename_calls
        rename_calls += 1
        if rename_calls == failed_rename:
            raise OSError(f"forced rename {failed_rename} failure")
        real_rename(source, target)

    monkeypatch.setattr(promotion, "_stamp", lambda: "20260725T130000000000Z")
    monkeypatch.setattr(promotion, "_rename", rename)
    monkeypatch.setattr(
        promotion,
        "run",
        lambda args, **kwargs: Result(tuple(args), 0, "", ""),
    )
    monkeypatch.setattr(promotion.deployment, "health", lambda *args: None)

    with pytest.raises(RestoreError, match="prior data was restored and is healthy"):
        promotion.promote(current, instance, _payload(record, release))

    candidate = promotion._candidate_path(instance, RESTORE_ID)
    assert (instance.data / "live-marker").read_text() == "prior"
    assert (candidate / "dump.rdb").read_bytes() == b"candidate"
    assert not promotion.retained(instance)


def test_recovery_stop_failure_preserves_swapped_and_prior_paths_with_high_severity(
    config, tmp_path, monkeypatch
):
    current, instance, release, _ = _active(config, tmp_path, monkeypatch)
    record = _candidate_record(current, instance, release)
    _store_record(current, instance, record)
    stop_calls = 0

    def execute(args, **kwargs):
        nonlocal stop_calls
        if args[-1] == "stop":
            stop_calls += 1
            if stop_calls == 2:
                raise CommandError("recovery stop failed")
        return Result(tuple(args), 0, "", "")

    health_calls = 0

    def health(*args):
        nonlocal health_calls
        health_calls += 1
        raise RestoreError("candidate health failed")

    monkeypatch.setattr(promotion, "_stamp", lambda: "20260725T130000000000Z")
    monkeypatch.setattr(promotion, "run", execute)
    monkeypatch.setattr(promotion.deployment, "health", health)

    with pytest.raises(RestoreError, match="automatic recovery failed"):
        promotion.promote(current, instance, _payload(record, release))

    retained = instance.data.with_name(f"data.retained-20260725T130000000000Z-{RESTORE_ID}")
    assert (instance.data / "dump.rdb").read_bytes() == b"candidate"
    assert (retained / "live-marker").read_text() == "prior"
    state = read_json(current.host.state_dir / f"state/{instance.group}/{instance.id}.json")
    assert state["promotion"]["severity"] == "high"
    assert "recovery stop failed" in " ".join(state["promotion"]["recovery_steps"])


@pytest.mark.parametrize("failed_rename", [3, 4])
def test_recovery_rename_failure_preserves_every_existing_data_path(
    config, tmp_path, monkeypatch, failed_rename
):
    current, instance, release, _ = _active(config, tmp_path, monkeypatch)
    record = _candidate_record(current, instance, release)
    _store_record(current, instance, record)
    real_rename = promotion._rename
    rename_calls = 0

    def rename(source, target):
        nonlocal rename_calls
        rename_calls += 1
        if rename_calls == failed_rename:
            raise OSError(f"forced recovery rename {failed_rename} failure")
        real_rename(source, target)

    monkeypatch.setattr(promotion, "_stamp", lambda: "20260725T130000000000Z")
    monkeypatch.setattr(promotion, "_rename", rename)
    monkeypatch.setattr(
        promotion,
        "run",
        lambda args, **kwargs: Result(tuple(args), 0, "", ""),
    )
    monkeypatch.setattr(
        promotion.deployment,
        "health",
        lambda *args: (_ for _ in ()).throw(RestoreError("candidate health failed")),
    )

    with pytest.raises(RestoreError, match="automatic recovery failed"):
        promotion.promote(current, instance, _payload(record, release))

    retained = instance.data.with_name(f"data.retained-20260725T130000000000Z-{RESTORE_ID}")
    failed = instance.data.with_name(f"data.failed-20260725T130000000000Z-{RESTORE_ID}")
    if failed_rename == 3:
        assert (instance.data / "dump.rdb").read_bytes() == b"candidate"
        assert not failed.exists()
    else:
        assert not instance.data.exists()
        assert (failed / "dump.rdb").read_bytes() == b"candidate"
    assert (retained / "live-marker").read_text() == "prior"
    state = read_json(current.host.state_dir / f"state/{instance.group}/{instance.id}.json")
    assert state["promotion"]["severity"] == "high"
    assert state["promotion"]["locations"]["retained"] == str(retained)


def test_failed_recovery_records_high_severity_and_exact_protected_paths(
    config, tmp_path, monkeypatch
):
    current, instance, release, _ = _active(config, tmp_path, monkeypatch)
    record = _candidate_record(current, instance, release)
    _store_record(current, instance, record)
    monkeypatch.setattr(promotion, "_stamp", lambda: "20260725T130000000000Z")
    monkeypatch.setattr(
        promotion,
        "run",
        lambda args, **kwargs: Result(tuple(args), 0, "", ""),
    )
    monkeypatch.setattr(
        promotion.deployment,
        "health",
        lambda *args: (_ for _ in ()).throw(RestoreError("all health checks failed")),
    )

    with pytest.raises(RestoreError, match="automatic recovery failed.*protected locations"):
        promotion.promote(current, instance, _payload(record, release))

    failed = instance.data.with_name(f"data.failed-20260725T130000000000Z-{RESTORE_ID}")
    assert (instance.data / "live-marker").read_text() == "prior"
    assert (failed / "dump.rdb").read_bytes() == b"candidate"
    state = read_json(current.host.state_dir / f"state/{instance.group}/{instance.id}.json")
    event = state["promotion"]
    assert event["status"] == "recovery_failed"
    assert event["severity"] == "high"
    assert event["locations"]["live"] == str(instance.data)
    assert event["locations"]["failed"] == str(failed)
    assert str(instance.data) in " ".join(event["recovery_steps"])
    assert str(failed) in " ".join(event["recovery_steps"])


def _active(config, tmp_path, monkeypatch):
    base = config.get("kv", "xai-server-prod-01")
    data_root = tmp_path / "data-root"
    host = replace(config.host, data_root=data_root)
    instance = replace(base, data=data_root / "kv" / base.id / "data")
    instance.data.mkdir(parents=True)
    (instance.data / "live-marker").write_text("prior")
    current = Config(host, (instance,))
    release = tmp_path / "opt/releases/release-active"
    compose = release / "compose/kv/database.json"
    compose.parent.mkdir(parents=True)
    compose.write_text("{}")
    release_manifest = {
        "host": host.id,
        "databases": {instance.selector: {"engine": "redis", "image": instance.image, "major": 7}},
    }
    monkeypatch.setattr(
        promotion,
        "_active_database",
        lambda selected_config, selected_instance: (
            release,
            release_manifest,
            current,
            instance,
        ),
    )
    monkeypatch.setattr(
        promotion.deployment,
        "compose_path",
        lambda selected_release, selected_manifest, selector: compose,
    )
    return current, instance, release, release_manifest


def _candidate_record(config, instance, release):
    candidate = promotion._candidate_path(instance, RESTORE_ID)
    candidate.mkdir(exist_ok=True)
    (candidate / "dump.rdb").write_bytes(b"candidate")
    backup = _backup_data(config, instance)
    manifest_text = json.dumps(backup, sort_keys=True, indent=2) + "\n"
    return {
        "version": promotion.VERSION,
        "restore_id": RESTORE_ID,
        "identity": {
            "host": config.host.id,
            "selector": instance.selector,
            "group": instance.group,
            "instance": instance.id,
            "engine": instance.engine,
        },
        "snapshot": {"id": "snapshot-exact", "time": SNAPSHOT_TIME},
        "manifest_hash": hashlib.sha256(manifest_text.encode()).hexdigest(),
        "engine_image": instance.image,
        "source": {
            "engine": instance.engine,
            "version": "7.2.5",
            "major": 7,
            "image": instance.image,
        },
        "target_major": 7,
        "active_release": release.name,
        "candidate_path": str(candidate),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verification": {
            "state": "verified",
            "time": datetime.now(timezone.utc).isoformat(),
            "promotable": True,
            "error": None,
        },
    }


def _store_record(config, instance, record):
    backup = _backup_data(config, instance)
    backup.update(
        {
            "engine": record["source"]["engine"],
            "version": record["source"]["version"],
            "image": record["source"]["image"],
        }
    )
    text = json.dumps(backup, sort_keys=True, indent=2) + "\n"
    record["manifest_hash"] = hashlib.sha256(text.encode()).hexdigest()
    promotion._write_candidate_record(config, instance, record, text)


def _write_backup(folder, config, instance):
    data = _backup_data(config, instance)
    data["files"] = [
        {
            "name": "dump.rdb",
            "size": (folder / "dump.rdb").stat().st_size,
            "sha256": hashlib.sha256((folder / "dump.rdb").read_bytes()).hexdigest(),
        }
    ]
    (folder / "backup.json").write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")


def _backup_data(config, instance):
    return {
        "status": "complete",
        "host": config.host.id,
        "group": instance.group,
        "instance": instance.id,
        "engine": instance.engine,
        "image": instance.image,
        "version": "7.2.5",
        "facts": {"databases": {"0": 1}, "keys": 1, "samples": []},
        "files": [],
    }


def _payload(record, release):
    return {
        "restore_id": RESTORE_ID,
        "expected_release": release.name,
        "manifest_hash": record["manifest_hash"],
    }


def _remove_record(root):
    for path in root.iterdir():
        path.chmod(0o600)
    root.chmod(0o700)
    for path in root.iterdir():
        path.unlink()
    root.rmdir()
