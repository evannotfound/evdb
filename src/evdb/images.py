from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from .errors import ConfigError
from .run import run

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def validate_source(image: Any, name: str = "image") -> str:
    if not isinstance(image, str) or not image or any(char.isspace() for char in image):
        raise ConfigError(f"{name} must use an explicit non-latest tag or sha256 digest")
    digest = source_digest(image, name)
    base = image.split("@", 1)[0]
    leaf = base.rsplit("/", 1)[-1]
    tag = leaf.rsplit(":", 1)[1] if ":" in leaf else None
    if tag is not None and tag.lower() == "latest":
        raise ConfigError(f"{name} must not use the latest tag")
    if digest is None and not tag:
        raise ConfigError(f"{name} must use an explicit non-latest tag or sha256 digest")
    return image


def source_digest(image: str, name: str = "image") -> str | None:
    if "@" not in image:
        return None
    if image.count("@") != 1:
        raise ConfigError(f"{name} must use a valid sha256 digest reference")
    base, digest = image.rsplit("@", 1)
    if not base or not DIGEST.fullmatch(digest):
        raise ConfigError(f"{name} must use a valid sha256 digest reference")
    return digest


def image_major(image: str) -> int | None:
    source = image.split("@", 1)[0]
    leaf = source.rsplit("/", 1)[-1]
    if ":" not in leaf:
        return None
    tag = leaf.rsplit(":", 1)[1]
    match = re.match(r"v?([0-9]+)(?:[._-]|$)", tag)
    if match is None:
        return None
    major = int(match.group(1))
    return major if major > 0 else None


def locked_image(source: str, digest: str) -> str:
    if not DIGEST.fullmatch(digest):
        raise ConfigError("invalid image digest")
    return f"{source.rsplit('@', 1)[0]}@{digest}"


def resolve(
    image: str,
    *,
    os_name: str = "linux",
    architecture: str = "amd64",
    timeout: int = 120,
) -> str:
    validate_source(image)
    digest = source_digest(image)
    if digest is not None:
        return digest
    result = run(["docker", "manifest", "inspect", "--verbose", image], timeout=timeout)
    try:
        data = json.loads(result.out)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"image {image}: docker returned invalid manifest JSON") from exc
    matches = []
    for item in _manifest_items(data):
        descriptor = item.get("Descriptor") if isinstance(item.get("Descriptor"), dict) else {}
        platform = item.get("Platform") or descriptor.get("platform") or item.get("platform")
        if not isinstance(platform, dict):
            continue
        if platform.get("os") != os_name or platform.get("architecture") != architecture:
            continue
        value = item.get("Digest") or descriptor.get("digest") or item.get("digest")
        if isinstance(value, str) and DIGEST.fullmatch(value):
            matches.append(value)
    unique = sorted(set(matches))
    if len(unique) != 1:
        raise ConfigError(
            f"image {image}: expected one {os_name}/{architecture} manifest, found {len(unique)}"
        )
    return unique[0]


def state(source: str, resolver: Callable[..., str] = resolve):
    from .config import ImageState

    digest = source_digest(source) or resolver(source)
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ConfigError(f"image {source}: resolver returned an invalid digest")
    return ImageState(source, digest)


def compatible(current: str, candidate: str) -> bool:
    current_major = image_major(current)
    candidate_major = image_major(candidate)
    return current_major is not None and current_major == candidate_major


def _manifest_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    manifests = data.get("manifests")
    if isinstance(manifests, list):
        return [item for item in manifests if isinstance(item, dict)]
    return [data]
