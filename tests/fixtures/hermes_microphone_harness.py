"""Drive Hermes 0.23.3's recorder, ``voice.toggle`` and wake detector with a fake sounddevice.

Usage: python -I -B hermes_microphone_harness.py <hermes tree>. Prints one JSON object: what
each scenario saw (open input streams, recorder identity, returned payloads). The tree holds the
pinned Hermes files (tests/test_hermes_microphone_release.py writes them from the source archive,
with or without the IRIS patches); ``_voice_toggle_mode`` runs from its real source, compiled
alone, because importing tui_gateway would pull in the whole gateway.
"""
import ast
import contextlib
import json
import logging
import os
import sys
import threading
import time
import types

TREE = sys.argv[1]
sys.path.insert(0, TREE)

import numpy as np  # noqa: E402

# Live STT (stt.streaming) is not under test: no session, as when it is off.
sys.modules["tools.transcription_streaming"] = types.SimpleNamespace(
    open_streaming_session=lambda on_partial=None: None)

import hermes_cli.voice as voice  # noqa: E402
import tools.voice_mode as voice_mode  # noqa: E402
import tools.wake_word as wake_word  # noqa: E402

STREAMS = []
WARNINGS = []


class FakeInputStream:
    """A PortAudio input stream: counts as holding the microphone until close()."""

    def __init__(self, *args, **kwargs):
        self.kind = "wake" if "blocksize" in kwargs else "recorder"
        self.active = False
        self.closed = False
        STREAMS.append(self)

    def start(self):
        if self.closed:
            raise RuntimeError("stream closed")
        self.active = True

    def stop(self):
        self.active = False

    def abort(self):
        self.active = False

    def close(self):
        self.active = False
        self.closed = True

    @property
    def read_available(self):
        return 1 << 20

    def read(self, frames):
        time.sleep(0.005)
        return np.zeros((frames, 1), dtype=np.int16), False


FAKE_SD = types.SimpleNamespace(
    InputStream=FakeInputStream,
    query_devices=lambda *args, **kwargs: {"name": "fake microphone", "default_samplerate": 16000.0,
                                           "max_input_channels": 1, "hostapi": 0},
    query_hostapis=lambda *args, **kwargs: {"name": "fake host"},
)
voice_mode._import_audio = lambda: (FAKE_SD, np)
wake_word._import_audio = lambda: (FAKE_SD, np)
voice._play_beep = lambda *args, **kwargs: None  # no speaker in tests


class Capture(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.WARNING:
            WARNINGS.append(record.getMessage())


logging.getLogger().addHandler(Capture())
logging.getLogger().setLevel(logging.WARNING)


def _toggle_function():
    """``_voice_toggle_mode`` from the tree's tui_gateway/methods_voice.py, with stub collaborators."""
    path = os.path.join(TREE, "tui_gateway", "methods_voice.py")
    with open(path, encoding="utf-8") as handle:
        module = ast.parse(handle.read(), path)
    node = next(item for item in module.body
                if isinstance(item, ast.FunctionDef) and item.name == "_voice_toggle_mode")
    namespace = {
        "os": os, "contextlib": contextlib, "logger": logging.getLogger("tui_gateway.methods_voice"),
        "_ok": lambda rid, payload: {"id": rid, "result": payload},
        "_voice_status_payload": lambda stop_hint="": {"enabled": os.environ.get("HERMES_VOICE") == "1"},
        "_voice_tts_enabled": lambda: False,
        "_tts_lease_async": lambda *args: None,
        "_set_voice_tts": lambda on: None,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), namespace)
    return namespace["_voice_toggle_mode"]


TOGGLE = _toggle_function()


def toggle(action):
    return TOGGLE(1, {"action": action})["result"]["enabled"]


def open_streams(kind=None):
    return sum(1 for s in STREAMS if not s.closed and (kind is None or s.kind == kind))


def created(kind):
    return sum(1 for s in STREAMS if s.kind == kind)


def start_take(statuses=None):
    """What ``voice.record start`` does: a one-shot VAD capture (auto_restart=False)."""
    return voice.start_continuous(on_transcript=lambda text: None,
                                  on_status=(statuses.append if statuses is not None else None),
                                  auto_restart=False)


def reset():
    """Leave no recorder or stream behind for the next scenario (upstream keeps them)."""
    rec = voice._continuous_recorder
    voice._continuous_recorder = None
    voice._continuous_active = voice._continuous_stopping = False
    if rec is not None:
        rec.shutdown()
    del STREAMS[:]
    del WARNINGS[:]
    os.environ["HERMES_VOICE"] = "0"


def vad_take():
    """A take that ends on its own (silence), then voice.toggle off, then the next take."""
    reset()
    seen = {}
    toggle("on")
    start_take()
    seen["recording_open"] = open_streams()
    voice._continuous_on_silence()  # the VAD end: stop(), nothing to transcribe, no re-arm
    seen["loop_active_after_take"] = voice.is_continuous_active()
    seen["open_after_take"] = open_streams()
    seen["enabled_after_off"] = toggle("off")
    seen["open_after_off"] = open_streams()
    seen["recorder_dropped"] = voice._continuous_recorder is None
    seen["off_again"] = toggle("off")  # idempotent
    toggle("on")
    start_take()
    seen["next_take_open"] = open_streams()
    seen["next_take_streams_created"] = created("recorder")
    toggle("off")
    seen["open_after_second_off"] = open_streams()
    return seen


def forced_stop_then_off_while_transcribing():
    """voice.record stop (force_transcribe) and voice.toggle off before transcription finishes."""
    reset()
    seen = {}
    gate, statuses = threading.Event(), []
    original = voice._turn_transcript

    def slow_transcript(*args, **kwargs):
        gate.wait(5)
        return original(*args, **kwargs)

    voice._turn_transcript = slow_transcript
    try:
        toggle("on")
        start_take(statuses)
        voice.stop_continuous(force_transcribe=True)
        seen["stopping_during_off"] = voice._continuous_stopping
        seen["enabled_after_off"] = toggle("off")
        seen["open_after_off"] = open_streams()
        gate.set()
        deadline = time.monotonic() + 5
        while voice._continuous_stopping and time.monotonic() < deadline:
            time.sleep(0.01)
        seen["stopping_cleared"] = not voice._continuous_stopping
        seen["statuses"] = statuses
    finally:
        voice._turn_transcript = original
        gate.set()
    toggle("on")
    seen["next_take_started"] = start_take() is True
    seen["next_take_open"] = open_streams()
    toggle("off")
    seen["open_after_second_off"] = open_streams()
    return seen


def cancel_while_recording():
    reset()
    seen = {}
    toggle("on")
    start_take()
    seen["recording_open"] = open_streams()
    seen["enabled_after_off"] = toggle("off")
    seen["loop_active_after_off"] = voice.is_continuous_active()
    seen["open_after_off"] = open_streams()
    return seen


def live_capture_untouched():
    reset()
    seen = {}
    toggle("on")
    start_take()
    rec = voice._continuous_recorder
    if hasattr(voice, "release_microphone"):
        voice.release_microphone()
    seen["same_recorder"] = voice._continuous_recorder is rec
    seen["still_recording"] = bool(getattr(rec, "is_recording", False))
    seen["open_while_live"] = open_streams()
    toggle("off")
    seen["open_after_off"] = open_streams()
    return seen


class _Darwin:
    platform = "darwin"

    def __getattr__(self, name):
        return getattr(sys, name)


def darwin_keeps_stream():
    reset()
    seen = {}
    voice.sys = _Darwin()
    try:
        toggle("on")
        start_take()
        voice._continuous_on_silence()
        toggle("off")
        seen["open_after_off"] = open_streams()
        seen["recorder_kept"] = voice._continuous_recorder is not None
    finally:
        voice.sys = sys
    return seen


def shutdown_failure_is_logged():
    reset()
    seen = {}
    original = voice_mode.AudioRecorder.shutdown

    def broken(self):
        raise RuntimeError("device vanished")

    voice_mode.AudioRecorder.shutdown = broken
    try:
        toggle("on")
        start_take()
        voice._continuous_on_silence()
        seen["enabled_after_off"] = toggle("off")
        seen["recorder_dropped"] = voice._continuous_recorder is None
        seen["warnings"] = list(WARNINGS)
    finally:
        voice_mode.AudioRecorder.shutdown = original
    for stream in STREAMS:
        stream.close()
    return seen


class FakeEngine:
    frame_length = 1280

    def process(self, frame):
        return False

    def reset(self):
        pass

    def close(self):
        pass


def wake_keeps_its_own_stream():
    """The wake detector's stream is its own: voice.toggle off leaves it; pause/stop close it."""
    reset()
    seen = {}
    detector = wake_word.WakeWordDetector(FakeEngine(), on_wake=lambda: None)
    detector.start()
    seen["wake_open_after_start"] = open_streams("wake")
    toggle("on")
    start_take()
    voice._continuous_on_silence()
    toggle("off")
    seen["wake_open_after_voice_off"] = open_streams("wake")
    seen["wake_running_after_voice_off"] = detector.running
    seen["recorder_open_after_voice_off"] = open_streams("recorder")
    detector.pause()
    seen["open_after_wake_pause"] = open_streams()
    detector.resume()
    seen["wake_open_after_resume"] = open_streams("wake")
    seen["wake_streams_created"] = created("wake")
    detector.stop()
    seen["open_after_wake_stop"] = open_streams()
    return seen


SCENARIOS = (vad_take, forced_stop_then_off_while_transcribing, cancel_while_recording,
             live_capture_untouched, darwin_keeps_stream, shutdown_failure_is_logged,
             wake_keeps_its_own_stream)

if __name__ == "__main__":
    result = {"patched": hasattr(voice, "release_microphone")}
    for scenario in SCENARIOS:
        result[scenario.__name__] = scenario()
    reset()
    print(json.dumps(result, sort_keys=True))
