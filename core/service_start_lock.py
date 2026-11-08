"""Short-lived cross-process launch locks; the OS releases them after a crash."""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager, contextmanager

from core.config import get_iris_home


def _open_lock(service: str):
    if service not in {"iris", "hermes"}:
        raise ValueError("Unknown service")
    path = get_iris_home() / f".{service}-start.lock"
    handle = path.open("a+b")
    if path.stat().st_size == 0:
        handle.write(b"\0")
        handle.flush()
    return handle


def _try_lock(handle) -> bool:
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def service_start_lock(service: str, timeout: float = 15):
    with _open_lock(service) as handle:
        deadline = time.monotonic() + timeout
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{service} already has a launch in progress")
            time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(handle)


@asynccontextmanager
async def async_service_start_lock(service: str, timeout: float = 110):
    with _open_lock(service) as handle:
        deadline = time.monotonic() + timeout
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{service} already has a launch in progress")
            await asyncio.sleep(0.1)
        try:
            yield
        finally:
            _unlock(handle)
