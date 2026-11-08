"""Version-aware Hermes slash-command catalogue for IRIS OS.

Hermes owns the canonical command registry.  IRIS reads that registry from the
managed Hermes environment instead of copying a version-specific list into the
frontend.  Only a deliberately small allow-list is executable from IRIS:

* browser-native navigation and settings actions;
* read-only Hermes diagnostics (version, profile and status);
* installed, enabled and eligible skills, forwarded through the normal agent
  turn so they retain personality, memory and tool reporting.

Every other Hermes command remains discoverable but is labelled as terminal
only.  This avoids the old failure mode where ``/update`` or ``/yolo`` was sent
to the model as ordinary prose even though IRIS could not perform the command.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from difflib import get_close_matches
from typing import Any

from core.hermes_runtime import (
    NO_WINDOW,
    clean_cli_output,
    get_hermes_repo,
    hermes_python_prefix,
    hermes_subprocess_env,
    hermes_timeout,
    resolve_hermes_cli,
    resolve_hermes_python,
    run_hermes_cli,
)


_CACHE_TTL_S = 60.0
_CATALOGUE_CACHE: dict[str, Any] = {"ts": 0.0, "commands": [], "source": ""}

# IRIS-native entries share the same surface as Hermes commands, but are kept
# separate from the upstream registry so their provenance is always visible.
_IRIS_COMMANDS: tuple[dict[str, Any], ...] = (
    {"name": "apps", "description": "Abrir el lanzador de aplicaciones de IRIS OS.", "category": "IRIS OS", "action": "apps"},
    {"name": "settings", "description": "Abrir la configuración avanzada de IRIS OS.", "category": "IRIS OS", "action": "settings"},
    {"name": "theme", "description": "Abrir Theme Studio y personalizar el escritorio.", "category": "IRIS OS", "action": "theme"},
    {"name": "camera", "description": "Abrir los controles de cámara de IRIS OS.", "category": "IRIS OS", "action": "camera"},
    {"name": "doctor", "description": "Abrir Hermes Doctor y sus diagnósticos.", "category": "IRIS OS", "action": "doctor"},
    {"name": "activity", "description": "Abrir el registro de actividad de Hermes.", "category": "IRIS OS", "action": "activity"},
    {"name": "missions", "description": "Abrir el tablero de misiones colaborativas.", "category": "IRIS OS", "action": "missions"},
    {"name": "routines", "description": "Abrir las rutinas programadas de Hermes.", "category": "IRIS OS", "action": "routines"},
    {"name": "chat", "description": "Volver al chat y enfocar el compositor.", "category": "IRIS OS", "action": "chat"},
)

_CLIENT_ACTIONS = {
    "help": "palette",
    "commands": "palette",
    "agents": "agents",
    "skills": "skills",
    "cron": "routines",
    "kanban": "missions",
    "tools": "tools",
    "wake": "wake",
    "voice": "settings",
    "reasoning": "reasoning",
    "model": "model",
}
_SERVER_READ_ONLY = {"status", "profile", "version"}
_CLIENT_SUPPORTED_SUBCOMMANDS = {
    "reasoning": ("none", "low", "medium", "high"),
    "wake": ("on", "off", "status"),
}
_DESCRIPTION_OVERRIDES = {
    "help": "Explorar los comandos disponibles de Hermes e IRIS OS.",
    "commands": "Explorar todos los comandos y skills disponibles.",
    "agents": "Abrir los agentes y tareas en ejecución.",
    "skills": "Abrir el catálogo de skills instaladas.",
    "cron": "Abrir y administrar rutinas programadas.",
    "kanban": "Abrir el tablero de misiones colaborativas.",
    "tools": "Abrir las herramientas MCP conectadas.",
    "wake": "Controlar o consultar la escucha “Hey Hermes”.",
    "voice": "Abrir la configuración de voz.",
    "reasoning": "Cambiar el esfuerzo de razonamiento de IRIS.",
    "model": "Elegir el modelo de esta conversación o el predeterminado de Hermes.",
    "status": "Mostrar el estado actual de Hermes Agent.",
    "profile": "Mostrar el perfil activo y su directorio.",
    "version": "Mostrar la versión instalada de Hermes Agent.",
}

_REGISTRY_SCRIPT = r"""
import json
from hermes_cli.commands import COMMAND_REGISTRY

rows = []
for cmd in COMMAND_REGISTRY:
    rows.append({
        "name": cmd.name,
        "description": cmd.description,
        "category": cmd.category,
        "aliases": list(cmd.aliases),
        "args_hint": cmd.args_hint,
        "subcommands": list(cmd.subcommands),
        "cli_only": bool(cmd.cli_only),
        "gateway_only": bool(cmd.gateway_only),
        "busy_policy": cmd.busy_policy,
    })
print("IRIS_COMMANDS_JSON=" + json.dumps(rows, ensure_ascii=False))
"""


def _installed_registry() -> tuple[list[dict[str, Any]], str]:
    """Read the registry with Hermes' own Python, returning a safe fallback."""
    python = resolve_hermes_python()
    repo = get_hermes_repo()
    prefix = hermes_python_prefix(python) if python else None
    if prefix and repo.exists():
        try:
            proc = subprocess.run(
                [*prefix, "-c", _REGISTRY_SCRIPT],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=hermes_timeout(15),
                cwd=str(repo),
                env=hermes_subprocess_env(),
                check=False,
                creationflags=NO_WINDOW,
            )
            for line in reversed((proc.stdout or "").splitlines()):
                if not line.startswith("IRIS_COMMANDS_JSON="):
                    continue
                rows = json.loads(line.split("=", 1)[1])
                if isinstance(rows, list) and rows:
                    return [row for row in rows if isinstance(row, dict)], "hermes-registry"
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            pass

    # The fallback is intentionally small. It keeps essential navigation and
    # diagnostics usable while clearly reporting that Hermes' live registry
    # could not be inspected; it never invents support for mutation commands.
    rows = [
        {"name": "help", "description": "Show available commands", "category": "Info"},
        {"name": "status", "description": "Show session and model status", "category": "Session"},
        {"name": "profile", "description": "Show active profile", "category": "Info"},
        {"name": "version", "description": "Show Hermes Agent version", "category": "Info", "aliases": ["v"]},
        {"name": "reasoning", "description": "Manage reasoning effort", "category": "Configuration", "subcommands": ["none", "low", "medium", "high"]},
        {"name": "wake", "description": "Toggle the Hey Hermes listener", "category": "Configuration", "subcommands": ["on", "off", "status"]},
        {"name": "voice", "description": "Toggle voice mode", "category": "Configuration", "subcommands": ["on", "off", "tts", "status"]},
        {"name": "skills", "description": "Manage skills", "category": "Tools & Skills"},
        {"name": "cron", "description": "Manage scheduled tasks", "category": "Tools & Skills"},
        {"name": "kanban", "description": "Manage collaborative tasks", "category": "Tools & Skills"},
        {"name": "agents", "description": "Show active agents", "category": "Session"},
        {"name": "tools", "description": "Manage tools", "category": "Tools & Skills"},
    ]
    return rows, "iris-fallback"


def _normalise_registry_row(raw: dict[str, Any], *, runtime_available: bool = True) -> dict[str, Any] | None:
    name = str(raw.get("name") or "").strip().lower().lstrip("/")
    if not name:
        return None
    execution = "terminal"
    action = ""
    available = False
    availability = "Solo terminal"
    reason = "Hermes expone este comando en su CLI, pero IRIS OS todavía no tiene un ejecutor equivalente seguro."
    if name in _CLIENT_ACTIONS:
        execution = "client"
        action = _CLIENT_ACTIONS[name]
        available = True
        availability = "Disponible"
        reason = ""
    elif name in _SERVER_READ_ONLY:
        execution = "server"
        available = runtime_available
        availability = "Disponible" if runtime_available else "Hermes no instalado"
        reason = "" if runtime_available else "Hermes Agent no está instalado o su launcher no es accesible."
    return {
        "name": name,
        "command": f"/{name}",
        "description": _DESCRIPTION_OVERRIDES.get(name, str(raw.get("description") or "Comando de Hermes.")),
        "category": str(raw.get("category") or "Hermes"),
        "aliases": [str(alias).lower().lstrip("/") for alias in (raw.get("aliases") or []) if str(alias).strip()],
        "args_hint": str(raw.get("args_hint") or ""),
        "subcommands": [str(item) for item in (raw.get("subcommands") or []) if str(item).strip()],
        "integrated_subcommands": list(_CLIENT_SUPPORTED_SUBCOMMANDS.get(name, ())),
        "cli_only": bool(raw.get("cli_only")),
        "gateway_only": bool(raw.get("gateway_only")),
        "source": "Hermes",
        "execution": execution,
        "action": action,
        "available": available,
        "availability": availability,
        "reason": reason,
    }


def _skill_commands(reserved: set[str]) -> list[dict[str, Any]]:
    try:
        from core.skills_bridge import list_installed_skills

        skills = list_installed_skills()
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for skill in skills:
        name = str(skill.get("name") or "").strip().lower().lstrip("/")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", name) or name in reserved:
            continue
        enabled = bool(skill.get("enabled"))
        eligible = bool(skill.get("eligible", True))
        available = enabled and eligible
        if not enabled:
            reason = "Activa esta skill en Skills Hub antes de usarla como comando."
        elif not eligible:
            reason = "La skill está instalada, pero le faltan dependencias en este equipo."
        else:
            reason = ""
        rows.append({
            "name": name,
            "command": f"/{name}",
            "description": str(skill.get("description") or "Skill instalada en Hermes."),
            "category": str(skill.get("category") or "Skills"),
            "aliases": [],
            "args_hint": "[solicitud]",
            "subcommands": [],
            "integrated_subcommands": [],
            "cli_only": False,
            "gateway_only": False,
            "source": "Skill",
            "execution": "skill",
            "action": "",
            "available": available,
            "availability": "Disponible" if available else "No disponible",
            "reason": reason,
        })
    return rows


def hermes_command_catalogue(*, refresh: bool = False) -> dict[str, Any]:
    """Return the live Hermes registry plus IRIS actions and installed skills."""
    now = time.monotonic()
    if not refresh and _CATALOGUE_CACHE["commands"] and now - _CATALOGUE_CACHE["ts"] < _CACHE_TTL_S:
        return {
            "commands": [dict(row) for row in _CATALOGUE_CACHE["commands"]],
            "source": _CATALOGUE_CACHE["source"],
            "updated_at": _CATALOGUE_CACHE["updated_at"],
            "hermes_available": _CATALOGUE_CACHE["hermes_available"],
        }

    raw_rows, source = _installed_registry()
    hermes_available = bool(resolve_hermes_cli())
    commands = [row for raw in raw_rows if (row := _normalise_registry_row(raw, runtime_available=hermes_available))]
    reserved = {row["name"] for row in commands}
    reserved.update(alias for row in commands for alias in (row.get("aliases") or []))
    for definition in _IRIS_COMMANDS:
        if definition["name"] in reserved:
            continue
        commands.append({
            **definition,
            "command": f"/{definition['name']}",
            "aliases": [],
            "args_hint": "",
            "subcommands": [],
            "integrated_subcommands": [],
            "cli_only": False,
            "gateway_only": False,
            "source": "IRIS",
            "execution": "client",
            "available": True,
            "availability": "Disponible",
            "reason": "",
        })
        reserved.add(definition["name"])
    commands.extend(_skill_commands(reserved))
    commands.sort(key=lambda row: (not row["available"], row["category"].lower(), row["name"]))
    updated_at = time.time()
    _CATALOGUE_CACHE.update(
        ts=now,
        commands=commands,
        source=source,
        updated_at=updated_at,
        hermes_available=hermes_available,
    )
    return {
        "commands": [dict(row) for row in commands],
        "source": source,
        "updated_at": updated_at,
        "hermes_available": hermes_available,
    }


def _parse_command_line(line: str) -> tuple[str, str]:
    raw = str(line or "").strip()
    if not raw.startswith("/"):
        return "", ""
    body = raw[1:].strip()
    if not body:
        return "", ""
    parts = body.split(maxsplit=1)
    return parts[0].lower(), parts[1].strip() if len(parts) > 1 else ""


def execute_hermes_command(line: str) -> dict[str, Any]:
    """Execute only the server-side allow-list or prepare an enabled skill turn."""
    requested, args = _parse_command_line(line)
    catalogue = hermes_command_catalogue()
    commands = catalogue["commands"]
    aliases: dict[str, dict[str, Any]] = {}
    for row in commands:
        aliases[row["name"]] = row
        for alias in row.get("aliases") or []:
            aliases[str(alias)] = row
    item = aliases.get(requested)
    if not item:
        close = get_close_matches(requested, aliases.keys(), n=3, cutoff=0.55)
        suffix = f" Quizá quisiste decir: {', '.join('/' + name for name in close)}." if close else ""
        return {"ok": False, "kind": "message", "text": f"No reconozco /{requested}.{suffix}"}
    if not item.get("available"):
        return {"ok": False, "kind": "message", "text": item.get("reason") or "Este comando no está disponible en IRIS OS."}
    if item.get("execution") == "skill":
        request = args or "Ejecuta esta skill y explícame brevemente el resultado."
        prompt = (
            f"Usa la skill instalada de Hermes '{item['name']}' para atender esta solicitud. "
            "Respeta sus instrucciones y no simules capacidades que no estén disponibles.\n\n"
            f"Solicitud del usuario: {request}"
        )
        return {"ok": True, "kind": "agent_prompt", "prompt": prompt, "memory_text": str(line).strip(), "command": item["name"]}
    if item.get("execution") != "server":
        return {"ok": False, "kind": "message", "text": "Este comando se ejecuta en la interfaz de IRIS OS."}
    if args:
        return {"ok": False, "kind": "message", "text": f"/{item['name']} no acepta argumentos dentro de IRIS OS."}

    cli_args = ["--version"] if item["name"] == "version" else [item["name"]]
    result = run_hermes_cli(cli_args, timeout=15)
    output = clean_cli_output(result.get("output") or result.get("stdout") or result.get("stderr") or result.get("error") or "")
    if len(output) > 5000:
        output = output[:5000].rstrip() + "\n…[salida truncada]"
    if not result.get("success"):
        return {"ok": False, "kind": "message", "text": output or f"Hermes no pudo ejecutar /{item['name']}."}
    return {"ok": True, "kind": "message", "text": output or f"/{item['name']} completado.", "command": item["name"]}


__all__ = ["execute_hermes_command", "hermes_command_catalogue"]
