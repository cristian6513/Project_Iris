"""A reply that could not be spoken says so: silence alone looks like a broken speaker."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import core.agent as agent_module
from core.tts import EdgeTTSProvider, TextToSpeech


@pytest.fixture
def face(monkeypatch):
    for name in ("send_emotion", "send_animation", "send_speech_start", "send_subtitle",
                 "send_lip_sync", "send_speech_end", "send_status"):
        monkeypatch.setattr(agent_module, name, AsyncMock())
    monkeypatch.setattr(agent_module, "audio_clients_connected", lambda: 0)
    sent = AsyncMock()
    monkeypatch.setattr(agent_module, "broadcast_to_face", sent)
    return sent


def reply_with(audio, last_failure=""):
    tts = SimpleNamespace(speak=AsyncMock(return_value=(audio, [])), play_audio=AsyncMock(), last_failure=last_failure)

    async def respond():
        agent = SimpleNamespace(tts=tts, _speak_lock=asyncio.Lock())
        await agent_module.IRISAgent._respond(agent, {"texto_hablado": "Hola", "emocion": "feliz", "animacion_virtual": "idle"})

    asyncio.run(respond())


def test_a_reply_without_voice_tells_why_after_it_ends(face):
    reply_with(b"FALLBACK_FAILED", "La API key de Fish Audio no es válida o caducó.")
    face.assert_awaited_once()
    kind, payload = face.await_args.args
    assert kind == "voice_unavailable"
    assert "La API key de Fish Audio no es válida o caducó." in payload["message"]
    assert "Revisa la voz" in payload["message"]
    agent_module.send_speech_end.assert_awaited_once()


def test_an_engine_that_gives_no_reason_still_says_so(face):
    reply_with(b"FALLBACK_FAILED")
    message = face.await_args.args[1]["message"]
    assert message == "Iris no pudo hablar. Revisa la voz en Configuración."


def test_a_reply_with_voice_says_nothing(face):
    reply_with(b"audio")
    face.assert_not_awaited()


def real_engine(provider):
    engine = TextToSpeech(SimpleNamespace(tts_allow_remote_fallback=False))
    engine._provider = provider
    return engine


def spoken_through(engine, text="Hola, esta es una prueba."):
    async def respond():
        agent = SimpleNamespace(tts=engine, _speak_lock=asyncio.Lock())
        await agent_module.IRISAgent._respond(agent, {"texto_hablado": text, "emocion": "feliz", "animacion_virtual": "idle"})

    asyncio.run(respond())


@pytest.mark.parametrize("provider", [EdgeTTSProvider(), None], ids=["edge-tts down", "no engine yet"])
def test_every_engine_that_stays_silent_says_so(face, provider):
    spoken_through(real_engine(provider))
    assert face.await_args.args[0] == "voice_unavailable"


def test_a_provider_message_is_shown_and_does_not_outlive_its_failure(face):
    provider = SimpleNamespace(synthesize=AsyncMock(return_value=(b"", [])), last_error="Sin saldo.")
    engine = real_engine(provider)
    spoken_through(engine)
    assert "Sin saldo." in face.await_args.args[1]["message"]
    face.reset_mock()
    provider.synthesize = AsyncMock(return_value=(b"audio", []))
    spoken_through(engine)
    face.assert_not_awaited()
    assert engine.last_failure == ""


def test_a_reply_with_nothing_to_say_is_not_an_error(face):
    spoken_through(real_engine(SimpleNamespace(synthesize=AsyncMock(return_value=(b"", [])))), text="😀")
    face.assert_not_awaited()
