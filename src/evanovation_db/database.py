from __future__ import annotations

import hashlib
import os
import shutil
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import compose, docker, secrets
from .config import (
    DEFAULT_IMAGES,
    HTTP,
    KV,
    Config,
    Database,
    MachineState,
    PgBouncer,
    Postgres,
    append_activity,
    dump,
    load_state,
    replace_role,
    require_no_orphans,
    resolve_state,
    state_dict,
    with_role,
    write_state,
)
from .engines import dragonfly, postgres, redis
from .errors import ConfigError, DatabaseError
from .files import private_dir, write_bytes, write_json, write_text
from .images import image_major, validate_source
from .lock import operation
from .log import sanitize
from .log import write as log_write
from .run import run

_VERSION_COMMANDS = {
    "postgres": ("postgres", "--version"),
    "redis": ("redis-server", "--version"),
    "dragonfly": ("dragonfly", "--version"),
}


@dataclass(frozen=True)
class FileState:
    path: Path
    data: bytes | None
    mode: int | None
    uid: int | None
    gid: int | None


@dataclass(frozen=True)
class Change:
    before: Config
    after: Config
    before_state: MachineState
    after_state: MachineState
    database: Database
    prior: Database | None
    changed: tuple[str, ...]
    services: tuple[str, ...]
    outage: str
    safety_backup: bool
    transaction: Path
    secret_files: tuple[secrets.SecretFile, ...]
    compose_data: dict[str, Any]
    fingerprint: str

    @property
    def noop(self) -> bool:
        return not self.changed

    def preview(self) -> str:
        changed = ", ".join(self.changed) if self.changed else "none"
        services = ", ".join(self.services) if self.services else "none"
        backup = "required" if self.safety_backup else "not required"
        return (
            f"Host: {self.after.host.id}\n"
            f"Database: {self.database.identity}\n"
            f"Settings: {changed}\n"
            f"Services: {services}\n"
            f"Interruption: {self.outage}\n"
            f"Safety backup: {backup}"
        )

    def cancel(self) -> None:
        shutil.rmtree(self.transaction, ignore_errors=True)


def prepare_add(
    config: Config,
    project: str,
    role: str,
    *,
    engine: str = "dragonfly",
    state: MachineState | None = None,
    resolver=None,
    generate=None,
) -> Change:
    require_no_orphans(config, state or load_state(config))
    identity = f"{project}/{role}"
    try:
        existing = config.select(identity)
    except ConfigError as exc:
        if "unknown database" not in str(exc):
            raise
    else:
        current = state or load_state(config)
        return _stage(
            config,
            config,
            current,
            existing,
            existing,
            (),
            resolver=resolver,
            generate=generate,
        )

    if role == "postgres":
        settings = Postgres(DEFAULT_IMAGES["postgres"], PgBouncer())
    elif role == "kv":
        if engine not in {"redis", "dragonfly"}:
            raise DatabaseError("KV engine must be redis or dragonfly")
        settings = KV(
            engine,
            DEFAULT_IMAGES[engine],
            http=HTTP(domain=f"{project}.kv-{config.host.id}.{config.host.domain}"),
            memory="256mb" if engine == "dragonfly" else None,
            threads=1 if engine == "dragonfly" else None,
        )
    else:
        raise DatabaseError("database role must be postgres or kv")
    after = with_role(config, project, role, settings)
    target = after.select(identity)
    current = state or load_state(config)
    return _stage(
        config,
        after,
        current,
        target,
        None,
        ("create",),
        resolver=resolver,
        generate=generate,
    )


def prepare_configure(
    config: Config,
    selector: str,
    values: dict[str, Any],
    *,
    reset: tuple[str, ...] = (),
    state: MachineState | None = None,
    resolver=None,
    generate=None,
) -> Change:
    current = state or load_state(config)
    require_no_orphans(config, current)
    database = config.select(selector)
    settings, changed = _settings(database, values, reset)
    if database.engine != ("postgres" if database.role == "postgres" else settings.engine):
        raise DatabaseError("changing the KV engine requires an explicit migration")
    if "image" in changed and image_major(database.image) != image_major(settings.image):
        raise DatabaseError("engine major changes require an explicit migration")
    after = replace_role(config, database, settings)
    target = after.select(database.identity)
    return _stage(
        config,
        after,
        current,
        target,
        database,
        changed,
        resolver=resolver,
        generate=generate,
    )


def commit(change: Change, *, backup_create=None, check_health=None) -> dict[str, Any]:
    if change.noop:
        change.cancel()
        return {"database": change.database.identity, "changed": [], "status": "unchanged"}
    config = change.before
    database = change.database
    health = check_health or globals()["health"]
    timeout = config.host.timeouts["command"]
    command_name = "database configure" if change.prior else "database add"
    started = time.monotonic()
    activity_started = datetime.now(timezone.utc).isoformat()
    protected = secrets.protected(change.secret_files)
    log_write(
        "database_operation",
        secrets=protected,
        host=config.host.id,
        project=database.project,
        role=database.role,
        engine=database.engine,
        command=command_name,
        step="start",
        result="started",
    )
    with operation(config, database, write=True, timeout=timeout):
        try:
            require_no_orphans(config, load_state(config))
            if _fingerprint(change.before, change.prior) != change.fingerprint:
                raise DatabaseError("configuration changed after preview; run the command again")
        except BaseException:
            append_activity(
                config,
                command=command_name,
                database=database,
                changed=change.changed,
                result="failed",
                recovery=None,
                started=activity_started,
            )
            raise
        safety = None
        safety_snapshot = None
        if change.safety_backup:
            try:
                if backup_create is None:
                    from .backup import create as backup_create
                safety = backup_create(config, change.prior, purpose="safety", lock_held=True)
                snapshot = safety.get("snapshot") or safety.get("upload", {}).get("snapshot")
                if not snapshot:
                    raise DatabaseError("safety backup did not produce a confirmed snapshot")
                safety_snapshot = snapshot
                live_state = load_state(config)
                live_role = live_state.roles.get(database.identity)
                candidate_role = change.after_state.roles.get(database.identity)
                if live_role is None or candidate_role is None:
                    raise DatabaseError("safety backup left incomplete database state")
                change = replace(
                    change,
                    after_state=replace(
                        change.after_state,
                        roles={
                            **change.after_state.roles,
                            database.identity: replace(
                                candidate_role,
                                operations=dict(live_role.operations),
                            ),
                        },
                    ),
                )
            except BaseException as exc:
                append_activity(
                    config,
                    command=command_name,
                    database=database,
                    changed=change.changed,
                    result="failed",
                    recovery=None,
                    started=activity_started,
                    safety_snapshot=safety_snapshot,
                )
                log_write(
                    "database_operation",
                    secrets=protected,
                    host=config.host.id,
                    project=database.project,
                    role=database.role,
                    engine=database.engine,
                    command=command_name,
                    step="safety_backup",
                    result="failed",
                    duration=round(time.monotonic() - started, 3),
                    error=str(exc),
                )
                raise

        try:
            targets = _installed_paths(change)
            prior_files = _snapshot(targets)
            asset_dirs = (
                database.data,
                change.after.paths.role_config(database.project, database.role),
                change.after.paths.role_secrets(database.project, database.role),
            )
            for path in asset_dirs:
                if path.is_symlink() or (path.exists() and not path.is_dir()):
                    raise DatabaseError(f"managed asset path is unsafe: {path}")
            created_dirs = tuple(path for path in asset_dirs if not path.exists())
            write_json(
                change.transaction / "transaction.json",
                {
                    "kind": "settings",
                    "host": config.host.id,
                    "database": database.identity,
                    "changed": list(change.changed),
                    "phase": "installing",
                    "recovery": "run evdb host check before retrying the settings change",
                },
            )
        except BaseException:
            append_activity(
                config,
                command=command_name,
                database=database,
                changed=change.changed,
                result="failed",
                recovery=None,
                started=activity_started,
                safety_snapshot=safety_snapshot,
            )
            raise
        candidate_invoked = False
        try:
            _install(change)
            candidate_invoked = True
            run(
                compose.command(
                    database.compose,
                    database.compose_project,
                    "up",
                    "-d",
                    "--remove-orphans",
                ),
                timeout=timeout,
                secrets=secrets.protected(change.secret_files),
            )
            health(change.after, database, state=change.after_state)
        except BaseException as exc:
            failures = []
            if change.prior is None and candidate_invoked:
                try:
                    run(
                        compose.command(database.compose, database.compose_project, "stop"),
                        timeout=timeout,
                        secrets=secrets.protected(change.secret_files),
                    )
                except BaseException:
                    failures.append("candidate service stop")
            if not failures:
                failures.extend(_restore(prior_files))
            if not failures and change.prior is not None:
                try:
                    run(
                        compose.command(
                            change.prior.compose,
                            change.prior.compose_project,
                            "up",
                            "-d",
                            "--remove-orphans",
                        ),
                        timeout=timeout,
                        secrets=secrets.protected(change.secret_files),
                    )
                    health(config, change.prior, state=change.before_state)
                except BaseException:
                    failures.append("prior service health")
            elif not failures:
                failures.extend(_remove_created_empty(created_dirs))
            append_activity(
                config,
                command=command_name,
                database=database,
                changed=change.changed,
                result="failed",
                recovery="failed" if failures else "recovered",
                started=activity_started,
                safety_snapshot=safety_snapshot,
            )
            log_write(
                "database_operation",
                secrets=protected,
                host=config.host.id,
                project=database.project,
                role=database.role,
                engine=database.engine,
                command=command_name,
                step="recovery",
                result="failed",
                recovery="failed" if failures else "recovered",
                duration=round(time.monotonic() - started, 3),
                error=str(exc),
            )
            if failures:
                write_json(
                    change.transaction / "transaction.json",
                    {
                        "kind": "settings",
                        "host": config.host.id,
                        "database": database.identity,
                        "changed": list(change.changed),
                        "phase": "recovery_failed",
                        "recovery": "inspect protected files and run evdb host check",
                    },
                )
                raise DatabaseError(
                    f"database change failed and recovery failed; inspect {change.transaction}"
                ) from exc
            change.cancel()
            if change.prior is None:
                raise DatabaseError("database creation failed; candidate services stopped") from exc
            raise DatabaseError("database change failed; prior service recovered") from exc

        append_activity(
            change.after,
            command=command_name,
            database=database,
            changed=change.changed,
            result="success",
            recovery=None,
            started=activity_started,
            safety_snapshot=safety_snapshot,
        )
        log_write(
            "database_operation",
            secrets=protected,
            host=config.host.id,
            project=database.project,
            role=database.role,
            engine=database.engine,
            command=command_name,
            step="complete",
            result="success",
            duration=round(time.monotonic() - started, 3),
        )
        change.cancel()
        return {
            "database": database.identity,
            "changed": list(change.changed),
            "safety_snapshot": (
                safety.get("snapshot") or safety.get("upload", {}).get("snapshot")
                if safety
                else None
            ),
            "status": "healthy",
        }


def start(config: Config, database: Database, *, state: MachineState | None = None) -> None:
    _lifecycle(config, database, "start", ("up", "-d", "--remove-orphans"), state, True)


def stop(config: Config, database: Database, *, state: MachineState | None = None) -> None:
    _lifecycle(config, database, "stop", ("stop",), state, False)


def restart(config: Config, database: Database, *, state: MachineState | None = None) -> None:
    _lifecycle(config, database, "restart", ("restart",), state, True)


def _lifecycle(
    config: Config,
    database: Database,
    command: str,
    args: tuple[str, ...],
    state: MachineState | None,
    check_health: bool,
) -> None:
    started = time.monotonic()
    protected = _protected_credentials(config, database)
    log_write(
        "database_lifecycle",
        secrets=protected,
        host=config.host.id,
        project=database.project,
        role=database.role,
        engine=database.engine,
        command=f"database {command}",
        step="start",
        result="started",
    )
    try:
        current = state or load_state(config)
        _require_installed(config, database, current)
        with operation(config, database, timeout=config.host.timeouts["command"]):
            run(
                compose.command(database.compose, database.compose_project, *args),
                timeout=config.host.timeouts["command"],
                secrets=protected,
            )
            if check_health:
                health(config, database, state=current)
    except BaseException as exc:
        log_write(
            "database_lifecycle",
            secrets=protected,
            host=config.host.id,
            project=database.project,
            role=database.role,
            engine=database.engine,
            command=f"database {command}",
            step="complete",
            result="failed",
            duration=round(time.monotonic() - started, 3),
            error=str(exc),
        )
        raise
    log_write(
        "database_lifecycle",
        secrets=protected,
        host=config.host.id,
        project=database.project,
        role=database.role,
        engine=database.engine,
        command=f"database {command}",
        step="complete",
        result="success",
        duration=round(time.monotonic() - started, 3),
    )


def logs(config: Config, database: Database, *, lines: int = 200) -> str:
    if not 1 <= lines <= 5000:
        raise DatabaseError("log line count must be between 1 and 5000")
    _require_installed(config, database, load_state(config))
    values = _protected_credentials(config, database)
    result = run(
        compose.command(
            database.compose,
            database.compose_project,
            "logs",
            "--no-color",
            "--tail",
            str(lines),
        ),
        timeout=config.host.timeouts["command"],
        secrets=values,
    )
    return sanitize(result.out, values)


def info(config: Config, database: Database) -> dict[str, Any]:
    values = secrets.credentials(config, database)
    state = load_state(config)
    role = state.roles.get(database.identity)
    if role is None or not role.installed:
        raise DatabaseError(f"database is not installed: {database.identity}")
    generated = compose.database(config, database, state)
    expected = compose.expected_services(generated, database)
    service_state = {}
    services_ok = True
    primary_running = False
    for name, item in expected.items():
        observed = docker.state(
            item["container"],
            timeout=min(10, config.host.timeouts["health"]),
            health=item["health"] == "docker",
        )
        ok = bool(
            observed["running"]
            and observed["image"] == item["image"]
            and observed["labels"].get(compose.CONTRACT_LABEL) == item["contract"]
            and (item["health"] != "docker" or observed["healthy"])
        )
        service_state[name] = {
            "running": bool(observed["running"]),
            "healthy": ok,
            "image": observed["image"],
        }
        services_ok = services_ok and ok
        if name.endswith("-primary"):
            primary_running = bool(observed["running"])
    engine_ok = _engine_health(database, values) if primary_running else False
    health = (
        "stopped"
        if not primary_running
        else ("healthy" if services_ok and engine_ok else "unhealthy")
    )
    engine_version = _engine_version(config, database) if primary_running else None
    try:
        from .backup import history

        rows = history(config, database) if database.durable else []
        latest = rows[0] if rows else None
    except Exception as exc:
        latest = {"error": sanitize(str(exc))[:500]}
    result = {
        "database": database.identity,
        "project": database.project,
        "role": database.role,
        "engine": database.engine,
        "settings": _info_settings(database),
        "source_image": database.image,
        "image": role.images["primary"].image,
        "engine_version": engine_version,
        "running": primary_running,
        "health": health,
        "services": service_state,
        "data": str(database.data),
        "compose": str(database.compose),
        "backup": latest,
        "host": database.domain,
        "port": database.port,
        "tls": True,
    }
    password = quote(values.password, safe="")
    if database.role == "postgres":
        user = quote(database.settings.user, safe="")
        name = quote(database.settings.database, safe="")
        result["username"] = database.settings.user
        result["database_name"] = database.settings.database
        result["url"] = (
            f"postgresql://{user}:{password}@{database.domain}:5432/{name}?sslmode=require"
        )
        result["pgbouncer"] = database.settings.pgbouncer.enabled
    else:
        result["url"] = f"rediss://default:{password}@{database.domain}:6379/0"
        if database.settings.http.enabled:
            if values.http_token is None:
                raise DatabaseError(f"{database.identity}: HTTP token is missing")
            domain = database.settings.http.domain or database.domain
            result["http"] = {
                "enabled": True,
                "url": f"https://{domain}",
                "token": values.http_token,
            }
        else:
            result["http"] = {"enabled": False}
    return result


def _info_settings(database: Database) -> dict[str, Any]:
    if database.role == "postgres":
        pool = database.settings.pgbouncer
        return {
            "image": database.settings.image,
            "user": database.settings.user,
            "database": database.settings.database,
            "pgbouncer": pool.enabled,
            "pgbouncer_image": pool.image,
            "max_clients": pool.max_clients,
            "pool_size": pool.pool_size,
            "reserve_size": pool.reserve_size,
        }
    http = database.settings.http
    result = {
        "image": database.settings.image,
        "mode": database.settings.mode,
        "http": http.enabled,
        "http_image": http.image,
        "http_connections": http.connections,
    }
    if database.engine == "dragonfly":
        result.update(memory=database.settings.memory, threads=database.settings.threads)
    return result


def _engine_health(database: Database, values: secrets.Credentials) -> bool:
    container = f"evdb-{database.project}-{database.role}-primary"
    if database.engine == "postgres":
        return postgres.health(
            container,
            user=database.settings.user,
            database=database.settings.database,
        )
    if database.engine == "redis":
        return redis.health(container, values.password)
    return dragonfly.health(container, values.password)


def _engine_version(config: Config, database: Database) -> str | None:
    container = f"evdb-{database.project}-{database.role}-primary"
    result = run(
        ["docker", "exec", container, *_VERSION_COMMANDS[database.engine]],
        timeout=min(10, config.host.timeouts["health"]),
        check=False,
    )
    value = result.out.strip()
    return value if result.code == 0 and value else None


def health(
    config: Config,
    database: Database,
    *,
    state: MachineState | None = None,
    timeout: int | None = None,
) -> None:
    current = state or load_state(config)
    data = compose.database(config, database, current)
    expected = compose.expected_services(data, database)
    deadline = time.monotonic() + (timeout or config.host.timeouts["health"])
    values = secrets.credentials(config, database)
    while True:
        services_ok = True
        for item in expected.values():
            observed = docker.state(
                item["container"],
                timeout=min(10, config.host.timeouts["health"]),
                health=item["health"] == "docker",
            )
            services_ok = services_ok and bool(
                observed["running"]
                and observed["image"] == item["image"]
                and observed["labels"].get(compose.CONTRACT_LABEL) == item["contract"]
                and (item["health"] != "docker" or observed["healthy"])
            )
        primary = f"evdb-{database.project}-{database.role}-primary"
        if database.engine == "postgres":
            engine_ok = postgres.health(
                primary,
                user=database.settings.user,
                database=database.settings.database,
            )
        elif database.engine == "redis":
            engine_ok = redis.health(primary, values.password)
        else:
            engine_ok = dragonfly.health(primary, values.password)
        if services_ok and engine_ok:
            return
        if time.monotonic() >= deadline:
            raise DatabaseError(f"database did not become healthy: {database.identity}")
        time.sleep(1)


def _stage(
    before,
    after,
    current,
    target,
    prior,
    changed,
    *,
    resolver=None,
    generate=None,
) -> Change:
    after_state = resolve_state(after, current, resolver=resolver)
    transaction = private_dir(
        after.paths.state / "transactions" / f"{target.project}-{target.role}-{uuid.uuid4().hex}"
    )
    try:
        create = generate or (lambda: __import__("secrets").token_urlsafe(32))
        if prior is None:
            password = create()
            token = create() if target.role == "kv" and target.settings.http.enabled else None
            if not isinstance(password, str) or not password or token == "":
                raise ConfigError("credential generator returned an empty value")
            values = secrets.Credentials(password, token)
        else:
            values = secrets.credentials(before, prior)
            if target.role == "kv" and target.settings.http.enabled and values.http_token is None:
                token_path = secrets.path(before, prior, "http-token")
                if token_path.exists() or token_path.is_symlink():
                    token = secrets.read(before, prior, "http-token")
                else:
                    token = create()
                    if not isinstance(token, str) or not token:
                        raise ConfigError("credential generator returned an empty value")
                values = secrets.Credentials(values.password, token)
        secret_files = secrets.render(after, target, values)
        data = compose.database(after, target, after_state)
        primary_name = f"evdb-{target.project}-{target.role}-primary"
        primary_change = False
        if prior is not None:
            prior_data = compose.database(before, prior, current)
            primary_change = prior_data["services"][primary_name] != data["services"][primary_name]
        contract = compose.service_hash(data)
        role = after_state.roles[target.identity]
        role = replace(role, compose_hash=contract, installed=True)
        after_state = replace(
            after_state,
            roles={**after_state.roles, target.identity: role},
        )
        write_text(transaction / "host.yml", dump(after), mode=0o600)
        write_json(transaction / "state.json", state_dict(after_state), mode=0o600)
        staged = []
        for item in secret_files:
            candidate = transaction / "secrets" / item.path.name
            write_text(candidate, item.content, mode=0o600)
            staged.append((str(item.path), str(candidate)))
        validation_data = _replace_paths(data, staged)
        validation_path = transaction / "compose.yaml"
        compose.write(validation_path, validation_data)
        if target.role == "postgres" and target.settings.pgbouncer.enabled:
            write_text(transaction / "pgbouncer.ini", compose.pool_config(target), mode=0o600)
        compose.validate(
            validation_path,
            target.compose_project,
            timeout=after.host.timeouts["command"],
            secrets=secrets.protected(secret_files),
        )
        services = tuple(data["services"])
        return Change(
            before,
            after,
            current,
            after_state,
            target,
            prior,
            tuple(changed),
            services,
            "brief database restart" if prior else "initial service start",
            bool(prior and prior.durable and primary_change),
            transaction,
            secret_files,
            data,
            _fingerprint(before, prior),
        )
    except Exception:
        shutil.rmtree(transaction, ignore_errors=True)
        raise


def _settings(database: Database, values: dict[str, Any], reset: tuple[str, ...]):
    allowed = (
        {"image", "pgbouncer", "pgbouncer_image", "max_clients", "pool_size", "reserve_size"}
        if database.role == "postgres"
        else {"image", "mode", "http", "http_image", "http_connections", "memory", "threads"}
    )
    unknown = sorted((set(values) | set(reset)) - allowed)
    if unknown:
        raise DatabaseError(f"setting is not valid for {database.engine}: {unknown[0]}")
    if database.engine == "redis" and ({"memory", "threads"} & (set(values) | set(reset))):
        raise DatabaseError("memory and threads are only valid for dragonfly")
    changed = []
    settings = database.settings
    defaults = (
        Postgres(DEFAULT_IMAGES["postgres"], PgBouncer())
        if database.role == "postgres"
        else KV(
            database.engine,
            DEFAULT_IMAGES[database.engine],
            http=HTTP(),
            memory="256mb" if database.engine == "dragonfly" else None,
            threads=1 if database.engine == "dragonfly" else None,
        )
    )
    updates = dict(values)
    for name in reset:
        if name.startswith("pgbouncer_"):
            key = name.removeprefix("pgbouncer_")
            updates[name] = getattr(defaults.pgbouncer, key)
        elif name.startswith("http_"):
            key = name.removeprefix("http_")
            updates[name] = getattr(defaults.http, key)
        else:
            updates[name] = getattr(defaults, name)
    if database.role == "postgres":
        pool = settings.pgbouncer
        pool_values = {}
        for name in ("pgbouncer", "pgbouncer_image", "max_clients", "pool_size", "reserve_size"):
            if name not in updates:
                continue
            key = {"pgbouncer": "enabled", "pgbouncer_image": "image"}.get(name, name)
            pool_values[key] = updates[name]
            if getattr(pool, key) != updates[name]:
                changed.append(name)
        if "image" in updates:
            validate_source(updates["image"])
            if settings.image != updates["image"]:
                changed.append("image")
        settings = replace(
            settings,
            image=updates.get("image", settings.image),
            pgbouncer=replace(pool, **pool_values),
        )
    else:
        http = settings.http
        http_values = {}
        for name in ("http", "http_image", "http_connections"):
            if name not in updates:
                continue
            key = {
                "http": "enabled",
                "http_image": "image",
                "http_connections": "connections",
            }[name]
            http_values[key] = updates[name]
            if getattr(http, key) != updates[name]:
                changed.append(name)
        direct = {}
        for name in ("image", "mode", "memory", "threads"):
            if name in updates:
                direct[name] = updates[name]
                if getattr(settings, name) != updates[name]:
                    changed.append(name)
        if "image" in direct:
            validate_source(direct["image"])
        settings = replace(settings, **direct, http=replace(http, **http_values))
    return settings, tuple(sorted(set(changed)))


def _install(change: Change) -> None:
    config = change.after
    database = change.database
    if config.paths.source.exists():
        write_bytes(config.paths.previous, config.paths.source.read_bytes(), mode=0o640)
    write_text(config.paths.source, dump(config), mode=0o640)
    secrets.write(change.secret_files)
    compose.write(database.compose, change.compose_data)
    if database.role == "postgres" and database.settings.pgbouncer.enabled:
        write_text(
            config.paths.role_config(database.project, database.role) / "pgbouncer.ini",
            compose.pool_config(database),
            mode=0o640,
        )
    write_state(config, change.after_state)
    database.data.mkdir(parents=True, exist_ok=True, mode=0o700)


def _installed_paths(change: Change) -> tuple[Path, ...]:
    database = change.database
    paths = {
        change.after.paths.source,
        change.after.paths.previous,
        change.after.paths.machine_state,
        database.compose,
        change.after.paths.role_config(database.project, database.role) / "pgbouncer.ini",
        *(item.path for item in change.secret_files),
    }
    return tuple(sorted(paths, key=str))


def _snapshot(paths) -> tuple[FileState, ...]:
    result = []
    for path in paths:
        if path.is_symlink():
            raise DatabaseError(f"managed path must not be a symlink: {path}")
        if not path.exists():
            result.append(FileState(path, None, None, None, None))
            continue
        if not path.is_file():
            raise DatabaseError(f"managed path must be a file: {path}")
        stat = path.stat()
        result.append(
            FileState(path, path.read_bytes(), stat.st_mode & 0o777, stat.st_uid, stat.st_gid)
        )
    return tuple(result)


def _restore(states) -> list[str]:
    failures = []
    for item in states:
        try:
            if item.data is None:
                item.path.unlink(missing_ok=True)
                continue
            write_bytes(item.path, item.data, mode=item.mode)
            if item.uid is not None and item.gid is not None:
                os.chown(item.path, item.uid, item.gid)
        except OSError:
            failures.append(str(item.path))
    return failures


def _remove_created_empty(paths: tuple[Path, ...]) -> list[str]:
    failures = []
    for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
        try:
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                failures.append(str(path))
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            failures.append(str(path))
    return failures


def _fingerprint(config: Config, database: Database | None) -> str:
    paths = [config.paths.source, config.paths.machine_state]
    if database is not None:
        paths.append(database.compose)
        paths.extend(
            config.paths.role_secrets(database.project, database.role) / name
            for name in (
                "password",
                "http-token",
                "pgbouncer-users",
                "redis.conf",
                "dragonfly.flags",
                "http.env",
            )
        )
    digest = hashlib.sha256()
    for path in sorted(paths, key=str):
        digest.update(str(path).encode())
        digest.update(b"\0")
        if path.is_file() and not path.is_symlink():
            digest.update(path.read_bytes())
            digest.update(str(path.stat().st_mode & 0o777).encode())
        else:
            digest.update(b"missing")
    return digest.hexdigest()


def _replace_paths(value: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_paths(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_paths(item, replacements) for item in value]
    if isinstance(value, str):
        for old, new in replacements:
            value = value.replace(old, new)
    return value


def _require_installed(config: Config, database: Database, state: MachineState) -> None:
    require_no_orphans(config, state)
    role = state.roles.get(database.identity)
    if role is None or not role.installed or not database.compose.is_file():
        raise DatabaseError(f"database is not installed: {database.identity}")


def _protected_credentials(config: Config, database: Database) -> tuple[str, ...]:
    values = secrets.credentials(config, database)
    return secrets.protected(item for item in (values.password, values.http_token) if item)
