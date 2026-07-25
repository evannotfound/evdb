from datetime import datetime, timedelta, timezone
from pathlib import Path

from evanovation_db import deployment, status
from evanovation_db.config import Config, load_lock
from evanovation_db.errors import CommandError
from evanovation_db.run import Result


def test_assessment_reports_healthy_stale_stopped_drifted_and_pending(config):
    current = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
    selected = _selected(config, "postgres", "redis", "dragonfly")
    active = _manifest(config, selected)
    observed = _observed(config, active, selected, current)

    healthy = status.assess(config, active, observed, now=current)

    assert healthy["version"] == status.VERSION
    assert healthy["healthy"] is True
    assert {row["state"] for row in healthy["databases"]} == {"healthy"}

    stale = _copy_observed(observed)
    stale["databases"][0]["upload"]["time"] = (
        current - timedelta(hours=config.host.backup_max_age_hours + 1)
    ).isoformat()
    assert _row(status.assess(config, active, stale, now=current), selected[0])["state"] == "stale"

    stopped = _copy_observed(observed)
    stopped["databases"][0]["container"].update({"state": "exited", "running": False})
    stopped["databases"][0]["engine_check"] = {"ok": None, "error": None}
    stopped_row = _row(status.assess(config, active, stopped, now=current), selected[0])
    assert stopped_row["state"] == "stopped"

    drifted = _copy_observed(observed)
    drifted["databases"][0]["container"]["image"] = "postgres:manual@sha256:" + "f" * 64
    row = _row(status.assess(config, active, drifted, now=current), selected[0])
    assert row["state"] == "drifted"
    assert row["deployment"]["state"] == "drifted"

    wanted = _copy_manifest(active)
    wanted["databases"][selected[0].selector]["config_hash"] = "pending"
    row = _row(status.assess(config, wanted, observed, now=current), selected[0])
    assert row["state"] == "pending"
    assert row["healthy"] is True

    planned = _copy_observed(observed)
    wanted = _copy_manifest(active)
    wanted_image = "postgres:17@sha256:" + "e" * 64
    wanted["databases"][selected[0].selector]["image"] = wanted_image
    planned["databases"][0]["container"]["image"] = wanted_image
    row = _row(status.assess(config, wanted, planned, now=current), selected[0])
    assert row["state"] == "pending"
    assert row["deployment"]["state"] == "pending"


def test_operation_failure_does_not_replace_recent_success_facts(config):
    current = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
    selected = _selected(config, "postgres")
    active = _manifest(config, selected)
    observed = _observed(config, active, selected, current)
    observed["databases"][0]["errors"] = {
        "backup": {
            "command": "backup",
            "step": "upload",
            "time": current.isoformat(),
            "message": "upload failed",
        }
    }

    row = status.assess(config, active, observed, now=current)["databases"][0]

    assert row["state"] == "failed"
    assert row["backup"]["state"] == "fresh"
    assert row["backup"]["snapshot"] == "snapshot-test"
    assert row["restore"]["state"] == "fresh"
    assert row["errors"]["backup"]["message"] == "upload failed"


def test_pending_new_database_is_not_stale_only_because_it_has_no_history(config):
    current = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
    instance = config.get("postgres", "test-dev-01")
    wanted = _manifest(config, (instance,))
    active = {**wanted, "databases": {}}
    observed = _observed(config, active, (), current)

    row = status.assess(config, wanted, observed, now=current)["databases"][0]

    assert row["state"] == "pending"
    assert row["backup"]["state"] == "n/a"
    assert row["restore"]["state"] == "n/a"


def test_host_low_disk_and_timer_state_are_unhealthy(config):
    current = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
    selected = _selected(config, "postgres")
    active = _manifest(config, selected)
    observed = _observed(config, active, selected, current)
    observed["host"]["disks"]["backup"] = {
        "free_gb": 1.0,
        "minimum_gb": 5,
        "ok": False,
        "error": None,
    }
    observed["host"]["timers"] = {
        "ok": False,
        "required": [
            {
                "name": "evanovation-db-status.timer",
                "active": False,
                "enabled": True,
                "ok": False,
                "state": "inactive/enabled",
            }
        ],
        "error": None,
    }

    result = status.assess(config, active, observed, now=current)
    rendered = status.render(result)

    assert result["healthy"] is False
    assert "backup disk has 1.0 GiB free" in rendered
    assert "timer evanovation-db-status.timer is inactive/enabled" in rendered


def test_remote_checks_each_engine_and_continues_after_one_timeout(config, monkeypatch):
    selected = _selected(config, "postgres", "redis", "dragonfly")
    current = Config(config.host, selected)
    active = _manifest(current, selected)
    calls = []

    monkeypatch.setattr(status, "_release", lambda value: (active["id"], active, True, None))
    monkeypatch.setattr(
        status,
        "_disk",
        lambda path, minimum: {"free_gb": 10.0, "minimum_gb": minimum, "ok": True, "error": None},
    )
    monkeypatch.setattr(
        status,
        "_timers",
        lambda value: {"ok": True, "required": [], "error": None},
    )
    monkeypatch.setattr(
        status.deployment,
        "infrastructure_state",
        lambda value, manifest: _infrastructure(manifest),
    )

    def container(value, instance, *, timeout):
        calls.append(("docker", instance.engine, timeout))
        if instance.engine == "postgres":
            raise CommandError("command timed out after 10s")
        return _container(instance.image)

    def engine(value, instance, *, timeout):
        calls.append(("engine", instance.engine, timeout))
        return True

    monkeypatch.setattr(status.details, "container", container)
    monkeypatch.setattr(status.deployment, "engine_healthy", engine)
    monkeypatch.setattr(
        status.deployment,
        "database_state",
        lambda value, database: {
            "services": {
                name: {
                    "running": True,
                    "healthy": True if service["health"] == "docker" else None,
                    "image": service["image"],
                    "service_hash": database["service_hash"],
                }
                for name, service in database["services"].items()
            }
        },
    )

    result = status.remote(current)

    assert len(result["databases"]) == 3
    assert result["databases"][0]["error"] == "command timed out after 10s"
    assert {item[1] for item in calls if item[0] == "engine"} == {"redis", "dragonfly"}


def test_required_timer_check_parses_active_and_enabled_independently(config, monkeypatch):
    current = Config(config.host, _selected(config, "postgres"))

    def fake_run(args, **kwargs):
        names = [item for item in args if item.endswith(".timer")]
        blocks = []
        for index, name in enumerate(names):
            blocks.append(
                f"Id={name}\nActiveState={'inactive' if index == 0 else 'active'}\n"
                "UnitFileState=enabled\n"
            )
        return Result(tuple(args), 0, "\n".join(blocks), "")

    monkeypatch.setattr(status, "run", fake_run)

    result = status._timers(current)

    assert result["ok"] is False
    assert any(item["name"].startswith("evanovation-db-backup@") for item in result["required"])
    assert result["required"][0]["active"] is False
    assert result["required"][0]["enabled"] is True


def test_unreachable_status_is_versioned_and_secret_free(config):
    result = status.unreachable(
        config, CommandError("connection refused"), selector="postgres/test-dev-01"
    )

    assert result["version"] == status.VERSION
    assert result["healthy"] is False
    assert result["host"]["ssh"] == {"reachable": False, "error": "connection refused"}
    assert result["databases"][0]["state"] == "failed"
    assert "op://" not in str(result)


def test_status_reports_retained_prior_data_without_marking_it_unhealthy(config):
    current = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
    selected = _selected(config, "redis")
    active = _manifest(config, selected)
    observed = _observed(config, active, selected, current)
    retained = str(selected[0].data.with_name("data.retained-restore"))
    observed["databases"][0]["retained"] = [retained]

    result = status.assess(config, active, observed, now=current)

    assert result["healthy"] is True
    assert result["databases"][0]["retained_data"] == [retained]
    assert f"RETAINED: {retained}" in status.render(result)


def test_status_reports_same_image_with_wrong_or_missing_contract_hash_as_drift(config):
    current = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
    selected = _selected(config, "postgres")
    active = _manifest(config, selected)
    observed = _observed(config, active, selected, current)

    observed["databases"][0]["container"]["service_hash"] = None
    row = status.assess(config, active, observed, now=current)["databases"][0]
    assert row["state"] == "drifted"
    assert "service contract" in row["details"][0]

    observed = _observed(config, active, selected, current)
    observed["infrastructure"]["traefik"]["service_hash"] = "wrong"
    result = status.assess(config, active, observed, now=current)
    assert result["host"]["infrastructure"]["state"] == "drifted"
    assert result["healthy"] is False


def test_status_blocks_unhealthy_http_and_wrong_pgbouncer_contract(config):
    current = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
    selected = _selected(config, "postgres", "redis")
    active = _manifest(config, selected)
    observed = _observed(config, active, selected, current)

    postgres = observed["databases"][0]
    pooler = next(
        name
        for name, item in active["databases"][selected[0].selector]["services"].items()
        if item["health"] == "docker"
    )
    postgres["services"][pooler]["service_hash"] = "wrong"
    redis = observed["databases"][1]
    http = next(
        name
        for name, item in active["databases"][selected[1].selector]["services"].items()
        if item["health"] == "docker"
    )
    redis["services"][http]["healthy"] = False

    result = status.assess(config, active, observed, now=current)

    postgres_row = _row(result, selected[0])
    redis_row = _row(result, selected[1])
    assert postgres_row["deployment"]["state"] == "drifted"
    assert any("sidecar" in detail for detail in postgres_row["details"])
    assert redis_row["state"] == "failed"
    assert redis_row["deployment"]["state"] == "drifted"


def _selected(config, *engines):
    return tuple(
        next(item for item in config.instances if item.engine == engine) for engine in engines
    )


def _manifest(config, instances):
    root = Path(__file__).parents[2]
    lock = load_lock(root / "config/montreal-01/host.lock.json")
    return deployment.build(Config(config.host, tuple(instances)), lock, "a" * 64).manifest


def _observed(config, manifest, instances, current):
    return {
        "version": status.VERSION,
        "host": {
            "id": config.host.id,
            "active_release": manifest["id"],
            "release_consistent": True,
            "release_error": None,
            "disks": {
                name: {"free_gb": 20.0, "minimum_gb": 5, "ok": True, "error": None}
                for name in ("data", "backup")
            },
            "timers": {"ok": True, "required": [], "error": None},
        },
        "manifest": manifest,
        "infrastructure": _infrastructure(manifest),
        "databases": [
            _facts(item, current, manifest["databases"][item.selector]) for item in instances
        ],
    }


def _facts(instance, current, manifest):
    return {
        "selector": instance.selector,
        "engine": instance.engine,
        "durable": instance.durable,
        "container": _container(instance.image, manifest["service_hash"]),
        "services": {
            name: {
                "running": True,
                "healthy": True if service["health"] == "docker" else None,
                "image": service["image"],
                "service_hash": manifest["service_hash"],
            }
            for name, service in manifest["services"].items()
        },
        "engine_check": {"ok": True, "error": None},
        "retained": [],
        "backup": {"time": current.isoformat()} if instance.durable else None,
        "upload": {"ok": True, "time": current.isoformat(), "snapshot": "snapshot-test"}
        if instance.durable
        else None,
        "restore": {"ok": True, "time": current.isoformat(), "backup": "backup-test"}
        if instance.durable
        else None,
        "errors": {},
        "error": None,
    }


def _container(image, service_hash="service-contract"):
    return {
        "state": "running",
        "running": True,
        "health": "healthy",
        "image": image,
        "image_id": "sha256:" + "1" * 64,
        "service_hash": service_hash,
    }


def _infrastructure(manifest):
    traefik = manifest["infrastructure"]["traefik"]
    return {
        "network": {"exists": True},
        "traefik": {
            "running": True,
            "healthy": True,
            "image": traefik["image"],
            "service_hash": traefik["service_hash"],
        },
    }


def _copy_observed(value):
    import copy

    return copy.deepcopy(value)


def _copy_manifest(value):
    return _copy_observed(value)


def _row(result, instance):
    return next(item for item in result["databases"] if item["selector"] == instance.selector)
