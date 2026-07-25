from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import Config, ConfigError, Instance
from .deployment import CONTRACT_LABEL
from .run import run

RELEASE_LINK = Path("/opt/evanovation-db/current")
VERSION_COMMANDS = {
    "postgres": ("postgres", "--version"),
    "redis": ("redis-server", "--version"),
    "dragonfly": ("dragonfly", "--version"),
}


def remote(config: Config, instance: Instance) -> dict[str, Any]:
    live = container(config, instance)
    return {
        "selector": instance.selector,
        "state": live["state"],
        "running": live["running"],
        "health": live["health"],
        "engine_version": engine_version(config, instance) if live["running"] else None,
        "image": live["image"],
        "image_id": live["image_id"],
        "service_hash": live["service_hash"],
        "active_release": _release(),
        "backup": _backup(config, instance),
    }


def container(config: Config, instance: Instance, *, timeout: int | None = None) -> dict[str, Any]:
    result = run(
        ["docker", "inspect", instance.container],
        timeout=timeout or config.host.timeouts["health"],
        check=False,
    )
    if result.code != 0:
        return {
            "state": "absent",
            "running": False,
            "health": "unknown",
            "image": None,
            "image_id": None,
            "service_hash": None,
        }
    try:
        data = json.loads(result.out)
    except json.JSONDecodeError as exc:
        raise ConfigError("container inspection returned invalid JSON") from exc
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ConfigError("container inspection returned invalid JSON")
    item = data[0]
    state = item.get("State") if isinstance(item.get("State"), dict) else {}
    status = state.get("Status") if isinstance(state.get("Status"), str) else "unknown"
    running = state.get("Running") is True
    health_data = state.get("Health") if isinstance(state.get("Health"), dict) else {}
    health = health_data.get("Status")
    if not isinstance(health, str) or not health:
        health = "running" if running else status
    container_config = item.get("Config") if isinstance(item.get("Config"), dict) else {}
    image = container_config.get("Image")
    labels = container_config.get("Labels")
    labels = labels if isinstance(labels, dict) else {}
    image_id = item.get("Image")
    return {
        "state": status,
        "running": running,
        "health": health,
        "image": image if isinstance(image, str) and image else None,
        "image_id": image_id if isinstance(image_id, str) and image_id else None,
        "service_hash": labels.get(CONTRACT_LABEL)
        if isinstance(labels.get(CONTRACT_LABEL), str)
        else None,
    }


def engine_version(config: Config, instance: Instance) -> str | None:
    result = run(
        ["docker", "exec", instance.container, *VERSION_COMMANDS[instance.engine]],
        timeout=config.host.timeouts["health"],
        check=False,
    )
    value = result.out.strip()
    return value if result.code == 0 and value else None


def _release() -> str | None:
    try:
        target = os.readlink(RELEASE_LINK)
    except OSError:
        return None
    name = Path(target).name
    return name or None


def _backup(config: Config, instance: Instance) -> dict[str, Any] | None:
    path = config.host.state_dir / "state" / instance.group / f"{instance.id}.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    backup = data.get("backup") if isinstance(data.get("backup"), dict) else {}
    upload = data.get("upload") if isinstance(data.get("upload"), dict) else {}
    if not upload:
        upload = backup.get("upload") if isinstance(backup.get("upload"), dict) else {}
    return {
        "finished": _optional_string(backup.get("finished")),
        "uploaded": _optional_string(upload.get("time")),
        "snapshot": _optional_string(upload.get("snapshot")),
        "upload_ok": upload.get("ok") is True,
    }


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
