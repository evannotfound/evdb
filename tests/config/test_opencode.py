from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
COMMAND = ROOT / ".opencode/commands/changelog.md"


def _markdown(path):
    marker, frontmatter, body = path.read_text().split("---", 2)
    assert not marker
    return yaml.safe_load(frontmatter), body.strip()


def test_changelog_command_uses_model_and_autonomous_repository_inspection():
    config, body = _markdown(COMMAND)

    assert config == {
        "description": "Generate repository-grounded notes for an evdb release",
        "model": "openai/gpt-5.6-sol",
        "variant": "high",
    }
    for phrase in (
        "$ARGUMENTS",
        "GitHub release metadata",
        "latest non-draft release",
        "Work autonomously",
        "`gh`, Git commands",
        "real diffs",
        "release-notes.md",
        "Write no other file",
        "do not publish",
    ):
        assert phrase in body
    assert "release-input.md" not in body
    assert "Do not run shell or Git" not in body


def test_changelog_command_uses_clear_sections_and_change_driven_length():
    _, body = _markdown(COMMAND)

    for phrase in (
        "`## Features`",
        "`## Improvements`",
        "`## Bugfixes`",
        "`## Breaking changes`",
        "Include only sections with at least one entry",
        "each distinct notable change",
        "Let the number of bullets reflect",
        "There is no fixed bullet or word limit",
        "plain, direct Markdown",
        "No notable changes.",
    ):
        assert phrase in body
    assert "no more than five bullets" not in body
    assert "150 words" not in body


def test_generated_release_note_files_are_ignored():
    ignored = (ROOT / ".gitignore").read_text().splitlines()

    assert "/release-input.md" not in ignored
    assert "/release-notes.md" in ignored
    assert not any(line.startswith(".opencode") for line in ignored)
