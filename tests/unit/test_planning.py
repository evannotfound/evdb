from copy import deepcopy
from pathlib import Path

import pytest

from evanovation_db import controller, planning
from evanovation_db.errors import ConfigError

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests/fixtures/config/minimal"


def test_compare_reports_create_update_pending_restart_and_drift():
    wanted = planning.desired(FIXTURE)
    state = _state(wanted)
    manifest = deepcopy(state["manifest"])
    live = deepcopy(state["live"])

    manifest["databases"].pop("dragonfly/queue-test-01")
    manifest["databases"]["postgres/example-prod-01"]["service_hash"] = "changed"
    manifest["databases"]["redis/cache-dev-01"]["config_hash"] = "changed"
    postgres = manifest["databases"]["postgres/example-prod-01"]
    postgres_primary = next(
        name for name, item in postgres["services"].items() if item["health"] == "engine"
    )
    live["postgres/example-prod-01"]["services"][postgres_primary]["image"] = "postgres:manual"
    live["postgres/example-prod-01"]["services"][postgres_primary]["service_hash"] = "wrong"
    redis = manifest["databases"]["redis/cache-dev-01"]
    redis_primary = next(
        name for name, item in redis["services"].items() if item["health"] == "engine"
    )
    live["redis/cache-dev-01"]["services"][redis_primary]["running"] = False

    plan = planning.compare(
        wanted,
        {
            "release": manifest["id"],
            "manifest": manifest,
            "infrastructure": state["infrastructure"],
            "live": live,
        },
    )

    assert {(item.kind, item.selector) for item in plan.actions} == {
        ("create", "dragonfly/queue-test-01"),
        ("update", "postgres/example-prod-01"),
        ("drift", "postgres/example-prod-01"),
        ("pending", "redis/cache-dev-01"),
        ("restart", "redis/cache-dev-01"),
    }
    assert plan.affected == (
        "dragonfly/queue-test-01",
        "postgres/example-prod-01",
        "redis/cache-dev-01",
    )


def test_compare_noop_and_blocked_removal():
    wanted = planning.desired(FIXTURE)
    state = _state(wanted)

    assert planning.compare(wanted, state).render().endswith("No changes.")

    state = deepcopy(state)
    state["manifest"]["databases"]["redis/retired-prod-01"] = {
        **state["manifest"]["databases"]["redis/cache-dev-01"],
        "project": "retired-prod-01",
    }
    plan = planning.compare(wanted, state)

    assert [item.selector for item in plan.blocked] == ["redis/retired-prod-01"]
    with pytest.raises(ConfigError, match="future retirement workflow"):
        planning.require_applicable(plan)


def test_host_runtime_update_is_pending_without_affecting_database_projects():
    wanted = planning.desired(FIXTURE)
    state = _state(wanted)
    state["manifest"]["code_hash"] = "0" * 64

    plan = planning.compare(wanted, state)

    assert [(item.kind, item.selector) for item in plan.actions] == [
        ("pending", f"host/{wanted.config.host.id}")
    ]
    assert plan.affected == ()


def test_traefik_change_and_same_image_contract_drift_affect_host_selector():
    wanted = planning.desired(FIXTURE)
    state = _state(wanted)
    host = f"host/{wanted.config.host.id}"
    state["manifest"]["infrastructure"]["traefik"]["service_hash"] = "0" * 64
    state["infrastructure"]["traefik"]["service_hash"] = None

    plan = planning.compare(wanted, state)

    assert {(item.kind, item.selector) for item in plan.actions} == {
        ("update", host),
        ("drift", host),
    }
    assert plan.affected == (host,)
    assert host in plan.render()


@pytest.mark.parametrize("failure", ["absent", "stopped", "unhealthy", "contract"])
def test_sidecar_failure_is_planned_as_drift(failure):
    wanted = planning.desired(FIXTURE)
    state = _state(wanted)
    selector = "postgres/example-prod-01"
    database = state["manifest"]["databases"][selector]
    sidecar = next(
        name for name, item in database["services"].items() if item["health"] == "docker"
    )
    live = state["live"][selector]["services"]
    if failure == "absent":
        live.pop(sidecar)
    elif failure == "stopped":
        live[sidecar]["running"] = False
    elif failure == "unhealthy":
        live[sidecar]["healthy"] = False
    else:
        live[sidecar]["service_hash"] = "wrong"

    plan = planning.compare(wanted, state)

    assert ("drift", selector) in {(item.kind, item.selector) for item in plan.actions}
    assert selector in plan.affected


def test_controller_plan_is_read_only(tmp_path, monkeypatch, capsys):
    root = tmp_path / "config"
    root.mkdir()
    for name in ("host.yml", "host.lock.json"):
        (root / name).write_bytes((FIXTURE / name).read_bytes())
    wanted = planning.desired(root)
    state = _state(wanted)
    before = {path.name: path.read_bytes() for path in root.iterdir()}

    def call(config, operation, selector):
        assert operation == "release_state"
        assert selector == config.host.id
        return state

    monkeypatch.setattr(controller.remote, "call", call)
    monkeypatch.setattr(
        controller,
        "_op",
        lambda host: pytest.fail("plan must not access 1Password"),
    )

    assert controller.main(["--config", str(root), "plan"]) == 0
    assert "No changes." in capsys.readouterr().out
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before


def _state(wanted):
    manifest = deepcopy(wanted.bundle.manifest)
    live = {
        selector: {
            "services": {
                name: {
                    "running": True,
                    "healthy": True if service["health"] == "docker" else None,
                    "image": service["image"],
                    "service_hash": item["service_hash"],
                }
                for name, service in item["services"].items()
            }
        }
        for selector, item in manifest["databases"].items()
    }
    infrastructure = manifest["infrastructure"]["traefik"]
    return {
        "release": manifest["id"],
        "manifest": manifest,
        "infrastructure": {
            "network": {"exists": True},
            "traefik": {
                "running": True,
                "healthy": True,
                "image": infrastructure["image"],
                "service_hash": infrastructure["service_hash"],
            },
        },
        "live": live,
    }
