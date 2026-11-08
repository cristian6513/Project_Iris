"""Hardware probing and conservative setup recommendations for the wizard.

The detection layer reports facts separately from recommendations.  In
particular, the absence of an Ollama/LM Studio HTTP server does *not* mean the
application is not installed, and a GPU name by itself is not treated as proof
that local inference will be fast enough.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Any

import httpx

from core.system_resources import (  # noqa: F401 - re-exported for the wizard
    _command_output,
    available_ram_gb,
    cpu_profile,
    gpu_profile,
    total_ram_gb,
)


async def probe_local_model_servers(timeout: float = 1.0) -> dict[str, dict[str, Any]]:
    """Probe fixed loopback endpoints; never infer installation from silence."""

    async def probe_ollama(client: httpx.AsyncClient) -> dict[str, Any]:
        try:
            response = await client.get("http://127.0.0.1:11434/api/tags")
            response.raise_for_status()
            payload = response.json()
            models = [
                str(item.get("name") or item.get("model") or "").strip()
                for item in payload.get("models", [])
                if isinstance(item, dict)
            ]
            return {"running": True, "models": [model for model in models if model]}
        except (httpx.HTTPError, ValueError, TypeError):
            return {"running": False, "models": []}

    async def probe_lmstudio(client: httpx.AsyncClient) -> dict[str, Any]:
        try:
            response = await client.get("http://127.0.0.1:1234/v1/models")
            response.raise_for_status()
            payload = response.json()
            models = [
                str(item.get("id") or "").strip()
                for item in payload.get("data", [])
                if isinstance(item, dict)
            ]
            return {"running": True, "models": [model for model in models if model]}
        except (httpx.HTTPError, ValueError, TypeError):
            return {"running": False, "models": []}

    limits = httpx.Limits(max_connections=2)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        ollama, lmstudio = await asyncio.gather(
            probe_ollama(client), probe_lmstudio(client),
        )
    ollama["cli_detected"] = bool(shutil.which("ollama") or shutil.which("ollama.exe"))
    # LM Studio does not publish a stable cross-platform CLI name. A silent
    # endpoint therefore stays "no detectado", never "no instalado".
    lmstudio["cli_detected"] = None
    return {"ollama": ollama, "lmstudio": lmstudio}


def setup_recommendations(
    *,
    ram_gb: float | None,
    cpu_threads: int,
    gpu: dict[str, Any],
    local_servers: dict[str, dict[str, Any]],
    existing_provider: str = "",
    existing_configured: bool = False,
) -> dict[str, Any]:
    """Build cautious, machine-independent inference and voice guidance."""
    acceleration = str(gpu.get("acceleration") or "cpu")
    enough_for_local = bool(
        ram_gb is not None
        and ram_gb >= 12
        and cpu_threads >= 4
        and acceleration in {"cuda", "metal"}
    )
    limited_local = bool(ram_gb is not None and ram_gb >= 8 and cpu_threads >= 4)
    ollama = local_servers.get("ollama", {})
    lmstudio = local_servers.get("lmstudio", {})

    local_by_provider = {"ollama-local": ollama, "lmstudio": lmstudio}
    existing_local = local_by_provider.get(existing_provider)
    if existing_configured and existing_provider and (
        existing_local is None
        or (
            bool(existing_local.get("running"))
            and bool(existing_local.get("models"))
        )
    ):
        inference = {
            "provider": existing_provider,
            "label": "Mantener la configuración existente",
            "reason": "Hermes ya tiene un proveedor utilizable; IRIS no necesita reemplazarlo.",
            "requires_user_install": False,
        }
    elif ollama.get("running") and ollama.get("models"):
        inference = {
            "provider": "ollama-local",
            "label": "Ollama local ya activo",
            "reason": f"Servidor detectado con {len(ollama['models'])} modelo(s) disponible(s).",
            "model": ollama["models"][0],
            "requires_user_install": False,
        }
    elif lmstudio.get("running") and lmstudio.get("models"):
        inference = {
            "provider": "lmstudio",
            "label": "LM Studio ya activo",
            "reason": f"Servidor detectado con {len(lmstudio['models'])} modelo(s) cargado(s).",
            "model": lmstudio["models"][0],
            "requires_user_install": False,
        }
    else:
        if enough_for_local:
            local_note = "El equipo puede ejecutar modelos locales pequeños, pero no hay un servidor con modelo listo."
        elif limited_local:
            local_note = "La inferencia local solo es recomendable con modelos pequeños y puede ser lenta."
        else:
            local_note = "No hay recursos confirmados para recomendar inferencia local con buena experiencia."
        inference = {
            "provider": "remote",
            "label": "Proveedor remoto",
            "reason": f"{local_note} Ollama y LM Studio no serán instalados por este wizard.",
            "requires_user_install": False,
        }

    if ram_gb is not None and ram_gb < 4:
        local_voice = "No se recomienda TTS local con menos de 4 GB de RAM."
    elif ram_gb is None:
        local_voice = "Kokoro queda como opción local, pero la RAM no pudo verificarse; su extra incluye eSpeak-NG para español."
    else:
        local_voice = "Kokoro es una alternativa local opcional; se instala como extra, que ya incluye eSpeak-NG para español."

    return {
        "inference": inference,
        "voice": {
            "provider": "edge-tts",
            "label": "Edge TTS",
            "reason": f"Es la opción ligera predeterminada y requiere internet. {local_voice}",
        },
        "local_inference": {
            "viability": "recommended" if enough_for_local else "limited" if limited_local else "not_recommended",
            "servers_detected": bool(ollama.get("running") or lmstudio.get("running")),
        },
    }


__all__ = [
    "cpu_profile",
    "gpu_profile",
    "probe_local_model_servers",
    "setup_recommendations",
    "total_ram_gb",
]
