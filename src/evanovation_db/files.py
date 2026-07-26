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
    if folder.is_symlink():
        raise OSError(f"managed directory must not be a symlink: {folder}")
    _create_dirs(folder)
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
    write_text(path, json.dumps(data, sort_keys=True, indent=2) + "\n", mode=mode)


def write_text(path: str | Path, text: str, *, mode: int = 0o600) -> None:
    write_bytes(path, text.encode(), mode=mode)


def write_bytes(path: str | Path, data: bytes, *, mode: int = 0o600) -> None:
    target = Path(path)
    _create_dirs(target.parent)
    if target.is_symlink():
        raise OSError(f"managed path must not be a symlink: {target}")
    owner = (target if target.exists() else target.parent).stat()
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temp = Path(temp_name)
    try:
        os.fchown(fd, owner.st_uid, owner.st_gid)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp.chmod(mode)
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def _create_dirs(folder: Path) -> None:
    missing = []
    current = folder
    while not current.exists():
        if current.is_symlink():
            raise OSError(f"managed directory must not be a symlink: {current}")
        missing.append(current)
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise OSError(f"managed directory is unsafe: {current}")
    for item in reversed(missing):
        owner = item.parent.stat()
        item.mkdir(mode=0o700)
        os.chown(item, owner.st_uid, owner.st_gid)


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
