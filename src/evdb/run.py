from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from .errors import CommandError


@dataclass(frozen=True)
class Result:
    args: tuple[str, ...]
    code: int
    out: str
    err: str


def redact(text: str, secrets: Sequence[str] = ()) -> str:
    result = text
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        result = result.replace(secret, "<redacted>")
    return result


def run(
    args: Sequence[str],
    *,
    timeout: int = 300,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    input: str | bytes | None = None,
    stdout: IO[bytes] | None = None,
    secrets: Sequence[str] = (),
    check: bool = True,
) -> Result:
    command = tuple(str(item) for item in args)
    if not command:
        raise CommandError("empty command")
    command_env = os.environ.copy()
    if env:
        command_env.update({str(key): str(value) for key, value in env.items()})
    input_data = input.encode() if isinstance(input, str) else input
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=command_env,
            stdin=subprocess.PIPE if input_data is not None else None,
            stdout=stdout if stdout is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        safe = redact(" ".join(command), secrets)
        raise CommandError(f"command failed to start: {safe}: {exc}") from exc

    try:
        out_data, err_data = process.communicate(input_data, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill(process)
        process.communicate()
        safe = redact(" ".join(command), secrets)
        raise CommandError(f"command timed out after {timeout}s: {safe}") from exc
    except BaseException:
        _kill(process)
        process.communicate()
        raise

    out = "" if stdout is not None else (out_data or b"").decode(errors="replace")
    err = (err_data or b"").decode(errors="replace")
    result = Result(command, process.returncode, redact(out, secrets), redact(err, secrets))
    if check and result.code != 0:
        safe = redact(" ".join(command), secrets)
        detail = result.err.strip() or result.out.strip() or "no output"
        raise CommandError(f"command failed ({result.code}): {safe}: {detail}")
    return result


def _kill(process: subprocess.Popen) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
