"""The speakers open when Iris speaks, not when core.tts is imported.

Importing sounddevice starts PortAudio, and on a machine with no audio system
(the Linux CI runner, a headless server) that raises. It used to stop ten test
modules before they ran, text-only ones included, and would stop Core too.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

import core.tts as tts
from core.tts import TextToSpeech

ROOT = Path(__file__).resolve().parents[1]


def engine() -> TextToSpeech:
    return TextToSpeech(SimpleNamespace(tts_allow_remote_fallback=False))


def fake_sounddevice() -> ModuleType:
    module = ModuleType("sounddevice")
    module.PortAudioError = type("PortAudioError", (Exception,), {})
    module.played = []
    module.stopped = 0
    module.play = lambda audio, samplerate: module.played.append((len(audio), samplerate))
    module.wait = lambda: None

    def stop():
        module.stopped += 1

    module.stop = stop
    return module


def test_importing_core_tts_does_not_load_the_audio_system():
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    probe = "import sys, core.tts; print('sounddevice' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=120, check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "False"


def test_the_first_reply_opens_the_speakers_and_plays(monkeypatch):
    device = fake_sounddevice()
    monkeypatch.setitem(sys.modules, "sounddevice", device)
    monkeypatch.setattr(tts, "_sounddevice", None)
    monkeypatch.setattr(tts, "decode_audio", lambda _audio: (np.zeros(480, dtype=np.int16), 24000))
    started = []

    played = asyncio.run(engine().play_audio(b"audio", on_started=lambda: started.append(True)))

    assert played is True
    assert device.played == [(480, 24000)]
    assert started == [True]
    assert tts._sounddevice is device


def test_no_audio_system_leaves_the_reply_silent_without_crashing(monkeypatch, caplog):
    def no_audio():
        raise OSError("PortAudio library not found")

    monkeypatch.setattr(tts, "_audio_output", no_audio)

    played = asyncio.run(engine().play_audio(b"audio"))

    assert played is False
    assert "no audio output is available" in caplog.text


def test_stopping_before_anything_played_does_not_open_the_speakers(monkeypatch):
    monkeypatch.setattr(tts, "_sounddevice", None)
    monkeypatch.setitem(sys.modules, "sounddevice", None)  # an import would now fail

    asyncio.run(engine().stop())

    assert tts._sounddevice is None


@pytest.mark.parametrize("slow_step", ["opening the speakers", "decoding"])
def test_a_phrase_stopped_while_being_prepared_never_starts(monkeypatch, slow_step):
    # Codex, 2026-11-17: Stop while the first phrase was still loading PortAudio
    # returned, and the phrase started once loading finished.
    device = fake_sounddevice()
    entered, release = threading.Event(), threading.Event()

    def hold():
        entered.set()
        assert release.wait(5)

    def slow_open():
        hold()
        return device

    def decode(_audio):
        if slow_step == "decoding":
            hold()
        return np.zeros(480, dtype=np.int16), 24000

    if slow_step == "opening the speakers":
        monkeypatch.setattr(tts, "_sounddevice", None)
        monkeypatch.setattr(tts, "_audio_output", slow_open)
    else:
        monkeypatch.setattr(tts, "_sounddevice", device)
    monkeypatch.setattr(tts, "decode_audio", decode)

    async def scenario():
        voice = engine()
        phrase = asyncio.ensure_future(voice.play_audio(b"audio"))
        assert await asyncio.to_thread(entered.wait, 5)
        await voice.stop()
        release.set()
        stopped = await phrase
        silent = list(device.played)
        next_phrase = await voice.play_audio(b"audio")
        return stopped, silent, next_phrase

    stopped, silent, next_phrase = asyncio.run(scenario())

    assert stopped is True  # stopped by the user, not a speaker failure
    assert silent == []
    assert next_phrase is True and device.played == [(480, 24000)]


@pytest.mark.parametrize("failing_step", ["opening the speakers", "decoding"])
def test_a_failure_after_stop_is_not_a_speaker_problem(monkeypatch, caplog, failing_step):
    # Codex, 2026-11-17: Stop, then the speakers failed to open, and the engine
    # still answered «playback failed», so Core would show «Revisa la salida de audio».
    entered, release = threading.Event(), threading.Event()
    opening = failing_step == "opening the speakers"

    def hold_then_fail(*_args):
        entered.set()
        assert release.wait(5)
        raise OSError("PortAudio could not start") if opening else RuntimeError("could not decode")

    if opening:
        monkeypatch.setattr(tts, "_sounddevice", None)
        monkeypatch.setattr(tts, "_audio_output", hold_then_fail)
    else:
        monkeypatch.setattr(tts, "_sounddevice", fake_sounddevice())
        monkeypatch.setattr(tts, "decode_audio", hold_then_fail)

    async def scenario():
        voice = engine()
        phrase = asyncio.ensure_future(voice.play_audio(b"audio"))
        assert await asyncio.to_thread(entered.wait, 5)
        await voice.stop()
        release.set()
        return await phrase

    assert asyncio.run(scenario()) is True
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_a_decode_failure_without_stop_is_still_reported(monkeypatch, caplog):
    def broken(_audio):
        raise RuntimeError("could not decode")

    monkeypatch.setattr(tts, "_sounddevice", fake_sounddevice())
    monkeypatch.setattr(tts, "decode_audio", broken)

    assert asyncio.run(engine().play_audio(b"audio")) is False
    assert "Audio decode error" in caplog.text


def test_stopping_after_playback_stops_the_stream(monkeypatch):
    device = fake_sounddevice()
    monkeypatch.setattr(tts, "_sounddevice", device)

    asyncio.run(engine().stop())

    assert device.stopped == 1
