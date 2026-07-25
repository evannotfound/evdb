from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from .config import Config, Instance
from .deployment import active, compose_path, health
from .errors import CommandError, DeploymentError
from .lock import operation
from .run import run

_FIELD = re.compile(r"(?im)\b(password|token|secret)\b(\s*[\"']?\s*[:=]\s*[\"']?)[^\r\n]*")
_URL = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^\s:/@]+:)([^\s@]+)(@)")
_REFERENCE = re.compile(r"op://[^\s\"']+")


def execute(config: Config, instance: Instance, action: str, payload: dict[str, Any]) -> dict:
    if action not in {"start", "stop", "restart", "logs"}:
        raise CommandError(f"unsupported lifecycle action: {action}")
    current = active()
    if current is None:
        raise DeploymentError("no active release is available")
    release, manifest = current
    if manifest["host"] != config.host.id:
        raise DeploymentError("active release is for another host")
    path = compose_path(release, manifest, instance.selector)
    database = manifest["databases"][instance.selector]
    if not all(isinstance(database.get(name), str) for name in ("project", "container", "image")):
        raise DeploymentError("active release database identity is invalid")
    active_instance = replace(
        instance,
        project=database["project"],
        container=database["container"],
        image=database["image"],
    )
    command = [
        "docker",
        "compose",
        "-f",
        str(path),
        "--project-name",
        active_instance.project,
    ]
    timeout = config.host.timeouts["command"]
    with operation(config.host, active_instance, timeout=timeout):
        if action == "logs":
            lines = payload["lines"]
            result = run(
                [*command, "logs", "--no-color", "--tail", str(lines)],
                timeout=timeout,
                check=False,
            )
            if result.code != 0:
                raise CommandError(f"failed to read logs for {instance.selector}")
            return {"selector": active_instance.selector, "logs": redact_logs(result.out)}
        if action == "start":
            args = [*command, "up", "-d"]
        elif action == "stop":
            args = [*command, "stop"]
        else:
            args = [*command, "restart"]
        run(args, timeout=timeout)
        if action != "stop":
            health(
                config,
                active_instance,
                contract_hash=database["service_hash"],
                services=database["services"],
            )
        return {
            "selector": active_instance.selector,
            "action": action,
            "release": manifest["id"],
        }


def redact_logs(text: str) -> str:
    result = _URL.sub(r"\1<redacted>\3", text)
    result = _FIELD.sub(r"\1\2<redacted>", result)
    return _REFERENCE.sub("<redacted>", result)
