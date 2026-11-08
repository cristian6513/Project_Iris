from __future__ import annotations

import asyncio
import contextlib
import io
import json
import re
import tempfile
import wave
from pathlib import Path
from typing import get_args
from unittest.mock import AsyncMock, patch

import numpy as np

from starlette.datastructures import UploadFile
from starlette.requests import Request
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

from core.config import IRISConfig
from core.tts import (
    LOCAL_ENGINE_PROVIDERS,
    CoquiTTSProvider,
    FishSpeechTTSProvider,
    HFSpeechToSpeechQwenProvider,
    KokoroTTSProvider,
    NeuTTSProvider,
    TextToSpeech,
    _kokoro_lang_code,
)
from wizard.app import (
    ConfigUpdate,
    _clone_reference_paths,
    _clone_wav_duration,
    _local_service_url_error,
    _merge_config_update,
    _minimax_clone_voice_id,
    _tts_config_error,
    _validate_clone_duration,
    app,
    clone_voice,
)


ROOT = Path(__file__).resolve().parents[1]


def _action_request(intent: str, *, idempotency_key: str = "") -> Request:
    headers = [
        (b"x-iris-intent", intent.encode("ascii")),
        (b"origin", b"http://127.0.0.1:5177"),
        (b"sec-fetch-site", b"same-origin"),
    ]
    if idempotency_key:
        headers.append((b"idempotency-key", idempotency_key.encode("ascii")))
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 5177),
        "scheme": "http",
    })


def _wav_bytes(seconds: float, sample_rate: int = 24_000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * int(seconds * sample_rate))
    return output.getvalue()


def test_local_clone_reference_does_not_overwrite_an_existing_voice() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        original_wav = directory / "Mi_voz_fish.wav"
        original_txt = directory / "Mi_voz_fish.txt"
        original_wav.write_bytes(b"original")
        original_txt.write_text("original", encoding="utf-8")

        wav_path, txt_path = _clone_reference_paths(
            directory, "Mi voz", "fish", transcript=True,
        )

        assert wav_path != original_wav
        assert txt_path != original_txt
        assert not wav_path.exists()
        assert txt_path is not None and not txt_path.exists()
        assert original_wav.read_bytes() == b"original"
        assert original_txt.read_text(encoding="utf-8") == "original"


def test_tts_provider_catalog_matches_config_and_runtime_dispatch() -> None:
    """A voice card must never persist a provider the agent cannot run."""
    html = (ROOT / "wizard" / "ui" / "index.html").read_text(encoding="utf-8")
    runtime = (ROOT / "core" / "tts.py").read_text(encoding="utf-8")
    ui_providers = set(re.findall(r'name="tts" value="([^"]+)"', html))
    configured_providers = set(get_args(IRISConfig.model_fields["tts_provider"].annotation))

    assert ui_providers == configured_providers
    for provider in configured_providers:
        # Local engines dispatch through their approved extra (A08.5).
        assert f'name == "{provider}"' in runtime or provider in LOCAL_ENGINE_PROVIDERS


def test_clone_upload_must_be_real_pcm_wav() -> None:
    assert round(_clone_wav_duration(_wav_bytes(3.5)), 1) == 3.5
    try:
        _clone_wav_duration(b"RIFF" + b"not-a-wave")
    except ValueError as exc:
        assert "WAV" in str(exc)
    else:
        raise AssertionError("A renamed non-WAV upload must be rejected")


def test_provider_specific_clone_durations_match_real_contracts() -> None:
    _validate_clone_duration("neutts", 3.0)
    _validate_clone_duration("hf-s2s", 3.0)
    _validate_clone_duration("fish-speech", 30.0)
    _validate_clone_duration("minimax", 10.0)
    _validate_clone_duration("minimax", 300.0)
    for provider, seconds in (
        ("neutts", 16.0),
        ("hf-s2s", 16.0),
        ("fish-speech", 9.0),
        ("minimax", 9.9),
        ("minimax", 300.1),
    ):
        try:
            _validate_clone_duration(provider, seconds)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{provider} accepted an invalid duration")


def test_minimax_clone_id_is_valid_and_collision_resistant() -> None:
    first = _minimax_clone_voice_id("Voz de papá")
    second = _minimax_clone_voice_id("Voz de papá")
    assert first[0].isalpha()
    assert 8 <= len(first) <= 256
    assert all(ch.isalnum() or ch in "-_" for ch in first)
    assert not first.endswith(("-", "_"))
    assert first != second


def test_coqui_clone_reference_reaches_xtts_synthesis() -> None:
    calls: list[dict[str, object]] = []

    class FakeTTS:
        def tts_to_file(self, **kwargs: object) -> None:
            calls.append(kwargs)
            Path(str(kwargs["file_path"])).write_bytes(b"RIFF-test")

    with tempfile.TemporaryDirectory() as tmp:
        reference = Path(tmp) / "voice.wav"
        reference.write_bytes(_wav_bytes(3.0))
        provider = CoquiTTSProvider(
            "tts_models/multilingual/multi-dataset/xtts_v2",
            str(reference),
            "es",
        )
        provider._tts = FakeTTS()
        audio, markers = asyncio.run(provider.synthesize("Hola"))

    assert audio == b"RIFF-test"
    assert markers == []
    assert calls[0]["speaker_wav"] == str(reference)
    assert calls[0]["language"] == "es"


def test_fish_speech_uses_references_with_audio_and_text() -> None:
    requests: list[dict[str, object]] = []

    class Response:
        status_code = 200
        content = b"RIFF-fish"
        text = ""

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> Response:
            requests.append({"url": url, **kwargs})
            return Response()

    with tempfile.TemporaryDirectory() as tmp:
        reference = Path(tmp) / "fish.wav"
        reference.write_bytes(_wav_bytes(10.0))
        provider = FishSpeechTTSProvider(
            reference_audio=str(reference),
            reference_text="Esta es la referencia exacta.",
        )
        with patch("httpx.AsyncClient", return_value=Client()):
            audio, _ = asyncio.run(provider.synthesize("Hola"))

    payload = requests[0]["json"]
    assert audio == b"RIFF-fish"
    assert "reference_audio" not in payload
    assert payload["references"][0]["text"] == "Esta es la referencia exacta."
    assert payload["references"][0]["audio"]


def test_neutts_clone_requires_transcript_before_writing() -> None:
    upload = UploadFile(io.BytesIO(_wav_bytes(3.0)), filename="sample.wav")
    response = asyncio.run(clone_voice(
        request=_action_request("clone-voice"),
        audio=upload,
        name="IRIS",
        provider="neutts",
        transcript="",
        consent_confirmed=True,
    ))
    body = json.loads(response.body)
    assert response.status_code == 400
    assert "transcripción exacta" in body["error"]


def test_clone_voice_reuses_an_idempotent_result_instead_of_charging_twice() -> None:
    async def scenario():
        first = await clone_voice(
            request=_action_request("clone-voice", idempotency_key="same-operation"),
            audio=UploadFile(io.BytesIO(_wav_bytes(3.0)), filename="first.wav"),
            consent_confirmed=True,
        )
        second = await clone_voice(
            request=_action_request("clone-voice", idempotency_key="same-operation"),
            audio=UploadFile(io.BytesIO(_wav_bytes(3.0)), filename="retry.wav"),
            consent_confirmed=True,
        )
        return first, second

    expected = {"status": "ok", "voice_id": "one-paid-operation"}
    with patch(
        "wizard.app._clone_voice_impl",
        new=AsyncMock(return_value=JSONResponse(expected)),
    ) as implementation:
        first, second = asyncio.run(scenario())

    assert json.loads(first.body) == expected
    assert json.loads(second.body) == expected
    implementation.assert_awaited_once()


def test_hf_s2s_qwen_adapter_uses_reviewed_handler_and_returns_wav() -> None:
    setup_calls: list[dict[str, object]] = []

    class FakeInput:
        def __init__(self, text: str, language_code: str) -> None:
            self.text = text
            self.language_code = language_code

    class FakeHandler:
        def __init__(self, *_args: object, setup_kwargs: dict[str, object], **_kwargs: object) -> None:
            setup_calls.append(setup_kwargs)

        def process(self, message: FakeInput):
            assert message.text == "Hola desde IRIS"
            assert message.language_code == "es"
            yield b"\x00\x00\x01\x00"

    provider = HFSpeechToSpeechQwenProvider(
        model_name="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        speaker="Aiden",
        language="spanish",
    )
    with patch(
        "core.tts_engines._load_hf_s2s_qwen_runtime",
        return_value=(FakeHandler, FakeInput),
    ), patch(
        # The upstream handler serves CUDA (and GGML off Windows); Windows CPU
        # takes the direct qwen_tts path below.
        "core.tts_engines._hf_s2s_device",
        return_value=("cuda", "torch", "BF16"),
    ):
        asyncio.run(provider.initialize())
        audio, markers = asyncio.run(provider.synthesize("Hola desde IRIS"))
        engine = TextToSpeech(IRISConfig(tts_provider="hf-s2s"))
        asyncio.run(engine.initialize())
        assert isinstance(engine._provider, HFSpeechToSpeechQwenProvider)

    assert audio[:4] == b"RIFF"
    assert markers == []
    with wave.open(io.BytesIO(audio), "rb") as wav:
        assert wav.getframerate() == 16_000
        assert wav.getnchannels() == 1
    assert setup_calls[0]["model_name"] == "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    assert setup_calls[0]["speaker"] == "Aiden"
    assert setup_calls[0]["device"] == "cuda"
    assert setup_calls[0]["dtype"] == "auto"
    assert setup_calls[0]["non_streaming_mode"] is True


def test_hf_s2s_on_windows_cpu_clones_through_qwen_tts_with_one_reference_encoding(tmp_path: Path) -> None:
    # The approved Windows lock has CPU-only torch: faster-qwen3-tts's torch
    # backend demands CUDA graphs, so the provider must not touch that handler.
    reference = tmp_path / "voz.wav"
    reference.write_bytes(_wav_bytes(3.0))
    calls: list[tuple[str, object]] = []

    class FakeModel:
        def create_voice_clone_prompt(self, *, ref_audio: str, ref_text: str) -> str:
            calls.append(("prompt", (ref_audio, ref_text)))
            return "prompt-items"

        def generate_voice_clone(self, *, text: str, language: str, voice_clone_prompt: str,
                                 non_streaming_mode: bool) -> tuple[list[np.ndarray], int]:
            calls.append(("clone", (text, language, voice_clone_prompt, non_streaming_mode)))
            return [np.full(240, 0.5, dtype=np.float32)], 24_000

    provider = HFSpeechToSpeechQwenProvider(
        model_name="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        language="spanish",
        reference_audio=str(reference),
        reference_text="Texto exacto de la muestra.",
    )
    with patch("core.tts_engines._hf_s2s_device", return_value=("cpu", "torch", "BF16")), patch(
        "core.tts_engines._load_qwen_tts_model", return_value=FakeModel(),
    ) as load, patch(
        "core.tts_engines._load_hf_s2s_qwen_runtime", side_effect=AssertionError("CUDA-graph handler"),
    ):
        asyncio.run(provider.initialize())
        audio, markers = asyncio.run(provider.synthesize("Primera frase. ¿Segunda frase?"))

    load.assert_called_once_with("Qwen/Qwen3-TTS-12Hz-1.7B-Base")
    assert [name for name, _ in calls] == ["prompt", "clone"]
    assert calls[0][1] == (str(reference), "Texto exacto de la muestra.")
    assert calls[1][1] == ("Primera frase. ¿Segunda frase?", "spanish", "prompt-items", True)
    assert provider.seconds_per_char == 3.0
    assert markers == []
    with wave.open(io.BytesIO(audio), "rb") as wav:
        assert wav.getframerate() == 24_000 and wav.getnchannels() == 1 and wav.getnframes() == 240
        assert int.from_bytes(wav.readframes(1), "little", signed=True) == int(0.5 * 32767)


def test_hf_s2s_cpu_path_splits_long_replies_and_serves_preset_voices() -> None:
    texts: list[str] = []

    class FakeModel:
        def generate_custom_voice(self, *, text: str, speaker: str, language: str,
                                  non_streaming_mode: bool) -> tuple[list[np.ndarray], int]:
            assert speaker == "Aiden" and language == "spanish"
            texts.append(text)
            return [np.zeros(10, dtype=np.float32)], 24_000

    provider = HFSpeechToSpeechQwenProvider(model_name="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice")
    long_reply = " ".join(f"Esta es la frase número {index} de una respuesta larga." for index in range(12))
    with patch("core.tts_engines._hf_s2s_device", return_value=("cpu", "torch", "BF16")), patch(
        "core.tts_engines._load_qwen_tts_model", return_value=FakeModel(),
    ):
        asyncio.run(provider.initialize())
        audio, _ = asyncio.run(provider.synthesize(long_reply))

    assert len(texts) > 1 and all(len(text) <= 220 for text in texts)
    assert " ".join(texts) == long_reply
    assert provider.seconds_per_char == 2.0
    with wave.open(io.BytesIO(audio), "rb") as wav:
        assert wav.getnframes() == 10 * len(texts)


def test_qwen_tts_rope_patch_reads_the_transformers_5_rope_parameters() -> None:
    import sys
    import types

    from core.tts_engines import _patch_qwen_tts_rope

    def upstream(config, device=None, seq_len=None, layer_type=None):
        return config.rope_theta, 1.0   # what qwen-tts-hf 0.1.1.post1 reads

    compat = types.ModuleType("qwen_tts._transformers_compat")
    compat._default_rope_parameters = upstream
    registry: dict[str, object] = {}
    compat.patch_transformers_rope_registry = lambda: registry.setdefault("default", compat._default_rope_parameters)
    rope_utils = types.ModuleType("transformers.modeling_rope_utils")
    rope_utils.ROPE_INIT_FUNCTIONS = registry
    modules = {"qwen_tts": types.ModuleType("qwen_tts"), "qwen_tts._transformers_compat": compat,
               "transformers": types.ModuleType("transformers"), "transformers.modeling_rope_utils": rope_utils}
    with patch.dict(sys.modules, modules):
        _patch_qwen_tts_rope()
        _patch_qwen_tts_rope()   # idempotent: never wraps twice

    class MimiConfig:   # Transformers 5.x keeps theta inside rope_parameters
        rope_parameters = {"rope_theta": 10000.0}

    assert registry["default"] is compat._default_rope_parameters
    assert registry["default"](MimiConfig()) == (10000.0, 1.0)
    assert compat._default_rope_parameters.__closure__[0].cell_contents is upstream


def _preview_race(scenario, tmp_path: Path, *, fail_load: bool = False) -> dict:
    """Run the real preview endpoint against fake engines whose load can be held open.

    Codex C01 (2026-10-20): loads in flight escaped the cache, so a concurrent
    preview or a clone during a load could leave an engine open or publish a
    retired one. Every engine created here is recorded with its final state.
    """
    from collections import OrderedDict

    from wizard import app as wizard_app

    created: list = []
    entered: "asyncio.Queue" = asyncio.Queue()
    release = asyncio.Event()

    class Engine:
        def __init__(self, cfg: IRISConfig) -> None:
            self.closed = False
            self.reference = cfg.hf_s2s_ref_audio
            created.append(self)

        async def initialize(self) -> None:
            entered.put_nowait(self)
            await release.wait()
            if fail_load:
                raise RuntimeError("sin memoria")

        async def speak(self, _text: str) -> tuple[bytes, list]:
            return _wav_bytes(0.1), []

        async def close(self) -> None:
            self.closed = True

    reference = tmp_path / "ref.wav"
    reference.write_bytes(_wav_bytes(3.0))
    config = IRISConfig(hf_s2s_ref_audio=str(reference), hf_s2s_ref_text="exacto")
    payload = wizard_app.TTSPreviewRequest(provider="hf-s2s", model="Qwen/Qwen3-TTS-12Hz-0.6B-Base", text="Hola")
    cache: OrderedDict = OrderedDict()
    loading: dict = {}

    async def preview():
        return await wizard_app.test_tts_audio(_action_request("preview-tts"), payload, tts_api_key="")

    async def run() -> dict:
        with patch("core.tts.TextToSpeech", Engine), patch.object(wizard_app.IRISConfig, "load", return_value=config), \
                patch.object(wizard_app, "_TTS_PREVIEW_CACHE", cache), \
                patch.object(wizard_app, "_TTS_PREVIEW_LOADING", loading), \
                patch.object(wizard_app, "_TTS_PREVIEW_EPOCH", {}), \
                patch.object(wizard_app, "_TTS_PREVIEW_LOCK", asyncio.Lock()):
            statuses = await scenario(preview, entered, release, wizard_app)
            result = {
                "statuses": statuses, "created": len(created), "cached": len(cache), "loading": len(loading),
                "closed": sum(engine.closed for engine in created),
                "open_outside_cache": sum(not engine.closed and engine not in cache.values() for engine in created),
            }
            for engine in created:
                await engine.close()
            return result

    return asyncio.run(run())


def test_preview_cache_sequential_control_reuses_one_engine_and_invalidation_closes_it(tmp_path: Path) -> None:
    async def scenario(preview, entered, release, wizard_app):
        first = asyncio.create_task(preview())
        await asyncio.wait_for(entered.get(), 2)
        release.set()
        statuses = [(await first).status_code, (await preview()).status_code]
        await wizard_app._invalidate_tts_preview_provider("hf-s2s")
        return statuses

    result = _preview_race(scenario, tmp_path)
    assert result == {"statuses": [200, 200], "created": 1, "cached": 0, "loading": 0,
                      "closed": 1, "open_outside_cache": 0}


def test_two_previews_of_the_same_voice_share_one_load(tmp_path: Path) -> None:
    async def scenario(preview, entered, release, wizard_app):
        first = asyncio.create_task(preview())
        await asyncio.wait_for(entered.get(), 2)
        second = asyncio.create_task(preview())
        await asyncio.sleep(0.05)      # the second request is waiting on the same load
        assert entered.empty()
        release.set()
        return [response.status_code for response in await asyncio.gather(first, second)]

    result = _preview_race(scenario, tmp_path)
    assert result == {"statuses": [200, 200], "created": 1, "cached": 1, "loading": 0,
                      "closed": 0, "open_outside_cache": 0}


def test_cloning_while_a_preview_loads_retires_and_closes_that_load(tmp_path: Path) -> None:
    async def scenario(preview, entered, release, wizard_app):
        first = asyncio.create_task(preview())
        await asyncio.wait_for(entered.get(), 2)
        await wizard_app._invalidate_tts_preview_provider("hf-s2s")   # a new clone saved
        release.set()
        response = await first
        assert "La voz cambió" in json.loads(response.body)["message"]
        return [response.status_code]

    result = _preview_race(scenario, tmp_path)
    # The retired load was closed and never published into the cache.
    assert result == {"statuses": [409], "created": 1, "cached": 0, "loading": 0,
                      "closed": 1, "open_outside_cache": 0}


def test_a_load_that_finishes_after_its_provider_was_retired_is_closed_not_published(tmp_path: Path) -> None:
    async def scenario(preview, entered, release, wizard_app):
        first = asyncio.create_task(preview())
        await asyncio.wait_for(entered.get(), 2)
        # The epoch alone, without cancelling: the guard for a load past its last await.
        wizard_app._TTS_PREVIEW_EPOCH["hf-s2s"] = wizard_app._TTS_PREVIEW_EPOCH.get("hf-s2s", 0) + 1
        release.set()
        return [(await first).status_code]

    result = _preview_race(scenario, tmp_path)
    assert result == {"statuses": [409], "created": 1, "cached": 0, "loading": 0,
                      "closed": 1, "open_outside_cache": 0}


def test_an_abandoned_preview_never_cancels_the_load_another_one_awaits(tmp_path: Path) -> None:
    async def scenario(preview, entered, release, wizard_app):
        first = asyncio.create_task(preview())
        await asyncio.wait_for(entered.get(), 2)
        second = asyncio.create_task(preview())
        await asyncio.sleep(0.05)
        first.cancel()                  # e.g. its tab was closed
        with contextlib.suppress(asyncio.CancelledError):
            await first
        release.set()
        return [(await second).status_code]

    result = _preview_race(scenario, tmp_path)
    assert result == {"statuses": [200], "created": 1, "cached": 1, "loading": 0,
                      "closed": 0, "open_outside_cache": 0}


def test_a_failed_preview_load_closes_its_engine_and_reports_it(tmp_path: Path) -> None:
    async def scenario(preview, entered, release, wizard_app):
        first = asyncio.create_task(preview())
        await asyncio.wait_for(entered.get(), 2)
        release.set()
        response = await first
        assert "RuntimeError: sin memoria" in json.loads(response.body)["message"]
        return [response.status_code]

    # Before C01 the half-loaded engine (and its worker) stayed open.
    result = _preview_race(scenario, tmp_path, fail_load=True)
    assert result == {"statuses": [503], "created": 1, "cached": 0, "loading": 0,
                      "closed": 1, "open_outside_cache": 0}


def test_a_new_clone_closes_the_previous_preview_engines_instead_of_leaking_them() -> None:
    from wizard import app as wizard_app

    class Engine:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    qwen_small, qwen_large, edge = Engine(), Engine(), Engine()
    cache = {("hf-s2s", "0.6B"): qwen_small, ("hf-s2s", "1.7B"): qwen_large, ("edge-tts", "es-MX"): edge}
    with patch.dict(wizard_app._TTS_PREVIEW_CACHE, cache, clear=True):
        asyncio.run(wizard_app._invalidate_tts_preview_provider("hf-s2s"))
        assert list(wizard_app._TTS_PREVIEW_CACHE) == [("edge-tts", "es-MX")]

    assert qwen_small.closed and qwen_large.closed   # their workers and models are released
    assert not edge.closed


def test_tts_preview_shows_why_a_local_engine_produced_no_audio(tmp_path: Path) -> None:
    class FailingEngine:
        last_error = "Qwen3-TTS no pudo cargar Qwen/Qwen3-TTS-12Hz-0.6B-Base: ValueError: sin memoria"

        def __init__(self, _config: IRISConfig) -> None:
            pass

        async def initialize(self) -> None:
            pass

        async def speak(self, _text: str) -> tuple[bytes, list]:
            return b"", []

        async def close(self) -> None:
            pass

    reference = tmp_path / "ref.wav"
    reference.write_bytes(_wav_bytes(3.0))
    config = IRISConfig(hf_s2s_ref_audio=str(reference), hf_s2s_ref_text="exacto")
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 51999))
    with patch("wizard.app.IRISConfig.load", return_value=config), patch(
        "core.tts.TextToSpeech", FailingEngine,
    ), patch.dict("wizard.app._TTS_PREVIEW_CACHE", clear=True):
        response = client.post(
            "/api/test-tts-audio",
            json={"provider": "hf-s2s", "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base", "text": "Hola"},
            headers={"X-IRIS-Intent": "preview-tts", "Origin": "http://127.0.0.1:8000"},
        )

    assert response.status_code == 502
    assert response.json()["message"] == (
        "El proveedor 'hf-s2s' no produjo audio: "
        "Qwen3-TTS no pudo cargar Qwen/Qwen3-TTS-12Hz-0.6B-Base: ValueError: sin memoria"
    )


def test_hf_s2s_clone_persists_reference_and_switches_to_base() -> None:
    class FakeConfig:
        # The persisted size intentionally differs from the unsaved Wizard
        # selection to guard against cloning with stale configuration.
        hf_s2s_model = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
        hf_s2s_ref_audio = ""
        hf_s2s_ref_text = ""
        hf_s2s_language = "spanish"
        saved = False

        def save(self) -> None:
            self.saved = True

    config = FakeConfig()
    with tempfile.TemporaryDirectory() as tmp:
        upload = UploadFile(io.BytesIO(_wav_bytes(3.0)), filename="sample.wav")
        with patch("wizard.app.IRISConfig.load", return_value=config), patch(
            "wizard.app.get_iris_home", return_value=Path(tmp)
        ):
            response = asyncio.run(clone_voice(
                request=_action_request("clone-voice"),
                audio=upload,
                name="Voz autorizada",
                provider="hf-s2s",
                model="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
                transcript="Esta es la transcripción exacta.",
                consent_confirmed=True,
            ))

        body = json.loads(response.body)
        assert response.status_code == 200
        assert body["model"] == "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
        assert Path(body["voice_id"]).is_file()
        assert Path(body["voice_id"]).with_suffix(".txt").is_file()
        assert config.hf_s2s_ref_text == "Esta es la transcripción exacta."
        assert config.saved is True

        invalid_home = Path(tmp) / "invalid-model"
        invalid_upload = UploadFile(io.BytesIO(_wav_bytes(3.0)), filename="sample.wav")
        with patch("wizard.app.IRISConfig.load", return_value=config), patch(
            "wizard.app.get_iris_home", return_value=invalid_home
        ):
            invalid_response = asyncio.run(clone_voice(
                request=_action_request("clone-voice"),
                audio=invalid_upload,
                name="No debe escribirse",
                provider="hf-s2s",
                model="Qwen/modelo-no-validado",
                transcript="Texto exacto.",
                consent_confirmed=True,
            ))
        assert invalid_response.status_code == 400
        assert not invalid_home.exists()


def test_minimax_clone_uses_upload_then_official_clone_endpoint() -> None:
    calls: list[dict[str, object]] = []

    class FakeConfig:
        minimax_api_key = ""
        minimax_voice_id = "Spanish_Wiselady"
        saved = False

        def save(self) -> None:
            self.saved = True

    class Response:
        def __init__(self, body: dict[str, object]) -> None:
            self.status_code = 200
            self._body = body
            self.text = json.dumps(body)

        def json(self) -> dict[str, object]:
            return self._body

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> Response:
            calls.append({"url": url, **kwargs})
            if url.endswith("/files/upload"):
                return Response({"file": {"file_id": 123}})
            return Response({"base_resp": {"status_code": 0, "status_msg": "success"}})

    config = FakeConfig()
    upload = UploadFile(io.BytesIO(_wav_bytes(10.0)), filename="sample.wav")
    with patch("wizard.app.IRISConfig.load", return_value=config), patch(
        "httpx.AsyncClient", return_value=Client()
    ):
        response = asyncio.run(clone_voice(
            request=_action_request("clone-voice"),
            audio=upload,
            name="Voz IRIS",
            provider="minimax",
            api_key="secret",
            consent_confirmed=True,
        ))

    body = json.loads(response.body)
    assert response.status_code == 200
    assert calls[0]["url"] == "https://api.minimax.io/v1/files/upload"
    assert calls[0]["data"] == {"purpose": "voice_clone"}
    assert calls[1]["url"] == "https://api.minimax.io/v1/voice_clone"
    assert calls[1]["json"]["file_id"] == 123
    assert calls[1]["json"]["voice_id"] == body["voice_id"]
    assert config.minimax_api_key == "secret"
    assert config.minimax_voice_id == body["voice_id"]
    assert config.saved is True

    class IncompleteClient(Client):
        async def post(self, url: str, **kwargs: object) -> Response:
            if url.endswith("/files/upload"):
                return Response({"file": {"file_id": 456}})
            return Response({})

    broken_config = FakeConfig()
    broken_upload = UploadFile(io.BytesIO(_wav_bytes(10.0)), filename="sample.wav")
    with patch("wizard.app.IRISConfig.load", return_value=broken_config), patch(
        "httpx.AsyncClient", return_value=IncompleteClient()
    ):
        broken_response = asyncio.run(clone_voice(
            request=_action_request("clone-voice"),
            audio=broken_upload,
            name="Voz IRIS",
            provider="minimax",
            api_key="secret",
            consent_confirmed=True,
        ))
    assert broken_response.status_code == 502
    assert broken_config.saved is False


def test_final_voice_preflight_rejects_guaranteed_broken_setups() -> None:
    assert _tts_config_error(IRISConfig(tts_provider="minimax", minimax_api_key=""))[0] == "missing_tts_api_key"
    assert _tts_config_error(IRISConfig(tts_provider="neutts"))[0] == "missing_voice_reference"
    fish = IRISConfig(
        tts_provider="fish-speech",
        fish_speech_ref_audio="voice.wav",
        fish_speech_ref_text="",
    )
    assert _tts_config_error(fish)[0] == "incomplete_voice_reference"
    conflicting_fish = IRISConfig(
        tts_provider="fish-speech",
        fish_speech_ref_audio="voice.wav",
        fish_speech_ref_text="texto",
        fish_speech_reference_id="registered-id",
    )
    assert _tts_config_error(conflicting_fish)[0] == "conflicting_voice_reference"
    public_fish = IRISConfig(
        tts_provider="fish-speech",
        fish_speech_url="https://example.com",
    )
    assert _tts_config_error(public_fish)[0] == "invalid_voice_server_url"
    missing_local_reference = str(ROOT / "tests" / "definitely-missing-voice.wav")
    assert _tts_config_error(IRISConfig(
        tts_provider="coqui",
        coqui_ref_audio=missing_local_reference,
    ))[0] == "missing_voice_reference_file"
    assert _tts_config_error(IRISConfig(
        tts_provider="neutts",
        neutts_ref_audio=missing_local_reference,
        neutts_ref_text="texto",
    ))[0] == "missing_voice_reference_file"


def test_fish_speech_server_url_is_limited_to_local_services() -> None:
    assert _local_service_url_error("http://127.0.0.1:8080") == ""
    assert _local_service_url_error("http://192.168.1.20:8080") == ""
    assert _local_service_url_error("http://voicebox.local:8080") == ""
    assert _local_service_url_error("https://example.com")
    assert _local_service_url_error("http://user:pass@localhost:8080")
    assert _local_service_url_error("http://localhost:8080/v1")


def test_wizard_exposes_transcript_consent_and_cloud_key_without_urls() -> None:
    html = (ROOT / "wizard" / "ui" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "wizard" / "ui" / "wizard.js").read_text(encoding="utf-8")
    backend = (ROOT / "wizard" / "app.py").read_text(encoding="utf-8")
    assert 'id="clone-transcript"' in html
    assert 'id="clone-consent-confirmed"' in html
    # One credential per service, beside its voice settings; clone and preview
    # reuse it rather than offering a second, potentially conflicting key.
    assert 'id="key-elevenlabs"' in html
    assert 'id="key-minimax"' in html
    assert 'id="clone-cloud-api-key"' not in html
    assert "formData.append('transcript'" in script
    assert "formData.append(\n                'model'" in script
    assert "formData.append('consent_confirmed', 'true')" in script
    assert "X-IRIS-TTS-Key" in script
    assert "fish_url: fishUrl" in script
    assert "'X-IRIS-Intent': 'preview-tts'" in script
    assert "'X-IRIS-Intent': 'clone-voice'" in script
    assert "tts_api_key=" not in script
    assert "voice = selectedVoice" in script
    assert "if (consent) consent.checked = false" in script
    assert "if (!navigator.mediaDevices?.getUserMedia)" in script
    assert "window.AudioContext || window.webkitAudioContext" in script
    assert "stream.getTracks().forEach(t => t.stop())" in script
    assert "minimax: 300" in script
    assert "el grabador limita a 2 min" not in script
    assert "'hf-s2s': 15" in script
    assert "selectVoice('${v.id}'" not in script
    assert "candidate.dataset.voiceId === voiceId" in script
    # A08.5: extras are probed in their isolated environment, never in Core.
    assert "asyncio.to_thread(extras_manager.extras_status)" in backend


def test_updated_defaults_can_encode_spanish_neutts_references() -> None:
    assert IRISConfig().neutts_model_repo == "neuphonic/neutts-nano-spanish-q4-gguf"
    assert IRISConfig().neutts_codec_repo == "neuphonic/neucodec"
    assert ConfigUpdate().neutts_codec_repo == "neuphonic/neucodec"
    assert NeuTTSProvider(device="metal").device == "mps"
    assert NeuTTSProvider(device="rocm").device == "cuda"


def test_hf_s2s_wizard_is_optional_honest_and_preserves_existing_stt() -> None:
    html = (ROOT / "wizard" / "ui" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "wizard" / "ui" / "wizard.js").read_text(encoding="utf-8")
    backend = (ROOT / "wizard" / "app.py").read_text(encoding="utf-8")

    assert 'value="hf-s2s"' in html
    assert 'id="hf-s2s-model"' in html
    assert 'id="hf-s2s-speaker"' in html
    assert "no se inicia un segundo agente de voz" in html
    assert "AMD" in html and "no promete aceleración" in html
    assert "'hf-s2s': {" in script
    assert "model, text, fish_url: fishUrl" in script
    assert "'hf-s2s': `HF Qwen3-TTS" in script
    assert "stt_provider: 'whisper-local'" not in script
    assert "whisper_model: 'base'" not in script
    from core.extras import load_manifest

    assert "speech-to-speech==0.2.12" in load_manifest().extras["hf-s2s"].requirements
    assert '"hf-s2s"' in backend

    existing = IRISConfig(stt_provider="google", whisper_model="large-v3")
    update = ConfigUpdate.model_validate({"tts_provider": "edge-tts"})
    merged = _merge_config_update(update, existing)
    assert merged.stt_provider == "google"
    assert merged.whisper_model == "large-v3"


def test_hf_s2s_preflight_distinguishes_preset_and_base_models() -> None:
    assert _tts_config_error(IRISConfig(
        tts_provider="hf-s2s",
        hf_s2s_model="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        hf_s2s_speaker="Aiden",
    )) is None
    assert _tts_config_error(IRISConfig(
        tts_provider="hf-s2s",
        hf_s2s_model="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        hf_s2s_speaker="inventada",
    ))[0] == "unsupported_voice_id"
    assert _tts_config_error(IRISConfig(
        tts_provider="hf-s2s",
        hf_s2s_model="Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    ))[0] == "missing_voice_reference"


def test_remote_tts_fallback_requires_explicit_opt_in() -> None:
    class EmptyLocalProvider:
        async def synthesize(self, _text: str) -> tuple[bytes, list[dict[str, object]]]:
            return b"", []

    class WorkingRemoteProvider:
        async def synthesize(self, _text: str) -> tuple[bytes, list[dict[str, object]]]:
            return b"remote-audio", []

    async def exercise() -> None:
        private_engine = TextToSpeech(IRISConfig(
            tts_provider="coqui",
            tts_allow_remote_fallback=False,
        ))
        private_engine._provider = EmptyLocalProvider()

        async def forbidden_fallback() -> object:
            raise AssertionError("remote fallback must not be initialised")

        private_engine._ensure_fallback = forbidden_fallback  # type: ignore[method-assign]
        audio, _ = await private_engine.speak("texto privado")
        assert audio == b"FALLBACK_FAILED"

        opted_in_engine = TextToSpeech(IRISConfig(
            tts_provider="coqui",
            tts_allow_remote_fallback=True,
        ))
        opted_in_engine._provider = EmptyLocalProvider()

        async def allowed_fallback() -> WorkingRemoteProvider:
            return WorkingRemoteProvider()

        opted_in_engine._ensure_fallback = allowed_fallback  # type: ignore[method-assign]
        audio, _ = await opted_in_engine.speak("texto autorizado")
        assert audio == b"remote-audio"

    assert IRISConfig().tts_allow_remote_fallback is False
    asyncio.run(exercise())


def test_wizard_preview_never_masks_provider_failure_with_edge_tts() -> None:
    source = (ROOT / "wizard" / "app.py").read_text(encoding="utf-8")
    script = (ROOT / "wizard" / "ui" / "wizard.js").read_text(encoding="utf-8")
    html = (ROOT / "wizard" / "ui" / "index.html").read_text(encoding="utf-8")
    assert "cfg.tts_allow_remote_fallback = False" in source
    assert 'audio_bytes == b"FALLBACK_FAILED"' in source
    assert "tts_allow_remote_fallback:" in script
    assert 'id="tts-allow-remote-fallback"' in html


def test_kokoro_voice_ids_select_their_own_language_and_migrate_legacy_values() -> None:
    assert _kokoro_lang_code("ef_dora", "en") == "e"
    assert _kokoro_lang_code("af_heart", "es") == "a"
    assert _kokoro_lang_code("jf_alpha", "es") == "j"
    assert _kokoro_lang_code("zf_xiaoxiao", "es") == "z"
    assert KokoroTTSProvider("ef_bella").voice == "af_bella"

    html = (ROOT / "wizard" / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'value="af_heart"' in html
    assert 'value="af_bella"' in html
    assert 'value="ef_bella"' not in html


def test_isolated_extras_do_not_share_one_transformers_runtime() -> None:
    """A08.5 replaced the old macOS install blocker: each extra has its own env."""
    from core import extras

    manifest = extras.load_manifest()
    assert manifest.extras["kokoro"].lock != manifest.extras["hf-s2s"].lock
    if extras.compatibility_error(manifest):
        return
    kokoro = {w.name: w.version for w in extras.locked_wheels(manifest.extras["kokoro"], manifest)}
    qwen = {w.name: w.version for w in extras.locked_wheels(manifest.extras["hf-s2s"], manifest)}
    # Incompatible major versions coexist because they never share sys.path.
    assert kokoro["transformers"].split(".")[0] != qwen["transformers"].split(".")[0]
    # Kokoro's Spanish phonemiser ships in the extra; no system eSpeak-NG step.
    assert "espeakng-loader" in kokoro


def test_voice_mutations_and_extra_installs_are_post_only() -> None:
    expected = {
        "/api/test-tts-audio": {"POST"},
        "/api/clone-voice": {"POST"},
        "/api/install-extra-stream": {"POST"},
    }
    for path, methods in expected.items():
        route = next(route for route in app.routes if getattr(route, "path", "") == path)
        assert route.methods == methods

    script = (ROOT / "wizard" / "ui" / "wizard.js").read_text(encoding="utf-8")
    assert "new EventSource(`/api/install-extra-stream" not in script
    assert "'X-IRIS-Intent': 'install-extra'" in script


def test_tts_preview_rejects_missing_or_external_intent_before_synthesis() -> None:
    client = TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 51999),
    )
    assert client.get("/api/test-tts-audio").status_code == 405
    assert client.post("/api/test-tts-audio", json={"provider": "invalid"}).status_code == 403
    assert client.post(
        "/api/test-tts-audio",
        json={"provider": "invalid"},
        headers={
            "X-IRIS-Intent": "preview-tts",
            "Origin": "https://evil.example",
        },
    ).status_code == 403
    response = client.post(
        "/api/test-tts-audio",
        json={"provider": "invalid"},
        headers={
            "X-IRIS-Intent": "preview-tts",
            "Origin": "http://127.0.0.1:8000",
        },
    )
    assert response.status_code == 400
