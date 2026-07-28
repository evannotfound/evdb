from __future__ import annotations

import os
from contextlib import nullcontext
from typing import Any

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text


class Terminal:
    def __init__(self, console: Console | None = None):
        self.console = console or Console(highlight=False, markup=False)

    def __call__(self, value: Any = "") -> None:
        self.text(value)

    def text(self, value: Any = "") -> None:
        self.console.print(Text(str(value)))

    def busy(self, message: str):
        if not self.console.is_terminal:
            return nullcontext()
        return self.console.status(message)

    def status(self, value: dict[str, Any] | str) -> None:
        if not isinstance(value, dict):
            self.text(value)
            return
        host = value["host"]
        state = "healthy" if host["healthy"] else "needs attention"
        self.console.print(
            Text.assemble(
                (f"Host {host['id']}", "bold"),
                f"  tool {host['tool_version']}  ",
                (state, "green" if host["healthy"] else "red"),
            )
        )
        table = Table(box=box.SIMPLE, expand=True, show_lines=False)
        for header in (
            "Database",
            "Engine",
            "Run",
            "Health",
            "Config",
            "Local",
            "Upload",
            "Test",
            "Error",
            "Image",
        ):
            table.add_column(header, overflow="ellipsis", no_wrap=header != "Error")
        for identity, item in value["databases"].items():
            table.add_row(
                identity,
                item["engine"],
                "yes" if item["running"] else "no",
                item["health"],
                "ok" if item["configuration_match"] else "differs",
                _backup_cell(item.get("backup")),
                _upload_cell(item.get("upload"), item.get("backup")),
                _test_cell(item.get("backup_test")),
                item.get("error") or "-",
                item.get("image") or "-",
            )
        self.console.print(table)
        for error in value["errors"]:
            self.console.print(Text(f"{error['scope']}: {error['message']}", style="red"))

    def menu(self, title: str | None, options: list[tuple[str, str]]) -> None:
        if title:
            self.console.print(Text(str(title), style="bold"))
        table = Table.grid(padding=(0, 2))
        table.add_column(justify="right", no_wrap=True)
        table.add_column(overflow="fold")
        for key, label in options:
            table.add_row(f"[{key}]", Text(str(label)))
        self.console.print(table)

    def databases(self, values, health: dict[str, str], *, allow_add: bool) -> None:
        table = Table(box=box.SIMPLE, show_header=True, expand=True)
        table.add_column("#", justify="right", no_wrap=True)
        table.add_column("Database", overflow="ellipsis")
        table.add_column("Engine", no_wrap=True)
        table.add_column("Health", no_wrap=True)
        for number, item in enumerate(values, start=1):
            table.add_row(
                str(number), item.identity, item.engine, health.get(item.identity, "unknown")
            )
        if allow_add:
            table.add_row(str(len(values) + 1), "Add database", "-", "-")
        table.add_row("0", "Back", "-", "-")
        self.console.print(table)

    def preview(self, text: str) -> None:
        lines = str(text).splitlines()
        table = Table(box=box.SIMPLE, show_header=False)
        table.add_column("Field", style="bold", no_wrap=True)
        table.add_column("Value", overflow="fold")
        plain = []
        for line in lines:
            if ": " in line and not line.startswith("  "):
                key, value = line.split(": ", 1)
                table.add_row(key, value)
            else:
                plain.append(line)
        if table.row_count:
            self.console.print(table)
        for line in plain:
            self.text(line)

    def result(self, value: Any) -> None:
        self.text(result(value))


def result(value: Any) -> str:
    if not isinstance(value, dict):
        return str(value)
    if database := value.get("database"):
        state = value.get("status", "complete")
        lines = [f"{database}: {state}" if state != "healthy" else f"{database} is healthy"]
        if changed := value.get("changed"):
            lines.append("Changed: " + ", ".join(map(str, changed)))
        if snapshot := value.get("safety_snapshot"):
            lines.append(f"Safety backup: {snapshot}")
        return "\n".join(lines)
    if value.get("status") == "healthy" and "backup" in value:
        lines = [f"Restore complete: {value['backup']}"]
        if snapshot := value.get("safety", {}).get("snapshot"):
            lines.append(f"Safety backup: {snapshot}")
        return "\n".join(lines)
    if status := value.get("status"):
        return f"Status: {status}"
    return "\n".join(f"{key.replace('_', ' ').title()}: {item}" for key, item in value.items())


def terminal() -> Terminal:
    return Terminal(Console(highlight=False, markup=False, no_color=_no_color()))


def _no_color() -> bool | None:
    if os.getenv("NO_COLOR") is not None or os.getenv("TERM") == "dumb":
        return True
    return None


def _backup_cell(item: dict[str, Any] | None) -> str:
    if not item or not item.get("ok"):
        return "missing"
    return item.get("backup") or "ok"


def _upload_cell(item: dict[str, Any] | None, backup: dict[str, Any] | None) -> str:
    if not item or not item.get("ok"):
        return "missing"
    return item.get("snapshot") or (backup or {}).get("backup") or "ok"


def _test_cell(item: dict[str, Any] | None) -> str:
    if not item or not item.get("ok"):
        return "missing"
    return item.get("backup") or "ok"
