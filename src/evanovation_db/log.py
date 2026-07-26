from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from typing import Any

from .run import redact as redact_values

_URL = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^\s/@]+@")
_FIELD = re.compile(
    r"(?i)(\b[\w-]*(?:password|token|secret|credential|authorization|api[_-]?key)"
    r"[\w-]*\b\s*[\"']?\s*[:=]\s*)"
    r"(?:\"(?:\\.|[^\"])*\"|'[^']*'|[^\s,;]+)"
)
_AUTHORIZATION = re.compile(r"(?i)(\bauthorization\b\s*[\"']?\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+")
_REFERENCE = re.compile(r"(?i)op://[^\s\"']+")
_SECRET_FIELDS = ("password", "token", "secret", "credential", "authorization", "api_key")


def sanitize(text: str, secrets: tuple[str, ...] = ()) -> str:
    result = redact_values(text, secrets)
    result = _URL.sub(r"\1<redacted>@", result)
    result = _AUTHORIZATION.sub(r'\1"<redacted>"', result)
    result = _FIELD.sub(r'\1"<redacted>"', result)
    return _REFERENCE.sub("<redacted>", result)


def write(event: str, *, secrets: tuple[str, ...] = (), **fields: Any) -> None:
    data = {
        "time": datetime.now(UTC).isoformat(),
        "event": event,
        **fields,
    }
    line = json.dumps(_clean(data), sort_keys=True, default=str)
    print(redact_values(line, secrets), file=sys.stderr)


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "<redacted>"
                if any(name in str(key).lower() for name in _SECRET_FIELDS)
                else _clean(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, str):
        return sanitize(value)
    return value
