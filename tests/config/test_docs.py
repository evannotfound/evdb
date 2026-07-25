from pathlib import Path

from evanovation_db import controller

ROOT = Path(__file__).parents[2]
README = (ROOT / "README.md").read_text()
DOCS = "\n".join(path.read_text() for path in sorted((ROOT / "docs").glob("*.md")))
COMMANDS = (
    "validate",
    "plan",
    "apply",
    "create",
    "show",
    "status",
    "start",
    "stop",
    "restart",
    "logs",
    "backup",
    "backups",
    "backup-check",
    "releases",
    "rollback",
    "restore",
    "promote",
)


def test_readme_workflows_match_public_help():
    help_text = controller.parser().format_help()

    for command in COMMANDS:
        assert command in help_text
        assert f"evdb {command}" in README or f"evdb --config config/my-host {command}" in README
    assert "uv run evdb" in README
    assert "evanovation-db restore-check" not in README + DOCS


def test_docs_record_operator_safety_contracts():
    text = README + DOCS

    for phrase in (
        "host.lock.json",
        "never edit it manually",
        "typed selector",
        "complete usable credentials",
        "Connect cannot create or edit items",
        "Expected outage",
        "atomically renames",
        "There is no destructive in-place restore",
        "Rollback is deployment recovery, not data recovery",
        "preserve existing timer state",
        "/opt/evanovation-db/host-runtime/runtime",
        "credential or protected database config files",
        "external HTTP proxy remains outside",
    ):
        assert phrase.lower() in text.lower()
