from __future__ import annotations

import secrets as random
import shutil
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from . import docker
from .config import (
    add_role,
    defaults,
    load,
    protected,
    remove_role,
    replace_role,
    validate_image,
    write,
)
from .engines import get
from .errors import DatabaseError, Error
from .files import allocated, disk, managed_dir, private_dir, private_line, write_text
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
        targets = (
            {target, "/var/lib/postgresql/data", "/var/lib/postgresql"}
            if database.role == "postgres"
            else {target}
        )
        current_source = _bind_source(current["services"][service], targets)
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


def _bind_source(service: dict[str, Any], targets: set[str]) -> Path:
    matches = [
        Path(volume.split(":", 1)[0])
        for volume in service["volumes"]
        if isinstance(volume, str) and ":" in volume and volume.split(":", 2)[1] in targets
    ]
    if len(matches) != 1:
        raise KeyError("data bind")
    return matches[0]


def _data_root(config: Config, value: str | Path | None) -> Path:
    if value is None:
        if len(config.host.data_roots) != 1:
            raise DatabaseError(
                "database data root is required when the host configures multiple roots"
            )
        return config.host.data_roots[0]
    path = Path(value)
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise DatabaseError("database data root must be configured on the host") from exc
    if not path.is_absolute() or path != resolved or path not in config.host.data_roots:
        raise DatabaseError(f"database data root is not configured on the host: {path}")
    return path


def add(
    config: Config,
    project: str,
    role: str,
    *,
    engine: str | None = None,
    password: str | None = None,
    username: str | None = None,
    database_name: str | None = None,
    data_root: str | Path | None = None,
    postgres_version: str | int = 16,
    pgbouncer: bool | None = None,
    pgbouncer_image: str | None = None,
    max_clients: int | None = None,
    pool_size: int | None = None,
    reserve_size: int | None = None,
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
            if data_root is not None and existing.settings.data_root != _data_root(
                current, data_root
            ):
                raise DatabaseError("changing database data root is outside this operation")
            with lock(current.paths.role_lock(project, role), timeout=timeout):
                render(current, existing)
                _prepare(existing, timeout)
                docker.up(
                    existing.compose,
                    existing.compose_project,
                    timeout=timeout,
                    secrets=protected(current),
                )
                health(current, existing)
            return current
        selected_root = _data_root(current, data_root)
        settings = defaults(role, selected_root, engine or "dragonfly")
        if role == "postgres":
            from .config import postgres_image, validate_postgres_name

            pool = settings.pgbouncer
            settings = replace(
                settings,
                image=postgres_image(postgres_version),
                username=validate_postgres_name(username or settings.username, "username"),
                database=validate_postgres_name(
                    database_name or settings.database, "database name"
                ),
                pgbouncer=replace(
                    pool,
                    enabled=pool.enabled if pgbouncer is None else pgbouncer,
                    image=pgbouncer_image or pool.image,
                    max_clients=pool.max_clients if max_clients is None else max_clients,
                    pool_size=pool.pool_size if pool_size is None else pool_size,
                    reserve_size=pool.reserve_size if reserve_size is None else reserve_size,
                ),
            )
            get("postgres").validate(settings)
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
            _prepare(target, timeout)
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
    selected = load(config.paths.source, paths=config.paths).select(database.identity)
    candidate = _settings(selected, values, reset)
    if selected.role == "postgres" and candidate.image != selected.image:
        from .engines import postgres

        source_major = postgres.major(selected.image)
        target_major = postgres.major(candidate.image)
        if target_major < source_major:
            raise DatabaseError("Postgres major-version downgrade is not supported")
        if target_major > source_major:
            return _upgrade(config, selected, candidate)
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
            _prepare(target, timeout)
            docker.up(
                target.compose,
                target.compose_project,
                timeout=timeout,
                secrets=protected(updated),
            )
            health(updated, target)
        return updated


def _upgrade(config: Config, database: Database, settings: Postgres) -> Config:
    from . import backup
    from .engines import postgres

    timeout = config.host.timeouts["command"]
    with operation(config, write=True, timeout=timeout):
        current = load(config.paths.source, paths=config.paths)
        source = current.select(database.identity)
        if source.settings != database.settings:
            raise DatabaseError("Postgres settings changed while preparing upgrade; retry")
        source_major = (
            int(
                postgres._psql(
                    source.service("primary"),
                    source.settings.database,
                    "SHOW server_version_num",
                    username=source.settings.username,
                    timeout=30,
                )
            )
            // 10000
        )
        target_major = postgres.major(settings.image)
        if target_major <= source_major:
            raise DatabaseError("Postgres target must be newer than the running server")
        required = allocated(source.data) * 2 + current.host.backup.min_free_gb * 1024**3
        if disk(source.data)["free_bytes"] < required:
            raise DatabaseError("insufficient free space for Postgres major upgrade")
        token = random.token_hex(6)
        candidate = source.data.with_name(f".data-upgrade-{token}")
        old = source.data.with_name(f".data-old-{token}")
        if candidate.exists() or old.exists():
            raise DatabaseError("Postgres upgrade staging path already exists")
        owner = postgres.image_user(settings.image)
        create_role = postgres._psql(
            source.service("primary"),
            source.settings.database,
            "SELECT format('CREATE ROLE %I;', current_user)",
            username=source.settings.username,
            timeout=30,
        )
        old_moved = False
        failed = None
        try:
            if source.settings.pgbouncer.enabled:
                docker.remove(source.service("pgbouncer"), timeout=timeout)
            docker.disconnect(docker.NETWORK, source.service("primary"), timeout=timeout)
            safety = backup.create(current, source, lock_held=True)
            docker.stop(
                source.compose,
                source.compose_project,
                timeout=timeout,
                secrets=protected(current),
            )
            postgres.restore(
                source,
                Path(safety["folder"]),
                settings.image,
                candidate,
                f"{source.service('primary')}-upgrade",
                create_role,
                owner,
            )
            for name in get(source.engine).services(source):
                docker.remove(name, timeout=timeout)
            source.data.rename(old)
            old_moved = True
            candidate.rename(source.data)
            updated = replace_role(current, source.identity, settings)
            write(updated, secrets=False)
            final = updated.select(source.identity)
            render(updated, final)
            _prepare(final, timeout)
            docker.up(
                final.compose,
                final.compose_project,
                timeout=timeout,
                secrets=protected(updated),
            )
            health(updated, final)
        except BaseException as exc:
            failed = exc
            for name in get(source.engine).services(source):
                docker.remove(name, timeout=timeout)
            if old_moved:
                failed_target = source.data.with_name(f".data-failed-{token}")
                if source.data.exists():
                    source.data.rename(failed_target)
                old.rename(source.data)
                shutil.rmtree(failed_target, ignore_errors=True)
                write(current, secrets=False)
                render(current, source)
            docker.up(
                source.compose,
                source.compose_project,
                timeout=timeout,
                secrets=protected(current),
            )
            health(current, source)
            raise
        finally:
            if candidate.exists():
                shutil.rmtree(candidate, ignore_errors=failed is not None)
        shutil.rmtree(old)
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


def delete(config: Config, database: Database) -> Config:
    timeout = config.host.timeouts["command"]
    with operation(config, write=True, timeout=timeout):
        current = load(config.paths.source, paths=config.paths)
        target = current.select(database.identity)
        updated = remove_role(current, target.identity)
        paths = (target.generated, target.data)
        if target.data.is_symlink():
            raise DatabaseError(f"database deletion path is unsafe: {target.data}")
        for path, root in (
            (target.generated, current.paths.projects),
            (target.data, target.settings.data_root),
        ):
            _require_delete_path(path, root)
            if path.is_symlink() or path.exists() and not path.is_dir():
                raise DatabaseError(f"database deletion path is unsafe: {path}")
        with lock(current.paths.role_lock(target.project, target.role), timeout=timeout):
            for name in get(target.engine).services(target):
                docker.remove(name, timeout=timeout)
            source_text = current.paths.source.read_bytes()
            secret_text = current.paths.secrets.read_bytes()
            try:
                write(updated)
            except BaseException:
                from .files import write_bytes

                write_bytes(current.paths.source, source_text, mode=0o600)
                write_bytes(current.paths.secrets, secret_text, mode=0o600)
                raise
            for path in paths:
                try:
                    shutil.rmtree(path)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise DatabaseError(f"database removed; cleanup failed: {path}: {exc}") from exc
        for path in (target.generated.parent, target.data.parent, target.data.parent.parent):
            with suppress(OSError):
                path.rmdir()
        return updated


def _require_delete_path(path: Path, root: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise DatabaseError(f"database deletion path is outside its managed root: {path}") from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise DatabaseError(f"database deletion path is unsafe: {current}")


def _converge(config: Config, database: Database) -> None:
    with operation(config, database, timeout=config.host.timeouts["command"]):
        current = load(config.paths.source, paths=config.paths)
        target = current.select(database.identity)
        render(current, target)
        _prepare(target, current.host.timeouts["command"])
        docker.up(
            target.compose,
            target.compose_project,
            timeout=current.host.timeouts["command"],
            secrets=protected(current),
        )
        health(current, target)


def _prepare(database: Database, timeout: int) -> None:
    if database.role == "postgres":
        from .engines import postgres

        postgres.prepare_data(database, timeout=timeout)


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


def info(
    config: Config,
    database: Database,
    *,
    observed: dict[str, Any] | None = None,
    runtime_error: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if observed is None:
        if progress is not None:
            progress("Checking database runtime")
        observed = observe(config, database)
    engine = get(database.engine)
    errors = [runtime_error] if runtime_error else []
    try:
        storage = disk(database.data)
        storage.update(allocated_bytes=allocated(database.data), available=True)
    except OSError as exc:
        message = clean(redact(str(exc), protected(config)))[:500]
        storage = {
            "path": str(database.data),
            "mount": None,
            "source": None,
            "filesystem": None,
            "total_bytes": None,
            "used_bytes": None,
            "free_bytes": None,
            "allocated_bytes": None,
            "available": False,
        }
        errors.append(message)
    if progress is not None:
        progress("Reading engine details")
    try:
        details = engine.info(database) if observed["running"] else {}
    except (Error, OSError, KeyError, TypeError, ValueError) as exc:
        message = clean(str(exc))
        details = {"error": message}
        errors.append(message)
    if observed["running"]:
        data_usage = details.pop(
            "data",
            {"available": False, "reason": "live data assessment unavailable"},
        )
    else:
        data_usage = {"available": False, "reason": "database is stopped"}
    backup_summary = {
        "state": "disabled" if not database.durable else "missing",
        "availability": None,
        "time": None,
        "backup": None,
        "snapshot": None,
    }
    if database.durable:
        from . import backup

        if progress is not None:
            progress("Reading backup history")
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
            message = clean(redact(str(exc), protected(config)))[:500]
            backup_summary.update(
                state="error",
                error=message,
            )
            errors.append(message)
    return {
        "database": database.identity,
        "engine": database.engine,
        "status": observed["health"],
        "image": database.image,
        "error": errors[0] if errors else "none",
        "sidecar_images": _sidecar_images(database),
        "data": str(database.data),
        "compose": str(database.compose),
        "storage": storage,
        "data_usage": data_usage,
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
        {
            "image",
            "postgres_version",
            "pgbouncer",
            "pgbouncer_image",
            "max_clients",
            "pool_size",
            "reserve_size",
        }
        if database.role == "postgres"
        else {"image", "mode", "http", "http_image", "http_connections", "memory", "threads"}
    )
    unknown = sorted((set(values) | set(reset)) - allowed)
    if unknown:
        raise DatabaseError(f"setting is not valid for {database.engine}: {unknown[0]}")
    if database.engine == "redis" and ({"memory", "threads"} & (set(values) | set(reset))):
        raise DatabaseError("memory and threads are only valid for Dragonfly")
    base = defaults(database.role, database.settings.data_root, database.engine)
    updates = dict(values)
    if "postgres_version" in updates:
        from .config import postgres_image

        updates["image"] = postgres_image(updates.pop("postgres_version"))
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
    if name == "postgres_version":
        from .engines import postgres

        return postgres.major(settings.image)
    if name in {"pgbouncer", "pgbouncer_image", "max_clients", "pool_size", "reserve_size"}:
        attribute = {"pgbouncer": "enabled", "pgbouncer_image": "image"}.get(name, name)
        return getattr(settings.pgbouncer, attribute)
    if name == "http":
        return settings.http.enabled
    if name.startswith("http_"):
        return getattr(settings.http, name.removeprefix("http_"))
    return getattr(settings, name)


def _setting_values(database: Database) -> dict[str, Any]:
    names = (
        (
            "postgres_version",
            "pgbouncer",
            "pgbouncer_image",
            "max_clients",
            "pool_size",
            "reserve_size",
        )
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
