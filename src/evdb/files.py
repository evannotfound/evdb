from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any

from .errors import BackupError


def private_dir(path: str | Path) -> Path:
    return managed_dir(path, 0o700)


def managed_dir(path: str | Path, mode: int | None) -> Path:
    folder = Path(path)
    _create_dirs(folder)
    descriptor = os.open(
        folder,
        (os.O_PATH if mode is None else os.O_RDONLY)
        | os.O_CLOEXEC
        | os.O_DIRECTORY
        | os.O_NOFOLLOW,
    )
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISDIR(details.st_mode):
            raise OSError(f"managed directory is unsafe: {folder}")
        if mode is not None:
            os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)
    return folder


def hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_file(path: str | Path, *, mode: int | None = 0o600) -> Path:
    file_path = Path(path)
    if not file_path.is_file() or file_path.stat().st_size == 0:
        raise BackupError(f"file is missing or empty: {file_path}")
    if mode is not None:
        file_path.chmod(mode)
    return file_path


def private_line(value: str | Path) -> str:
    path = Path(value)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        with os.fdopen(descriptor, encoding="utf-8") as source:
            details = os.fstat(source.fileno())
            if not stat.S_ISREG(details.st_mode) or details.st_mode & 0o077:
                raise ValueError("password file is not a private regular file")
            text = source.read()
    except (OSError, UnicodeError) as exc:
        raise ValueError("password file cannot be read") from exc
    if text.endswith("\n"):
        text = text[:-1]
    if not text or any(character in text for character in "\0\r\n"):
        raise ValueError("password must be one non-empty line")
    return text


def write_json(
    path: str | Path,
    data: Any,
    *,
    mode: int = 0o600,
    owner: tuple[int, int] | None = None,
) -> None:
    write_text(path, json.dumps(data, sort_keys=True, indent=2) + "\n", mode=mode, owner=owner)


def write_text(
    path: str | Path,
    text: str,
    *,
    mode: int = 0o600,
    owner: tuple[int, int] | None = None,
) -> None:
    write_bytes(path, text.encode(), mode=mode, owner=owner)


def write_bytes(
    path: str | Path,
    data: bytes,
    *,
    mode: int = 0o600,
    owner: tuple[int, int] | None = None,
) -> None:
    target = Path(path)
    _create_dirs(target.parent)
    if target.is_symlink():
        raise OSError(f"managed path must not be a symlink: {target}")
    current = (target if target.exists() else target.parent).lstat()
    uid, gid = owner or (current.st_uid, current.st_gid)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temp = Path(temp_name)
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def _create_dirs(folder: Path) -> None:
    for item in reversed((folder, *folder.parents)):
        try:
            details = item.lstat()
        except FileNotFoundError:
            owner = item.parent.lstat()
            if not stat.S_ISDIR(owner.st_mode):
                raise OSError(f"managed directory is unsafe: {item.parent}") from None
            item.mkdir(mode=0o700)
            os.chown(item, owner.st_uid, owner.st_gid)
            continue
        if not stat.S_ISDIR(details.st_mode):
            raise OSError(f"managed directory is unsafe: {item}")


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
