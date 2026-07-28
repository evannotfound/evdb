import sys
from urllib.parse import quote

import pytest

from evdb.errors import CommandError
from evdb.run import clean, redact, run


def test_run_uses_argument_arrays_and_captures_output():
    result = run([sys.executable, "-c", "print('ok')"])
    assert result.out == "ok\n"
    assert isinstance(result.args, tuple)


def test_run_redacts_only_supplied_exact_values_and_encoded_forms():
    secret = "p@ss word"
    text = f"repo=rclone:remote:path secret={secret} encoded={quote(secret, safe='')}"
    value = redact(text, [secret, quote(secret, safe="")])

    assert "rclone:remote:path" in value
    assert secret not in value
    assert quote(secret, safe="") not in value


def test_run_failure_preserves_unrelated_stderr():
    with pytest.raises(CommandError, match="ordinary diagnostic"):
        run(
            [
                sys.executable,
                "-c",
                "import sys; print('ordinary diagnostic', file=sys.stderr); raise SystemExit(2)",
            ]
        )


def test_external_text_strips_ansi_and_controls_but_preserves_newlines_and_tabs():
    text = "\x1b[31mfailed\x1b[0m\n\tdetail\x00\x7f\u0085"

    assert clean(text) == "failed\n\tdetail"


def test_run_sanitizes_external_output():
    result = run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('\\x1b[31moutside\\x1b[0m\\n\\tline\\0')",
        ]
    )

    assert result.out == "outside\n\tline"
