from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from .run import redact


def write(event: str, *, secrets: tuple[str, ...] = (), **fields: Any) -> None:
    data = {
        "time": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    line = json.dumps(data, sort_keys=True, default=str)
    print(redact(line, secrets), file=sys.stderr)
