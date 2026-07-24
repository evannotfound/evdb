from copy import deepcopy
from pathlib import Path

import pytest

from evanovation_db.config import Config, ConfigError, Instance, load, render, require_valid

ROOT = Path(__file__).parents[2]


def test_montreal_config_has_every_instance():
    config = load(ROOT / "config/montreal-01")

    assert len(config.instances) == 25
    assert sum(item.engine == "postgres" for item in config.instances) == 14
    assert sum(item.engine == "dragonfly" for item in config.instances) == 7
    assert sum(item.engine == "redis" for item in config.instances) == 4
    assert sum(bool(item.http and item.http["enabled"]) for item in config.instances) == 11
    assert config.host.resources == {"traefik": "unlimited"}
    assert all(item.resources["database"] == "unlimited" for item in config.instances)
    assert all(
        item.resources.get("pgbouncer") == "unlimited"
        for item in config.instances
        if item.engine == "postgres" and item.settings["pgbouncer"]
    )
    assert all(
        item.resources.get("http") == "unlimited"
        for item in config.instances
        if item.http and item.http["enabled"]
    )


def test_same_product_name_can_exist_in_both_groups():
    config = load(ROOT / "config/montreal-01")

    assert config.get("postgres", "vercount-prod-01").engine == "postgres"
    assert config.get("kv", "vercount-prod-01").engine == "dragonfly"


def test_duplicate_name_in_one_group_fails():
    config = load(ROOT / "config/montreal-01")
    first = config.instances[0]
    duplicate = Instance.from_dict(deepcopy(_instance_data(first)))

    with pytest.raises(ConfigError, match="duplicate id"):
        require_valid(Config(config.host, (*config.instances, duplicate)))


def test_target_data_change_fails():
    config = load(ROOT / "config/montreal-01")
    data = _instance_data(config.instances[0])
    data["target"]["data"] = "/wrong"
    changed = Instance.from_dict(data)

    with pytest.raises(ConfigError, match="target data path change"):
        require_valid(Config(config.host, (changed, *config.instances[1:])))


def test_latest_target_image_fails():
    config = load(ROOT / "config/montreal-01")
    data = _instance_data(config.instances[0])
    data["target"]["image"] = "postgres:latest"
    changed = Instance.from_dict(data)

    with pytest.raises(ConfigError, match="fixed version and digest"):
        require_valid(Config(config.host, (changed, *config.instances[1:])))


def test_changed_resource_contract_fails():
    config = load(ROOT / "config/montreal-01")
    data = _instance_data(config.get("kv", "test-dev-01"))
    data["resources"] = {"database": "512m", "http": "unlimited"}
    changed = Instance.from_dict(data)

    with pytest.raises(ConfigError, match="current unlimited setting"):
        require_valid(Config(config.host, (changed,)))


def test_secret_value_fails_without_printing_it():
    config = load(ROOT / "config/montreal-01")
    data = _instance_data(config.instances[0])
    data["secrets"]["password"] = "do-not-print"
    changed = Instance.from_dict(data)

    with pytest.raises(ConfigError) as caught:
        require_valid(Config(config.host, (changed, *config.instances[1:])))
    assert "do-not-print" not in str(caught.value)


def test_http_enabled_must_be_boolean():
    config = load(ROOT / "config/montreal-01")
    kv_index = next(index for index, item in enumerate(config.instances) if item.group == "kv")
    data = _instance_data(config.instances[kv_index])
    data["http"]["enabled"] = "yes"
    changed = Instance.from_dict(data)
    items = list(config.instances)
    items[kv_index] = changed

    with pytest.raises(ConfigError, match="HTTP enabled must be a boolean"):
        require_valid(Config(config.host, tuple(items)))


def test_render_keeps_duplicate_product_names(tmp_path):
    rendered = render(ROOT / "config/montreal-01", tmp_path)

    assert len(rendered.instances) == 25
    assert (tmp_path / "instances/postgres-vercount-prod-01.json").is_file()
    assert (tmp_path / "instances/kv-vercount-prod-01.json").is_file()
    runtime = load(tmp_path)
    assert runtime.host.runtime
    assert (
        runtime.get("postgres", "vercount-prod-01")
        .secrets["password"]
        .endswith("/secrets/postgres-vercount-prod-01.password")
    )


def _instance_data(instance: Instance) -> dict:
    data = deepcopy(vars(instance))
    data["data"] = str(instance.data)
    return data
