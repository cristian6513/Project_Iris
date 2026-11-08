"""A saved voice speaks the next reply: Core outlives the Wizard that changes it."""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

import core.extras as extras
import core.tts as tts_module
from core.config import IRISConfig, get_iris_home
from core.tts import IsolatedExtraTTSProvider, TextToSpeech

built: list[Engine] = []


class Engine:
    sound = b""
    fails_to_start = False

    def __init__(self, *args) -> None:
        self.args = args
        self.closed = False
        self._available = True
        built.append(self)

    async def initialize(self) -> None:
        if self.fails_to_start:
            raise RuntimeError("synthetic engine failure")

    async def synthesize(self, text: str):
        return self.sound, []

    async def close(self) -> None:
        self.closed = True


class Edge(Engine):
    sound = b"edge"


class Fish(Engine):
    sound = b"fish"


@pytest.fixture(autouse=True)
def engines(monkeypatch, tmp_path):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "iris"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv("IRIS_RUNTIME_DESCRIPTOR", raising=False)
    monkeypatch.setattr(tts_module, "EdgeTTSProvider", Edge)
    monkeypatch.setattr(tts_module, "FishAudioTTSProvider", Fish)
    monkeypatch.setattr(Fish, "sound", b"fish")
    monkeypatch.setattr(Fish, "fails_to_start", False)
    built.clear()
    save(tts_provider="edge-tts")


def save(**fields) -> None:
    """What the Wizard does: another copy of the settings, written to the profile."""
    config = IRISConfig.load()
    for name, value in fields.items():
        setattr(config, name, value)
    config.save()


FISH = {
    "tts_provider": "fish-audio",
    "fish_audio_api_key": "synthetic-key",
    "fish_audio_voice_id": "a" * 32,
}


async def running_core() -> TextToSpeech:
    tts = TextToSpeech(IRISConfig.load())
    await tts.initialize()
    return tts


async def spoken(tts: TextToSpeech) -> bytes:
    return (await tts.speak("Hola"))[0]


def test_a_voice_saved_while_core_runs_speaks_the_next_reply() -> None:
    async def scenario() -> None:
        tts = await running_core()
        assert await spoken(tts) == b"edge"

        save(**FISH)

        assert await spoken(tts) == b"fish"

    asyncio.run(scenario())


def test_a_new_key_for_the_same_voice_engine_replaces_the_old_one() -> None:
    save(**FISH)

    async def scenario() -> None:
        tts = await running_core()
        save(fish_audio_api_key="synthetic-key-2")
        await spoken(tts)
        assert built[-1].args[0] == "synthetic-key-2"

    asyncio.run(scenario())


def test_the_replaced_engine_is_closed_so_its_worker_does_not_linger() -> None:
    async def scenario() -> None:
        tts = await running_core()
        first = built[0]
        save(**FISH)
        await spoken(tts)
        assert first.closed

    asyncio.run(scenario())


def test_saving_something_else_keeps_the_running_engine() -> None:
    async def scenario() -> None:
        tts = await running_core()
        save(assistant_name="Otra")
        assert await spoken(tts) == b"edge"
        assert len(built) == 1

    asyncio.run(scenario())


def test_two_replies_at_once_build_the_new_voice_only_once() -> None:
    async def scenario() -> None:
        tts = await running_core()
        save(**FISH)
        assert await asyncio.gather(spoken(tts), spoken(tts)) == [b"fish", b"fish"]
        assert len([engine for engine in built if isinstance(engine, Fish)]) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("broken", ["{", "[]"])
def test_an_unreadable_profile_keeps_the_chosen_voice_instead_of_the_defaults(broken: str) -> None:
    """IRISConfig.load() answers defaults here, and the default engine is remote Edge-TTS."""
    save(**FISH)

    async def scenario() -> None:
        tts = await running_core()
        path = get_iris_home() / "config.json"
        healthy = path.read_text(encoding="utf-8")

        path.write_text(broken, encoding="utf-8")
        assert await spoken(tts) == b"fish"
        assert len(built) == 1

        path.write_text(healthy.replace("synthetic-key", "synthetic-key-2"), encoding="utf-8")
        assert await spoken(tts) == b"fish"
        assert built[-1].args[0] == "synthetic-key-2"

    asyncio.run(scenario())


def test_a_saved_voice_that_cannot_start_never_falls_back_to_the_old_one(monkeypatch) -> None:
    async def scenario() -> None:
        tts = await running_core()
        monkeypatch.setattr(Fish, "fails_to_start", True)
        save(**FISH)
        assert await spoken(tts) == b"FALLBACK_FAILED"

        monkeypatch.setattr(Fish, "fails_to_start", False)
        save(fish_audio_voice_id="b" * 32)
        assert await spoken(tts) == b"fish"

    asyncio.run(scenario())


def test_the_optional_edge_fallback_uses_the_voice_saved_later(monkeypatch) -> None:
    save(**FISH, tts_allow_remote_fallback=True)
    monkeypatch.setattr(Fish, "sound", b"")

    async def scenario() -> None:
        tts = await running_core()
        assert await spoken(tts) == b"edge"
        original_fallback = built[-1]

        save(edge_tts_voice="es-ES-ElviraNeural")

        assert await spoken(tts) == b"edge"
        assert built[-1].args == ("es-ES-ElviraNeural",)
        assert original_fallback.closed

    asyncio.run(scenario())


def test_a_reply_with_nothing_to_say_does_not_touch_the_engine() -> None:
    async def scenario() -> None:
        tts = await running_core()
        save(**FISH)
        assert await tts.speak("") == (b"", [])
        assert len(built) == 1

    asyncio.run(scenario())


def damage(edit) -> None:
    path = get_iris_home() / "config.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("edit", [
    lambda data: data.update(tts_provider="corrupted-provider"),
    lambda data: data.pop("tts_provider"),
    lambda data: data.update(fish_audio_voice_id=12345),
], ids=["invalid provider", "no provider", "invalid voice field"])
def test_a_damaged_voice_setting_never_chooses_another_voice(edit) -> None:
    """Valid JSON with a recovered default is still not something the user chose."""
    save(**FISH)

    async def scenario() -> None:
        tts = await running_core()
        damage(edit)
        assert await spoken(tts) == b"fish"
        assert len(built) == 1

        save(**FISH, fish_audio_model="s2-pro")
        assert await spoken(tts) == b"fish"
        assert len(built) == 2

    asyncio.run(scenario())


def test_a_damaged_setting_elsewhere_does_not_hold_the_voice_back() -> None:
    async def scenario() -> None:
        tts = await running_core()
        save(**FISH)
        damage(lambda data: data.update(websocket_port="not a port"))
        assert await spoken(tts) == b"fish"

    asyncio.run(scenario())


class Workers:
    """The real selector and isolated provider over a synthetic worker transport."""

    def __init__(self, monkeypatch) -> None:
        self.started: list = []
        self.loaded_during_load: list[int] = []
        self.loading = asyncio.Event()
        self.hold_load = False
        self.release_load = asyncio.Event()
        self.closing = asyncio.Event()
        self.hold_close = False
        self.release_close = asyncio.Event()
        fleet = self

        class Worker:
            def __init__(self, environment) -> None:
                self.running = False
                self.loaded = False
                fleet.started.append(self)

            async def start(self) -> None:
                self.running = True

            async def request(self, request, timeout):
                if request["op"] != "load":
                    return {"ok": True, "audio": base64.b64encode(b"local").decode(), "visemes": []}
                fleet.loaded_during_load.append(sum(worker.loaded for worker in fleet.started))
                fleet.loading.set()
                if fleet.hold_load:
                    await fleet.release_load.wait()
                self.loaded = True
                return {"ok": True}

            async def close(self) -> None:
                fleet.closing.set()
                if fleet.hold_close:
                    await fleet.release_close.wait()
                self.running = False
                self.loaded = False

        monkeypatch.setattr(extras, "ExtraWorker", Worker)
        monkeypatch.setattr(
            TextToSpeech, "_local_engine_provider",
            lambda tts, name: IsolatedExtraTTSProvider(name, tts._local_engine_options(name), object()),
        )

    def running(self) -> int:
        return sum(worker.running for worker in self.started)


async def local_core() -> TextToSpeech:
    save(tts_provider="hf-s2s", hf_s2s_model="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice", hf_s2s_speaker="Aiden")
    return await running_core()


def test_the_old_local_model_is_released_before_the_new_one_loads(monkeypatch) -> None:
    async def scenario() -> None:
        fleet = Workers(monkeypatch)
        tts = await local_core()
        save(hf_s2s_speaker="Ryan")
        assert await spoken(tts) == b"local"
        assert fleet.loaded_during_load == [0, 0]
        assert fleet.running() == 1
        await tts.close()
        assert fleet.running() == 0

    asyncio.run(scenario())


def test_stopping_while_the_new_voice_loads_leaves_no_engine_running(monkeypatch) -> None:
    async def scenario() -> None:
        fleet = Workers(monkeypatch)
        tts = await local_core()
        save(hf_s2s_speaker="Ryan")
        fleet.loading.clear()
        fleet.hold_load = True
        turn = asyncio.create_task(spoken(tts))
        await asyncio.wait_for(fleet.loading.wait(), 2)

        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn
        assert fleet.running() == 0

        fleet.hold_load = False
        assert await spoken(tts) == b"local", "the next reply starts the saved voice"
        assert tts.config.hf_s2s_speaker == "Ryan"
        await tts.close()
        assert fleet.running() == 0

    asyncio.run(scenario())


def test_stopping_twice_while_the_old_engine_closes_still_closes_it(monkeypatch) -> None:
    async def scenario() -> None:
        fleet = Workers(monkeypatch)
        tts = await local_core()
        save(hf_s2s_speaker="Ryan")
        fleet.hold_close = True
        turn = asyncio.create_task(spoken(tts))
        await asyncio.wait_for(fleet.closing.wait(), 2)

        turn.cancel()
        await asyncio.sleep(0)
        turn.cancel()
        await asyncio.sleep(0)
        assert not turn.done(), "the stop waits for the worker it would otherwise lose"

        fleet.release_close.set()
        with pytest.raises(asyncio.CancelledError):
            await turn
        assert fleet.running() == 0

        fleet.hold_close = False
        assert await spoken(tts) == b"local"
        await tts.close()

    asyncio.run(scenario())
