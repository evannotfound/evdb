import os
import sys
import time
from pathlib import Path
from threading import Event, Thread
from urllib.parse import quote

import pytest

from evdb.errors import CommandError
from evdb.run import Cancelled, cancellable, clean, redact, run


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


def test_run_can_replace_environment_and_inherit_descriptor(monkeypatch):
    monkeypatch.setenv("EVDB_ROOT_ONLY", "must-not-leak")
    descriptor = os.memfd_create("evdb-run-test", os.MFD_CLOEXEC)
    try:
        os.write(descriptor, b"descriptor value")
        os.lseek(descriptor, 0, os.SEEK_SET)
        result = run(
            [
                sys.executable,
                "-c",
                (
                    "import os,sys; "
                    "print(os.environ.get('EVDB_ROOT_ONLY')); "
                    "print(os.environ['ONLY']); "
                    "print(os.read(int(sys.argv[1]), 100).decode())"
                ),
                str(descriptor),
            ],
            env={"ONLY": "operator"},
            replace_env=True,
            pass_fds=(descriptor,),
        )
    finally:
        os.close(descriptor)

    assert result.out.splitlines() == ["None", "operator", "descriptor value"]


def test_run_forwards_numeric_identity_and_groups(monkeypatch):
    seen = {}

    class Process:
        returncode = 0

        def communicate(self, input_data, timeout):
            del input_data, timeout
            return b"", b""

    monkeypatch.setattr(
        "evdb.run.subprocess.Popen",
        lambda command, **kwargs: seen.update(command=command, kwargs=kwargs) or Process(),
    )

    run(["/bin/true"], user=1001, group=1002, extra_groups=(1003, 1004))

    assert seen["kwargs"]["user"] == 1001
    assert seen["kwargs"]["group"] == 1002
    assert seen["kwargs"]["extra_groups"] == (1003, 1004)


def test_cancellable_run_kills_process_group(tmp_path):
    child = tmp_path / "child.pid"
    cancel = Event()
    trigger = Thread(target=lambda: (time.sleep(0.2), cancel.set()))
    trigger.start()
    started = time.monotonic()

    with pytest.raises(Cancelled), cancellable(cancel):
        run(
            [
                sys.executable,
                "-c",
                (
                    "import pathlib,subprocess,sys,time; "
                    "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                    "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); "
                    "time.sleep(30)"
                ),
                str(child),
            ],
            timeout=30,
        )
    trigger.join()

    assert time.monotonic() - started < 2
    child_pid = int(child.read_text())
    deadline = time.monotonic() + 2
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not Path(f"/proc/{child_pid}").exists()
