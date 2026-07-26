import ast
from pathlib import Path

from evanovation_db import cli
from tests.fixtures.containers import _guard

ROOT = Path(__file__).parents[2]
REMOVED_MODULES = {
    "ansible",
    "controller",
    "deployment",
    "details",
    "lifecycle",
    "planning",
    "remote",
}
REMOVED_COMMANDS = {"apply", "backup-check", "plan", "promote", "releases", "rollback"}
REMOVED_FILES = tuple(ROOT / "src/evanovation_db" / f"{name}.py" for name in REMOVED_MODULES)
REMOVED_TREES = (
    ROOT / "ansible",
    ROOT / "systemd",
    ROOT / "src/evanovation_db/backup",
    ROOT / "src/evanovation_db/restore",
)
PRODUCTION_CONFIG = "config/" + "montreal-01"


def test_superseded_architecture_paths_are_absent():
    assert not [path for path in REMOVED_FILES if path.exists()]
    artifacts = [
        path
        for root in REMOVED_TREES
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    assert not artifacts


def test_python_does_not_import_removed_architecture():
    failures = []
    for root in (ROOT / "src", ROOT / "tests"):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {item.name.split(".")[-1] for item in node.names}
                elif isinstance(node, ast.ImportFrom):
                    names = {item.name for item in node.names}
                    if node.module:
                        names.add(node.module.split(".")[-1])
                else:
                    continue
                found = names & REMOVED_MODULES
                if found:
                    failures.append(f"{path.relative_to(ROOT)}: {', '.join(sorted(found))}")
    assert not failures, failures


def test_public_cli_has_no_superseded_commands():
    actions = next(action for action in cli.parser()._actions if getattr(action, "choices", None))

    assert not (set(actions.choices) & REMOVED_COMMANDS)


def test_ci_and_tests_never_target_production_config():
    paths = [ROOT / ".github/workflows/check.yml", ROOT / "Makefile"]
    paths.extend((ROOT / "tests").rglob("*.py"))

    offenders = [path.relative_to(ROOT) for path in paths if PRODUCTION_CONFIG in path.read_text()]
    assert not offenders, offenders


def test_disposable_command_guard_rejects_production_and_host_mutation():
    unsafe = (
        ["ssh", "db-host"],
        ["systemctl", "restart", "docker"],
        ["tool", PRODUCTION_CONFIG],
    )
    for args in unsafe:
        try:
            _guard(args)
        except AssertionError:
            pass
        else:
            raise AssertionError(f"unsafe disposable command accepted: {args}")

    _guard(["restic", "snapshots"], {"RESTIC_REPOSITORY": "/tmp/local-repository"})


def test_operator_support_has_no_alternate_config_and_ci_keeps_docker_opt_in():
    support = [ROOT / "Makefile", ROOT / "README.md", *(ROOT / "docs").glob("*.md")]
    assert not [path.relative_to(ROOT) for path in support if "--config" in path.read_text()]

    makefile = (ROOT / "Makefile").read_text()
    assert "EVDB ?= /usr/local/bin/evdb" in makefile
    assert "EVDB ?= $(UV) run evdb" not in makefile

    workflow = (ROOT / ".github/workflows/check.yml").read_text()
    assert "command -v restic" in workflow
    assert "pytest tests/integration/test_restic.py" in workflow
    assert "pytest --collect-only -q tests/integration" in workflow
    assert "pytest tests/integration\n" not in workflow
