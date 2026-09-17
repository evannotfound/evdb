import hashlib
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
CHECK = ROOT / "tools/check_release.py"


def _executable(tmp_path, *, version="1.2.3", invalid=None, preview=False):
    name = "evdb_linux_amd64" + (f"_{'a' * 40}_17_1" if preview else "")
    executable = tmp_path / ("wrong-name" if invalid == "name" else name)
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


def _check(executable, tag="v1.2.3", *, preview=False):
    return subprocess.run(
        [sys.executable, str(CHECK), str(executable), tag, *(["--preview"] if preview else [])],
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


@pytest.mark.parametrize("version", ["1.2.4.dev17+gabc1234", "1.2.4rc1.dev2+gabc1234", "1.2.3"])
def test_preview_checker_accepts_exact_scm_version(tmp_path, version):
    result = _check(_executable(tmp_path, version=version, preview=True), version, preview=True)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("invalid", ["checksum", "mode", "symlink", "version", "name"])
def test_preview_checker_rejects_invalid_generated_executables(tmp_path, invalid):
    version = "1.2.4.dev17+gabc1234"
    result = _check(
        _executable(tmp_path, version=version, invalid=invalid, preview=True), version, preview=True
    )

    assert result.returncode != 0
    assert "release check failed" in result.stderr


@pytest.mark.parametrize(
    "version", ["preview", "v1.2.3", "1.2.4.dev1", "1.2.4.dev1+gabc1234.d20260101"]
)
def test_preview_checker_rejects_non_scm_or_dirty_version(tmp_path, version):
    result = _check(_executable(tmp_path, version=version, preview=True), version, preview=True)

    assert result.returncode != 0
    assert "invalid preview version" in result.stderr


def test_scm_describe_ignores_rolling_preview_tag(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, text=True, capture_output=True, check=True
        ).stdout.strip()

    git("init")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "base",
    )
    git("tag", "v1.2.3")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "next",
    )
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    describe = config["tool"]["setuptools_scm"]["scm"]["git"]["describe_command"]
    before = git(*describe[1:])
    git("tag", "preview")

    assert before.startswith("v1.2.3-1-g")
    assert git(*describe[1:]) == before
    git("tag", "v1.2.4")
    assert git(*describe[1:]).startswith("v1.2.4-0-g")
