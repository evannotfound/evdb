import io
import json

import pytest

from evanovation_db import remote
from evanovation_db.config import ConfigError
from evanovation_db.errors import (
    CommandError,
    ProtocolError,
    ProtocolMismatchError,
    RuntimeUnavailableError,
)
from evanovation_db.run import Result


def test_call_uses_fixed_ssh_arguments_and_json_stdin(config, monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        response = {"version": remote.VERSION, "ok": True, "result": {"state": "running"}}
        return Result(tuple(args), 0, json.dumps(response), "")

    monkeypatch.setattr(remote, "run", fake_run)

    result = remote.call(config, "show", "postgres/test-dev-01", timeout=17)

    assert result == {"state": "running"}
    assert seen["args"] == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=17",
        config.host.ssh,
        "sudo",
        "-n",
        "/usr/bin/env",
        "PYTHONPATH=/opt/evanovation-db/current/src",
        "/usr/bin/python3",
        "-m",
        "evanovation_db.cli",
        "--config",
        "/opt/evanovation-db/current/runtime",
        "remote",
    ]
    assert seen["kwargs"]["timeout"] == 17
    assert seen["kwargs"]["check"] is False
    assert json.loads(seen["kwargs"]["input"]) == {
        "version": remote.VERSION,
        "operation": "show",
        "selector": "postgres/test-dev-01",
        "payload": {},
    }


def test_call_uses_bootstrap_runtime_only_when_explicitly_selected(config, monkeypatch):
    seen = []

    def fake_run(args, **kwargs):
        seen.append(args)
        response = {"version": remote.VERSION, "ok": True, "result": {}}
        return Result(tuple(args), 0, json.dumps(response), "")

    monkeypatch.setattr(remote, "run", fake_run)

    remote.call(config, "release_state", config.host.id, runtime="bootstrap")

    assert "PYTHONPATH=/opt/evanovation-db/host-runtime/src" in seen[0]
    assert "PYTHONPATH=/opt/evanovation-db/current/src" not in seen[0]
    assert "/opt/evanovation-db/host-runtime/runtime" in seen[0]
    assert "/etc/evanovation-db" not in seen[0]


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (Result(("ssh",), 255, "", "connection refused"), "transport failed"),
        (Result(("ssh",), 0, "not-json", ""), "invalid remote response"),
        (
            Result(
                ("ssh",),
                1,
                json.dumps(
                    {
                        "version": remote.VERSION,
                        "ok": False,
                        "error": {"code": "invalid_selector", "message": "unknown database"},
                    }
                ),
                "",
            ),
            "remote invalid_selector",
        ),
        (
            Result(
                ("ssh",),
                0,
                json.dumps({"version": remote.VERSION + 1, "ok": True, "result": {}}),
                "",
            ),
            "install matching",
        ),
    ],
)
def test_call_rejects_transport_and_protocol_failures(config, monkeypatch, result, message):
    monkeypatch.setattr(remote, "run", lambda args, **kwargs: result)

    with pytest.raises(ProtocolError, match=message):
        remote.call(config, "show", "postgres/test-dev-01")


def test_call_propagates_bounded_timeout(config, monkeypatch):
    def fake_run(args, **kwargs):
        raise CommandError("command timed out after 1s")

    monkeypatch.setattr(remote, "run", fake_run)

    with pytest.raises(CommandError, match="timed out"):
        remote.call(config, "show", "postgres/test-dev-01", timeout=1)


def test_call_classifies_only_explicit_bootstrap_conditions(config, monkeypatch):
    monkeypatch.setattr(
        remote,
        "run",
        lambda *args, **kwargs: Result(
            ("ssh",),
            1,
            "",
            "/usr/bin/python3: No module named evanovation_db",
        ),
    )
    with pytest.raises(RuntimeUnavailableError):
        remote.call(config, "release_state", config.host.id)

    response = {
        "version": remote.VERSION,
        "ok": False,
        "error": {"code": "protocol_mismatch", "message": "install matching releases"},
    }
    monkeypatch.setattr(
        remote,
        "run",
        lambda *args, **kwargs: Result(("ssh",), 1, json.dumps(response), ""),
    )
    with pytest.raises(ProtocolMismatchError):
        remote.call(config, "release_state", config.host.id)

    response["error"] = {"code": "operation_failed", "message": "malformed active config"}
    with pytest.raises(ProtocolError, match="operation_failed"):
        remote.call(config, "release_state", config.host.id)


def test_dispatch_validates_before_calling_handler(config):
    calls = []

    def handler(current, instance, payload):
        calls.append((current, instance, payload))
        return {"selector": instance.selector}

    request = json.dumps(
        {
            "version": remote.VERSION,
            "operation": "show",
            "selector": "postgres/test-dev-01",
            "payload": {},
        }
    )

    response = remote.dispatch(config, request, handlers={"show": handler})

    assert response == {
        "version": remote.VERSION,
        "ok": True,
        "result": {"selector": "postgres/test-dev-01"},
    }
    assert calls == [(config, config.select("postgres/test-dev-01"), {})]


def test_dispatch_distinguishes_operation_failure_from_invalid_selector(config):
    def handler(*args):
        raise ConfigError("container inspection returned invalid JSON")

    request = json.dumps(
        {
            "version": remote.VERSION,
            "operation": "show",
            "selector": "postgres/test-dev-01",
            "payload": {},
        }
    )

    response = remote.dispatch(config, request, handlers={"show": handler})

    assert response["error"] == {
        "code": "operation_failed",
        "message": "container inspection returned invalid JSON",
    }


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("not-json", "invalid_request"),
        (
            {
                "version": remote.VERSION + 1,
                "operation": "show",
                "selector": "test-dev-01",
                "payload": {},
            },
            "protocol_mismatch",
        ),
        (
            {
                "version": remote.VERSION,
                "operation": "delete",
                "selector": "test-dev-01",
                "payload": {},
            },
            "unknown_operation",
        ),
        (
            {"version": remote.VERSION, "operation": "show", "selector": "", "payload": {}},
            "invalid_request",
        ),
        (
            {
                "version": remote.VERSION,
                "operation": "show",
                "selector": "test-dev-01",
                "payload": {"x": 1},
            },
            "invalid_request",
        ),
        (
            {
                "version": remote.VERSION,
                "operation": "show",
                "selector": "missing-prod-01",
                "payload": {},
            },
            "invalid_selector",
        ),
    ],
)
def test_dispatch_rejects_malformed_requests_without_calling(config, raw, code):
    called = False

    def handler(*args):
        nonlocal called
        called = True
        return {}

    text = raw if isinstance(raw, str) else json.dumps(raw)
    response = remote.dispatch(config, text, handlers={"show": handler})

    assert response["ok"] is False
    assert response["error"]["code"] == code
    assert called is False


def test_protocol_mismatch_has_upgrade_guidance(config):
    request = json.dumps(
        {"version": 99, "operation": "show", "selector": "test-dev-01", "payload": {}}
    )

    response = remote.dispatch(config, request)

    assert response["error"]["code"] == "protocol_mismatch"
    assert "install matching" in response["error"]["message"]


def test_serve_returns_only_structured_json_for_malformed_input(config, monkeypatch, tmp_path):
    (tmp_path / "host.json").write_text("{}")
    monkeypatch.setattr(remote, "load", lambda path: config)
    output = io.StringIO()

    code = remote.serve(tmp_path, input_stream=io.StringIO("bad"), output_stream=output)

    assert code == 1
    assert json.loads(output.getvalue())["error"]["code"] == "invalid_request"


def test_serve_distinguishes_missing_from_invalid_runtime_config(config, monkeypatch, tmp_path):
    output = io.StringIO()
    assert remote.serve(tmp_path, input_stream=io.StringIO("{}"), output_stream=output) == 1
    assert json.loads(output.getvalue())["error"]["code"] == "runtime_unavailable"

    (tmp_path / "host.json").write_text("broken")
    monkeypatch.setattr(
        remote,
        "load",
        lambda path: (_ for _ in ()).throw(ConfigError("invalid runtime host.json")),
    )
    output = io.StringIO()
    assert remote.serve(tmp_path, input_stream=io.StringIO("{}"), output_stream=output) == 1
    assert json.loads(output.getvalue())["error"]["code"] == "runtime_error"


def test_host_and_lifecycle_operations_have_deliberate_payload_schemas(config):
    calls = []

    def state(current, instance, payload):
        calls.append((current, instance, payload))
        return {"release": None, "manifest": None, "live": {}}

    request = json.dumps(
        {
            "version": remote.VERSION,
            "operation": "release_state",
            "selector": config.host.id,
            "payload": {},
        }
    )
    response = remote.dispatch(config, request, handlers={"release_state": state})

    assert response["ok"] is True
    assert calls == [(config, None, {})]

    invalid = [
        ("release_state", config.host.id, {"write": True}),
        ("start", "postgres/test-dev-01", {"force": True}),
        ("logs", "postgres/test-dev-01", {}),
        ("logs", "postgres/test-dev-01", {"lines": 1001}),
        ("apply", config.host.id, {}),
    ]
    for operation, selector, payload in invalid:
        request = json.dumps(
            {
                "version": remote.VERSION,
                "operation": operation,
                "selector": selector,
                "payload": payload,
            }
        )
        assert remote.dispatch(config, request)["error"]["code"] == "invalid_request"


def test_host_operation_rejects_database_selector(config):
    request = json.dumps(
        {
            "version": remote.VERSION,
            "operation": "release_state",
            "selector": "postgres/test-dev-01",
            "payload": {},
        }
    )

    response = remote.dispatch(config, request)

    assert response["error"]["code"] == "invalid_selector"


def test_status_and_backup_operations_have_closed_payload_schemas(config):
    cases = [
        ("status", config.host.id, {}, True),
        ("status", config.host.id, {"database": "test-dev-01"}, False),
        ("backup", "postgres/test-dev-01", {}, True),
        ("backup", "postgres/test-dev-01", {"upload": False}, False),
        ("backups", "postgres/test-dev-01", {}, True),
        ("backups", "postgres/test-dev-01", {"limit": 1}, False),
        ("backup_check", "postgres/test-dev-01", {"backup": None}, True),
        ("backup_check", "postgres/test-dev-01", {"backup": "snapshot-id"}, True),
        ("backup_check", "postgres/test-dev-01", {}, False),
        ("backup_check", "postgres/test-dev-01", {"backup": "../snapshot"}, False),
        ("backup_check", "postgres/test-dev-01", {"backup": "x", "extra": True}, False),
        ("restore", "postgres/test-dev-01", {"snapshot": "latest"}, True),
        ("restore", "postgres/test-dev-01", {"snapshot": "snapshot-id"}, True),
        ("restore", "postgres/test-dev-01", {"snapshot": "../snapshot"}, False),
        (
            "promotion_plan",
            "postgres/test-dev-01",
            {"restore_id": "restore-20260725T120000000000Z-aaaaaaaaaaaa"},
            True,
        ),
        ("promotion_plan", "postgres/test-dev-01", {"restore_id": "../candidate"}, False),
        (
            "promote",
            "postgres/test-dev-01",
            {
                "restore_id": "restore-20260725T120000000000Z-aaaaaaaaaaaa",
                "expected_release": "release-active",
                "manifest_hash": "a" * 64,
            },
            True,
        ),
        (
            "promote",
            "postgres/test-dev-01",
            {
                "restore_id": "restore-20260725T120000000000Z-aaaaaaaaaaaa",
                "expected_release": "release-active",
                "manifest_hash": "a" * 64,
                "force": True,
            },
            False,
        ),
    ]
    for operation, selector, payload, valid in cases:
        called = []

        def handler(current, instance, body, calls=called):
            calls.append((current, instance, body))
            return {"accepted": True}

        request = json.dumps(
            {
                "version": remote.VERSION,
                "operation": operation,
                "selector": selector,
                "payload": payload,
            }
        )
        response = remote.dispatch(config, request, handlers={operation: handler})
        assert response["ok"] is valid
        assert bool(called) is valid


def test_release_operations_have_closed_payload_schemas(config):
    cases = [
        ("releases", {}, True),
        ("releases", {"limit": 1}, False),
        ("rollback_plan", {"release": None}, True),
        ("rollback_plan", {"release": "release-prior"}, True),
        ("rollback_plan", {}, False),
        ("rollback_plan", {"release": "../prior"}, False),
        (
            "rollback",
            {"release": "release-prior", "expected": "release-current"},
            True,
        ),
        ("rollback", {"release": "release-prior"}, False),
        (
            "rollback",
            {"release": "release-prior", "expected": "release-current", "force": True},
            False,
        ),
    ]
    for operation, payload, valid in cases:
        called = []

        def handler(current, instance, body, calls=called):
            calls.append((current, instance, body))
            return {"accepted": True}

        request = json.dumps(
            {
                "version": remote.VERSION,
                "operation": operation,
                "selector": config.host.id,
                "payload": payload,
            }
        )
        response = remote.dispatch(config, request, handlers={operation: handler})
        assert response["ok"] is valid
        assert bool(called) is valid


def test_dispatch_rejects_protected_response_fields_without_echoing_value(config):
    protected = "must-not-cross-remote-protocol"
    request = json.dumps(
        {
            "version": remote.VERSION,
            "operation": "show",
            "selector": "postgres/test-dev-01",
            "payload": {},
        }
    )

    response = remote.dispatch(
        config,
        request,
        handlers={"show": lambda *args: {"password": protected}},
    )

    assert response["ok"] is False
    assert response["error"] == {
        "code": "operation_failed",
        "message": "remote operation returned protected data",
    }
    assert protected not in json.dumps(response)


def test_backup_check_handler_uses_local_or_exact_remote_source(config, monkeypatch, tmp_path):
    instance = config.get("postgres", "test-dev-01")
    config = type(config)(
        type(config.host)(**{**vars(config.host), "backup_dir": tmp_path}),
        config.instances,
    )
    checks = []

    monkeypatch.setattr(
        remote,
        "select_backup",
        lambda current, selected, value: {
            "backup": "local-id" if value == "local-id" else "remote-id",
            "snapshot": None if value == "local-id" else "snapshot-id",
            "time": "2026-07-25T10:00:00+00:00",
            "local": value == "local-id",
            "remote": value != "local-id",
        },
    )

    def check(current, selected, folder=None, *, snapshot=None):
        checks.append((folder, snapshot))
        return {"ok": True}

    monkeypatch.setattr(remote, "restore_check", check)

    local_result = remote._backup_check(config, instance, {"backup": "local-id"})
    remote_result = remote._backup_check(config, instance, {"backup": "snapshot-id"})

    assert checks == [(tmp_path / "postgres/test-dev-01/local-id", None), (None, "snapshot-id")]
    assert local_result["backup"] == "local-id"
    assert remote_result["snapshot"] == "snapshot-id"


def test_restore_handlers_delegate_only_validated_candidate_operations(config, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    calls = []
    monkeypatch.setattr(
        remote.restore_promotion,
        "create",
        lambda current, selected, snapshot: calls.append(("restore", snapshot)) or {"ok": True},
    )
    monkeypatch.setattr(
        remote.restore_promotion,
        "plan",
        lambda current, selected, restore_id: calls.append(("plan", restore_id)) or {"ok": True},
    )
    monkeypatch.setattr(
        remote.restore_promotion,
        "promote",
        lambda current, selected, payload: calls.append(("promote", payload)) or {"ok": True},
    )
    restore_id = "restore-20260725T120000000000Z-aaaaaaaaaaaa"
    payload = {
        "restore_id": restore_id,
        "expected_release": "release-active",
        "manifest_hash": "a" * 64,
    }

    assert remote._restore(config, instance, {"snapshot": "latest"}) == {"ok": True}
    assert remote._promotion_plan(config, instance, {"restore_id": restore_id}) == {"ok": True}
    assert remote._promote(config, instance, payload) == {"ok": True}
    assert calls == [("restore", "latest"), ("plan", restore_id), ("promote", payload)]
