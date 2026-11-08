"""Local capability policy and Hermes enforcement inventory for IRIS OS.

IRIS owns only the policy for actions initiated by its own surfaces. Hermes
continues to own tool availability, MCP authentication and dangerous-command
approvals. An ``allow`` decision here never starts a sensor and never bypasses
Hermes' guards; it only lets an IRIS-originated action advance to its next
controller.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC
from pathlib import Path
from typing import Any, Callable

from core.config import get_iris_home
from core.file_lock import interprocess_file_lock
from core.hermes_compatibility import parse_hermes_version
from core.hermes_mcp import list_mcp_catalog
from core.hermes_runtime import resolve_hermes_cli, run_hermes_cli
from core.runtime_descriptor import get_runtime

POLICY_SCHEMA = "iris.capability-policy/v1"
SNAPSHOT_SCHEMA = "iris.capability-center/v1"
ALLOWED_MODES = frozenset({"deny", "ask", "allow"})
ALLOWED_TRIGGERS = frozenset({"manual", "suggested", "automatic", "system"})
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_TOOL_ROW = re.compile(
    r"^\s*(?:✓|✗|x|OK)\s+(?P<state>enabled|disabled)\s+"
    r"(?P<name>[A-Za-z0-9_.:-]+)\s*(?P<description>.*)$",
    flags=re.IGNORECASE,
)


CAPABILITY_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "google.read", "category": "data", "category_label": "Datos locales",
        "label": "Correo y Calendario en el chat", "risk": "high", "default_mode": "ask",
        "hermes_tools": [],
        "description": "Consultar las cuentas, los correos y los eventos conectados en Iris desde una conversación.",
        "note": "El contenido seleccionado llega al proveedor de IA del chat. Conectar Google no concede este permiso; no permite enviar correos ni modificar eventos.",
    },
    {
        "id": "camera.capture",
        "category": "sensors",
        "category_label": "Sensores",
        "label": "Cámara",
        "risk": "high",
        "default_mode": "deny",
        "hermes_tools": [],
        "description": "Permite a una superficie local abrir la cámara y producir señales locales.",
        "note": "Permitir no enciende la cámara ni autoriza enviar imágenes a Hermes.",
    },
    {
        "id": "vision.context",
        "category": "data",
        "category_label": "Datos locales",
        "label": "Contexto perceptivo",
        "risk": "high",
        "default_mode": "deny",
        "hermes_tools": [],
        "description": "Usar una señal emocional resumida para adaptar la siguiente respuesta.",
        "note": (
            "No envía vídeo, pero el resumen puede llegar al proveedor "
            "cuando el usuario conversa."
        ),
    },
    {
        "id": "vision.snapshot",
        "category": "sensors",
        "category_label": "Sensores",
        "label": "Fotos a petición",
        "risk": "high",
        "default_mode": "ask",
        "hermes_tools": [],
        "description": (
            "Cuando le pides a Iris que mire algo, toma una foto de la cámara y la envía a tu modelo de visión."
        ),
        "note": (
            "Una foto por cada petición tuya, con el destino a la vista. No permite fotos periódicas ni en "
            "segundo plano; la cámara local y el contexto perceptivo no la autorizan."
        ),
    },
    {
        "id": "vision.ai_request",
        "category": "sensors",
        "category_label": "Sensores",
        "label": "Fotos que Iris decide pedir",
        "risk": "high",
        "default_mode": "ask",
        "hermes_tools": [],
        "description": (
            "Iris puede pedir una foto de la cámara cuando considere que la necesita para responderte, "
            "en una conversación que empezaste en este equipo."
        ),
        "note": (
            "Preguntar: Iris OS te avisa antes de cada foto. Permitir: la toma sin preguntarte si la cámara "
            "ya está encendida; si está apagada, siempre hace falta tu clic. Verás un aviso cada vez. "
            "Una foto por turno; nunca desde Telegram ni automatizaciones. Bloquear «Fotos a petición» "
            "también la bloquea."
        ),
    },
    {
        "id": "microphone.capture",
        "category": "sensors",
        "category_label": "Sensores",
        "label": "Micrófono",
        "risk": "high",
        "default_mode": "deny",
        "hermes_tools": ["stt"],
        "description": "Autoriza solicitudes locales de captura de voz o wake word.",
        "note": "El motor nativo de Hermes sigue apagado hasta una activación explícita.",
    },
    {
        "id": "files.read",
        "category": "data",
        "category_label": "Datos locales",
        "label": "Leer archivos",
        "risk": "medium",
        "default_mode": "ask",
        "hermes_tools": ["file"],
        "description": "Leer contenido seleccionado dentro del equipo o workspace.",
        "note": "No amplía el alcance del selector ni concede acceso a carpetas nuevas.",
    },
    {
        "id": "files.write",
        "category": "data",
        "category_label": "Datos locales",
        "label": "Modificar archivos",
        "risk": "high",
        "default_mode": "ask",
        "hermes_tools": ["file"],
        "description": "Crear, editar, mover o eliminar datos mediante acciones de IRIS.",
        "note": "Las operaciones destructivas continúan requiriendo las aprobaciones de Hermes.",
    },
    {
        "id": "memory.persist",
        "category": "data",
        "category_label": "Datos locales",
        "label": "Memoria local",
        "risk": "medium",
        "default_mode": "allow",
        "hermes_tools": ["memory"],
        "description": "Guardar preferencias y continuidad en los almacenes locales configurados.",
        "note": "Borrar o exportar memoria conserva confirmaciones independientes.",
    },
    {
        "id": "terminal.execute",
        "category": "actions",
        "category_label": "Acciones",
        "label": "Terminal y código",
        "risk": "critical",
        "default_mode": "ask",
        "hermes_tools": ["terminal", "code_execution"],
        "description": "Ejecutar comandos o código en el equipo desde una acción de IRIS.",
        "note": "Hermes evalúa después blocklist, deny rules, allowlist y approvals.",
    },
    {
        "id": "automations.run",
        "category": "actions",
        "category_label": "Acciones",
        "label": "Automatizaciones",
        "risk": "high",
        "default_mode": "ask",
        "hermes_tools": [],
        "description": (
            "Ejecutar en Hermes la tarea de una automatización que habilitaste cuando se cumple su condición."
        ),
        "note": (
            "Ocurre sin que estés, así que «Preguntar» no puede confirmarse: solo «Permitir» deja ejecutar. "
            "Cada regla conserva sus límites y Hermes sus aprobaciones."
        ),
    },
    {
        "id": "screen.snapshot",
        "category": "actions",
        "category_label": "Acciones",
        "label": "Capturas de pantalla a petición",
        "risk": "high",
        "default_mode": "ask",
        "hermes_tools": [],
        "description": "Analizar una captura de la ventana o pantalla que elijas para responder a tu pregunta.",
        "note": "Siempre eliges el origen y revisas la imagen antes de enviarla. No autoriza ratón, teclado ni captura continua.",
    },
    {
        "id": "screen.ai_request",
        "category": "actions",
        "category_label": "Acciones",
        "label": "Capturas que Iris decide pedir",
        "risk": "high",
        "default_mode": "ask",
        "hermes_tools": [],
        "description": (
            "Iris puede pedirte una captura de pantalla cuando considere que la necesita para responderte, "
            "en una conversación que empezaste en este equipo."
        ),
        "note": (
            "Siempre eliges la ventana o pantalla con un clic: lo exige Windows. Preguntar: revisas la imagen "
            "antes de enviarla. Permitir: se envía sin esa revisión. Verás un aviso cada vez. Una captura por "
            "turno; nunca desde Telegram ni automatizaciones. Bloquear «Capturas de pantalla a petición» "
            "también la bloquea."
        ),
    },
    {
        "id": "computer.control",
        "category": "actions",
        "category_label": "Acciones",
        "label": "Control del equipo",
        "risk": "critical",
        "default_mode": "ask",
        "hermes_tools": ["computer_use"],
        "description": "Mover el cursor, escribir o interactuar con aplicaciones visibles.",
        "note": "No se habilita automáticamente para Vision Assistant ni tareas en segundo plano.",
    },
    {
        "id": "browser.control",
        "category": "actions",
        "category_label": "Acciones",
        "label": "Navegador",
        "risk": "high",
        "default_mode": "ask",
        "hermes_tools": ["browser"],
        "description": "Navegar, hacer clic y escribir dentro del browser controlado por Hermes.",
        "note": "Sesiones, cookies y formularios siguen dentro del browser y sus propios límites.",
    },
    {
        "id": "network.remote",
        "category": "network",
        "category_label": "Red y servicios",
        "label": "Enviar datos a internet",
        "risk": "high",
        "default_mode": "ask",
        "hermes_tools": ["web", "image_gen", "tts"],
        "description": (
            "Usar proveedores, búsquedas web o servicios que reciben datos fuera del equipo."
        ),
        "note": "El destino real debe seguir visible; IRIS no cambia proveedor silenciosamente.",
    },
    {
        "id": "mcp.execute",
        "category": "network",
        "category_label": "Red y servicios",
        "label": "Herramientas MCP",
        "risk": "high",
        "default_mode": "ask",
        "hermes_tools": [],
        "description": "Invocar herramientas aportadas por servidores MCP configurados en Hermes.",
        "note": "Instalación, OAuth, selección de tools y health checks permanecen en Hermes.",
    },
    {
        "id": "agents.delegate",
        "category": "automation",
        "category_label": "Automatización",
        "label": "Delegar a agentes",
        "risk": "high",
        "default_mode": "ask",
        "hermes_tools": ["delegation"],
        "description": "Crear trabajo asíncrono o delegar subtareas a otros perfiles.",
        "note": "Cada creación debe mostrar alcance; steer y stop se negocian por capability.",
    },
    {
        "id": "notifications.proactive",
        "category": "automation",
        "category_label": "Automatización",
        "label": "Avisos proactivos",
        "risk": "low",
        "default_mode": "allow",
        "hermes_tools": [],
        "description": "Mostrar avisos locales de rutinas, privacidad, errores o Vision Assistant.",
        "note": "Permitir avisos no concede voz, cámara, tools ni ejecución automática.",
    },
    {
        "id": "player.control",
        "category": "automation",
        "category_label": "Automatización",
        "label": "IRIS Player",
        "risk": "low",
        "default_mode": "ask",
        "hermes_tools": [],
        "description": (
            "Controlar reproducción dentro de la biblioteca local elegida por el usuario."
        ),
        "note": "No amplía la carpeta autorizada y evita reintentos automáticos ambiguos.",
    },
    {
        # F09.4: actions that reviewed IRIS OS apps register for Hermes.
        "id": "apps.actions",
        "category": "automation",
        "category_label": "Automatización",
        "label": "Acciones de apps de IRIS OS",
        "risk": "medium",
        "default_mode": "ask",
        "hermes_tools": [],
        "description": "Usar las acciones registradas de las apps incluidas o instaladas explícitamente en Iris Store.",
        "note": "Solo acciones registradas y validadas por Core; cada una se confirma en el equipo si lo pides.",
    },
)

_CAPABILITIES_BY_ID = {item["id"]: item for item in CAPABILITY_DEFINITIONS}
DEFAULT_POLICIES = {item["id"]: item["default_mode"] for item in CAPABILITY_DEFINITIONS}
PRESETS: dict[str, dict[str, str]] = {
    "strict": {item["id"]: "deny" for item in CAPABILITY_DEFINITIONS},
    "balanced": dict(DEFAULT_POLICIES),
    "collaborative": {
        **DEFAULT_POLICIES,
        "camera.capture": "ask",
        "microphone.capture": "ask",
        "files.read": "allow",
        "browser.control": "allow",
        "notifications.proactive": "allow",
        "player.control": "allow",
    },
}


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


def _clean_text(value: Any, limit: int = 280) -> str:
    return " ".join(str(value or "").split())[:limit]


def _hermes_version_label(output: Any) -> str:
    text = str(output or "")
    parsed = parse_hermes_version(text)
    if parsed is not None:
        return f"Hermes Agent {parsed[0]}.{parsed[1]}.{parsed[2]}"
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return _clean_text(first_line, 96)


class CapabilityPermissionStore:
    """Atomic, bounded and secret-free local permission ledger."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (get_iris_home() / "capability_permissions.json")
        self.lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")

    @staticmethod
    def _blank() -> dict[str, Any]:
        return {"schema": POLICY_SCHEMA, "policies": {}, "audit": [], "action_grants": {}}

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return self._blank()
        if not isinstance(raw, dict):
            return self._blank()
        policies: dict[str, dict[str, str]] = {}
        raw_policies = raw.get("policies")
        if not isinstance(raw_policies, dict):
            raw_policies = {}
        for capability_id, value in raw_policies.items():
            if capability_id not in _CAPABILITIES_BY_ID or not isinstance(value, dict):
                continue
            mode = str(value.get("mode") or "").lower()
            if mode not in ALLOWED_MODES:
                continue
            policies[capability_id] = {
                "mode": mode,
                "updated_at": _clean_text(value.get("updated_at"), 64),
            }
        audit: list[dict[str, str]] = []
        rows = raw.get("audit") if isinstance(raw.get("audit"), list) else []
        for row in rows[-160:]:
            if not isinstance(row, dict):
                continue
            audit.append(
                {
                    "at": _clean_text(row.get("at"), 64),
                    "kind": _clean_text(row.get("kind"), 48),
                    "capability": _clean_text(row.get("capability"), 96),
                    "verdict": _clean_text(row.get("verdict"), 32),
                    "detail": _clean_text(row.get("detail"), 240),
                }
            )
        grants = {}
        raw_grants = raw.get("action_grants")
        grant_rows = list(raw_grants.items())[-160:] if isinstance(raw_grants, dict) else []
        for key, row in grant_rows:
            if (isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key) and isinstance(row, dict)
                    and row.get("capability") in _CAPABILITIES_BY_ID):
                grants[key] = {name: _clean_text(row.get(name), 160) for name in
                               ("app", "action", "capability", "label", "updated_at")}
        return {"schema": POLICY_SCHEMA, "policies": policies, "audit": audit, "action_grants": grants}

    def _write_unlocked(self, document: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(document, ensure_ascii=False, indent=2)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _append_audit(
        document: dict[str, Any],
        *,
        kind: str,
        capability: str = "",
        verdict: str = "",
        detail: str = "",
    ) -> None:
        rows = document.setdefault("audit", [])
        rows.append(
            {
                "at": _now_iso(),
                "kind": _clean_text(kind, 48),
                "capability": _clean_text(capability, 96),
                "verdict": _clean_text(verdict, 32),
                "detail": _clean_text(detail, 240),
            }
        )
        document["audit"] = rows[-160:]

    def snapshot(self) -> dict[str, Any]:
        with interprocess_file_lock(self.lock_path):
            document = self._read_unlocked()
        policies = document["policies"]
        capabilities = []
        for definition in CAPABILITY_DEFINITIONS:
            saved = policies.get(definition["id"], {})
            capabilities.append(
                {
                    **definition,
                    "mode": saved.get("mode") or definition["default_mode"],
                    "updated_at": saved.get("updated_at") or "",
                }
            )
        counts = {
            mode: sum(1 for row in capabilities if row["mode"] == mode) for mode in ALLOWED_MODES
        }
        return {
            "schema": POLICY_SCHEMA,
            "capabilities": capabilities,
            "counts": counts,
            "audit": list(reversed(document["audit"][-80:])),
            "presets": ["strict", "balanced", "collaborative"],
            "action_grants": [{"id": key, **row} for key, row in document["action_grants"].items()],
        }

    def action_grants(self, app: str, action: str) -> dict[str, dict]:
        with interprocess_file_lock(self.lock_path):
            rows = self._read_unlocked()["action_grants"]
        return {key: row for key, row in rows.items() if row["app"] == app and row["action"] == action}

    def remember_action(self, key: str, *, app: str, action: str, capability: str,
                        label: str, expected: tuple[str, str]) -> bool:
        """Remember a reviewed operation/scope, never change the global policy."""
        if not re.fullmatch(r"[0-9a-f]{64}", key) or capability not in _CAPABILITIES_BY_ID:
            raise ValueError("Permiso recordado no válido.")
        with interprocess_file_lock(self.lock_path):
            document = self._read_unlocked()
            saved = document["policies"].get(capability, {})
            current = (saved.get("mode") or DEFAULT_POLICIES[capability], saved.get("updated_at") or "")
            if current != expected or current[0] != "ask":
                return False
            rows = document["action_grants"]
            rows[key] = {"app": app, "action": action, "capability": capability,
                         "label": _clean_text(label, 160), "updated_at": _now_iso()}
            while len(rows) > 160:
                del rows[next(iter(rows))]
            self._append_audit(document, kind="action_remembered", capability=capability,
                               verdict="allow", detail=f"{app}.{action}")
            self._write_unlocked(document)
        return True

    def forget_action(self, key: str) -> dict:
        with interprocess_file_lock(self.lock_path):
            document = self._read_unlocked()
            document["action_grants"].pop(key, None)
            self._write_unlocked(document)
        return self.snapshot()

    def set_mode(self, capability_id: str, mode: str) -> dict[str, Any]:
        capability_id = str(capability_id or "").strip()
        mode = str(mode or "").strip().lower()
        if capability_id not in _CAPABILITIES_BY_ID:
            raise ValueError("Capacidad desconocida.")
        if mode not in ALLOWED_MODES:
            raise ValueError("Modo de permiso no válido.")
        with interprocess_file_lock(self.lock_path):
            document = self._read_unlocked()
            previous = (
                document["policies"].get(capability_id, {}).get("mode")
                or DEFAULT_POLICIES[capability_id]
            )
            document["policies"][capability_id] = {"mode": mode, "updated_at": _now_iso()}
            document["action_grants"] = {key: row for key, row in document["action_grants"].items()
                                         if row["capability"] != capability_id}
            self._append_audit(
                document,
                kind="policy_changed",
                capability=capability_id,
                verdict=mode,
                detail=f"{previous} -> {mode}",
            )
            self._write_unlocked(document)
        return self.snapshot()

    def set_section(self, category: str, mode: str) -> dict[str, Any]:
        ids = [row["id"] for row in CAPABILITY_DEFINITIONS if row["category"] == category]
        if not ids or mode not in ALLOWED_MODES:
            raise ValueError("Sección o modo de permiso no válido.")
        now = _now_iso()
        with interprocess_file_lock(self.lock_path):
            document = self._read_unlocked()
            for capability in ids:
                document["policies"][capability] = {"mode": mode, "updated_at": now}
            document["action_grants"] = {key: row for key, row in document["action_grants"].items()
                                         if row["capability"] not in ids}
            self._append_audit(document, kind="section_changed", verdict=mode,
                               detail=f"{category}: {', '.join(ids)}")
            self._write_unlocked(document)
        return self.snapshot()

    def set_mode_if_unchanged(
        self,
        capability_id: str,
        mode: str,
        *,
        expected: tuple[str, str],
        on_commit: Callable[[], None] | None = None,
    ) -> bool:
        """Compare-and-set: write ``mode`` only while the capability is still at
        ``expected`` (its ``(mode, updated_at)``), reading and writing under one
        interprocess lock. ``on_commit`` runs inside that lock after the write.

        An answer given under an older policy never overwrites a newer choice:
        it returns False and changes nothing.
        """
        capability_id = str(capability_id or "").strip()
        mode = str(mode or "").strip().lower()
        if capability_id not in _CAPABILITIES_BY_ID:
            raise ValueError("Capacidad desconocida.")
        if mode not in ALLOWED_MODES:
            raise ValueError("Modo de permiso no válido.")
        with interprocess_file_lock(self.lock_path):
            document = self._read_unlocked()
            saved = document["policies"].get(capability_id, {})
            current = (saved.get("mode") or DEFAULT_POLICIES[capability_id], saved.get("updated_at") or "")
            if current != tuple(expected):
                return False
            document["policies"][capability_id] = {"mode": mode, "updated_at": _now_iso()}
            self._append_audit(
                document,
                kind="policy_changed",
                capability=capability_id,
                verdict=mode,
                detail=f"{current[0]} -> {mode}",
            )
            self._write_unlocked(document)
            if on_commit is not None:
                on_commit()
        return True

    def apply_preset(self, preset: str) -> dict[str, Any]:
        preset = str(preset or "").strip().lower()
        if preset not in PRESETS:
            raise ValueError("Preset de permisos desconocido.")
        now = _now_iso()
        with interprocess_file_lock(self.lock_path):
            document = self._read_unlocked()
            document["policies"] = {
                capability_id: {"mode": mode, "updated_at": now}
                for capability_id, mode in PRESETS[preset].items()
            }
            document["action_grants"] = {}
            self._append_audit(
                document,
                kind="preset_applied",
                verdict=preset,
                detail="Política completa reemplazada por un preset reversible.",
            )
            self._write_unlocked(document)
        return self.snapshot()

    def record_event(
        self,
        kind: str,
        *,
        capability: str = "",
        verdict: str = "",
        detail: str = "",
    ) -> None:
        with interprocess_file_lock(self.lock_path):
            document = self._read_unlocked()
            self._append_audit(
                document,
                kind=kind,
                capability=capability,
                verdict=verdict,
                detail=detail,
            )
            self._write_unlocked(document)

    def clear_audit(self) -> dict[str, Any]:
        with interprocess_file_lock(self.lock_path):
            document = self._read_unlocked()
            document["audit"] = []
            self._write_unlocked(document)
        return self.snapshot()

    def evaluate(
        self,
        capability_id: str,
        *,
        trigger: str = "manual",
        confirmed: bool = False,
    ) -> dict[str, Any]:
        capability_id = str(capability_id or "").strip()
        trigger = str(trigger or "manual").strip().lower()
        if capability_id not in _CAPABILITIES_BY_ID:
            raise ValueError("Capacidad desconocida.")
        if trigger not in ALLOWED_TRIGGERS:
            raise ValueError("Origen de acción desconocido.")
        row = next(item for item in self.snapshot()["capabilities"] if item["id"] == capability_id)
        mode = row["mode"]
        if mode == "deny":
            verdict = "deny"
        elif mode == "ask" and not confirmed:
            verdict = "ask"
        else:
            verdict = "allow"
        return {
            "capability": capability_id,
            "mode": mode,
            "updated_at": row.get("updated_at", ""),
            "trigger": trigger,
            "verdict": verdict,
            "requires_confirmation": verdict == "ask",
            "sensor_activation": False,
            "bypasses_hermes_approvals": False,
        }


def parse_hermes_tools(output: str) -> list[dict[str, Any]]:
    """Parse the stable human CLI list without reading Hermes config internals."""
    source = "builtin"
    rows: list[dict[str, Any]] = []
    for raw in str(output or "").splitlines():
        line = raw.strip()
        lower = line.lower()
        if lower.startswith("built-in toolsets"):
            source = "builtin"
            continue
        if lower.startswith("plugin toolsets"):
            source = "plugin"
            continue
        if lower.startswith("mcp tools"):
            source = "mcp"
            continue
        match = _TOOL_ROW.match(raw)
        if not match:
            continue
        description = match.group("description").strip()
        description = re.sub(r"^[^A-Za-z0-9ÁÉÍÓÚÜÑáéíóúüñ]+", "", description).strip()
        rows.append(
            {
                "name": match.group("name"),
                "enabled": match.group("state").lower() == "enabled",
                "source": source,
                "platform": "cli",
                "description": _clean_text(description, 180),
            }
        )
    return rows


def parse_egress_status(output: str) -> dict[str, Any]:
    values: dict[str, str] = {}
    for raw in str(output or "").splitlines():
        match = re.match(r"^\s*([A-Za-z][A-Za-z ]+?)\s{2,}(.+?)\s*$", raw)
        if not match:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", match.group(1).strip().lower()).strip("_")
        values[key] = _clean_text(match.group(2), 120)
    enabled = values.get("enabled", "").lower() in {"yes", "true", "enabled", "on"}
    listening = values.get("listening", "").lower() in {"yes", "true", "enabled", "on"}
    return {
        "enabled": enabled,
        "listening": listening,
        "process": values.get("process", "unknown"),
        "binary": values.get("binary", "unknown"),
        "docker_enforce": values.get("docker_enforce", "unknown"),
    }


def _public_mcp_snapshot() -> dict[str, Any]:
    raw = list_mcp_catalog()
    entries = []
    for item in raw.get("catalog") or []:
        if not isinstance(item, dict) or not item.get("installed"):
            continue
        entries.append(
            {
                "name": _clean_text(item.get("name"), 96),
                "enabled": bool(item.get("enabled")),
                "installed": True,
                "transport": _clean_text(item.get("transport"), 32),
                "auth": _clean_text(item.get("auth"), 32),
                "enabled_tools": len(item.get("enabled_tools") or []),
            }
        )
    return {
        "available": bool(raw.get("success")),
        "installed": entries,
        "installed_count": len(entries),
        "enabled_count": sum(1 for item in entries if item["enabled"]),
        "source": _clean_text(raw.get("source_mode"), 48),
    }


def _hermes_version_result() -> dict[str, Any]:
    """The included engine is pinned: its version needs no Hermes process (qa9)."""
    runtime = get_runtime()
    if runtime.packaged and runtime.hermes is not None:
        return {"success": True, "output": f"Hermes Agent v{runtime.hermes.version}"}
    return run_hermes_cli(["--version"], timeout=10)


def capability_center_snapshot(
    hermes_capabilities: dict[str, Any] | None = None,
    *,
    store: CapabilityPermissionStore | None = None,
) -> dict[str, Any]:
    store = store or CapabilityPermissionStore()
    with ThreadPoolExecutor(max_workers=6) as executor:
        tools_future = executor.submit(run_hermes_cli, ["tools", "list"], timeout=18)
        egress_future = executor.submit(run_hermes_cli, ["egress", "status"], timeout=12)
        version_future = executor.submit(_hermes_version_result)
        mcp_future = executor.submit(_public_mcp_snapshot)
        approvals_help_future = executor.submit(
            run_hermes_cli,
            ["approvals", "test", "--help"],
            timeout=10,
        )
        security_help_future = executor.submit(
            run_hermes_cli,
            ["security", "audit", "--help"],
            timeout=10,
        )
        tools_result = tools_future.result()
        egress_result = egress_future.result()
        version_result = version_future.result()
        approvals_help_result = approvals_help_future.result()
        security_help_result = security_help_future.result()
        try:
            mcp = mcp_future.result()
        except Exception as exc:
            mcp = {
                "available": False,
                "installed": [],
                "installed_count": 0,
                "enabled_count": 0,
                "source": "unavailable",
                "error": _clean_text(exc, 240),
            }

    tools = parse_hermes_tools(tools_result.get("output", ""))
    tool_inventory_available = bool(tools_result.get("success") and tools)
    tool_state = {item["name"]: item["enabled"] for item in tools}
    policy = store.snapshot()
    for capability in policy["capabilities"]:
        mapped = capability.get("hermes_tools") or []
        mapped_enabled = sum(1 for name in mapped if tool_state.get(name) is True)
        capability["runtime_enabled"] = (
            None if not mapped or not tool_inventory_available else mapped_enabled > 0
        )
        capability["tool_gate"] = {
            "mapped": len(mapped),
            "enabled": mapped_enabled,
            "status": (
                "local"
                if not mapped
                else "unknown"
                if not tool_inventory_available
                else "active"
                if mapped_enabled == len(mapped)
                else "partial"
                if mapped_enabled
                else "off"
            ),
        }

    cli_path = resolve_hermes_cli()
    version_output = version_result.get("output")
    version_tuple = parse_hermes_version(str(version_output or ""))
    has_pantheon_guards = bool(version_tuple and version_tuple >= (0, 21, 0))
    approvals_available = bool(
        cli_path and has_pantheon_guards and approvals_help_result.get("success")
    )
    security_audit_available = bool(
        cli_path and has_pantheon_guards and security_help_result.get("success")
    )
    caps = hermes_capabilities if isinstance(hermes_capabilities, dict) else {}
    supports = caps.get("supports") if isinstance(caps.get("supports"), dict) else {}
    return {
        "schema": SNAPSHOT_SCHEMA,
        "generated_at": _now_iso(),
        "policy": policy,
        "hermes": {
            "installed": bool(cli_path),
            "version": _hermes_version_label(version_output),
            "reachable": bool(caps.get("reachable")),
            "capabilities_available": bool(caps.get("capabilities_available")),
            "supports": {key: bool(value) for key, value in supports.items()},
            "tools_available": tool_inventory_available,
            "tools_error": _clean_text(
                tools_result.get("error")
                or (
                    "Hermes no entregó un inventario de tools compatible."
                    if tools_result.get("success") and not tools
                    else ""
                ),
                240,
            ),
        },
        "tools": tools,
        "tool_summary": {
            "total": len(tools),
            "enabled": sum(1 for item in tools if item["enabled"]),
            "disabled": sum(1 for item in tools if not item["enabled"]),
        },
        "mcp": mcp,
        "guards": {
            "approvals": {
                "available": approvals_available,
                "mode": "hermes-native",
                "detail": (
                    "Dry-run oficial; nunca ejecuta el comando."
                    if approvals_available
                    else "Hermes 0.21 fue detectado, pero approvals test no responde."
                    if has_pantheon_guards
                    else "Requiere Hermes 0.21 o superior."
                ),
            },
            "egress": {
                "available": bool(egress_result.get("success")),
                **parse_egress_status(egress_result.get("output", "")),
            },
            "security_audit": {
                "available": security_audit_available,
                "automatic": False,
                "detail": (
                    "Consulta OSV.dev solo cuando el usuario la inicia."
                    if security_audit_available
                    else "Hermes 0.21 fue detectado, pero security audit no responde."
                    if has_pantheon_guards
                    else "Requiere Hermes 0.21 o superior."
                ),
            },
        },
        "invariants": {
            "starts_sensors": False,
            "bypasses_hermes_approvals": False,
            "stores_secrets": False,
            "remote_administration": False,
        },
    }


def set_hermes_tool_enabled(name: str, enabled: bool) -> dict[str, Any]:
    name = str(name or "").strip()
    if not _SAFE_NAME.fullmatch(name):
        raise ValueError("Nombre de tool no válido.")
    inventory_result = run_hermes_cli(["tools", "list", "--platform", "cli"], timeout=18)
    inventory = parse_hermes_tools(inventory_result.get("output", ""))
    current = next((item for item in inventory if item["name"] == name), None)
    if not inventory_result.get("success") or current is None:
        return {
            "success": False,
            "name": name,
            "enabled": bool(enabled),
            "message": "Hermes no reconoce ese toolset en la plataforma CLI.",
        }
    if current["enabled"] is bool(enabled):
        return {
            "success": True,
            "name": name,
            "enabled": bool(enabled),
            "message": (
                f"{name} ya estaba "
                f"{'habilitado' if enabled else 'deshabilitado'} en Hermes CLI."
            ),
        }

    action = "enable" if enabled else "disable"
    result = run_hermes_cli(["tools", action, "--platform", "cli", name], timeout=25)
    verification_result = run_hermes_cli(["tools", "list", "--platform", "cli"], timeout=18)
    verified = next(
        (
            item
            for item in parse_hermes_tools(verification_result.get("output", ""))
            if item["name"] == name
        ),
        None,
    )
    success = bool(
        result.get("success")
        and verification_result.get("success")
        and verified is not None
        and verified["enabled"] is bool(enabled)
    )
    evidence = _clean_text(result.get("error") or result.get("output"), 500)
    if success:
        message = (
            evidence
            or f"{name} quedó {'habilitado' if enabled else 'deshabilitado'} en Hermes CLI."
        )
    else:
        message = "Hermes no confirmó el estado final del toolset."
        if evidence:
            message += f" Respuesta CLI: {evidence}"
    return {
        "success": success,
        "name": name,
        "enabled": bool(enabled),
        "message": message,
    }


def approval_dry_run(command: str) -> dict[str, Any]:
    command = str(command or "").strip()
    if not command or len(command) > 320 or "\n" in command or "\r" in command:
        raise ValueError("Escribe un comando de una sola línea (máximo 320 caracteres).")
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        raise ValueError("El comando tiene comillas incompletas.") from exc
    if not parts:
        raise ValueError("Escribe un comando para evaluar.")
    result = run_hermes_cli(["approvals", "test", "--json", "--", *parts], timeout=20)
    if result.get("returncode") not in {0, 2, 3}:
        return {
            "success": False,
            "verdict": "error",
            "detail": _clean_text(result.get("error") or "Hermes no pudo evaluar el comando.", 500),
            "rule": "",
            "normalized_variants": [],
        }
    try:
        raw = json.loads(result.get("stdout") or result.get("output") or "{}")
    except json.JSONDecodeError:
        return {
            "success": False,
            "verdict": "error",
            "native_verdict": "",
            "detail": "Hermes devolvió un diagnóstico no válido.",
            "rule": "",
            "normalized_variants": [],
        }
    if not isinstance(raw, dict):
        raw = {}
    native_verdict = _clean_text(raw.get("verdict"), 32)
    verdict = {0: "allow", 2: "ask", 3: "deny"}.get(result.get("returncode"), "error")
    variants = (
        raw.get("normalized_variants") if isinstance(raw.get("normalized_variants"), list) else []
    )
    return {
        "success": verdict != "error",
        "verdict": verdict,
        "native_verdict": native_verdict,
        "detail": _clean_text(raw.get("detail"), 500),
        "rule": _clean_text(raw.get("rule"), 160),
        "normalized_variants": [_clean_text(item, 260) for item in variants[:6]],
    }


def test_mcp_server(name: str) -> dict[str, Any]:
    name = str(name or "").strip()
    if not _SAFE_NAME.fullmatch(name):
        raise ValueError("Nombre de servidor MCP no válido.")
    result = run_hermes_cli(["mcp", "test", name], timeout=35)
    message = _clean_text(result.get("output") or result.get("error"), 800)
    lower = message.casefold()
    # Hermes 0.21's mcp test handler currently returns process exit 0 even
    # for unknown servers and connection failures. Require its two positive
    # completion markers instead of trusting only the return code.
    connected = "connected (" in lower and "tools discovered:" in lower
    return {
        "success": bool(result.get("success") and connected),
        "name": name,
        "message": message or "Hermes no confirmó la conexión con el servidor MCP.",
    }


def run_security_audit() -> dict[str, Any]:
    result = run_hermes_cli(["security", "audit", "--json"], timeout=120)
    output = result.get("stdout") or result.get("output") or result.get("error") or ""
    try:
        raw = json.loads(output)
    except json.JSONDecodeError:
        raw = {}
    findings = (
        raw.get("findings")
        if isinstance(raw, dict) and isinstance(raw.get("findings"), list)
        else []
    )
    returncode = result.get("returncode")
    completed = bool(
        returncode in {0, 1}
        and isinstance(raw, dict)
        and isinstance(raw.get("total_components_scanned"), int)
        and isinstance(raw.get("finding_count"), int)
    )
    severity_counts = {"low": 0, "moderate": 0, "high": 0, "critical": 0}
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        severity = str(finding.get("severity") or "").strip().lower()
        if severity in severity_counts:
            severity_counts[severity] += 1
    total_components = int(raw.get("total_components_scanned") or 0) if completed else 0
    finding_count = int(raw.get("finding_count") or 0) if completed else 0
    if completed and finding_count:
        breakdown = ", ".join(
            f"{name}: {count}" for name, count in reversed(severity_counts.items()) if count
        )
        detail = (
            f"{total_components} componentes revisados · {finding_count} hallazgos"
            f"{f' ({breakdown})' if breakdown else ''}."
        )
        verdict = "warning"
    elif completed:
        detail = f"{total_components} componentes revisados · sin vulnerabilidades conocidas."
        verdict = "clean"
    else:
        detail = _clean_text(output, 1000) or "La auditoría no pudo completarse."
        verdict = "error"
    return {
        # Hermes returns 1 when a completed audit finds a critical issue. That
        # is a diagnostic result, not a transport failure.
        "success": completed,
        "returncode": returncode,
        "verdict": verdict,
        "detail": detail,
        "summary": {
            "scanned": total_components,
            "total": finding_count,
            **severity_counts,
        },
        "message": detail,
    }


__all__ = [
    "ALLOWED_MODES",
    "CAPABILITY_DEFINITIONS",
    "CapabilityPermissionStore",
    "PRESETS",
    "approval_dry_run",
    "capability_center_snapshot",
    "parse_egress_status",
    "parse_hermes_tools",
    "run_security_audit",
    "set_hermes_tool_enabled",
    "test_mcp_server",
]
