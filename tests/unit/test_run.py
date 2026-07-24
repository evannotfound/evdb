import os
import sys
import time

import pytest

from evanovation_db.errors import CommandError
from evanovation_db.run import redact, run


def test_run_captures_output():
    result = run([sys.executable, "-c", "print('ok')"])

    assert result.code == 0
    assert result.out == "ok\n"


def test_run_streams_binary_output(tmp_path):
    target = tmp_path / "output"
    with target.open("wb") as output:
        result = run(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'abc')"],
            stdout=output,
        )

    assert result.out == ""
    assert target.read_bytes() == b"abc"


def test_run_redacts_error():
    secret = "hidden-value"

    with pytest.raises(CommandError) as caught:
        run(
            [sys.executable, "-c", f"import sys; sys.stderr.write('{secret}'); sys.exit(2)"],
            secrets=[secret],
        )
    assert secret not in str(caught.value)
    assert "<redacted>" in str(caught.value)


def test_run_times_out():
    with pytest.raises(CommandError, match="timed out"):
        run([sys.executable, "-c", "import time; time.sleep(2)"], timeout=1)


def test_timeout_kills_descendants(tmp_path):
    child = tmp_path / "child.pid"
    script = (
        "import pathlib, subprocess, sys, time; "
        "p=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"pathlib.Path({str(child)!r}).write_text(str(p.pid)); "
        "time.sleep(60)"
    )

    with pytest.raises(CommandError, match="timed out"):
        run([sys.executable, "-c", script], timeout=1)

    pid = int(child.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _running(pid):
        time.sleep(0.05)
    assert not _running(pid)


def test_redact_uses_longest_values_first():
    assert redact("token-long token", ["token", "token-long"]) == "<redacted> <redacted>"


def _running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
