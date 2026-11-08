"""Hermes Doctor checks for IRIS' setup center.

This module keeps the setup/health logic separate from the IRIS OS UI.
It only inspects Hermes through shared runtime helpers and returns a secret-free
report that the frontend can render later as a setup center.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.hermes_runtime import (
    clean_cli_output,
    get_hermes_config_path,
    get_hermes_env_path,
    get_hermes_home,
    get_hermes_repo,
    resolve_hermes_cli,
    run_hermes_cli,
)
from core.hermes_compatibility import hermes_compatibility


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _line_value(output: str, label: str) -> str:
    pattern = rf"^\s*{re.escape(label)}\s*:\s*(.+?)\s*$"
    match = re.search(pattern, output or "", flags=re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else ""


def _path_state(path: Path) -> dict[str, Any]:
    exists = path.exists()
    meta: dict[str, Any] = {
        "path": str(path),
        "exists": exists,
        "kind": "missing",
    }
    if not exists:
        return meta

    try:
        stat = path.stat()
        meta.update({
            "kind": "directory" if path.is_dir() else "file",
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        })
        if path.is_file():
            meta["size_bytes"] = stat.st_size
    except OSError as exc:
        meta["error"] = str(exc)
    return meta


def _check(
    check_id: str,
    label: str,
    status: str,
    summary: str,
    *,
    detail: str = "",
    recommendation: str = "",
    actions: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "summary": summary,
        "detail": detail,
        "recommendation": recommendation,
        "actions": actions or [],
        "meta": meta or {},
    }


def _output_excerpt(result: dict[str, Any], limit: int = 700) -> str:
    text = clean_cli_output(str(result.get("error") or result.get("output") or ""))
    if len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def _command_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": bool(result.get("success")),
        "returncode": result.get("returncode"),
        "error": _output_excerpt(result, 500) if not result.get("success") else "",
        "cli_path": result.get("cli_path") or "",
    }


def _parse_status(output: str) -> dict[str, str]:
    return {
        "project": _line_value(output, "Project"),
        "python": _line_value(output, "Python"),
        "model": _line_value(output, "Model"),
        "provider": _line_value(output, "Provider"),
        "env_file": _line_value(output, ".env file"),
        "gateway_service": _line_value(output, "Gateway Service"),
        "scheduled_jobs": _line_value(output, "Scheduled Jobs"),
        "sessions": _line_value(output, "Sessions"),
    }


def _summarize_skills(output: str) -> dict[str, Any]:
    text = output or ""
    enabled = re.search(r"(\d+)\s+enabled", text, flags=re.IGNORECASE)
    disabled = re.search(r"(\d+)\s+disabled", text, flags=re.IGNORECASE)
    builtin = re.search(r"(\d+)\s+builtin", text, flags=re.IGNORECASE)
    hub = re.search(r"(\d+)\s+hub[-\s]?installed", text, flags=re.IGNORECASE)
    local = re.search(r"(\d+)\s+local", text, flags=re.IGNORECASE)
    return {
        "enabled": int(enabled.group(1)) if enabled else None,
        "disabled": int(disabled.group(1)) if disabled else None,
        "builtin": int(builtin.group(1)) if builtin else None,
        "hub_installed": int(hub.group(1)) if hub else None,
        "local": int(local.group(1)) if local else None,
    }


def _summarize_mcp(output: str) -> dict[str, Any]:
    rows = []
    for line in (output or "").splitlines():
        clean = line.strip()
        if not clean or clean.lower().startswith(("name ", "install ", "catalog", "available")):
            continue
        if re.match(r"^[A-Za-z0-9_.-]+\s+\w+", clean):
            rows.append(clean)
    return {"catalog_rows": len(rows), "rows_preview": rows[:8]}


def _summarize_cron(output: str) -> dict[str, Any]:
    text = output or ""
    gateway_down = bool(re.search(r"gateway\s+is\s+not\s+running|will\s+not\s+fire", text, re.I))
    gateway_up = bool(re.search(r"gateway\s+is\s+running|scheduler\s+running", text, re.I))
    no_jobs = bool(re.search(r"no\s+scheduled\s+jobs", text, re.I))
    return {
        "gateway_running": False if gateway_down else (True if gateway_up else None),
        "no_jobs": no_jobs,
        "warning": "Gateway detenido: las rutinas no se ejecutan solas." if gateway_down else "",
    }


def _kanban_probe() -> dict[str, Any]:
    try:
        from core.hermes_kanban import read_board

        result = read_board()
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc)[:700],
            "counts": {},
            "task_count": None,
        }

    tasks = result.get("tasks") if isinstance(result, dict) else []
    return {
        "success": bool(result.get("success")) if isinstance(result, dict) else False,
        "error": str(result.get("error") or "")[:700] if isinstance(result, dict) else "",
        "board": result.get("board") if isinstance(result, dict) else "",
        "boards": result.get("boards") if isinstance(result, dict) else [],
        "counts": result.get("counts") if isinstance(result, dict) else {},
        "task_count": len(tasks) if isinstance(tasks, list) else None,
    }


def _overall_status(checks: list[dict[str, Any]]) -> str:
    statuses = {str(c.get("status")) for c in checks}
    if "error" in statuses:
        return "error"
    if "warning" in statuses:
        return "warning"
    return "ready"


def hermes_doctor() -> dict[str, Any]:
    """Run a Hermes setup/health inspection for IRIS.

    The report intentionally avoids raw .env contents, tokens, API keys, and
    unrestricted command output. It is suitable for frontend display.
    """
    home = get_hermes_home()
    repo = get_hermes_repo()
    config = get_hermes_config_path()
    env_file = get_hermes_env_path()
    cli = resolve_hermes_cli()

    checks: list[dict[str, Any]] = []

    checks.append(_check(
        "hermes_home",
        "Hermes home",
        "ok" if home.exists() else "warning",
        "Hermes home encontrado." if home.exists() else "Hermes home no existe todavia.",
        recommendation="" if home.exists() else "Ejecuta Hermes una vez para inicializar su carpeta local.",
        meta=_path_state(home),
    ))
    checks.append(_check(
        "hermes_repo",
        "Hermes install",
        "ok" if repo.exists() else "error",
        "Instalacion de Hermes encontrada." if repo.exists() else "No se encontro la instalacion de Hermes.",
        recommendation="" if repo.exists() else "Reinstala Hermes o define HERMES_REPO/HERMES_AGENT_ROOT.",
        meta=_path_state(repo),
    ))
    checks.append(_check(
        "hermes_cli",
        "Hermes CLI",
        "ok" if cli else "error",
        "Hermes CLI listo." if cli else "IRIS no puede encontrar el ejecutable de Hermes.",
        recommendation="" if cli else "Verifica la instalacion facil de Hermes o reinicia la terminal para refrescar PATH.",
        meta={"cli_path": cli or ""},
    ))
    checks.append(_check(
        "config_file",
        "Config",
        "ok" if config.exists() else "warning",
        "config.yaml existe." if config.exists() else "config.yaml no existe.",
        recommendation="" if config.exists() else "Completa el setup de Hermes para crear config.yaml.",
        meta=_path_state(config),
    ))
    checks.append(_check(
        "env_file",
        "Environment",
        "ok" if env_file.exists() else "info",
        ".env existe sin exponer secretos." if env_file.exists() else ".env no existe; es válido cuando Hermes gestiona credenciales mediante OAuth u otro almacén.",
        recommendation="",
        meta=_path_state(env_file),
    ))

    commands = {
        "version": (["--version"], 6),
        "status": (["status"], 10),
        "cron": (["cron", "status"], 10),
        "skills": (["skills", "list", "--source", "all"], 12),
        "mcp": (["mcp", "catalog"], 10),
    }
    with ThreadPoolExecutor(max_workers=len(commands)) as executor:
        futures = {
            name: executor.submit(run_hermes_cli, args, timeout=timeout)
            for name, (args, timeout) in commands.items()
        }
        results = {name: future.result() for name, future in futures.items()}

    version = results["version"]
    status = results["status"]
    cron = results["cron"]
    skills = results["skills"]
    mcp = results["mcp"]
    kanban = _kanban_probe()

    # Reuse the version probe already executed in the parallel Doctor batch.
    # This avoids invoking the Hermes launcher twice on every refresh while
    # keeping the IRIS policy visible next to Hermes' own health checks.
    compatibility = hermes_compatibility(version_result=version)
    checks.extend(compatibility.get("checks", []))

    parsed_status = _parse_status(status.get("output", ""))
    cron_summary = _summarize_cron(cron.get("output", ""))
    skills_summary = _summarize_skills(skills.get("output", ""))
    skills_browse_summary = {"deferred": True}
    mcp_summary = _summarize_mcp(mcp.get("output", ""))

    checks.append(_check(
        "version",
        "Hermes version",
        "ok" if version.get("success") else "warning",
        (version.get("output") or "Version no disponible.")[:160],
        detail=_output_excerpt(version) if not version.get("success") else "",
        recommendation="" if version.get("success") else "Actualiza Hermes o verifica que el CLI responda.",
        meta=_command_summary(version),
    ))
    checks.append(_check(
        "status",
        "Runtime status",
        "ok" if status.get("success") else "error",
        "Hermes status responde." if status.get("success") else "Hermes status fallo.",
        detail=_output_excerpt(status) if not status.get("success") else "",
        recommendation="" if status.get("success") else "Ejecuta `hermes status` fuera de IRIS para ver el error completo.",
        meta={"parsed": parsed_status, "command": _command_summary(status)},
    ))

    gateway_text = (parsed_status.get("gateway_service") or "").lower()
    gateway_stopped = "stopped" in gateway_text or cron_summary.get("gateway_running") is False
    checks.append(_check(
        "gateway",
        "Gateway",
        "warning" if gateway_stopped else ("ok" if parsed_status.get("gateway_service") else "info"),
        "Gateway detenido; las rutinas pueden no dispararse solas." if gateway_stopped else "Gateway sin bloqueo detectado.",
        recommendation="Inicia el gateway desde IRIS o Hermes antes de probar rutinas/cron." if gateway_stopped else "",
        actions=[{
            "id": "start_gateway",
            "label": "Iniciar gateway",
            "method": "POST",
            "endpoint": "/api/start-gateway",
            "risk": "safe",
        }] if gateway_stopped else [],
        meta={"gateway_service": parsed_status.get("gateway_service"), "cron": cron_summary},
    ))
    checks.append(_check(
        "cron",
        "Cron/Routines",
        "ok" if cron.get("success") and not cron_summary.get("warning") else "warning",
        cron_summary.get("warning") or ("Cron responde." if cron.get("success") else "Cron no respondio correctamente."),
        detail=_output_excerpt(cron) if not cron.get("success") else "",
        recommendation="Arranca el gateway para que los cron se ejecuten y entreguen resultados." if cron_summary.get("warning") else "",
        meta={"summary": cron_summary, "command": _command_summary(cron)},
    ))
    checks.append(_check(
        "skills_installed",
        "Installed skills",
        "ok" if skills.get("success") else "warning",
        "Skills instaladas/listadas por Hermes." if skills.get("success") else "No se pudo leer la lista de skills.",
        detail=_output_excerpt(skills) if not skills.get("success") else "",
        recommendation="" if skills.get("success") else "Prueba `hermes skills list --source all` para validar el catalogo local.",
        meta={"summary": skills_summary, "command": _command_summary(skills)},
    ))
    checks.append(_check(
        "skills_marketplace",
        "Skills marketplace",
        "info",
        "Marketplace diferido para que Doctor abra rapido.",
        detail="Abre la tab Skills o usa Refresh alli para leer Hermes Skill Hub.",
        recommendation="",
        meta={"summary": skills_browse_summary, "deferred_to": "/api/skills/marketplace"},
    ))
    checks.append(_check(
        "mcp_catalog",
        "MCP catalog",
        "ok" if mcp.get("success") else "warning",
        "Catalogo MCP disponible." if mcp.get("success") else "No se pudo leer el catalogo MCP.",
        detail=_output_excerpt(mcp) if not mcp.get("success") else "",
        recommendation="" if mcp.get("success") else "Prueba `hermes mcp catalog` y revisa si Hermes esta actualizado.",
        meta={"summary": mcp_summary, "command": _command_summary(mcp)},
    ))
    checks.append(_check(
        "kanban",
        "Mission board",
        "ok" if kanban.get("success") else "warning",
        "Kanban de Hermes responde." if kanban.get("success") else "Kanban/Mission Board no respondio.",
        detail=str(kanban.get("error") or "")[:700],
        recommendation="" if kanban.get("success") else "Revisa que Hermes tenga disponible `hermes_cli.kanban_db` y que la instalacion este completa.",
        meta=kanban,
    ))

    issues = [c for c in checks if c.get("status") in {"warning", "error"}]
    recommendations: list[str] = []
    for recommendation in compatibility.get("recommendations", []):
        text = str(recommendation or "").strip()
        if text and text not in recommendations:
            recommendations.append(text)
    for item in issues:
        rec = str(item.get("recommendation") or "").strip()
        if rec and rec not in recommendations:
            recommendations.append(rec)

    overall = _overall_status(checks)
    compatibility_state = str(compatibility.get("overall_status") or "")
    summary_label = (
        "Hermes listo"
        if overall == "ready"
        else str(compatibility.get("summary", {}).get("label") or "Revisar Hermes")
        if compatibility_state in {"warning", "error"}
        else ("Hermes con errores" if overall == "error" else "Revisar Hermes")
    )
    return {
        "success": overall != "error",
        "generated_at": _utc_now(),
        "overall_status": overall,
        "summary": {
            "label": summary_label,
            "headline": compatibility.get("summary", {}).get("headline", ""),
            "issue_count": len(issues),
            "critical_count": sum(1 for c in checks if c.get("status") == "error"),
            "warning_count": sum(1 for c in checks if c.get("status") == "warning"),
        },
        "paths": {
            "home": _path_state(home),
            "repo": _path_state(repo),
            "config": _path_state(config),
            "env": _path_state(env_file),
            "cli": cli or "",
        },
        "runtime": {
            "version": (version.get("output") or "").strip(),
            "status": parsed_status,
            "skills": skills_summary,
            "skills_marketplace": skills_browse_summary,
            "mcp": mcp_summary,
            "cron": cron_summary,
            "kanban": kanban,
        },
        "compatibility": compatibility,
        "checks": checks,
        "issues": issues,
        "recommendations": recommendations,
        "commands": {
            "status": _command_summary(status),
            "version": _command_summary(version),
            "cron_status": _command_summary(cron),
            "skills_list": _command_summary(skills),
            "skills_browse": {"success": True, "deferred": True, "error": "", "cli_path": cli or ""},
            "mcp_catalog": _command_summary(mcp),
        },
        "quick_actions": [
            {
                "id": "refresh_doctor",
                "label": "Actualizar Doctor",
                "method": "GET",
                "endpoint": "/api/hermes/doctor",
                "risk": "safe",
            },
            {
                "id": "start_gateway",
                "label": "Iniciar Gateway",
                "method": "POST",
                "endpoint": "/api/start-gateway",
                "risk": "safe",
                "enabled": gateway_stopped,
            },
        ],
    }


__all__ = ["hermes_doctor"]
