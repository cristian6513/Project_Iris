"""
Provider catalog sync — reads Hermes' official model-picker inventory.

Motivation
──────────
`wizard/providers.py` carries presentation metadata and a first-run fallback.
To keep selectable model IDs fresh, IRIS reads the same inventory builder used
by Hermes' `/api/model/options` endpoint once per day when the provider step
opens and whenever the user requests a manual retry. Older Hermes releases
fall back to the partial public manifest at:

    https://hermes-agent.nousresearch.com/docs/api/model-catalog.json

Re-published on every merge to ``main`` of the Hermes repo, so
clicking "Actualizar catálogo" in the wizard reflects the upstream
state without any CLI involvement. Hermes itself caches the same
file at ``~/.hermes/cache/model_catalog.json`` (refreshed every
24 h when the user runs ``hermes model``); we read that first when
present so we don't re-hit the network unnecessarily.

Architecture
────────────
We separate **reading** from **refreshing**:

* **`hermes_catalog()`** never hits the network. It serves
  from four tiers, each falling through to the next on miss:

    1. In-memory dict (60 s TTL, hot path during a session).
    2. Runtime snapshot at `~/.iris/provider_catalog_snapshot.json`
       — created/overwritten by the wizard's daily refresh or the user's
       explicit "Actualizar catálogo" action.
    3. Bundled snapshot at `wizard/provider_catalog_snapshot.json`
       — a committed JSON file shipped with the app, refreshed by
       us each release so first-run installs already have a good
       catalog with a visible date stamp.
    4. Nothing → empty dict → caller falls back to the static
       `wizard/providers.py` list.

* **`fetch_live_catalog()`** is the only function that performs external I/O.
  The `/api/providers/refresh` SSE endpoint calls it for both the daily
  background refresh and the manual retry button. It first invokes Hermes'
  official inventory with Hermes' own interpreter; old installations fall
  back to the local/public manifest. On success it writes a runtime snapshot,
  invalidates the memory cache, and returns `(catalog, metadata)`.

Current runtime snapshots use schema 4:

    {
      "schema":         3,
      "snapshot_date":  "2026-04-24",
      "hermes_version": "Hermes Agent 0.12.0",
      "model_count":    835,
      "catalog":        {"<slug>": [...], ...}
    }

`snapshot_info()` tells the UI which file we read from and how
old the data is, so the wizard can render a "Datos actualizados
hasta DD/MM/AAAA · Actualizar" badge next to the provider list.
"""

from __future__ import annotations

import asyncio
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

import httpx
from core import runtime_descriptor as runtime_policy

logger = logging.getLogger("IRIS.wizard.provider_sync")

# ── Public types ───────────────────────────────────────────────────
# The shape we promise to `wizard/providers.py`. Keep it narrow on
# purpose so the merge layer doesn't have to care about Hermes'
# whole schema evolving.
#
#   {
#     "<hermes_slug>": [
#       {"key": "<slug>/<model_id>",  # as Hermes sees it
#        "id":  "<model_id>",         # stripped of the slug prefix
#        "name": "<Display Name>",
#        "input": "text" | "text+image" | "text+image+audio",
#        "context": 128000,
#        "local": bool,
#        "available": bool,           # credentials configured?
#        "missing": bool,             # plugin not installed?
#        "tags": [...]
#       }, …
#     ]
#   }
Catalog = dict[str, list[dict[str, Any]]]


# ── In-process cache ───────────────────────────────────────────────

_CACHE_TTL_SECONDS = 60.0

# {(bin_path, version): (timestamp, catalog)}
_cache: dict[tuple[str, str], tuple[float, Catalog]] = {}
_provider_meta_cache: tuple[float, dict[str, dict[str, Any]]] | None = None


def _normalise_provider_metadata(raw: Any) -> dict[str, dict[str, Any]]:
    """Keep only the non-secret provider capabilities needed by the wizard."""
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for slug, meta in raw.items():
        if not isinstance(slug, str) or not slug.strip() or not isinstance(meta, dict):
            continue
        clean_slug = slug.strip().lower()
        result[clean_slug] = {
            "label": str(meta.get("label") or clean_slug),
            "description": str(meta.get("description") or ""),
            "auth_type": str(meta.get("auth_type") or ""),
            "env_vars": tuple(str(value) for value in (meta.get("env_vars") or []) if value),
            "oauth_capable": bool(meta.get("oauth_capable")),
        }
    return result


# ── Snapshot files ─────────────────────────────────────────────────
#
# The runtime snapshot is what the daily/manual "Actualizar catálogo"
# flow writes to; the bundled snapshot is what we ship
# in the repo as a baseline for first-run installs. Runtime data uses v4;
# read-only bundled v2/v3 files remain accepted only as offline fallbacks.
#
# Schema v4 envelope:
#   {
#     "schema":          3,
#     "snapshot_date":   "YYYY-MM-DD",
#     "hermes_version":  "Hermes Agent X.Y.Z",
#     "catalog_source":  "installed_inventory",
#     "model_count":     835,
#     "catalog":         {"<slug>": [entry, ...], ...},
#     "provider_metadata": {"<slug>": {auth capabilities only}}
#   }

# Schema 4 also persists Hermes' non-secret auth capabilities, so merely
# opening the provider step never needs to spawn a hidden Hermes subprocess.
# Runtime schema-2 files were produced by the old append-only merger and may
# contain models removed upstream, so they must not be presented as current.
_SNAPSHOT_SCHEMA = 4
_LEGACY_BUNDLED_SCHEMAS = {2, 3}
_BUNDLED_SNAPSHOT_PATH = Path(__file__).resolve().parent / "provider_catalog_snapshot.json"


def _runtime_snapshot_path() -> Path:
    """Return `~/.iris/provider_catalog_snapshot.json`, creating the
    parent directory on demand.

    The canonical home helper also honours ``IRIS_HOME`` and migrations.
    """
    from core.config import get_iris_home

    iris_dir = get_iris_home()
    try:
        iris_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Read-only home dir / permission denied. Callers treat
        # every read/write failure as "no snapshot" and fall back
        # further down the chain, so this is survivable.
        pass
    return iris_dir / "provider_catalog_snapshot.json"


def _read_snapshot_file(
    path: Path,
    *,
    accepted_schemas: set[int] | None = None,
) -> tuple[Catalog, dict[str, Any]] | None:
    """Load a snapshot JSON file and return (catalog, metadata).

    Returns None on any of:
      * file missing or unreadable
      * JSON malformed
      * schema mismatch
      * catalog shape invalid (not a dict of lists)

    Metadata dict shape:
      {"snapshot_date": "YYYY-MM-DD",
       "hermes_version": "...",
       "model_count": int,
       "path": "<absolute path>"}

    Errors are silent by design — a corrupt or missing snapshot must
    never crash the wizard; we simply fall through to the next tier.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(envelope, dict):
        return None
    accepted = accepted_schemas or {_SNAPSHOT_SCHEMA}
    if envelope.get("schema") not in accepted:
        return None

    catalog = envelope.get("catalog")
    if not isinstance(catalog, dict) or not catalog:
        return None
    # Shape-check: each value should be a list. Paranoid but cheap.
    for slug, entries in catalog.items():
        if not isinstance(slug, str) or not isinstance(entries, list):
            return None

    metadata = {
        "snapshot_schema": int(envelope.get("schema") or 0),
        "snapshot_date":  str(envelope.get("snapshot_date") or ""),
        "hermes_version": str(envelope.get("hermes_version") or ""),
        # Derive the visible count from the validated payload instead of
        # trusting duplicated envelope metadata that may be stale/malformed.
        "model_count":    sum(len(entries) for entries in catalog.values()),
        "catalog_source": str(envelope.get("catalog_source") or ""),
        "upstream_date":  str(envelope.get("upstream_date") or ""),
        "provider_metadata": _normalise_provider_metadata(
            envelope.get("provider_metadata")
        ),
        "path":           str(path),
    }
    return catalog, metadata  # type: ignore[return-value]


def _write_runtime_snapshot(
    catalog: Catalog,
    hermes_version: str,
    catalog_source: str = "",
    upstream_date: str = "",
    provider_metadata: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist a fresh catalog to `~/.iris/provider_catalog_snapshot.json`
    with today's date. Returns the metadata dict the caller can forward
    to the UI so it re-paints the "updated on" badge without another
    disk read.

    Uses an atomic rename (tmp + os.replace) so the file is never
    observed half-written — matters here because the 200 KB payload
    takes a noticeable fraction of a second to serialize on slow disks.

    Raises OSError on write failure so the refresh endpoint can
    surface a clear error to the user (vs. silently succeeding with
    nothing persisted).
    """
    import datetime as _dt

    path = _runtime_snapshot_path()
    today = _dt.date.today().isoformat()
    # Total model count across all providers — shown in the UI badge.
    model_count = sum(len(v) for v in catalog.values() if isinstance(v, list))

    envelope = {
        "schema":         _SNAPSHOT_SCHEMA,
        "snapshot_date":  today,
        "hermes_version": hermes_version or "",
        "catalog_source": catalog_source or "",
        "upstream_date":  upstream_date or "",
        "model_count":    model_count,
        "catalog":        catalog,
        "provider_metadata": _normalise_provider_metadata(provider_metadata),
    }

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.refresh-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(envelope, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink(missing_ok=True)

    return {
        "snapshot_date":  today,
        "hermes_version": envelope["hermes_version"],
        "catalog_source": envelope["catalog_source"],
        "upstream_date":  envelope["upstream_date"],
        "model_count":    model_count,
        "provider_metadata": envelope["provider_metadata"],
        "path":           str(path),
    }


def load_snapshot() -> tuple[Catalog, dict[str, Any]] | tuple[dict, dict[str, Any]]:
    """Read the best-available snapshot: runtime (user-updated) first,
    then bundled (shipped with the app). Returns (catalog, metadata)
    where metadata includes a `source` field of `"runtime"` /
    `"bundled"` / `"missing"`.

    On `"missing"`, catalog is `{}` — callers should fall back to
    the static `wizard/providers.py` list.
    """
    # Runtime first — the user's explicit refresh takes precedence
    # over whatever we shipped in the repo.
    hit = _read_snapshot_file(_runtime_snapshot_path())
    if hit is not None:
        catalog, meta = hit
        meta["source"] = "runtime"
        return catalog, meta

    # Bundled fallback — ships with the wizard, has a known date
    # stamp from the last release cut. Read-only; never written.
    hit = _read_snapshot_file(
        _BUNDLED_SNAPSHOT_PATH,
        accepted_schemas={_SNAPSHOT_SCHEMA, *_LEGACY_BUNDLED_SCHEMAS},
    )
    if hit is not None:
        catalog, meta = hit
        meta["source"] = "bundled"
        return catalog, meta

    return {}, {
        "source":         "missing",
        "snapshot_schema": 0,
        "snapshot_date":  "",
        "hermes_version": "",
        "catalog_source": "",
        "upstream_date":  "",
        "model_count":    0,
        "provider_metadata": {},
        "path":           "",
    }


def snapshot_info() -> dict[str, Any]:
    """Return just the metadata of the currently-active snapshot
    without loading the full catalog. Cheap — meant for endpoints
    that only need to show the "updated on" date to the UI.
    """
    _, meta = load_snapshot()
    return meta


def invalidate_cache() -> None:
    """Drop the in-memory cache. Called after `hermes install`
    runs so a follow-up catalog read re-consults the snapshot
    files (e.g. for a freshly-bundled version number).

    Does NOT touch the runtime snapshot file — that's owned by
    the user's explicit refresh action.
    """
    global _provider_meta_cache
    _cache.clear()
    _provider_meta_cache = None


# ── Live catalog fetch (Hermes' published manifest) ───────────────
#
# Hermes publishes a model manifest at a stable URL that updates on
# every merge to its ``main`` branch. Our refresh path reads it (or
# Hermes' own cache copy) and converts the schema to ours.
#
# Manifest schema (from
# https://hermes-agent.nousresearch.com/docs/reference/model-catalog):
#
#     {
#       "version": 1,
#       "updated_at": "2026-...",
#       "metadata": {...},
#       "providers": {
#         "<slug>": {
#           "metadata": {...},
#           "models": [{"id": "<model_id>", "description": "...", "metadata": {...}}, ...]
#         },
#         ...
#       }
#     }
#
# Notable absences in Hermes' manifest: pricing and context window.
# Hermes resolves those at chat time from each provider's live API,
# so our snapshot stores ``context: 0`` for entries we discover
# remotely. The static `wizard/providers.py` registry still carries
# context for the curated headline models.

_REMOTE_CATALOG_URL = (
    "https://hermes-agent.nousresearch.com/docs/api/model-catalog.json"
)

# Lifetime of an HTTP fetch for the public manifest. Generous because
# the file is small (< 100 KB) but the first DNS round-trip on a cold
# network can be slow. Exposed as a constant so tests can monkeypatch.
_REMOTE_FETCH_TIMEOUT_S = 30.0

# How old Hermes' own cache file is allowed to be before we force a remote
# fetch. Match Hermes' 24 h cadence so IRIS' daily refresh cannot stamp a
# week-old local cache as freshly updated.
_HERMES_CACHE_MAX_AGE_S = 24 * 60 * 60


def _hermes_cache_path() -> Path:
    """Return the path to Hermes' own ``model_catalog.json`` cache.

    Hermes writes this file at ``~/.hermes/cache/model_catalog.json``
    on POSIX and ``%LOCALAPPDATA%\\hermes\\cache\\model_catalog.json``
    on Windows. We resolve the workspace root via the same helper
    ``core.workspace_sync`` uses so we always agree with where Hermes
    actually wrote.
    """
    # Inline import keeps this module a leaf — `core.workspace_sync`
    # is part of the runtime layer, not the wizard.
    from core.workspace_sync import get_hermes_home
    return get_hermes_home() / "cache" / "model_catalog.json"


def _read_hermes_cache_or_none() -> dict[str, Any] | None:
    """Read Hermes' local cache file. Returns the parsed JSON dict
    when present, fresh and well-formed; ``None`` otherwise.

    "Fresh" means modified within the last 24 hours — beyond that we
    prefer a remote fetch even though Hermes itself would still
    serve the cache. We're stricter because the wizard's "Actualizar
    catálogo" button is the user explicitly asking for fresh data.
    """
    try:
        path = _hermes_cache_path()
        if not path.exists():
            return None
        if (time.time() - path.stat().st_mtime) > _HERMES_CACHE_MAX_AGE_S:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("providers"), dict):
        return None
    return data


async def _fetch_remote_catalog(timeout_s: float | None = None) -> dict[str, Any]:
    """Fetch Hermes' published manifest over HTTPS.

    Raises:
        TimeoutError    — request didn't finish within ``_REMOTE_FETCH_TIMEOUT_S``.
        RuntimeError    — non-2xx response or malformed JSON.
    """
    effective_timeout = max(
        0.1,
        min(_REMOTE_FETCH_TIMEOUT_S, float(timeout_s or _REMOTE_FETCH_TIMEOUT_S)),
    )
    timeout = httpx.Timeout(effective_timeout, connect=min(10.0, effective_timeout))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            res = await client.get(_REMOTE_CATALOG_URL)
            res.raise_for_status()
            data = res.json()
    except httpx.TimeoutException as exc:
        raise TimeoutError(
            f"El manifest remoto no respondió en {effective_timeout:.0f} s."
        ) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Error HTTP al pedir el manifest: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"JSON inválido en el manifest remoto: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("providers"), dict):
        raise RuntimeError("Schema inesperado en el manifest remoto.")
    return data


def _merge_catalogs(bundled: Catalog, fresh: Catalog) -> Catalog:
    """Overlay a fresh catalog onto the bundled fallback by provider.

    A provider returned by Hermes is authoritative: its complete fresh list
    replaces the old list. The previous union algorithm retained every model
    ever bundled and then stamped the result with today's date, which made
    removed Claude/Grok/MiniMax entries look current forever. Providers absent
    from a partial legacy manifest still fall back to the bundled snapshot.
    """
    merged: Catalog = {slug: list(entries) for slug, entries in bundled.items()}
    for slug, entries in fresh.items():
        if entries:
            merged[slug] = list(entries)
    return merged


def _read_bundled_for_merge() -> tuple[Catalog, dict[str, Any]]:
    """Read the bundled snapshot for use as a merge floor.

    Returns ``(catalog, metadata)`` like ``_read_snapshot_file``, with
    an empty dict on any failure (missing, malformed, schema mismatch).
    Separated from ``load_snapshot()`` so the refresh path doesn't
    accidentally pick up the user's previous runtime snapshot when
    they're trying to refresh — that would compound staleness.
    """
    hit = _read_snapshot_file(
        _BUNDLED_SNAPSHOT_PATH,
        accepted_schemas={_SNAPSHOT_SCHEMA, *_LEGACY_BUNDLED_SCHEMAS},
    )
    if hit is None:
        return {}, {}
    catalog, metadata = hit
    if metadata.get("snapshot_schema") != _SNAPSHOT_SCHEMA:
        # Legacy bundled files are useful as migration provenance only. Their
        # model IDs are not a validated compatibility source for today's
        # Hermes and must never be re-stamped by a partial fallback refresh.
        return {}, metadata
    return catalog, metadata


def _resolve_hermes_python() -> Path | None:
    """Locate the interpreter owned by the managed Hermes installation."""
    from core.hermes_runtime import resolve_hermes_python

    python = resolve_hermes_python()
    return Path(python) if python else None


def _hermes_python_probe_command(python: Path, probe: str) -> list[str]:
    runtime = runtime_policy.get_runtime()
    if runtime.packaged:
        from core.hermes_runtime import hermes_command_prefix

        prefix = hermes_command_prefix()
        return [*prefix[:-1], "-c", probe]
    return [str(python), "-c", probe]


def _probe_timeout(seconds: float) -> float:
    """A cold included engine may need longer than a warm one (qa9)."""
    from core.hermes_runtime import PACKAGED_MIN_TIMEOUT

    return max(seconds, PACKAGED_MIN_TIMEOUT) if runtime_policy.get_runtime().packaged else seconds


def _hermes_probe_kwargs(timeout: float) -> dict[str, Any]:
    """Common safe subprocess options for isolated Hermes introspection."""
    from core.hermes_runtime import _hermes_subprocess_env

    timeout = _probe_timeout(timeout)
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "env": _hermes_subprocess_env(),
    }
    runtime = runtime_policy.get_runtime()
    if runtime.packaged:
        kwargs["cwd"] = str(runtime.hermes.root)
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return kwargs


def _convert_hermes_inventory_payload(raw: dict[str, Any]) -> Catalog:
    """Convert the official ``model.options``-style payload into IRIS rows."""
    rows = raw.get("providers")
    if not isinstance(rows, list):
        return {}

    catalog: Catalog = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("slug") or "").strip().lower()
        models = row.get("models")
        if not slug or not isinstance(models, list):
            continue

        # Q04: a free Nous account cannot use paid models, and while its plan is unknown Hermes
        # locks them all. Those rows stay visible but locked, never "available".
        unavailable_raw = row.get("unavailable_models")
        unavailable = (
            {str(model_id).strip() for model_id in unavailable_raw}
            if isinstance(unavailable_raw, list) else set()
        )
        plan_pending = bool(row.get("free_tier_pending"))
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for model in models:
            details = model if isinstance(model, dict) else {"id": model}
            mid = str(details.get("id") or "").strip()
            if not mid or mid in seen:
                continue
            output_raw = details.get("output") or []
            output_modalities = {
                str(modality).strip().lower()
                for modality in (
                    output_raw if isinstance(output_raw, (list, tuple, set)) else [output_raw]
                )
                if str(modality).strip()
            }
            # The wizard configures Hermes' conversational model. Rich live
            # catalogs can also expose image/video/TTS/embedding endpoints;
            # those belong in their dedicated IRIS sections, not this picker.
            if output_modalities and "text" not in output_modalities:
                continue
            seen.add(mid)
            tags = [str(tag) for tag in (details.get("tags") or []) if tag]
            needs_login = slug == "nous" and not row.get("authenticated")
            locked = needs_login or plan_pending or mid in unavailable
            entry = {
                "key": f"{slug}/{mid}",
                "id": mid,
                # Hermes' picker only derives names from ids; IRIS keeps
                # version numbers readable ("Opus 5.5", not "Opus 5 5").
                "name": _humanize_model_id(mid),
                "input": str(details.get("input") or "text"),
                "context": int(details.get("context") or 0),
                "local": bool(details.get("local")) or slug in {"lmstudio", "ollama"},
                "available": bool(row.get("authenticated")) and not locked,
                "missing": False,
                "tags": tags,
            }
            if locked:
                entry["locked"] = True
                entry["lock_reason"] = "auth" if needs_login else "pending" if plan_pending else "plan"
            entries.append(entry)
        # Presence is meaningful even when Hermes has no safe curated models:
        # it prevents IRIS from resurrecting a stale hardcoded list for that
        # provider and leaves the existing manual-model path available.
        catalog[slug] = entries
    return catalog


def _discover_hermes_inventory(
    refresh: bool = False,
    cancel_event: threading.Event | None = None,
) -> tuple[Catalog, dict[str, Any]]:
    """Read Hermes' official provider/model picker inventory in isolation.

    Hermes documents ``/api/model/options`` as the contract for GUI model
    pickers. Calling the same inventory builder with Hermes' own interpreter
    also works before its API server is running. Empty unconfigured rows are
    filled only from Hermes' own curated ``_PROVIDER_MODELS`` table; IRIS does
    not add its historical static models to an authoritative row.
    """
    global _provider_meta_cache

    python = _resolve_hermes_python()
    if python is None:
        return {}, {"inventory_supported": False, "reason": "python_missing"}

    refresh_literal = "True" if refresh else "False"
    probe = r'''
import json
from importlib.metadata import PackageNotFoundError, version
from hermes_cli.inventory import build_model_options_payload, load_picker_context
try:
    from hermes_cli.models import OPENROUTER_MODELS
except Exception:
    OPENROUTER_MODELS = []
try:
    from hermes_cli.models import _PROVIDER_MODELS
except Exception:
    _PROVIDER_MODELS = {}
try:
    from hermes_cli.main import _DEFAULT_QWEN_PORTAL_MODELS
except Exception:
    _DEFAULT_QWEN_PORTAL_MODELS = []

try:
    from agent.models_dev import get_model_info, list_agentic_models
except Exception:
    get_model_info = None
    list_agentic_models = None
try:
    from hermes_cli.auth import PROVIDER_REGISTRY
    from hermes_cli.models import CANONICAL_PROVIDERS
except Exception:
    PROVIDER_REGISTRY = {}
    CANONICAL_PROVIDERS = []
try:
    from hermes_cli.auth_commands import _OAUTH_CAPABLE_PROVIDERS
except Exception:
    _OAUTH_CAPABLE_PROVIDERS = set()

def nous_plan(models):
    # QA12/Q04: the list Hermes itself offers after a Nous sign-in (_pick_nous_model_after_login):
    # the Portal's recommendations for the account's tier, the org policy, and for a free
    # account the paid rows as unavailable. Unknown entitlement stays locked.
    pending = {"models": list(models), "free_tier_pending": True, "unavailable_models": list(models)}
    try:
        from hermes_cli.auth import get_provider_auth_state
        from hermes_cli.nous_account import get_nous_portal_account_info
        from hermes_cli.models import (
            partition_nous_models_by_tier, union_with_nous_on_sale_models,
            union_with_portal_free_recommendations, union_with_portal_paid_recommendations,
        )
        from hermes_cli.models_pricing import (
            get_pricing_for_provider, nous_policy_allowed_ids, restrict_to_nous_policy,
        )
        pricing = get_pricing_for_provider("nous") or {}
        account = get_nous_portal_account_info(force_fresh=True)
        # An unknown tier must not be confused with a confirmed paid account.
        if (not account.logged_in or account.error
                or type(account.paid_service_access) is not bool):
            return pending
        free_tier = not account.paid_service_access
        portal = str((get_provider_auth_state("nous") or {}).get("portal_base_url") or "")
        union = union_with_portal_free_recommendations if free_tier else union_with_portal_paid_recommendations
        ids, pricing = union(list(models), pricing, portal, force_refresh=True)
        if free_tier and not pricing:
            return pending
        if not free_tier:
            ids = union_with_nous_on_sale_models(ids, pricing)
        ids = restrict_to_nous_policy(ids, nous_policy_allowed_ids(), rescue_empty=True)
        unavailable = partition_nous_models_by_tier(ids, pricing, free_tier=True)[1] if free_tier else []
        ids = [m for m in ids if m not in unavailable] + list(unavailable)
        return {"models": ids, "free_tier": free_tier, "unavailable_models": list(unavailable)}
    except Exception:
        return pending


payload = build_model_options_payload(
    load_picker_context(),
    include_unconfigured=True,
    refresh=__REFRESH__,
)
rows = []
for source_row in payload.get("providers", []):
    slug = str(source_row.get("slug") or "").strip().lower()
    models = list(source_row.get("models") or [])
    if not models:
        if slug == "openrouter":
            models = [model_id for model_id, _label in OPENROUTER_MODELS]
        elif slug == "qwen-oauth":
            models = list(_DEFAULT_QWEN_PORTAL_MODELS)
        else:
            models = list(_PROVIDER_MODELS.get(slug, []))
    if not models and list_agentic_models is not None:
        try:
            models = list_agentic_models(slug, allow_network=False)
        except Exception:
            models = []
    # Per-model plan limits (Nous): unavailable rows, or all of them while the plan is unknown.
    plan = {key: source_row[key] for key in ("free_tier", "free_tier_pending", "unavailable_models")
            if key in source_row}
    if slug == "nous" and source_row.get("authenticated") and __REFRESH__:
        checked = nous_plan(models)
        if checked:
            models = checked.pop("models")
            plan = checked

    capabilities = source_row.get("capabilities") or {}
    converted = []
    for model_id in models:
        model_id = str(model_id).strip()
        if not model_id:
            continue
        context = 0
        input_kind = "text"
        output_modalities = set()
        tags = []
        info = None
        if get_model_info is not None:
            try:
                info = get_model_info(slug, model_id, allow_network=False)
            except Exception:
                info = None
        if info is not None:
            context = int(getattr(info, "context_window", 0) or 0)
            modalities = set(getattr(info, "input_modalities", ()) or ())
            output_modalities = set(getattr(info, "output_modalities", ()) or ())
            if getattr(info, "supports_vision", lambda: False)() or "image" in modalities:
                input_kind += "+image"
            if "audio" in modalities:
                input_kind += "+audio"
            status = str(getattr(info, "status", "") or "").strip().lower()
            if status:
                tags.append(status)
        model_caps = capabilities.get(model_id) or {}
        if model_caps.get("reasoning"):
            tags.append("reasoning")
        converted.append({
            "id": model_id,
            "name": model_id.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title(),
            "context": context,
            "input": input_kind,
            "output": sorted(output_modalities),
            "tags": list(dict.fromkeys(tags)),
            "local": slug in {"lmstudio", "ollama"},
        })
    rows.append({
        "slug": slug,
        "authenticated": bool(source_row.get("authenticated")),
        "models": converted,
        **plan,
    })

provider_metadata = {}
for entry in CANONICAL_PROVIDERS:
    slug = str(getattr(entry, "slug", "") or "").strip().lower()
    if not slug:
        continue
    cfg = PROVIDER_REGISTRY.get(slug)
    provider_metadata[slug] = {
        "label": str(getattr(entry, "label", slug)),
        "description": str(
            getattr(entry, "tui_desc", None)
            or getattr(entry, "description", "")
            or ""
        ),
        "auth_type": str(getattr(cfg, "auth_type", "")) if cfg is not None else "",
        "env_vars": list(getattr(cfg, "api_key_env_vars", ()) or ()) if cfg is not None else [],
        "oauth_capable": slug in _OAUTH_CAPABLE_PROVIDERS,
    }

hermes_version = ""
for distribution in ("hermes-agent", "hermes_agent"):
    try:
        hermes_version = version(distribution)
        break
    except PackageNotFoundError:
        pass
result = {
    "providers": rows,
    "provider_metadata": provider_metadata,
    "hermes_version": hermes_version,
}
print("IRIS_HERMES_INVENTORY=" + json.dumps(result, ensure_ascii=False))
'''.replace("__REFRESH__", refresh_literal)

    timeout = _probe_timeout(45 if refresh else 20)
    try:
        if cancel_event is None:
            completed = subprocess.run(
                _hermes_python_probe_command(python, probe),
                **_hermes_probe_kwargs(timeout),
            )
        else:
            popen_kwargs = _hermes_probe_kwargs(timeout)
            popen_kwargs.pop("timeout", None)
            popen_kwargs.pop("capture_output", None)
            process = subprocess.Popen(
                _hermes_python_probe_command(python, probe),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **popen_kwargs,
            )
            deadline = time.monotonic() + timeout
            while True:
                if cancel_event.is_set():
                    try:
                        process.terminate()
                    except OSError:
                        process.kill()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                    return {}, {"inventory_supported": True, "error": "cancelled"}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    stdout, stderr = process.communicate()
                    logger.debug("Hermes inventory probe timed out: %s", stderr[-500:])
                    return {}, {"inventory_supported": True, "error": "timeout"}
                try:
                    stdout, stderr = process.communicate(timeout=min(0.25, remaining))
                    completed = subprocess.CompletedProcess(
                        process.args,
                        process.returncode,
                        stdout,
                        stderr,
                    )
                    break
                except subprocess.TimeoutExpired:
                    continue
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("Hermes inventory probe failed: %s", exc)
        return {}, {"inventory_supported": True, "error": str(exc)}
    if completed.returncode != 0:
        logger.debug("Hermes inventory probe failed: %s", completed.stderr[-500:])
        stderr_lower = completed.stderr.lower()
        unsupported = (
            "no module named 'hermes_cli.inventory'" in stderr_lower
            or "cannot import name 'build_model_options_payload'" in stderr_lower
            or "cannot import name 'load_picker_context'" in stderr_lower
            or (
                "unexpected keyword argument" in stderr_lower
                and "build_model_options_payload" in stderr_lower
                and (
                    "include_unconfigured" in stderr_lower
                    or "refresh" in stderr_lower
                )
            )
        )
        return {}, {
            "inventory_supported": not unsupported,
            "error": completed.stderr[-500:],
        }

    marker = "IRIS_HERMES_INVENTORY="
    line = next(
        (
            item[len(marker):]
            for item in reversed(completed.stdout.splitlines())
            if item.startswith(marker)
        ),
        "",
    )
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return {}, {"inventory_supported": True, "error": "invalid_json"}
    if not isinstance(payload, dict):
        return {}, {"inventory_supported": True, "error": "invalid_payload"}
    catalog = _convert_hermes_inventory_payload(payload)
    if not catalog:
        return {}, {"inventory_supported": True, "error": "empty_inventory"}
    provider_metadata = _normalise_provider_metadata(payload.get("provider_metadata"))
    if provider_metadata:
        _provider_meta_cache = (time.monotonic(), provider_metadata)
    return catalog, {
        "inventory_supported": True,
        "hermes_version": str(payload.get("hermes_version") or ""),
        "providers_count": len(catalog),
        "models_count": sum(len(models) for models in catalog.values()),
        "provider_metadata": provider_metadata,
    }


def _discover_local_hermes_providers(
    timeout_s: float = 12.0,
) -> dict[str, dict[str, Any]]:
    """Read Hermes' installed source for the live provider list.

    Hermes' published model manifest only ships catalog entries for
    OpenRouter and Nous (the two providers without API-side
    discovery). Every direct-API plugin — Anthropic, Gemini, Groq's
    spiritual successors, NVIDIA, etc. — only shows up if we
    introspect the local install.

    This function imports ``hermes_cli.models.CANONICAL_PROVIDERS``
    (the single source of truth Hermes uses for the ``hermes model``
    interactive picker) and ``hermes_cli.auth.PROVIDER_REGISTRY``
    (where each plugin declares its env vars + auth flavour). The
    intersection gives us a fully-typed list, with every entry
    guaranteed to be a slug Hermes' resolver accepts.

    Returns ``{slug: {"label": str, "description": str, "auth_type":
    str, "env_vars": tuple[str, ...]}}``. Empty dict on any
    discovery failure (Hermes not installed, version mismatch,
    import explodes) — caller treats that as "stick with what we
    already have".
    """
    out: dict[str, dict[str, Any]] = {}

    # Importing Hermes into IRIS' process is unsafe: both applications have
    # independent virtual environments and dependency versions. Run the
    # introspection with Hermes' own Python and exchange a tiny JSON payload.
    try:
        python = _resolve_hermes_python()
        if python is None:
            return out

        probe = r'''
import json
from hermes_cli.auth import PROVIDER_REGISTRY
from hermes_cli.models import CANONICAL_PROVIDERS
try:
    from hermes_cli.auth_commands import _OAUTH_CAPABLE_PROVIDERS
except Exception:
    _OAUTH_CAPABLE_PROVIDERS = set()

result = {}
for entry in CANONICAL_PROVIDERS:
    slug = str(entry.slug)
    cfg = PROVIDER_REGISTRY.get(slug)
    result[slug] = {
        "label": str(getattr(entry, "label", slug)),
        "description": str(getattr(entry, "tui_desc", None) or getattr(entry, "description", "")),
        "auth_type": str(getattr(cfg, "auth_type", "")) if cfg is not None else "",
        "env_vars": list(getattr(cfg, "api_key_env_vars", ()) or ()) if cfg is not None else [],
        "oauth_capable": slug in _OAUTH_CAPABLE_PROVIDERS,
    }
print(json.dumps(result, ensure_ascii=False))
'''
        kwargs = _hermes_probe_kwargs(max(0.1, timeout_s))
        completed = subprocess.run(_hermes_python_probe_command(python, probe), **kwargs)
        if completed.returncode != 0:
            logger.debug("Hermes provider probe failed: %s", completed.stderr[-500:])
            return out
        payload = json.loads(completed.stdout.strip())
        if not isinstance(payload, dict):
            return out
        for slug, meta in payload.items():
            if not isinstance(slug, str) or not isinstance(meta, dict):
                continue
            out[slug] = {
                "label": str(meta.get("label") or slug),
                "description": str(meta.get("description") or ""),
                "auth_type": str(meta.get("auth_type") or ""),
                "env_vars": tuple(str(v) for v in (meta.get("env_vars") or []) if v),
                "oauth_capable": bool(meta.get("oauth_capable")),
            }
    except runtime_policy.RuntimeDescriptorError:
        raise
    except Exception as exc:
        logger.debug("Hermes local discovery failed: %s", exc)
        return out

    return out


def local_hermes_provider_metadata(
    *,
    allow_probe: bool = True,
) -> dict[str, dict[str, Any]]:
    """Return cached provider/auth capabilities from the installed Hermes.

    Empty means Hermes is absent or too damaged to introspect. Callers must
    then fall back to the curated registry and must not invent OAuth support.
    """
    global _provider_meta_cache
    runtime_policy.get_runtime()
    now = time.monotonic()
    if _provider_meta_cache and now - _provider_meta_cache[0] < _CACHE_TTL_SECONDS:
        return _provider_meta_cache[1]
    # Schema-4 runtime snapshots carry the exact auth capabilities that were
    # observed together with the model inventory. This keeps GET /api/providers
    # read-only and fast across restarts, instead of spawning Hermes merely
    # because the user opened the provider step.
    _catalog, snapshot = load_snapshot()
    snapshot_meta = _normalise_provider_metadata(snapshot.get("provider_metadata"))
    if snapshot.get("source") == "runtime" and snapshot_meta:
        _provider_meta_cache = (now, snapshot_meta)
        return snapshot_meta
    if not allow_probe:
        return {}
    data = _discover_local_hermes_providers()
    _provider_meta_cache = (now, data)
    return data


def _humanize_model_id(mid: str) -> str:
    """Build a display name from a raw model id.

    Hermes' manifest entries often look like ``anthropic/claude-sonnet-4``
    or just ``glm-5``. We take the segment after the last ``/``, swap
    separators for spaces, and title-case. Good enough for "Claude
    Sonnet 4" / "Glm 5" — the curated registry overrides this for
    headline models when the slug matches.
    """
    last = mid.rsplit("/", 1)[-1]
    words: list[str] = []
    for word in last.replace("_", " ").replace("-", " ").split():
        # Version digits written with dashes read as a version: "opus-5-5" is
        # Opus 5.5, not "Opus 5 5" (qa9). Dates such as 20251101 stay apart.
        if (words and len(word) <= 2 and word.isdigit()
                and re.fullmatch(r"\d{1,2}(?:\.\d{1,2})*", words[-1])):
            words[-1] += "." + word
        else:
            words.append(word)
    return " ".join(word[:1].upper() + word[1:] for word in words)


def _convert_hermes_manifest(raw: dict[str, Any]) -> Catalog:
    """Translate Hermes' published schema into the wizard's schema.

    Hermes input::

        {"providers": {"<slug>": {"models": [{"id": "...", "description": "..."}], ...}, ...}}

    Wizard output::

        {"<slug>": [{"key": "<slug>/<id>", "id": "<id>", "name": "...",
                     "input": "text", "context": 0, "local": false,
                     "available": false, "missing": false, "tags": [...]}, ...]}

    Hermes' ``description`` field on OpenRouter entries carries badge
    strings like ``"recommended"`` / ``"free"``; we surface those as
    tags so `providers.py` can render the right ribbon.
    """
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        return {}

    catalog: Catalog = {}
    for slug, prov_block in providers.items():
        if not isinstance(slug, str) or not isinstance(prov_block, dict):
            continue
        models = prov_block.get("models")
        if not isinstance(models, list):
            continue

        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for m in models:
            if not isinstance(m, dict):
                continue
            mid = m.get("id")
            if not isinstance(mid, str) or not mid:
                continue
            if mid in seen:
                continue
            seen.add(mid)

            tags: list[str] = []
            desc = (m.get("description") or "").strip().lower()
            if desc in {"recommended", "free", "experimental",
                        "deprecated", "beta", "preview"}:
                tags.append(desc)

            entries.append({
                "key":       f"{slug}/{mid}",
                "id":        mid,
                "name":      m.get("name") or _humanize_model_id(mid),
                "input":     m.get("input") or "text",
                "context":   int(m.get("contextWindow") or 0),
                "local":     bool(m.get("local")) or slug in {"lmstudio", "ollama"},
                "available": False,
                "missing":   False,
                "tags":      tags,
            })

        if entries:
            catalog[slug] = entries

    return catalog


# ── Public API ─────────────────────────────────────────────────────


async def hermes_catalog() -> Catalog:
    """Return the Hermes model catalog, grouped by provider slug.

    Read-only — this function never spawns a subprocess. The
    catalog comes from three tiers in order:

      1. 60 s in-memory cache (hot path during a session).
      2. Runtime snapshot at `~/.iris/provider_catalog_snapshot.json`
         (user's last explicit refresh).
      3. Bundled snapshot at `wizard/provider_catalog_snapshot.json`
         (shipped baseline).
      4. Empty dict → caller falls back to the static `providers.py`
         list.

    The explicit slow refresh lives in `fetch_live_catalog()` and is
    only called from the `/api/providers/refresh` SSE endpoint. We
    deliberately don't spawn here because the live fetch can take
    up to 45 s, and `/api/providers` runs on every wizard reload —
    blocking that request path is unacceptable.
    """
    # The memory-cache key is stable across calls (no subprocess
    # probe needed): it's just a sentinel, since the snapshot
    # files themselves are the source of truth.
    runtime_policy.get_runtime()
    CACHE_KEY = ("__snapshot__", "__snapshot__")
    now = time.monotonic()
    hit = _cache.get(CACHE_KEY)
    if hit and (now - hit[0]) < _CACHE_TTL_SECONDS:
        return hit[1]

    # Snapshot read is synchronous I/O on a file < 500 KB. At this
    # size the cost is dominated by JSON parse (~5-15 ms on a
    # typical SSD), so we hop to the executor to keep the event
    # loop free for other request handlers.
    loop = asyncio.get_running_loop()
    catalog, meta = await loop.run_in_executor(None, load_snapshot)
    if (
        meta.get("source") == "bundled"
        and meta.get("snapshot_schema") != _SNAPSHOT_SCHEMA
    ):
        catalog = {}

    # Cache even empty results briefly so a missing snapshot doesn't
    # cause us to re-read the filesystem on every `/api/providers`
    # hit while the first-run user hasn't clicked Refresh yet.
    _cache[CACHE_KEY] = (now, catalog)
    return catalog


class UnsupportedByEngine(RuntimeError):
    """Reserved exception for engines that lack a published catalog.

    Currently unused — Hermes publishes its manifest at a stable URL,
    so the live-fetch path always has somewhere to read from. Kept
    so `app.py`'s SSE error mapping doesn't lose its `unsupported`
    code if a future engine swap reintroduces this case.
    """


async def fetch_live_catalog() -> tuple[Catalog, dict[str, Any]]:
    """Read Hermes' current picker inventory and persist a snapshot.

    Source order:

      1. Installed Hermes inventory, identical to ``/api/model/options``.
      2. Local/public legacy manifest for older Hermes installations.

    Returns ``(catalog, metadata)``. Metadata on success::

        {"snapshot_date":  "YYYY-MM-DD",
         "hermes_version": "Hermes catalog v<n> (updated YYYY-MM-DD)",
         "model_count":    int,
         "path":           "<runtime snapshot path>",
         "duration_s":     float,
         "source":         "installed_inventory" | "hermes_cache" | "remote"}

    Raises:
        TimeoutError  — remote fetch exceeded the timeout.
        RuntimeError  — manifest unreachable, malformed, or empty.
        OSError       — snapshot write failure.

    These map onto the SSE error codes in ``wizard/app.py``: TimeoutError
    → ``timeout``, RuntimeError → ``crashed``, OSError → ``write_failed``.
    """
    runtime_policy.get_runtime()
    loop = asyncio.get_running_loop()
    t0 = time.monotonic()

    # Preferred path: this is the same inventory builder exposed by Hermes at
    # /api/model/options. Its provider rows are authoritative; mixing the old
    # bundled list back in would resurrect models removed upstream.
    cancel_event = threading.Event()
    try:
        inventory, inventory_meta = await loop.run_in_executor(
            None, _discover_hermes_inventory, True, cancel_event,
        )
    except asyncio.CancelledError:
        # Cancelling an executor future does not stop its thread. Signal the
        # worker, which owns and terminates the Hermes child process.
        cancel_event.set()
        raise
    if inventory:
        installed_version = str(inventory_meta.get("hermes_version") or "").strip()
        version_label = (
            f"Hermes Agent {installed_version} · inventario oficial"
            if installed_version
            else "Hermes Agent · inventario oficial"
        )
        try:
            meta = await loop.run_in_executor(
                None,
                _write_runtime_snapshot,
                inventory,
                version_label,
                "installed_inventory",
                "",
                inventory_meta.get("provider_metadata") or {},
            )
        except OSError as exc:
            raise OSError(f"No se pudo escribir el snapshot: {exc}") from exc

        _cache[("__snapshot__", "__snapshot__")] = (time.monotonic(), inventory)
        meta["duration_s"] = round(time.monotonic() - t0, 1)
        meta["source"] = "installed_inventory"
        meta["local_discovered"] = int(inventory_meta.get("providers_count") or 0)
        return inventory, meta
    if inventory_meta.get("inventory_supported"):
        error = str(inventory_meta.get("error") or "inventory_failed")
        if error == "cancelled":
            raise asyncio.CancelledError
        if error == "timeout":
            raise TimeoutError("El inventario oficial de Hermes excedió 45 segundos.")
        raise RuntimeError(f"El inventario oficial de Hermes falló: {error}")

    # Tier 1 — Hermes' own cache (synchronous file read; cheap).
    raw = await loop.run_in_executor(None, _read_hermes_cache_or_none)
    source = "hermes_cache"

    # Tier 2 — public manifest. The remote fetch is async because
    # httpx supports it natively; no executor hop needed.
    if raw is None:
        remaining = 45.0 - (time.monotonic() - t0)
        if remaining <= 0:
            raise TimeoutError("La actualización del catálogo excedió 45 segundos.")
        try:
            raw = await asyncio.wait_for(
                _fetch_remote_catalog(remaining),
                timeout=remaining,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                "La actualización del catálogo excedió 45 segundos."
            ) from exc
        source = "remote"

    fresh = _convert_hermes_manifest(raw)
    if not fresh:
        raise RuntimeError("El manifest no contiene proveedores válidos.")

    # The legacy public manifest is partial. A slug it does publish replaces
    # that provider's bundled list; bundled-only slugs remain as compatibility
    # fallbacks for old Hermes versions. We never union model IDs within a
    # refreshed slug, because doing so preserves removed models forever.
    bundled, _meta = await loop.run_in_executor(None, _read_bundled_for_merge)
    remaining = 45.0 - (time.monotonic() - t0)
    if remaining > 0.75:
        # Preserve a small budget for snapshot serialization. The subprocess
        # itself receives the bounded timeout, so an old Hermes install cannot
        # extend the UI's advertised 45-second refresh window by another 12 s.
        metadata_timeout = min(12.0, max(0.1, remaining - 0.5))
        try:
            local_discovered = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    _discover_local_hermes_providers,
                    metadata_timeout,
                ),
                timeout=metadata_timeout + 0.2,
            )
        except asyncio.TimeoutError:
            local_discovered = {}
    else:
        local_discovered = {}
    discovered_count = len(local_discovered)

    catalog: Catalog = _merge_catalogs(bundled, fresh)

    # Provenance stamp combines the manifest's schema version with
    # its ``updated_at`` timestamp so the wizard badge reflects the
    # upstream publish date instead of just our local fetch date.
    schema_v = raw.get("version", 1)
    updated_at = str(raw.get("updated_at") or "").strip()
    updated_short = updated_at[:10] if updated_at else "fecha desconocida"
    version_label = f"Hermes catalog v{schema_v} (updated {updated_short})"

    try:
        meta = await loop.run_in_executor(
            None,
            _write_runtime_snapshot,
            catalog,
            version_label,
            source,
            updated_short if updated_at else "",
            local_discovered,
        )
    except OSError as exc:
        raise OSError(f"No se pudo escribir el snapshot: {exc}") from exc

    # Warm memory cache so the next `/api/providers` hit is instant.
    _cache[("__snapshot__", "__snapshot__")] = (time.monotonic(), catalog)

    meta["duration_s"] = round(time.monotonic() - t0, 1)
    meta["source"] = source
    meta["local_discovered"] = discovered_count
    return catalog, meta


# ── Heuristics for merging into the curated registry ───────────────

# Keywords that bump a model into the "premium" / "reasoning" / "fast"
# tiers when we synthesize entries from Hermes output. The static
# registry keeps its hand-curated tiers; this is only for discovered
# providers we've never seen before.
_PREMIUM_RE = (
    "opus", "premier", "ultra", "flagship", "-pro", "-4.20",
    "-5.6", "-5.5", "-5.4",
)
_FAST_RE    = ("haiku", "mini", "flash", "lite", "turbo", "nano", "fast", "instant", "highspeed")
_REASON_RE  = ("reasoning", "thinking", "-r1", "-o1", "-o3", "deep-think", "magistral")


def guess_tier(model_id: str, name: str, tags: list[str]) -> str:
    """Map a Hermes model entry to one of our tier strings.

    The static registry uses `{fast, mid, premium, reasoning}`. When
    Hermes surfaces a model we don't know about, we pick a tier
    from its ID + display name so the UI can still sort/badge
    sensibly.
    """
    haystack = f"{model_id} {name}".lower()
    tagset = {t.lower() for t in tags if isinstance(t, str)}
    if "reasoning" in tagset or any(k in haystack for k in _REASON_RE):
        return "reasoning"
    if any(k in haystack for k in _PREMIUM_RE):
        return "premium"
    if any(k in haystack for k in _FAST_RE):
        return "fast"
    return "mid"


# Known-good guesses for Hermes slugs we don't curate in the static
# registry. Used when a discovered provider surfaces: we render it as
# a real entry instead of "<slug>" titlecased. Missing entries fall
# back to (slug.title(), "🔌", "aggregator").
#
# Auth values mirror `providers.py::AuthMode`. Setting `auth`
# explicitly here matters for two cases the env_key heuristic
# alone can't distinguish:
#   * OAuth-only providers (`codex`, `google-antigravity`,
#     `google-gemini-cli`, `opencode*`) have no env_key but DO
#     need a login flow — UI must show OAuth path, not "Sin auth".
#   * `github-copilot` accepts BOTH a personal access token AND
#     `hermes auth add --type oauth github-copilot`, so it's
#     "api+oauth", not just "api".
DISCOVERY_META: dict[str, dict[str, str]] = {
    "amazon-bedrock":         {"name": "Amazon Bedrock",           "icon": "🟠", "category": "cloud",      "auth": "aws",       "env_key": ""},
    "azure-openai-responses": {"name": "Azure OpenAI",              "icon": "🔷", "category": "cloud",      "auth": "api",       "env_key": "AZURE_OPENAI_API_KEY"},
    "cerebras":               {"name": "Cerebras",                  "icon": "🧠", "category": "cloud",      "auth": "api",       "env_key": "CEREBRAS_API_KEY"},
    "codex":                  {"name": "OpenAI Codex (CLI auth)",   "icon": "🟢", "category": "popular",    "auth": "oauth",     "env_key": ""},
    "github-copilot":         {"name": "GitHub Copilot",            "icon": "🐙", "category": "cloud",      "auth": "api+oauth", "env_key": "GITHUB_TOKEN"},
    "google-antigravity":     {"name": "Google Antigravity",        "icon": "🔵", "category": "popular",    "auth": "oauth",     "env_key": ""},
    "google-gemini-cli":      {"name": "Gemini CLI (OAuth)",        "icon": "🔵", "category": "popular",    "auth": "oauth",     "env_key": ""},
    "google-vertex":          {"name": "Google Vertex AI",          "icon": "🔵", "category": "cloud",      "auth": "api",       "env_key": "GOOGLE_APPLICATION_CREDENTIALS"},
    "groq":                   {"name": "Groq",                      "icon": "⚡", "category": "cloud",      "auth": "api",       "env_key": "GROQ_API_KEY"},
    "kimi-coding":            {"name": "Kimi Coding",               "icon": "🌙", "category": "chinese",    "auth": "api",       "env_key": "MOONSHOT_API_KEY"},
    "lmstudio":               {"name": "LM Studio (Local)",         "icon": "🏠", "category": "local",      "auth": "api-optional", "env_key": "LM_API_KEY"},
    "minimax-cn":             {"name": "MiniMax (CN region)",       "icon": "🔷", "category": "chinese",    "auth": "api",       "env_key": "MINIMAX_API_KEY"},
    "opencode":               {"name": "OpenCode",                  "icon": "💻", "category": "aggregator", "auth": "oauth",     "env_key": ""},
    "opencode-go":            {"name": "OpenCode Go",               "icon": "💻", "category": "aggregator", "auth": "oauth",     "env_key": ""},
    "vercel-ai-gateway":      {"name": "Vercel AI Gateway",         "icon": "▲",  "category": "aggregator", "auth": "api",       "env_key": "VERCEL_API_KEY"},
    "zai":                    {"name": "Z.AI (GLM)",                "icon": "🔶", "category": "chinese",    "auth": "api",       "env_key": "ZHIPUAI_API_KEY"},
}




