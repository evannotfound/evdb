import hashlib
import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
CHECK = ROOT / "tools/check_release.py"
UNITS = ROOT / "src/evdb/units"


def _archive(tmp_path, *, version="1.2.3", invalid=None):
    archive = tmp_path / "evdb_linux_amd64.tar.gz"
    binary_version = "9.9.9" if invalid == "version" else version
    members = [
        (
            "bin/evdb",
            f"#!/bin/sh\nprintf '%s\\n' 'evdb {binary_version}'\n".encode(),
            0o755,
            tarfile.REGTYPE,
        ),
        (
            "units/evdb-backup.service",
            (UNITS / "evdb-backup.service").read_bytes(),
            0o644,
            tarfile.REGTYPE,
        ),
        (
            "units/evdb-backup.timer",
            (UNITS / "evdb-backup.timer").read_bytes(),
            0o644,
            tarfile.REGTYPE,
        ),
    ]
    if invalid == "member":
        members.append(("unexpected", b"no\n", 0o644, tarfile.REGTYPE))
    if invalid == "mode":
        members[0] = (*members[0][:2], 0o777, tarfile.REGTYPE)
    if invalid == "symlink":
        members[0] = ("bin/evdb", b"", 0o755, tarfile.SYMTYPE)
    with tarfile.open(archive, "w:gz") as bundle:
        for name, data, mode, kind in members:
            item = tarfile.TarInfo(name)
            item.size = len(data)
            item.mode = mode
            item.type = kind
            if kind == tarfile.SYMTYPE:
                item.linkname = "/bin/true"
            bundle.addfile(item, io.BytesIO(data))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if invalid == "checksum":
        digest = "0" * 64
    archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n")
    return archive


def _check(archive, tag="v1.2.3"):
    return subprocess.run(
        [sys.executable, str(CHECK), str(archive), tag],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": ""},
        text=True,
        capture_output=True,
        check=False,
    )


def test_release_checker_accepts_exact_self_contained_archive(tmp_path):
    result = _check(_archive(tmp_path))

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("invalid", ["checksum", "member", "mode", "symlink", "version"])
def test_release_checker_rejects_invalid_generated_archives(tmp_path, invalid):
    result = _check(_archive(tmp_path, invalid=invalid))

    assert result.returncode != 0
    assert "release check failed" in result.stderr


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2", "v1.2.3-01"])
def test_release_checker_rejects_invalid_semantic_tags(tmp_path, tag):
    result = _check(_archive(tmp_path), tag)

    assert result.returncode != 0
    assert "invalid release tag" in result.stderr
