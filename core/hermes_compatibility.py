"""IRIS/Hermes compatibility policy and local inspection.

The UI should never guess a Hermes feature from a version string alone. This
module keeps a small, explicit baseline for IRIS and combines it with checks of
the local launcher and supported install layout. Live feature availability is
negotiated separately through the Hermes capabilities endpoint. The report is
deliberately secret-free so it can be shown in IRIS OS Doctor and the wizard.
"""

from __future__ import annotations

import os
import platform
import re
from typing import Any

from core.hermes_runtime import (
    get_hermes_config_path,
    get_hermes_home,
    get_hermes_repo,
    resolve_hermes_cli,
    run_hermes_cli,
)


# Hermes 0.20 and 0.21 are the release families IRIS validates against,
# including the native wake-word, OAuth and local API contracts. Hermes 0.19
# remains the minimum supported chat/runtime baseline; optional features are
# still negotiated live instead of being promised from a version number.
IRIS_MIN_HERMES_VERSION = (0, 19, 0)
IRIS_MIN_HERMES_VERSION_TEXT = ".".join(str(part) for part in IRIS_MIN_HERMES_VERSION)
IRIS_VALIDATED_HERMES_SERIES = ((0, 20), (0, 21))
IRIS_VALIDATED_HERMES_VERSION = "0.20.x–0.21.x"

_SEMVER_RE = re.compile(r"(?<!\d)(?:v(?:ersion)?\s*)?(\d+)\.(\d+)\.(\d+)(?!\d)", re.I)


def parse_hermes_version(value: str) -> tuple[int, int, int] | None:
    """Extract the first semantic version from tolerant Hermes CLI output."""
    match = _SEMVER_RE.search(value or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def format_version(value: tuple[int, int, int] | None) -> str:
    return ".".join(str(part) for part in value) if value else "No disponible"


def platform_support(
    system_name: str | None = None,
    machine: str | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, str | bool]:
    """Return Hermes' published platform tier without running a subprocess."""
    system = (system_name or platform.system()).strip().lower()
    arch = (machine or platform.machine()).strip().lower().replace("amd64", "x86_64")
    normalized_arch = "arm64" if arch in {"aarch64", "arm64"} else arch
    env = os.environ if environ is None else environ
    is_termux = bool(env.get("TERMUX_VERSION") or "com.termux" in env.get("PREFIX", "").lower())
    is_nix = any(env.get(key) for key in ("IN_NIX_SHELL", "NIX_STORE", "NIX_PROFILES"))

    if is_termux:
        if normalized_arch == "arm64":
            return {
                "status": "best_effort",
                "tier": "Tier 2",
                "label": "Android/Termux con soporte limitado",
                "detail": "Hermes mantiene Android/Termux ARM64 como Tier 2; algunas funciones no están disponibles.",
                "system": "Android/Termux",
                "architecture": normalized_arch,
                "supported": False,
            }
        return {
            "status": "unsupported",
            "tier": "No compatible",
            "label": "Android/Termux no compatible",
            "detail": "Hermes sólo publica soporte Tier 2 para Android/Termux sobre ARM64.",
            "system": "Android/Termux",
            "architecture": normalized_arch or "desconocida",
            "supported": False,
        }
    if is_nix and system in {"linux", "darwin"}:
        return {
            "status": "best_effort",
            "tier": "Tier 2",
            "label": "Nix con soporte limitado",
            "detail": "Hermes mantiene Nix, NixOS y entornos Nix como Tier 2; las actualizaciones pueden romperse.",
            "system": system_name or platform.system(),
            "architecture": normalized_arch or "desconocida",
            "supported": False,
        }

    if system == "windows" and normalized_arch in {"x86_64", "arm64"}:
        return {
            "status": "supported",
            "tier": "Tier 1",
            "label": "Windows compatible",
            "detail": "Hermes admite Windows 10/11 en x64 y ARM64 mediante su instalador nativo oficial; algunas funciones tienen límites de plataforma.",
            "system": system_name or platform.system(),
            "architecture": normalized_arch,
            "supported": True,
        }
    if system == "linux" and normalized_arch in {"x86_64", "arm64"}:
        return {
            "status": "supported",
            "tier": "Tier 1",
            "label": "Linux/WSL compatible",
            "detail": "Hermes admite Linux y WSL2 en x64 y ARM64.",
            "system": system_name or platform.system(),
            "architecture": normalized_arch,
            "supported": True,
        }
    if system == "darwin" and normalized_arch == "arm64":
        return {
            "status": "supported",
            "tier": "Tier 1",
            "label": "macOS Apple Silicon compatible",
            "detail": "Hermes admite macOS sobre Apple Silicon.",
            "system": system_name or platform.system(),
            "architecture": normalized_arch,
            "supported": True,
        }
    if system == "linux":
        return {
            "status": "best_effort",
            "tier": "Best effort",
            "label": "Plataforma con soporte limitado",
            "detail": "Esta combinación no forma parte de la matriz principal de Hermes; puede funcionar, pero IRIS no puede garantizar todas las funciones.",
            "system": system_name or platform.system(),
            "architecture": normalized_arch or "desconocida",
            "supported": False,
        }
    return {
        "status": "unsupported",
        "tier": "No compatible",
        "label": "Plataforma no compatible",
        "detail": "Hermes no publica soporte para esta combinación de sistema y arquitectura.",
        "system": system_name or platform.system(),
        "architecture": normalized_arch or "desconocida",
        "supported": False,
    }


def _check(
    check_id: str,
    label: str,
    status: str,
    summary: str,
    *,
    recommendation: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "summary": summary,
        "recommendation": recommendation,
        "meta": meta or {},
    }


def hermes_compatibility(
    *,
    version_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inspect the local Hermes installation against IRIS' safe baseline."""
    platform_info = platform_support()
    cli = resolve_hermes_cli()
    home = get_hermes_home()
    repo = get_hermes_repo()
    config = get_hermes_config_path()
    checks: list[dict[str, Any]] = []
    recommendations: list[str] = []

    platform_status = "ok" if platform_info["supported"] else (
        "warning" if platform_info["status"] == "best_effort" else "error"
    )
    platform_recommendation = ""
    if platform_status == "warning":
        platform_recommendation = "Usa una plataforma Tier 1 de Hermes antes de habilitar automatizaciones críticas."
    elif platform_status == "error":
        platform_recommendation = "Instala Hermes en Windows 10/11, Linux/WSL2 o macOS Apple Silicon compatibles."
    checks.append(_check(
        "iris_platform",
        "Plataforma",
        platform_status,
        str(platform_info["detail"]),
        recommendation=platform_recommendation,
        meta=platform_info,
    ))
    if platform_recommendation:
        recommendations.append(platform_recommendation)

    layout_ok = bool(cli and repo.exists())
    layout_recommendation = ""
    if not cli:
        layout_recommendation = "Instala o repara Hermes desde el asistente de IRIS; luego vuelve a ejecutar el diagnóstico."
    elif not repo.exists():
        layout_recommendation = "El lanzador existe, pero IRIS no localiza su instalación administrada. Repara Hermes antes de actualizarlo."
    checks.append(_check(
        "iris_install_layout",
        "Instalación administrada",
        "ok" if layout_ok else "error",
        "Lanzador y repositorio local detectados." if layout_ok else (
            "No se encontró el lanzador de Hermes." if not cli else "La instalación de Hermes parece incompleta."
        ),
        recommendation=layout_recommendation,
        meta={
            "cli_path": cli or "",
            "home": str(home),
            "repo": str(repo),
            "repo_exists": repo.exists(),
            "config_exists": config.exists(),
        },
    ))
    if layout_recommendation:
        recommendations.append(layout_recommendation)

    version_probe = (
        version_result
        if version_result is not None
        else run_hermes_cli(["--version"], timeout=8) if cli else {}
    )
    version_text = str(version_probe.get("output") or "")
    version = parse_hermes_version(version_text)
    if not cli:
        version_status = "error"
        version_summary = "No hay versión que comprobar porque Hermes no está instalado."
        version_recommendation = "Instala Hermes antes de habilitar el runtime de IRIS."
    elif not version_probe.get("success"):
        version_status = "error"
        version_summary = "IRIS localizó Hermes, pero el comando de versión no pudo ejecutarse."
        version_recommendation = "Repara Hermes: un lanzador roto no es una instalación segura para IRIS."
    elif not version:
        version_status = "warning"
        version_summary = "Hermes respondió, pero no expuso una versión semántica reconocible."
        version_recommendation = "Actualiza Hermes y vuelve a comprobar la compatibilidad."
    elif version < IRIS_MIN_HERMES_VERSION:
        version_status = "warning"
        version_summary = (
            f"Hermes {format_version(version)} funciona en modo de compatibilidad parcial; "
            f"IRIS valida completamente desde {IRIS_MIN_HERMES_VERSION_TEXT}."
        )
        version_recommendation = "Actualiza Hermes para habilitar las APIs modernas de capabilities y runs."
    elif version[:2] not in IRIS_VALIDATED_HERMES_SERIES:
        version_status = "warning"
        version_summary = (
            f"Hermes {format_version(version)} cumple el mínimo, pero está fuera de la "
            f"familia validada por IRIS ({IRIS_VALIDATED_HERMES_VERSION})."
        )
        version_recommendation = "Comprueba las capacidades en tiempo de ejecución antes de habilitar automatizaciones críticas."
    else:
        version_status = "ok"
        version_summary = (
            f"Hermes {format_version(version)} cumple el mínimo de IRIS "
            f"({IRIS_MIN_HERMES_VERSION_TEXT}; validado con {IRIS_VALIDATED_HERMES_VERSION})."
        )
        version_recommendation = ""
    checks.append(_check(
        "iris_hermes_version",
        "Versión compatible",
        version_status,
        version_summary,
        recommendation=version_recommendation,
        meta={
            "installed": format_version(version),
            "minimum": IRIS_MIN_HERMES_VERSION_TEXT,
            "validated": IRIS_VALIDATED_HERMES_VERSION,
            "raw": version_text[:300],
        },
    ))
    if version_recommendation:
        recommendations.append(version_recommendation)

    config_status = "ok" if config.exists() else "warning"
    config_recommendation = "" if config.exists() else "Completa la selección de proveedor/modelo en IRIS antes de iniciar el gateway."
    checks.append(_check(
        "iris_hermes_config",
        "Configuración base",
        config_status,
        "config.yaml detectado." if config.exists() else "Aún no hay config.yaml; es normal antes de terminar el setup.",
        recommendation=config_recommendation,
        meta={"path": str(config), "exists": config.exists()},
    ))
    if config_recommendation:
        recommendations.append(config_recommendation)

    statuses = {str(item["status"]) for item in checks}
    if "error" in statuses:
        overall = "error"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "ready"

    label = {
        "ready": "Compatible con IRIS",
        "warning": "Compatible con límites",
        "error": "Hermes requiere atención",
    }[overall]
    headline = {
        "ready": "La base local de Hermes cumple la política de compatibilidad de IRIS.",
        "warning": "IRIS puede continuar, pero algunas superficies avanzadas se degradarán o necesitarán actualización.",
        "error": "IRIS no debería habilitar automatizaciones hasta reparar o instalar Hermes.",
    }[overall]
    return {
        "success": overall != "error",
        "overall_status": overall,
        "summary": {
            "label": label,
            "headline": headline,
            "minimum_version": IRIS_MIN_HERMES_VERSION_TEXT,
            "validated_version": IRIS_VALIDATED_HERMES_VERSION,
        },
        "platform": platform_info,
        "version": {
            "installed": format_version(version),
            "minimum": IRIS_MIN_HERMES_VERSION_TEXT,
            "validated": IRIS_VALIDATED_HERMES_VERSION,
        },
        "feature_contract": {
            "strategy": "runtime_probe",
            "endpoint": "/api/hermes/capabilities",
            "version_is_guardrail": True,
        },
        "checks": checks,
        "recommendations": recommendations,
    }


__all__ = [
    "IRIS_MIN_HERMES_VERSION",
    "IRIS_MIN_HERMES_VERSION_TEXT",
    "IRIS_VALIDATED_HERMES_SERIES",
    "IRIS_VALIDATED_HERMES_VERSION",
    "format_version",
    "hermes_compatibility",
    "parse_hermes_version",
    "platform_support",
]
