from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

MAX_SIZE = 268_435_456
ASSET = re.compile(r"evdb_linux_(?:amd64|arm64)")
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


def checksum(executable: Path) -> None:
    path = executable.with_name(executable.name + ".sha256")
    if path.is_symlink() or not path.is_file():
        fail("checksum file is missing or unsafe")
    try:
        fields = path.read_text(encoding="ascii").split()
    except (OSError, UnicodeError) as exc:
        fail(f"checksum file cannot be read: {exc}")
    if len(fields) != 2 or fields[1].removeprefix("*") != executable.name:
        fail("checksum file is malformed or names another executable")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
        fail("checksum file is malformed")
    digest = hashlib.sha256()
    with executable.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != fields[0].lower():
        fail("checksum does not match the executable")


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
        raise SystemExit("usage: check_release.py EXECUTABLE TAG")
    executable = Path(sys.argv[1])
    tag = sys.argv[2]
    selected = tag.removeprefix("v")
    if tag != f"v{selected}" or not semantic(selected):
        fail(f"invalid release tag: {tag}")
    if not ASSET.fullmatch(executable.name):
        fail(f"invalid release asset name: {executable.name}")
    if executable.is_symlink() or not executable.is_file():
        fail("release executable is missing or unsafe")
    if executable.stat().st_size <= 0 or executable.stat().st_size > MAX_SIZE:
        fail("release executable has an invalid size")
    if executable.stat().st_mode & 0o777 != 0o755:
        fail("release executable must have mode 0755")
    checksum(executable)
    executable_version(executable, selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
