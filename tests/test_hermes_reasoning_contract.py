"""Contract tests for IRIS' request-level Hermes reasoning integration."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from core.config import IRISConfig
from core.hermes_bridge import HermesBridge, _build_reasoning_model_options


class _ChatClient:
    def __init__(self) -> None:
        self.last_body: dict[str, Any] = {}

    async def post(self, _path: str, *, json: dict[str, Any], headers: dict[str, str]):
        self.last_body = json
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "choices": [{
                    "message": {
                        "content": '{"texto_hablado":"ok","emocion":"neutral","animacion_virtual":"idle"}'
                    }
                }],
            },
        )


class _ProbeClient:
    def __init__(self, *, supports_model_options: bool) -> None:
        self.supports_model_options = supports_model_options

    async def get(self, path: str, **_kwargs: Any):
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "hermes-agent"}]})
        if path == "/v1/capabilities":
            return httpx.Response(
                200,
                json={"features": {"model_options": self.supports_model_options}},
            )
        return httpx.Response(404)


class _StreamResponse:
    status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def aiter_lines(self):
        payload = {
            "id": "chatcmpl-stream-test",
            "choices": [{
                "delta": {
                    "content": '{"texto_hablado":"stream","emocion":"neutral","animacion_virtual":"idle"}'
                }
            }],
        }
        yield f"data: {json.dumps(payload)}"
        yield "data: [DONE]"


class _StreamingClient:
    def __init__(self) -> None:
        self.last_body: dict[str, Any] = {}

    def stream(
        self,
        _method: str,
        _path: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
    ) -> _StreamResponse:
        self.last_body = json
        return _StreamResponse()


def test_reasoning_options_match_the_hermes_020_contract() -> None:
    assert _build_reasoning_model_options("none") == {
        "reasoning": {"enabled": False},
        "reasoning_effort": "none",
    }
    assert _build_reasoning_model_options("high") == {
        "reasoning": {"enabled": True, "effort": "high"},
        "reasoning_effort": "high",
    }
    assert _build_reasoning_model_options("invalid") is None


def test_capability_probe_enables_request_model_options() -> None:
    bridge = HermesBridge(IRISConfig())
    bridge._client = _ProbeClient(supports_model_options=True)  # type: ignore[assignment]

    asyncio.run(bridge._check_availability())

    assert bridge.is_available is True
    assert bridge._supports_model_options is True


def test_chat_uses_model_options_and_never_the_ignored_extra_body() -> None:
    bridge = HermesBridge(IRISConfig())
    client = _ChatClient()
    bridge._client = client  # type: ignore[assignment]
    bridge._is_available = True
    bridge._supports_model_options = True

    result = asyncio.run(bridge.send_message("mira", reasoning_effort="none"))

    assert result["texto_hablado"] == "ok"
    assert client.last_body["model_options"] == {
        "reasoning": {"enabled": False},
        "reasoning_effort": "none",
    }
    assert "extra_body" not in client.last_body


def test_legacy_chat_relies_on_persisted_reasoning_without_fake_override() -> None:
    bridge = HermesBridge(IRISConfig())
    client = _ChatClient()
    bridge._client = client  # type: ignore[assignment]
    bridge._is_available = True
    bridge._supports_model_options = False

    asyncio.run(bridge.send_message("hola", reasoning_effort="high"))

    assert "model_options" not in client.last_body
    assert "extra_body" not in client.last_body


def test_streaming_chat_uses_the_same_model_options_contract() -> None:
    bridge = HermesBridge(IRISConfig())
    client = _StreamingClient()
    bridge._client = client  # type: ignore[assignment]
    bridge._is_available = True
    bridge._supports_model_options = True

    result = asyncio.run(
        bridge.send_message(
            "mira",
            reasoning_effort="low",
            progress_callback=lambda _event: None,
        )
    )

    assert result["texto_hablado"] == "stream"
    assert client.last_body["model_options"] == {
        "reasoning": {"enabled": True, "effort": "low"},
        "reasoning_effort": "low",
    }
    assert "extra_body" not in client.last_body
