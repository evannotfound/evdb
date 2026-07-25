import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from evanovation_db import controller, planning
from evanovation_db.config import Config, load
from evanovation_db.errors import (
    ConfigError,
    ProtocolError,
    ProtocolMismatchError,
    RuntimeUnavailableError,
)
from evanovation_db.secrets import SecretFile

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests/fixtures/config/minimal"


class Client:
    def __init__(self):
        self.preflights = 0
        self.ensured = []

    def preflight(self, *, write=False):
        assert write
        self.preflights += 1
        return {"type": "USER"}

    def ensure(self, instance):
        self.ensured.append(instance.selector)


def test_declined_apply_changes_nothing(monkeypatch, capsys):
    wanted = planning.desired(FIXTURE)
    state = _empty_state()
    plan = planning.compare(wanted, state)
    monkeypatch.setattr(controller, "_plan", lambda path: (wanted, state, plan))
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    monkeypatch.setattr(
        controller,
        "write_lock",
        lambda *args: pytest.fail("declined apply must not write the lock"),
    )
    monkeypatch.setattr(
        controller,
        "_op",
        lambda *args: pytest.fail("declined apply must not access 1Password"),
    )
    monkeypatch.setattr(
        controller.ansible,
        "bootstrap",
        lambda *args, **kwargs: pytest.fail("declined apply must not run Ansible"),
    )
    monkeypatch.setattr(
        controller.remote,
        "call",
        lambda *args, **kwargs: pytest.fail("declined apply must not write remotely"),
    )

    assert controller._apply(str(FIXTURE), yes=False) == 0
    assert "Cancelled." in capsys.readouterr().out


def test_yes_apply_writes_lock_ensures_new_item_and_calls_remote(monkeypatch, tmp_path):
    wanted = planning.desired(FIXTURE)
    state = _empty_state()
    plan = planning.compare(wanted, state)
    client = Client()
    calls = []
    order = []
    monkeypatch.setattr(controller, "_plan", lambda path: (wanted, state, plan))
    monkeypatch.setattr(controller.planning, "desired", lambda path: wanted)
    monkeypatch.setattr(controller, "write_lock", lambda path, lock: calls.append((path, lock)))
    monkeypatch.setattr(
        controller,
        "deployment_files",
        lambda config, op: (SecretFile(tmp_path / "secret", "protected\n"),),
    )
    monkeypatch.setattr(
        controller.ansible,
        "bootstrap",
        lambda *args, **kwargs: pytest.fail("routine apply must not run Ansible"),
    )

    def call(config, operation, selector, payload, **kwargs):
        order.append(("remote", operation))
        assert operation == "apply"
        assert selector == config.host.id
        assert kwargs["protected"]
        return {"release": wanted.bundle.manifest["id"], "affected": list(plan.affected)}

    monkeypatch.setattr(controller.remote, "call", call)

    assert controller._apply(str(FIXTURE), yes=True, client=client) == 0
    assert calls == [(wanted.lock_path, wanted.lock)]
    assert client.ensured == sorted(wanted.bundle.manifest["databases"])
    assert order == [("remote", "apply")]


@pytest.mark.parametrize(
    "bootstrap_error",
    [
        RuntimeUnavailableError("remote current runtime is unavailable"),
        ProtocolMismatchError("controller and current runtime protocols differ"),
    ],
)
def test_apply_bootstraps_only_explicit_runtime_upgrade_conditions(
    monkeypatch, tmp_path, bootstrap_error
):
    wanted = planning.desired(FIXTURE)
    state = _empty_state()
    plan = planning.compare(wanted, state)
    attempts = 0
    order = []
    client = Client()

    def make_plan(path, *, runtime="current"):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise bootstrap_error
        order.append(("plan", runtime))
        return wanted, state, plan

    monkeypatch.setattr(controller, "_plan", make_plan)
    monkeypatch.setattr(controller.planning, "desired", lambda path: wanted)
    monkeypatch.setattr(controller, "write_lock", lambda *args: None)
    monkeypatch.setattr(
        controller,
        "deployment_files",
        lambda config, op: (SecretFile(tmp_path / "secret", "protected\n"),),
    )

    def bootstrap(config, **kwargs):
        order.append("bootstrap")

    def call(config, operation, selector, payload, **kwargs):
        order.append(("remote", operation, kwargs["runtime"]))
        return {"release": wanted.bundle.manifest["id"], "affected": list(plan.affected)}

    monkeypatch.setattr(controller.ansible, "bootstrap", bootstrap)
    monkeypatch.setattr(controller.remote, "call", call)

    assert controller._apply(str(FIXTURE), yes=True, client=client) == 0
    assert order == [
        "bootstrap",
        ("plan", "bootstrap"),
        ("remote", "apply", "bootstrap"),
    ]


def test_missing_runtime_bootstrap_decline_performs_no_ansible_write(monkeypatch):
    wanted = planning.desired(FIXTURE)
    monkeypatch.setattr(
        controller,
        "_plan",
        lambda path: (_ for _ in ()).throw(RuntimeUnavailableError("host runtime unavailable")),
    )
    monkeypatch.setattr(controller.planning, "desired", lambda path: wanted)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    monkeypatch.setattr(
        controller.ansible,
        "bootstrap",
        lambda *args, **kwargs: pytest.fail("declined bootstrap must not run Ansible"),
    )

    assert controller._apply(str(FIXTURE), yes=False) == 0


@pytest.mark.parametrize(
    "error",
    [
        ProtocolError("invalid remote response"),
        ProtocolError("remote operation_failed: malformed active config"),
    ],
)
def test_apply_never_bootstraps_generic_protocol_or_remote_operation_errors(monkeypatch, error):
    monkeypatch.setattr(
        controller,
        "_plan",
        lambda path: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        controller.ansible,
        "bootstrap",
        lambda *args, **kwargs: pytest.fail("generic errors must not bootstrap"),
    )

    with pytest.raises(ProtocolError, match=str(error)):
        controller._apply(str(FIXTURE), yes=True)


def test_create_is_atomic_idempotent_and_preserves_desired_state_after_failure(
    tmp_path, monkeypatch
):
    root = tmp_path / "config"
    root.mkdir()
    for name in ("host.yml", "host.lock.json"):
        (root / name).write_bytes((FIXTURE / name).read_bytes())
    client = Client()
    monkeypatch.setattr(controller, "_source_op", lambda source: client)
    monkeypatch.setattr(
        controller,
        "_apply",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConfigError("deployment failed")),
    )

    command = ["--config", str(root), "create", "redis", "new-prod-01", "--yes"]
    assert controller.main(command) == 1
    assert controller.main(command) == 1

    data = yaml.safe_load((root / "host.yml").read_text())
    assert data["databases"].count({"name": "new-prod-01", "type": "redis"}) == 1
    assert client.preflights == 2
    assert client.ensured == ["redis/new-prod-01", "redis/new-prod-01"]
    assert not list(root.glob(".host.yml.*"))


def test_lifecycle_confirmation_selectors_and_logs(monkeypatch, capsys):
    config = load(FIXTURE)
    calls = []
    monkeypatch.setattr(controller, "load", lambda path: config)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    monkeypatch.setattr(
        controller.remote,
        "call",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert controller.main(["--config", "ignored", "stop", "example-prod-01"]) == 0
    assert calls == []

    def call(current, operation, selector, payload=None):
        calls.append((operation, selector, payload))
        if operation == "logs":
            return {"selector": selector, "logs": "safe output\n"}
        return {"action": operation, "selector": selector, "release": "release-test"}

    monkeypatch.setattr(controller.remote, "call", call)
    assert controller.main(["--config", "ignored", "restart", "example-prod-01", "--yes"]) == 0
    assert controller.main(["--config", "ignored", "logs", "example-prod-01", "--lines", "5"]) == 0
    assert calls == [
        ("restart", "postgres/example-prod-01", None),
        ("logs", "postgres/example-prod-01", {"lines": 5}),
    ]
    assert "safe output" in capsys.readouterr().out

    postgres = config.select("postgres/example-prod-01")
    redis = replace(config.select("redis/cache-dev-01"), id=postgres.id)
    ambiguous = Config(config.host, (postgres, redis))
    monkeypatch.setattr(controller, "load", lambda path: ambiguous)
    assert controller.main(["--config", "ignored", "start", "example-prod-01", "--yes"]) == 1
    assert len(calls) == 2


def test_status_json_filters_database_and_preserves_unhealthy_exit(monkeypatch, capsys):
    wanted = planning.desired(FIXTURE)
    config = wanted.config
    observed = _status_result(config, wanted.bundle.manifest)
    calls = []

    def call(current, operation, selector, **kwargs):
        calls.append((operation, selector, kwargs["timeout"]))
        return observed

    monkeypatch.setattr(controller.remote, "call", call)

    code = controller.main(["--config", str(FIXTURE), "status", "example-prod-01", "--json"])

    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result["version"] == controller.status.VERSION
    assert result["healthy"] is True
    assert [item["selector"] for item in result["databases"]] == ["postgres/example-prod-01"]
    assert calls[0][0:2] == ("status", config.host.id)
    assert "op://" not in json.dumps(result)

    monkeypatch.setattr(
        controller.remote,
        "call",
        lambda *args, **kwargs: (_ for _ in ()).throw(ProtocolError("connection refused")),
    )
    code = controller.main(["--config", str(FIXTURE), "status", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert code == 1
    assert result["host"]["ssh"]["reachable"] is False


def test_status_reports_source_lock_inconsistency_without_ssh(tmp_path, monkeypatch, capsys):
    root = tmp_path / "config"
    root.mkdir()
    for name in ("host.yml", "host.lock.json"):
        (root / name).write_bytes((FIXTURE / name).read_bytes())
    data = yaml.safe_load((root / "host.yml").read_text())
    data["host"]["images"]["postgres"] = "postgres:17.0"
    (root / "host.yml").write_text(yaml.safe_dump(data, sort_keys=False))
    monkeypatch.setattr(
        controller.remote,
        "call",
        lambda *args, **kwargs: pytest.fail("inconsistent source lock must not reach SSH"),
    )

    code = controller.main(["--config", str(root), "status", "--json"])

    result = json.loads(capsys.readouterr().out)
    assert code == 1
    assert result["host"]["source_lock_consistent"] is False
    assert "image postgres source changed" in result["host"]["release_error"]


def test_public_backup_commands_use_remote_protocol_and_validate_results(monkeypatch, capsys):
    config = load(FIXTURE)
    instance = config.select("example-prod-01")
    calls = []
    row = {
        "backup": "20260725T100000000000Z",
        "time": "2026-07-25T10:00:00+00:00",
        "snapshot": "snapshot-id",
        "local": True,
        "remote": True,
        "source": "local+remote",
        "verification": {"state": "verified", "time": "2026-07-25T11:00:00+00:00", "error": None},
    }

    def call(current, operation, selector, payload=None, **kwargs):
        calls.append((operation, selector, payload, kwargs))
        if operation == "backup":
            return {
                "selector": selector,
                "backup": row["backup"],
                "time": row["time"],
                "snapshot": row["snapshot"],
            }
        if operation == "backups":
            return {"selector": selector, "backups": [row]}
        return {
            "selector": selector,
            "backup": row["backup"],
            "snapshot": row["snapshot"],
            "time": row["time"],
            "result": {"databases": ["postgres"]},
        }

    monkeypatch.setattr(controller, "load", lambda path: config)
    monkeypatch.setattr(controller.remote, "call", call)

    assert controller.main(["--config", "ignored", "backup", "example-prod-01"]) == 0
    assert controller.main(["--config", "ignored", "backups", "example-prod-01"]) == 0
    assert (
        controller.main(["--config", "ignored", "backup-check", "example-prod-01", "snapshot-id"])
        == 0
    )

    output = capsys.readouterr().out
    assert "snapshot-id" in output
    assert [item[0] for item in calls] == ["backup", "backups", "backup_check"]
    assert calls[0][3]["timeout"] == config.host.timeouts["backup"]
    assert calls[2][2] == {"backup": "snapshot-id"}
    assert calls[2][3]["timeout"] == config.host.timeouts["restore"]
    assert all(item[1] == instance.selector for item in calls)


def test_backup_history_rejects_unexpected_secret_field_without_printing_it(monkeypatch, capsys):
    config = load(FIXTURE)
    monkeypatch.setattr(controller, "load", lambda path: config)
    monkeypatch.setattr(
        controller.remote,
        "call",
        lambda *args, **kwargs: {
            "selector": "postgres/example-prod-01",
            "backups": [
                {
                    "backup": "backup-id",
                    "time": "2026-07-25T10:00:00+00:00",
                    "snapshot": "snapshot-id",
                    "local": True,
                    "remote": True,
                    "source": "local+remote",
                    "verification": {"state": "verified", "time": None, "error": None},
                    "password": "must-not-print",
                }
            ],
        },
    )

    code = controller.main(["--config", "ignored", "backups", "example-prod-01"])

    captured = capsys.readouterr()
    assert code == 1
    assert "must-not-print" not in captured.out + captured.err


def test_public_restore_and_confirmed_promotion_use_narrow_remote_payloads(monkeypatch, capsys):
    config = load(FIXTURE)
    instance = config.select("redis/cache-dev-01")
    restore_id = "restore-20260725T120000000000Z-aaaaaaaaaaaa"
    manifest_hash = "a" * 64
    plan = {
        "version": 1,
        "host": config.host.id,
        "selector": instance.selector,
        "restore_id": restore_id,
        "snapshot": "snapshot-exact",
        "snapshot_time": "2026-07-25T11:00:00+00:00",
        "manifest_hash": manifest_hash,
        "created_at": "2026-07-25T12:00:00+00:00",
        "engine_image": instance.image,
        "active_release": "release-active",
        "candidate_path": str(instance.data.parent / f".{restore_id}.candidate"),
        "live_path": str(instance.data),
        "outage": True,
    }
    calls = []

    def call(current, operation, selector, payload=None, **kwargs):
        calls.append((operation, selector, payload, kwargs))
        if operation == "restore":
            return {
                "restore_id": restore_id,
                "selector": selector,
                "snapshot": "snapshot-exact",
                "created_at": plan["created_at"],
                "state": "verified",
                "promotable": True,
            }
        if operation == "promotion_plan":
            return plan
        return {
            "selector": selector,
            "restore_id": restore_id,
            "snapshot": "snapshot-exact",
            "release": "release-active",
            "retained": str(instance.data.with_name("data.retained-test")),
        }

    monkeypatch.setattr(controller, "load", lambda path: config)
    monkeypatch.setattr(controller.remote, "call", call)
    assert (
        controller.main(["--config", "ignored", "restore", "cache-dev-01", "--snapshot", "latest"])
        == 0
    )

    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    assert controller.main(["--config", "ignored", "promote", "cache-dev-01", restore_id]) == 0
    assert [item[0] for item in calls] == ["restore", "promotion_plan"]

    assert (
        controller.main(["--config", "ignored", "promote", "cache-dev-01", restore_id, "--yes"])
        == 0
    )
    assert [item[0] for item in calls] == [
        "restore",
        "promotion_plan",
        "promotion_plan",
        "promote",
    ]
    assert calls[0][2] == {"snapshot": "latest"}
    assert calls[-1][2] == {
        "restore_id": restore_id,
        "expected_release": "release-active",
        "manifest_hash": manifest_hash,
    }
    output = capsys.readouterr().out
    assert "Expected outage" in output
    assert "Retained prior data" in output


def _status_result(config, manifest):
    current = datetime.now(timezone.utc).isoformat()
    return {
        "version": controller.status.VERSION,
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
        "infrastructure": {
            "network": {"exists": True},
            "traefik": {
                "running": True,
                "healthy": True,
                "image": manifest["infrastructure"]["traefik"]["image"],
                "service_hash": manifest["infrastructure"]["traefik"]["service_hash"],
            },
        },
        "databases": [
            {
                "selector": item.selector,
                "engine": item.engine,
                "durable": item.durable,
                "container": {
                    "state": "running",
                    "running": True,
                    "health": "healthy",
                    "image": item.image,
                    "image_id": "sha256:" + "1" * 64,
                    "service_hash": manifest["databases"][item.selector]["service_hash"],
                },
                "services": {
                    name: {
                        "running": True,
                        "healthy": True if service["health"] == "docker" else None,
                        "image": service["image"],
                        "service_hash": manifest["databases"][item.selector]["service_hash"],
                    }
                    for name, service in manifest["databases"][item.selector]["services"].items()
                },
                "engine_check": {"ok": True, "error": None},
                "retained": [],
                "backup": {"time": current} if item.durable else None,
                "upload": {"ok": True, "time": current, "snapshot": "snapshot-id"}
                if item.durable
                else None,
                "restore": {"ok": True, "time": current, "backup": "backup-id"}
                if item.durable
                else None,
                "errors": {},
                "error": None,
            }
            for item in config.instances
        ],
    }


def _empty_state():
    return {"release": None, "manifest": None, "infrastructure": {}, "live": {}}
