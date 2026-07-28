import ast
from pathlib import Path

from evdb import cli

ROOT = Path(__file__).parents[2]
REMOVED = {"restore", "restic", "compose", "images", "secrets", "interactive", "log"}


def test_removed_modules_units_and_state_models_are_absent():
    assert not [
        ROOT / "src/evdb" / f"{name}.py"
        for name in REMOVED
        if (ROOT / "src/evdb" / f"{name}.py").exists()
    ]
    assert {path.name for path in (ROOT / "src/evdb/units").iterdir()} == {
        "evdb-backup.service",
        "evdb-backup.timer",
    }
    source = "\n".join(path.read_text() for path in (ROOT / "src/evdb").rglob("*.py"))
    for name in ("MachineState", "RoleState", "ImageState", "transaction.json"):
        assert name not in source


def test_production_does_not_import_removed_modules():
    failures = []
    for path in (ROOT / "src/evdb").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names = set()
            if isinstance(node, ast.Import):
                names = {
                    item.name.split(".")[-1] for item in node.names if item.name.startswith("evdb.")
                }
            elif isinstance(node, ast.ImportFrom) and node.level:
                names = {item.name for item in node.names}
                if node.module:
                    names.add(node.module.split(".")[-1])
            if names & REMOVED:
                failures.append((path, names & REMOVED))
    assert not failures


def test_public_cli_rejects_removed_surface():
    choices = next(action.choices for action in cli.parser()._actions if action.choices)
    assert set(choices) == {"init", "status", "database", "backup"}
