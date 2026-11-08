"""The selected browser finishes a reply; duration is never its normal stop signal."""
import asyncio
from unittest.mock import patch

import pytest

from core.tts_playback import TtsPlaybackReceipts


def test_only_the_original_owner_can_finish_a_specific_playback():
    async def run():
        broker = TtsPlaybackReceipts()
        owner, other = object(), object()
        token = broker.begin(owner)
        wait = asyncio.create_task(broker.wait(token, 0.01))
        await asyncio.sleep(0.03)  # longer than the nominal audio; still no ended
        assert not wait.done()
        assert not broker.answer(other, {"playback_id": token, "status": "ended"})
        assert not broker.answer(owner, {"playback_id": "old", "status": "ended"})
        assert not broker.answer(owner, {"playback_id": token, "status": "playing"})
        assert broker.answer(owner, {"playback_id": token, "status": "ended"})
        assert not broker.answer(owner, {"playback_id": token, "status": "failed"})
        assert await wait == "ended"
        assert not broker._pending
    asyncio.run(run())


@pytest.mark.parametrize("status", ["failed", "stopped", "disconnected", "cancelled"])
def test_failure_disconnect_and_interruption_release_the_wait(status):
    async def run():
        broker, owner = TtsPlaybackReceipts(), object()
        token = broker.begin(owner)
        wait = asyncio.create_task(broker.wait(token, 10))
        await asyncio.sleep(0)
        if status == "cancelled":
            wait.cancel()
            with pytest.raises(asyncio.CancelledError):
                await wait
        else:
            if status == "disconnected":
                broker.disconnect(owner)
            else:
                assert broker.answer(owner, {"playback_id": token, "status": status})
            assert await wait == status
        assert not broker._pending
        assert not broker.answer(owner, {"playback_id": token, "status": "ended"})
    asyncio.run(run())


def test_missing_receipt_has_a_bounded_recovery_and_cleans_up():
    async def run():
        broker, owner = TtsPlaybackReceipts(), object()
        token = broker.begin(owner)
        async def timeout(*args):
            raise TimeoutError
        with patch("core.tts_playback.asyncio.wait_for", timeout):
            assert await broker.wait(token, 6) == "timeout"
        assert not broker._pending
    asyncio.run(run())


def test_socket_route_waits_even_when_receipt_is_received_inside_send():
    from core import websocket_server as server
    from core.surface_registry import SurfaceRegistry
    import json
    async def run():
        registry, receipts = SurfaceRegistry(), TtsPlaybackReceipts()
        messages = []
        class Socket:
            async def send_text(self, text):
                message = json.loads(text)
                messages.append(message)
                await server._handle_face_message(self, {"type": "tts_playback_result", "payload": {
                    "playback_id": message["payload"]["playback_id"], "status": "ended"}})
        owner = Socket()
        registry.register_ready(owner, {"role": "face", "capabilities": {"reports_tts_playback": True}})
        with patch.object(server, "_surface_registry", registry), patch.object(server, "_tts_playback", receipts):
            assert await server.send_tts_audio({"audio": "YWJj", "visemes": []}, wait_for_end=True, duration_s=6)
        assert len(messages) == 1 and messages[0]["payload"]["playback_id"]
        assert not receipts._pending
    asyncio.run(run())
