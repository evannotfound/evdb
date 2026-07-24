from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import BackupError
from .files import hash as file_hash
from .files import read_json, require_file, write_json

NAME = "backup.json"


def write(folder: str | Path, data: dict[str, Any]) -> Path:
    path = Path(folder) / NAME
    write_json(path, data)
    return path


def read(folder: str | Path) -> dict[str, Any]:
    path = Path(folder) / NAME
    if not path.is_file():
        raise BackupError(f"missing {NAME}: {folder}")
    data = read_json(path)
    if not isinstance(data, dict):
        raise BackupError(f"invalid {NAME}: {folder}")
    return data


def files(folder: str | Path, names: list[str]) -> list[dict[str, Any]]:
    root = Path(folder)
    result = []
    for name in names:
        path = require_file(root / name)
        result.append({"name": name, "size": path.stat().st_size, "sha256": file_hash(path)})
    return result


def check(folder: str | Path) -> dict[str, Any]:
    root = Path(folder)
    data = read(root)
    if data.get("status") != "complete":
        raise BackupError(f"backup is not complete: {root}")
    for item in data.get("files", []):
        path = require_file(root / item["name"])
        if path.stat().st_size != item["size"] or file_hash(path) != item["sha256"]:
            raise BackupError(f"backup file changed: {item['name']}")
    return data
