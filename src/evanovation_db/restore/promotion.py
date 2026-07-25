from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .. import deployment, docker, manifest, restic
from ..config import Config, Instance, load
from ..errors import Error, RestoreError
from ..files import private_dir, read_json, write_json, write_text
from ..lifecycle import redact_logs
from ..lock import operation
from ..run import run
from . import kv, postgres
from .main import _compatible, _defer_interrupts, _interrupts, _source

VERSION = 1
PROMOTION_VERSION = 1
_RESTORE_ID = re.compile(r"restore-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}")
_PROMOTION_ID = re.compile(r"promote-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}")
_RELEASE_ID = re.compile(r"release-[A-Za-z0-9][A-Za-z0-9.-]{0,126}")
_HASH = re.compile(r"[0-9a-f]{64}")
_LOCKED_IMAGE = re.compile(r".+@sha256:[0-9a-f]{64}")


def create(config: Config, instance: Instance, snapshot: str) -> dict[str, Any]:
    _validate_snapshot_value(snapshot)
    timeout = config.host.timeouts["restore"]
    with operation(config.host, instance, timeout=timeout), _interrupts():
        release, release_manifest, active_config, active_instance = _active_database(
            config, instance
        )
        selected = restic.select_snapshot(config.host, active_instance, snapshot)
        snapshot_id = _required_string(selected.get("id"), "selected snapshot id")
        snapshot_time = _required_string(selected.get("time"), "selected snapshot time")
        root = private_dir(config.host.state_dir / "restore-downloads")
        staging = Path(tempfile.mkdtemp(prefix="snapshot-", dir=root))
        try:
            backup = restic.restore(
                config.host,
                active_instance,
                snapshot_id,
                staging / "files",
            )
            backup_data = manifest.check(backup)
            _backup_identity(config, active_instance, backup_data)
            source = _source(backup_data)
            target_major = _target_major(release_manifest, active_instance)
            _compatible(source, active_instance, target_major)
            manifest_text = (backup / manifest.NAME).read_text()
            manifest_hash = hashlib.sha256(manifest_text.encode()).hexdigest()
        except BaseException:
            with _defer_interrupts():
                shutil.rmtree(staging)
            raise

        restore_id, candidate = _new_candidate(config, active_instance)
        name = f"evdb-{restore_id}"
        if name in {item.container for item in active_config.instances}:
            with _defer_interrupts():
                shutil.rmtree(staging)
            raise RestoreError("restore candidate container overlaps a live container")
        candidate.mkdir(mode=0o700)
        created_at = _now()
        result = None
        failure: BaseException | None = None
        try:
            if active_instance.engine == "postgres":
                result = postgres.restore(
                    config.host,
                    active_instance,
                    backup,
                    name,
                    candidate,
                )
            else:
                result = kv.restore(
                    config.host,
                    active_instance,
                    backup,
                    name,
                    candidate,
                )
        except BaseException as exc:
            failure = exc
        if failure is None:
            data_path = (
                "/var/lib/postgresql/data" if active_instance.engine == "postgres" else "/data"
            )
            marker = "PG_VERSION" if active_instance.engine == "postgres" else "dump.rdb"
            try:
                for mode, path in (("0755", data_path), ("0644", f"{data_path}/{marker}")):
                    access = docker.exec(
                        name,
                        ["chmod", mode, path],
                        user="0",
                        timeout=60,
                        check=False,
                    )
                    if access.code != 0:
                        raise RestoreError("restore candidate access finalization failed")
            except BaseException as exc:
                failure = _combined(None, exc, "candidate access finalization")
        try:
            with _defer_interrupts():
                docker.remove(name)
        except BaseException as exc:
            failure = _combined(failure, exc, "verification container cleanup")
        try:
            with _defer_interrupts():
                shutil.rmtree(staging)
        except BaseException as exc:
            failure = _combined(failure, exc, "snapshot staging cleanup")
        if failure is None:
            try:
                _complete(candidate, active_instance, target_major)
            except BaseException as exc:
                failure = exc

        verified_at = _now()
        record = {
            "version": VERSION,
            "restore_id": restore_id,
            "identity": {
                "host": config.host.id,
                "selector": active_instance.selector,
                "group": active_instance.group,
                "instance": active_instance.id,
                "engine": active_instance.engine,
            },
            "snapshot": {"id": snapshot_id, "time": snapshot_time},
            "manifest_hash": manifest_hash,
            "engine_image": active_instance.image,
            "source": source,
            "target_major": target_major,
            "active_release": release.name,
            "candidate_path": str(candidate),
            "created_at": created_at,
            "verification": {
                "state": "verified" if failure is None else "failed",
                "time": verified_at,
                "promotable": failure is None,
                "error": None if failure is None else _safe_error(failure),
            },
        }
        _write_candidate_record(config, active_instance, record, manifest_text)
        if failure is not None:
            raise RestoreError(
                f"restore candidate {restore_id} failed verification: {_safe_error(failure)}"
            ) from failure
        assert result is not None
        return {
            "restore_id": restore_id,
            "selector": active_instance.selector,
            "snapshot": snapshot_id,
            "created_at": created_at,
            "state": "verified",
            "promotable": True,
        }


def validate_create_request(payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) != {"snapshot"}:
        raise RestoreError("restore payload requires one snapshot field")
    _validate_snapshot_value(payload.get("snapshot"))


def validate_plan_request(payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) != {"restore_id"}:
        raise RestoreError("promotion plan payload requires one restore_id field")
    _validate_restore_id(payload.get("restore_id"))


def plan(config: Config, instance: Instance, restore_id: str) -> dict[str, Any]:
    _validate_restore_id(restore_id)
    with operation(
        config.host,
        instance,
        timeout=config.host.timeouts["command"],
    ):
        return _plan_locked(config, instance, restore_id)


def validate_request(payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) != {
        "restore_id",
        "expected_release",
        "manifest_hash",
    }:
        raise RestoreError("promotion payload fields are invalid")
    _validate_restore_id(payload.get("restore_id"))
    expected = payload.get("expected_release")
    if not isinstance(expected, str) or not _RELEASE_ID.fullmatch(expected):
        raise RestoreError("promotion expected release is invalid")
    value = payload.get("manifest_hash")
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise RestoreError("promotion manifest hash is invalid")


def promote(config: Config, instance: Instance, payload: dict[str, Any]) -> dict[str, Any]:
    validate_request(payload)
    timeout = config.host.timeouts["command"]
    with operation(config.host, instance, timeout=timeout), _interrupts():
        confirmed = _plan_locked(config, instance, payload["restore_id"])
        if (
            confirmed["active_release"] != payload["expected_release"]
            or confirmed["manifest_hash"] != payload["manifest_hash"]
        ):
            raise RestoreError("restore candidate changed after confirmation; plan promotion again")

        release, release_manifest, active_config, active_instance = _active_database(
            config, instance
        )
        compose = deployment.compose_path(release, release_manifest, active_instance.selector)
        stamp = _stamp()
        promotion_id = f"promote-{stamp}-{uuid.uuid4().hex[:12]}"
        live = active_instance.data
        candidate = Path(confirmed["candidate_path"])
        retained = live.with_name(f"{live.name}.retained-{stamp}-{payload['restore_id']}")
        failed = live.with_name(f"{live.name}.failed-{stamp}-{payload['restore_id']}")
        if retained.exists() or failed.exists():
            raise RestoreError("promotion retained data path already exists")
        command = [
            "docker",
            "compose",
            "-f",
            str(compose),
            "--project-name",
            active_instance.project,
        ]
        journal = {
            "version": PROMOTION_VERSION,
            "promotion_id": promotion_id,
            "restore_id": payload["restore_id"],
            "host": config.host.id,
            "selector": active_instance.selector,
            "snapshot": confirmed["snapshot"],
            "manifest_hash": confirmed["manifest_hash"],
            "release": release.name,
            "compose": str(compose),
            "live_path": str(live),
            "candidate_path": str(candidate),
            "retained_path": str(retained),
            "failed_path": str(failed),
            "started_at": _now(),
            "finished_at": None,
            "status": "starting",
            "severity": "info",
            "recovered": None,
            "error": None,
            "locations": _locations(live, candidate, retained, failed),
            "recovery_steps": [],
        }
        _write_promotion(config, active_instance, journal)
        try:
            run([*command, "stop"], timeout=timeout)
            journal["status"] = "stopped"
            _write_promotion(config, active_instance, journal)
            _rename(live, retained)
            journal["status"] = "live_retained"
            journal["locations"] = _locations(live, candidate, retained, failed)
            _write_promotion(config, active_instance, journal)
            _rename(candidate, live)
            journal["status"] = "candidate_active"
            journal["locations"] = _locations(live, candidate, retained, failed)
            _write_promotion(config, active_instance, journal)
            run([*command, "up", "-d"], timeout=timeout)
            deployment.health(active_config, active_instance)
        except BaseException as exc:
            with _defer_interrupts():
                _recover(
                    config,
                    active_config,
                    active_instance,
                    command,
                    journal,
                    live,
                    candidate,
                    retained,
                    failed,
                    exc,
                )
            raise AssertionError("promotion recovery must raise") from exc

        journal.update(
            {
                "finished_at": _now(),
                "status": "active",
                "severity": "info",
                "recovered": None,
                "locations": _locations(live, candidate, retained, failed),
            }
        )
        _finish_promotion(config, active_instance, journal)
        return {
            "selector": active_instance.selector,
            "restore_id": payload["restore_id"],
            "snapshot": confirmed["snapshot"],
            "release": release.name,
            "retained": str(retained),
        }


def retained(instance: Instance) -> list[str]:
    parent = instance.data.parent
    prefix = f"{instance.data.name}.retained-"
    if not parent.is_dir():
        return []
    return sorted(
        str(path)
        for path in parent.iterdir()
        if path.name.startswith(prefix) and path.is_dir() and not path.is_symlink()
    )


def _plan_locked(config: Config, instance: Instance, restore_id: str) -> dict[str, Any]:
    release, release_manifest, _, active_instance = _active_database(config, instance)
    record, backup_data = _read_candidate_record(config, active_instance, restore_id)
    _record_identity(config, active_instance, record)
    target_major = _target_major(release_manifest, active_instance)
    source = _source(backup_data)
    if record["source"] != source:
        raise RestoreError("restore candidate source does not match its immutable manifest")
    _compatible(source, active_instance, target_major)
    if record["target_major"] != target_major or record["engine_image"] != active_instance.image:
        raise RestoreError("restore candidate is incompatible with the active locked image")
    if record["active_release"] != release.name:
        raise RestoreError("restore candidate was created for another active release")
    verification = record["verification"]
    if verification["state"] != "verified" or verification["promotable"] is not True:
        raise RestoreError("restore candidate is not promotable")
    _fresh(config, record["created_at"])
    candidate = _candidate_path(active_instance, restore_id)
    if record["candidate_path"] != str(candidate):
        raise RestoreError("restore candidate path does not match its immutable identity")
    _backup_identity(config, active_instance, backup_data)
    _complete(candidate, active_instance, target_major)
    _same_filesystem(candidate, active_instance.data)
    return {
        "version": VERSION,
        "host": config.host.id,
        "selector": active_instance.selector,
        "restore_id": restore_id,
        "snapshot": record["snapshot"]["id"],
        "snapshot_time": record["snapshot"]["time"],
        "manifest_hash": record["manifest_hash"],
        "created_at": record["created_at"],
        "engine_image": record["engine_image"],
        "active_release": release.name,
        "candidate_path": str(candidate),
        "live_path": str(active_instance.data),
        "outage": True,
    }


def _active_database(
    config: Config, instance: Instance
) -> tuple[Path, dict[str, Any], Config, Instance]:
    current = deployment.active()
    if current is None:
        raise RestoreError("no active release is available")
    release, release_manifest = current
    if release_manifest["host"] != config.host.id:
        raise RestoreError("active release is for another host")
    try:
        database = release_manifest["databases"][instance.selector]
        active_config = load(release / "runtime")
        active_instance = active_config.select(instance.selector)
    except (KeyError, Error) as exc:
        raise RestoreError(f"database is absent from active release: {instance.selector}") from exc
    if (
        active_instance.engine != instance.engine
        or active_instance.data != instance.data
        or active_instance.image != instance.image
        or database.get("engine") != instance.engine
        or database.get("image") != instance.image
    ):
        raise RestoreError("active release database identity does not match runtime configuration")
    if not _LOCKED_IMAGE.fullmatch(active_instance.image):
        raise RestoreError("active release database image is not locked")
    return release, release_manifest, active_config, active_instance


def _backup_identity(config: Config, instance: Instance, data: dict[str, Any]) -> None:
    expected = {
        "host": config.host.id,
        "group": instance.group,
        "instance": instance.id,
        "engine": instance.engine,
    }
    for key, value in expected.items():
        if data.get(key) != value:
            raise RestoreError(f"backup {key} does not match {value}")


def _target_major(release_manifest: dict[str, Any], instance: Instance) -> int:
    try:
        value = release_manifest["databases"][instance.selector]["major"]
    except (KeyError, TypeError) as exc:
        raise RestoreError("active release engine major is unavailable") from exc
    if type(value) is not int or value < 1:
        raise RestoreError("active release engine major is invalid")
    return value


def _new_candidate(config: Config, instance: Instance) -> tuple[str, Path]:
    instance.data.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for _ in range(10):
        restore_id = f"restore-{_stamp()}-{uuid.uuid4().hex[:12]}"
        candidate = _candidate_path(instance, restore_id)
        record = _candidate_record_dir(config, instance, restore_id)
        if not candidate.exists() and not record.exists():
            return restore_id, candidate
    raise RestoreError("could not allocate a unique restore candidate id")


def _candidate_path(instance: Instance, restore_id: str) -> Path:
    _validate_restore_id(restore_id)
    return instance.data.parent / f".{restore_id}.candidate"


def _candidate_record_dir(config: Config, instance: Instance, restore_id: str) -> Path:
    _validate_restore_id(restore_id)
    return config.host.state_dir / "restores" / instance.group / instance.id / restore_id


def _write_candidate_record(
    config: Config,
    instance: Instance,
    record: dict[str, Any],
    manifest_text: str,
) -> None:
    restore_id = record["restore_id"]
    _validate_candidate_record(record, instance, restore_id)
    expected_hash = hashlib.sha256(manifest_text.encode()).hexdigest()
    if expected_hash != record["manifest_hash"]:
        raise RestoreError("restore candidate manifest hash changed before recording")
    target = _candidate_record_dir(config, instance, restore_id)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    partial = target.with_name(f".{restore_id}.partial")
    if target.exists() or partial.exists():
        raise RestoreError("restore candidate record already exists")
    partial.mkdir(mode=0o700)
    try:
        write_text(partial / manifest.NAME, manifest_text, mode=0o400)
        write_json(partial / "record.json", record, mode=0o400)
        partial.chmod(0o500)
        partial.rename(target)
    except BaseException:
        partial.chmod(0o700)
        raise


def _read_candidate_record(
    config: Config, instance: Instance, restore_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = _candidate_record_dir(config, instance, restore_id)
    if root.is_symlink() or not root.is_dir():
        raise RestoreError(f"restore candidate record is unavailable: {restore_id}")
    record_path = root / "record.json"
    manifest_path = root / manifest.NAME
    if not record_path.is_file() or not manifest_path.is_file():
        raise RestoreError("restore candidate record is incomplete")
    if root.stat().st_mode & 0o222 or any(
        path.stat().st_mode & 0o222 for path in (record_path, manifest_path)
    ):
        raise RestoreError("restore candidate record is not immutable")
    try:
        record = read_json(record_path)
        manifest_text = manifest_path.read_text()
        backup_data = json.loads(manifest_text)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RestoreError("restore candidate record is invalid") from exc
    _validate_candidate_record(record, instance, restore_id)
    if hashlib.sha256(manifest_text.encode()).hexdigest() != record["manifest_hash"]:
        raise RestoreError("restore candidate manifest hash does not match its record")
    if not isinstance(backup_data, dict):
        raise RestoreError("restore candidate manifest is invalid")
    return record, backup_data


def _validate_candidate_record(record: Any, instance: Instance, restore_id: str) -> None:
    fields = {
        "version",
        "restore_id",
        "identity",
        "snapshot",
        "manifest_hash",
        "engine_image",
        "source",
        "target_major",
        "active_release",
        "candidate_path",
        "created_at",
        "verification",
    }
    if (
        not isinstance(record, dict)
        or set(record) != fields
        or record.get("version") != VERSION
        or record.get("restore_id") != restore_id
        or not isinstance(record.get("manifest_hash"), str)
        or not _HASH.fullmatch(record["manifest_hash"])
        or not isinstance(record.get("engine_image"), str)
        or not _LOCKED_IMAGE.fullmatch(record["engine_image"])
        or type(record.get("target_major")) is not int
        or record["target_major"] < 1
        or not isinstance(record.get("active_release"), str)
        or not _RELEASE_ID.fullmatch(record["active_release"])
        or not isinstance(record.get("candidate_path"), str)
        or _time(record.get("created_at")) is None
    ):
        raise RestoreError("restore candidate record fields are invalid")
    identity = record["identity"]
    if (
        not isinstance(identity, dict)
        or set(identity)
        != {
            "host",
            "selector",
            "group",
            "instance",
            "engine",
        }
        or not all(isinstance(value, str) and value for value in identity.values())
    ):
        raise RestoreError("restore candidate typed identity is invalid")
    snapshot = record["snapshot"]
    if not isinstance(snapshot, dict) or set(snapshot) != {"id", "time"}:
        raise RestoreError("restore candidate snapshot identity is invalid")
    _validate_snapshot_value(snapshot.get("id"), allow_latest=False)
    if _time(snapshot.get("time")) is None:
        raise RestoreError("restore candidate snapshot time is invalid")
    source = record["source"]
    if not isinstance(source, dict) or set(source) != {"engine", "version", "major", "image"}:
        raise RestoreError("restore candidate source identity is invalid")
    if (
        not all(
            isinstance(source.get(key), str) and source[key]
            for key in ("engine", "version", "image")
        )
        or type(source.get("major")) is not int
        or source["major"] < 1
    ):
        raise RestoreError("restore candidate source identity is invalid")
    verification = record["verification"]
    if (
        not isinstance(verification, dict)
        or set(verification) != {"state", "time", "promotable", "error"}
        or verification.get("state") not in {"verified", "failed"}
        or _time(verification.get("time")) is None
        or not isinstance(verification.get("promotable"), bool)
        or (verification.get("error") is not None and not isinstance(verification["error"], str))
        or verification["promotable"] != (verification["state"] == "verified")
        or (verification["state"] == "verified") != (verification["error"] is None)
    ):
        raise RestoreError("restore candidate verification state is invalid")
    if record["candidate_path"] != str(_candidate_path(instance, restore_id)):
        raise RestoreError("restore candidate record path is invalid")


def _record_identity(config: Config, instance: Instance, record: dict[str, Any]) -> None:
    expected = {
        "host": config.host.id,
        "selector": instance.selector,
        "group": instance.group,
        "instance": instance.id,
        "engine": instance.engine,
    }
    if record["identity"] != expected:
        raise RestoreError("restore candidate belongs to another typed database identity")


def _fresh(config: Config, value: str) -> None:
    created = _time(value)
    assert created is not None
    now = datetime.now(timezone.utc)
    if created > now + timedelta(minutes=5):
        raise RestoreError("restore candidate creation time is in the future")
    if now - created > timedelta(days=config.host.restore_max_age_days):
        raise RestoreError("restore candidate is stale; create a new candidate")


def _complete(candidate: Path, instance: Instance, target_major: int) -> None:
    if candidate.is_symlink() or not candidate.is_dir():
        raise RestoreError("restore candidate data directory is unavailable")
    if instance.engine == "postgres":
        version = candidate / "PG_VERSION"
        try:
            major = int(version.read_text().strip().split(".", 1)[0])
        except (OSError, ValueError) as exc:
            raise RestoreError("Postgres restore candidate is incomplete") from exc
        if major != target_major:
            raise RestoreError(
                f"Postgres restore candidate major {major} does not match locked major "
                f"{target_major}"
            )
        return
    dump = candidate / "dump.rdb"
    if not dump.is_file() or dump.stat().st_size == 0:
        raise RestoreError("KV restore candidate is incomplete")


def _same_filesystem(candidate: Path, live: Path) -> None:
    if candidate.resolve() == live.resolve() or candidate.resolve() in live.resolve().parents:
        raise RestoreError("restore candidate overlaps live data")
    if candidate.parent.resolve() != live.parent.resolve():
        raise RestoreError("restore candidate is not a sibling of live data")
    if live.is_symlink() or not live.is_dir():
        raise RestoreError("live data directory is unavailable")
    if candidate.stat().st_dev != live.stat().st_dev:
        raise RestoreError("restore candidate and live data are on different filesystems")


def _rename(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise RestoreError(f"data directory is unavailable for atomic rename: {source}")
    if target.exists() or target.is_symlink():
        raise RestoreError(f"atomic rename target already exists: {target}")
    source.rename(target)


def _recover(
    config: Config,
    active_config: Config,
    instance: Instance,
    command: list[str],
    journal: dict[str, Any],
    live: Path,
    candidate: Path,
    retained_path: Path,
    failed: Path,
    cause: BaseException,
) -> None:
    failures = []
    try:
        run([*command, "stop"], timeout=config.host.timeouts["command"])
    except (Error, OSError) as exc:
        failures.append(f"stop promoted service: {_safe_error(exc)}")
    if not failures and retained_path.exists():
        if live.exists():
            try:
                _rename(live, failed)
            except (Error, OSError) as exc:
                failures.append(f"retain failed candidate: {_safe_error(exc)}")
        if not failures:
            try:
                _rename(retained_path, live)
            except (Error, OSError) as exc:
                failures.append(f"restore prior data path: {_safe_error(exc)}")
    elif not retained_path.exists() and not live.is_dir():
        failures.append("prior live data path is unavailable")
    if not failures:
        try:
            run([*command, "up", "-d"], timeout=config.host.timeouts["command"])
            deployment.health(active_config, instance)
        except (Error, OSError) as exc:
            failures.append(f"restart prior service: {_safe_error(exc)}")

    recovered = not failures
    journal.update(
        {
            "finished_at": _now(),
            "status": "failed_recovered" if recovered else "recovery_failed",
            "severity": "error" if recovered else "high",
            "recovered": recovered,
            "error": _safe_error(cause),
            "locations": _locations(live, candidate, retained_path, failed),
            "recovery_steps": []
            if recovered
            else _recovery_steps(live, candidate, retained_path, failed, journal["compose"]),
        }
    )
    if failures:
        journal["recovery_steps"].append("Automatic recovery failures: " + "; ".join(failures))
    _finish_promotion(config, instance, journal)
    if recovered:
        kept = str(failed if failed.exists() else candidate)
        raise RestoreError(
            f"promotion failed; prior data was restored and is healthy; failed candidate retained "
            f"at {kept}: {_safe_error(cause)}"
        ) from cause
    locations = ", ".join(
        f"{name}={path}" for name, path in journal["locations"].items() if path is not None
    )
    raise RestoreError(
        "promotion and automatic recovery failed; do not move or delete data; "
        f"protected locations: {locations}"
    ) from cause


def _write_promotion(config: Config, instance: Instance, journal: dict[str, Any]) -> None:
    promotion_id = journal.get("promotion_id")
    if not isinstance(promotion_id, str) or not _PROMOTION_ID.fullmatch(promotion_id):
        raise RestoreError("promotion journal identity is invalid")
    path = _promotion_path(config, instance, promotion_id)
    if path.is_file() and not path.stat().st_mode & 0o222:
        raise RestoreError("promotion journal is already final")
    write_json(path, journal, mode=0o600)


def _finish_promotion(config: Config, instance: Instance, journal: dict[str, Any]) -> None:
    _write_promotion(config, instance, journal)
    path = _promotion_path(config, instance, journal["promotion_id"])
    path.chmod(0o400)
    state_path = config.host.state_dir / "state" / instance.group / f"{instance.id}.json"
    state = read_json(state_path) if state_path.is_file() else {}
    if not isinstance(state, dict):
        state = {}
    state["promotion"] = {
        "id": journal["promotion_id"],
        "restore_id": journal["restore_id"],
        "status": journal["status"],
        "severity": journal["severity"],
        "time": journal["finished_at"],
        "retained": retained(instance),
        "locations": journal["locations"],
        "recovery_steps": journal["recovery_steps"],
    }
    errors = dict(state.get("errors", {})) if isinstance(state.get("errors"), dict) else {}
    if journal["status"] == "active":
        errors.pop("promote", None)
    else:
        errors["promote"] = {
            "command": "promote",
            "step": journal["status"],
            "time": journal["finished_at"],
            "message": journal["error"] or "promotion failed",
        }
    if errors:
        state["errors"] = errors
    else:
        state.pop("errors", None)
    write_json(state_path, state)


def _promotion_path(config: Config, instance: Instance, promotion_id: str) -> Path:
    return (
        config.host.state_dir / "promotions" / instance.group / instance.id / f"{promotion_id}.json"
    )


def _locations(
    live: Path, candidate: Path, retained_path: Path, failed: Path
) -> dict[str, str | None]:
    return {
        "live": str(live) if live.exists() else None,
        "candidate": str(candidate) if candidate.exists() else None,
        "retained": str(retained_path) if retained_path.exists() else None,
        "failed": str(failed) if failed.exists() else None,
    }


def _recovery_steps(
    live: Path,
    candidate: Path,
    retained_path: Path,
    failed: Path,
    compose: str,
) -> list[str]:
    return [
        "Do not delete, rename, or replace any listed data directory.",
        f"Inspect live data at {live}.",
        f"Inspect the original candidate path at {candidate}.",
        f"Inspect retained prior data at {retained_path}.",
        f"Inspect failed promoted data at {failed}.",
        f"Use the retained active release Compose file at {compose} for manual recovery.",
    ]


def _validate_snapshot_value(value: Any, *, allow_latest: bool = True) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or "/" in value
        or "\x00" in value
        or len(value) > 256
        or (not allow_latest and value == "latest")
    ):
        expected = "latest or an exact snapshot id" if allow_latest else "an exact snapshot id"
        raise RestoreError(f"restore snapshot must be {expected}")


def _validate_restore_id(value: Any) -> None:
    if not isinstance(value, str) or not _RESTORE_ID.fullmatch(value):
        raise RestoreError("restore candidate id is invalid")


def _required_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RestoreError(f"{name} is invalid")
    return value


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo is not None else result.replace(tzinfo=timezone.utc)


def _combined(
    first: BaseException | None,
    second: BaseException,
    step: str,
) -> BaseException:
    if first is None:
        return RestoreError(f"{step} failed: {_safe_error(second)}")
    return RestoreError(f"{_safe_error(first)}; {step} failed: {_safe_error(second)}")


def _safe_error(error: BaseException) -> str:
    return redact_logs(str(error)).replace("\n", " ")[:1000]


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
