from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, TextIO

from . import deployment, details, lifecycle, manifest, status
from .backup import backup as run_backup
from .backup import history as backup_history
from .backup import select_backup
from .config import Config, ConfigError, Instance, load
from .errors import (
    BackupError,
    Error,
    ProtocolError,
    ProtocolMismatchError,
    RuntimeUnavailableError,
)
from .restore import promotion as restore_promotion
from .restore import restore as restore_check
from .run import run

VERSION = 4
HOST_OPERATIONS = frozenset(
    {"release_state", "releases", "rollback_plan", "rollback", "apply", "status"}
)
DATABASE_OPERATIONS = frozenset(
    {
        "show",
        "start",
        "stop",
        "restart",
        "logs",
        "backup",
        "backups",
        "backup_check",
        "restore",
        "promotion_plan",
        "promote",
    }
)
OPERATIONS = HOST_OPERATIONS | DATABASE_OPERATIONS
_COMMAND = (
    "sudo",
    "-n",
    "/usr/bin/env",
    "PYTHONPATH={pythonpath}",
    "/usr/bin/python3",
    "-m",
    "evanovation_db.cli",
    "--config",
    "{config}",
    "remote",
)
RUNTIMES = {
    "current": (
        "/opt/evanovation-db/current/src",
        "/opt/evanovation-db/current/runtime",
    ),
    "bootstrap": (
        "/opt/evanovation-db/host-runtime/src",
        "/opt/evanovation-db/host-runtime/runtime",
    ),
}
_RELEASE = re.compile(r"release-[A-Za-z0-9][A-Za-z0-9.-]{0,126}")

Handler = Callable[[Config, Instance | None, dict[str, Any]], dict[str, Any]]


class _Invalid(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def call(
    config: Config,
    operation: str,
    selector: str,
    payload: Mapping[str, Any] | None = None,
    *,
    timeout: int | None = None,
    protected: tuple[str, ...] = (),
    runtime: Literal["current", "bootstrap"] = "current",
) -> dict[str, Any]:
    request = _request(operation, selector, payload)
    limit = timeout or config.host.timeouts["command"]
    connect_timeout = max(1, min(limit, 30))
    try:
        pythonpath, config_path = RUNTIMES[runtime]
    except KeyError as exc:
        raise ProtocolError(f"unsupported remote runtime: {runtime}") from exc
    command = tuple(item.format(pythonpath=pythonpath, config=config_path) for item in _COMMAND)
    args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        config.host.ssh,
        *command,
    ]
    result = run(
        args,
        input=_dump(request),
        timeout=limit,
        secrets=protected,
        check=False,
    )
    if not result.out.strip():
        detail = result.err.strip() or f"ssh exited with status {result.code}"
        if _runtime_missing(result.err):
            raise RuntimeUnavailableError(f"remote {runtime} runtime is unavailable")
        raise ProtocolError(f"remote transport failed: {detail}")
    try:
        response = _response(result.out)
    except _Invalid as exc:
        raise ProtocolError(f"invalid remote response: {exc}") from exc
    if response["version"] != VERSION:
        raise ProtocolMismatchError(_upgrade_message(response["version"]))
    if not response["ok"]:
        error = response["error"]
        if error["code"] == "runtime_unavailable":
            raise RuntimeUnavailableError(error["message"])
        if error["code"] == "protocol_mismatch":
            raise ProtocolMismatchError(error["message"])
        raise ProtocolError(f"remote {error['code']}: {error['message']}")
    if result.code != 0:
        raise ProtocolError(f"remote transport exited with status {result.code}")
    return response["result"]


def dispatch(
    config: Config,
    text: str,
    *,
    handlers: Mapping[str, Handler] | None = None,
) -> dict[str, Any]:
    try:
        request = _parse_request(text)
        operation = request["operation"]
        if operation in HOST_OPERATIONS:
            if request["selector"] != config.host.id:
                raise _Invalid("invalid_selector", "host selector does not match runtime host")
            selected = None
        else:
            try:
                selected = config.select(request["selector"])
            except ConfigError as exc:
                raise _Invalid("invalid_selector", str(exc)) from exc
        handler = (handlers or _handlers())[operation]
        result = handler(config, selected, request["payload"])
        if not isinstance(result, dict):
            raise _Invalid("operation_failed", "remote operation returned an invalid result")
        if not _secret_free(result):
            raise _Invalid("operation_failed", "remote operation returned protected data")
        return _success(result)
    except _Invalid as exc:
        return _failure(exc.code, str(exc))
    except Error as exc:
        return _failure("operation_failed", str(exc))
    except (KeyError, OSError, ValueError):
        return _failure("operation_failed", "remote operation failed")


def serve(
    config_path: str | Path,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    source = input_stream or sys.stdin
    target = output_stream or sys.stdout
    root = Path(config_path)
    if not (root / "host.json").is_file():
        response = _failure("runtime_unavailable", "host runtime configuration is unavailable")
    else:
        try:
            config = load(config_path)
            response = dispatch(config, source.read())
        except (Error, OSError, ValueError):
            response = _failure("runtime_error", "host runtime configuration is invalid")
    target.write(_dump(response))
    target.flush()
    return 0 if response["ok"] else 1


def _request(operation: str, selector: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if operation not in OPERATIONS:
        raise ProtocolError(f"unsupported remote operation: {operation}")
    if not isinstance(selector, str) or not selector:
        raise ProtocolError("remote selector must be a non-empty string")
    body = {} if payload is None else dict(payload)
    try:
        _validate_payload(operation, body)
    except Error as exc:
        raise ProtocolError(f"invalid remote {operation} payload: {exc}") from exc
    return {"version": VERSION, "operation": operation, "selector": selector, "payload": body}


def _parse_request(text: str) -> dict[str, Any]:
    data = _json_object(text, "request")
    if set(data) != {"version", "operation", "selector", "payload"}:
        raise _Invalid("invalid_request", "request fields are invalid")
    version = data["version"]
    if type(version) is not int or version != VERSION:
        raise _Invalid("protocol_mismatch", _upgrade_message(version))
    operation = data["operation"]
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise _Invalid("unknown_operation", "remote operation is not supported")
    selector = data["selector"]
    if not isinstance(selector, str) or not selector or selector.strip() != selector:
        raise _Invalid("invalid_request", "selector must be a non-empty string")
    payload = data["payload"]
    if not isinstance(payload, dict):
        raise _Invalid("invalid_request", "operation payload must be an object")
    try:
        _validate_payload(operation, payload)
    except Error as exc:
        raise _Invalid("invalid_request", str(exc)) from exc
    return data


def _response(text: str) -> dict[str, Any]:
    data = _json_object(text, "response")
    version = data.get("version")
    if type(version) is not int:
        raise _Invalid("invalid_response", "response version is invalid")
    ok = data.get("ok")
    if not isinstance(ok, bool):
        raise _Invalid("invalid_response", "response status is invalid")
    if ok:
        if set(data) != {"version", "ok", "result"} or not isinstance(data["result"], dict):
            raise _Invalid("invalid_response", "response result is invalid")
    elif set(data) != {"version", "ok", "error"}:
        raise _Invalid("invalid_response", "response error is invalid")
    else:
        error = data["error"]
        if (
            not isinstance(error, dict)
            or set(error) != {"code", "message"}
            or not isinstance(error["code"], str)
            or not isinstance(error["message"], str)
        ):
            raise _Invalid("invalid_response", "response error is invalid")
    return data


def _json_object(text: str, name: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise _Invalid(f"invalid_{name}", f"{name} is not valid JSON") from exc
    if not isinstance(data, dict):
        raise _Invalid(f"invalid_{name}", f"{name} must be a JSON object")
    return data


def _handlers() -> dict[str, Handler]:
    return {
        "show": _show,
        "status": _status,
        "release_state": _release_state,
        "releases": _releases,
        "rollback_plan": _rollback_plan,
        "rollback": _rollback,
        "apply": _apply,
        "start": _start,
        "stop": _stop,
        "restart": _restart,
        "logs": _logs,
        "backup": _backup,
        "backups": _backups,
        "backup_check": _backup_check,
        "restore": _restore,
        "promotion_plan": _promotion_plan,
        "promote": _promote,
    }


def _validate_payload(operation: str, payload: dict[str, Any]) -> None:
    if operation in {
        "show",
        "status",
        "release_state",
        "releases",
        "start",
        "stop",
        "restart",
        "backup",
        "backups",
    }:
        if payload:
            raise ProtocolError(f"remote operation {operation} requires an empty payload")
        return
    if operation == "logs":
        if set(payload) != {"lines"} or type(payload.get("lines")) is not int:
            raise ProtocolError("remote logs payload requires an integer lines field")
        if not 1 <= payload["lines"] <= 1000:
            raise ProtocolError("remote logs lines must be between 1 and 1000")
        return
    if operation == "apply":
        deployment.validate_request(payload)
        return
    if operation == "rollback_plan":
        if set(payload) != {"release"} or not _release_value(payload.get("release"), optional=True):
            raise ProtocolError("remote rollback_plan payload requires a valid release or null")
        return
    if operation == "rollback":
        deployment.validate_rollback_request(payload)
        return
    if operation == "backup_check":
        if set(payload) != {"backup"}:
            raise ProtocolError("remote backup_check payload requires a backup field")
        selected = payload["backup"]
        if selected is not None and (
            not isinstance(selected, str)
            or not selected
            or selected.strip() != selected
            or "/" in selected
            or len(selected) > 256
        ):
            raise ProtocolError("remote backup_check backup must be an exact backup id or null")
        return
    if operation == "restore":
        restore_promotion.validate_create_request(payload)
        return
    if operation == "promotion_plan":
        restore_promotion.validate_plan_request(payload)
        return
    if operation == "promote":
        restore_promotion.validate_request(payload)
        return
    raise ProtocolError(f"unsupported remote operation: {operation}")


def _show(config: Config, instance: Instance | None, payload: dict[str, Any]) -> dict[str, Any]:
    assert instance is not None
    return details.remote(config, instance)


def _status(config: Config, instance: Instance | None, payload: dict[str, Any]) -> dict[str, Any]:
    assert instance is None
    return status.remote(config)


def _release_state(
    config: Config, instance: Instance | None, payload: dict[str, Any]
) -> dict[str, Any]:
    assert instance is None
    return deployment.state(config)


def _releases(config: Config, instance: Instance | None, payload: dict[str, Any]) -> dict[str, Any]:
    assert instance is None
    return deployment.history(config)


def _rollback_plan(
    config: Config, instance: Instance | None, payload: dict[str, Any]
) -> dict[str, Any]:
    assert instance is None
    return deployment.rollback_plan(config, payload["release"])


def _rollback(config: Config, instance: Instance | None, payload: dict[str, Any]) -> dict[str, Any]:
    assert instance is None
    return deployment.rollback(config, payload)


def _apply(config: Config, instance: Instance | None, payload: dict[str, Any]) -> dict[str, Any]:
    assert instance is None
    return deployment.apply(config, payload)


def _start(config: Config, instance: Instance | None, payload: dict[str, Any]) -> dict[str, Any]:
    assert instance is not None
    return lifecycle.execute(config, instance, "start", payload)


def _stop(config: Config, instance: Instance | None, payload: dict[str, Any]) -> dict[str, Any]:
    assert instance is not None
    return lifecycle.execute(config, instance, "stop", payload)


def _restart(config: Config, instance: Instance | None, payload: dict[str, Any]) -> dict[str, Any]:
    assert instance is not None
    return lifecycle.execute(config, instance, "restart", payload)


def _logs(config: Config, instance: Instance | None, payload: dict[str, Any]) -> dict[str, Any]:
    assert instance is not None
    return lifecycle.execute(config, instance, "logs", payload)


def _backup(config: Config, instance: Instance | None, payload: dict[str, Any]) -> dict[str, Any]:
    assert instance is not None
    if not instance.durable:
        raise BackupError(f"backups are disabled for {instance.selector}")
    folder = run_backup(config, instance)
    data = manifest.read(folder)
    upload = data.get("upload") if isinstance(data.get("upload"), dict) else {}
    if (
        not isinstance(data.get("finished"), str)
        or not data["finished"]
        or upload.get("ok") is not True
        or not isinstance(upload.get("snapshot"), str)
        or not upload["snapshot"]
    ):
        raise BackupError("completed backup result is invalid")
    return {
        "selector": instance.selector,
        "backup": folder.name,
        "time": data.get("finished"),
        "snapshot": upload.get("snapshot"),
    }


def _backups(config: Config, instance: Instance | None, payload: dict[str, Any]) -> dict[str, Any]:
    assert instance is not None
    return {"selector": instance.selector, "backups": backup_history(config, instance)}


def _backup_check(
    config: Config, instance: Instance | None, payload: dict[str, Any]
) -> dict[str, Any]:
    assert instance is not None
    selected = select_backup(config, instance, payload["backup"])
    if selected["local"]:
        folder = config.host.backup_dir / instance.group / instance.id / selected["backup"]
        result = restore_check(config, instance, folder)
    else:
        result = restore_check(config, instance, snapshot=selected["snapshot"])
    return {
        "selector": instance.selector,
        "backup": selected["backup"],
        "snapshot": selected["snapshot"],
        "time": selected["time"],
        "result": result,
    }


def _restore(config: Config, instance: Instance | None, payload: dict[str, Any]) -> dict[str, Any]:
    assert instance is not None
    return restore_promotion.create(config, instance, payload["snapshot"])


def _promotion_plan(
    config: Config, instance: Instance | None, payload: dict[str, Any]
) -> dict[str, Any]:
    assert instance is not None
    return restore_promotion.plan(config, instance, payload["restore_id"])


def _promote(config: Config, instance: Instance | None, payload: dict[str, Any]) -> dict[str, Any]:
    assert instance is not None
    return restore_promotion.promote(config, instance, payload)


def _success(result: dict[str, Any]) -> dict[str, Any]:
    return {"version": VERSION, "ok": True, "result": result}


def _failure(code: str, message: str) -> dict[str, Any]:
    safe = message if "op://" not in message else "remote operation failed"
    return {"version": VERSION, "ok": False, "error": {"code": code, "message": safe}}


def _upgrade_message(version: Any) -> str:
    versions = f"received {version!r}, supported {VERSION}"
    return (
        f"controller and host protocol versions differ ({versions}); "
        "install matching evanovation-db releases on the controller and host"
    )


def _runtime_missing(stderr: str) -> bool:
    return any(
        marker in stderr
        for marker in (
            "No module named evanovation_db",
            "No module named 'evanovation_db'",
            "Error while finding module specification for 'evanovation_db.cli'",
        )
    )


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=True) + "\n"


def _release_value(value: Any, *, optional: bool) -> bool:
    if value is None:
        return optional
    return isinstance(value, str) and _RELEASE.fullmatch(value) is not None


def _secret_free(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if any(word in str(key).lower() for word in ("password", "token", "secret")):
                return False
            if not _secret_free(item):
                return False
    elif isinstance(value, list):
        return all(_secret_free(item) for item in value)
    elif isinstance(value, str):
        return "op://" not in value
    return True
