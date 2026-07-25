from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .errors import BackupError


def private_dir(path: str | Path) -> Path:
    folder = Path(path)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)
    return folder


def hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_file(path: str | Path) -> Path:
    file_path = Path(path)
    if not file_path.is_file() or file_path.stat().st_size == 0:
        raise BackupError(f"file is missing or empty: {file_path}")
    file_path.chmod(0o600)
    return file_path


def write_json(path: str | Path, data: Any, *, mode: int = 0o600) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temp.chmod(mode)
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def write_text(path: str | Path, text: str, *, mode: int = 0o600) -> None:
    write_bytes(path, text.encode(), mode=mode)


def write_bytes(path: str | Path, data: bytes, *, mode: int = 0o600) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp.chmod(mode)
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def free_gb(path: str | Path) -> float:
    return shutil.disk_usage(Path(path)).free / (1024**3)


def require_space(path: str | Path, minimum_gb: float) -> None:
    available = free_gb(path)
    if available < minimum_gb:
        raise BackupError(f"not enough free space: {available:.1f} GiB available")


def finish(partial: str | Path) -> Path:
    source = Path(partial)
    if not source.name.endswith(".partial"):
        raise BackupError(f"not a partial folder: {source}")
    target = source.with_name(source.name.removesuffix(".partial"))
    if target.exists():
        raise BackupError(f"backup folder already exists: {target}")
    source.replace(target)
    return target
