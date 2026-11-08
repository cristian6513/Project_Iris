"""Voice receives readable text; visual replies keep their original content."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.config import IRISConfig
from core.speech_text import text_for_speech
from core.tts import FishSpeechTTSProvider, TextToSpeech


@pytest.mark.parametrize(
    ("original", "spoken"),
    [
        ("Hola 😊, ¿cómo estás?", "Hola, ¿cómo estás?"),
        (r"Hola \ ¿cómo estás?", "Hola ¿cómo estás?"),
        ("Hola😊mundo", "Hola mundo"),
        ("Listo 👍🏽 👩🏽‍💻 👨‍👩‍👧‍👦 🇨🇴 🏳️‍🌈", "Listo"),
        ("Bien ❤ ❤️ ♥️ ✅ ⭐ ⏰ ☀️", "Bien"),
        ("Sigue ➡️ hacia allí", "Sigue hacia allí"),
        ("1️⃣ Abre la app\n2⃣ Pulsa Hablar", "1 Abre la app\n2 Pulsa Hablar"),
        ("## Resumen\n- **Hola**\n* _mundo_\n> Bien", "Resumen\nHola\nmundo\nBien"),
        ("**Hola *mundo***", "Hola mundo"),
        ("Lee [la guía](https://example.com/guia_(voz)) primero.", "Lee la guía primero."),
        ("![Avatar](avatar.png)", "Avatar"),
        (r"Abre `C:\Users\Ana\archivo.txt`.", "Abre C: Users Ana archivo.txt."),
        ("```python\nprint('hola')\n```", "print('hola')"),
        ("```hola```", "hola"),
        ("```python\r\nprint(1)\r\n```", "print(1)"),
        ("Texto\n---\n***\n___\nMás texto", "Texto\n\nMás texto"),
        ("Hola&#x20;mundo&nbsp;&amp; compañía.", "Hola mundo & compañía."),
        ("Árbol, pingüino, niño: ¡sí! ¿Y tú?", "Árbol, pingüino, niño: ¡sí! ¿Y tú?"),
        ("Cuesta 25 € o $30; bajó 5% a -3,5 °C.", "Cuesta 25 € o $30; bajó 5% a -3,5 °C."),
        (
            "2 * 3 * 4 = 24; 2*3*4; 2 × 3 = 6; 6 ÷ 2 = 3; 3 ✕ 2 = 6.",
            "2 * 3 * 4 = 24; 2*3*4; 2 × 3 = 6; 6 ÷ 2 = 3; 3 ✕ 2 = 6.",
        ),
        ("- 5\n> 3\n* 2", "- 5\n> 3\n* 2"),
        (
            "C++ y C#, versión 1.2.3, issue #42, mi_archivo_final.py",
            "C++ y C#, versión 1.2.3, issue #42, mi_archivo_final.py",
        ),
        ("x ≤ 5 → y ≥ 3; © 2026", "x ≤ 5 → y ≥ 3; © 2026"),
        ("", ""),
        ("😊👩🏽‍💻🇨🇴❤️⭐", ""),
        (r"*** \ | ...", ""),
    ],
)
def test_spoken_projection(original: str, spoken: str) -> None:
    assert text_for_speech(original) == spoken


class RecordingProvider:
    def __init__(self, audio: bytes = b"audio", *, raises: bool = False) -> None:
        self.audio = audio
        self.raises = raises
        self.received: list[str] = []

    async def synthesize(self, text: str):
        self.received.append(text)
        if self.raises:
            raise RuntimeError("synthesis failed")
        return self.audio, [{"time": 0.0, "viseme": "aa"}] if self.audio else []


def test_primary_receives_clean_text_without_rewriting_reply() -> None:
    engine = TextToSpeech(IRISConfig())
    provider = RecordingProvider()
    engine._provider = provider
    reply = {"texto_hablado": r"**Hola** 😊 \ ¿cómo estás?"}
    result = asyncio.run(engine.speak(reply["texto_hablado"]))
    assert provider.received == ["Hola ¿cómo estás?"]
    assert result == (b"audio", [{"time": 0.0, "viseme": "aa"}])
    assert reply["texto_hablado"] == r"**Hola** 😊 \ ¿cómo estás?"


@pytest.mark.parametrize("raises", [False, True])
def test_opted_in_fallback_also_receives_clean_text(raises: bool) -> None:
    engine = TextToSpeech(IRISConfig(tts_allow_remote_fallback=True))
    primary = RecordingProvider(b"", raises=raises)
    fallback = RecordingProvider(b"fallback-audio")
    engine._provider = primary
    engine._ensure_fallback = AsyncMock(return_value=fallback)
    audio, _ = asyncio.run(engine.speak(r"Listo ✅ \ continúa."))
    assert primary.received == fallback.received == ["Listo continúa."]
    assert audio == b"fallback-audio"


def test_emoji_only_reply_does_not_call_any_provider() -> None:
    engine = TextToSpeech(IRISConfig(tts_allow_remote_fallback=True))
    provider = RecordingProvider()
    engine._provider = provider
    engine._ensure_fallback = AsyncMock()
    assert asyncio.run(engine.speak("😊 ❤️ 👍🏽")) == (b"", [])
    assert provider.received == []
    engine._ensure_fallback.assert_not_awaited()


def test_emotion_still_reaches_fish_speech() -> None:
    engine = TextToSpeech(IRISConfig())
    provider = FishSpeechTTSProvider()
    provider.synthesize = AsyncMock(return_value=(b"audio", []))
    engine._provider = provider
    assert asyncio.run(engine.speak("Hola 😊", emotion="feliz")) == (b"audio", [])
    provider.synthesize.assert_awaited_once_with("Hola")
    assert provider.emotion == "feliz"


def test_agent_keeps_original_subtitles_and_speech_events(monkeypatch) -> None:
    import core.agent as agent_module

    engine = TextToSpeech(IRISConfig())
    provider = RecordingProvider()
    engine._provider = provider
    engine.play_audio = AsyncMock()
    for name in (
        "send_emotion",
        "send_animation",
        "send_speech_start",
        "send_subtitle",
        "send_lip_sync",
        "send_speech_end",
        "send_status",
    ):
        monkeypatch.setattr(agent_module, name, AsyncMock())
    monkeypatch.setattr(agent_module, "audio_clients_connected", lambda: 0)
    original = r"**Hola** 😊 \ ¿cómo estás?"
    response = {"texto_hablado": original, "emocion": "feliz", "animacion_virtual": "idle"}

    async def respond() -> None:
        agent = SimpleNamespace(tts=engine, _speak_lock=asyncio.Lock())
        await agent_module.IRISAgent._respond(agent, response)

    asyncio.run(respond())
    assert provider.received == ["Hola ¿cómo estás?"]
    engine.play_audio.assert_awaited_once_with(b"audio")
    agent_module.send_subtitle.assert_awaited_once_with(original)
    agent_module.send_speech_start.assert_awaited_once_with(
        original,
        "feliz",
        animation="idle",
    )
    agent_module.send_speech_end.assert_awaited_once_with()
    assert response["texto_hablado"] == original
