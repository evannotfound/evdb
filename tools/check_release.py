from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

MAX_EXPANDED_SIZE = 268_435_456
MEMBERS = {
    "bin/evdb": 0o755,
    "units/evdb-backup.service": 0o644,
    "units/evdb-backup.timer": 0o644,
}
VERSION = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


def fail(message: str) -> None:
    raise SystemExit(f"release check failed: {message}")


def semantic(value: str) -> bool:
    match = VERSION.fullmatch(value)
    if not match:
        return False
    prerelease = match.group(4)
    return prerelease is None or all(
        not (item.isdigit() and len(item) > 1 and item.startswith("0"))
        for item in prerelease.split(".")
    )


def checksum(archive: Path) -> None:
    path = archive.with_name(archive.name + ".sha256")
    if path.is_symlink() or not path.is_file():
        fail("checksum file is missing or unsafe")
    try:
        fields = path.read_text(encoding="ascii").split()
    except (OSError, UnicodeError) as exc:
        fail(f"checksum file cannot be read: {exc}")
    if len(fields) != 2 or fields[1].removeprefix("*") != archive.name:
        fail("checksum file is malformed or names another archive")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
        fail("checksum file is malformed")
    digest = hashlib.sha256()
    with archive.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != fields[0].lower():
        fail("checksum does not match the archive")


def extract(archive: Path, target: Path) -> None:
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            names = [item.name for item in members]
            if len(names) != len(set(names)) or set(names) != set(MEMBERS):
                fail("archive must contain exactly bin/evdb and the two canonical units")
            total = 0
            for member in members:
                expected_mode = MEMBERS[member.name]
                if not member.isreg() or member.size <= 0:
                    fail(f"archive member is not a non-empty regular file: {member.name}")
                if member.mode & 0o7777 != expected_mode:
                    fail(f"archive member has an invalid mode: {member.name}")
                total += member.size
                if total > MAX_EXPANDED_SIZE:
                    fail("archive expands beyond the size limit")
                source = bundle.extractfile(member)
                if source is None:
                    fail(f"archive member cannot be read: {member.name}")
                destination = target / member.name
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output)
                destination.chmod(expected_mode)
    except (OSError, tarfile.TarError, UnicodeError) as exc:
        fail(f"archive is invalid: {exc}")


def executable_version(executable: Path, selected: str) -> None:
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
            env={**os.environ, "PYTHONPATH": ""},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail(f"release executable could not report its version: {exc}")
    lines = result.stdout.splitlines()
    if result.returncode != 0 or result.stderr or lines != [f"evdb {selected}"]:
        fail("release executable returned an invalid or mismatched version")


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_release.py ARCHIVE TAG")
    archive = Path(sys.argv[1])
    tag = sys.argv[2]
    selected = tag.removeprefix("v")
    if tag != f"v{selected}" or not semantic(selected):
        fail(f"invalid release tag: {tag}")
    if archive.is_symlink() or not archive.is_file():
        fail("archive is missing or unsafe")
    if archive.stat().st_size > MAX_EXPANDED_SIZE:
        fail("archive exceeds the size limit")
    checksum(archive)
    with tempfile.TemporaryDirectory(prefix="evdb-release-check-") as folder:
        target = Path(folder)
        extract(archive, target)
        executable_version(target / "bin/evdb", selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
