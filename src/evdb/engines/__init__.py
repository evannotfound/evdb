"""Direct concrete engine lookup."""

from __future__ import annotations

from ..errors import ConfigError
from . import dragonfly, postgres, redis


def get(name: str):
    try:
        return {"postgres": postgres, "redis": redis, "dragonfly": dragonfly}[name]
    except KeyError as exc:
        raise ConfigError(f"unsupported database engine: {name}") from exc
