import json
from dataclasses import replace
from pathlib import Path

import tomllib

from evanovation_db import ansible
from evanovation_db.config import Config
from evanovation_db.run import Result

ROOT = Path(__file__).parents[2]


def test_public_and_host_runtime_entry_points_are_distinct():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

    assert project["scripts"] == {
        "evdb": "evanovation_db.controller:main",
        "evanovation-db": "evanovation_db.cli:main",
    }


def test_generated_ansible_inputs_are_private_normalized_and_temporary(config):
    host = replace(config.host, ssh="deploy@test-host")
    selected = Config(host, config.instances[:2])

    with ansible.inputs(selected, target="managed") as generated:
        root = generated.inventory.parent
        inventory = json.loads(generated.inventory.read_text())
        variables = json.loads(generated.variables.read_text())

        assert inventory["all"]["children"]["managed"]["hosts"][host.id] == {
            "ansible_host": "test-host",
            "ansible_user": "deploy",
            "evdb_become": True,
        }
        assert variables["evdb_config"]["host"]["id"] == host.id
        assert variables["evdb_config"]["host"]["runtime"] is True
        assert variables["evdb_config"]["host"]["secrets"] == {
            "restic_password": str(host.config_dir / "secrets/restic_password"),
            "rclone_config": str(host.state_dir / "rclone/rclone.conf"),
        }
        assert [item["id"] for item in variables["evdb_config"]["databases"]] == [
            item.id for item in selected.instances
        ]
        assert variables["target"] == "managed"
        assert variables["apply"] == "no"
        assert "evdb_secret_files" not in variables
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in root.iterdir())
        assert "test-password" not in generated.variables.read_text()

    assert not root.exists()


def test_controller_bootstrap_inventory_is_irreducibly_production(config):
    inventory, _ = ansible.data(config)

    assert set(inventory["all"]["children"]) == {"production"}
    assert config.host.id in inventory["all"]["children"]["production"]["hosts"]


def test_confirmed_bootstrap_uses_private_production_inputs_without_secret_arguments(
    config, monkeypatch
):
    seen = {}

    def execute(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        variables = Path(args[-1].removeprefix("@")).read_text()
        inventory = json.loads(Path(args[2]).read_text())
        values = json.loads(variables)
        assert set(inventory["all"]["children"]) == {"production"}
        assert values["target"] == "production"
        assert "evdb_secret_files" not in values
        assert Path(args[-1].removeprefix("@")).stat().st_mode & 0o777 == 0o600
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(ansible, "run", execute)

    ansible.bootstrap(config, timeout=17)

    assert seen["args"][0] == "ansible-playbook"
    assert seen["args"][1] == "-i"
    assert seen["args"][-2] == "--extra-vars"
    assert "secrets" not in seen["kwargs"]
    assert seen["kwargs"]["timeout"] == 17
