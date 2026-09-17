from __future__ import annotations

import os
import re
import select
import shutil
import sys
import termios
import time
import tty
from contextlib import contextmanager
from getpass import getpass
from threading import Condition, Event, Thread
from typing import IO, Any

from rich.console import Console, Group
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .errors import Error
from .models import Config, Database
from .run import Cancelled, cancellable, clean

_PROMPT_DEFAULT = re.compile(r"\[([^]]+)](?=\s*:?[ ]*$)")
_STATUS_TTL = 60


class _StatusSession:
    def __init__(self, config: Config, *, collector=None, clock=time.monotonic):
        from . import status

        self.config = config
        self._collector = collector or status.collect
        self._clock = clock
        self._condition = Condition()
        self._value = status.pending(config)
        self._finished: float | None = None
        self._thread: Thread | None = None
        self._cancel: Event | None = None
        self._generation = 0
        self._revision = 0
        self._callback = None
        self._error: BaseException | None = None

    @property
    def value(self) -> dict[str, Any]:
        with self._condition:
            return self._value

    def snapshot(self) -> tuple[int, dict[str, Any]]:
        with self._condition:
            return self._revision, self._value

    def subscribe(self, callback) -> None:
        with self._condition:
            self._callback = callback
            value = self._value
        if callback is not None:
            callback(value)

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                return
            if self._finished is not None and self._clock() - self._finished < _STATUS_TTL:
                return
            self._generation += 1
            generation = self._generation
            cancel = Event()
            self._cancel = cancel
            self._error = None
            thread = Thread(
                target=self._refresh,
                args=(generation, cancel),
                name="evdb-status",
            )
            self._thread = thread
            thread.start()

    def database(self, identity: str) -> dict[str, Any]:
        with self._condition:
            return self._value["databases"][identity]

    def wait_database(self, identity: str) -> dict[str, Any] | None:
        self.start()
        with self._condition:
            while self._value["databases"][identity]["health"] == "checking":
                self._raise_error()
                if self._thread is None:
                    return None
                self._condition.wait()
            self._raise_error()
            return self._value["databases"][identity]

    def wait_host(self) -> dict[str, Any] | None:
        self.start()
        with self._condition:
            while "infrastructure" not in self._value["host"]:
                self._raise_error()
                if self._thread is None:
                    return None
                self._condition.wait()
            self._raise_error()
            return self._value

    def invalidate(self, config: Config | None = None) -> None:
        from . import status

        self.cancel()
        with self._condition:
            if config is not None:
                self.config = config
            self._value = status.pending(self.config)
            self._revision += 1
            self._finished = None
            self._error = None

    def cancel(self) -> None:
        with self._condition:
            thread = self._thread
            cancel = self._cancel
            if thread is None:
                return
            self._generation += 1
        if cancel is not None:
            cancel.set()
        thread.join()
        with self._condition:
            if self._thread is thread:
                self._thread = None
                self._cancel = None
            self._condition.notify_all()

    def close(self) -> None:
        self.subscribe(None)
        self.cancel()

    def raise_error(self) -> None:
        with self._condition:
            self._raise_error()

    def _refresh(self, generation: int, cancel: Event) -> None:
        try:
            with cancellable(cancel):
                value = self._collector(
                    self.config,
                    preview=lambda current: self._preview(generation, current),
                )
            self._publish(generation, value, finished=True)
        except Cancelled:
            pass
        except BaseException as exc:
            with self._condition:
                if generation == self._generation:
                    self._error = exc
                    self._condition.notify_all()
        finally:
            with self._condition:
                if generation == self._generation:
                    self._thread = None
                    self._cancel = None
                    self._condition.notify_all()

    def _publish(self, generation: int, value: dict[str, Any], *, finished=False) -> None:
        with self._condition:
            if generation != self._generation:
                return
            self._value = value
            self._revision += 1
            if finished:
                self._finished = self._clock()
            callback = self._callback
            self._condition.notify_all()
        if callback is not None:
            callback(value)

    def _preview(self, generation: int, value: dict[str, Any]) -> None:
        with self._condition:
            complete = not self._value.get("pending", False)
        if complete and value.get("pending") and "infrastructure" not in value["host"]:
            return
        self._publish(generation, value)

    def _raise_error(self) -> None:
        if self._error is not None:
            raise self._error


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

    @contextmanager
    def live(self, renderable):
        with Live(
            renderable,
            console=self.console,
            refresh_per_second=8,
            redirect_stdout=False,
            redirect_stderr=False,
        ) as live:
            yield lambda value: live.update(value, refresh=True)

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
    if value in {"checking", "loading"}:
        return "cyan"
    return None


def _overview_states(value: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    from . import status

    host_state = status.host_text(value)
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
    host_state = status.host_text(value)
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


def check_status(
    output,
    config: Config,
    database: Database | None = None,
    *,
    guided: bool = False,
) -> tuple[dict[str, Any], bool]:
    from . import status

    message = "Checking host and databases" if guided else "Checking status"
    if _live_enabled(output):
        if guided:
            output("")
        initial = (
            Group(Text("Databases", style="bold"), Spinner("dots", text=message))
            if guided
            else Spinner("dots", text=message)
        )
        with output.live(initial) as update:
            value = status.collect(
                config,
                database,
                preview=lambda current: update(
                    _status_table(current, guided=guided, width=output.console.width)
                ),
            )
            update(_status_table(value, guided=guided, width=output.console.width))
        return value, True
    with loading(output, message) as update:
        return status.collect(config, database, progress=update), False


def _live_enabled(output) -> bool:
    return (
        isinstance(output, Terminal) and output.console.is_terminal and os.getenv("TERM") != "dumb"
    )


def _status_table(value: dict[str, Any], *, guided: bool, width: int):
    from . import status

    identity_width = max(12, min(36, width - (41 if guided else 37)))
    state = status.host_text(value)
    host = value["host"]
    summary = Text(f"Host {host['id']}")
    if not guided:
        summary.append(f"  evdb {host['tool_version']}")
    summary.append("  ")
    summary.append(state, style=_state_style(state))
    table = Table(
        box=None,
        collapse_padding=True,
        expand=False,
        pad_edge=False,
        padding=(0, 2),
    )
    if guided:
        table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Database", width=identity_width, no_wrap=True)
    table.add_column("Engine", width=9, no_wrap=True)
    table.add_column("Status", width=9, no_wrap=True)
    table.add_column("Backup", no_wrap=True)
    for number, (identity, item) in enumerate(value["databases"].items(), 1):
        health = item["health"]
        backup_state = item["latest_backup"]
        backup_text = status.backup_text(backup_state)
        backup_style = (
            "green"
            if backup_state["state"] == "current"
            else "yellow"
            if backup_state["state"] == "stale"
            else _state_style(backup_text)
        )
        backup_value = (
            Spinner("dots", text="loading", style="cyan")
            if backup_state["state"] == "loading"
            else Text(backup_text, style=backup_style)
        )
        row = [
            status.fit(identity, identity_width),
            status.fit(item["engine"], 9),
            Text(status.fit(health, 9), style=_state_style(health)),
            backup_value,
        ]
        if guided:
            row.insert(0, str(number))
        table.add_row(*row)
    if not value["databases"]:
        row = ["No databases configured", "", "", ""]
        if guided:
            row.insert(0, "")
        table.add_row(*row)
    renderables = [summary, Text(""), table]
    if guided:
        renderables.insert(0, Text("Databases", style="bold"))
    return Group(*renderables)


def _root_view(
    value: dict[str, Any],
    options: list[tuple[str, str]],
    *,
    width: int,
    answer: str = "",
    error: str | None = None,
):
    prompt = _prompt("Select: ")
    prompt.append(answer)
    prompt.end = ""
    values = [
        _status_table(value, guided=True, width=width),
        Text(""),
        Text("\n".join(f"{key}. {label}" for key, label in options)),
        Text(""),
    ]
    if error:
        values.extend((Text(error, style="red"), Text("")))
    values.append(prompt)
    return Group(*values)


def _live_input(input_fn, output) -> bool:
    return (
        isinstance(output, Terminal)
        and getattr(input_fn, "__self__", None) is output
        and sys.stdin.isatty()
    )


def _live_choice(
    output: Terminal,
    session: _StatusSession,
    options: list[tuple[str, str]],
    allowed: set[str],
) -> str | None:
    descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(descriptor)
    answer = ""
    error = None
    revision = -1
    width = output.console.width
    try:
        tty.setcbreak(descriptor)
        with output.live(_root_view(session.value, options, width=width)) as update:
            session.start()
            output.console.show_cursor(True)
            while True:
                current_revision, value = session.snapshot()
                if current_revision != revision:
                    revision = current_revision
                    update(_root_view(value, options, width=width, answer=answer, error=error))
                ready, _write, _error = select.select([descriptor], [], [], 0.05)
                if not ready:
                    continue
                text = os.read(descriptor, 64).decode(errors="ignore")
                for character in text:
                    if character == "\x03":
                        raise KeyboardInterrupt
                    if character == "\x04" and not answer:
                        return None
                    if character in "\r\n":
                        if answer in allowed:
                            return answer
                        error = _invalid_choice(allowed)
                        answer = ""
                    elif character in {"\x08", "\x7f"}:
                        answer = answer[:-1]
                        error = None
                    elif character.isdigit():
                        answer += character
                        error = None
                update(_root_view(value, options, width=width, answer=answer, error=error))
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)


def run(
    config: Config,
    *,
    input_fn=input,
    output=print,
    password_fn=read_secret,
) -> int:
    current = config
    session = _StatusSession(config)
    try:
        while True:
            if current != session.config:
                session.invalidate(current)
            rows = {str(index) for index, _target in enumerate(current.databases, 1)}
            add = len(rows) + 1
            host = add + 1
            options = [(str(add), "Add database"), (str(host), "Host"), ("0", "Exit")]
            allowed = rows | {key for key, _label in options}
            live = _live_enabled(output)
            if live and _live_input(input_fn, output):
                choice = _live_choice(output, session, options, allowed)
                value = session.value
            elif live:
                width = output.console.width
                with output.live(_root_view(session.value, options, width=width)) as update:
                    session.subscribe(
                        lambda value, options=options, width=width: update(
                            _root_view(value, options, width=width)
                        )
                    )
                    session.start()
                    output.console.show_cursor(True)
                    choice = _choice(input_fn, output, None, allowed)
                    session.subscribe(None)
                value = session.value
            else:
                value, rendered = check_status(output, current, guided=True)
                if not rendered:
                    _screen(
                        output,
                        overview(value),
                        heading="Databases",
                        states=_overview_states(value),
                        bold_lines=(0, 2),
                    )
                _options(output, options)
                choice = _choice(input_fn, output, "Select", allowed)
            session.raise_error()
            if choice in {None, "0"}:
                return 0
            try:
                if int(choice) <= len(current.databases):
                    identity = current.databases[int(choice) - 1].identity
                    current = _database(
                        current,
                        identity,
                        input_fn,
                        output,
                        initial=value["databases"][identity],
                        session=session if live else None,
                    )
                elif int(choice) == add:
                    current = _add(
                        current,
                        input_fn,
                        output,
                        password_fn,
                        session=session if live else None,
                    )
                else:
                    if live:
                        with loading(output, "Checking host"):
                            host_value = session.wait_host()
                    else:
                        host_value = value
                    if host_value is not None:
                        current = _host(
                            current,
                            host_value,
                            input_fn,
                            output,
                            session=session if live else None,
                        )
            except Error as exc:
                _screen(output, f"evdb: {exc}", heading="Error", error=True)
    finally:
        session.close()


def _database(
    config: Config,
    identity: str,
    input_fn,
    output,
    *,
    initial: dict[str, Any] | None = None,
    session: _StatusSession | None = None,
) -> Config:
    from . import database

    current = config
    observed = initial
    initial_error = initial.get("error") if initial and initial.get("running") is None else None
    while True:
        target = current.select(identity)
        local_error = initial_error or (
            observed.get("error") if observed and observed.get("running") is None else None
        )
        initial_error = None
        if observed is None:
            observed = session.database(identity) if session is not None else None
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
        state_action = (
            "Start/Stop"
            if observed["health"] == "checking"
            else "Stop"
            if observed["running"]
            else "Start"
        )
        options = [
            ("1", "Details"),
            ("2", "Connection"),
            ("3", "Settings"),
            ("4", state_action),
            ("5", "Restart"),
            ("6", "Backups"),
            ("7", "Logs"),
            ("8", "Delete"),
            ("0", "Back"),
        ]
        _options(output, options)
        choice = _choice(input_fn, output, "Select", {key for key, _label in options})
        if choice in {None, "0"}:
            return current
        try:
            if choice == "1":
                observed = _runtime(current, target, observed, output, session)
                if session is not None:
                    session.cancel()
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
                    if session is not None:
                        session.invalidate(current)
                    with loading(output, f"Applying {identity} settings"):
                        current = database.configure(current, target, values)
                    if session is not None:
                        session.invalidate(current)
                    _screen(
                        output,
                        f"{identity} settings saved and healthy",
                        heading="Settings",
                        states=(("healthy", "green"),),
                    )
            elif choice == "4":
                observed = _runtime(current, target, observed, output, session)
                action = "Stop" if observed["running"] else "Start"
                if session is not None:
                    session.invalidate(current)
                (database.stop if observed["running"] else database.start)(current, target)
                _screen(
                    output,
                    f"{identity}: {action.lower()} complete",
                    heading=action,
                    states=(("complete", "green"),),
                )
            elif choice == "5":
                if session is not None:
                    session.invalidate(current)
                database.restart(current, target)
                _screen(
                    output,
                    f"{identity}: restart complete",
                    heading="Restart",
                    states=(("complete", "green"),),
                )
            elif choice == "6":
                current = _backups(current, target, input_fn, output, session=session)
            elif choice == "7":
                with loading(output, f"Loading {identity} logs"):
                    text = database.logs(current, target)
                _screen(output, text, heading="Logs")
            else:
                deleted = _delete(current, target, input_fn, output, session)
                if deleted is not None:
                    return deleted
        except Error as exc:
            _screen(
                output,
                f"evdb: {exc}",
                heading=f"{target.identity} error",
                error=True,
            )
        observed = session.database(identity) if session is not None else None


def _runtime(
    config: Config,
    target: Database,
    observed: dict[str, Any],
    output,
    session: _StatusSession | None,
) -> dict[str, Any]:
    if observed["health"] != "checking":
        return observed
    if session is not None:
        with loading(output, f"Checking {target.identity}"):
            value = session.wait_database(target.identity)
        if value is not None:
            return value
    from . import database

    with loading(output, f"Checking {target.identity}"):
        return database.observe(config, target)


def _delete(
    config: Config,
    target: Database,
    input_fn,
    output,
    session: _StatusSession | None,
) -> Config | None:
    from . import database, status
    from .files import allocated

    if session is not None:
        session.cancel()
    with loading(output, f"Checking {target.identity} backups"):
        backup_state, backup_errors = status._backup(config, target)
    try:
        size = _bytes(allocated(target.data))
    except OSError:
        size = "unknown"
    current_backup = backup_state["state"] == "current"
    phrase = "DELETE" if current_backup else "DELETE WITHOUT BACKUP"
    backup_text = f"current at {backup_state['time']}" if current_backup else backup_state["state"]
    if backup_errors:
        backup_text += f" ({backup_errors[0]['message']})"
    _screen(
        output,
        pairs(
            {
                "Database": target.identity,
                "Engine": target.engine,
                "Live data": f"{target.data} ({size})",
                "Generated": str(target.generated),
                "Remote backup": backup_text,
                "Local backups retained": str(
                    config.paths.role_backups(target.project, target.role)
                ),
                "Remote backups retained": config.host.backup.repository,
            }
        ),
        heading="Delete database",
        error=True,
    )
    if not confirm(input_fn, "Delete this database?"):
        return None
    identity = ask_text(input_fn, output, f"Type {target.identity} to continue")
    if identity != target.identity:
        _screen(output, "Database identity did not match", heading="Delete cancelled")
        return None
    confirmation = ask_text(input_fn, output, f"Type {phrase} to permanently delete live data")
    if confirmation != phrase:
        _screen(output, "Confirmation phrase did not match", heading="Delete cancelled")
        return None
    if session is not None:
        session.invalidate(config)
    with loading(output, f"Deleting {target.identity}"):
        updated = database.delete(config, target)
    if session is not None:
        session.invalidate(updated)
    _screen(
        output,
        "Live data removed; local and remote backups retained",
        heading=f"Deleted {target.identity}",
    )
    return updated


def _add(
    config: Config,
    input_fn,
    output,
    password_fn,
    *,
    session: _StatusSession | None = None,
) -> Config:
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
    postgres_version = 16
    pgbouncer = None
    pgbouncer_image = None
    max_clients = None
    pool_size = None
    reserve_size = None
    if role == "kv":
        _screen(output, "1. Dragonfly\n2. Redis\n0. Cancel", heading="KV engine")
        selected = _choice(input_fn, output, "Engine", {"0", "1", "2"})
        if selected in {None, "0"}:
            return config
        engine = "dragonfly" if selected == "1" else "redis"
    else:
        from .config import postgres_image, validate_postgres_name

        selected_version = ask_text(
            input_fn,
            output,
            "Postgres version",
            default="16",
            validate=lambda value: int(postgres_image(value).removeprefix("postgres:")),
        )
        if selected_version is None:
            return config
        postgres_version = selected_version
    if role == "postgres" and confirm(input_fn, "Advanced Postgres configuration?"):
        from .config import validate_image, validate_postgres_name

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
        pgbouncer = confirm(input_fn, "Enable PgBouncer?", default=True)
        if pgbouncer:
            pgbouncer_image = ask_text(
                input_fn,
                output,
                "PgBouncer image",
                default="edoburu/pgbouncer:v1.25.1-p0",
                validate=lambda value: _image(value, validate_image, "PgBouncer image"),
            )
            max_clients = ask_text(
                input_fn, output, "PgBouncer max clients", default="100", validate=_positive
            )
            pool_size = ask_text(
                input_fn, output, "PgBouncer pool size", default="20", validate=_positive
            )
            reserve_size = ask_text(
                input_fn, output, "PgBouncer reserve size", default="5", validate=_positive
            )
        if (
            username is None
            or database_name is None
            or password is None
            or pgbouncer
            and None in {pgbouncer_image, max_clients, pool_size, reserve_size}
        ):
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
            Version=postgres_version,
            Username=username or "default",
            Database=database_name or "postgres",
            Password="provided" if password else "generated",
            PgBouncer=True if pgbouncer is None else pgbouncer,
        )
    _screen(
        output,
        pairs(summary),
        heading="Create database",
    )
    if not _yes(input_fn, "Create? [y/N] "):
        return config
    if session is not None:
        session.invalidate(config)
    updated = database.add(
        config,
        project,
        role,
        engine=engine,
        password=password,
        username=username,
        database_name=database_name,
        data_root=data_root,
        postgres_version=postgres_version,
        pgbouncer=pgbouncer,
        pgbouncer_image=pgbouncer_image,
        max_clients=max_clients,
        pool_size=pool_size,
        reserve_size=reserve_size,
    )
    if session is not None:
        session.invalidate(updated)
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


def _backups(
    config: Config,
    target: Database,
    input_fn,
    output,
    *,
    session: _StatusSession | None = None,
) -> Config:
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
                if session is not None:
                    session.invalidate(config)
                with loading(output, f"Creating {target.identity} backup"):
                    result = backup.create(config, target)
                if session is not None:
                    session.start()
                _screen(
                    output,
                    f"{target.identity}: backup {result['backup']} completed "
                    f"at {result['finished']}\n"
                    f"Snapshot: {result['snapshot']}\nRepository: {result['repository']}",
                    heading="Backup complete",
                    states=(("completed", "green"),),
                )
            else:
                if session is not None:
                    session.cancel()
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


def _host(
    config: Config,
    value: dict[str, Any],
    input_fn,
    output,
    *,
    session: _StatusSession | None = None,
) -> Config:
    host = value["host"]
    infrastructure = host["infrastructure"]
    errors = [
        f"{item['scope']}: {item['message']}"
        for item in value["errors"]
        if item["scope"].startswith("host/")
    ]
    storage = [host["storage"], *host.get("database_storage", [])]
    states = [
        ("ok", "green")
        if item.get("ok")
        else ("low", "yellow")
        if item.get("available", True)
        else ("unknown", "yellow")
        for item in storage
    ]
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
                "State storage": _storage(host["storage"]),
                "Database storage": {
                    f"Root {index}": _storage(item, assignments=True)
                    for index, item in enumerate(host.get("database_storage", []), 1)
                },
                "Listeners": {
                    port: _state(ready, "listening", "missing")
                    for port, ready in infrastructure["listeners"].items()
                },
                **port_summary(config.host.routing.postgres_ports, config.host.routing.kv_ports),
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
    _options(output, [("1", "Restart traffic"), ("2", "Native ports"), ("0", "Back")])
    choice = _choice(input_fn, output, "Select", {"0", "1", "2"})
    if choice == "2":
        from dataclasses import replace

        from . import host as host_module
        from .config import load, require_valid

        routing = config.host.routing
        postgres_ports = ask_ports(input_fn, output, "Postgres ports", routing.postgres_ports)
        if postgres_ports is None:
            return config
        kv_ports = ask_ports(input_fn, output, "KV ports", routing.kv_ports)
        if kv_ports is None:
            return config
        candidate = replace(routing, postgres_ports=postgres_ports, kv_ports=kv_ports)
        require_valid(replace(config, host=replace(config.host, routing=candidate)))
        _screen(
            output,
            pairs(port_summary(postgres_ports, kv_ports)),
            heading="Save native ports",
        )
        if set(postgres_ports) != set(routing.postgres_ports) or set(kv_ports) != set(
            routing.kv_ports
        ):
            output("Changing bindings will briefly drop native connections.")
        if not confirm(input_fn, "Save?") or candidate == routing:
            return config
        if session is not None:
            session.invalidate(config)
        with loading(output, "Applying native ports"):
            host_module.initialize(
                config.paths.source,
                {"postgres_ports": list(postgres_ports), "kv_ports": list(kv_ports)},
                paths=config.paths,
                output=output,
            )
            updated = load(config.paths.source, paths=config.paths)
        if session is not None:
            session.invalidate(updated)
        _screen(output, "Native ports saved", heading="Native ports")
        return updated
    if choice != "1":
        return config
    _screen(
        output,
        "Active database connections may briefly drop.",
        heading="Restart traffic",
    )
    if not _yes(input_fn, "Restart Traefik traffic? [y/N] "):
        return config
    from . import host as host_module

    if session is not None:
        session.invalidate(config)
    with loading(output, "Restarting Traefik traffic"):
        host_module.restart_traffic(config)
    _screen(
        output,
        "Traefik traffic restart complete",
        heading="Restart traffic",
        states=(("complete", "green"),),
    )
    return config


def port_summary(postgres_ports, kv_ports) -> dict[str, str]:
    return {
        label: f"{', '.join(map(str, ports))} (preferred: {ports[0]})"
        for label, ports in (("Postgres ports", postgres_ports), ("KV ports", kv_ports))
    }


def ask_ports(input_fn, output, label: str, default: tuple[int, ...]) -> tuple[int, ...] | None:
    from .config import validate_ports

    def validate(value):
        try:
            ports = [int(port.strip()) for port in value.split(",")]
        except ValueError as exc:
            raise Error(f"{label} must be comma-separated integers") from exc
        return validate_ports(ports, label)

    return ask_text(
        input_fn,
        output,
        label,
        default=", ".join(map(str, default)),
        help_text="Comma-separated native host ports; the first is preferred for connections.",
        validate=validate,
    )


def _storage(value: dict[str, Any], *, assignments: bool = False) -> dict[str, Any] | str:
    if not value.get("available", True):
        return {"path": value["path"], "status": "unknown"}
    result = {
        "path": value["path"],
        "mount": value["mount"],
        "source": value["source"],
        "filesystem": value["filesystem"],
        "capacity": (
            f"{_bytes(value['used_bytes'])} used / {_bytes(value['total_bytes'])} total "
            f"({_bytes(value['free_bytes'])} free, {'ok' if value['ok'] else 'low'})"
        ),
    }
    if assignments:
        result["databases"] = ", ".join(value.get("databases", [])) or "none"
    return result


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


def _choice(input_fn, output, prompt: str | None, allowed: set[str]) -> str | None:
    while True:
        try:
            value = input_fn(f"{prompt}: " if prompt else "").strip()
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


def _positive(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise Error("positive integer required") from exc
    if result < 1:
        raise Error("positive integer required")
    return result


def _image(value: str, validate, name: str) -> str:
    validate(value, name)
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
        ("Storage", _database_storage(value.get("storage"))),
        ("Data", _data_usage(value.get("data_usage"))),
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


def _database_storage(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    if not value.get("available", True):
        return {"path": value["path"], "status": "unknown"}
    return {
        "path": value["path"],
        "allocated": _bytes(value["allocated_bytes"]),
        "mount": value["mount"],
        "source": value["source"],
        "filesystem": value["filesystem"],
        "capacity": (
            f"{_bytes(value['used_bytes'])} used / {_bytes(value['total_bytes'])} total "
            f"({_bytes(value['free_bytes'])} free)"
        ),
    }


def _data_usage(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    if not value.get("available"):
        return {"status": "unavailable", "reason": value.get("reason", "assessment failed")}
    if "logical_bytes" in value:
        return {"logical size": _bytes(value["logical_bytes"]), "databases": value["databases"]}
    return {"dataset memory": _bytes(value["dataset_bytes"]), "keys": value["keys"]}


def _bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError


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
