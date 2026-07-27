from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
AGENT = ROOT / ".opencode/agents/release-notes.md"
COMMAND = ROOT / ".opencode/commands/changelog.md"


def _markdown(path):
    marker, frontmatter, body = path.read_text().split("---", 2)
    assert not marker
    return yaml.safe_load(frontmatter), body.strip()


def test_release_note_agent_has_bounded_model_and_tools():
    config, body = _markdown(AGENT)

    assert config["mode"] == "primary"
    assert config["model"] == "openai/gpt-5.6-sol"
    assert config["variant"] == "high"
    assert config["steps"] == 40
    assert config["tools"] == {
        "webfetch": False,
        "websearch": False,
        "task": False,
        "question": False,
    }
    assert "untrusted evidence" in body


def test_release_note_agent_permissions_default_deny_and_allow_one_write():
    config, _ = _markdown(AGENT)
    permission = config["permission"]

    assert permission["*"] == "deny"
    assert permission["read"]["*"] == "allow"
    assert permission["read"][".secrets/**"] == "deny"
    assert permission["glob"] == "allow"
    assert permission["grep"] == "allow"
    assert permission["edit"] == {"*": "deny", "release-notes.md": "allow"}
    assert permission["external_directory"] == "deny"
    for name in ("webfetch", "websearch", "task", "question", "skill", "todowrite"):
        assert permission[name] == "deny"


def test_release_note_agent_denies_shell_access():
    config, _ = _markdown(AGENT)

    assert config["permission"]["bash"] == "deny"


def test_changelog_command_uses_agent_range_and_grounding_rules():
    config, body = _markdown(COMMAND)

    assert config == {
        "description": "Generate repository-grounded notes for an evdb release",
        "agent": "release-notes",
        "subtask": False,
    }
    for phrase in (
        "release-input.md",
        "authoritative candidate commit set",
        "untrusted evidence",
        "# Range patch` evidence",
        "Do not run shell or Git",
        "release-notes.md",
        "Write no other file",
        "do not publish",
    ):
        assert phrase in body


def test_changelog_command_sets_plain_language_limits():
    _, body = _markdown(COMMAND)

    for phrase in (
        "plain, direct Markdown",
        "one natural sentence",
        "no more than five bullets",
        "150 words in the whole document",
        "familiar words",
        "natural sentence structure",
        "complete thoughts",
        "Do not force brevity",
        "choppy, vague, or harder to understand",
        "unnecessary jargon",
    ):
        assert phrase in body


def test_generated_release_note_files_are_ignored():
    ignored = (ROOT / ".gitignore").read_text().splitlines()

    assert "/release-input.md" in ignored
    assert "/release-notes.md" in ignored
    assert not any(line.startswith(".opencode") for line in ignored)
