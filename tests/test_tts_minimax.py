from __future__ import annotations

import asyncio
import inspect
from unittest.mock import patch

from core.config import IRISConfig
from core.tts import MiniMaxTTSProvider
from wizard.app import ConfigUpdate


def test_minimax_defaults_use_an_official_spanish_voice() -> None:
    assert IRISConfig().minimax_voice_id == "Spanish_Wiselady"
    assert ConfigUpdate().minimax_voice_id == "Spanish_Wiselady"
    assert MiniMaxTTSProvider("token").voice_id == "Spanish_Wiselady"


def test_minimax_t2a_requests_and_decodes_hex_audio() -> None:
    source = inspect.getsource(MiniMaxTTSProvider.synthesize)

    assert '"output_format": "hex"' in source
    assert "bytes.fromhex(audio_hex)" in source
    assert "base64.b64decode" not in source


def test_minimax_hex_response_produces_playable_binary_bytes() -> None:
    class Response:
        status_code = 200
        headers: dict[str, str] = {"content-type": "application/json"}

        @staticmethod
        def json() -> dict[str, object]:
            return {"data": {"audio": "49443304"}}

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> Response:
            return Response()

    with patch("httpx.AsyncClient", return_value=Client()):
        audio, markers = asyncio.run(MiniMaxTTSProvider("token").synthesize("Hola"))

    assert audio == b"ID3\x04"
    assert markers == []
