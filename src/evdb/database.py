from __future__ import annotations

import secrets as random
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from . import docker
from .config import add_role, defaults, load, protected, replace_role, validate_image, write
from .engines import get
from .errors import DatabaseError, Error
from .files import managed_dir, private_dir, private_line, write_text
from .lock import lock, operation
from .models import KV, Config, Database, Postgres, RoleSecrets
from .run import clean, redact


def render(config: Config, database: Database) -> dict[str, Any]:
    engine = get(database.engine)
    engine.validate(database.settings)
    data = {
        "name": database.compose_project,
        "services": engine.services(database),
        "networks": {docker.NETWORK: {"external": True, "name": docker.NETWORK}},
    }
    _require_stable_data_bind(database, data)
    try:
        managed_dir(database.data, None)
    except OSError as exc:
        raise DatabaseError(f"database data path is unsafe: {database.data}") from exc
    generated = private_dir(database.generated)
    for name, text in engine.files(database).items():
        write_text(generated / name, text, mode=0o640)
    docker.write_compose(database.compose, data)
    docker.validate_compose(
        database.compose,
        database.compose_project,
        timeout=config.host.timeouts["command"],
        secrets=protected(config),
    )
    return data


def _require_stable_data_bind(database: Database, desired: dict[str, Any]) -> None:
    compose = database.compose
    if not compose.exists() and not compose.is_symlink():
        return
    if compose.is_symlink() or not compose.is_file():
        raise DatabaseError(f"generated Compose is missing or unsafe: {compose}")
    try:
        current = yaml.safe_load(compose.read_text())
        service = database.service("primary")
        desired_service = desired["services"][service]
        target = _data_target(desired_service, database.data)
        current_source = _bind_source(current["services"][service], target)
    except (KeyError, TypeError, OSError, yaml.YAMLError) as exc:
        raise DatabaseError(f"generated Compose data bind is missing or unsafe: {compose}") from exc
    if current_source != database.data:
        raise DatabaseError(
            f"database data root cannot change for {database.identity}: "
            f"{current_source} -> {database.data}"
        )


def _data_target(service: dict[str, Any], source: Path) -> str:
    prefix = f"{source}:"
    matches = [
        volume.removeprefix(prefix).split(":", 1)[0]
        for volume in service["volumes"]
        if isinstance(volume, str) and volume.startswith(prefix)
    ]
    if len(matches) != 1:
        raise KeyError("data bind")
    return matches[0]


def _bind_source(service: dict[str, Any], target: str) -> Path:
    suffix = f":{target}"
    matches = [
        Path(volume.removesuffix(suffix))
        for volume in service["volumes"]
        if isinstance(volume, str) and volume.endswith(suffix)
    ]
    if len(matches) != 1:
        raise KeyError("data bind")
    return matches[0]


def add(
    config: Config,
    project: str,
    role: str,
    *,
    engine: str | None = None,
    password: str | None = None,
    username: str | None = None,
    database_name: str | None = None,
) -> Config:
    identity = f"{project}/{role}"
    if role == "postgres" and engine is not None:
        raise DatabaseError("--engine is only valid for a KV database")
    if role != "postgres" and (username is not None or database_name is not None):
        raise DatabaseError("Postgres identity is only valid for a Postgres database")
    timeout = config.host.timeouts["command"]
    with operation(config, write=True, timeout=timeout):
        current = load(config.paths.source, paths=config.paths)
        existing = next(
            (database for database in current.databases if database.identity == identity),
            None,
        )
        if existing is not None:
            if role == "kv" and engine is not None and existing.engine != engine:
                raise DatabaseError(
                    f"{identity} already uses {existing.engine}; engine conversion is outside v1"
                )
            if password is not None and existing.credentials.password != _password(password):
                raise DatabaseError("password rotation is outside v1")
            if username is not None and existing.settings.username != username:
                raise DatabaseError(
                    "changing initialized Postgres identity is outside this operation"
                )
            if database_name is not None and existing.settings.database != database_name:
                raise DatabaseError(
                    "changing initialized Postgres identity is outside this operation"
                )
            with lock(current.paths.role_lock(project, role), timeout=timeout):
                health(current, existing)
            return current
        settings = defaults(role, engine or "dragonfly")
        if role == "postgres":
            from .config import validate_postgres_name

            settings = replace(
                settings,
                username=validate_postgres_name(username or settings.username, "username"),
                database=validate_postgres_name(
                    database_name or settings.database, "database name"
                ),
            )
        credential = (
            _password(password) if password is not None else _password(random.token_urlsafe(32))
        )
        token = (
            _password(random.token_urlsafe(32)) if role == "kv" and settings.http.enabled else None
        )
        updated = add_role(current, project, role, settings, RoleSecrets(credential, token))
        target = updated.select(identity)
        with lock(updated.paths.role_lock(project, role), timeout=timeout):
            write(updated)
            render(updated, target)
            docker.up(
                target.compose,
                target.compose_project,
                timeout=timeout,
                secrets=protected(updated),
            )
            health(updated, target)
        return updated


def configure(
    config: Config,
    database: Database,
    values: dict[str, Any],
    *,
    reset: tuple[str, ...] = (),
) -> Config:
    timeout = config.host.timeouts["command"]
    with operation(config, write=True, timeout=timeout):
        current = load(config.paths.source, paths=config.paths)
        selected = current.select(database.identity)
        settings = _settings(selected, values, reset)
        if settings == selected.settings:
            with lock(
                current.paths.role_lock(selected.project, selected.role),
                timeout=timeout,
            ):
                health(current, selected)
            return current
        updated = replace_role(current, selected.identity, settings)
        target = updated.select(selected.identity)
        with lock(updated.paths.role_lock(target.project, target.role), timeout=timeout):
            write(updated, secrets=False)
            render(updated, target)
            docker.up(
                target.compose,
                target.compose_project,
                timeout=timeout,
                secrets=protected(updated),
            )
            health(updated, target)
        return updated


def start(config: Config, database: Database) -> None:
    _converge(config, database)


def stop(config: Config, database: Database) -> None:
    _require_generated(database)
    with operation(config, database, timeout=config.host.timeouts["command"]):
        docker.stop(
            database.compose,
            database.compose_project,
            timeout=config.host.timeouts["command"],
            secrets=protected(config),
        )


def restart(config: Config, database: Database) -> None:
    _converge(config, database)


def _converge(config: Config, database: Database) -> None:
    with operation(config, database, timeout=config.host.timeouts["command"]):
        current = load(config.paths.source, paths=config.paths)
        target = current.select(database.identity)
        render(current, target)
        docker.up(
            target.compose,
            target.compose_project,
            timeout=current.host.timeouts["command"],
            secrets=protected(current),
        )
        health(current, target)


def logs(config: Config, database: Database, *, lines: int = 200) -> str:
    if not 1 <= lines <= 5000:
        raise DatabaseError("log line count must be between 1 and 5000")
    _require_generated(database)
    return docker.logs(
        database.compose,
        database.compose_project,
        lines,
        timeout=config.host.timeouts["command"],
        secrets=protected(config),
    )


def health(
    config: Config,
    database: Database,
    *,
    timeout: int | None = None,
) -> None:
    deadline = time.monotonic() + (timeout or config.host.timeouts["health"])
    engine = get(database.engine)
    service_names = tuple(engine.services(database))
    while True:
        containers = [
            docker.state(
                name,
                timeout=min(10, config.host.timeouts["health"]),
                health=name != database.service("primary"),
            )
            for name in service_names
        ]
        services_ok = all(item["running"] and (item["healthy"] is not False) for item in containers)
        if services_ok and engine.health(database, timeout=10):
            return
        if time.monotonic() >= deadline:
            raise DatabaseError(f"database did not become healthy: {database.identity}")
        time.sleep(1)


def observe(config: Config, database: Database) -> dict[str, Any]:
    engine = get(database.engine)
    services = engine.services(database)
    states = {
        name: docker.state(name, health=name != database.service("primary")) for name in services
    }
    running = states[database.service("primary")]["running"]
    healthy = bool(
        running
        and all(value["running"] and value["healthy"] is not False for value in states.values())
        and engine.health(database)
    )
    return {
        "running": running,
        "healthy": healthy,
        "health": "healthy" if healthy else ("unhealthy" if running else "stopped"),
        "services": states,
    }


def info(config: Config, database: Database) -> dict[str, Any]:
    observed = observe(config, database)
    engine = get(database.engine)
    try:
        details = engine.info(database) if observed["running"] else {}
    except (Error, OSError) as exc:
        details = {"error": clean(str(exc))}
    backup_summary = {
        "state": "disabled" if not database.durable else "missing",
        "availability": None,
        "time": None,
        "backup": None,
        "snapshot": None,
    }
    if database.durable:
        from . import backup

        try:
            rows = backup.history(config, database)
            if rows:
                latest = rows[0]
                backup_summary.update(
                    state="available",
                    availability=latest["source"],
                    time=latest["time"],
                    backup=latest["backup"],
                    snapshot=latest["snapshot"],
                )
        except (Error, OSError) as exc:
            backup_summary.update(
                state="error",
                error=clean(redact(str(exc), protected(config)))[:500],
            )
    return {
        "database": database.identity,
        "engine": database.engine,
        "status": observed["health"],
        "image": database.image,
        "sidecar_images": _sidecar_images(database),
        "data": str(database.data),
        "compose": str(database.compose),
        "settings": _setting_values(database),
        "engine_info": details,
        "backup": backup_summary,
        "connection": connection(database),
    }


def connection(database: Database) -> dict[str, Any]:
    password = quote(database.credentials.password, safe="")
    if database.role == "postgres":
        username = quote(database.settings.username, safe="")
        name = quote(database.settings.database, safe="")
        url = f"postgresql://{username}:{password}@{database.domain}:5432/{name}?sslmode=require"
        return {
            "url": url,
            "username": database.settings.username,
            "password": database.credentials.password,
            "database": database.settings.database,
        }
    value = {
        "url": f"rediss://default:{password}@{database.domain}:6379/0",
        "username": "default",
        "password": database.credentials.password,
    }
    if database.settings.http.enabled:
        value.update(
            http_url=f"https://{database.settings.http.domain or database.domain}",
            http_loopback=f"http://127.0.0.1:{database.http_port}",
            http_token=database.credentials.http_token,
        )
    return value


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
        raise DatabaseError("memory and threads are only valid for Dragonfly")
    base = defaults(database.role, database.engine)
    updates = dict(values)
    for name in reset:
        updates[name] = _current(base, name)
    settings = database.settings
    if "image" in updates:
        validate_image(updates["image"])
    if database.role == "postgres":
        pool = settings.pgbouncer
        pool_values = {}
        for name in ("pgbouncer", "pgbouncer_image", "max_clients", "pool_size", "reserve_size"):
            if name in updates:
                pool_values[
                    {"pgbouncer": "enabled", "pgbouncer_image": "image"}.get(name, name)
                ] = updates[name]
        settings = replace(
            settings,
            image=updates.get("image", settings.image),
            pgbouncer=replace(pool, **pool_values),
        )
    else:
        http_values = {}
        for name in ("http", "http_image", "http_connections"):
            if name in updates:
                http_values[
                    {"http": "enabled", "http_image": "image", "http_connections": "connections"}[
                        name
                    ]
                ] = updates[name]
        direct = {
            name: updates[name]
            for name in ("image", "mode", "memory", "threads")
            if name in updates
        }
        settings = replace(settings, **direct, http=replace(settings.http, **http_values))
    get(database.engine).validate(settings)
    return settings


def _current(settings: Postgres | KV, name: str):
    if name == "pgbouncer":
        return settings.pgbouncer.enabled
    if name.startswith("pgbouncer_"):
        return getattr(settings.pgbouncer, name.removeprefix("pgbouncer_"))
    if name == "http":
        return settings.http.enabled
    if name.startswith("http_"):
        return getattr(settings.http, name.removeprefix("http_"))
    return getattr(settings, name)


def _setting_values(database: Database) -> dict[str, Any]:
    names = (
        ("image", "pgbouncer", "pgbouncer_image", "max_clients", "pool_size", "reserve_size")
        if database.role == "postgres"
        else (
            ("image", "mode", "http", "http_image", "http_connections", "memory", "threads")
            if database.engine == "dragonfly"
            else ("image", "mode", "http", "http_image", "http_connections")
        )
    )
    return {name: _current(database.settings, name) for name in names}


def _sidecar_images(database: Database) -> dict[str, str]:
    values = {}
    if database.role == "postgres" and database.settings.pgbouncer.enabled:
        values["pgbouncer"] = database.settings.pgbouncer.image
    if database.role == "kv" and database.settings.http.enabled:
        values["http"] = database.settings.http.image
    return values


def _password(value: str) -> str:
    if not isinstance(value, str) or not value or any(char in value for char in "\0\r\n"):
        raise DatabaseError("database credentials must be one non-empty line")
    return value


def password_file(value: str | Path) -> str:
    try:
        return private_line(value)
    except ValueError as exc:
        raise DatabaseError(str(exc)) from exc


def _require_generated(database: Database) -> None:
    if database.compose.is_symlink() or not database.compose.is_file():
        raise DatabaseError(f"database generated files are missing: {database.identity}")
