import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from evanovation_db import ansible as ansible_inputs
from evanovation_db.config import Config, load

ROOT = Path(__file__).parents[2]


def test_disposable_local_deploy(tmp_path):
    ansible = shutil.which("ansible-playbook")
    if ansible is None:
        pytest.skip("ansible-playbook is not installed")

    opt = tmp_path / "opt"
    config_dir = tmp_path / "etc"
    state = tmp_path / "state"
    source = load(ROOT / "config/montreal-01")
    host = replace(
        source.host,
        config_dir=config_dir,
        state_dir=state,
        backup_dir=state / "backups",
        lock_dir=state / "locks",
        repos={
            "postgres": str(state / "repos/postgres"),
            "kv": str(state / "repos/kv"),
        },
    )
    config = Config(host, source.instances)

    with ansible_inputs.inputs(config, target="test", confirmed=True) as generated:
        command = [
            ansible,
            "-i",
            str(ROOT / "ansible/test-hosts.yml"),
            str(ROOT / "ansible/databases.yml"),
            "--extra-vars",
            f"@{generated.variables}",
            "--extra-vars",
            f"evdb_root={opt}",
            "--extra-vars",
            f"evdb_repo={ROOT}",
            "--extra-vars",
            f"evdb_python={sys.executable}",
        ]
        checked = subprocess.run(
            [*command, "--check"], cwd=ROOT, text=True, capture_output=True, timeout=180
        )
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert not opt.exists()
        assert not config_dir.exists()

        legacy = config_dir / "host.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"legacy": true}\n')
        stale = opt / "host-runtime/runtime/instances/stale.json"
        stale.parent.mkdir(parents=True)
        stale.write_text('{"instance": "stale"}\n')

        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        repeated = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=180)
        assert repeated.returncode == 0, repeated.stdout + repeated.stderr
        assert "changed=0" in repeated.stdout

    runtime = opt / "host-runtime/runtime"
    deployed = load(runtime)
    assert len(deployed.instances) == 25
    assert deployed.host.config_dir == config_dir
    assert deployed.host.state_dir == state
    assert deployed.host.repos == {
        "postgres": str(state / "repos/postgres"),
        "kv": str(state / "repos/kv"),
    }
    assert (opt / "host-runtime/src/evanovation_db/cli.py").is_file()
    assert not (opt / "current").exists()
    assert not list((opt / "releases").iterdir())
    assert not (config_dir / "compose").exists()
    assert len(list((runtime / "instances").glob("*.json"))) == 25
    assert not (runtime / "instances/stale.json").exists()
    assert (config_dir / "host.json").read_text() == '{"legacy": true}\n'
    assert not (config_dir / "jobs").exists()
    assert (runtime / "host.json").stat().st_mode & 0o777 == 0o640
    assert all(
        path.stat().st_mode & 0o777 == 0o640 for path in (runtime / "instances").glob("*.json")
    )
    assert not (tmp_path / "systemd").exists()
    assert not list((config_dir / "secrets").iterdir())
