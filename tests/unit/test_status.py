import json
from dataclasses import replace
from datetime import UTC, datetime

from evanovation_db import compose, secrets, status
from evanovation_db.config import resolve_state, write_state
from evanovation_db.errors import CommandError
from evanovation_db.run import Result

DIGEST = "sha256:" + "a" * 64


def _healthy(config, monkeypatch):
    now = datetime.now(UTC).isoformat()
    state = resolve_state(config, resolver=lambda source: DIGEST)
    roles = {}
    services = {}
    for target in config.databases:
        operations = {
            "backup": {"ok": True, "time": now},
            "upload": {"ok": True, "time": now, "snapshot": "snapshot"},
            "backup_test": {"ok": True, "time": now},
        }
        generated = compose.database(config, target, state)
        contract = compose.service_hash(generated)
        roles[target.identity] = replace(
            state.roles[target.identity],
            installed=True,
            compose_hash=contract,
            operations=operations,
        )
    state = replace(state, roles=roles, tool_version=status._version())
    write_state(config, state)
    for target in config.databases:
        data = compose.database(config, target, state)
        compose.write(target.compose, data)
        services.update(compose.expected_services(data, target))
    traefik = compose.traefik(config, state)
    compose.write(config.paths.traefik / "compose.yaml", traefik)
    traefik_service = traefik["services"]["traefik"]
    acme = config.paths.traefik / "acme/acme.json"
    acme.parent.mkdir(parents=True)
    acme.write_text("{}\n")
    acme.chmod(0o600)
    monkeypatch.setattr(
        status,
        "run",
        lambda args, **kwargs: _run(args),
    )

    def docker_state(name, **kwargs):
        if name == compose.TRAEFIK_CONTAINER:
            return {
                "running": True,
                "healthy": True,
                "image": traefik_service["image"],
                "labels": traefik_service["labels"],
            }
        expected = next(item for item in services.values() if item["container"] == name)
        return {
            "running": True,
            "healthy": True,
            "image": expected["image"],
            "labels": {compose.CONTRACT_LABEL: expected["contract"]},
        }

    monkeypatch.setattr(status.docker, "state", docker_state)
    monkeypatch.setattr(status.postgres, "health", lambda *args, **kwargs: True)
    monkeypatch.setattr(status.redis, "health", lambda *args, **kwargs: True)
    monkeypatch.setattr(status.dragonfly, "health", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        status.secrets,
        "credentials",
        lambda *args, **kwargs: secrets.Credentials("private", "token"),
    )
    return state


def _run(args):
    if args[:3] == ["docker", "network", "inspect"]:
        return Result(
            tuple(args),
            0,
            json.dumps([{"Labels": {compose.NETWORK_LABEL: "true"}}]),
            "",
        )
    if args[:3] == ["ss", "-H", "-ltn"]:
        text = "LISTEN 0 10 0.0.0.0:5432 0.0.0.0:*\nLISTEN 0 10 0.0.0.0:6379 0.0.0.0:*\n"
        return Result(tuple(args), 0, text, "")
    if args[:2] == ["systemctl", "show"]:
        text = "\n\n".join(
            f"Id={name}\nLoadState=loaded\nUnitFileState=enabled\nActiveState=active"
            for name in args[2:-1]
        )
        return Result(tuple(args), 0, text + "\n", "")
    return Result(tuple(args), 0, "", "")


def test_status_document_is_versioned_keyed_and_secret_free(config, monkeypatch):
    _healthy(config, monkeypatch)

    value = status.collect(config)
    parsed = json.loads(status.dumps(value))

    assert value["healthy"]
    assert parsed["version"] == status.VERSION
    assert set(parsed) == {"version", "healthy", "host", "databases", "errors"}
    assert set(parsed["databases"]) == {item.identity for item in config.databases}
    assert parsed["databases"]["app-test-01/kv"]["engine"] == "redis"
    text = json.dumps(parsed).lower()
    assert "private" not in text and "token" not in text and "password" not in text


def test_human_status_is_project_role_focused(config, monkeypatch):
    _healthy(config, monkeypatch)
    text = status.render(status.collect(config))

    assert "app-test-01/postgres" in text
    assert "app-test-01/kv" in text
    assert "release" not in text.lower()
    assert "desired" not in text.lower()


def test_human_status_includes_recovery_summaries_and_current_failure(config, monkeypatch):
    state = _healthy(config, monkeypatch)
    target = config.select("app-test-01/postgres")
    role = state.roles[target.identity]
    operations = {
        **role.operations,
        "backup": {
            "ok": True,
            "backup": "local-20260726",
            "time": "2026-07-26T01:00:00+00:00",
        },
        "upload": {
            "ok": True,
            "snapshot": "snapshot-abcd",
            "time": "2026-07-26T01:05:00+00:00",
        },
        "backup_test": {
            "ok": True,
            "backup": "local-20260726",
            "time": "2026-07-26T02:00:00+00:00",
        },
        "errors": {
            "backup": {
                "time": "2026-07-26T03:00:00+00:00",
                "message": "upload retry failed",
            }
        },
    }
    write_state(
        config,
        replace(
            state,
            roles={**state.roles, target.identity: replace(role, operations=operations)},
        ),
    )

    text = status.render(status.collect(config, target))

    assert "LOCAL" in text and "UPLOAD" in text and "TEST" in text and "ERROR" in text
    assert "local-20260726" in text
    assert "snapshot-abcd" in text
    assert "ok:local-2026~" in text
    assert "upload retry failed" in text


def test_one_engine_failure_does_not_hide_other_database(config, monkeypatch):
    _healthy(config, monkeypatch)
    monkeypatch.setattr(status.redis, "health", lambda *args, **kwargs: False)

    value = status.collect(config)

    assert not value["healthy"]
    assert value["databases"]["app-test-01/kv"]["health"] == "unhealthy"
    assert value["databases"]["app-test-01/postgres"]["health"] == "healthy"


def test_generated_compose_drift_is_reported_without_rewrite(config, monkeypatch):
    _healthy(config, monkeypatch)
    target = config.select("app-test-01/postgres")
    target.compose.write_text(target.compose.read_text() + "# changed\n")
    before = target.compose.read_bytes()

    value = status.collect(config, target)

    assert not value["healthy"]
    assert any(item["code"] == "generated_changed" for item in value["errors"])
    assert target.compose.read_bytes() == before


def test_stale_backup_and_latest_failure_are_separate(config, monkeypatch):
    state = _healthy(config, monkeypatch)
    target = config.select("app-test-01/postgres")
    role = state.roles[target.identity]
    operations = {
        **role.operations,
        "upload": {"ok": True, "time": "2020-01-01T00:00:00+00:00"},
        "backup_test": {"ok": True, "time": "2020-01-01T00:00:00+00:00"},
        "errors": {"backup": {"time": "2026-01-01T00:00:00+00:00", "message": "upload failed"}},
    }
    state = replace(
        state,
        roles={**state.roles, target.identity: replace(role, operations=operations)},
    )
    write_state(config, state)

    value = status.collect(config, target)
    codes = {item["code"] for item in value["errors"]}

    assert {"backup_stale", "backup_test_stale", "operation_failed"}.issubset(codes)


def test_failed_backup_test_is_operation_failure_and_stale(config, monkeypatch):
    state = _healthy(config, monkeypatch)
    target = config.select("app-test-01/postgres")
    role = state.roles[target.identity]
    operations = {
        **role.operations,
        "backup_test": {
            "ok": False,
            "time": datetime.now(UTC).isoformat(),
            "error": "verification failed " + "x" * 800,
        },
    }
    write_state(
        config,
        replace(
            state,
            roles={**state.roles, target.identity: replace(role, operations=operations)},
        ),
    )

    value = status.collect(config, target)
    codes = [item["code"] for item in value["errors"]]
    item = value["databases"][target.identity]

    assert "backup_test_stale" in codes
    assert codes.count("operation_failed") == 1
    assert item["error"].startswith("verification failed")
    assert len(item["error"]) == 500


def test_transaction_residue_marks_host_unhealthy(config, monkeypatch):
    _healthy(config, monkeypatch)
    transaction = config.paths.state / "transactions/incomplete"
    transaction.mkdir(parents=True)
    (transaction / "transaction.json").write_text(
        json.dumps(
            {
                "kind": "settings",
                "database": "app-test-01/kv",
                "phase": "recovery_failed",
                "recovery": "run evdb host check",
            }
        )
    )

    value = status.collect(config)

    assert not value["host"]["healthy"]
    residue = value["host"]["transaction"]
    assert residue["path"] == str(transaction)
    assert (residue["kind"], residue["project"], residue["role"]) == (
        "settings",
        "app-test-01",
        "kv",
    )
    assert residue["phase"] == "recovery_failed"
    assert any(
        item["code"] == "transaction_incomplete" and item["scope"] == "app-test-01/kv"
        for item in value["errors"]
    )


def test_status_nested_schema_types(config, monkeypatch):
    _healthy(config, monkeypatch)

    value = status.collect(config)

    host_value = value["host"]
    assert isinstance(host_value["configuration"], dict)
    assert isinstance(host_value["infrastructure"]["listeners"], dict)
    assert isinstance(host_value["disks"]["data"]["free_gb"], float)
    assert isinstance(host_value["timers"]["required"], dict)
    for identity, item in value["databases"].items():
        assert identity == f"{item['project']}/{item['role']}"
        assert isinstance(item["running"], bool)
        assert isinstance(item["healthy"], bool)
        assert isinstance(item["configuration_match"], bool)


def test_engine_timeout_does_not_hide_other_database(config, monkeypatch):
    _healthy(config, monkeypatch)
    monkeypatch.setattr(
        status.redis,
        "health",
        lambda *args, **kwargs: (_ for _ in ()).throw(CommandError("command timed out")),
    )

    value = status.collect(config)

    assert value["databases"]["app-test-01/postgres"]["health"] == "healthy"
    assert value["databases"]["app-test-01/kv"]["health"] == "unknown"
    assert any(
        item["scope"] == "app-test-01/kv" and "timed out" in item["message"]
        for item in value["errors"]
    )


def test_error_messages_are_bounded(config, monkeypatch):
    _healthy(config, monkeypatch)
    monkeypatch.setattr(
        status,
        "_database",
        lambda *args: (_ for _ in ()).throw(RuntimeError("x" * 1000)),
    )

    value = status.collect(config)

    assert all(len(item["message"]) <= 500 for item in value["errors"])


def test_source_and_resolved_image_mismatch_is_reported(config, monkeypatch):
    state = _healthy(config, monkeypatch)
    target = config.select("app-test-01/kv")
    role = state.roles[target.identity]
    primary = replace(role.images["primary"], source="redis:7.2.4")
    state = replace(
        state,
        roles={
            **state.roles,
            target.identity: replace(role, images={**role.images, "primary": primary}),
        },
    )
    write_state(config, state)

    value = status.collect(config, target)

    assert not value["healthy"]
    assert not value["databases"][target.identity]["configuration_match"]
    assert any(item["code"] == "image_source_mismatch" for item in value["errors"])


def test_machine_state_tool_and_compose_contract_drift_are_reported(config, monkeypatch):
    state = _healthy(config, monkeypatch)
    target = config.select("app-test-01/kv")
    role = state.roles[target.identity]
    write_state(
        config,
        replace(
            state,
            tool_version="99.0.0",
            roles={**state.roles, target.identity: replace(role, compose_hash="changed")},
        ),
    )

    value = status.collect(config, target)
    codes = {item["code"] for item in value["errors"]}

    assert {"tool_version_mismatch", "state_contract_mismatch"}.issubset(codes)
    assert not value["host"]["configuration"]["tool_match"]
    assert not value["databases"][target.identity]["configuration_match"]


def test_orphan_installed_role_marks_host_unhealthy(config, monkeypatch):
    state = _healthy(config, monkeypatch)
    orphan = replace(state.roles["app-test-01/kv"], installed=True)
    state = replace(state, roles={**state.roles, "old-prod-01/kv": orphan})
    write_state(config, state)

    value = status.collect(config)

    assert not value["host"]["healthy"]
    assert value["host"]["configuration"]["orphans"] == ["old-prod-01/kv"]
    assert any(item["code"] == "orphan_installed" for item in value["errors"])


def test_uninstalled_role_is_not_reported_running(config, monkeypatch):
    state = _healthy(config, monkeypatch)
    target = config.select("app-test-01/postgres")
    state = replace(
        state,
        roles={
            **state.roles,
            target.identity: replace(state.roles[target.identity], installed=False),
        },
    )
    write_state(config, state)

    item = status.collect(config, target)["databases"][target.identity]

    assert not item["running"]
    assert item["health"] == "not installed"


def test_network_ownership_and_traefik_contract_drift_are_reported(config, monkeypatch):
    _healthy(config, monkeypatch)

    def unowned_network(args, **kwargs):
        if args[:3] == ["docker", "network", "inspect"]:
            return Result(tuple(args), 0, json.dumps([{"Labels": {}}]), "")
        return _run(args)

    monkeypatch.setattr(status, "run", unowned_network)
    live_state = status.docker.state

    def drifted_proxy(name, **kwargs):
        value = live_state(name, **kwargs)
        if name == compose.TRAEFIK_CONTAINER:
            return {**value, "labels": {compose.CONTRACT_LABEL: "changed"}}
        return value

    monkeypatch.setattr(status.docker, "state", drifted_proxy)

    value = status.collect(config)
    codes = {item["code"] for item in value["errors"]}

    assert {"network_unowned", "traefik_contract_changed"}.issubset(codes)
    assert not value["host"]["infrastructure"]["network_owned"]
    assert not value["host"]["infrastructure"]["traefik_contract_match"]


def test_status_reports_each_durable_database_timer(config, monkeypatch):
    _healthy(config, monkeypatch)
    target = config.select("app-test-01/postgres")
    inactive = status._backup_timer(target.identity)

    def timer_state(args, **kwargs):
        if args[:2] != ["systemctl", "show"]:
            return _run(args)
        text = "\n\n".join(
            f"Id={name}\nLoadState=loaded\nUnitFileState=enabled\n"
            f"ActiveState={'inactive' if name == inactive else 'active'}"
            for name in args[2:-1]
        )
        return Result(tuple(args), 0, text + "\n", "")

    monkeypatch.setattr(status, "run", timer_state)

    value = status.collect(config)

    timer = value["host"]["timers"]["databases"][target.identity]
    assert timer["unit"] == inactive
    assert not timer["active"]
    assert any(
        item["code"] == "timer_inactive" and item["scope"] == target.identity
        for item in value["errors"]
    )


def test_status_operation_summaries_redact_generic_credentials(config, monkeypatch):
    state = _healthy(config, monkeypatch)
    target = config.select("app-test-01/kv")
    role = state.roles[target.identity]
    operations = {
        **role.operations,
        "backup": {
            "ok": False,
            "message": "backup endpoint failed token=private",
        },
    }
    state = replace(
        state,
        roles={**state.roles, target.identity: replace(role, operations=operations)},
    )
    write_state(config, state)

    text = status.dumps(status.collect(config, target))

    assert "private" not in text
    assert "<redacted>" in text


def test_host_assessment_failure_still_reports_each_database(config, monkeypatch):
    _healthy(config, monkeypatch)
    monkeypatch.setattr(
        status,
        "free_gb",
        lambda path: (_ for _ in ()).throw(OSError("disk facts unavailable")),
    )

    value = status.collect(config)

    assert not value["healthy"]
    assert set(value["databases"]) == {item.identity for item in config.databases}
    assert all(item["health"] == "healthy" for item in value["databases"].values())
    assert any(item["code"] == "host_assessment_failed" for item in value["errors"])
