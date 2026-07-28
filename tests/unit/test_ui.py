from io import StringIO

from rich.console import Console

from evdb import ui


def _terminal(width=60):
    stream = StringIO()
    console = Console(
        file=stream,
        force_terminal=True,
        no_color=True,
        highlight=False,
        markup=False,
        width=width,
    )
    return ui.Terminal(console), stream


def test_terminal_status_renders_table_without_interpreting_markup():
    terminal, stream = _terminal(width=100)
    value = {
        "host": {"id": "test-host", "tool_version": "1.2.3", "healthy": True},
        "databases": {
            "app-[test]-01/kv": {
                "engine": "redis",
                "running": True,
                "health": "healthy",
                "configuration_match": True,
                "backup": {"ok": True, "backup": "backup-1"},
                "upload": {"ok": True, "snapshot": "snapshot-1"},
                "backup_test": {"ok": True, "backup": "backup-1"},
                "error": None,
                "image": "redis:7",
            }
        },
        "errors": [],
    }

    terminal.status(value)

    text = stream.getvalue()
    assert "test-host" in text
    assert "app-[test]-01/kv" in text
    assert "\x1b[32m" not in text


def test_terminal_menu_preview_and_result_are_human_readable():
    terminal, stream = _terminal()

    terminal.menu("Database", [("1", "Info"), ("0", "Back")])
    terminal.preview("Host: test-host\nDatabase: app-test-01/kv\nValues:\n  mode: cache -> durable")
    terminal.result(
        {
            "database": "app-test-01/kv",
            "changed": ["mode"],
            "safety_snapshot": "snapshot-1",
            "status": "healthy",
        }
    )

    text = stream.getvalue()
    assert "[1]" in text and "Info" in text
    assert "test-host" in text
    assert "app-test-01/kv is healthy" in text
    assert "Changed: mode" in text
    assert "Safety backup: snapshot-1" in text
