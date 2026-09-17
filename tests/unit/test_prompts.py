from io import StringIO

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from rich.console import Console

from evdb import ui
from evdb.errors import Error


@pytest.fixture
def prompts(monkeypatch):
    stream = StringIO()
    output = ui.Terminal(Console(file=stream, force_terminal=True, width=80))
    answers = []
    sessions = []
    monkeypatch.setattr(ui.sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("TERM", "xterm")
    with create_pipe_input() as pipe:

        def session(message, **kwargs):
            pipe.send_text(answers.pop(0))
            result = PromptSession(message, input=pipe, **kwargs)
            sessions.append(result)
            return result

        monkeypatch.setattr(ui, "PromptSession", session)
        yield output, stream, answers, sessions


def test_text_error_keeps_answer_editable_and_returns_normalized_value(prompts):
    output, stream, answers, sessions = prompts
    answers.append("team!\n\x7f\n")
    checked = []

    def validate(value):
        checked.append(value)
        if not value.isalpha():
            raise Error("Use letters only")
        return value.upper()

    assert ui.ask_text(output.input, output, "Project", validate=validate) == "TEAM"
    assert checked == ["team!", "team"]
    assert len(sessions) == 1
    assert "team" in stream.getvalue()


@pytest.mark.parametrize("menu", ["choice", "choose"])
def test_numbered_menus_keep_invalid_input_for_correction(prompts, menu):
    output, _stream, answers, sessions = prompts
    answers.append("12\n\x7f\n")
    if menu == "choice":
        value = ui._choice(output.input, output, "Select", {"0", "1"})
    else:
        value = ui.choose(output.input, output, "Select", [("0", "Back"), ("1", "Details")])
    assert value == "1"
    assert len(sessions) == 1


def test_defaults_optional_values_and_required_fields(prompts):
    output, _stream, answers, sessions = prompts
    answers.extend(["\n", "\n", "\nvalue\n", "\n"])
    assert ui.ask_text(output.input, output, "Port", default="5432", validate=int) == 5432
    assert ui.ask_text(output.input, output, "Optional", required=False) is None
    assert ui.ask_text(output.input, output, "Required") == "value"
    assert ui.choose(output.input, output, "Next", [("1", "Apply")], default="1") == "1"
    assert len(sessions) == 4


@pytest.mark.parametrize("legacy", [False, True])
def test_confirmation_typo_is_correctable(prompts, legacy):
    output, _stream, answers, sessions = prompts
    answers.append("ye\ns\n")
    if legacy:
        assert ui._yes(output.input, "Save? [y/N] ") is True
    else:
        assert ui.confirm(output.input, "Save?") is True
    assert len(sessions) == 1


@pytest.mark.parametrize(
    ("answer", "default", "expected"),
    [
        ("\n", False, False),
        ("\n", True, True),
        ("no\n", True, False),
    ],
)
def test_confirmation_defaults_and_no(prompts, answer, default, expected):
    output, _stream, answers, _sessions = prompts
    answers.append(answer)
    assert ui.confirm(output.input, "Apply?", default=default) is expected


def test_secret_confirmation_is_masked_editable_and_has_no_history(prompts):
    output, stream, answers, sessions = prompts
    answers.extend(["private\n", "privatX\n\x7fe\n"])
    assert ui.ask_secret(output.read_secret, output, "Password", confirm=True) == "private"
    assert len(sessions) == 2
    assert "private" not in stream.getvalue()
    assert "privatX" not in stream.getvalue()
    assert "*******" in stream.getvalue()
    assert all(not session.history.get_strings() for session in sessions)


@pytest.mark.parametrize(("keys", "error"), [("\x03", KeyboardInterrupt), ("\x04", EOFError)])
def test_input_cancellation(prompts, keys, error):
    output, _stream, answers, _sessions = prompts
    answers.append(keys)
    with pytest.raises(error):
        output.input("Name: ")


def test_eof_cancels_text_and_menu_helpers(prompts):
    output, _stream, answers, _sessions = prompts
    answers.extend(["\x04", "\x04", "\x04"])
    assert ui.ask_text(output.input, output, "Name") is None
    assert ui._choice(output.input, output, "Select", {"0", "1"}) is None
    assert ui.confirm(output.input, "Apply?", default=True) is False


def test_prompt_respects_no_color(prompts, monkeypatch):
    output, stream, answers, _sessions = prompts
    monkeypatch.setenv("NO_COLOR", "1")
    answers.append("value\n")
    assert output.input("Name: ") == "value"
    assert "\x1b[36" not in stream.getvalue()


def test_dumb_terminal_uses_existing_input(monkeypatch):
    output = ui.Terminal(Console(file=StringIO()))
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr(ui.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(output.console, "input", lambda prompt: "plain")
    monkeypatch.setattr(ui, "PromptSession", lambda *a, **kw: pytest.fail("toolkit used"))
    assert output.input("Name: ") == "plain"
