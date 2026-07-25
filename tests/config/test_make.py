import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_make_has_operator_targets():
    text = (ROOT / "Makefile").read_text()
    phony = text.split(".PHONY:", 1)[1].split("\n\n", 1)[0].replace("\\\n", " ").split()

    for name in (
        "help",
        "check",
        "validate",
        "plan",
        "apply",
        "create",
        "show",
        "start",
        "stop",
        "restart",
        "logs",
        "backup",
        "backups",
        "backup-check",
        "status",
        "releases",
        "rollback",
        "restore",
        "promote",
    ):
        assert name in phony
    assert "deploy-backup:" not in text
    assert "deploy-db:" not in text


def test_make_operator_target_requires_explicit_config():
    result = subprocess.run(
        ["make", "--silent", "status"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "CONFIG is required" in result.stderr


def test_make_database_target_requires_selector():
    result = subprocess.run(
        ["make", "--silent", "backup", "CONFIG=tests/fixtures/config/minimal"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "DB is required" in result.stderr


def test_make_operator_recipes_use_evdb_controller():
    text = (ROOT / "Makefile").read_text()
    operator = text[text.index("validate: require-config") :]

    assert "$(EVDB) --config" in operator
    assert "ansible-playbook" not in operator
    assert "evanovation_db.cli" not in operator


def test_make_help_is_concise_and_lists_required_config():
    result = subprocess.run(
        ["make", "--silent", "help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert "Usage: make TARGET CONFIG=path" in result.stdout
    assert "Operator:" in result.stdout
    assert len(result.stdout.splitlines()) <= 8
