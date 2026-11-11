from __future__ import annotations

import asyncio
import json
import threading
import time
from unittest.mock import AsyncMock

import httpx
import pytest

import core.hermes_bridge as module
from core.config import IRISConfig
from core.hermes_bridge import HermesBridge
from core.model_selection import ConversationModelStore, ModelSelectionService


def setup_bridge(tmp_path, monkeypatch, mode="legacy", local=True):
    monkeypatch.setattr(module, "_enabled_skills_block", lambda: "")
    bridge = HermesBridge(IRISConfig())
    bridge._is_available = True
    bridge._gateway_local = (time.monotonic(), local)
    bridge._supports_session_runtime = mode == "session"
    bridge.model_selection = ModelSelectionService(
        ConversationModelStore(tmp_path / "models.json"),
        default_reader=lambda profile: {"provider": "fixture", "model": "fixture-model"},
    )
    bridge.default_model = AsyncMock(return_value={"provider": "fixture", "model": "fixture-model"})
    requests = []

    def handle(req):
        requests.append(req)
        if req.url.path.endswith("/api/sessions"):
            return httpx.Response(201, json={"session": {"id": bridge.session_id}})
        if mode == "stream":
            data = {"choices": [{"delta": {"content": '{"texto_hablado":"Listo"}'}}]}
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "id": "fixture-run",
                "choices": [{"message": {"content": '{"texto_hablado":"Listo"}'}}],
            },
        )

    bridge._client = httpx.AsyncClient(
        base_url="http://fixture.test", transport=httpx.MockTransport(handle)
    )
    return bridge, requests


@pytest.mark.parametrize("mode", ["legacy", "stream", "session"])
def test_unverified_policy_prevents_every_chat_transport(tmp_path, monkeypatch, mode):
    bridge, requests = setup_bridge(tmp_path, monkeypatch, mode)
    checked = []

    def refuse(profile=None):
        checked.append(profile)
        return False

    monkeypatch.setattr(module, "keep_unusable_tools_off", refuse)

    async def go():
        try:
            return await bridge.send_message(
                "hola", progress_callback=(lambda event: None) if mode == "stream" else None
            )
        finally:
            await bridge._client.aclose()

    result = asyncio.run(go())
    assert result["_provider_error"]["kind"] == "tool_policy"
    assert checked == [None]
    assert not any(r.method == "POST" for r in requests)


def test_foreign_gateway_never_edits_local_settings(tmp_path, monkeypatch):
    bridge, requests = setup_bridge(tmp_path, monkeypatch, local=False)

    def forbidden(profile=None):
        raise AssertionError("foreign gateway must not change local tools")

    monkeypatch.setattr(module, "keep_unusable_tools_off", forbidden)

    async def go():
        try:
            return await bridge.send_message("hola")
        finally:
            await bridge._client.aclose()

    assert asyncio.run(go())["texto_hablado"] == "Listo"
    assert len(requests) == 1


@pytest.mark.parametrize("mode", ["legacy", "stream"])
def test_preparing_a_turn_never_retargets_it_to_another_profile(tmp_path, monkeypatch, mode):
    bridge, requests = setup_bridge(tmp_path, monkeypatch, mode)
    bridge._active_profile = "first"
    bridge._profile_api_key = "first-key"
    reached = threading.Event()
    release = threading.Event()
    checked = []

    def prepare(profile=None):
        checked.append(profile)
        reached.set()
        assert release.wait(5)
        return True

    monkeypatch.setattr(module, "keep_unusable_tools_off", prepare)

    async def go():
        task = asyncio.create_task(
            bridge.send_message(
                "hola", progress_callback=(lambda event: None) if mode == "stream" else None
            )
        )
        try:
            assert await asyncio.to_thread(reached.wait, 3)
            bridge._active_profile = "second"
            bridge._profile_api_key = "second-key"
            bridge._session_id = "another-session"
            bridge._conversation_generation += 1
            release.set()
            return await task
        finally:
            release.set()
            await bridge._client.aclose()

    assert asyncio.run(go())["texto_hablado"] == "Listo"
    assert checked == ["first"]
    assert requests[-1].url.path == "/p/first/v1/chat/completions"
    assert requests[-1].headers["authorization"] == "Bearer first-key"


def test_stop_during_preparation_sends_no_chat_request(tmp_path, monkeypatch):
    bridge, requests = setup_bridge(tmp_path, monkeypatch)
    reached = threading.Event()
    release = threading.Event()

    def prepare(profile=None):
        reached.set()
        release.wait(5)
        return True

    monkeypatch.setattr(module, "keep_unusable_tools_off", prepare)

    async def go():
        task = asyncio.create_task(bridge.send_message("hola"))
        try:
            assert await asyncio.to_thread(reached.wait, 3)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            await bridge._client.aclose()

    asyncio.run(go())
    assert requests == []
