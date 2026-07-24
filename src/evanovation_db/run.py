from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
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
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=command_env,
            stdout=stdout or subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        safe = redact(" ".join(command), secrets)
        raise CommandError(f"command timed out after {timeout}s: {safe}") from exc
    except OSError as exc:
        safe = redact(" ".join(command), secrets)
        raise CommandError(f"command failed to start: {safe}: {exc}") from exc

    out = "" if stdout else completed.stdout.decode(errors="replace")
    err = completed.stderr.decode(errors="replace")
    result = Result(command, completed.returncode, redact(out, secrets), redact(err, secrets))
    if check and result.code != 0:
        safe = redact(" ".join(command), secrets)
        detail = result.err.strip() or result.out.strip() or "no output"
        raise CommandError(f"command failed ({result.code}): {safe}: {detail}")
    return result
