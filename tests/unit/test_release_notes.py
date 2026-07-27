import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location("release_notes", ROOT / "tools/release_notes.py")
assert SPEC is not None and SPEC.loader is not None
release_notes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_notes)


def test_check_notes_accepts_utf8_markdown(tmp_path):
    path = tmp_path / "release-notes.md"
    path.write_text("## Features\n\n- Initial release.\n")

    assert release_notes.check_notes(path).startswith("## Features")


def test_check_notes_accepts_no_notable_changes(tmp_path):
    path = tmp_path / "release-notes.md"
    path.write_text("No notable changes.\n")

    assert release_notes.check_notes(path) == "No notable changes.\n"


@pytest.mark.parametrize("content", [b"", b" \n", b"bad\x00notes", b"\xff"])
def test_check_notes_rejects_invalid_content(tmp_path, content):
    path = tmp_path / "release-notes.md"
    path.write_bytes(content)

    with pytest.raises(release_notes.NotesError):
        release_notes.check_notes(path)


def test_check_notes_rejects_missing_directory_symlink_and_oversize(tmp_path):
    missing = tmp_path / "missing.md"
    with pytest.raises(release_notes.NotesError, match="missing or unsafe"):
        release_notes.check_notes(missing)

    folder = tmp_path / "folder"
    folder.mkdir()
    with pytest.raises(release_notes.NotesError, match="missing or unsafe"):
        release_notes.check_notes(folder)

    notes = tmp_path / "notes.md"
    notes.write_text("valid")
    link = tmp_path / "link.md"
    link.symlink_to(notes)
    with pytest.raises(release_notes.NotesError, match="missing or unsafe"):
        release_notes.check_notes(link)

    notes.write_bytes(b"x" * (release_notes.MAX_NOTES_BYTES + 1))
    with pytest.raises(release_notes.NotesError, match="too large"):
        release_notes.check_notes(notes)


@pytest.mark.parametrize(
    "content",
    [
        "Release summary.\n",
        "## Added\n\n- New thing.\n",
        "## Features\n\n## Bugfixes\n\n- Fixed thing.\n",
        "## Bugfixes\n\n- Fixed thing.\n\n## Features\n\n- New thing.\n",
    ],
)
def test_check_notes_rejects_invalid_sections(tmp_path, content):
    path = tmp_path / "release-notes.md"
    path.write_text(content)

    with pytest.raises(release_notes.NotesError):
        release_notes.check_notes(path)


@pytest.mark.parametrize("name", ["RELEASE_LLM_URL", "RELEASE_LLM_KEY", "GH_TOKEN", "GITHUB_TOKEN"])
def test_check_notes_rejects_credentials(tmp_path, monkeypatch, name):
    path = tmp_path / "release-notes.md"
    path.write_text("## Bugfixes\n\n- Fixed secret-value exposure.\n")
    monkeypatch.setenv(name, "secret-value")

    with pytest.raises(release_notes.NotesError, match="credential"):
        release_notes.check_notes(path)
