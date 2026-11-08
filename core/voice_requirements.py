"""Can this PC run each local voice engine? Measured needs and plain reasons.

Core refuses to load a voice this PC cannot run (core/tts.py), and the wizard
refuses to download, clone, preview or save it, saying why in the user's words:
too little RAM, not enough free disk, or no NVIDIA card with CUDA. Warnings and
notes (tight or busy RAM, few CPU threads, what the graphics card does, measured
speed) never block.

Measured on 2026-10-21 on a Ryzen 5 5600G (6 cores, 12 threads), 32 GB RAM,
Windows 11, with the approved Windows extras (CPU-only torch):

| Engine            | Peak RAM while speaking | Weights | CPU time per audio second |
|-------------------|-------------------------|---------|---------------------------|
| Kokoro-82M        | 1.2 GB                  | 0.33 GB | 0.5 s (faster than real)  |
| Qwen3-TTS 0.6B    | 5.9 GB                  | 2.4 GB  | 8-12 s                    |
| Qwen3-TTS 1.7B    | 12.2 GB                 | 4.5 GB  | 17 s                      |

The minimum total RAM is Iris's own margin over the measured peak, in nominal
sizes (4, 12, 20 GB) that leave room for Windows, Iris and Hermes; it is not a
measured limit of the model. A PC reports a little less than its nominal RAM,
hence RAM_TOLERANCE_GB. Below the comfortable figure the engine runs but other
apps compete with it for memory.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VoiceNeeds:
    label: str
    ram_peak_gb: float          # measured peak of the engine's process
    min_ram_gb: float           # Iris's minimum total RAM (peak plus margin)
    comfortable_ram_gb: float   # below this it works but may compete for memory
    weights_gb: float           # first-use download into the profile, per model repository
    speed: str                  # measured on the reference PC (see above)
    min_threads: int = 4        # fewer logical cores: warn that it will be slower
    needs_cuda: bool = False    # an engine without a CPU path (none today)


# Disk taken by each approved extra environment once extracted.
ENGINE_ENV_GB = {"kokoro": 0.9, "hf-s2s": 1.6}
DISK_MARGIN_GB = 1.0
# A 16 GB PC reports 15.2-15.9 GB: memory reserved by the hardware.
RAM_TOLERANCE_GB = 0.8

VOICE_NEEDS: dict[str, dict[str, VoiceNeeds]] = {
    "kokoro": {
        "": VoiceNeeds("Kokoro", 1.2, 4, 8, 0.33, "habla más rápido de lo que dura el audio", min_threads=2),
    },
    "hf-s2s": {
        "0.6B": VoiceNeeds("Qwen3-TTS 0.6B", 5.9, 12, 16, 2.4,
                           "tarda unas 8 a 12 veces lo que dura el audio", min_threads=8),
        "1.7B": VoiceNeeds("Qwen3-TTS 1.7B", 12.2, 20, 32, 4.5,
                           "tarda unas 17 veces lo que dura el audio", min_threads=8),
    },
}

# The models each engine offers; Base and CustomVoice are separate downloads.
ENGINE_MODELS: dict[str, tuple[str, ...]] = {
    "kokoro": ("",),
    "hf-s2s": (
        "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    ),
}
_KOKORO_REPO = "hexgrad/Kokoro-82M"


def needs_for(engine: str, model_name: str = "") -> VoiceNeeds:
    if engine == "hf-s2s":
        return VOICE_NEEDS[engine]["1.7B" if "1.7B" in (model_name or "") else "0.6B"]
    return VOICE_NEEDS[engine][""]


def weights_repo(engine: str, model_name: str = "") -> str:
    return model_name if engine == "hf-s2s" else _KOKORO_REPO


def downloaded_gb(models_dir: Path, repo: str) -> float:
    """Bytes of this repository's files already complete in the profile cache.

    Only finished files count (a snapshot holds them; an interrupted download
    leaves ``*.incomplete`` blobs and maybe empty folders), so a partial or
    empty cache never passes for the whole model.
    """
    snapshots = models_dir / "huggingface" / "hub" / f"models--{repo.replace('/', '--')}" / "snapshots"
    best = 0
    try:
        for revision in snapshots.iterdir():
            total = 0
            for path in revision.rglob("*"):
                try:
                    if path.is_file():
                        total += path.stat().st_size   # follows the cache's links to blobs
                except OSError:
                    continue
            best = max(best, total)
    except OSError:
        return 0.0
    return best / 1024 ** 3


@dataclass(frozen=True)
class Machine:
    ram_gb: float | None
    threads: int
    gpu: dict[str, Any]
    free_disk_gb: float | None
    available_ram_gb: float | None = None


_CACHE: tuple[float, tuple[float | None, int, dict[str, Any]]] | None = None
_CACHE_SECONDS = 60.0


def current_machine(home: Path) -> Machine:
    """Total RAM, CPU and GPU once a minute (GPU probes take a second or two);
    free disk and free RAM are read every time."""
    global _CACHE
    from core.system_resources import available_ram_gb, cpu_profile, gpu_profile, total_ram_gb

    now = time.monotonic()
    if not _CACHE or now - _CACHE[0] >= _CACHE_SECONDS:
        _CACHE = (now, (total_ram_gb(), int(cpu_profile().get("logical_cores") or 1), gpu_profile()))
    ram, threads, gpu = _CACHE[1]
    try:
        free = round(shutil.disk_usage(home if home.exists() else home.anchor).free / 1024 ** 3, 1)
    except OSError:
        free = None
    return Machine(ram, threads, gpu, free, available_ram_gb())


def _gpu_note(gpu: dict[str, Any]) -> str:
    name = str(gpu.get("name") or "").strip()
    if gpu.get("acceleration") == "cuda":
        return f"Iris aún no usa tu {name or 'gráfica NVIDIA'} con este motor (su paquete es solo CPU): lo ejecuta en la CPU."
    if gpu.get("detected") and name:
        return f"Iris aún no usa tu {name} con este motor: lo ejecuta en la CPU."
    return "Este motor corre en la CPU."


def assess(engine: str, model_name: str, machine: Machine, *, env_installed: bool,
           weights_missing_gb: float) -> dict[str, Any]:
    """Verdict for one model: blocked reasons, warnings and notes, in Spanish."""
    needs = needs_for(engine, model_name)
    blocked: list[str] = []
    warnings: list[str] = []
    if machine.ram_gb is None:
        warnings.append("No se pudo comprobar la RAM de tu equipo.")
    elif machine.ram_gb + RAM_TOLERANCE_GB < needs.min_ram_gb:
        blocked.append(
            f"Tu equipo tiene {machine.ram_gb:g} GB de RAM. Para {needs.label} Iris pide al menos "
            f"{needs.min_ram_gb:g} GB: usa unos {needs.ram_peak_gb:g} GB mientras habla y el resto queda "
            "para Windows e Iris."
        )
    elif machine.ram_gb + RAM_TOLERANCE_GB < needs.comfortable_ram_gb:
        warnings.append(
            f"Con {machine.ram_gb:g} GB de RAM {needs.label} puede ir justo: cierra otras aplicaciones "
            "mientras habla."
        )
    if (not blocked and machine.available_ram_gb is not None
            and machine.available_ram_gb < needs.ram_peak_gb + 0.5):
        warnings.append(
            f"Ahora quedan {machine.available_ram_gb:g} GB de RAM libres y {needs.label} usa unos "
            f"{needs.ram_peak_gb:g} GB: cierra aplicaciones antes de usarlo."
        )
    if needs.needs_cuda and machine.gpu.get("acceleration") != "cuda":
        name = str(machine.gpu.get("name") or "").strip()
        blocked.append(f"{needs.label} necesita una gráfica NVIDIA con CUDA; {name or 'tu equipo'} no la tiene.")
    need_disk = (0 if env_installed else ENGINE_ENV_GB[engine]) + max(0.0, weights_missing_gb)
    if need_disk:
        if machine.free_disk_gb is None:
            warnings.append("No se pudo comprobar el espacio libre en el disco de tu perfil.")
        elif machine.free_disk_gb < need_disk + DISK_MARGIN_GB:
            blocked.append(
                f"Necesitas unos {need_disk + DISK_MARGIN_GB:.1f} GB libres en el disco de tu perfil y quedan "
                f"{machine.free_disk_gb:g} GB."
            )
    if machine.threads < needs.min_threads:
        warnings.append(f"Tu procesador tiene {machine.threads} hilos: la voz irá más lenta de lo indicado.")
    return {
        "label": needs.label,
        "verdict": "blocked" if blocked else ("warn" if warnings else "ok"),
        "reasons": blocked,
        "warnings": warnings,
        "notes": [_gpu_note(machine.gpu), f"En un Ryzen 5 5600G {needs.speed}."],
        "ram_peak_gb": needs.ram_peak_gb,
        "download_gb": round(need_disk, 1),
    }


def _weights_missing(engine: str, model_name: str, models_dir: Path) -> float:
    needs = needs_for(engine, model_name)
    return max(0.0, needs.weights_gb - downloaded_gb(models_dir, weights_repo(engine, model_name)))


def engine_assessment(engine: str, machine: Machine, extra_dir: Path, *, env_installed: bool) -> dict[str, Any]:
    """Every model of an engine (by model id); the engine is blocked only when none can run."""
    models = {
        model: assess(engine, model, machine, env_installed=env_installed,
                      weights_missing_gb=_weights_missing(engine, model, extra_dir / "models"))
        for model in ENGINE_MODELS[engine]
    }
    runnable = [result for result in models.values() if result["verdict"] != "blocked"]
    return {
        "verdict": "blocked" if not runnable else ("ok" if any(r["verdict"] == "ok" for r in runnable) else "warn"),
        "reasons": [] if runnable else sorted({reason for r in models.values() for reason in r["reasons"]}),
        "models": models,
    }


def environment_installed(engine: str, home: Path) -> bool:
    """The extras manager's committed environment, never just a folder that exists."""
    try:
        from core.extras import resolve_environment

        return resolve_environment(engine, home=home) is not None
    except Exception:
        return False


def blocked_reason(engine: str, model_name: str, home: Path) -> str:
    """Why this PC cannot use this engine and model, or "" when it can."""
    if engine not in VOICE_NEEDS:
        return ""
    extra_dir = home / "extras" / engine
    result = assess(engine, model_name if engine == "hf-s2s" else "", current_machine(home),
                    env_installed=environment_installed(engine, home),
                    weights_missing_gb=_weights_missing(engine, model_name, extra_dir / "models"))
    return " ".join(result["reasons"])
