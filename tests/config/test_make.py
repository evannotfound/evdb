import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
PRODUCTION_CONFIG = "config/" + "montreal-01"
DIRECT_TARGETS = (
    "status",
    "database-list",
    "database-add",
    "database-info",
    "database-configure",
    "database-start",
    "database-stop",
    "database-restart",
    "database-logs",
    "backup-create",
    "backup-list",
    "backup-test",
    "restore",
    "host-check",
    "host-setup",
    "host-update",
)
REMOVED_TARGETS = (
    "validate",
    "plan",
    "apply",
    "create",
    "show",
    "backups",
    "backup-check",
    "releases",
    "rollback",
    "promote",
    "ansible-check",
)


def _makefile() -> str:
    return (ROOT / "Makefile").read_text()


def test_make_exposes_grouped_host_local_targets_only():
    text = _makefile()
    phony = text.split(".PHONY:", 1)[1].split("\n\n", 1)[0].replace("\\\n", " ").split()

    for name in ("help", "check", "lint", "test", *DIRECT_TARGETS):
        assert name in phony
    for name in REMOVED_TARGETS:
        assert name not in phony


def test_make_database_target_requires_project_role_selector():
    result = subprocess.run(
        ["make", "--silent", "backup-create"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "DB is required" in result.stderr


def test_make_recipes_use_grouped_evdb_commands_without_config_override():
    text = _makefile()

    assert "EVDB ?= /usr/local/bin/evdb" in text
    assert "$(EVDB) database" in text
    assert "$(EVDB) backup" in text
    assert "$(EVDB) host" in text
    assert "CONFIG_ARG" not in text
    assert "--config" not in text
    assert PRODUCTION_CONFIG not in text
    assert "ansible-playbook" not in text
    assert "systemd-analyze" not in text


def test_make_help_describes_host_local_variables_concisely():
    result = subprocess.run(
        ["make", "--silent", "help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert "CONFIG=" not in result.stdout
    assert "DB=project/role" in result.stdout
    assert len(result.stdout.splitlines()) <= 8


def test_make_runs_only_safe_restic_integration_and_collects_docker_suites():
    text = _makefile()

    assert "restic-integration" in text
    assert "command -v restic" in text
    assert "$(PYTEST) tests/integration/test_restic.py" in text
    assert "$(PYTEST) --collect-only -q tests/integration" in text
