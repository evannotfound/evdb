from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from .errors import ConfigError

TRAEFIK_VERSION = "v3.7.8"
LEGO_VERSION = "v5.2.2"


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    source = files("evdb").joinpath("dns_providers.json")
    try:
        value = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError("DNS provider catalog is missing or invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("traefik") != TRAEFIK_VERSION
        or value.get("lego") != LEGO_VERSION
        or not isinstance(value.get("providers"), list)
        or not value["providers"]
    ):
        raise ConfigError("DNS provider catalog version is invalid")
    return value


def providers() -> tuple[dict[str, Any], ...]:
    return tuple(catalog()["providers"])


def normalize(code: str) -> str:
    aliases = catalog().get("aliases", {})
    canonical = aliases.get(code, code)
    if not any(item["code"] == canonical for item in providers()):
        raise ConfigError(f"unsupported DNS provider: {code}")
    return canonical


def provider(code: str) -> dict[str, Any]:
    canonical = normalize(code)
    return next(item for item in providers() if item["code"] == canonical)


def search(value: str) -> tuple[dict[str, Any], ...]:
    term = value.strip().casefold()
    return tuple(
        item
        for item in providers()
        if not term or term in item["code"].casefold() or term in item["name"].casefold()
    )


def validate_variables(code: str, values: dict[str, str]) -> None:
    selected = provider(code)
    allowed = set(selected["credentials"]) | set(selected["additional"])
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ConfigError(f"{code}: unsupported DNS variable {unknown[0]}")


def secret_variable(name: str) -> bool:
    visible = ("EMAIL", "REGION", "ZONE_ID", "PROFILE", "USERNAME", "USER", "PATH", "FILE")
    if any(word in name for word in visible):
        return False
    return any(word in name for word in ("KEY", "PASSWORD", "SECRET", "TOKEN", "CREDENTIAL"))


def require_versions(traefik_image: str) -> None:
    expected = f"traefik:{TRAEFIK_VERSION}"
    if traefik_image != expected or catalog()["traefik"] != TRAEFIK_VERSION:
        raise ConfigError(f"DNS provider catalog requires {expected} and lego {LEGO_VERSION}")
