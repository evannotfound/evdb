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
        "preserve timer state",
        "uv build",
        "immutable artifacts",
        "retention project/role --dry-run",
        "separate production migration",
    ):
        assert phrase.lower() in TEXT.lower()


def test_docs_show_versioned_uv_bootstrap_and_no_fixture_command_override():
    for phrase in (
        'UV_TOOL_DIR="/opt/evdb/versions/${VERSION}/tools"',
        'UV_TOOL_BIN_DIR="/opt/evdb/versions/${VERSION}/bin"',
        'tool install "evanovation-db==${VERSION}"',
        'ln -sfn "versions/${VERSION}" /opt/evdb/current',
        "ln -sfn /opt/evdb/current/bin/evdb /usr/local/bin/evdb",
        "/usr/local/bin/evdb host setup",
    ):
        assert phrase in TEXT
    assert "--config" not in README + DOCS
