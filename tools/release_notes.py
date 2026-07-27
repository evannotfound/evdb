from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

MAX_NOTES_BYTES = 64 * 1024
SECTIONS = ("Features", "Improvements", "Bugfixes", "Breaking changes")


class NotesError(Exception):
    pass


def _check_sections(text: str) -> None:
    if text.strip() == "No notable changes.":
        return

    sections = []
    has_bullet = False
    for line in text.splitlines():
        if line.startswith("## "):
            if sections and not has_bullet:
                raise NotesError(f"release note section is empty: {sections[-1]}")
            name = line.removeprefix("## ").strip()
            if name not in SECTIONS or name in sections:
                raise NotesError(f"release note section is invalid: {name}")
            if sections and SECTIONS.index(name) <= SECTIONS.index(sections[-1]):
                raise NotesError("release note sections are out of order")
            sections.append(name)
            has_bullet = False
        elif sections and line.startswith("- "):
            has_bullet = True

    if not sections:
        raise NotesError("release notes contain no change sections")
    if not has_bullet:
        raise NotesError(f"release note section is empty: {sections[-1]}")


def check_notes(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise NotesError(f"release notes are missing or unsafe: {path}")
    details = path.stat()
    if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_NOTES_BYTES:
        raise NotesError("release notes are unsafe or too large")
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NotesError("release notes are not valid UTF-8") from exc
    if not text.strip() or "\0" in text:
        raise NotesError("release notes are empty or invalid")
    for name in ("RELEASE_LLM_URL", "RELEASE_LLM_KEY", "GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name)
        if value and value in text:
            raise NotesError("release notes contain a credential")
    _check_sections(text)
    return text


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Validate generated release notes")
    result.add_argument("path", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        check_notes(args.path)
    except (NotesError, OSError) as exc:
        print(f"release notes: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
