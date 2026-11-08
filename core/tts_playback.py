"""Correlated completion receipts from the surface that actually plays a reply."""
from __future__ import annotations

import asyncio
import math
import uuid
from typing import Any


class TtsPlaybackReceipts:
    def __init__(self) -> None:
        self._pending: dict[str, tuple[Any, asyncio.Future[str]]] = {}

    def begin(self, owner: Any) -> str:
        token = uuid.uuid4().hex
        self._pending[token] = (owner, asyncio.get_running_loop().create_future())
        return token

    def answer(self, owner: Any, payload: dict) -> bool:
        token = payload.get("playback_id")
        if not isinstance(token, str):
            return False
        pending = self._pending.get(token)
        status = payload.get("status")
        if pending is None or pending[0] is not owner or status not in ("ended", "failed", "stopped"):
            return False
        future = pending[1]
        if future.done():
            return False
        future.set_result(status)
        return True

    async def wait(self, token: str, duration_s: float) -> str:
        pending = self._pending[token]
        # Duration is only a recovery bound. Normal completion comes from ended,
        # after decode/startup and the entire recording, not from a server clock.
        duration = duration_s if math.isfinite(duration_s) and duration_s > 0 else 60.0
        timeout = min(900.0, max(30.0, duration + 30.0))
        try:
            return await asyncio.wait_for(asyncio.shield(pending[1]), timeout)
        except TimeoutError:
            return "timeout"
        finally:
            self.discard(token)

    def discard(self, token: str) -> None:
        pending = self._pending.pop(token, None)
        if pending and not pending[1].done():
            pending[1].cancel()

    def disconnect(self, owner: Any) -> None:
        for pending_owner, future in tuple(self._pending.values()):
            if pending_owner is owner and not future.done():
                future.set_result("disconnected")
