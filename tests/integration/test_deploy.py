import grp
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from evanovation_db.config import load

ROOT = Path(__file__).parents[2]


def test_disposable_local_deploy(tmp_path):
    ansible = shutil.which("ansible-playbook")
    if ansible is None:
        pytest.skip("ansible-playbook is not installed")

    user = pwd.getpwuid(os.getuid()).pw_name
    group = grp.getgrgid(os.getgid()).gr_name
    opt = tmp_path / "opt"
    config_dir = tmp_path / "etc"
    state = tmp_path / "state"
    units = tmp_path / "systemd"
    command = [
        ansible,
        "-i",
        str(ROOT / "ansible/test-hosts.yml"),
        str(ROOT / "ansible/databases.yml"),
        "-e",
        "target=test",
        "-e",
        f"evdb_root={opt}",
        "-e",
        f"evdb_config_dir={config_dir}",
        "-e",
        f"evdb_secret_dir={config_dir / 'secrets'}",
        "-e",
        f"evdb_state_dir={state}",
        "-e",
        f"evdb_unit_dir={units}",
        "-e",
        f"evdb_repo={ROOT}",
        "-e",
        f"evdb_source_dir={ROOT / 'config/montreal-01'}",
        "-e",
        f"evdb_python={sys.executable}",
        "-e",
        "evdb_release=0000000000000000000000000000000000000000",
        "-e",
        f"evdb_user={user}",
        "-e",
        f"evdb_group={group}",
        "-e",
        f"evdb_admin_user={user}",
        "-e",
        f"evdb_admin_group={group}",
        "-e",
        "evdb_manage_user=false",
        "-e",
        "evdb_manage_systemd=false",
        "-e",
        "evdb_skip_secrets=true",
    ]

    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr

    deployed = load(config_dir)
    assert len(deployed.instances) == 25
    assert deployed.host.config_dir == config_dir
    assert deployed.host.state_dir == state
    assert deployed.host.repos == {
        "postgres": str(state / "repos/postgres"),
        "kv": str(state / "repos/kv"),
    }
    assert (opt / "current").resolve() == opt / "releases" / ("0" * 40)
    assert len(list((config_dir / "compose/postgres").glob("*.yml"))) == 14
    assert len(list((config_dir / "compose/kv").glob("*.yml"))) == 11
    assert len(list(units.glob("*"))) == 10
    assert not list((config_dir / "secrets").iterdir())
