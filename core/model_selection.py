"""F01: provider/model per conversation and the default model.

Hermes 0.21 confirms the model that answered only on its session API, and only
when the turn carries a confirmed model lock (``require_model_lock``). Every
Iris turn therefore names its model:

* a conversation with its own selection sends ``provider`` + ``model``;
* a conversation that follows the default sends the profile's current
  ``model.default`` with no provider. It equals the global model, so Hermes
  keeps its global runtime and still confirms it.

Hermes never returns a session's lock through the API, so the per-conversation
choice lives here, keyed by Hermes profile and session id. The default model is
Hermes' own ``config.yaml``; changing it runs Hermes' ``/model --global`` writer
with Hermes' interpreter. See docs/audits/iris_f01_model_selector_report_2026-10-07.md.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("IRIS.model_selection")

_PROVIDER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,79}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_MODEL_LEN = 200
_MAX_ENTRIES = 400
_MAX_PROVIDERS = 120
_MAX_MODELS_PER_PROVIDER = 400
_STORE_NAME = "conversation_models.json"
# Virtual routing modes in Hermes' picker that are not a provider a turn can lock.
_VIRTUAL_PROVIDERS = frozenset({"moa"})


class SelectionError(ValueError):
    """A selection the inventory does not allow; ``code`` is stable for the UI."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def clean_provider(value: Any) -> str:
    text = str(value or "").strip()
    return text if _PROVIDER_RE.fullmatch(text) else ""


def clean_model(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > _MAX_MODEL_LEN or _CONTROL_RE.search(text):
        return ""
    return text


def _clean_text(value: Any, limit: int = 300) -> str:
    return _CONTROL_RE.sub(" ", str(value or "")).strip()[:limit]


def parse_runtime(raw: Any) -> dict[str, Any] | None:
    """Hermes' sanitized runtime metadata for a turn, or None when absent."""
    if not isinstance(raw, dict):
        return None
    requested = raw.get("requested") if isinstance(raw.get("requested"), dict) else {}
    return {
        "provider": clean_provider(raw.get("provider")),
        "model": clean_model(raw.get("model")),
        "route_source": _clean_text(raw.get("route_source"), 64),
        "model_lock": _clean_text(raw.get("model_lock"), 32),
        "requested": {
            "provider": clean_provider(requested.get("provider")),
            "model": clean_model(requested.get("model")),
        },
    }


def _profile_key(profile: str | None, session_id: str) -> str:
    return f"{(profile or 'default').strip().lower()}|{session_id}"


# ── Default model (Hermes config.yaml) ────────────────────────────────────


def _profile_hermes_home(profile: str | None) -> Path:
    from core.workspace_sync import get_hermes_home

    home = get_hermes_home()
    return home / "profiles" / profile if profile else home


def read_default_model(profile: str | None = None) -> dict[str, str]:
    """``model.provider`` / ``model.default`` exactly as the Gateway reads them.

    Hermes reads the user YAML raw (``gateway.run._resolve_gateway_model``):
    ``model.default``, else ``model.model``, else a bare string ``model:``.
    """
    path = _profile_hermes_home(profile) / "config.yaml"
    try:
        import yaml

        document = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except Exception as exc:
        logger.warning("Could not read Hermes default model from %s: %s", path, exc)
        return {"provider": "", "model": ""}
    model_cfg = document.get("model") if isinstance(document, dict) else None
    if isinstance(model_cfg, str):
        return {"provider": "", "model": clean_model(model_cfg)}
    if isinstance(model_cfg, dict):
        return {
            "provider": clean_provider(model_cfg.get("provider")),
            "model": clean_model(model_cfg.get("default") or model_cfg.get("model")),
        }
    return {"provider": "", "model": ""}


# ── Per-conversation store ────────────────────────────────────────────────


class ConversationModelStore:
    """Iris' per-conversation selection and last outcome, in the Iris profile.

    One JSON document, rewritten atomically. A missing selection means the
    conversation follows the default model. The store is bounded: the oldest
    conversations are forgotten first, which only returns them to the default.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] | None = None

    @property
    def path(self) -> Path:
        if self._path is None:
            from core.config import get_iris_home

            self._path = get_iris_home() / _STORE_NAME
        return self._path

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._entries is not None:
            return self._entries
        entries: dict[str, dict[str, Any]] = {}
        path = self.path
        if path.is_file():
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                raw = document.get("conversations") if isinstance(document, dict) else None
                if not isinstance(raw, dict):
                    raise ValueError("'conversations' is not an object")
                for key, value in raw.items():
                    if isinstance(key, str) and isinstance(value, dict):
                        entries[key] = value
            except (OSError, ValueError) as exc:
                # Keep the unreadable file for diagnosis; the conversations
                # fall back to the default model instead of failing turns.
                logger.warning("Ignoring unreadable %s: %s", path, exc)
                try:
                    os.replace(path, path.with_name(path.name + ".corrupt"))
                except OSError:
                    pass
        self._entries = entries
        return entries

    def _save(self) -> None:
        entries = self._entries or {}
        if len(entries) > _MAX_ENTRIES:
            ordered = sorted(entries.items(), key=lambda item: float(item[1].get("updated_at") or 0))
            for key, _ in ordered[: len(entries) - _MAX_ENTRIES]:
                entries.pop(key, None)
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"schema": 1, "conversations": entries}, ensure_ascii=False, indent=1)
        fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def entry(self, profile: str | None, session_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._load().get(_profile_key(profile, session_id)) or {})

    def selection(self, profile: str | None, session_id: str) -> dict[str, str] | None:
        selection = self.entry(profile, session_id).get("selection")
        if not isinstance(selection, dict):
            return None
        provider, model = clean_provider(selection.get("provider")), clean_model(selection.get("model"))
        return {"provider": provider, "model": model} if provider and model else None

    def set_selection(self, profile: str | None, session_id: str, provider: str, model: str) -> None:
        with self._lock:
            entries = self._load()
            entry = entries.setdefault(_profile_key(profile, session_id), {})
            entry["selection"] = {"provider": provider, "model": model}
            entry["updated_at"] = time.time()
            self._save()

    def clear_selection(self, profile: str | None, session_id: str) -> None:
        with self._lock:
            entries = self._load()
            key = _profile_key(profile, session_id)
            entry = entries.get(key)
            if entry is None:
                return
            entry.pop("selection", None)
            entry["updated_at"] = time.time()
            self._save()

    def record_outcome(self, profile: str | None, session_id: str, outcome: dict[str, Any]) -> None:
        with self._lock:
            entries = self._load()
            entry = entries.setdefault(_profile_key(profile, session_id), {})
            entry["last"] = outcome
            entry["updated_at"] = time.time()
            self._save()

    def copy(self, profile: str | None, source_id: str, target_id: str) -> None:
        """Carry a conversation's selection to the id Hermes rotated it to."""
        with self._lock:
            entries = self._load()
            source = entries.get(_profile_key(profile, source_id))
            if not source or source_id == target_id:
                return
            target = entries.setdefault(_profile_key(profile, target_id), {})
            if "selection" in source:
                target["selection"] = dict(source["selection"])
            target["updated_at"] = time.time()
            self._save()


# ── Models the selector lists ─────────────────────────────────────────────

_VISIBLE_NAME = "model_selector_visible.json"
_MAX_VISIBLE = 600


def clean_visible_models(raw: Any) -> list[dict[str, str]] | None:
    """``None`` (list every model) or the chosen ``{provider, model}`` pairs.

    An authenticated provider can publish hundreds of models (qa9: one OpenAI
    login already filled the selector). The user picks the ones to list; this
    only filters what the selector shows, it never adds a model Hermes did not
    publish, and a conversation keeps the model it uses even if it is hidden.
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise SelectionError("invalid_visible_models", "La lista de modelos visibles no es válida.")
    chosen: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        provider = clean_provider(item.get("provider")) if isinstance(item, dict) else ""
        model = clean_model(item.get("model")) if isinstance(item, dict) else ""
        if not provider or not model:
            raise SelectionError("invalid_visible_models", "Cada modelo visible necesita proveedor y modelo.")
        if (provider, model) not in seen:
            seen.add((provider, model))
            chosen.append({"provider": provider, "model": model})
    if len(chosen) > _MAX_VISIBLE:
        raise SelectionError("too_many_visible_models", f"Elige como máximo {_MAX_VISIBLE} modelos.")
    return chosen


class SelectorVisibilityStore:
    """The selector's chosen models, in the Iris profile (one atomic JSON document)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        if self._path is None:
            from core.config import get_iris_home

            self._path = get_iris_home() / _VISIBLE_NAME
        return self._path

    def load(self) -> list[dict[str, str]] | None:
        with self._lock:
            path = self.path
            if not path.is_file():
                return None
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                return clean_visible_models(document.get("models") if isinstance(document, dict) else [])
            except (OSError, ValueError) as exc:
                # An unreadable choice lists every model rather than none.
                logger.warning("Ignoring unreadable %s: %s", path, exc)
                return None

    def save(self, raw: Any) -> list[dict[str, str]] | None:
        models = clean_visible_models(raw)
        with self._lock:
            path = self.path
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"schema": 1, "models": models}, ensure_ascii=False, indent=1)
            fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        return models


# ── Inventory ─────────────────────────────────────────────────────────────


def normalize_inventory(payload: Any) -> dict[str, Any]:
    """Selector rows from Hermes ``/api/model/options``.

    Keeps the providers Hermes reports with credentials and models, plus the
    current provider even without credentials (so the UI can say why it fails).
    ``moa`` is a routing mode, not a lockable provider, unless it is current.
    """
    rows = payload.get("providers") if isinstance(payload, dict) else None
    providers: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        slug = clean_provider(row.get("slug"))
        current = row.get("is_current") is True
        if not slug or (slug.lower() in _VIRTUAL_PROVIDERS and not current):
            continue
        models: list[str] = []
        for item in row.get("models") or []:
            model = clean_model(item.get("id") if isinstance(item, dict) else item)
            if model and model not in models:
                models.append(model)
            if len(models) >= _MAX_MODELS_PER_PROVIDER:
                break
        authenticated = row.get("authenticated") is True
        if not current and not (authenticated and models):
            continue
        providers.append({
            "id": slug,
            "label": _clean_text(row.get("name") or slug, 80) or slug,
            "current": current,
            "authenticated": authenticated,
            "models": models,
            "warning": _clean_text(row.get("warning"), 300),
            "free_tier_pending": row.get("free_tier_pending") is True,
            "pricing_pending": row.get("pricing_pending") is True,
            "unavailable_models": [model for model in models if model in (row.get("unavailable_models") or [])],
        })
        if len(providers) >= _MAX_PROVIDERS:
            break
    return {
        "providers": providers,
        "default": {
            "provider": clean_provider(payload.get("provider")) if isinstance(payload, dict) else "",
            "model": clean_model(payload.get("model")) if isinstance(payload, dict) else "",
        },
    }


def validate_selection(inventory: dict[str, Any], provider: str, model: str) -> None:
    """Raise SelectionError unless ``provider``/``model`` is a selectable inventory row."""
    provider, model = clean_provider(provider), clean_model(model)
    if not provider or not model:
        raise SelectionError("invalid_selection", "Elige un proveedor y un modelo válidos.")
    row = next((item for item in inventory.get("providers") or [] if item.get("id") == provider), None)
    if row is None:
        raise SelectionError("unknown_provider", "Ese proveedor no está configurado en Hermes.")
    if not row.get("authenticated"):
        raise SelectionError(
            "provider_without_credentials",
            "Ese proveedor no tiene sesión ni clave en Hermes. Configúralo en el asistente.",
        )
    if model not in (row.get("models") or []):
        raise SelectionError("unknown_model", "Ese modelo no aparece en el catálogo del proveedor.")
    if row.get("free_tier_pending") or row.get("pricing_pending"):
        raise SelectionError("plan_pending", "Falta comprobar los modelos disponibles para tu cuenta. Actualiza el catálogo.")
    if model in (row.get("unavailable_models") or []):
        raise SelectionError("model_not_in_plan", "Ese modelo no está incluido en el plan de tu cuenta.")


# ── Default model writer (Hermes' own /model --global path) ───────────────

_DEFAULT_WRITER = r'''
import json, sys
from hermes_cli.config import load_config
from hermes_cli.model_switch import persist_model_selection, switch_model
provider, model = sys.argv[1], sys.argv[2]
cfg = load_config()
current = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
result = switch_model(
    model, str(current.get("provider") or ""), str(current.get("default") or current.get("model") or ""),
    current_base_url=str(current.get("base_url") or ""), is_global=True, explicit_provider=provider,
    user_providers=cfg.get("providers") if isinstance(cfg.get("providers"), dict) else None,
    custom_providers=cfg.get("custom_providers") if isinstance(cfg.get("custom_providers"), list) else None)
if result.success:
    persist_model_selection(result)
print("IRIS_DEFAULT_MODEL=" + json.dumps({
    "ok": bool(result.success), "provider": result.target_provider, "model": result.new_model,
    "error": result.error_message, "warning": result.warning_message}, ensure_ascii=False))
'''


def change_default_model(provider: str, model: str, profile: str | None = None, *, timeout: float = 60.0) -> dict[str, Any]:
    """Make ``provider``/``model`` Hermes' default with Hermes' own writer.

    Runs ``switch_model`` + ``persist_model_selection`` (the CLI's
    ``/model --global``) under Hermes' interpreter and HERMES_HOME, so the
    config shape (base_url, api_mode, stale keys) is Hermes' and never Iris'.
    """
    from wizard.provider_sync import _hermes_probe_kwargs, _hermes_python_probe_command, _resolve_hermes_python

    provider, model = clean_provider(provider), clean_model(model)
    if not provider or not model:
        return {"ok": False, "error": "Elige un proveedor y un modelo válidos."}
    python = _resolve_hermes_python()
    if python is None:
        return {"ok": False, "error": "No encuentro el intérprete de Hermes."}
    kwargs = _hermes_probe_kwargs(timeout)
    if profile:
        kwargs["env"] = {**kwargs["env"], "HERMES_HOME": str(_profile_hermes_home(profile))}
    try:
        completed = subprocess.run(
            [*_hermes_python_probe_command(python, _DEFAULT_WRITER), provider, model], **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Hermes default-model writer failed: %s", exc)
        return {"ok": False, "error": "Hermes no respondió al cambiar el modelo predeterminado."}
    marker = "IRIS_DEFAULT_MODEL="
    line = next((item[len(marker):] for item in reversed(completed.stdout.splitlines())
                 if item.startswith(marker)), "")
    try:
        result = json.loads(line)
    except ValueError:
        logger.warning("Hermes default-model writer exited %s: %s", completed.returncode, completed.stderr[-500:])
        return {"ok": False, "error": "Hermes no pudo cambiar el modelo predeterminado."}
    return {
        "ok": result.get("ok") is True,
        "provider": clean_provider(result.get("provider")),
        "model": clean_model(result.get("model")),
        "error": _clean_text(result.get("error"), 400),
        "warning": _clean_text(result.get("warning"), 400),
    }


# ── Turn contract ─────────────────────────────────────────────────────────


class ModelSelectionService:
    """Builds each turn's selection and records what Hermes confirmed."""

    def __init__(self, store: ConversationModelStore | None = None, default_reader=read_default_model) -> None:
        self.store = store or ConversationModelStore()
        self._read_default = default_reader

    def read_default(self, profile: str | None) -> dict[str, str]:
        """The local Hermes config's default for ``profile`` (a local Gateway)."""
        return self._read_default(profile)

    def turn_plan(
        self, profile: str | None, session_id: str, *, default: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """The body fields for the next turn plus what Hermes must confirm.

        ``default`` is the default of the Gateway serving the conversation;
        without it this reads the local profile's config (a local Gateway).
        """
        selection = self.store.selection(profile, session_id)
        if selection:
            return {
                "mode": "custom", **selection,
                "fields": {**selection, "require_model_lock": True},
            }
        if default is None:
            default = self._read_default(profile)
        default = {"provider": clean_provider(default.get("provider")), "model": clean_model(default.get("model"))}
        fields = {"model": default["model"], "require_model_lock": True} if default["model"] else {}
        return {"mode": "default", **default, "fields": fields}

    @staticmethod
    def outcome(plan: dict[str, Any], runtime: dict[str, Any] | None, *, status: str, error: str = "") -> dict[str, Any]:
        """Public summary of a finished turn (stored and broadcast to the panel).

        ``confirmed`` means "this model answered": the turn completed and Hermes
        ran it under a confirmed lock on the planned model (Hermes' own
        actual-vs-requested check). A failed turn never confirms its model.
        """
        runtime = runtime or {}
        expected = plan.get("model") or ""
        confirmed = bool(
            status == "completed"
            and plan.get("fields", {}).get("require_model_lock")
            and runtime.get("model_lock") == "confirmed"
            and runtime.get("model")
            and (not expected or runtime.get("model") == expected))
        return {
            "mode": plan.get("mode") or "default",
            "provider": plan.get("provider") or "",
            "model": expected,
            "status": status,
            "confirmed": confirmed,
            "effective": {
                "provider": runtime.get("provider") or "",
                "model": runtime.get("model") or "",
                "route_source": runtime.get("route_source") or "",
            },
            "error": _clean_text(error, 400),
            "at": time.time(),
        }
