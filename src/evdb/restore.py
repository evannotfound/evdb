from __future__ import annotations

import os
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import backup, compose, docker
from . import database as databases
from .config import Config, Database, MachineState, append_activity, load_state, require_no_orphans
from .engines import dragonfly, postgres, redis
from .errors import RestoreError
from .files import private_dir, write_json
from .images import image_major
from .lock import operation
from .log import write as log_write
from .run import run


@dataclass(frozen=True)
class Candidate:
    path: Path
    backup: str
    snapshot: str | None
    record: dict[str, Any]
    result: dict[str, Any]


def verify(
    config: Config,
    database: Database,
    folder: str | Path,
    *,
    snapshot: str | None = None,
    keep: bool = False,
    state: MachineState | None = None,
) -> dict[str, Any]:
    selected = Path(folder)
    _safe_backup(database, selected)
    source = selected.resolve()
    record = backup.manifest_check(source)
    current = state or load_state(config)
    require_no_orphans(config, current)
    _compatible(config, database, current, record)
    database.data.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = f"evdb-verify-{database.project}-{database.role}-{uuid.uuid4().hex[:10]}"
    work = database.data.parent / f".{database.data.name}.candidate-{uuid.uuid4().hex}"
    result = None
    failure = None
    try:
        result = _engine(database).restore(
            config,
            database,
            source,
            name,
            work,
            current,
            record,
        )
    except BaseException as exc:
        failure = exc
    try:
        docker.remove(name)
    except BaseException as exc:
        failure = RestoreError(f"{failure}; candidate cleanup failed: {exc}") if failure else exc
    if failure is not None:
        _remove_data(database, current, work, missing_ok=True)
        raise failure
    assert result is not None
    if work.is_symlink() or not work.is_dir():
        shutil.rmtree(work, ignore_errors=True)
        raise RestoreError("restore engine did not produce a safe candidate directory")
    candidate = Candidate(work, record["backup"], snapshot, record, result)
    if not keep:
        _remove_data(database, current, work)
    return {"candidate": candidate if keep else None, "result": result, "record": record}


def restore(
    config: Config,
    database: Database,
    value: str | None = None,
    *,
    yes: bool = False,
    confirm=None,
    state: MachineState | None = None,
) -> dict[str, Any]:
    if not database.durable:
        raise RestoreError(f"restore is disabled for cache database {database.identity}")
    current = state or load_state(config)
    activity_started = datetime.now(UTC).isoformat()
    started = time.monotonic()
    log_write(
        "restore_operation",
        host=config.host.id,
        project=database.project,
        role=database.role,
        engine=database.engine,
        command="restore",
        step="start",
        result="started",
        backup=value or "latest",
    )
    with operation(config, database, timeout=config.host.timeouts["restore"]):
        staging = None
        candidate = None
        transaction = None
        mutated = False
        try:
            selected = backup.select(config, database, value)
            folder, staging = backup.materialize(config, database, selected)
            checked = verify(
                config,
                database,
                folder,
                snapshot=selected.get("snapshot"),
                keep=True,
                state=current,
            )
            candidate = checked["candidate"]
            if not isinstance(candidate, Candidate):
                raise RestoreError("verification did not produce a restore candidate")
            if candidate.backup != selected["backup"] or candidate.snapshot != selected.get(
                "snapshot"
            ):
                raise RestoreError("verified candidate identity does not match the selected backup")
            _same_filesystem(candidate.path, database.data)
            safety = backup.create(
                config,
                database,
                purpose="safety",
                lock_held=True,
                state=current,
            )
            snapshot = safety.get("snapshot")
            if not snapshot:
                raise RestoreError("safety backup upload did not produce a snapshot")
            preview = {
                "host": config.host.id,
                "database": database.identity,
                "backup": selected["backup"],
                "snapshot": selected.get("snapshot"),
                "safety_snapshot": snapshot,
                "live": str(database.data),
                "candidate": str(candidate.path),
                "outage": "database unavailable during data directory exchange and startup",
            }
            approved = yes or (confirm is not None and bool(confirm(preview)))
            if not approved:
                _remove_data(database, current, candidate.path, missing_ok=True)
                log_write(
                    "restore_operation",
                    host=config.host.id,
                    project=database.project,
                    role=database.role,
                    engine=database.engine,
                    command="restore",
                    step="confirmation",
                    result="cancelled",
                    backup=selected["backup"],
                    duration=round(time.monotonic() - started, 3),
                )
                return {**preview, "status": "cancelled"}

            transaction = private_dir(
                config.paths.restores / f"{database.project}-{database.role}-{uuid.uuid4().hex}"
            )
            prior = database.data.with_name(f".{database.data.name}.prior-{uuid.uuid4().hex}")
            failed = database.data.with_name(f".{database.data.name}.failed-{uuid.uuid4().hex}")
            if prior.exists() or prior.is_symlink() or failed.exists() or failed.is_symlink():
                raise RestoreError("restore transaction data paths already exist")
            write_json(
                transaction / "transaction.json",
                {
                    **preview,
                    "kind": "restore",
                    "phase": "stopping",
                    "prior": str(prior),
                    "failed": str(failed),
                    "recovery": "run evdb host check before retrying restore",
                },
            )
            stopped = False
            try:
                try:
                    _compose(config, database, "stop")
                except KeyboardInterrupt, SystemExit:
                    stopped = True
                    raise
                stopped = True
                _exchange(database.data, candidate.path, prior)
                mutated = True
                write_json(
                    transaction / "transaction.json",
                    {
                        **preview,
                        "kind": "restore",
                        "phase": "starting",
                        "prior": str(prior),
                        "failed": str(failed),
                        "recovery": "run evdb host check before retrying restore",
                    },
                )
                _compose(config, database, "up", "-d", "--remove-orphans")
                databases.health(config, database, state=current)
            except BaseException as exc:
                mutated = mutated or prior.exists()
                if not mutated:
                    if stopped:
                        try:
                            _compose(config, database, "up", "-d", "--remove-orphans")
                            databases.health(config, database, state=current)
                        except BaseException as recovery_error:
                            raise RestoreError(
                                "restore swap failed and live service recovery failed; "
                                f"live={database.data} transaction={transaction}"
                            ) from recovery_error
                    shutil.rmtree(transaction, ignore_errors=True)
                    raise
                recovery_error = _recover(config, database, prior, failed, current)
                append_activity(
                    config,
                    command="restore",
                    database=database,
                    changed=("data",),
                    result="failed",
                    recovery="failed" if recovery_error else "recovered",
                    started=activity_started,
                    backup=selected["backup"],
                    safety_snapshot=snapshot,
                )
                if recovery_error:
                    protected_prior = prior if prior.exists() else database.data
                    protected_failed = (
                        failed
                        if failed.exists()
                        else candidate.path
                        if candidate.path.exists()
                        else database.data
                        if prior.exists() and database.data.exists()
                        else failed
                    )
                    raise RestoreError(
                        "restore and automatic recovery failed; "
                        f"prior={protected_prior} failed={protected_failed} "
                        f"transaction={transaction}"
                    ) from exc
                if candidate.path.exists():
                    _remove_data(database, current, candidate.path, missing_ok=True)
                shutil.rmtree(transaction, ignore_errors=True)
                detail = f"failed={failed}" if failed.exists() else "unpromoted candidate cleaned"
                raise RestoreError(
                    f"restored data failed health; prior data recovered; {detail}"
                ) from exc
            _remove_data(database, current, prior)
            shutil.rmtree(transaction)
            append_activity(
                config,
                command="restore",
                database=database,
                changed=("data",),
                result="success",
                recovery=None,
                started=activity_started,
                backup=selected["backup"],
                safety_snapshot=snapshot,
            )
            log_write(
                "restore_operation",
                host=config.host.id,
                project=database.project,
                role=database.role,
                engine=database.engine,
                command="restore",
                step="complete",
                result="success",
                backup=selected["backup"],
                snapshot=selected.get("snapshot"),
                safety_snapshot=snapshot,
                duration=round(time.monotonic() - started, 3),
            )
            return {**preview, "status": "healthy", "result": checked["result"]}
        except BaseException as exc:
            log_write(
                "restore_operation",
                host=config.host.id,
                project=database.project,
                role=database.role,
                engine=database.engine,
                command="restore",
                step="recovery" if mutated else "prepare",
                result="failed",
                backup=value or "latest",
                duration=round(time.monotonic() - started, 3),
                error=str(exc),
            )
            if candidate is not None and candidate.path.exists() and not mutated:
                _remove_data(database, current, candidate.path, missing_ok=True)
            raise
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)


def _recover(
    config: Config,
    database: Database,
    prior: Path,
    failed: Path,
    state: MachineState,
) -> BaseException | None:
    try:
        _compose(config, database, "stop")
        if database.data.exists():
            if database.data.is_symlink() or not database.data.is_dir() or failed.exists():
                raise RestoreError("failed restore data path is unsafe")
            database.data.replace(failed)
        if prior.is_symlink() or not prior.is_dir() or database.data.exists():
            raise RestoreError("prior restore data path is unsafe")
        prior.replace(database.data)
        _compose(config, database, "up", "-d", "--remove-orphans")
        databases.health(config, database, state=state)
        return None
    except BaseException as exc:
        return exc


def _exchange(live: Path, candidate: Path, prior: Path) -> None:
    live.replace(prior)
    candidate.replace(live)


def _compose(config: Config, database: Database, *args: str, check: bool = True) -> None:
    run(
        compose.command(database.compose, database.compose_project, *args),
        timeout=config.host.timeouts["command"],
        check=check,
    )


def _compatible(
    config: Config,
    database: Database,
    state: MachineState,
    record: dict[str, Any],
) -> None:
    expected = {
        "host": config.host.id,
        "project": database.project,
        "role": database.role,
        "engine": database.engine,
    }
    for key, value in expected.items():
        if record.get(key) != value:
            raise RestoreError(f"backup {key} does not match {value}")
    expected_format = {
        "postgres": "postgres-custom-v1",
        "redis": "redis-rdb-v1",
        "dragonfly": "dragonfly-dfs-v1",
    }[database.engine]
    if record.get("format") != expected_format:
        raise RestoreError(f"backup format does not match {expected_format}")
    role = state.roles.get(database.identity)
    if role is None or not role.installed or role.engine != database.engine:
        raise RestoreError("target database identity is not installed with the selected engine")
    primary = role.images.get("primary")
    if primary is None or primary.source != database.image:
        raise RestoreError("target database image state does not match its configuration")
    engine_version = str(record.get("version", ""))
    if database.engine == "dragonfly":
        engine_version = engine_version.removeprefix("df-")
    majors = {
        "backup source": image_major(record.get("source_image", "")),
        "backup image": image_major(record.get("image", "")),
        "backup engine": image_major(f"engine:{engine_version}"),
        "target source": image_major(database.image),
        "target state": image_major(primary.source),
    }
    if any(value is None for value in majors.values()) or len(set(majors.values())) != 1:
        detail = ", ".join(f"{name}={value}" for name, value in majors.items())
        raise RestoreError(f"backup and target engine majors must match ({detail})")


def _same_filesystem(candidate: Path, live: Path) -> None:
    if candidate.is_symlink() or not candidate.is_dir():
        raise RestoreError(f"restore candidate directory is missing or unsafe: {candidate}")
    if live.is_symlink() or not live.is_dir():
        raise RestoreError(f"live data directory is missing or unsafe: {live}")
    if live.parent.is_symlink() or candidate.parent.resolve() != live.parent.resolve():
        raise RestoreError("restore candidate and live data must be sibling directories")
    if not candidate.name.startswith(f".{live.name}.candidate-"):
        raise RestoreError("restore candidate identity does not match the live data directory")
    if os.path.samefile(candidate, live):
        raise RestoreError("restore candidate must not be the live data directory")
    if (
        os.stat(candidate, follow_symlinks=False).st_dev
        != os.stat(live.parent, follow_symlinks=False).st_dev
    ):
        raise RestoreError("restore candidate and live data must share one filesystem")


def _safe_backup(database: Database, folder: Path) -> None:
    if folder.is_symlink() or not folder.is_dir():
        raise RestoreError(f"backup folder does not exist or is unsafe: {folder}")
    folder = folder.resolve()
    live = database.data.resolve()
    if folder == live or live in folder.parents or folder in live.parents:
        raise RestoreError("restore path overlaps live data")


def _remove_data(
    database: Database,
    state: MachineState,
    path: Path,
    *,
    missing_ok: bool = False,
) -> None:
    if not path.exists():
        if missing_ok:
            return
        raise RestoreError(f"restore data path is missing: {path}")
    parent = database.data.parent.resolve()
    if path.is_symlink() or path.parent.resolve() != parent or path == database.data:
        raise RestoreError(f"restore data cleanup path is unsafe: {path}")
    try:
        shutil.rmtree(path)
        return
    except PermissionError:
        pass
    image = state.roles[database.identity].images["primary"].image
    run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--security-opt",
            "no-new-privileges",
            "--volume",
            f"{parent}:/work",
            "--entrypoint",
            "rm",
            image,
            "-rf",
            "--",
            f"/work/{path.name}",
        ],
        timeout=300,
    )
    if path.exists():
        raise RestoreError(f"restore data cleanup failed: {path}")


def _engine(database: Database):
    if database.engine == "postgres":
        return postgres
    if database.engine == "redis":
        return redis
    return dragonfly
