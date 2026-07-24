import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_make_has_operator_targets():
    text = (ROOT / "Makefile").read_text()

    for name in (
        "check",
        "plan",
        "deploy-backup",
        "deploy-db",
        "backup",
        "restore-check",
        "status",
    ):
        assert f"{name}:" in text


def test_make_backup_requires_name():
    result = subprocess.run(
        ["make", "--silent", "backup", "ENGINE=postgres"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "NAME is required" in result.stderr
