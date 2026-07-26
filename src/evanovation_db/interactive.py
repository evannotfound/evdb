from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .config import DEFAULT_IMAGES, HTTP, KV, Config, Database, PgBouncer, Postgres

_ADD = object()
_SETUP_FIELDS = (
    ("host_id", "Host ID"),
    ("domain", "Base domain"),
    ("data_root", "Database data root"),
    ("acme_email", "ACME email"),
    ("dns_provider", "DNS provider"),
    ("postgres_repo", "Postgres Restic repository"),
    ("kv_repo", "KV Restic repository"),
    ("dns_env_file", "DNS credential environment file"),
)
ActionResult = str | tuple[str, Config]


@dataclass(frozen=True)
class Actions:
    status: Callable[[], str]
    health: Callable[[], dict[str, str]]
    add: Callable[[str, str, str], ActionResult]
    info: Callable[[str], str]
    configure: Callable[[str, dict, tuple[str, ...]], ActionResult]
    start: Callable[[str], ActionResult]
    stop: Callable[[str], ActionResult]
    restart: Callable[[str], ActionResult]
    logs: Callable[[str], str]
    backup: Callable[[str], ActionResult]
    backup_list: Callable[[str], str]
    backup_history: Callable[[str], list[dict]]
    backup_test: Callable[[str, str], ActionResult]
    restore: Callable[[str, str], ActionResult]
    host_check: Callable[[], str]
    host_setup: Callable[[], ActionResult]
    host_update: Callable[[str], ActionResult]


def run(config: Config, actions: Actions, *, input_fn=input, output=print) -> int:
    while True:
        output(actions.status())
        output("\n1. Databases\n2. Backups\n3. Restore\n4. Host\n0. Exit")
        choice = _choice(input_fn, output, "Select", {"0", "1", "2", "3", "4"})
        if choice in {None, "0"}:
            return 0
        if choice == "1":
            updated = _databases(config, actions, input_fn, output)
            if updated is not None:
                config = updated
        elif choice == "2":
            updated = _backups(config, actions, input_fn, output)
            if updated is not None:
                config = updated
        elif choice == "3":
            target = _database(config, actions, input_fn, output, durable=True)
            if target:
                selected = _backup(actions, target, input_fn, output)
                if selected:
                    updated = _show(actions.restore(target.identity, selected), output)
                    if updated is not None:
                        config = updated
        else:
            updated = _host(actions, input_fn, output)
            if updated is not None:
                config = updated


def _databases(config, actions, input_fn, output):
    target = _database(config, actions, input_fn, output, allow_add=True)
    if target is None:
        return
    if target is _ADD:
        return _add(actions, input_fn, output)
    current = config
    identity = target.identity
    while True:
        output(
            f"\n{target.identity} ({target.engine})\n"
            "1. Info\n2. Settings\n3. Start\n4. Stop\n5. Restart\n6. Logs\n0. Back"
        )
        choice = _choice(input_fn, output, "Select", {str(value) for value in range(7)})
        if choice in {None, "0"}:
            return current
        if choice == "1":
            output(actions.info(target.identity))
        elif choice == "2":
            values, reset = settings(target, input_fn=input_fn, output=output)
            if values or reset:
                updated = _show(actions.configure(target.identity, values, reset), output)
                if updated is not None:
                    current = updated
                    target = current.select(identity)
        else:
            action = {
                "3": actions.start,
                "4": actions.stop,
                "5": actions.restart,
                "6": actions.logs,
            }[choice]
            updated = _show(action(target.identity), output)
            if updated is not None:
                current = updated
                target = current.select(identity)


def _backups(config, actions, input_fn, output):
    target = _database(config, actions, input_fn, output, durable=True)
    if target is None:
        return
    output("\n1. Create\n2. List\n3. Test\n0. Back")
    choice = _choice(input_fn, output, "Select", {"0", "1", "2", "3"})
    if choice == "1":
        return _show(actions.backup(target.identity), output)
    elif choice == "2":
        output(actions.backup_list(target.identity))
    elif choice == "3":
        selected = _backup(actions, target, input_fn, output)
        if selected:
            return _show(actions.backup_test(target.identity, selected), output)
    return None


def settings(target: Database, *, input_fn=input, output=print) -> tuple[dict, tuple[str, ...]]:
    names = (
        (
            "image",
            "pgbouncer",
            "pgbouncer_image",
            "max_clients",
            "pool_size",
            "reserve_size",
        )
        if target.role == "postgres"
        else (
            (
                "image",
                "mode",
                "http",
                "http_image",
                "http_connections",
                "memory",
                "threads",
            )
            if target.engine == "dragonfly"
            else ("image", "mode", "http", "http_image", "http_connections")
        )
    )
    values = {}
    reset = []
    output("Enter a value, reset, or leave blank to keep the current value.")
    for name in names:
        current = _current(target, name)
        label = "default" if current == _default(target, name) else "custom"
        while True:
            try:
                value = input_fn(f"{name} [{_display(current)} ({label})]: ").strip()
            except EOFError:
                return {}, ()
            if not value:
                break
            if value == "reset":
                reset.append(name)
                break
            try:
                parsed = _parse(value, current)
                if name == "mode" and parsed not in {"durable", "cache"}:
                    raise ValueError
                if name == "memory" and not re.fullmatch(r"[1-9][0-9]*(?:kb|mb|gb)", parsed):
                    raise ValueError
            except (TypeError, ValueError):
                output("Invalid value")
                continue
            values[name] = parsed
            break
    if not values and not reset:
        return {}, ()
    try:
        save = input_fn("Save these settings? [y/N] ").strip().lower()
    except EOFError:
        return {}, ()
    return (values, tuple(reset)) if save in {"y", "yes"} else ({}, ())


def setup(values=None, *, input_fn=input, output=print) -> dict | None:
    result = dict(values or {})
    output("Initial host setup")
    for name, label in _SETUP_FIELDS:
        if result.get(name):
            continue
        value = _text(input_fn, label)
        if value is None:
            return None
        result[name] = value
    if "rclone_config" not in result:
        try:
            value = input_fn("Rclone config (optional): ").strip()
        except EOFError:
            return None
        if value:
            result["rclone_config"] = value
    return result


def _database(config, actions, input_fn, output, durable=False, allow_add=False):
    values = [item for item in config.databases if not durable or item.durable]
    health = actions.health()
    for number, item in enumerate(values, start=1):
        output(f"{number}. {item.identity} ({item.engine}; {health.get(item.identity, 'unknown')})")
    if allow_add:
        output(f"{len(values) + 1}. Add database")
    output("0. Back")
    maximum = len(values) + (1 if allow_add else 0)
    choice = _choice(
        input_fn,
        output,
        "Select database",
        {str(value) for value in range(maximum + 1)},
    )
    if choice in {None, "0"}:
        return None
    if allow_add and int(choice) == len(values) + 1:
        return _ADD
    return values[int(choice) - 1]


def _add(actions, input_fn, output):
    project = _text(input_fn, "Project")
    if project is None:
        return
    output("1. Postgres\n2. KV\n0. Cancel")
    role_choice = _choice(input_fn, output, "Select role", {"0", "1", "2"})
    if role_choice in {None, "0"}:
        return
    role = "postgres" if role_choice == "1" else "kv"
    engine = "dragonfly"
    if role == "kv":
        output("1. Dragonfly (default)\n2. Redis\n0. Cancel")
        engine_choice = _choice(input_fn, output, "Select engine", {"0", "1", "2"})
        if engine_choice in {None, "0"}:
            return
        engine = "dragonfly" if engine_choice == "1" else "redis"
    result = actions.add(project, role, engine)
    return _show(result, output)


def _backup(actions, target, input_fn, output):
    rows = actions.backup_history(target.identity)
    if not rows:
        output("No backups available")
        return None
    for number, row in enumerate(rows, start=1):
        output(f"{number}. {row['time']}  {row['backup']}  {row.get('source', 'unknown')}")
    output("0. Cancel")
    choice = _choice(
        input_fn,
        output,
        "Select backup",
        {str(value) for value in range(len(rows) + 1)},
    )
    if choice in {None, "0"}:
        return None
    selected = rows[int(choice) - 1]
    return selected.get("snapshot") or selected["backup"]


def _host(actions, input_fn, output):
    output("\n1. Check\n2. Setup\n3. Update\n0. Back")
    choice = _choice(input_fn, output, "Select", {"0", "1", "2", "3"})
    if choice in {None, "0"}:
        return
    if choice == "1":
        output(actions.host_check())
    elif choice == "2":
        return _show(actions.host_setup(), output)
    else:
        selected = _text(input_fn, "Exact version (for example 1.2.3)")
        if selected is not None:
            return _show(actions.host_update(selected), output)
    return None


def _show(result: ActionResult, output) -> Config | None:
    if isinstance(result, tuple):
        text, config = result
        output(text)
        return config
    output(result)
    return None


def _choice(input_fn, output, prompt, allowed):
    while True:
        try:
            value = input_fn(f"{prompt}: ").strip()
        except EOFError:
            return None
        if value in allowed:
            return value
        output("Invalid choice")


def _current(target, name):
    if name.startswith("pgbouncer_"):
        return getattr(target.settings.pgbouncer, name.removeprefix("pgbouncer_"))
    if name == "pgbouncer":
        return target.settings.pgbouncer.enabled
    if name.startswith("http_"):
        return getattr(target.settings.http, name.removeprefix("http_"))
    if name == "http":
        return target.settings.http.enabled
    return getattr(target.settings, name)


def _default(target, name):
    settings = (
        Postgres(DEFAULT_IMAGES["postgres"], PgBouncer())
        if target.role == "postgres"
        else KV(
            target.engine,
            DEFAULT_IMAGES[target.engine],
            http=HTTP(),
            memory="256mb" if target.engine == "dragonfly" else None,
            threads=1 if target.engine == "dragonfly" else None,
        )
    )
    default = Database(target.project, target.role, settings, target.host, target.paths)
    return _current(default, name)


def _display(value):
    if value is None:
        return "not set"
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def _text(input_fn, prompt):
    try:
        value = input_fn(f"{prompt}: ").strip()
    except EOFError:
        return None
    return value or None


def _parse(value, current):
    if isinstance(current, bool):
        if value.lower() in {"true", "yes", "on", "1"}:
            return True
        if value.lower() in {"false", "no", "off", "0"}:
            return False
        raise ValueError("boolean value required")
    if isinstance(current, int):
        try:
            return int(value)
        except ValueError:
            raise ValueError("integer value required") from None
    return value
