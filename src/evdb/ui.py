from __future__ import annotations

import shutil
from getpass import getpass
from typing import Any

from .errors import Error
from .models import Config, Database
from .run import clean


def overview(value: dict[str, Any], *, width: int | None = None) -> str:
    from . import status

    width = width or shutil.get_terminal_size((100, 24)).columns
    identity_width = max(12, min(36, width - 41))
    host_state = "healthy" if value["host"]["healthy"] else "needs attention"
    lines = [
        status.fit(f"Host {value['host']['id']}  {host_state}", width),
        "",
        f"{'#':>2}  {'Database':<{identity_width}}  {'Engine':<9}  {'Status':<9}  Backup",
    ]
    for number, (identity, item) in enumerate(value["databases"].items(), 1):
        lines.append(
            f"{number:>2}  {status.fit(identity, identity_width):<{identity_width}}  "
            f"{status.fit(item['engine'], 9):<9}  {status.fit(item['health'], 9):<9}  "
            f"{status.backup_text(item['latest_backup'])}"
        )
    if not value["databases"]:
        lines.append("    No databases configured")
    return "\n".join(lines)


def run(
    config: Config,
    *,
    input_fn=input,
    output=print,
    password_fn=getpass,
) -> int:
    from . import status

    current = config
    while True:
        value = status.collect(current)
        _screen(output, overview(value), heading="Databases")
        rows = {str(index) for index, _identity in enumerate(value["databases"], 1)}
        add = len(rows) + 1
        host = add + 1
        options = [(str(add), "Add database"), (str(host), "Host"), ("0", "Exit")]
        _options(output, options)
        choice = _choice(input_fn, output, "Select", rows | {key for key, _label in options})
        if choice in {None, "0"}:
            return 0
        try:
            if int(choice) <= len(value["databases"]):
                identity = tuple(value["databases"])[int(choice) - 1]
                current = _database(current, identity, input_fn, output)
            elif int(choice) == add:
                current = _add(current, input_fn, output, password_fn)
            else:
                _host(value, output)
        except Error as exc:
            _screen(output, f"evdb: {exc}", heading="Error")


def _database(config: Config, identity: str, input_fn, output) -> Config:
    from . import database, status

    current = config
    while True:
        target = current.select(identity)
        local_error = None
        try:
            observed = database.observe(current, target)
        except Error as exc:
            observed = {"running": False, "healthy": False, "health": "unknown"}
            local_error = clean(str(exc))
        summary = {
            "Database": target.identity,
            "Engine": target.engine,
            "Status": observed["health"],
            "Backup": "enabled" if target.durable else "disabled",
        }
        if local_error:
            summary["Error"] = local_error
        _screen(
            output,
            pairs(summary),
            heading=target.identity,
        )
        state_action = "Stop" if observed["running"] else "Start"
        options = [
            ("1", "Details"),
            ("2", "Connection"),
            ("3", "Settings"),
            ("4", state_action),
            ("5", "Restart"),
            ("6", "Backups"),
            ("7", "Logs"),
            ("0", "Back"),
        ]
        _options(output, options)
        choice = _choice(input_fn, output, "Select", {key for key, _label in options})
        if choice in {None, "0"}:
            return current
        try:
            if choice == "1":
                value = database.info(current, target)
                selected = status.collect(current, target)["databases"][target.identity]
                details = {key: item for key, item in value.items() if key != "connection"}
                details["error"] = selected["error"] or "none"
                _screen(output, database_details(details), heading="Details")
            elif choice == "2":
                _screen(
                    output,
                    connection_details(database.connection(target)),
                    heading="Connection",
                )
            elif choice == "3":
                values = _settings(target, input_fn, output)
                if values is not None:
                    current = database.configure(current, target, values)
                    _screen(
                        output,
                        f"{identity} settings saved and healthy",
                        heading="Settings",
                    )
            elif choice == "4":
                (database.stop if observed["running"] else database.start)(current, target)
                _screen(
                    output,
                    f"{identity}: {state_action.lower()} complete",
                    heading=state_action,
                )
            elif choice == "5":
                database.restart(current, target)
                _screen(output, f"{identity}: restart complete", heading="Restart")
            elif choice == "6":
                current = _backups(current, target, input_fn, output)
            else:
                _screen(output, database.logs(current, target), heading="Logs")
        except Error as exc:
            _screen(output, f"evdb: {exc}", heading=f"{target.identity} error")


def _add(config: Config, input_fn, output, password_fn) -> Config:
    from . import database

    project = _text(input_fn, "Project")
    if not project:
        return config
    _screen(output, "1. Postgres\n2. KV\n0. Cancel", heading="Add database")
    role_choice = _choice(input_fn, output, "Role", {"0", "1", "2"})
    if role_choice in {None, "0"}:
        return config
    role = "postgres" if role_choice == "1" else "kv"
    engine = None
    data_root = config.host.data_roots[0]
    password = None
    username = None
    database_name = None
    if role == "kv":
        _screen(output, "1. Dragonfly\n2. Redis\n0. Cancel", heading="KV engine")
        selected = _choice(input_fn, output, "Engine", {"0", "1", "2"})
        if selected in {None, "0"}:
            return config
        engine = "dragonfly" if selected == "1" else "redis"
    elif confirm(input_fn, "Advanced Postgres identity?"):
        from .config import validate_postgres_name

        username = ask_text(
            input_fn,
            output,
            "Username",
            default="default",
            validate=lambda value: validate_postgres_name(value, "username"),
        )
        database_name = ask_text(
            input_fn,
            output,
            "Database name",
            default="postgres",
            validate=lambda value: validate_postgres_name(value, "database name"),
        )
        password = ask_secret(
            password_fn,
            output,
            "Initial Postgres password",
            required=True,
            confirm=True,
        )
        if username is None or database_name is None or password is None:
            return config
    if len(config.host.data_roots) > 1:
        selected = choose(
            input_fn,
            output,
            "Database data root",
            [(str(index), str(path)) for index, path in enumerate(config.host.data_roots, 1)],
        )
        if selected is None:
            return config
        data_root = config.host.data_roots[int(selected) - 1]
    summary = {
        "Database": f"{project}/{role}",
        "Engine": engine or "postgres",
        "Data root": str(data_root),
    }
    if role == "postgres":
        summary.update(
            Username=username or "default",
            Database=database_name or "postgres",
            Password="provided" if password else "generated",
        )
    _screen(
        output,
        pairs(summary),
        heading="Create database",
    )
    if not _yes(input_fn, "Create? [y/N] "):
        return config
    updated = database.add(
        config,
        project,
        role,
        engine=engine,
        password=password,
        username=username,
        database_name=database_name,
        data_root=data_root,
    )
    _screen(output, f"{project}/{role} is healthy", heading="Created")
    return updated


def _settings(target: Database, input_fn, output) -> dict[str, Any] | None:
    from . import database

    current = database._setting_values(target)
    values = {}
    _screen(output, pairs(current), heading="Settings")
    for name, old in current.items():
        while True:
            entered = _text(input_fn, f"{name} [{old}]", blank=True)
            if not entered:
                break
            try:
                candidate = {**values, name: _parse(entered, old)}
                database._settings(target, candidate, ())
            except Error as exc:
                _screen(output, str(exc), heading=f"Invalid {name}")
                continue
            values = candidate
            break
    if not values:
        return None
    _screen(
        output,
        pairs({name: f"{current[name]} -> {value}" for name, value in values.items()}),
        heading="Save settings",
    )
    return values if _yes(input_fn, "Save? [y/N] ") else None


def _backups(config: Config, target: Database, input_fn, output) -> Config:
    from . import backup

    if not target.durable:
        _screen(output, "Backups are disabled for this cache database", heading="Backups")
        return config
    while True:
        _screen(output, "1. Create\n2. History\n0. Back", heading="Backups")
        choice = _choice(input_fn, output, "Select", {"0", "1", "2"})
        if choice in {None, "0"}:
            return config
        try:
            if choice == "1":
                result = backup.create(config, target)
                _screen(
                    output,
                    f"{target.identity}: backup {result['backup']} completed "
                    f"at {result['finished']}\n"
                    f"Snapshot: {result['snapshot']}\nRepository: {result['repository']}",
                    heading="Backup complete",
                )
            else:
                rows = backup.history(config, target)
                _screen(output, backup_history(rows), heading="Backup history")
        except Error as exc:
            _screen(output, f"evdb: {exc}", heading=f"{target.identity} backup error")


def _host(value: dict[str, Any], output) -> None:
    host = value["host"]
    infrastructure = host["infrastructure"]
    errors = [
        f"{item['scope']}: {item['message']}"
        for item in value["errors"]
        if item["scope"].startswith("host/")
    ]
    _screen(
        output,
        pairs(
            {
                "Host": host["id"],
                "Version": host["tool_version"],
                "Source": {
                    "config": host["source"]["config"],
                    "valid": host["source"]["valid"],
                },
                "Storage": (
                    f"{host['storage']['free_gb']} GiB free "
                    f"({'ok' if host['storage']['ok'] else 'low'})"
                    if host["storage"].get("available", True)
                    else "unknown"
                ),
                "Listeners": {
                    port: _state(ready, "listening", "missing")
                    for port, ready in infrastructure["listeners"].items()
                },
                "Network": _state(infrastructure["network"], "healthy", "missing"),
                "Traefik": _state(infrastructure["traefik"], "healthy", "unhealthy"),
                "ACME": _state(infrastructure["acme"], "ready", "missing or unsafe"),
                "Repository": {
                    "url": host["repository"]["url"],
                    "ready": _state(host["repository"]["ready"], "yes", "no"),
                },
                "Timer": {
                    "unit": host["timer"]["unit"],
                    "loaded": _state(host["timer"]["loaded"], "yes", "no"),
                    "enabled": _state(host["timer"]["enabled"], "yes", "no"),
                    "active": _state(host["timer"]["active"], "yes", "no"),
                },
                "Errors": "\n".join(errors) if errors else "none",
            }
        ),
        heading="Host",
    )


def _state(value: bool | None, ready: str, missing: str) -> str:
    if value is None:
        return "unknown"
    return ready if value else missing


def _screen(output, text: str, *, heading: str | None = None) -> None:
    output("")
    if heading:
        output(clean(heading))
    output(clean(text))
    output("")


def _options(output, options: list[tuple[str, str]]) -> None:
    output("\n".join(f"{key}. {label}" for key, label in options))
    output("")


def _choice(input_fn, output, prompt: str, allowed: set[str]) -> str | None:
    while True:
        try:
            value = input_fn(f"{prompt}: ").strip()
        except EOFError:
            return None
        if value in allowed:
            return value
        output("")
        output(_invalid_choice(allowed))
        output("")


def _invalid_choice(allowed: set[str]) -> str:
    if allowed and all(item.isdigit() for item in allowed):
        numbers = sorted(int(item) for item in allowed)
        if numbers == list(range(numbers[0], numbers[-1] + 1)):
            return f"Invalid choice; enter {numbers[0]}-{numbers[-1]}"
    return "Invalid choice; enter " + ", ".join(sorted(allowed))


def _text(input_fn, prompt: str, *, blank: bool = False) -> str | None:
    try:
        value = input_fn(f"{prompt}: ").strip()
    except EOFError:
        return None
    return value if value or blank else None


def _yes(input_fn, prompt: str) -> bool:
    try:
        return input_fn(prompt).strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def ask_text(
    input_fn,
    output,
    prompt: str,
    *,
    default: str | None = None,
    help_text: str | None = None,
    required: bool = True,
    validate=None,
) -> str | None:
    if help_text:
        output(help_text)
    while True:
        suffix = f" [{default}]" if default is not None else ""
        try:
            value = input_fn(f"{prompt}{suffix}: ").strip()
        except EOFError:
            return None
        value = value or default
        if value is None or not value:
            if not required:
                return None
            output(f"{prompt} is required")
            continue
        if validate is None:
            return value
        try:
            return validate(value)
        except (Error, ValueError) as exc:
            output(clean(str(exc)))


def choose(
    input_fn,
    output,
    prompt: str,
    choices: list[tuple[str, str]],
    *,
    default: str | None = None,
) -> str | None:
    _options(output, choices)
    while True:
        suffix = f" [{default}]" if default is not None else ""
        try:
            value = input_fn(f"{prompt}{suffix}: ").strip() or default
        except EOFError:
            return None
        allowed = {key for key, _label in choices}
        if value in allowed:
            return value
        output(_invalid_choice(allowed))


def ask_secret(
    password_fn,
    output,
    prompt: str,
    *,
    required: bool = False,
    confirm: bool = False,
) -> str | None:
    while True:
        value = password_fn(f"{prompt}: ")
        if not value:
            if required:
                output(f"{prompt} is required")
                continue
            return None
        if any(char in value for char in "\0\r\n"):
            output(f"{prompt} must be one non-empty line")
            continue
        if confirm and password_fn(f"Confirm {prompt.lower()}: ") != value:
            output("Passwords do not match")
            continue
        return value


def confirm(input_fn, prompt: str, *, default: bool = False) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        value = input_fn(prompt + suffix).strip().lower()
    except EOFError:
        return False
    if not value:
        return default
    return value in {"y", "yes"}


def _parse(value: str, current: Any) -> Any:
    if isinstance(current, bool):
        if value.lower() in {"true", "yes", "on", "1"}:
            return True
        if value.lower() in {"false", "no", "off", "0"}:
            return False
        raise Error("boolean value required")
    if isinstance(current, int):
        try:
            return int(value)
        except ValueError as exc:
            raise Error("integer value required") from exc
    return value


def pairs(values: dict[str, Any]) -> str:
    lines = _pair_lines(values)
    return clean("\n".join(lines))


def _pair_lines(values: dict[str, Any], *, indent: int = 0) -> list[str]:
    lines = []
    prefix = " " * indent
    for key, value in values.items():
        label = key.replace("_", " ").title()
        if isinstance(value, dict):
            lines.append(f"{prefix}{label}:")
            lines.extend(_pair_lines(value, indent=indent + 2))
        elif isinstance(value, (list, tuple)):
            lines.append(f"{prefix}{label}: {', '.join(map(str, value)) or 'none'}")
        else:
            lines.append(f"{prefix}{label}: {value}")
    return lines


def database_details(value: dict[str, Any], *, include_connection: bool = False) -> str:
    lines = []
    summary = {
        key: value[key]
        for key in ("database", "engine", "status", "image", "error")
        if key in value
    }
    if summary:
        lines.extend(_pair_lines(summary))
    paths = {key: value[key] for key in ("data", "compose") if key in value}
    for heading, section in (
        ("Sidecars", value.get("sidecar_images")),
        ("Settings", value.get("settings")),
        ("Engine", value.get("engine_info")),
        ("Backup", value.get("backup")),
        ("Paths", paths),
    ):
        if not section:
            continue
        if lines:
            lines.append("")
        lines.append(f"{heading}:")
        lines.extend(_pair_lines(section, indent=2))
    if include_connection and value.get("connection"):
        if lines:
            lines.append("")
        lines.append("Connection:")
        lines.extend(_pair_lines(value["connection"], indent=2))
    return clean("\n".join(lines))


def connection_details(value: dict[str, Any]) -> str:
    native = {
        key: value[key] for key in ("url", "username", "password", "database") if key in value
    }
    optional = {key: item for key, item in value.items() if key not in native}
    lines = _pair_lines(native)
    if optional:
        lines.extend(("", "HTTP:"))
        lines.extend(_pair_lines(optional, indent=2))
    return clean("\n".join(lines))


def backup_history(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No backups available"
    values = ["Time  Backup  Source  Snapshot"]
    values.extend(
        f"{row['time']}  {row['backup']}  {row['source']}  {row.get('snapshot') or '-'}"
        for row in rows
    )
    return "\n".join(values)
