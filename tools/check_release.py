from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from evanovation_db import host


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_release.py ARCHIVE TAG")
    archive = Path(sys.argv[1])
    tag = sys.argv[2]
    selected = tag.removeprefix("v")
    if tag != f"v{selected}" or not host.VERSION.fullmatch(selected):
        raise SystemExit(f"invalid release tag: {tag}")
    checksum = archive.with_name(archive.name + ".sha256")
    expected_units = {path.name for path in host._units()}
    host._verify_checksum(archive, checksum)
    with tempfile.TemporaryDirectory(prefix="evdb-release-check-") as folder:
        target = Path(folder) / selected
        host._extract_release(archive, target, expected_units)
        actual_units = {path.name for path in host._candidate_units(target)}
        if actual_units != expected_units:
            raise SystemExit("release archive contains an unexpected canonical unit set")
        host._validate_executable_version(target / "bin/evdb", selected, timeout=60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
