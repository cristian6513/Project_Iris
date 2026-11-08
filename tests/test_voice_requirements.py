"""Local voice engines a PC cannot run are refused by Core and the wizard, with the reason in plain words."""
from __future__ import annotations

import asyncio
import io
import json
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile
from starlette.requests import Request

from core import voice_requirements as vr
from core.config import IRISConfig
from wizard import app as wizard_app

RADEON = {"detected": True, "name": "AMD Radeon RX 9060 XT", "acceleration": "generic"}
NVIDIA = {"detected": True, "name": "NVIDIA GeForce RTX 3060", "acceleration": "cuda", "vram_gb": 12.0}
Q06B = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
Q06C = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
Q17B = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"


def machine(ram: float | None, *, threads: int = 12, gpu: dict | None = None, disk: float | None = 200.0,
            free_ram: float | None = 24.0) -> vr.Machine:
    return vr.Machine(ram_gb=ram, threads=threads, gpu=gpu or RADEON, free_disk_gb=disk, available_ram_gb=free_ram)


def verdicts(ram: float) -> dict[str, str]:
    qwen = vr.engine_assessment("hf-s2s", machine(ram), Path("missing"), env_installed=False)["models"]
    kokoro = vr.assess("kokoro", "", machine(ram), env_installed=False, weights_missing_gb=0.33)
    return {"kokoro": kokoro["verdict"], "0.6B": qwen[Q06B]["verdict"], "1.7B": qwen[Q17B]["verdict"]}


@pytest.mark.parametrize("ram,expected", [
    (2.9, {"kokoro": "blocked", "0.6B": "blocked", "1.7B": "blocked"}),   # a 3 GB PC
    (3.6, {"kokoro": "warn", "0.6B": "blocked", "1.7B": "blocked"}),      # 4 GB: Kokoro fits, tightly
    (7.8, {"kokoro": "ok", "0.6B": "blocked", "1.7B": "blocked"}),        # 8 GB
    (11.9, {"kokoro": "ok", "0.6B": "warn", "1.7B": "blocked"}),          # 12 GB
    (15.4, {"kokoro": "ok", "0.6B": "ok", "1.7B": "blocked"}),            # 16 GB
    (23.6, {"kokoro": "ok", "0.6B": "ok", "1.7B": "warn"}),               # 24 GB
    (31.9, {"kokoro": "ok", "0.6B": "ok", "1.7B": "ok"}),                 # 32 GB, the measured PC
])
def test_measured_ram_needs_decide_each_model(ram, expected):
    assert verdicts(ram) == expected


def test_the_reason_names_iris_margin_not_a_measured_model_limit():
    result = vr.assess("hf-s2s", Q17B, machine(15.4), env_installed=False, weights_missing_gb=4.5)
    assert result["reasons"] == ["Tu equipo tiene 15.4 GB de RAM. Para Qwen3-TTS 1.7B Iris pide al menos 20 GB: "
                                 "usa unos 12.2 GB mientras habla y el resto queda para Windows e Iris."]
    # No general claim about Radeon on Windows: only that Iris does not use it yet.
    assert result["notes"][0] == "Iris aún no usa tu AMD Radeon RX 9060 XT con este motor: lo ejecuta en la CPU."


def test_unknown_ram_or_disk_is_reported_as_not_checked_never_as_fine():
    unknown = vr.assess("hf-s2s", Q06B, machine(None, disk=None, free_ram=None), env_installed=False,
                        weights_missing_gb=2.4)
    assert unknown["verdict"] == "warn"
    assert unknown["warnings"][:2] == ["No se pudo comprobar la RAM de tu equipo.",
                                       "No se pudo comprobar el espacio libre en el disco de tu perfil."]


def test_little_free_ram_right_now_is_a_warning():
    busy = vr.assess("hf-s2s", Q06B, machine(31.9, free_ram=4.0), env_installed=True, weights_missing_gb=0)
    assert busy["verdict"] == "warn"
    assert busy["warnings"] == ["Ahora quedan 4 GB de RAM libres y Qwen3-TTS 0.6B usa unos 5.9 GB: "
                                "cierra aplicaciones antes de usarlo."]


def _snapshot(models: Path, repo: str, files: dict[str, int]) -> None:
    folder = models / "huggingface" / "hub" / f"models--{repo.replace('/', '--')}" / "snapshots" / "abc123"
    folder.mkdir(parents=True, exist_ok=True)
    for name, size in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"\0" * size)


def test_only_finished_files_count_and_base_and_customvoice_are_separate(tmp_path):
    models = tmp_path / "models"
    # Codex H02: an empty cache folder (interrupted download) is not the model.
    (models / "huggingface" / "hub" / "models--Qwen--Qwen3-TTS-12Hz-0.6B-Base" / "snapshots" / "abc123").mkdir(parents=True)
    blobs = models / "huggingface" / "hub" / "models--Qwen--Qwen3-TTS-12Hz-0.6B-Base" / "blobs"
    blobs.mkdir()
    (blobs / "a1b2.incomplete").write_bytes(b"\0" * 1024 ** 2)   # a download cut halfway
    assert vr.downloaded_gb(models, Q06B) == 0
    _snapshot(models, Q06B, {"model.safetensors": 3 * 1024 ** 2, "speech_tokenizer/model.safetensors": 1024 ** 2})
    assert vr.downloaded_gb(models, Q06B) == pytest.approx(4 / 1024)
    assert vr.downloaded_gb(models, Q06C) == 0   # having Base never counts as CustomVoice

    small_disk = machine(31.9, disk=3.0)
    status = vr.engine_assessment("hf-s2s", small_disk, tmp_path, env_installed=True)["models"]
    # Still almost the whole 2.4 GB to download: blocked on a 3 GB disk.
    assert status[Q06B]["verdict"] == "blocked" and status[Q06B]["download_gb"] == 2.4


def test_free_disk_counts_only_what_is_still_to_download():
    small_disk = machine(31.9, disk=3.0)
    fresh = vr.assess("hf-s2s", Q06B, small_disk, env_installed=False, weights_missing_gb=2.4)
    assert fresh["verdict"] == "blocked" and fresh["download_gb"] == 4.0
    assert fresh["reasons"] == ["Necesitas unos 5.0 GB libres en el disco de tu perfil y quedan 3 GB."]
    ready = vr.assess("hf-s2s", Q06B, small_disk, env_installed=True, weights_missing_gb=0)
    assert ready["verdict"] == "ok" and ready["download_gb"] == 0


def test_an_engine_is_blocked_only_when_none_of_its_models_can_run():
    twelve = vr.engine_assessment("hf-s2s", machine(11.9), Path("missing"), env_installed=False)
    assert twelve["verdict"] == "warn" and twelve["reasons"] == []
    assert set(twelve["models"]) == set(vr.ENGINE_MODELS["hf-s2s"])
    eight = vr.engine_assessment("hf-s2s", machine(7.8), Path("missing"), env_installed=False)
    assert eight["verdict"] == "blocked" and len(eight["reasons"]) == 2


def test_gpu_and_cpu_notes_never_block(monkeypatch):
    nvidia = vr.assess("hf-s2s", Q06B, machine(31.9, gpu=NVIDIA), env_installed=True, weights_missing_gb=0)
    assert nvidia["verdict"] == "ok"
    assert nvidia["notes"][0] == ("Iris aún no usa tu NVIDIA GeForce RTX 3060 con este motor (su paquete es solo CPU): "
                                  "lo ejecuta en la CPU.")
    slow = vr.assess("hf-s2s", Q06B, machine(31.9, threads=4), env_installed=True, weights_missing_gb=0)
    assert slow["verdict"] == "warn" and "4 hilos" in slow["warnings"][0]
    cuda_only = vr.VoiceNeeds("Motor CUDA", 4.0, 8, 16, 1.0, "rápido", needs_cuda=True)
    monkeypatch.setitem(vr.VOICE_NEEDS, "hf-s2s", {"0.6B": cuda_only, "1.7B": cuda_only})
    radeon = vr.assess("hf-s2s", Q06B, machine(31.9), env_installed=True, weights_missing_gb=0)
    assert radeon["reasons"] == ["Motor CUDA necesita una gráfica NVIDIA con CUDA; AMD Radeon RX 9060 XT no la tiene."]


def test_a_folder_is_not_an_installed_environment(tmp_path):
    (tmp_path / "extras" / "hf-s2s" / "env-empty").mkdir(parents=True)   # Codex H02
    assert vr.environment_installed("hf-s2s", tmp_path) is False


# ── Enforcement ─────────────────────────────────────────────────────────────


def _small(monkeypatch, ram: float) -> None:
    monkeypatch.setattr(vr, "current_machine", lambda _home: machine(ram))


def _wav(seconds: float = 4.0) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * int(24000 * seconds))
    return out.getvalue()


def _request(intent: str) -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "raw_path": b"/", "query_string": b"",
                    "headers": [(b"x-iris-intent", intent.encode()), (b"origin", b"http://127.0.0.1:5177"),
                                (b"sec-fetch-site", b"same-origin")],
                    "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 5177), "scheme": "http"})


@pytest.mark.parametrize("action", ["install", "repair"])
def test_installing_or_repairing_an_engine_this_pc_cannot_run_downloads_nothing(monkeypatch, tmp_path, action):
    _small(monkeypatch, 7.8)
    monkeypatch.setenv("IRIS_HOME", str(tmp_path))
    from core import extras as extras_manager

    monkeypatch.setattr(extras_manager, "install_extra", lambda *a, **k: pytest.fail("must not download"))
    client = TestClient(wizard_app.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 51999))
    response = client.post(f"/api/install-extra-stream?extra=hf-s2s&action={action}",
                           headers={"X-IRIS-Intent": "install-extra", "Origin": "http://127.0.0.1:8000"})
    event = json.loads(response.text.split("data: ", 1)[1].strip())
    assert event["type"] == "error" and event["code"] == "hardware_insufficient"
    assert "7.8 GB de RAM" in event["message"]


def test_cloning_with_a_size_this_pc_cannot_run_is_refused(monkeypatch, tmp_path):
    _small(monkeypatch, 15.4)
    monkeypatch.setattr(wizard_app.IRISConfig, "load", lambda: IRISConfig())
    monkeypatch.setattr(wizard_app, "get_iris_home", lambda: tmp_path)
    response = asyncio.run(wizard_app.clone_voice(
        request=_request("clone-voice"), audio=UploadFile(io.BytesIO(_wav()), filename="muestra.wav"),
        name="Voz", provider="hf-s2s", model=Q17B, transcript="Texto exacto.", consent_confirmed=True,
    ))
    assert response.status_code == 409
    assert "Para Qwen3-TTS 1.7B Iris pide al menos 20 GB" in json.loads(response.body)["error"]
    assert not (tmp_path / "voices").exists()   # nothing saved


def test_saving_or_previewing_a_voice_this_pc_cannot_run_is_refused(monkeypatch, tmp_path):
    _small(monkeypatch, 15.4)
    monkeypatch.setattr(wizard_app, "get_iris_home", lambda: tmp_path)
    reference = tmp_path / "ref.wav"
    reference.write_bytes(_wav())
    config = IRISConfig(tts_provider="hf-s2s", hf_s2s_model=Q17B, hf_s2s_ref_audio=str(reference),
                        hf_s2s_ref_text="exacto")
    code, message = wizard_app._tts_config_error(config)
    assert code == "hardware_insufficient" and "15.4 GB de RAM" in message
    config.hf_s2s_model = Q06B
    assert wizard_app._tts_config_error(config) is None   # the smaller model fits

    _small(monkeypatch, 2.9)
    monkeypatch.setattr(wizard_app.IRISConfig, "load", lambda: IRISConfig())
    client = TestClient(wizard_app.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 51999))
    response = client.post("/api/test-tts-audio", json={"provider": "kokoro", "text": "Hola"},
                           headers={"X-IRIS-Intent": "preview-tts", "Origin": "http://127.0.0.1:8000"})
    assert response.status_code == 409 and "Para Kokoro Iris pide al menos 4 GB" in response.json()["message"]


def test_core_never_loads_a_saved_voice_this_pc_cannot_run(monkeypatch, tmp_path):
    # Codex H03: a saved or migrated profile bypassed the wizard's checks.
    from core import extras as extras_manager
    from core import tts

    _small(monkeypatch, 7.8)
    monkeypatch.setattr(tts, "get_iris_home", lambda: tmp_path)
    requests: list[str] = []

    class Worker:
        running = False

        def __init__(self, environment):
            pass

        async def start(self):
            self.running = True

        async def request(self, payload, *, timeout):
            requests.append(payload["op"])
            return {"ok": True}

        async def close(self):
            self.running = False

    monkeypatch.setattr(extras_manager, "ExtraWorker", Worker)
    monkeypatch.setattr(extras_manager, "resolve_environment", lambda *_a, **_k: object())
    engine = tts.TextToSpeech(IRISConfig(tts_provider="hf-s2s", hf_s2s_model="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"))
    asyncio.run(engine.initialize())
    assert isinstance(engine._provider, tts.UnavailableExtraTTSProvider)
    assert requests == []                                   # no «load», so no weights download
    assert "Para Qwen3-TTS 1.7B Iris pide al menos 20 GB" in engine.last_error
    assert asyncio.run(engine.speak("Hola")) == (b"FALLBACK_FAILED", [])   # no silent remote fallback

    _small(monkeypatch, 31.9)   # the same profile on a roomy PC loads normally
    roomy = tts.TextToSpeech(IRISConfig(tts_provider="hf-s2s", hf_s2s_model="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"))
    asyncio.run(roomy.initialize())
    assert requests == ["load"]


def test_extras_status_carries_the_verdict_for_each_voice_model(monkeypatch, tmp_path):
    _small(monkeypatch, 11.9)
    monkeypatch.setattr(wizard_app, "get_iris_home", lambda: tmp_path)
    status = wizard_app._voice_hardware_status({"kokoro": {"state": "not_installed"},
                                                "hf-s2s": {"state": "broken"}})
    assert status["kokoro"]["verdict"] == "ok"
    assert {model: value["verdict"] for model, value in status["hf-s2s"]["models"].items()} == {
        Q06C: "warn", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice": "blocked", Q06B: "warn", Q17B: "blocked"}
