from __future__ import annotations

import shutil
from getpass import getpass
from typing import Any

from .errors import Error
from .models import Config, Database
from .run import clean


def overview(value: dict[str, Any], *, width: int | None = None) -> str:
    width = width or shutil.get_terminal_size((100, 24)).columns
    identity_width = max(12, min(36, width - 41))
    host_state = "healthy" if value["host"]["healthy"] else "needs attention"
    lines = [
        _fit(f"Host {value['host']['id']}  {host_state}", width),
        "",
        f"{'#':>2}  {'Database':<{identity_width}}  {'Engine':<9}  {'Status':<9}  Backup",
    ]
    for number, (identity, item) in enumerate(value["databases"].items(), 1):
        lines.append(
            f"{number:>2}  {_fit(identity, identity_width):<{identity_width}}  "
            f"{_fit(item['engine'], 9):<9}  {_fit(item['health'], 9):<9}  "
            f"{_backup_text(item['latest_backup'])}"
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
            _pairs(summary),
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
                _screen(output, _pairs(details), heading="Details")
            elif choice == "2":
                _screen(
                    output,
                    _pairs(database.info(current, target)["connection"]),
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
    password = None
    if role == "kv":
        _screen(output, "1. Dragonfly\n2. Redis\n0. Cancel", heading="KV engine")
        selected = _choice(input_fn, output, "Engine", {"0", "1", "2"})
        if selected in {None, "0"}:
            return config
        engine = "dragonfly" if selected == "1" else "redis"
    else:
        output("")
        password = password_fn("Initial Postgres password (blank to generate): ") or None
    _screen(
        output,
        _pairs({"Database": f"{project}/{role}", "Engine": engine or "postgres"}),
        heading="Create database",
    )
    if not _yes(input_fn, "Create? [y/N] "):
        return config
    updated = database.add(config, project, role, engine=engine, password=password)
    _screen(output, f"{project}/{role} is healthy", heading="Created")
    return updated


def _settings(target: Database, input_fn, output) -> dict[str, Any] | None:
    from . import database

    current = database._setting_values(target)
    values = {}
    _screen(output, _pairs(current), heading="Settings")
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
        _pairs({name: f"{current[name]} -> {value}" for name, value in values.items()}),
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
                text = (
                    "No backups available"
                    if not rows
                    else "\n".join(
                        f"{row['time']}  {row['backup']}  {row['source']}  "
                        f"{row.get('snapshot') or '-'}"
                        for row in rows
                    )
                )
                _screen(output, text, heading="Backup history")
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
        _pairs(
            {
                "Host": host["id"],
                "Version": host["tool_version"],
                "Source": {
                    "config": host["source"]["config"],
                    "valid": host["source"]["valid"],
                },
                "Disks": {
                    name: (
                        f"{item['free_gb']} GiB free ({'ok' if item['ok'] else 'low'})"
                        if item.get("available", True)
                        else "unknown"
                    )
                    for name, item in host["disks"].items()
                },
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


def _backup_text(value: dict[str, Any]) -> str:
    state = value["state"]
    if state != "current" or not value.get("time"):
        return state
    from datetime import datetime

    try:
        date = datetime.fromisoformat(value["time"].replace("Z", "+00:00"))
    except ValueError:
        return "current"
    return date.strftime("%m-%d %H:%M")


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


def _pairs(values: dict[str, Any]) -> str:
    lines = []
    for key, value in values.items():
        label = key.replace("_", " ").title()
        if isinstance(value, dict):
            lines.append(f"{label}:")
            lines.extend(
                f"  {name.replace('_', ' ').title()}: {item}" for name, item in value.items()
            )
        else:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def _fit(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width < 7:
        return value[:width]
    left = (width - 1) // 2
    return value[:left] + "~" + value[-(width - left - 1) :]
