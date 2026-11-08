"""Watch or read a parent's stdin pipe without leaving a read pending (A08.7).

On Windows a thread blocked in a synchronous ReadFile on a pipe serializes other
calls on that handle. A DLL whose runtime queries the standard handles while it
loads (GetFileInformationByHandle; numpy's OpenBLAS does) then waits forever,
and so does whatever imported it: the Desktop controller froze on its first
engine start. These helpers poll with PeekNamedPipe and only read bytes that are
already there. Elsewhere, or when the stream is not a pipe, they fall back to
ordinary blocking reads.

Standard library only: the Hermes workers import it as a sibling module.
"""

from __future__ import annotations

import os
import sys
import time


def _pipe(stream):
    """(fd, handle) of a Windows pipe; ``(None, None)`` otherwise; handle 0 when already closed."""
    if os.name != "nt":
        return None, None
    import _winapi
    import msvcrt

    try:
        fd = stream.fileno()
        handle = msvcrt.get_osfhandle(fd)
        _winapi.PeekNamedPipe(handle, 0)
        return fd, handle
    except BrokenPipeError:
        return fd, 0
    except (OSError, AttributeError, ValueError):
        return None, None  # a console or a file, e.g. during development


def _available(handle) -> int | None:
    """Bytes waiting in the pipe, or None once the writer has closed it."""
    import _winapi

    if not handle:
        return None
    try:
        return _winapi.PeekNamedPipe(handle, 0)[0]
    except OSError:
        return None


def wait_for_eof(stream=None, interval: float = 0.25) -> None:
    """Return once the parent closes its end (exit, crash or a deliberate close)."""
    stream = sys.stdin if stream is None else stream
    _fd, handle = _pipe(stream)
    if handle is None:
        stream.buffer.read()
        return
    while _available(handle) is not None:
        time.sleep(interval)


class LineReader:
    """``readline`` that waits for a whole line without blocking inside ReadFile."""

    def __init__(self, stream=None, interval: float = 0.05):
        self.stream = sys.stdin if stream is None else stream
        self.interval = interval
        self._fd, self._handle = _pipe(self.stream)
        self._pending = b""

    def readline(self) -> str:
        if self._handle is None:
            return self.stream.readline()
        while b"\n" not in self._pending:
            available = _available(self._handle)
            if available is None:  # EOF: whatever is left, then ''
                line, self._pending = self._pending, b""
                return line.decode("utf-8")
            if available:
                self._pending += os.read(self._fd, available)
            else:
                time.sleep(self.interval)
        line, _newline, self._pending = self._pending.partition(b"\n")
        return line.decode("utf-8") + "\n"
