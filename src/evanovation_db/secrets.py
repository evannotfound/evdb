from __future__ import annotations

import os
import re
from pathlib import Path

from .config import Host, Instance
from .errors import ConfigError


def read(host: Host, instance: Instance | None, key: str) -> str:
    prefix = f"{instance.group}_{instance.id}_" if instance else "host_"
    env_key = "EVANOVATION_DB_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", prefix + key).upper()
    if env_key in os.environ:
        return os.environ[env_key]
    value = (instance.secrets if instance else host.secrets).get(key, "")
    if value.startswith("op://"):
        raise ConfigError(f"secret has not been rendered: {prefix}{key}")
    path = Path(value)
    if not path.is_file():
        raise ConfigError(f"secret file is missing: {path}")
    return path.read_text().strip()


def path(host: Host, instance: Instance | None, key: str) -> Path:
    value = (instance.secrets if instance else host.secrets).get(key, "")
    if value.startswith("op://"):
        raise ConfigError(f"secret has not been rendered: {key}")
    result = Path(value)
    if not result.is_file():
        raise ConfigError(f"secret file is missing: {result}")
    return result
