import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location("release_notes", ROOT / "tools/release_notes.py")
assert SPEC is not None and SPEC.loader is not None
release_notes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_notes)


def _git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Release Test")
    _git(root, "config", "user.email", "release@example.test")
    return root


def _commit(repo, name, text, message):
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def test_initial_release_uses_all_history_in_order(repo):
    first = _commit(repo, "README.md", "first\n", "Initial product")
    second = _commit(repo, "README.md", "second\n", "Document install")

    text = release_notes.build_input(repo, "v0.1.0", target="HEAD")

    assert "Previous release: none (initial release)" in text
    assert "Commit count: 2" in text
    assert text.index(f"Commit: {first}") < text.index(f"Commit: {second}")
    assert "README.md" in text
    patch = text.split("# Range patch", 1)[1]
    assert "+second" in patch
    assert "+first" not in patch


def test_release_uses_nearest_prior_reachable_tag(repo):
    first = _commit(repo, "app.py", "first\n", "Initial release")
    tagged = _commit(repo, "stable.py", "unchanged\n", "Add stable module")
    _git(repo, "tag", "v0.1.0", tagged)
    second = _commit(repo, "app.py", "second\n", "Add status output")
    _git(repo, "tag", "v0.2.0", second)

    text = release_notes.build_input(repo, "v0.2.0")

    assert "Previous release: v0.1.0" in text
    assert "Commit count: 1" in text
    assert first not in text
    assert second in text
    patch = text.split("# Range patch", 1)[1]
    assert "-first" in patch
    assert "+second" in patch
    assert "stable.py" not in patch


@pytest.mark.parametrize("tag", ["0.1.0", "v01.2.3", "v1.2", "v1.2.3-01"])
def test_input_rejects_invalid_tag(repo, tag):
    _commit(repo, "app.py", "first\n", "Initial release")

    with pytest.raises(release_notes.NotesError, match="invalid semantic-version tag"):
        release_notes.build_input(repo, tag, target="HEAD")


def test_input_rejects_missing_tag_and_target(repo):
    _commit(repo, "app.py", "first\n", "Initial release")

    with pytest.raises(release_notes.NotesError, match="release tag does not exist"):
        release_notes.build_input(repo, "v0.1.0")
    with pytest.raises(release_notes.NotesError, match="unresolvable release target"):
        release_notes.build_input(repo, "v0.1.0", target="missing")


def test_input_rejects_existing_tag_that_does_not_match_preview_target(repo):
    first = _commit(repo, "app.py", "first\n", "Initial release")
    _git(repo, "tag", "v0.1.0", first)
    _commit(repo, "app.py", "second\n", "Post-release work")

    with pytest.raises(release_notes.NotesError, match="does not match target"):
        release_notes.build_input(repo, "v0.1.0", target="HEAD")


def test_write_input_is_atomic_and_rejects_symlink(tmp_path):
    target = tmp_path / "input.md"
    release_notes.write_input(target, "evidence\n")
    assert target.read_text() == "evidence\n"

    outside = tmp_path / "outside"
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(release_notes.NotesError, match="unsafe release input path"):
        release_notes.write_input(target, "changed\n")
    assert not outside.exists()


def test_check_notes_accepts_utf8_markdown(tmp_path):
    path = tmp_path / "release-notes.md"
    path.write_text("## Added\n\n- Initial release.\n")

    assert release_notes.check_notes(path).startswith("## Added")


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
