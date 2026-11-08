from __future__ import annotations

from pathlib import Path

from wizard.providers import auth_mode_for, get_provider, warning_for
from wizard.system_profile import setup_recommendations


def _gpu(acceleration: str = "cpu") -> dict[str, object]:
    return {"detected": acceleration != "cpu", "acceleration": acceleration}


def _servers(
    *,
    ollama_running: bool = False,
    ollama_models: list[str] | None = None,
    lmstudio_running: bool = False,
    lmstudio_models: list[str] | None = None,
) -> dict[str, dict[str, object]]:
    return {
        "ollama": {
            "running": ollama_running,
            "models": ollama_models or [],
        },
        "lmstudio": {
            "running": lmstudio_running,
            "models": lmstudio_models or [],
        },
    }


def test_weak_machine_without_local_server_recommends_remote_and_light_voice() -> None:
    result = setup_recommendations(
        ram_gb=4.0,
        cpu_threads=2,
        gpu=_gpu(),
        local_servers=_servers(),
    )

    assert result["inference"]["provider"] == "remote"
    assert result["local_inference"]["viability"] == "not_recommended"
    assert result["voice"]["provider"] == "edge-tts"
    assert "requiere internet" in result["voice"]["reason"]


def test_running_ollama_recommends_only_a_model_that_is_really_available() -> None:
    result = setup_recommendations(
        ram_gb=16.0,
        cpu_threads=8,
        gpu=_gpu("cuda"),
        local_servers=_servers(
            ollama_running=True,
            ollama_models=["qwen2.5:3b", "gemma3:4b"],
        ),
    )

    assert result["inference"]["provider"] == "ollama-local"
    assert result["inference"]["model"] == "qwen2.5:3b"
    assert result["inference"]["requires_user_install"] is False


def test_existing_viable_remote_provider_is_preserved() -> None:
    result = setup_recommendations(
        ram_gb=8.0,
        cpu_threads=4,
        gpu=_gpu(),
        local_servers=_servers(),
        existing_provider="anthropic",
        existing_configured=True,
    )

    assert result["inference"]["provider"] == "anthropic"
    assert result["inference"]["label"] == "Mantener la configuración existente"


def test_existing_local_provider_is_not_called_viable_when_its_server_is_down() -> None:
    result = setup_recommendations(
        ram_gb=16.0,
        cpu_threads=8,
        gpu=_gpu("cuda"),
        local_servers=_servers(),
        existing_provider="ollama-local",
        existing_configured=True,
    )

    assert result["inference"]["provider"] == "remote"
    assert "no hay un servidor con modelo listo" in result["inference"]["reason"]


def test_ollama_local_and_cloud_keep_distinct_ids_and_auth() -> None:
    local = get_provider("ollama-local")
    cloud = get_provider("ollama")

    assert local and local["base_url"] == "http://127.0.0.1:11434/v1"
    assert cloud and cloud["category"] == "cloud"
    assert auth_mode_for("ollama-local") == "none"
    assert auth_mode_for("ollama") == "api"
    assert warning_for("ollama-local") is not None
    assert warning_for("ollama") is None


def test_hardware_ui_does_not_claim_every_audio_input_is_a_microphone() -> None:
    source = (Path(__file__).resolve().parents[1] / "wizard" / "ui" / "wizard.js").read_text(
        encoding="utf-8"
    )
    assert "Entradas / salidas de audio" in source
    assert "Micrófonos / parlantes" not in source
