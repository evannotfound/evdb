import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
CHECK = ROOT / "tools/check_release.py"


def _executable(tmp_path, *, version="1.2.3", invalid=None):
    executable = tmp_path / ("wrong-name" if invalid == "name" else "evdb_linux_amd64")
    reported = "9.9.9" if invalid == "version" else version
    executable.write_text(f"#!/bin/sh\nprintf '%s\\n' 'evdb {reported}'\n")
    executable.chmod(0o644 if invalid == "mode" else 0o755)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    if invalid == "checksum":
        digest = "0" * 64
    executable.with_name(executable.name + ".sha256").write_text(f"{digest}  {executable.name}\n")
    if invalid == "symlink":
        target = tmp_path / "target"
        executable.replace(target)
        executable.symlink_to(target)
    return executable


def _check(executable, tag="v1.2.3"):
    return subprocess.run(
        [sys.executable, str(CHECK), str(executable), tag],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": ""},
        text=True,
        capture_output=True,
        check=False,
    )


def test_release_checker_accepts_exact_standalone_executable(tmp_path):
    result = _check(_executable(tmp_path))

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("invalid", ["checksum", "mode", "symlink", "version", "name"])
def test_release_checker_rejects_invalid_generated_executables(tmp_path, invalid):
    result = _check(_executable(tmp_path, invalid=invalid))

    assert result.returncode != 0
    assert "release check failed" in result.stderr


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2", "v1.2.3-01"])
def test_release_checker_rejects_invalid_semantic_tags(tmp_path, tag):
    result = _check(_executable(tmp_path), tag)

    assert result.returncode != 0
    assert "invalid release tag" in result.stderr
