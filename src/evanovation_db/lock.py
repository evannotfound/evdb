from __future__ import annotations

import fcntl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import LockError

if TYPE_CHECKING:
    from .config import Host, Instance


@contextmanager
def lock(path: str | Path, *, timeout: float = 0, shared: bool = False) -> Iterator[None]:
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = lock_path.open("a+")
    deadline = time.monotonic() + timeout
    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    try:
        while True:
            try:
                fcntl.flock(handle, mode | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise LockError(f"lock is busy: {lock_path}") from exc
                time.sleep(0.1)
        if not shared:
            handle.seek(0)
            handle.truncate()
            handle.write(str(time.time()))
            handle.flush()
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


@contextmanager
def operation(
    host: Host,
    instance: Instance | None = None,
    *,
    write: bool = False,
    timeout: float = 0,
) -> Iterator[None]:
    with lock(host.lock_dir / "host.lock", timeout=timeout, shared=not write):
        if instance is None:
            yield
            return
        path = host.lock_dir / f"{instance.group}-{instance.id}.lock"
        with lock(path, timeout=timeout):
            yield
