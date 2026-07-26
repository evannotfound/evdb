from pathlib import Path

from evanovation_db import cli

ROOT = Path(__file__).parents[2]
README = (ROOT / "README.md").read_text()
DOCS = "\n".join(path.read_text() for path in sorted((ROOT / "docs").glob("*.md")))
TEXT = README + DOCS
COMMANDS = (
    "status",
    "database list",
    "database add",
    "database info",
    "database configure",
    "database start",
    "database stop",
    "database restart",
    "database logs",
    "backup create",
    "backup list",
    "backup test",
    "backup retention",
    "backup prune",
    "backup repository-check",
    "restore",
    "host check",
    "host setup",
    "host update",
)
REMOVED_COMMANDS = ("plan", "apply", "releases", "rollback", "promote", "backup-check")


def test_docs_cover_every_public_host_command():
    parser = cli.parser()

    for command in COMMANDS:
        parser.parse_args(command.split())
        assert f"evdb {command}" in TEXT
    assert "uv sync --locked" in README


def test_docs_remove_controller_and_release_commands():
    for command in REMOVED_COMMANDS:
        assert f"evdb {command}" not in TEXT


def test_docs_record_host_local_safety_contracts():
    normalized = " ".join(TEXT.lower().split())
    for phrase in (
        "/etc/evdb/host.yml",
        "project/role",
        "machine-owned state",
        "private host files",
        "complete credentials",
        "safety backup",
        "expected outage",
        "same filesystem",
        "automatic recovery",
        "external HTTP proxy",
        "one previous version",
        "preserves timer state",
        "standalone release",
        "SHA-256",
        "artifact attestations",
        "retention project/role --dry-run",
        "separate production migration",
    ):
        assert " ".join(phrase.lower().split()) in normalized


def test_docs_show_public_binary_install_and_no_fixture_command_override():
    for phrase in (
        "releases/latest/download/install.sh",
        "releases/download/v1.2.3/install.sh",
        "sudo evdb host setup",
        "sudo evdb host update 1.3.0",
        "evdb --version",
        "evdb_linux_arm64.tar.gz",
        "evdb_linux_amd64.tar.gz",
    ):
        assert phrase in TEXT
    assert "uv tool install" not in README + DOCS
    assert "Python package registry" not in README + DOCS
    assert "--config" not in README + DOCS


def test_readme_is_a_concise_public_entry_point():
    for heading in (
        "## Features",
        "## Install",
        "## Use",
        "## Documentation",
        "## Development",
        "## License",
    ):
        assert heading in README
    assert len(README.splitlines()) < 120
    assert "projects:" not in README
    assert "montreal-01" not in README
    assert "MIT" in README
