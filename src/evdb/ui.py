from __future__ import annotations

import os
import re
import shutil
from contextlib import contextmanager
from getpass import getpass
from typing import IO, Any

from rich.console import Console
from rich.text import Text

from .errors import Error
from .models import Config, Database
from .run import clean

_PROMPT_DEFAULT = re.compile(r"\[([^]]+)](?=\s*:?[ ]*$)")


class Terminal:
    def __init__(self, console: Console):
        self.console = console

    def __call__(self, value: Any = "") -> None:
        self.text(value)

    def text(
        self,
        value: Any = "",
        *,
        style: str | None = None,
        states: tuple[tuple[str, str], ...] = (),
        bold_lines: tuple[int, ...] = (),
    ) -> None:
        text = Text(clean(str(value)), style=style)
        _style_states(text, states)
        _style_lines(text, bold_lines)
        self.console.print(text, soft_wrap=True)

    def heading(self, value: str, *, error: bool = False) -> None:
        self.text(value, style="bold red" if error else "bold")

    def input(self, prompt: str) -> str:
        return self.console.input(_prompt(prompt))

    def read_secret(self, prompt: str) -> str:
        self.console.print(_prompt(prompt), end="")
        return getpass("", echo_char="*")

    @contextmanager
    def loading(self, message: str):
        with self.console.status(
            Text(message), spinner_style="cyan", refresh_per_second=8
        ) as status:
            yield lambda value: status.update(status=Text(value))

    def error(self, value: Any) -> None:
        text = Text(clean(str(value)))
        end = text.plain.find(":")
        text.stylize("bold red", 0, end + 1 if end >= 0 else len(text))
        self.console.print(text, soft_wrap=True)

    def status(self, value: dict[str, Any]) -> None:
        from . import status

        self.text(
            status.render(value, width=self.console.width),
            states=_overview_states(value),
            bold_lines=(0, 1),
        )


def terminal(
    *,
    file: IO[str] | None = None,
    stderr: bool = False,
    force_terminal: bool | None = None,
) -> Terminal:
    return Terminal(
        Console(
            file=file,
            stderr=stderr,
            force_terminal=force_terminal,
            highlight=False,
            markup=False,
            no_color=_no_color(),
        )
    )


def _no_color() -> bool | None:
    if os.getenv("NO_COLOR") is not None or os.getenv("TERM") == "dumb":
        return True
    return None


def _prompt(value: str) -> Text:
    text = Text(value)
    match = _PROMPT_DEFAULT.search(value)
    label_end = match.start() if match else len(value.rstrip(" :"))
    text.stylize("bold cyan", 0, label_end)
    if match:
        default = match.group(1)
        upper = next((index for index, char in enumerate(default) if char.isupper()), None)
        start = match.start(1) + (upper or 0)
        end = start + 1 if upper is not None else match.end(1)
        text.stylize("bold", start, end)
    return text


def _style_states(text: Text, states: tuple[tuple[str, str], ...]) -> None:
    for value, style in states:
        if not value:
            continue
        pattern = rf"(?<!\w){re.escape(value)}(?!\w)"
        for match in re.finditer(pattern, text.plain):
            text.stylize(style, match.start(), match.end())


def _style_lines(text: Text, lines: tuple[int, ...]) -> None:
    start = 0
    for number, line in enumerate(text.plain.splitlines(keepends=True)):
        end = start + len(line.rstrip("\n"))
        if number in lines:
            text.stylize("bold", start, end)
        start += len(line)


def _state_style(value: str) -> str | None:
    if value in {"healthy", "ready", "current", "complete", "completed", "listening"}:
        return "green"
    if value in {"needs attention", "stale/missing", "unknown", "stopped", "low"}:
        return "yellow"
    if value in {"unhealthy", "failed", "missing", "missing or unsafe"}:
        return "red"
    return None


def _overview_states(value: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    from . import status

    host_state = "healthy" if value["host"]["healthy"] else "needs attention"
    states = [(host_state, _state_style(host_state))]
    for item in value["databases"].values():
        health = item["health"]
        states.append((health, _state_style(health)))
        backup = status.backup_text(item["latest_backup"])
        if item["latest_backup"]["state"] == "current":
            states.append((backup, "green"))
        elif item["latest_backup"]["state"] == "stale":
            states.append((backup, "yellow"))
    return tuple((text, style) for text, style in states if style)


def show_status(output, value: dict[str, Any]) -> None:
    from . import status

    if isinstance(output, Terminal):
        output.status(value)
    else:
        output(status.render(value))


@contextmanager
def loading(output, message: str):
    if isinstance(output, Terminal) and output.console.is_terminal and os.getenv("TERM") != "dumb":
        with output.loading(message) as update:
            yield update
    else:
        yield lambda _value: None


def success(output, value: str) -> None:
    states = tuple(
        (word, "green") for word in ("healthy", "complete", "completed") if word in value
    )
    if isinstance(output, Terminal):
        output.text(value, states=states)
    else:
        output(value)


def read_secret(prompt: str) -> str:
    return getpass(prompt, echo_char="*")


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
    password_fn=read_secret,
) -> int:
    from . import status

    current = config
    while True:
        with loading(output, "Checking host and databases") as update:
            value = status.collect(current, progress=update)
        width = output.console.width if isinstance(output, Terminal) else None
        _screen(
            output,
            overview(value, width=width),
            heading="Databases",
            states=_overview_states(value),
            bold_lines=(0, 2),
        )
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
                current = _database(
                    current,
                    identity,
                    input_fn,
                    output,
                    initial=value["databases"][identity],
                )
            elif int(choice) == add:
                current = _add(current, input_fn, output, password_fn)
            else:
                _host(value, output)
        except Error as exc:
            _screen(output, f"evdb: {exc}", heading="Error", error=True)


def _database(
    config: Config,
    identity: str,
    input_fn,
    output,
    *,
    initial: dict[str, Any] | None = None,
) -> Config:
    from . import database

    current = config
    observed = initial
    initial_error = initial.get("error") if initial and initial.get("running") is None else None
    while True:
        target = current.select(identity)
        local_error = initial_error
        initial_error = None
        if observed is None:
            try:
                with loading(output, f"Checking {identity}"):
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
            states=((observed["health"], _state_style(observed["health"])),)
            if _state_style(observed["health"])
            else (),
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
                with loading(output, f"Loading {identity} details") as update:
                    value = database.info(
                        current,
                        target,
                        observed=observed,
                        runtime_error=local_error,
                        progress=update,
                    )
                details = {key: item for key, item in value.items() if key != "connection"}
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
                        states=(("healthy", "green"),),
                    )
            elif choice == "4":
                (database.stop if observed["running"] else database.start)(current, target)
                _screen(
                    output,
                    f"{identity}: {state_action.lower()} complete",
                    heading=state_action,
                    states=(("complete", "green"),),
                )
            elif choice == "5":
                database.restart(current, target)
                _screen(
                    output,
                    f"{identity}: restart complete",
                    heading="Restart",
                    states=(("complete", "green"),),
                )
            elif choice == "6":
                current = _backups(current, target, input_fn, output)
            else:
                with loading(output, f"Loading {identity} logs"):
                    text = database.logs(current, target)
                _screen(output, text, heading="Logs")
        except Error as exc:
            _screen(
                output,
                f"evdb: {exc}",
                heading=f"{target.identity} error",
                error=True,
            )
        observed = None


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
    _screen(
        output,
        f"{project}/{role} is healthy",
        heading="Created",
        states=(("healthy", "green"),),
    )
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
                _screen(output, str(exc), heading=f"Invalid {name}", error=True)
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
                with loading(output, f"Creating {target.identity} backup"):
                    result = backup.create(config, target)
                _screen(
                    output,
                    f"{target.identity}: backup {result['backup']} completed "
                    f"at {result['finished']}\n"
                    f"Snapshot: {result['snapshot']}\nRepository: {result['repository']}",
                    heading="Backup complete",
                    states=(("completed", "green"),),
                )
            else:
                with loading(output, f"Loading {target.identity} backup history"):
                    rows = backup.history(config, target)
                _screen(output, backup_history(rows), heading="Backup history")
        except Error as exc:
            _screen(
                output,
                f"evdb: {exc}",
                heading=f"{target.identity} backup error",
                error=True,
            )


def _host(value: dict[str, Any], output) -> None:
    host = value["host"]
    infrastructure = host["infrastructure"]
    errors = [
        f"{item['scope']}: {item['message']}"
        for item in value["errors"]
        if item["scope"].startswith("host/")
    ]
    storage_state = (
        ("ok", "green")
        if host["storage"].get("ok")
        else ("low", "yellow")
        if host["storage"].get("available", True)
        else ("unknown", "yellow")
    )
    states = [storage_state]
    states.extend(
        _state_tone(ready, "listening", "missing") for ready in infrastructure["listeners"].values()
    )
    states.extend(
        (
            _state_tone(infrastructure["network"], "healthy", "missing"),
            _state_tone(infrastructure["traefik"], "healthy", "unhealthy"),
            _state_tone(infrastructure["acme"], "ready", "missing or unsafe"),
        )
    )
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
        states=tuple(states),
    )


def _state(value: bool | None, ready: str, missing: str) -> str:
    if value is None:
        return "unknown"
    return ready if value else missing


def _state_tone(value: bool | None, ready: str, missing: str) -> tuple[str, str]:
    text = _state(value, ready, missing)
    return text, "yellow" if value is None else "green" if value else "red"


def _screen(
    output,
    text: str,
    *,
    heading: str | None = None,
    states: tuple[tuple[str, str], ...] = (),
    bold_lines: tuple[int, ...] = (),
    error: bool = False,
) -> None:
    output("")
    if heading:
        if isinstance(output, Terminal):
            output.heading(clean(heading), error=error)
        else:
            output(clean(heading))
    if isinstance(output, Terminal):
        output.text(clean(text), states=states, bold_lines=bold_lines)
    else:
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
