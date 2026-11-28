"""The microphone is released when voice mode goes off (QA30 report, 2026-11-27).

Windows listed Hermes' python.exe as «Actualmente en uso» while Iris sat idle: Hermes 0.23.3's
recorder keeps its input stream open for its lifetime, and voice.toggle off only cancelled it.
The IRIS patch release-microphone-on-voice-off (scripts/runtime_sources.json) closes it. These
tests run the real Hermes files, written from the pinned source archive with the build's own
patch code, against a fake sounddevice; the upstream bytes are run too, to show the bug they fix.
They skip when the archive is not in the local runtime cache (IRIS_RUNTIME_CACHE or
artifacts/a08-4-validation/downloads). IRIS's side is tested without Hermes: every way a take ends
sends voice.toggle off, including a wake take that heard nothing.
"""
import asyncio
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.hermes_wake import HermesWakeRuntime

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).resolve().parent / "fixtures" / "hermes_microphone_harness.py"
PATCH_ID = "release-microphone-on-voice-off"
# What the harness imports, plus the toggle handler it compiles alone.
HERMES_FILES = (
    "hermes_cli/__init__.py", "hermes_cli/voice.py", "hermes_constants.py",
    "hermes_platform/__init__.py", "hermes_platform/host/__init__.py",
    "hermes_platform/host/darwin_pci.py", "hermes_platform/host/facts.py",
    "hermes_platform/host/runtime.py", "pm/__init__.py", "pm/extras.py",
    "tools/__init__.py", "tools/voice_mode.py", "tools/voice_mode_transcript.py",
    "tools/wake_word.py", "tools/wake_word_engines.py", "tui_gateway/methods_voice.py",
)


def _pin():
    return json.loads((ROOT / "scripts/runtime_sources.json").read_text("utf-8"))["hermes"]


def _hermes_source():
    spec = importlib.util.spec_from_file_location("iris_hermes_source_for_mic", ROOT / "scripts/hermes_source.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_patch_is_pinned_to_hermes_0233_voice_files():
    pin = _pin()
    assert (pin["version"], pin["revision"]) == ("0.23.3", "caafe5389ae585ef77387a962aee05b1bcf17029")
    patch = next(item for item in pin["source_patches"] if item["id"] == PATCH_ID)
    assert {item["path"]: item["sha256"] for item in patch["files"]} == {
        "hermes_cli/voice.py": "54275dbdd02126407e1dec79296a831c664cb78ca17009677d871abe122f339f",
        "tui_gateway/methods_voice.py": "a65b73284aae8fd96abb3e6807d8955cceeed303abf23535ad6fd418dae6bf39",
    }
    assert "macOS" in patch["reason"] and "Drop it when upstream" in patch["reason"]
    _hermes_source()._validate_patches(pin)


@pytest.fixture(scope="module")
def hermes_runs(tmp_path_factory):
    """Harness results for the upstream and the patched Hermes files."""
    pin = _pin()
    cache = Path(os.environ.get("IRIS_RUNTIME_CACHE") or ROOT / "artifacts/a08-4-validation/downloads")
    archive = cache / pin["source_archive_filename"]
    if not archive.is_file():
        pytest.skip(f"Hermes source archive not in the runtime cache: {archive}")
    payload = archive.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == pin["source_archive_sha256"]
    source = _hermes_source()
    patches = source._validate_patches(pin)
    assert {"hermes_cli/voice.py", "tui_gateway/methods_voice.py"} <= patches.keys()
    prefix = pin["archive_prefix"]
    with tarfile.open(archive) as bundle:
        upstream = {name: bundle.extractfile(f"{prefix}/{name}").read() for name in HERMES_FILES}
    runs = {}
    for label in ("upstream", "patched"):
        tree = tmp_path_factory.mktemp(f"hermes-{label}")
        for name, data in upstream.items():
            if label == "patched" and name in patches:
                data = source.apply_source_patch(name, data, patches[name])  # the build's own check
            target = tree.joinpath(*name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
        env["HERMES_HOME"] = str(tree / "hermes-home")  # never the real profile
        proc = subprocess.run([sys.executable, "-I", "-B", str(HARNESS), str(tree)], cwd=tree, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=180)
        assert proc.returncode == 0, proc.stderr[-4000:]
        runs[label] = json.loads(proc.stdout.strip().splitlines()[-1])
    assert (runs["upstream"]["patched"], runs["patched"]["patched"]) == (False, True)
    return runs


def test_upstream_0233_keeps_the_microphone_open_after_voice_off(hermes_runs):
    run = hermes_runs["upstream"]
    assert run["vad_take"]["open_after_off"] == 1  # what Windows reported as «en uso»
    assert run["vad_take"]["recorder_dropped"] is False
    assert run["vad_take"]["next_take_streams_created"] == 1
    assert run["cancel_while_recording"]["open_after_off"] == 1
    assert run["forced_stop_then_off_while_transcribing"]["open_after_off"] == 1


def test_voice_off_closes_the_stream_and_the_next_take_opens_a_new_one(hermes_runs):
    take = hermes_runs["patched"]["vad_take"]
    assert take["recording_open"] == 1
    # The take ended on its own: loop inactive, stream still held until voice goes off.
    assert take["loop_active_after_take"] is False and take["open_after_take"] == 1
    assert take["enabled_after_off"] is False and take["off_again"] is False
    assert take["open_after_off"] == 0 and take["recorder_dropped"] is True
    assert take["next_take_open"] == 1 and take["next_take_streams_created"] == 2
    assert take["open_after_second_off"] == 0


def test_voice_off_while_a_forced_stop_is_still_transcribing_releases_too(hermes_runs):
    run = hermes_runs["patched"]["forced_stop_then_off_while_transcribing"]
    assert run["stopping_during_off"] is True
    assert run["enabled_after_off"] is False and run["open_after_off"] == 0
    assert run["stopping_cleared"] is True and run["statuses"][-1] == "idle"
    assert run["next_take_started"] is True and run["next_take_open"] == 1
    assert run["open_after_second_off"] == 0


def test_cancelling_a_live_take_releases_the_microphone(hermes_runs):
    run = hermes_runs["patched"]["cancel_while_recording"]
    assert run["recording_open"] == 1
    assert run["enabled_after_off"] is False and run["loop_active_after_off"] is False
    assert run["open_after_off"] == 0


def test_a_live_capture_is_never_released_under_it(hermes_runs):
    run = hermes_runs["patched"]["live_capture_untouched"]
    assert run["same_recorder"] is True and run["still_recording"] is True
    assert run["open_while_live"] == 1 and run["open_after_off"] == 0


def test_macos_keeps_upstreams_lifetime_stream(hermes_runs):
    run = hermes_runs["patched"]["darwin_keeps_stream"]
    assert run == {"open_after_off": 1, "recorder_kept": True}


def test_a_failed_release_is_logged_and_voice_still_turns_off(hermes_runs):
    run = hermes_runs["patched"]["shutdown_failure_is_logged"]
    assert run["enabled_after_off"] is False and run["recorder_dropped"] is True
    assert any("failed to release the microphone: device vanished" in line for line in run["warnings"])


@pytest.mark.parametrize("label", ["upstream", "patched"])
def test_wake_keeps_its_own_stream_only_while_it_is_on(hermes_runs, label):
    run = hermes_runs[label]["wake_keeps_its_own_stream"]
    assert run["wake_open_after_start"] == 1
    assert run["wake_open_after_voice_off"] == 1 and run["wake_running_after_voice_off"] is True
    assert run["recorder_open_after_voice_off"] == (0 if label == "patched" else 1)
    assert run["open_after_wake_pause"] == (0 if label == "patched" else 1)
    assert run["wake_open_after_resume"] == 1 and run["wake_streams_created"] == 2
    assert run["open_after_wake_stop"] == (0 if label == "patched" else 1)


# ── IRIS: every way a take ends sends voice.toggle off (the call the patch hooks) ──

def manual_runtime(*, record_status="recording"):
    runtime = HermesWakeRuntime()
    runtime.websocket = SimpleNamespace(close_code=None)
    events = []

    async def emit(kind, payload):
        events.append((kind, dict(payload)))

    async def rpc(method, params, **kwargs):
        if method == "voice.toggle":
            return {"enabled": params["action"] == "on"}
        if method == "voice.record":
            return {"status": record_status if params["action"] == "start" else "stopped"}
        if method == "wake.status":
            return {"listening": True}  # Hermes resumes its detector after a take
        return {"paused": True, "resumed": True}

    async def close():
        runtime.websocket = None
        runtime.manual_voice.disconnected()

    runtime.request = AsyncMock(side_effect=rpc)
    runtime.close = AsyncMock(side_effect=close)
    runtime.event_handler = emit
    return runtime, events


def voice_calls(runtime):
    return [(call.args[0], call.args[1].get("action")) for call in runtime.request.await_args_list
            if call.args[0].startswith("voice.")]


async def _spoken(runtime):
    await runtime.manual_voice.start("room", "one")
    await runtime._handle_native_event("voice.transcript", {"text": "abre mi agenda"})


async def _native_end(runtime, payload):
    await runtime.manual_voice.start("room", "one")
    await runtime._handle_native_event("voice.transcript", payload)


async def _idle_without_text(runtime):
    await runtime.manual_voice.start("room", "one")
    await runtime._handle_native_event("voice.status", {"state": "idle"})


async def _user_stop(runtime):
    await runtime.manual_voice.start("room", "one")
    await runtime.manual_voice.stop("room", "one")
    await runtime._handle_native_event("voice.transcript", {"text": "hola"})


async def _cancel(runtime):
    await runtime.manual_voice.start("room", "one")
    await runtime.manual_voice.cancel("room", "one")


async def _timeout(runtime):
    runtime.manual_voice.MAX_CAPTURE_SECONDS = 0
    await runtime.manual_voice.start("room", "one")
    await runtime.manual_voice._timer


async def _conversation_ended_while_arming(runtime):
    await runtime.manual_voice.start("room", "one", still_allowed=iter([True, False]).__next__)


@pytest.mark.parametrize("ending", [
    _spoken,
    lambda runtime: _native_end(runtime, {}),
    lambda runtime: _native_end(runtime, {"text": "stop", "stop_phrase": True}),
    lambda runtime: _native_end(runtime, {"no_speech_limit": True}),
    _idle_without_text, _user_stop, _cancel, _timeout, _conversation_ended_while_arming,
], ids=["spoken", "empty", "stop-phrase", "no-speech-limit", "idle", "user-stop", "cancel", "timeout",
        "conversation-ended"])
def test_every_manual_take_ends_with_voice_off(ending):
    async def scenario():
        runtime, _ = manual_runtime()
        await ending(runtime)
        calls = voice_calls(runtime)
        assert calls[:2] == [("voice.toggle", "on"), ("voice.record", "start")]
        assert calls[-1] == ("voice.toggle", "off")
        assert not runtime.manual_voice.busy
    asyncio.run(scenario())


def test_a_take_hermes_could_not_start_still_turns_voice_off():
    async def scenario():
        runtime, _ = manual_runtime(record_status="busy")
        with pytest.raises(RuntimeError, match="iniciar"):
            await runtime.manual_voice.start("room", "one")
        assert voice_calls(runtime)[-1] == ("voice.toggle", "off")
        assert runtime.close.await_count == 1
    asyncio.run(scenario())


def wake_runtime(*, fail_off=False):
    runtime, events = manual_runtime()
    runtime.listening = True
    runtime.state = "listening"
    original = runtime.request.side_effect

    async def rpc(method, params, **kwargs):
        if fail_off and method == "voice.toggle" and params["action"] == "off":
            raise RuntimeError("transport closed")
        return await original(method, params, **kwargs)

    runtime.request.side_effect = rpc
    return runtime, events


def test_a_wake_take_that_hears_nothing_turns_voice_off_once():
    async def scenario():
        runtime, _ = wake_runtime()
        await runtime._handle_native_event("wake.detected", {})
        await runtime._handle_native_event("voice.status", {"state": "idle"})
        await runtime._handle_native_event("voice.status", {"state": "idle"})
        assert voice_calls(runtime) == [("voice.toggle", "on"), ("voice.record", "start"), ("voice.toggle", "off")]
    asyncio.run(scenario())


def test_a_wake_take_at_the_no_speech_limit_turns_voice_off_once():
    async def scenario():
        runtime, events = wake_runtime()
        await runtime._handle_native_event("wake.detected", {})
        await runtime._handle_native_event("voice.transcript", {"no_speech_limit": True})
        await runtime._handle_native_event("voice.status", {"state": "idle"})
        assert voice_calls(runtime) == [("voice.toggle", "on"), ("voice.record", "start"), ("voice.toggle", "off")]
        assert [call.args[0] for call in runtime.request.await_args_list] == [
            "voice.toggle", "voice.record", "voice.toggle", "wake.status"]
        assert ("voice.transcript", {"no_speech_limit": True}) in events
    asyncio.run(scenario())


def test_a_spoken_wake_take_releases_once_and_its_late_idle_adds_nothing():
    async def scenario():
        runtime, _ = wake_runtime()
        await runtime._handle_native_event("wake.detected", {})
        await runtime._handle_native_event("voice.transcript", {"text": "abre mi agenda"})
        await runtime._handle_native_event("voice.status", {"state": "idle"})
        assert voice_calls(runtime) == [("voice.toggle", "on"), ("voice.record", "start"), ("voice.toggle", "off")]
        assert runtime.listening is True
    asyncio.run(scenario())


def test_a_failed_release_after_an_empty_wake_take_is_logged_not_raised(caplog):
    async def scenario():
        runtime, _ = wake_runtime(fail_off=True)
        await runtime._handle_native_event("wake.detected", {})
        await runtime._handle_native_event("voice.transcript", {})
        assert runtime.state == "listening"
    with caplog.at_level("WARNING", logger="IRIS.hermes_wake"):
        asyncio.run(scenario())
    assert "empty wake take failed: transport closed" in caplog.text
