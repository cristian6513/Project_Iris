"""
IRIS WebSocket Server — Real-time Communication Hub

FastAPI server providing:
- WebSocket at :8765/ws for the face layer (Three.js avatar)
- REST API for status, config, and wizard integration
- Serves the face/ directory as static files
"""

from __future__ import annotations

import asyncio
import hmac
import json
import sqlite3
import logging
import math
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import (
    Body,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from core.static_assets import LocalWebStaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from core import runtime_descriptor as runtime_policy

from core.capability_permissions import (
    CapabilityPermissionStore,
    approval_dry_run,
    run_security_audit,
    set_hermes_tool_enabled,
    test_mcp_server,
)
from core.capability_permissions import (
    capability_center_snapshot as build_capability_center_snapshot,
)
from core.companion_auth import (
    COMPANION_TOKEN_HEADER,
    COMPANION_WS_PROTOCOL,
    companion_token_from_websocket_protocol,
    is_tauri_companion_origin,
    is_valid_companion_token,
)
from core.companion_lifecycle import (
    companion_operation_lock,
)
from core.companion_lifecycle import (
    router as companion_lifecycle_router,
)
from core.asset_uploads import (
    AVATAR_MAX_BYTES,
    AssetUploadError,
    atomic_avatar_write,
    read_bounded,
    upload_filename,
    validate_asset,
)
from core.upload_routes import asset_upload_route
from core.config import IRISConfig, get_iris_home
from core.async_agents import ManualRunStore, result_text
from core.async_agents_api import router as async_agents_router
from core.document_attachments import (
    MAX_PDF_BYTES,
    PdfAttachmentError,
    PdfNoTextError,
    PdfTooLargeError,
    extract_pdf_text,
)
from core.hermes_wake import router as hermes_wake_router
from core.identity_api import router as identity_router
from core.model_selection_api import model_selection_router
from core.vision_model_api import vision_model_router
from core import (app_messages, app_notices, background_tasks, vision_model, vision_snapshot, screen_snapshot,
                  visual_requests)
from core.iris_skill_lifecycle import installed_iris_skill_dir
from core.app_actions import AppActionBridge, AppActionRegistry, OperationRejected
from core.google_api import create_google_router, require_local_peer
from core.desktop_widgets import create_desktop_widgets_router, forget_app
from core.app_packages import create_app_packages_router, installed_catalog
from core.app_extensions import AppExtensions
from core.widget_catalog import APP_IDS as BUNDLED_APP_IDS, ICON_NAMES
from core.theme_fonts import create_theme_fonts_router
from core.calendar_api import create_calendar_router
from core.mail_api import create_mail_router
from core.mail_classification_api import create_classification_router
from core.mail_classification import MailClassificationRuntime
from core.meeting_preparation import MeetingPreparationRuntime
from core.meeting_api import create_meeting_router
from core.calendar_runtime import CalendarRuntime, create_calendar_preferences_router
from core.calendar_store import CalendarStoreError
from core.app_services import include_app_services
from core.player_actions import register_player_actions
from core.google_actions import register_google_actions
from core.automation_api import AutomationRuntime, HermesRunsTransport, automation_router, hermes_profile_route
from core.inbox_api import InboxPresenter, InboxService, inbox_router
from core.iris_player import (
    InvalidLibraryRootError,
    IrisPlayerError,
    IrisPlayerLibrary,
    LibraryNotConfiguredError,
    TrackNotFoundError,
    UnsafeTrackPathError,
)
from core.request_security import (
    is_loopback_host,
    is_loopback_url,
    is_safe_service_url,
    is_public_bootstrap_path,
    is_trusted_http_host,
    is_trusted_local_origin,
)
from core.surface_registry import SurfaceRegistry
from core.tts_playback import TtsPlaybackReceipts
from core.version import __version__ as IRIS_VERSION
from core.vision_assistant import (
    VisionAssistantRuntime,
    VisionSignalError,
    normalize_user_emotion,
)
from core.vision_policy import VisionPolicySettings
from core.notification_api import notification_router
from core.vision_diagnostics import VisionSensorDiagnostics

logger = logging.getLogger("IRIS.websocket")
_LAN_PAIR_COOKIE = "iris_lan_pair"

# Connected WebSocket clients
_connected_clients: set[WebSocket] = set()
_surface_registry = SurfaceRegistry()
_tts_playback = TtsPlaybackReceipts()
_api_cache: dict[str, tuple[float, Any]] = {}
_FACE_IMAGE_MAX_COUNT = 8
_FACE_IMAGE_MAX_TOTAL_CHARS = 14 * 1024 * 1024
_LOCAL_ONLY_READ_PATHS = {
    "/api/notifications",
    "/api/capability-center",
    "/api/gateway-logs",
    "/api/hermes/continuity",
    "/api/vision-assistant/settings",
    "/api/vision-assistant/events",
    # F02: starts Hermes' interpreter for the profile; administration of this PC.
    "/api/vision-model",
    # F06: rules, prompts and results of this PC's automations.
    "/api/automations",
    # F07: the general inbox (automation results and Vision notices).
    "/api/inbox",
    "/api/calendar",
    "/api/mail",
    "/api/desktop/widgets",
}
_vision_assistant = VisionAssistantRuntime()
_capability_store = CapabilityPermissionStore()
# F09.4: operations Hermes may ask IRIS OS apps to perform. Registered in reviewed code only;
# IRIS Player is the first. The bridge reads the registry and policy store at call time.
_app_action_registry = AppActionRegistry()
_app_extensions = AppExtensions(get_iris_home, registry=_app_action_registry,
                                notices=lambda: _app_notices, notices_changed=lambda: _app_notices_changed())
register_player_actions(_app_action_registry, track_label=lambda track_id: _player_library().track_label(track_id))
register_google_actions(_app_action_registry, lambda: _google_service)
_app_actions = AppActionBridge(
    _app_action_registry,
    surfaces=lambda: _surface_registry,
    capabilities=lambda: _capability_store,
    on_disconnect=lambda socket: _disconnect_surface(socket),
    on_audit=lambda: _invalidate_capability_center_cache(),
    # App actions run on, and are confirmed by, this PC's own surfaces only (Codex A01).
    owner_allowed=lambda socket: is_loopback_host(getattr(getattr(socket, "client", None), "host", "") or ""),
)
_snapshot_broker = vision_snapshot.SnapshotBroker()
_screen_snapshot_broker = vision_snapshot.SnapshotBroker()
_snapshot_consent = vision_snapshot.SnapshotConsent()
_visual_gates = visual_requests.VisualRequestGates()  # Iris may ask to look once per local turn
_launch_offers = background_tasks.LaunchOffers()  # Iris may start one background task per local turn
_background_runtime: background_tasks.BackgroundTaskRuntime | None = None  # started by core.agent
_app_notices: app_notices.AppNoticeStore | None = None  # notices apps scheduled; started by core.agent
_app_notices_wake: asyncio.Event | None = None
_app_notices_due = -1  # due notices last counted, so the Bandeja is told once when more arrive
_background_deliveries: set[asyncio.Task] = set()
_background_turns: set[asyncio.Task] = set()
_turn_epoch = 0  # +1 per turn that arrives: a photo is only asked for the latest one
# The turn this task is running, so that what it reports says which turn it belongs to.
current_turn: ContextVar[int | None] = ContextVar("iris_current_turn", default=None)
# +1 each time Core applies a permission change: a photo or capture still preparing
# re-reads its permissions instead of using a verdict from before the change (Codex V02).
_policy_revision = 0
_automation_runtime: AutomationRuntime | None = None  # F04–F06, started by core.agent
_google_service = None  # F10: the profile's Google connections, started by core.agent
_calendar_runtime: CalendarRuntime | None = None  # F11: local view and deterministic reminders
_mail_classification: MailClassificationRuntime | None = None
_meeting_preparation: MeetingPreparationRuntime | None = None
_inbox_tasks: set[asyncio.Task] = set()  # F07: return notices and inbox announcements in flight
_inbox_offer_at = float("-inf")
_INBOX_OFFER_EVERY_S = 3.0  # interactions arrive often; one claim attempt at most this often
_inbox_announce_pending = False


def _connect_surface(websocket: WebSocket) -> None:
    """Register one socket in the legacy set and capability registry."""
    _connected_clients.add(websocket)
    _surface_registry.connect(websocket)


def _disconnect_surface(websocket: WebSocket) -> bool:
    """Remove a socket and report whether it released the camera lease."""
    _snapshot_broker.drop_surface(websocket)
    _screen_snapshot_broker.drop_surface(websocket)
    _tts_playback.disconnect(websocket)
    released_camera = _surface_registry.camera_owner() is websocket
    _connected_clients.discard(websocket)
    _surface_registry.disconnect(websocket)
    if released_camera:
        app.state.user_emotion = None
        _vision_assistant.reset_observation()
    return released_camera


def _valid_face_images(images: Any) -> bool:
    """Accept only bounded base64 image data URLs from the local composer."""
    if images is None:
        return True
    if not isinstance(images, list) or len(images) > _FACE_IMAGE_MAX_COUNT:
        return False
    if sum(len(image) for image in images if isinstance(image, str)) > _FACE_IMAGE_MAX_TOTAL_CHARS:
        return False
    return all(
        isinstance(image, str)
        and image.startswith("data:image/")
        and ";base64," in image[:160]
        for image in images
    )


def _cache_get(key: str, ttl_s: float, *, force: bool = False) -> Any | None:
    if force:
        return None
    cached = _api_cache.get(key)
    if not cached:
        return None
    ts, value = cached
    if time.monotonic() - ts > ttl_s:
        _api_cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> Any:
    _api_cache[key] = (time.monotonic(), value)
    return value

# Global reference to the FastAPI app
app = FastAPI(
    title="IRIS OS",
    description="AI operating environment powered by Hermes Agent",
    version=IRIS_VERSION,
)


@app.exception_handler(runtime_policy.RuntimePolicyError)
async def runtime_policy_error(_request: Request, exc) -> JSONResponse:
    return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=exc.status_code)


@app.exception_handler(runtime_policy.RuntimeDescriptorError)
async def runtime_descriptor_error(_request: Request, _exc) -> JSONResponse:
    return JSONResponse({"detail": "El descriptor de runtime no es válido.",
                         "code": "runtime_descriptor_invalid"}, status_code=503)

# CORS is only needed for loopback aliases. LAN clients use relative URLs on
# the same host/port, so their requests are same-origin and need no CORS grant.
# Mutation requests also have an explicit same-host Origin check below.
_CORS_ORIGIN_REGEX = (
    r"^(?:https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?|"
    r"https?://tauri\.localhost|tauri://localhost)$"
)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_CORS_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["ETag"],
)


@app.middleware("http")
async def enforce_mutation_origin(request: Request, call_next):
    """Authorize HTTP reads and writes without weakening Origin or pairing."""
    authorities = request.headers.getlist("host")
    peer_host = request.client.host if request.client else ""
    direct_remote_client = bool(peer_host and not is_loopback_host(peer_host))
    cfg = IRISConfig.load() if direct_remote_client else None
    authority = authorities[0] if len(authorities) == 1 else ""
    trusted_host = is_trusted_http_host(authority)
    if not trusted_host and len(authorities) == 1:
        cfg = cfg or IRISConfig.load()
        server = request.scope.get("server")
        trusted_host = is_trusted_http_host(
            authority,
            server_host=server[0] if server else None,
            bind_host=cfg.websocket_host,
            allow_lan=cfg.lan_access and not cfg.container_proxy_mode,
        )
    if not trusted_host:
        return JSONResponse(
            {"status": "error", "detail": "Untrusted HTTP Host"},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    public_bootstrap = is_public_bootstrap_path(
        request.scope["path"], getattr(request.app.state, "face_dir", None),
    )
    origin = request.headers.get("origin", "").strip()
    if is_tauri_companion_origin(origin) and request.method != "OPTIONS":
        candidate = request.headers.get(COMPANION_TOKEN_HEADER, "").strip()
        if not is_valid_companion_token(candidate):
            logger.warning("Rejected unauthenticated Tauri request: %s", request.url.path)
            return JSONResponse(
                {"status": "error", "detail": "Invalid companion pairing token"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
    container_proxy_mode = False
    if direct_remote_client:
        container_proxy_mode = bool(getattr(cfg, "container_proxy_mode", False))
        if not container_proxy_mode and not cfg.lan_access:
            logger.warning("Rejected remote request while LAN access is disabled: %s", request.url.path)
            return JSONResponse(
                {"status": "error", "detail": "LAN access disabled"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        if cfg.lan_access and not getattr(cfg, "container_proxy_mode", False):
            expected = (getattr(cfg, "lan_pairing_token", "") or "").strip()
            query_pair = request.query_params.get("pair", "").strip()
            candidate = (
                request.headers.get("x-iris-lan-token", "").strip()
                or request.cookies.get(_LAN_PAIR_COOKIE, "").strip()
                or query_pair
            )
            paired = bool(expected and candidate and hmac.compare_digest(candidate, expected))
            if query_pair and paired and request.method in {"GET", "HEAD"}:
                clean_query = urllib.parse.urlencode([
                    (key, value)
                    for key, value in request.query_params.multi_items()
                    if key != "pair"
                ])
                response = RedirectResponse(
                    str(request.url.replace(query=clean_query)),
                    status_code=303,
                    headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
                )
                response.set_cookie(
                    _LAN_PAIR_COOKIE,
                    expected,
                    httponly=True,
                    secure=request.url.scheme == "https",
                    samesite="strict",
                )
                return response
            if not public_bootstrap and not paired:
                return JSONResponse(
                    {"status": "error", "detail": "LAN pairing required"},
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )
    # LAN pairing grants use of IRIS (Face, chat and WebRTC), not permission to
    # administer the host PC. Keep every REST mutation loopback-only; otherwise
    # a paired phone could restart Hermes, update the Git checkout or erase
    # memory. Container proxy mode is excluded because its non-loopback peer is
    # the trusted local reverse proxy bound to 127.0.0.1 by docker-compose.
    if (
        direct_remote_client
        and not container_proxy_mode
        and request.url.path.startswith("/api/")
        and (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            or request.url.path in _LOCAL_ONLY_READ_PATHS
        )
    ):
        logger.warning("Rejected remote administration request: %s", request.url.path)
        return JSONResponse(
            {"status": "error", "detail": "Local administration required"},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        request_host = request.url.hostname or ""
        rebinding = bool(
            origin
            and not is_tauri_companion_origin(origin)
            and is_loopback_host(peer_host)
            and not is_loopback_host(request_host)
        )
        if (not origin and peer_host and not is_loopback_host(peer_host)) or (
            origin
            and not (
                is_tauri_companion_origin(origin)
                or is_trusted_local_origin(
                    origin,
                    request.url.hostname,
                    request_scheme=request.url.scheme,
                    request_port=request.url.port,
                )
            )
        ) or rebinding:
            logger.warning("Rejected %s from untrusted origin: %s", request.url.path, origin)
            return JSONResponse(
                {"status": "error", "detail": "Untrusted browser origin"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
    response = await call_next(request)
    if not public_bootstrap or "pair" in request.query_params:
        response.headers["Cache-Control"] = "no-store"
    if "pair" in request.query_params:
        response.headers["Referrer-Policy"] = "no-referrer"
    return response

# Shared identity endpoints (same routes exposed by wizard/app.py so the
# side panel can POST to whichever backend is serving it).
app.include_router(identity_router)
app.include_router(async_agents_router)
app.state.notify_avatar_change = lambda: broadcast_to_face("avatar_config_changed", {})
app.include_router(hermes_wake_router)
app.include_router(companion_lifecycle_router)
app.include_router(model_selection_router(
    agent=lambda: getattr(app.state, "agent", None),
    broadcast=lambda event, payload: broadcast_to_face(event, payload),
))
app.include_router(automation_router(lambda: _automation_runtime, permission=lambda: _automation_permission()))
app.include_router(inbox_router(lambda: _inbox_service(), changed=lambda: _announce_inbox()))
app.include_router(vision_model_router(
    agent=lambda: getattr(app.state, "agent", None),
    broadcast=lambda event, payload: broadcast_to_face(event, payload),
))
app.include_router(notification_router(
    runtime=lambda: _vision_assistant,
    broadcast=lambda payload: _broadcast_vision_notifications(payload),
    vision_snapshot=lambda cfg: _vision_snapshot(cfg),
    calibrate=lambda action: _calibrate_vision(action),
))
# F10: Google connections, local only on every route (the Conexiones app's API).
app.include_router(create_google_router(lambda: _google_service))
app.include_router(create_calendar_preferences_router(lambda: _calendar_runtime))
app.include_router(create_calendar_router(lambda: _calendar_runtime))
app.include_router(create_mail_router(lambda: _google_service))
app.include_router(create_classification_router(lambda: _mail_classification))
app.include_router(create_meeting_router(lambda: _meeting_preparation))
app.include_router(create_desktop_widgets_router(get_iris_home, installed=lambda: installed_catalog(get_iris_home())))
def _store_app_name(app_id: str) -> str | None:
    """The name of an app installed from Iris Store, or None: only those may post notices."""
    from core.app_packages import open_store

    store = open_store(Path(get_iris_home()))
    row = store.get(app_id) if store is not None else None
    return str(row["manifest"]["name"]) if row else None


def _forget_installed_app(home: Path, app_id: str) -> bool:
    """F17 uninstall: clean the desktop, and the app's future notices never fire."""
    cleaned = forget_app(home, app_id)
    if _app_notices is not None:
        _app_notices.forget_app(app_id)
        _app_notices_changed()
    return cleaned


# F17: apps installed from a file (Iris Store); bundled IDs and icons come from the build's catalog.
app.include_router(create_app_packages_router(get_iris_home, bundled_ids=BUNDLED_APP_IDS, icon_names=ICON_NAMES,
                                              forget_app=_forget_installed_app, extensions=_app_extensions))
# Notices installed apps schedule; Core shows them when due, even with the app closed.
app.include_router(app_notices.app_notices_router(
    store=lambda: _app_notices, app_name=lambda app_id: _store_app_name(app_id),
    installed=lambda app_id, installation, work: _app_extensions.for_installation(app_id, installation, work),
    changed=lambda: _app_notices_changed(), require_local=require_local_peer))
app.include_router(create_theme_fonts_router(get_iris_home))
# F09.3/F09.4: Python services and operations of reviewed apps (none ships yet).
include_app_services(app, operations=_app_action_registry)


# --- WebSocket Endpoint ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket connection for the face layer."""
    origin = websocket.headers.get("origin", "").strip()
    accepted_protocol: str | None = None
    if websocket.client and not is_loopback_host(websocket.client.host):
        cfg = IRISConfig.load()
        if not getattr(cfg, "container_proxy_mode", False) and not cfg.lan_access:
            await websocket.close(code=1008, reason="LAN access disabled")
            return
        if cfg.lan_access and not getattr(cfg, "container_proxy_mode", False):
            expected = (getattr(cfg, "lan_pairing_token", "") or "").strip()
            candidate = (
                websocket.cookies.get(_LAN_PAIR_COOKIE, "").strip()
                or websocket.query_params.get("pair", "").strip()
            )
            if not expected or not candidate or not hmac.compare_digest(candidate, expected):
                await websocket.close(code=1008, reason="LAN pairing required")
                return
    if is_tauri_companion_origin(origin):
        offered_protocols = websocket.headers.get("sec-websocket-protocol", "")
        candidate = companion_token_from_websocket_protocol(offered_protocols)
        if not is_valid_companion_token(candidate):
            logger.warning("Rejected unauthenticated Tauri WebSocket")
            await websocket.close(code=1008, reason="Invalid companion pairing token")
            return
        if COMPANION_WS_PROTOCOL not in {
            item.strip() for item in offered_protocols.split(",")
        }:
            await websocket.close(code=1008, reason="Unsupported companion protocol")
            return
        accepted_protocol = COMPANION_WS_PROTOCOL
    elif origin and (
        (
            websocket.client
            and is_loopback_host(websocket.client.host)
            and not is_loopback_host(websocket.url.hostname)
        )
        or not is_trusted_local_origin(
            origin,
            websocket.url.hostname,
            request_scheme=websocket.url.scheme,
            request_port=websocket.url.port,
        )
    ):
        logger.warning("Rejected WebSocket from untrusted browser origin: %s", origin)
        await websocket.close(code=1008, reason="Untrusted browser origin")
        return
    elif not origin and websocket.client and not is_loopback_host(websocket.client.host):
        logger.warning("Rejected non-local WebSocket without browser origin")
        await websocket.close(code=1008, reason="Remote clients require an origin")
        return
    await websocket.accept(subprotocol=accepted_protocol)
    _connect_surface(websocket)
    client_host = websocket.client.host if websocket.client else "unknown"
    logger.info("🔗 Face layer connected from %s (%d clients)", client_host, len(_connected_clients))

    try:
        # Send initial hello
        await websocket.send_json({
            "type": "hello",
            "payload": {
                "version": IRIS_VERSION,
                "name": "IRIS",
                "status": "ready",
                "hermes_wake_active": bool(
                    getattr(getattr(app.state, "hermes_wake", None), "listening", False)
                ),
            },
        })

        # Replay the last N chat turns so a page reload doesn't wipe
        # the conversation. The voice path persists its turns via
        # `memory.remember`; we now also persist chat turns from
        # `_handle_user_text`. Both end up in the same SQLite store
        # under session_id="default" (or whatever the agent uses),
        # which is the single source of truth for chat history.
        agent = getattr(app.state, "agent", None)
        if agent is not None:
            try:
                history = await agent.memory.recall(
                    session_id=agent._session_id,
                    limit=30,
                )
                # Filter out anything that would feel like clutter on
                # reload — system prompts, empty messages, debug entries.
                clean = [
                    {
                        "role":      h["role"],
                        "content":   h["content"],
                        "emotion":   h.get("emotion") or "neutral",
                        "timestamp": h.get("timestamp", 0),
                    }
                    for h in history
                    if h.get("content") and h.get("role") in ("user", "IRIS")
                ]
                if clean:
                    await websocket.send_json({
                        "type": "chat_history",
                        "payload": {"messages": clean},
                    })
            except Exception as e:
                logger.warning("Could not replay chat history: %s", e)

        # Listen for messages from the face layer
        while True:
            event = await websocket.receive()
            if event.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(event.get("code", 1000))
            audio = event.get("bytes")
            if audio is not None:
                # Native Hermes owns microphone capture. Reject binary frames
                # so an old/stale browser cannot silently re-enable the removed
                # continuous-upload path.
                await websocket.send_json({
                    "type": "hermes_wake_state",
                    "payload": {
                        "active": False,
                        "state": "blocked",
                        "error": "El audio del wake word se procesa localmente por Hermes Agent.",
                    },
                })
                continue
            data = event.get("text")
            if data is None:
                continue
            try:
                message = json.loads(data)
                await _handle_face_message(websocket, message)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from face layer: %s", data[:100])

    except WebSocketDisconnect:
        released_camera = _disconnect_surface(websocket)
        if released_camera:
            await broadcast_to_face("camera_lease_state", _surface_registry.camera_snapshot())
        logger.info("🔌 Face layer disconnected (%d clients)", len(_connected_clients))
    except Exception as e:
        released_camera = _disconnect_surface(websocket)
        if released_camera:
            await broadcast_to_face("camera_lease_state", _surface_registry.camera_snapshot())
        logger.error("WebSocket error: %s", e)


def _surface_payload_for_peer(payload: dict[str, Any], peer_host: str) -> dict[str, Any]:
    """Prevent a LAN surface from claiming host-only application controls.

    ``surface_ready`` and later ``surface_state`` messages share the same
    capability shape.  Applying the restriction at both boundaries prevents a
    remote page from registering safely and then elevating itself afterwards.
    """
    if not peer_host or is_loopback_host(peer_host):
        return payload
    safe_payload = dict(payload)
    raw_capabilities = payload.get("capabilities")
    safe_capabilities = (
        dict(raw_capabilities) if isinstance(raw_capabilities, dict) else {}
    )
    safe_capabilities["captures_camera"] = False
    safe_capabilities["controls_local_player"] = False
    safe_capabilities["presents_vision"] = False
    safe_capabilities["presents_inbox"] = False
    safe_capabilities["speaks_inbox"] = False
    safe_capabilities["hosts_iris_apps"] = False  # F09.4: confirms and runs this PC's app actions
    safe_capabilities["tunes_vision"] = False
    safe_capabilities["takes_screen_snapshots"] = False
    safe_payload["capabilities"] = safe_capabilities
    return safe_payload


async def _handle_face_message(websocket: WebSocket, message: Any) -> None:
    """Handle messages received from the face layer."""
    if not isinstance(message, dict):
        logger.warning("Ignored non-object WebSocket message: %s", type(message).__name__)
        await websocket.send_json({
            "type": "protocol_error",
            "payload": {"message": "El mensaje WebSocket debe ser un objeto JSON."},
        })
        return

    msg_type = message.get("type", "")
    if not isinstance(msg_type, str):
        await websocket.send_json({
            "type": "protocol_error",
            "payload": {"message": "El tipo de mensaje WebSocket no es válido."},
        })
        return

    raw_payload = message.get("payload")
    if raw_payload is not None and not isinstance(raw_payload, dict):
        await websocket.send_json({
            "type": "protocol_error",
            "payload": {"message": "El payload WebSocket debe ser un objeto JSON."},
        })
        return
    payload: dict[str, Any] = raw_payload or {}

    peer = getattr(websocket, "client", None)
    peer_host = getattr(peer, "host", "") if peer is not None else ""
    if msg_type in {"set_personality", "launch_wizard"} and peer_host and not is_loopback_host(peer_host):
        logger.warning("Rejected remote WebSocket administration message: %s", msg_type)
        await websocket.send_json({
            "type": "protocol_error",
            "payload": {"message": "Esta acción administrativa solo está disponible en el equipo principal."},
        })
        return

    if msg_type == "ping":
        await websocket.send_json({"type": "pong"})
    elif msg_type == "tts_playback_result":
        _tts_playback.answer(websocket, payload)
    elif msg_type == "surface_ready":
        # Music metadata and playback stay on the host. A LAN page may render
        # IRIS, chat or receive TTS, but cannot own the local player.
        ready_payload = _surface_payload_for_peer(payload, peer_host)
        record = _surface_registry.register_ready(websocket, ready_payload)
        logger.info(
            "Surface ready: role=%s id=%s audio=%s camera=%s",
            record.role,
            record.surface_id,
            record.supports("plays_tts"),
            record.supports("captures_camera"),
        )
        await websocket.send_json({
            "type": "surface_registered",
            "payload": {
                **(_surface_registry.snapshot_for(websocket) or {}),
                "audio_owner_surface_id": _surface_registry.audio_owner_surface_id(),
                "camera": _surface_registry.camera_snapshot(),
            },
        })
        if _is_local_client(websocket) and _background_runtime is not None:
            await websocket.send_json({"type": "background_tasks", "payload": await _background_tasks_snapshot()})
    elif msg_type == "face_ready":
        # Compatibility path for cached clients from before the surface
        # protocol. They retain every capability the old Face had.
        _surface_registry.register_ready(websocket, {
            "role": "legacy_face",
            "visible": True,
        })
        logger.info("Face layer reports ready.")
    elif msg_type == "surface_state":
        record = _surface_registry.update_state(
            websocket,
            _surface_payload_for_peer(payload, peer_host),
        )
        _app_notices_changed()  # a window that became visible may show a waiting notice
        await websocket.send_json({
            "type": "surface_state_ack",
            "payload": {
                **(_surface_registry.snapshot_for(websocket) or {}),
                "audio_owner_surface_id": _surface_registry.audio_owner_surface_id(),
            },
        })
        logger.debug("Surface state updated: %s visible=%s", record.surface_id, record.visible)
    elif msg_type == "surface_activity":
        _surface_registry.mark_interaction(websocket)
        _note_iris_interaction(websocket, offer_notice=True)
    elif msg_type == "calendar_reminder_receipt":
        record = _surface_registry.get(websocket)
        calendar = _calendar_runtime
        accepted = False
        if (not peer_host or not is_loopback_host(peer_host) or record is None or calendar is None
                or record.role != "face" or not record.supports("presents_inbox")
                or payload.get("surface_id") != record.surface_id or type(payload.get("shown")) is not bool):
            await websocket.send_json({"type": "protocol_error", "payload": {
                "message": "El recibo del calendario requiere la superficie local que recibió el aviso."}})
            return
        try:
            accepted = await calendar._thread(calendar.presentation_receipt,
                payload.get("notice_id"), token=payload.get("token"),
                surface_id=record.surface_id, shown=payload["shown"])
        except (ValueError, CalendarStoreError):
            pass  # Invalid/stale receipts never alter the inbox or echo private values.
        await websocket.send_json({"type": "calendar_reminder_ack", "payload": {"accepted": accepted}})
    elif msg_type == "app_notice_receipt":
        record = _surface_registry.get(websocket)
        store = _app_notices
        if (not peer_host or not is_loopback_host(peer_host) or record is None or store is None
                or record.role != "face" or not record.supports("presents_inbox")
                or payload.get("surface_id") != record.surface_id or type(payload.get("shown")) is not bool):
            await websocket.send_json({"type": "protocol_error", "payload": {
                "message": "El recibo del aviso requiere la ventana local que lo recibió."}})
            return
        try:
            accepted = await asyncio.to_thread(store.receipt, str(payload.get("notice_id") or ""),
                                               token=str(payload.get("token") or ""), surface_id=record.surface_id,
                                               shown=payload["shown"])
        except (sqlite3.Error, OSError):
            accepted = False  # the notice stays unread in the Bandeja
        if accepted and payload["shown"]:
            await _announce_inbox()
        await websocket.send_json({"type": "app_notice_ack", "payload": {"accepted": accepted}})
    elif msg_type == "inbox_presentation_receipt":
        record = _surface_registry.get(websocket)
        runtime = _automation_runtime
        if (not peer_host or not is_loopback_host(peer_host) or record is None
                or record.role not in {"face", "desktop_companion"} or runtime is None):
            await websocket.send_json({
                "type": "protocol_error",
                "payload": {"message": "El recibo del aviso requiere una superficie local registrada."},
            })
            return
        store = runtime.store()
        accepted = False
        if store is not None:
            try:
                # The surface is the one this socket registered, never what the payload says.
                accepted = await asyncio.to_thread(InboxPresenter(store=lambda: store).receipt, payload,
                                                   surface_id=record.surface_id)
            except Exception as exc:  # the notice stays as it was; the results stay in the inbox
                logger.warning("Inbox notice receipt not recorded: %s", exc)
        await websocket.send_json({"type": "inbox_presentation_ack",
                                   "payload": {"id": str(payload.get("id") or "")[:32], "accepted": accepted}})
        if accepted and payload.get("shown") is True:
            _spawn_inbox_task(_inbox_voice(runtime).offer(websocket, record.surface_id, payload))
    elif msg_type == "inbox_voice_receipt":
        record = _surface_registry.get(websocket)
        service = getattr(_automation_runtime, "voice_service", None)
        accepted = False
        if (_is_local_client(websocket) and record is not None and service is not None
                and record.role in {"face", "desktop_companion"}):
            accepted = await service.receipt(websocket, record.surface_id, payload)
        await websocket.send_json({"type":"inbox_voice_ack", "payload":{"accepted":accepted}})
    elif msg_type == "camera_lease_request":
        if peer_host and not is_loopback_host(peer_host):
            await websocket.send_json({
                "type": "camera_lease_result",
                "payload": {
                    "granted": False,
                    "reason": "local_only",
                    "permission": {"capability": "camera.capture", "verdict": "deny"},
                    **_surface_registry.camera_snapshot(),
                },
            })
            return
        permission = await asyncio.to_thread(
            _capability_store.evaluate,
            "camera.capture",
            trigger="manual" if payload.get("confirmed") is True else "automatic",
            confirmed=payload.get("confirmed") is True,
        )
        if permission["verdict"] != "allow":
            await websocket.send_json({
                "type": "camera_lease_result",
                "payload": {
                    "granted": False,
                    "reason": (
                        "permission_required"
                        if permission["verdict"] == "ask"
                        else "permission_denied"
                    ),
                    "permission": permission,
                    **_surface_registry.camera_snapshot(),
                },
            })
            return
        purpose = payload.get("purpose") if isinstance(payload.get("purpose"), str) else ""
        previous_camera_owner = _surface_registry.camera_owner()
        decision = _surface_registry.request_camera(websocket, purpose=purpose)
        if decision.granted and previous_camera_owner is not websocket:
            # A new lease starts with no perception context. Never carry a
            # previous surface's last face reading into the next chat turn.
            app.state.user_emotion = None
            _vision_assistant.reset_observation(
                "surface_changed" if previous_camera_owner is not None else "camera_released"
            )
        if decision.previous_owner is not None:
            try:
                await decision.previous_owner.send_json({
                    "type": "camera_lease_revoked",
                    "payload": {"reason": "another-surface-requested-camera"},
                })
            except Exception:
                _disconnect_surface(decision.previous_owner)
        await websocket.send_json({
            "type": "camera_lease_result",
            "payload": {
                "granted": decision.granted,
                "reason": decision.reason,
                "permission": permission,
                **_surface_registry.camera_snapshot(),
            },
        })
        await broadcast_to_face("camera_lease_state", _surface_registry.camera_snapshot())
    elif msg_type == "camera_lease_release":
        released = _surface_registry.release_camera(websocket)
        await websocket.send_json({
            "type": "camera_lease_result",
            "payload": {
                "granted": False,
                "released": released,
                **_surface_registry.camera_snapshot(),
            },
        })
        if released:
            app.state.user_emotion = None
            _vision_assistant.reset_observation()
            await broadcast_to_face("camera_lease_state", _surface_registry.camera_snapshot())
    elif msg_type == "set_personality":
        # Compatibility with cached face clients from before identity saves
        # moved to the REST endpoint. Apply the safe subset in memory; the
        # accompanying /api/update-identity call remains the persistence path.
        from core.identity_api import _apply_identity_to_running_agent
        from core.personality import normalize_personality_template
        cfg = IRISConfig.load()
        requested_name = str(message.get("name") or "").strip()
        if requested_name:
            cfg.assistant_name = requested_name[:48]
        cfg.personality_template = normalize_personality_template(
            str(message.get("personality") or cfg.personality_template)
        )
        _apply_identity_to_running_agent(getattr(app.state, "agent", None), cfg)
    elif msg_type == "launch_wizard":
        _surface_registry.mark_interaction(websocket)
        logger.info("Lanzando Setup Wizard a petición del panel...")
        import subprocess
        import sys

        # Lanza el wizard de forma desacoplada
        subprocess.Popen([sys.executable, "-m", "wizard.app"], start_new_session=True)
        # Informa al frontend para que abra pestaña si quiere, aunque wizard.app ya lo hace
        await websocket.send_json({"type": "wizard_launched", "url": "http://127.0.0.1:8000/ui/"})
    elif msg_type == "user_text":
        # Typed message from the side-panel chat. Without this handler
        # every typed message used to fall through to the `else` branch
        # below and silently disappear — the user saw their own bubble
        # but IRIS never replied because the bridge was never invoked.
        # Optional `images` array (Fase 2.C): list of data-URL strings
        # forwarded to the LLM as a multimodal payload.
        raw_text = payload.get("text", "")
        raw_images = payload.get("images")
        if not isinstance(raw_text, str) or not _valid_face_images(raw_images):
            await websocket.send_json({
                "type": "protocol_error",
                "payload": {
                    "message": (
                        "El texto o las imágenes del mensaje no son válidos; "
                        "usa hasta 8 imágenes locales y 14 MB codificados."
                    )
                },
            })
            return
        _surface_registry.mark_interaction(websocket)
        _note_iris_interaction(websocket)
        await _stop_inbox_voice()
        # F03b: turns run beside this loop, which keeps reading: a photo answer,
        # its cancellation or the next message must never wait for a turn that
        # is waiting for a photo (Codex F02/F03 review, R06). The conversation
        # lock still runs turns one at a time, in arrival order.
        _spawn_turn(_handle_user_text(
            websocket,
            raw_text,
            images=raw_images or None,
            reasoning_effort=(
                payload.get("reasoning_effort")
                if isinstance(payload.get("reasoning_effort"), str)
                else None
            ),
            camera_question=payload.get("camera_question") is True,
        ))
    elif msg_type == "app_message":
        # «Avisar a Iris» from an installed app, after the person's click in this PC's window.
        # Each message is answered, so the app hears «sent» only when Iris took it.
        record = _surface_registry.get(websocket)
        request_id = app_messages.request_id_of(payload)
        try:
            if not peer_host or not is_loopback_host(peer_host) or record is None:
                raise app_messages.MessageRefused("«Avisar a Iris» solo funciona en este equipo.")
            app_id, text, data = app_messages.checked_message(payload)
            name = await asyncio.to_thread(_store_app_name, app_id)
            if not name:
                raise app_messages.MessageRefused("Solo las apps instaladas desde Iris Store pueden avisar a Iris.")
        except app_messages.MessageRefused as exc:
            await websocket.send_json(app_messages.acknowledgement(request_id, str(exc)) if request_id
                                      else {"type": "protocol_error", "payload": {"message": str(exc)}})
            return
        await websocket.send_json(app_messages.acknowledgement(request_id))
        _surface_registry.mark_interaction(websocket)
        _note_iris_interaction(websocket)
        await _stop_inbox_voice()
        _spawn_turn(_handle_user_text(websocket, app_messages.turn_text(name, text, data),
                                      memory_text=app_messages.memory_text(name, text)))
    elif msg_type == "hermes_command":
        # Slash commands have their own dispatcher. Never let an unsupported
        # command fall through to the LLM as ordinary prose: the catalogue
        # marks terminal-only commands explicitly and this handler enforces
        # the same allow-list on the server.
        _surface_registry.mark_interaction(websocket)
        await _handle_hermes_command(
            websocket,
            payload.get("line", ""),
            reasoning_effort=(
                payload.get("reasoning_effort")
                if isinstance(payload.get("reasoning_effort"), str)
                else None
            ),
        )
    elif msg_type == "vision_snapshot_result":
        if not isinstance(payload, dict) or not _snapshot_broker.answer(websocket, payload):
            logger.warning("Ignored an unrequested or late camera photo answer.")
    elif msg_type == "screen_snapshot_result":
        if not isinstance(payload, dict) or not _screen_snapshot_broker.answer(websocket, payload):
            logger.warning("Ignored an unrequested or late screen capture answer.")
    elif msg_type in {"background_task_cancel", "background_task_release"}:
        # The indicator's «Cancelar», or «Liberar» for a start nobody can confirm (the user
        # accepts it may still run), from this PC only. «Detener» and new messages never get here.
        task_id = str(payload.get("id") or "") if isinstance(payload, dict) else ""
        if (_is_local_client(websocket) and _background_runtime is not None
                and len(task_id) == 32 and all(ch in "0123456789abcdef" for ch in task_id)):
            action = (_background_runtime.cancel if msg_type == "background_task_cancel"
                      else _background_runtime.release)
            work = asyncio.get_running_loop().create_task(action(task_id))
            _background_deliveries.add(work)
            work.add_done_callback(_background_deliveries.discard)
    elif msg_type in {"iris_player_result", "iris_app_result"}:
        if isinstance(payload, dict):
            _app_actions.answer(websocket, msg_type, payload)
    elif msg_type == "barge_in":
        # A connected client can still interrupt an in-flight reply explicitly.
        _surface_registry.mark_interaction(websocket)
        agent = getattr(app.state, "agent", None)
        if agent is not None:
            try:
                await agent.interrupt(reason="barge_in")
            except Exception:
                logger.exception("barge_in handler failed")
    elif msg_type == "user_emotion":
        # Camera-derived emotion snapshot from the face layer's
        # EmotionClassifier. Stored on app.state so `agent._think()`
        # can fold it into the LLM system prompt as context. Accept it
        # only from the current camera owner so a stale/hidden surface
        # cannot keep influencing the next turn after losing its lease.
        if peer_host and not is_loopback_host(peer_host):
            await websocket.send_json({
                "type": "protocol_error",
                "payload": {
                    "message": (
                        "El contexto perceptivo solo se acepta en el equipo principal."
                    )
                },
            })
            return
        if _surface_registry.camera_owner() is not websocket:
            await websocket.send_json({
                "type": "protocol_error",
                "payload": {
                    "message": "La superficie no posee la concesión activa de cámara.",
                },
            })
            return
        try:
            normalized_emotion = normalize_user_emotion(payload)
        except VisionSignalError as exc:
            await websocket.send_json({
                "type": "protocol_error",
                "payload": {"message": str(exc)},
            })
            return
        context_permission = await asyncio.to_thread(
            _capability_store.evaluate,
            "vision.context",
            trigger="automatic",
            confirmed=False,
        )
        if context_permission["verdict"] != "allow":
            app.state.user_emotion = None
            await websocket.send_json({
                "type": "user_emotion_ack",
                "payload": {
                    "accepted": False,
                    "reason": "permission_required"
                    if context_permission["verdict"] == "ask"
                    else "permission_denied",
                },
            })
            return
        _stash_user_emotion(normalized_emotion)
        await websocket.send_json({
            "type": "user_emotion_ack",
            "payload": {"accepted": True},
        })
    elif msg_type == "vision_activity":
        record = _surface_registry.get(websocket)
        if (
            not peer_host or not is_loopback_host(peer_host)
            or record is None or record.role not in {"face", "desktop_companion"}
        ):
            await websocket.send_json({
                "type": "protocol_error",
                "payload": {"message": "La actividad visual requiere una superficie local registrada."},
            })
            return
        cfg = IRISConfig.load()
        if not cfg.vision_assistant_enabled or cfg.vision_assistant_paused_until > time.time():
            await websocket.send_json({
                "type": "vision_activity_ack", "payload": {"accepted": False},
            })
            return
        try:
            _vision_assistant.update_activity(payload, surface_id=record.surface_id)
        except VisionSignalError as exc:
            await websocket.send_json({
                "type": "protocol_error", "payload": {"message": str(exc)},
            })
            return
        await websocket.send_json({
            "type": "vision_activity_ack", "payload": {"accepted": True},
        })
    elif msg_type == "vision_sensor_diagnostics":
        cfg = IRISConfig.load()
        if (not peer_host or not is_loopback_host(peer_host)
            or _surface_registry.camera_owner() is not websocket
            or not cfg.vision_assistant_enabled
            or cfg.vision_assistant_paused_until > time.time()
            or cfg.vision_assistant_log_retention_days <= 0):
            return
        try:
            sensor = VisionSensorDiagnostics.model_validate(payload)
        except ValidationError:
            return
        record = _surface_registry.snapshot_for(websocket) or {}
        _vision_assistant.report_vision_sensor(sensor, surface_role=record.get("role", "unknown"))
    elif msg_type == "vision_signal":
        # Vision Assistant accepts only derived presence metadata from the
        # current, loopback camera owner. Raw media fields are rejected by the
        # state machine and never become an upload path to Core or Hermes.
        if peer_host and not is_loopback_host(peer_host):
            await websocket.send_json({
                "type": "protocol_error",
                "payload": {
                    "message": (
                        "Vision Assistant procesa señales solo en el equipo principal."
                    )
                },
            })
            return
        if _surface_registry.camera_owner() is not websocket:
            await websocket.send_json({
                "type": "protocol_error",
                "payload": {"message": "La superficie no posee la concesión activa de cámara."},
            })
            return
        cfg = IRISConfig.load()
        record = _surface_registry.snapshot_for(websocket) or {}
        try:
            result = await asyncio.to_thread(
                _vision_assistant.ingest,
                payload,
                surface_id=str(record.get("surface_id") or "surface"),
                surface_role=str(record.get("role") or "unknown"),
                enabled=cfg.vision_assistant_enabled,
                paused_until=cfg.vision_assistant_paused_until,
                cooldown_minutes=cfg.vision_assistant_cooldown_minutes,
                retention_days=cfg.vision_assistant_log_retention_days,
                policy_settings=cfg.vision_assistant_policy,
                activity_surfaces=_vision_activity_surfaces(),
                backend_busy=_vision_backend_busy(),
            )
        except VisionSignalError as exc:
            await websocket.send_json({
                "type": "protocol_error",
                "payload": {"message": str(exc)},
            })
            return
        await websocket.send_json({"type": "vision_signal_ack", "payload": result})
        event = result.get("event")
        if isinstance(event, dict):
            await broadcast_to_face("vision_assistant_event", event)
        if result.get("notification"):
            await _broadcast_notification_summary(cfg)
            await _present_vision_notification(result["notification"])
    elif msg_type == "rtc.init":
        # Remote client (phone) or host (PC) signals WebRTC intent.
        # We relay to the other side so both browser tabs can
        # coordinate the P2P handshake over the existing WS.
        client_role = payload.get("role", "unknown")
        logger.info("[rtc] rtc.init from %s (role=%s)", websocket.client.host, client_role)
        # Relay to all OTHER connected clients so the host/remote
        # on the opposite device can coordinate.
        relay_payload = {"payload": payload, "from": str(websocket.client.host)}
        await _relay_to_others(websocket, "rtc.init", relay_payload)
    elif msg_type == "rtc.offer":
        logger.info("[rtc] relaying rtc.offer from %s", websocket.client.host)
        await _relay_to_others(websocket, "rtc.offer", payload)
    elif msg_type == "rtc.answer":
        logger.info("[rtc] relaying rtc.answer from %s", websocket.client.host)
        await _relay_to_others(websocket, "rtc.answer", payload)
    elif msg_type == "rtc.ice-candidate":
        await _relay_to_others(websocket, "rtc.ice-candidate", payload)
    elif msg_type == "rtc.stream_ready":
        await _relay_to_others(websocket, "rtc.stream_ready", payload)
    elif msg_type == "rtc.stop":
        logger.info("[rtc] rtc.stop from %s", websocket.client.host)
        await _relay_to_others(websocket, "rtc.stop", {})
    else:
        logger.debug("Unknown face message type: %s", msg_type)


@app.get("/api/network-info")
async def network_info(request: Request) -> JSONResponse:
    """Return the host's LAN-reachable URLs so the side panel can show
    a "connect from your phone" QR code.

    Strategy for IP discovery:
      1. Open a UDP socket to a non-routable address (8.8.8.8:80) and
         read the source IP the OS picked. This works without actually
         sending a packet — the OS just tells us which interface would
         have been used for that route. Far more reliable than
         `gethostbyname(gethostname())` which on Windows returns
         127.0.0.1 about half the time, and beats parsing `ipconfig`.
      2. If that fails (no network at all, only loopback), we return
         127.0.0.1 with `lan_reachable=False` so the UI can grey out
         the QR code instead of advertising a useless URL.

    Bind status is best-effort: we look at the configured
    `websocket_host` plus a probe of Hermes' .env for
    `API_SERVER_HOST`. We do NOT inspect the actual socket — that would
    require platform-specific code (Windows vs POSIX) for marginal
    benefit. The configured value is what the user sees in the wizard
    and what survives restarts.
    """
    import socket as _sock

    cfg = IRISConfig.load()
    ws_port = cfg.websocket_port
    ws_host = cfg.websocket_host

    # Pick a real LAN IP (not 127.0.0.1 / ::1).
    lan_ip = "127.0.0.1"
    lan_reachable = False
    try:
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))   # no packets are sent for UDP
            lan_ip = s.getsockname()[0]
            lan_reachable = lan_ip != "127.0.0.1"
        finally:
            s.close()
    except OSError:
        pass

    # If the websocket itself is bound to loopback, even the local
    # ip won't work for other devices. Surface that in the response.
    bound_for_lan = ws_host in ("0.0.0.0", "::") or ws_host == lan_ip

    # Read Hermes' api-server bind from its env file — needed for
    # full multi-device LLM access. Best-effort; on missing file we
    # just say "unknown" instead of guessing.
    hermes_bind = "unknown"
    try:
        from wizard.hermes_config import read_api_server_host
        hermes_bind = read_api_server_host() or "127.0.0.1"
    except Exception:
        pass

    # core/agent.py sets ``app.state.tls_enabled = True`` when uvicorn
    # was launched with auto-generated cert paths (see core/tls.py). The
    # face/avatar UI URL we hand to the QR code must match the scheme
    # the server is actually listening on — otherwise the phone hits
    # the wrong protocol and gets ERR_SSL_PROTOCOL_ERROR or
    # ERR_EMPTY_RESPONSE.
    tls_enabled = bool(getattr(app.state, "tls_enabled", False))
    scheme = "https" if tls_enabled else "http"

    # Only the local owner receives a one-time pairing URL. The remote
    # browser exchanges it for an HttpOnly cookie on its first GET.
    local_request = bool(request.client and is_loopback_host(request.client.host))
    pair_query = ""
    if local_request and cfg.lan_access and cfg.lan_pairing_token:
        pair_query = f"?pair={urllib.parse.quote(cfg.lan_pairing_token, safe='')}"

    return JSONResponse({
        "lan_ip":           lan_ip,
        "lan_reachable":    lan_reachable,
        "websocket_port":   ws_port,
        "websocket_host":   ws_host,
        "lan_access":       bool(cfg.lan_access),
        "bound_for_lan":    bound_for_lan,
        "hermes_bind":      hermes_bind,
        "tls_enabled":      tls_enabled,
        # Fully-qualified URLs for the UI to display + QR-encode.
        "avatar_url":       f"{scheme}://{lan_ip}:{ws_port}/face/{pair_query}",
        # Setup can execute installers and write credentials, so it remains
        # loopback-only even when the finished Face is shared over the LAN.
        "wizard_url":       "http://127.0.0.1:8000/ui/",
    })


@app.post("/api/lan-access")
async def update_lan_access(enabled: bool = Body(..., embed=True)) -> JSONResponse:
    """Persist IRIS and Hermes LAN binds without pretending to hot-rebind.

    Uvicorn and Hermes already own listening sockets at this point, so a
    restart is required whenever their persisted bind changes. If Hermes is
    not installed yet we save only the IRIS preference and report that fact.
    """
    target_host = "0.0.0.0" if enabled else "127.0.0.1"
    cfg = IRISConfig.load()
    token_changed = bool(
        enabled and (not cfg.lan_access or not getattr(cfg, "lan_pairing_token", ""))
    )
    runtime_changed = bool(
        cfg.lan_access != enabled
        or cfg.websocket_host != target_host
        or token_changed
    )

    hermes_changed = False
    hermes_available = False
    hermes_detail = "Hermes no está instalado; se guardó únicamente el acceso de IRIS."
    previous_hermes_host = ""
    try:
        from wizard.hermes_config import (
            has_hermes_workspace,
            read_api_server_host,
            write_api_server_host,
        )

        hermes_available = has_hermes_workspace()
        if hermes_available:
            previous_hermes_host = read_api_server_host()
            hermes_changed = write_api_server_host(target_host)
            hermes_detail = (
                "Hermes quedará disponible en la red local tras reiniciarlo."
                if enabled else
                "Hermes volverá a aceptar conexiones solo desde este equipo tras reiniciarlo."
            )
    except Exception as exc:
        logger.warning("Could not update Hermes LAN bind: %s", exc)
        return JSONResponse(
            {"status": "error", "detail": f"No se pudo configurar Hermes: {exc}"},
            status_code=500,
        )

    if runtime_changed:
        cfg.lan_access = enabled
        cfg.websocket_host = target_host
        if token_changed:
            cfg.lan_pairing_token = secrets.token_urlsafe(32)
        try:
            cfg.save()
        except Exception as exc:
            # Keep the two persisted configurations aligned if IRIS fails to
            # save after Hermes was already updated.
            if hermes_available and hermes_changed:
                try:
                    write_api_server_host(previous_hermes_host or "127.0.0.1")
                except Exception:
                    logger.exception("Could not roll back Hermes LAN bind")
            logger.warning("Could not save IRIS LAN preference: %s", exc)
            return JSONResponse(
                {"status": "error", "detail": f"No se pudo guardar la configuración de IRIS: {exc}"},
                status_code=500,
            )

    return JSONResponse({
        "status": "ok",
        "enabled": enabled,
        "websocket_host": target_host,
        "hermes_bind": target_host if hermes_available else "unavailable",
        "hermes_detail": hermes_detail,
        "needs_runtime_restart": runtime_changed,
        "needs_gateway_restart": hermes_changed,
    })


def _stash_user_emotion(payload: dict[str, Any]) -> None:
    """Store the latest user emotion snapshot on the FastAPI app state.

    The snapshot is read on every chat turn by `agent._think()` and
    composed into the LLM context — see `core/agent.py`. Only one
    snapshot is kept (most-recent-wins) since by the time the LLM
    runs, anything older than ~1s is stale anyway.
    """
    emotion = normalize_user_emotion(payload)
    import time as _time
    app.state.user_emotion = {
        **emotion,
        "timestamp":  _time.time(),
    }


async def _handle_hermes_command(
    websocket: WebSocket,
    line: str,
    *,
    reasoning_effort: str | None = None,
) -> None:
    """Dispatch an IRIS-safe slash command without blocking the event loop."""
    clean_line = str(line or "").strip()
    if not clean_line.startswith("/") or len(clean_line) > 4096:
        await websocket.send_json({
            "type": "hermes_command_result",
            "payload": {"ok": False, "text": "El comando no es válido."},
        })
        return
    try:
        from core.hermes_commands import execute_hermes_command

        result = await asyncio.to_thread(execute_hermes_command, clean_line)
    except Exception as exc:
        logger.exception("Hermes slash-command dispatcher failed")
        result = {"ok": False, "kind": "message", "text": f"No pude ejecutar el comando: {exc}"}

    if result.get("kind") == "agent_prompt" and result.get("ok"):
        await _handle_user_text(
            websocket,
            str(result.get("prompt") or ""),
            reasoning_effort=reasoning_effort,
            memory_text=str(result.get("memory_text") or clean_line),
        )
        return
    await websocket.send_json({
        "type": "hermes_command_result",
        "payload": {
            "ok": bool(result.get("ok")),
            "text": str(result.get("text") or "El comando no devolvió información."),
            "command": str(result.get("command") or ""),
        },
    })


async def _handle_user_text(
    websocket: WebSocket | None,
    text: str,
    *,
    images: list[str] | None = None,
    reasoning_effort: str | None = None,
    memory_text: str | None = None,
    camera_question: bool = False,
) -> None:
    # A new turn ends a photo an earlier turn is still waiting for, so that
    # turn answers at once and releases the conversation, and an earlier turn
    # that has not asked yet will not ask (R06).
    global _turn_epoch
    await _stop_inbox_voice()
    _turn_epoch += 1
    epoch = _turn_epoch
    # Surfaces go by this number and not by the clock: the end of a stopped turn, its late tool
    # events and the next message can cross on the way. It ends however the turn ends.
    turn = current_turn.set(epoch)
    try:
        await broadcast_to_face("turn_started", {"turn": epoch})
        _visual_gates.close(reason="superseded")  # the earlier turn's code no longer serves
        _launch_offers.close(reason="superseded")  # nor its offer to start a background task
        await _cancel_pending_snapshots("superseded")
        agent = getattr(app.state, "agent", None)
        conversation_lock = getattr(agent, "_conversation_lock", None)
        if conversation_lock is None:
            await _handle_user_text_unlocked(
                websocket, text, images=images,
                reasoning_effort=reasoning_effort, memory_text=memory_text,
                camera_question=camera_question, turn_epoch=epoch,
            )
            return
        async with conversation_lock:
            await _handle_user_text_unlocked(
                websocket, text, images=images,
                reasoning_effort=reasoning_effort, memory_text=memory_text,
                camera_question=camera_question, turn_epoch=epoch,
            )
    finally:
        current_turn.reset(turn)
        await asyncio.shield(broadcast_to_face("turn_ended", {"turn": epoch}))


def _is_local_client(client: Any) -> bool:
    return is_loopback_host(str(getattr(getattr(client, "client", None), "host", "")))


def _note_iris_interaction(websocket: Any, *, offer_notice: bool = False) -> None:
    """F04: a user interaction on a local Iris surface ends Iris' idle episode.

    F07: a pointer interaction (never a page load, a reconnection or a chat
    message, which starts a conversation) may bring the return notice to that
    same surface.
    """
    if _automation_runtime is not None and _is_local_client(websocket):
        _automation_runtime.interaction()
        if offer_notice:
            _spawn_inbox_task(_offer_inbox_notice(websocket))


def _spawn_inbox_task(coroutine: Any) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coroutine)
    except RuntimeError:
        coroutine.close()
        return
    _inbox_tasks.add(task)
    task.add_done_callback(_inbox_tasks.discard)


def _inbox_service() -> InboxService:
    """Built on the event loop: opening the store there lets its reconciliation start (F06)."""
    runtime = _automation_runtime
    store = runtime.store() if runtime is not None else None

    def vision() -> dict[str, Any]:
        return _vision_assistant.notification_snapshot(
            retention_days=IRISConfig.load().vision_assistant_log_retention_days)

    return InboxService(store=lambda: store, store_problem=lambda: runtime.problem if runtime else "",
                        vision=vision, calendar=lambda: _calendar_runtime, mail=lambda: _mail_classification,
                        meeting=lambda: _meeting_preparation,
                        background=lambda: _background_runtime.store if _background_runtime else None,
                        apps=lambda: _app_notices)


def _voice_eligible(websocket: Any) -> bool:
    from core.windows_idle import windows_notifications_allowed

    runtime = _automation_runtime
    record = _surface_registry.get(websocket)
    if (runtime is None or record is None or not _is_local_client(websocket)
            or record.role not in {"face", "desktop_companion"} or not record.visible or not record.focused
            or not record.supports("speaks_inbox") or not record.supports("plays_tts")
            or _surface_registry.audio_owner() is not websocket or _vision_backend_busy()
            or getattr(getattr(app.state, "hermes_wake", None), "listening", False)):
        return False
    try:
        prefs = runtime.presence_preferences()
        return prefs.return_voice and not prefs.focus and windows_notifications_allowed()
    except Exception:
        return False


async def _synthesize_inbox_voice(text: str):
    agent = getattr(app.state, "agent", None)
    if agent is None or _vision_backend_busy():
        raise RuntimeError("Voice unavailable")
    async with agent._speak_lock:
        conversation = getattr(agent, "_conversation_lock", None)
        task = getattr(agent, "_current_turn_task", None)
        if (conversation is not None and conversation.locked()) or (task is not None and not task.done()):
            raise RuntimeError("Conversation in progress")
        return await agent.tts.speak(text, emotion="neutral")


def _inbox_voice(runtime):
    from core.inbox_voice import InboxVoiceService

    if getattr(runtime, "voice_service", None) is None:
        runtime.voice_service = InboxVoiceService(store=lambda:runtime.store(), eligible=_voice_eligible,
                                                synthesize=_synthesize_inbox_voice)
    return runtime.voice_service


async def _stop_inbox_voice():
    service = getattr(_automation_runtime, "voice_service", None)
    if service is not None:
        await service.stop()


async def _offer_inbox_notice(websocket: Any) -> None:
    """F07: the return notice for the surface the user just used, if it can show one now.

    Only a local, visible Face or Companion that announced ``presents_inbox``;
    never while a conversation is in progress. Nothing is spoken, sent to Hermes
    or asked of the camera. A send that may have arrived is never retried
    through another surface: its results stay in the inbox.
    """
    global _inbox_offer_at
    runtime = _automation_runtime
    record = _surface_registry.get(websocket)
    if (runtime is None or record is None or not record.visible or not _is_local_client(websocket)
            or record.role not in {"face", "desktop_companion"} or not record.supports("presents_inbox")
            or _vision_backend_busy()):
        return
    now = time.monotonic()
    if now - _inbox_offer_at < _INBOX_OFFER_EVERY_S:
        return
    _inbox_offer_at = now
    store = runtime.store()
    if store is None:
        return
    presenter = InboxPresenter(store=lambda: store)
    try:
        payload = await asyncio.to_thread(presenter.offer, record.surface_id)
    except Exception as exc:
        logger.warning("Inbox notice not prepared: %s", exc)
        return
    if payload is None:
        return
    current = _surface_registry.get(websocket)
    if current is not record or not record.visible or _vision_backend_busy():
        # Nothing went out: the surface left or a conversation started meanwhile.
        await asyncio.to_thread(presenter.failed, payload, certain=True)
        return
    try:
        await asyncio.wait_for(websocket.send_json({"type": "inbox_presentation", "payload": payload}), timeout=2)
    except Exception:
        # It may have arrived: never resent, here or elsewhere; the results stay in the inbox.
        await asyncio.to_thread(presenter.failed, payload, certain=False)
        return
    await asyncio.to_thread(presenter.sent, payload)


def _queue_inbox_announcement(_kind: str = "") -> None:
    """On the event loop: tell the local surfaces the inbox changed, coalescing bursts."""
    global _inbox_announce_pending
    if _kind in {"preferences", "delivery", "rule"}:
        _spawn_inbox_task(_stop_inbox_voice())
    if _inbox_announce_pending:
        return
    _inbox_announce_pending = True
    _spawn_inbox_task(_announce_inbox(delay=0.25))


async def _announce_inbox(delay: float = 0.0) -> None:
    global _inbox_announce_pending
    try:
        if delay:
            await asyncio.sleep(delay)
        _inbox_announce_pending = False
        service = _inbox_service()
        unread = await asyncio.to_thread(service.unread)
        await broadcast_to_face("inbox_changed", {"unread": unread})
    except Exception as exc:
        _inbox_announce_pending = False
        logger.debug("Inbox announcement skipped: %s", exc)


def _local_iris_surface_open() -> bool:
    return any(_is_local_client(record.client) and record.role in {"face", "legacy_face", "desktop_companion"}
               for record in _surface_registry.records())


def _automation_permission() -> str:
    return _capability_store.evaluate("automations.run", trigger="automatic", confirmed=False)["verdict"]


async def run_automations() -> None:
    """F05/F06 in this Core: observe Iris activity, evaluate enabled rules, run ready ones."""
    global _automation_runtime, _calendar_runtime, _mail_classification, _meeting_preparation
    try:
        runtime = AutomationRuntime(
            home=None,
            transport=HermesRunsTransport(_hermes_api_request, hermes_profile_route,
                                          lambda: (_hermes_api_base_url(), _hermes_api_token())),
            permission=_automation_permission,
            profile_problem=lambda profile: hermes_profile_route(profile)[2],
            local_surface_open=_local_iris_surface_open,
            after_tick=_tick_calendar,
        )
    except Exception as exc:  # an invalid profile folder: automations stay off, Iris keeps working
        logger.warning("Automations are unavailable: %s", exc)
        return
    loop = asyncio.get_running_loop()
    runtime.on_inbox_change = lambda kind: loop.call_soon_threadsafe(_queue_inbox_announcement, kind)
    _automation_runtime = runtime
    calendar = CalendarRuntime(runtime.home, google=lambda: _google_service,
                               changed=lambda: loop.call_soon_threadsafe(_queue_inbox_announcement, "calendar"))
    _calendar_runtime = calendar
    try:
        classification = MailClassificationRuntime(runtime.home, google=lambda: _google_service,
            changed=lambda: loop.call_soon_threadsafe(_queue_inbox_announcement, "gmail"))
    except Exception:
        classification = None
        logger.warning("Mail classification store unavailable; other Core services continue")
    _mail_classification = classification
    try:
        preparation = MeetingPreparationRuntime(runtime.home, google=lambda: _google_service,
            changed=lambda: loop.call_soon_threadsafe(_queue_inbox_announcement, "meeting"), calendar=lambda: calendar)
    except Exception:
        preparation = None
        logger.warning("Meeting preparation unavailable; deterministic Calendar reminders continue")
    _meeting_preparation = preparation
    try:
        await runtime.run()
    finally:
        await runtime.close()
        await calendar.close()
        if classification is not None:
            await classification.close()
        if preparation is not None:
            await preparation.close()
        _automation_runtime = None
        _calendar_runtime = None
        _mail_classification = None
        _meeting_preparation = None


async def _tick_calendar() -> None:
    """A bounded cached pass inside Core's existing scheduler, with no separate UI timer."""
    if _mail_classification is not None:
        try:
            await _mail_classification.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Mail classification unavailable; no mail content logged")
    runtime = _automation_runtime
    if runtime is not None and getattr(runtime, "take_desktop_return", lambda: False)():
        # Native input ends the Windows episode even outside Iris. Elect one
        # visible local presenter; minimized pages are never force-opened.
        owner = _surface_registry.capability_owner("presents_inbox", allowed=lambda client:
            _is_local_client(client) and _surface_registry.get(client).visible
            and _surface_registry.get(client).role in {"face", "desktop_companion"})
        if owner is not None:
            await _offer_inbox_notice(owner)
    calendar = _calendar_runtime
    if calendar is None:
        return
    try:
        notices = await calendar.tick()
        if not notices or _vision_backend_busy():
            return
        # One local visible Face, one visual intent. Remote paired surfaces never
        # receive the account/event content; even here the message contains only an id.
        surface = next((record for record in _surface_registry.records()
                        if record.role == "face" and record.visible and record.supports("presents_inbox")
                        and _is_local_client(record.client)), None)
        if surface is None:
            return
        runtime = _automation_runtime
        store = runtime.store() if runtime else None
        if store is not None:
            prefs = await asyncio.to_thread(store.inbox_preferences)
            if prefs.quiet_until > time.time():
                return
        notice = notices[0]  # never burst a group of reminders on wake
        claimed = await calendar._thread(calendar.claim_notice, notice.id, notice.revision,
                                         surface_id=surface.surface_id)
        if claimed is None:
            return
        current = _surface_registry.get(surface.client)
        if (current is not surface or not surface.visible or surface.role != "face"
                or not surface.supports("presents_inbox") or not _is_local_client(surface.client)
                or _vision_backend_busy()):
            return  # Keep the durable intent uncertain; never replay it on another surface.
        # Persist before sending. An ambiguous send is not retried automatically;
        # the reminder remains unread in the inbox and Calendar still works.
        await asyncio.wait_for(surface.client.send_json({"type": "calendar_reminder", "payload": {
            "notice_id": claimed.id, "surface_id": surface.surface_id,
            "token": claimed.presentation_token}}), timeout=2)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Calendar reminder pass unavailable; the next shared tick will check again")


async def run_google_connections() -> None:
    """F10 in this Core: the connections service, dormant until the person connects or reads.
    Its periodic loop only reads services the person enabled; closing collects its workers."""
    global _google_service
    from core.google_worker_client import create_google_service

    try:
        service = create_google_service()
    except Exception as exc:  # an invalid profile folder: connections stay off, Iris keeps working
        logger.warning("Google connections are unavailable: %s", exc)
        return
    _google_service = service
    try:
        await service.run_periodic()
    finally:
        await service.close()
        _google_service = None


def _spawn_turn(turn: Any) -> asyncio.Task:
    task = asyncio.create_task(turn)
    _background_turns.add(task)

    def finished(done: asyncio.Task) -> None:
        _background_turns.discard(done)
        if not done.cancelled() and done.exception() is not None:
            logger.error("Chat turn failed", exc_info=done.exception())

    task.add_done_callback(finished)
    return task


async def _cancel_pending_snapshots(reason: str, *, source: str | None = None) -> None:
    """End every photo request still waiting (a new turn, a permission change)."""
    if source != "screen":
        for request in _snapshot_broker.close_all(reason):
            await _notify_snapshot_cancel(request, reason)
    if source != "camera":
        for request in _screen_snapshot_broker.close_all(reason):
            await _notify_screen_snapshot_cancel(request, reason)


def _snapshot_policy() -> tuple[str, str]:
    """``vision.snapshot``'s mode and when it last changed: its revision."""
    row = next(item for item in _capability_store.snapshot()["capabilities"] if item["id"] == "vision.snapshot")
    return row["mode"], row.get("updated_at") or ""


def _snapshot_surface(origin: Any) -> tuple[Any | None, str]:
    """The one surface asked for the photo: the camera owner, else the one that asked.

    Never a LAN or hidden surface, and never a lease taken from another window:
    if the owner cannot take photos for the chat, the user is told so.
    """
    def usable(client: Any) -> bool:
        record = _surface_registry.get(client)
        return bool(record and record.visible and record.supports("takes_snapshots") and _is_local_client(client))

    owner = _surface_registry.camera_owner()
    if owner is not None:
        return (owner, "") if usable(owner) else (None, "camera_elsewhere")
    if origin is not None and usable(origin):
        return origin, ""
    best = _surface_registry.capability_owner("takes_snapshots")
    return (best, "") if best is not None and usable(best) else (None, "no_surface")


async def _snapshot_permission(by_iris: bool) -> dict[str, Any] | None:
    """The permission that governs a photo now, or None when it is blocked.

    Read again if Core applied a change meanwhile: a verdict from before a
    revocation never takes a photo (Codex V02).
    """
    for _ in range(3):
        revision = _policy_revision
        permission = await asyncio.to_thread(
            _capability_store.evaluate, "vision.snapshot", trigger="manual", confirmed=False)
        if by_iris and permission["verdict"] != "deny":
            permission = await asyncio.to_thread(
                _capability_store.evaluate, "vision.ai_request", trigger="manual", confirmed=False)
        if revision == _policy_revision:
            return None if permission["verdict"] == "deny" else permission
    return None


async def _take_turn_snapshot(origin: Any, agent: Any, question: str, *, turn_epoch: int | None = None,
                              requested_by: str = "user",
                              still_current: Callable[[], bool] | None = None) -> dict[str, Any]:
    """F03b: one fresh photo for this turn, or the reason there is none (``code``).

    ``origin`` is the local socket that asked, or None for local voice; a LAN
    chat never gets here (R03). The permission, the surface and the camera
    lease are checked again when the answer arrives (R04, R05).

    A photo Iris asked for (``requested_by="iris"``, core.visual_requests) also
    needs «Fotos que Iris decide pedir». Its «Permitir» stands in for the
    remembered destination, and it never offers "no volver a preguntar".
    ``still_current`` is its turn's own check: a turn that ended or changed
    conversation while the photo was prepared asks for nothing (Codex V01).
    """
    by_iris = requested_by == "iris"
    policy_at_start = await asyncio.to_thread(_snapshot_policy)
    if await _snapshot_permission(by_iris) is None:
        return {"code": "permission_denied"}
    bridge = agent.bridge
    if not bridge.is_available:
        return {"code": "no_vision_model"}
    route = bridge.route_context()
    home = vision_model.profile_home(route["profile"])
    try:
        planned = await vision_model.planned_route(home)
    except vision_model.VisionModelError:
        return {"code": "no_vision_model"}
    if not planned.get("available"):
        return {"code": "no_vision_model"}
    # The destination lookup can wait up to a minute: decide from the
    # permission as it is now, then ask with no await in between.
    permission = await _snapshot_permission(by_iris)
    if permission is None:
        return {"code": "permission_denied"}
    destination = {"provider": planned["provider"], "model": planned["model"]}
    target, problem = _snapshot_surface(origin)
    if target is None:
        return {"code": problem}
    owner_at_start = _surface_registry.camera_owner()
    lease_at_start = _surface_registry.camera_generation()
    remembered = permission["verdict"] == "allow" and (by_iris or _snapshot_consent.covers(destination))
    # A camera that is off is only turned on after a click on that surface.
    needs_confirmation = not remembered or owner_at_start is None
    timeout = vision_snapshot.CONFIRM_TIMEOUT_S if needs_confirmation else vision_snapshot.CAPTURE_TIMEOUT_S
    if turn_epoch is not None and turn_epoch != _turn_epoch:
        return {"code": "superseded"}  # another message arrived while this turn waited
    if still_current is not None and not still_current():
        return {"code": "superseded"}  # Iris' turn ended or its conversation changed meanwhile
    request = _snapshot_broker.open(target, route, timeout=timeout)
    try:
        await target.send_json({"type": "vision_snapshot_request", "payload": {
            "request_id": request.request_id,
            "question": question[:200],
            "destination": destination,
            "needs_confirmation": needs_confirmation,
            "can_remember": not remembered and not by_iris,
            "camera_active": owner_at_start is target,
            "timeout_s": timeout,
            **({"requested_by": "iris"} if by_iris else {}),
        }})
        answer = await asyncio.wait_for(request.future, timeout)
    except asyncio.TimeoutError:
        await _cancel_snapshot_request(request, "timeout")
        return {"code": "timeout"}
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            # Stop cancelled this turn: the surface drops its photo.
            await asyncio.shield(_cancel_snapshot_request(request, "cancelled"))
            raise
        # The request itself was ended: a new turn, a permission change or a
        # surface that left. The surface was already told.
        return {"code": {"superseded": "superseded", "permission_changed": "permission_denied"}.get(
            request.reason, "surface_changed")}
    except Exception:
        await _cancel_snapshot_request(request, "error")
        return {"code": "no_surface"}
    status = str(answer.get("status") or "")
    if status != "ok":
        return {"code": {"cancelled": "cancelled", "camera_denied": "camera_denied",
                         "unavailable": "camera_unavailable"}.get(status, "error")}
    try:
        meta = vision_snapshot.validate_image(answer.get("image"))
    except vision_snapshot.SnapshotError as exc:
        return {"code": exc.code}
    if bridge.route_context()["token"] != route["token"]:
        return {"code": "stale"}
    # R05: the surface is still the one asked, local, visible and able; no
    # other surface had the camera meanwhile (its own open-and-release is fine).
    record = _surface_registry.get(target)
    owner_now = _surface_registry.camera_owner()
    if (
        record is None or not record.visible or not record.supports("takes_snapshots")
        or not _is_local_client(target)
        or not _surface_registry.camera_granted_only_to(target, lease_at_start)
        or owner_now not in (None, target)
    ):
        return {"code": "surface_changed"}

    async def authorized() -> bool:
        """R04: the permission still allows this photo now, as it was taken."""
        now = await asyncio.to_thread(
            _capability_store.evaluate, "vision.snapshot", trigger="manual", confirmed=False)
        if now["verdict"] == "deny":
            return False
        if by_iris:
            now = await asyncio.to_thread(
                _capability_store.evaluate, "vision.ai_request", trigger="manual", confirmed=False)
            # Taken without a click under «Permitir»: it must still be allowed.
            return now["verdict"] != "deny" and (needs_confirmation or now["verdict"] == "allow")
        # Taken without a click under a remembered destination: it must still be remembered.
        return needs_confirmation or (now["verdict"] == "allow" and _snapshot_consent.covers(destination))

    if not await authorized():
        return {"code": "permission_denied"}
    if answer.get("remember") is True and needs_confirmation and not by_iris:
        # "No volver a preguntar con este destino", only if the permission did
        # not change meanwhile: an older answer never undoes a revocation. The
        # comparison, the write and the remembered destination share the
        # store's interprocess lock (Q01), so no change can slip in between.
        await asyncio.to_thread(
            _capability_store.set_mode_if_unchanged, "vision.snapshot", "allow",
            expected=policy_at_start, on_commit=lambda: _snapshot_consent.remember(destination))
    return {"image": answer["image"], "destination": destination, "meta": meta, "authorized": authorized,
            "asked": needs_confirmation}


async def _cancel_snapshot_request(request: Any, reason: str) -> None:
    _snapshot_broker.close(request.request_id, reason)
    await _notify_snapshot_cancel(request, reason)


def _screen_surface(origin: Any) -> Any | None:
    """The local, visible surface that would show the screen picker for this turn."""
    target = origin if origin is not None else _surface_registry.capability_owner("takes_screen_snapshots")
    record = _surface_registry.get(target) if target is not None else None
    if record and record.visible and record.supports("takes_screen_snapshots") and _is_local_client(target):
        return target
    return None


async def _offer_visual_requests(origin: Any, agent: Any, text: str,
                                 turn_epoch: int | None) -> visual_requests.TurnGate | None:
    """This local turn's one-time offer to look, or None when nothing could be asked."""
    sources = []
    for source, ids in visual_requests.PERMISSIONS.items():
        verdicts = [(await asyncio.to_thread(_capability_store.evaluate, cid, trigger="manual",
                                             confirmed=False))["verdict"] for cid in ids]
        if "deny" in verdicts:
            continue
        reachable = (_snapshot_surface(origin)[0] if source == "camera" else _screen_surface(origin)) is not None
        if reachable:
            sources.append(source)
    bridge = agent.bridge
    if not sources or not bridge.is_available:
        return None
    route = bridge.route_context()
    # Only a vision route already known to be missing withholds the offer:
    # asking the vision worker here would slow down every ordinary turn.
    known = vision_model.cached_route(vision_model.profile_home(route["profile"]))
    if known is not None and not known.get("available"):
        return None
    return _visual_gates.open(origin=origin, text=text, epoch=turn_epoch, route_token=route["token"],
                              sources=tuple(sources))


async def _close_visual_gate(gate: visual_requests.TurnGate) -> None:
    _visual_gates.close(gate)
    if gate.used:
        # A dialog or picker still open for this turn ends with it.
        await _cancel_pending_snapshots("cancelled")


async def _run_visual_request(token: str, source: str) -> dict[str, Any]:
    """One photo or capture Iris asked for, analysed for the turn that offered it."""
    gate, problem = _visual_gates.claim(token, source)
    if gate is None:
        return visual_requests.result(problem)
    agent = getattr(app.state, "agent", None)
    if agent is None:
        return visual_requests.result("unavailable")

    def current() -> bool:
        """The offering turn is still the one running, in its own conversation (Codex V01)."""
        return (gate.is_open and (gate.epoch is None or gate.epoch == _turn_epoch)
                and agent.bridge.route_context()["token"] == gate.route_token)

    if not current():
        return visual_requests.result("stale")  # nothing asked of the user
    if source == "camera":
        outcome = await _take_turn_snapshot(gate.origin, agent, gate.question, turn_epoch=gate.epoch,
                                            requested_by="iris", still_current=current)
        outcomes = vision_snapshot.OUTCOMES
        prompt = vision_snapshot.SNAPSHOT_PROMPT
    else:
        outcome = await screen_snapshot.take_snapshot(
            gate.origin, agent, broker=_screen_snapshot_broker, store=_capability_store,
            registry=_surface_registry, is_local=_is_local_client, turn_is_current=current,
            notify_cancel=_notify_screen_snapshot_cancel, requested_by="iris", question=gate.question,
            policy_revision=lambda: _policy_revision)
        outcomes = screen_snapshot.OUTCOMES
        prompt = screen_snapshot.SNAPSHOT_PROMPT
    if "image" not in outcome:
        code = outcome.get("code", "error")
        return visual_requests.result(code, message=outcomes.get(code, outcomes["error"]))
    route = agent.bridge.route_context()
    if not current() or route["token"] != gate.route_token or not await outcome["authorized"]():
        return visual_requests.result("stale", message=outcomes["stale"])
    home = vision_model.profile_home(route["profile"])
    work = asyncio.create_task(vision_model.analyze(
        home, [outcome["image"]], prompt.format(question=gate.analysis), expected=outcome["destination"]))
    gate.work = work
    try:
        analysis = await work
    except asyncio.CancelledError:
        current_task = asyncio.current_task()
        if current_task is not None and current_task.cancelling():
            work.cancel()
            raise
        return visual_requests.result("superseded", message=outcomes["superseded"])  # the turn ended
    except vision_model.VisionModelError as exc:
        vision_model.record_analysis(route["profile"], {"ok": False, "code": exc.code, **exc.route})
        return visual_requests.result("analysis_failed", message=f"No pude analizar la imagen. {exc.message}")
    finally:
        gate.work = None
    vision_model.record_analysis(route["profile"], {"ok": True, **analysis["route"]})
    if not current() or agent.bridge.route_context()["token"] != gate.route_token:
        return visual_requests.result("stale", message=outcomes["stale"])
    skipped = not (outcome.get("asked", True) if source == "camera" else outcome.get("reviewed", True))
    await broadcast_to_face("visual_request_notice", {
        "source": source, "text": visual_requests.notice_text(source, outcome["destination"], skipped_step=skipped)})
    return visual_requests.described(source, analysis["analyses"][0], analysis["route"])


# ── Background tasks (core.background_tasks) ─────────────────────────────


def _local_iris_surface_visible() -> bool:
    return any(_is_local_client(record.client) and record.visible
               and record.role in {"face", "legacy_face", "desktop_companion"}
               for record in _surface_registry.records())


def _iris_skill_script(name: str) -> str:
    """A script of the installed iris-os skill, as the turn contexts tell the model to run it."""
    return (installed_iris_skill_dir() / "scripts" / name).as_posix()


def _app_builder_context(agent: Any, *, local_origin: bool, app_message: bool) -> str:
    if not local_origin or app_message or getattr(agent, "app_builder_ready", False) is not True:
        return ""
    profile = get_iris_home()
    argv = [
        runtime_policy.get_runtime().iris_python.as_posix(), "-I", "-B",
        Path(__file__).with_name("app_builder.py").resolve().as_posix(),
        "--profile", profile.as_posix(),
    ]
    return (
        "Constructor de apps de IRIS OS disponible en este turno local. "
        "Si el usuario pide crear o actualizar una app, carga la skill iris-app-builder. "
        "También puedes cargarla por iniciativa propia si falta una herramienta reutilizable "
        "para la petición del usuario. Prepara un borrador para revisar en Iris Store. "
        f"Raíz de borradores: {(profile / 'apps' / 'drafts').as_posix()}. "
        f"Comando local (argumentos JSON, cita las rutas según el shell): {json.dumps(argv)}. "
        "Añade guides para localizar las guías, new para una app de interfaz o new --sdk2 "
        "para partir de una app con backend y acciones. Usa pack <id-del-borrador> para validarla y empaquetarla."
    )


async def _background_history_note(agent: Any) -> tuple[str, list[str]]:
    """Endings this conversation's Hermes history has not seen: a cancel, a failure, a Bandeja result."""
    runtime = _background_runtime
    if runtime is None or not agent.bridge.is_available:
        return "", []
    route = agent.bridge.route_context()
    tasks = await asyncio.to_thread(runtime.store.unnoted, profile=route["profile"] or "",
                                    session_id=route["session_id"])
    return (background_tasks.history_note(tasks), [task["id"] for task in tasks]) if tasks else ("", [])


async def _offer_background_task(agent: Any, text: str, turn_epoch: int | None
                                 ) -> tuple[background_tasks.LaunchOffer | None, str]:
    """This local turn's offer to start a background task, or what is already running."""
    runtime = _background_runtime
    if runtime is None or not agent.bridge.is_available:
        return None, ""
    active = await asyncio.to_thread(runtime.store.active)
    if active:
        return None, background_tasks.busy_context(active[0], time.time())
    route = agent.bridge.route_context()
    previous = await asyncio.to_thread(runtime.store.last_finished, profile=route["profile"] or "",
                                       session_id=route["session_id"])
    offer = _launch_offers.open(epoch=turn_epoch, route_token=route["token"], profile=route["profile"] or "",
                                session_id=route["session_id"], request=text)
    return offer, background_tasks.launch_context(offer, _iris_skill_script("background_task.py"), previous)


async def _launch_background_task(token: str, title: str, task: str) -> dict[str, Any]:
    """The skill's launch: only with the open turn's code, in that turn's conversation."""
    offer, problem = _launch_offers.claim(token)
    if offer is None:
        return background_tasks.result(problem)
    runtime = _background_runtime
    agent = getattr(app.state, "agent", None)
    if runtime is None or agent is None:
        return background_tasks.result("unavailable")
    if ((offer.epoch is not None and offer.epoch != _turn_epoch)
            or agent.bridge.route_context()["token"] != offer.route_token):
        return background_tasks.result("invalid_turn")  # the user moved on before it was posted
    return await runtime.launch(offer, title=title, task=task)


def _background_tasks_changed() -> None:
    """On the event loop: refresh the «Trabajando en» indicators."""
    task = asyncio.get_running_loop().create_task(_broadcast_background_tasks())
    _background_deliveries.add(task)
    task.add_done_callback(_background_deliveries.discard)


async def _background_tasks_snapshot() -> dict[str, Any]:
    runtime = _background_runtime
    return {"tasks": await asyncio.to_thread(runtime.snapshot) if runtime is not None else []}


async def _broadcast_background_tasks() -> None:
    await broadcast_to_face("background_tasks", await _background_tasks_snapshot())


def _schedule_background_delivery(task_id: str) -> None:
    task = asyncio.get_running_loop().create_task(_deliver_background_task(task_id))
    _background_deliveries.add(task)
    task.add_done_callback(_background_deliveries.discard)


async def _keep_background_in_inbox(runtime: background_tasks.BackgroundTaskRuntime, task_id: str) -> None:
    await asyncio.to_thread(runtime.store.settle_delivery, task_id, "inbox")
    _queue_inbox_announcement("background")


async def _deliver_background_task(task_id: str) -> None:
    """Present a finished task in its own conversation when Iris is free, else keep it in the Bandeja.

    The conversation lock is the gap: a reply in progress, with its speech,
    finishes first. ``claim_delivery`` hands the task out once, so a repeated
    call, or two Cores sharing the store, never present it twice.
    """
    runtime = _background_runtime
    agent = getattr(app.state, "agent", None)
    lock = getattr(agent, "_conversation_lock", None)
    if runtime is None:
        return
    if lock is None:
        if await asyncio.to_thread(runtime.store.claim_delivery, task_id) is not None:
            await _keep_background_in_inbox(runtime, task_id)
        return
    async with lock:
        row = await asyncio.to_thread(runtime.store.claim_delivery, task_id)
        if row is None:
            return
        # Bound to the route seen now (Codex B01): Wake can switch conversation outside the lock.
        token = agent.bridge.route_context()["token"]

        def still_here() -> bool:
            route = agent.bridge.route_context()
            return (route["token"] == token and (route["profile"] or "") == row["profile"]
                    and route["session_id"] == row["session_id"] and _local_iris_surface_visible()
                    and agent.bridge.is_available)

        if not still_here():
            # Another conversation or profile, or nobody to hear it: never told elsewhere.
            await _keep_background_in_inbox(runtime, task_id)
            return
        try:
            await _present_background_task(agent, row, still_here)
        except asyncio.CancelledError:
            # «Detener» during the presentation: the result stays readable in the Bandeja.
            await asyncio.shield(_keep_background_in_inbox(runtime, task_id))
            raise
        except _NotPresented:
            await _keep_background_in_inbox(runtime, task_id)
        except Exception:
            logger.exception("Background task %s could not be presented", task_id)
            await _keep_background_in_inbox(runtime, task_id)
        else:
            await asyncio.to_thread(runtime.store.settle_delivery, task_id, "delivered")


class _NotPresented(Exception):
    """The presentation no longer belongs here, or said nothing: the Bandeja keeps the result."""


async def _present_background_task(agent: Any, row: dict[str, Any], still_here: Callable[[], bool]) -> None:
    """Iris tells the result with her own voice; a failure or a cancellation in a fixed sentence.

    ``still_here`` is checked again after the model answers and right before
    anything is kept or said (Codex B01). An empty answer is not a
    presentation (Codex B04).
    """
    if row["state"] == "cancelled":
        await broadcast_to_face("background_task_notice", {"text": background_tasks.outcome_text(row)})
        return
    async with agent._turn_lock:
        agent._current_turn_task = asyncio.current_task()  # «Detener» stops this presentation too
    try:
        if row["state"] == "completed" and row["result"].strip():
            # No turn context: the presenting turn gets no code to delegate or to look again.
            response = await agent._think(background_tasks.presentation_message(row))
            if _background_runtime is not None:  # the history now holds the result, even if unheard
                await asyncio.to_thread(_background_runtime.store.mark_noted, [row["id"]])
        else:
            response = {"texto_hablado": background_tasks.outcome_text(row, spoken=True), "emocion": "neutral",
                        "animacion_virtual": "idle"}
        spoken = (response.get("texto_hablado") or "").strip()
        if not spoken or not still_here():
            raise _NotPresented()
        # Checked again after the audio is generated, before text or sound leaves (Codex B01).
        if await agent._respond(response, still_relevant=still_here) is False:
            raise _NotPresented()
        try:
            # Filed under the task's own conversation, never the one active now.
            await agent.memory.remember("IRIS", spoken, emotion=response.get("emocion", "neutral"),
                                        session_id=row["session_id"],
                                        metadata={"animacion": response.get("animacion_virtual", ""),
                                                  "background_task": row["id"]})
        except Exception as exc:
            logger.warning("Could not persist a background task presentation: %s", exc)
    finally:
        async with agent._turn_lock:
            agent._current_turn_task = None


async def run_background_tasks() -> None:
    """Recover, follow and deliver this Core's background tasks (started by core.agent)."""
    global _background_runtime
    try:
        store = background_tasks.BackgroundTaskStore(Path(get_iris_home()) / "background_tasks.db")
    except Exception as exc:  # an unreadable folder: background tasks stay off, Iris keeps working
        logger.warning("Background tasks are unavailable: %s", exc)
        return
    loop = asyncio.get_running_loop()
    runtime = background_tasks.BackgroundTaskRuntime(
        store,
        HermesRunsTransport(_hermes_api_request, hermes_profile_route,
                            lambda: (_hermes_api_base_url(), _hermes_api_token())),
        changed=lambda: loop.call_soon_threadsafe(_background_tasks_changed),
        finished=lambda task_id: loop.call_soon_threadsafe(_schedule_background_delivery, task_id),
    )
    _background_runtime = runtime
    await runtime.run()


APP_NOTICE_GAP_S = 8.0  # between two cards, so one never replaces another at once


def _app_notices_changed() -> None:
    """A notice was posted, moved or cancelled, or a window appeared: look again now."""
    if _app_notices_wake is not None:
        _app_notices_wake.set()


async def _present_app_notice(store: app_notices.AppNoticeStore) -> bool:
    """Offer the first due notice to one visible local Iris window, as Calendar reminders are."""
    rows = await asyncio.to_thread(store.presentable)
    if not rows or _vision_backend_busy():
        return False
    surface = next((record for record in _surface_registry.records()
                    if record.role == "face" and record.visible and record.supports("presents_inbox")
                    and _is_local_client(record.client)), None)
    if surface is None:
        return False
    runtime = _automation_runtime
    automations = runtime.store() if runtime else None
    if automations is not None:
        prefs = await asyncio.to_thread(automations.inbox_preferences)
        if prefs.quiet_until > time.time():
            return False  # «No molestar» in the Bandeja: it waits there
    claimed = await asyncio.to_thread(store.claim, rows[0]["id"], surface.surface_id)
    if claimed is None:
        return False
    current = _surface_registry.get(surface.client)
    if (current is not surface or not surface.visible or not surface.supports("presents_inbox")
            or not _is_local_client(surface.client)):
        return False  # claimed once: never replayed on another window; it stays in the Bandeja
    # Only an id and a token travel; the window reads the text from this PC's route.
    await asyncio.wait_for(surface.client.send_json({"type": "app_notice", "payload": {
        "notice_id": claimed["id"], "surface_id": surface.surface_id,
        "token": claimed["presentation_token"]}}), timeout=2)
    return True


async def run_app_notices() -> None:
    """Show app notices when they fall due, also with the app closed (started by core.agent)."""
    global _app_notices, _app_notices_wake, _app_notices_due
    try:
        store = app_notices.AppNoticeStore(Path(get_iris_home()) / "app_notices.db")
    except Exception as exc:  # an unreadable folder: app notices stay off, Iris keeps working
        logger.warning("App notices are unavailable: %s", exc)
        return
    _app_notices, _app_notices_wake = store, asyncio.Event()
    while True:
        _app_notices_wake.clear()
        shown = False
        try:
            due = len(await asyncio.to_thread(store.inbox_rows))
            if due != _app_notices_due:
                _app_notices_due = due
                await _announce_inbox()
            shown = await _present_app_notice(store)
            upcoming = await asyncio.to_thread(store.next_due)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("App notice pass unavailable; the next one will check again")
            upcoming = None
        if shown:
            await asyncio.sleep(APP_NOTICE_GAP_S)
            continue
        delay = 30.0 if upcoming is None else min(30.0, max(0.05, upcoming - time.time()))
        try:
            await asyncio.wait_for(_app_notices_wake.wait(), timeout=delay)
        except TimeoutError:
            pass


async def _notify_snapshot_cancel(request: Any, reason: str) -> None:
    try:
        await asyncio.wait_for(request.surface.send_json({
            "type": "vision_snapshot_cancel",
            "payload": {"request_id": request.request_id, "reason": reason},
        }), 2.0)
    except Exception:
        pass


async def _notify_screen_snapshot_cancel(request: Any, reason: str) -> None:
    try:
        await asyncio.wait_for(request.surface.send_json({
            "type": "screen_snapshot_cancel",
            "payload": {"request_id": request.request_id, "reason": reason},
        }), 2.0)
    except Exception:
        pass


def _snapshot_reply(code: str, *, source: str = "camera") -> dict[str, Any]:
    outcomes = screen_snapshot.OUTCOMES if source == "screen" else vision_snapshot.OUTCOMES
    return {
        "texto_hablado": outcomes.get(code, outcomes["error"]),
        "emocion": "neutral",
        "animacion_virtual": "pensar",
    }


async def _handle_user_text_unlocked(
    websocket: WebSocket | None,
    text: str,
    *,
    images: list[str] | None = None,
    reasoning_effort: str | None = None,
    memory_text: str | None = None,
    camera_question: bool = False,
    turn_epoch: int | None = None,
) -> None:
    """Forward a typed user message through IRIS's full think→respond
    pipeline so chat input behaves identically to voice input.

    Why we delegate to `agent._respond` instead of emitting
    `speech_start` ourselves:
      • The voice path already emits *seven* events per turn —
        `emotion`, `animation`, `scene_action`, `speech_start`,
        `subtitle`, `lip_sync`, `speech_end` — plus actually plays
        the synthesised audio through the server's speakers and
        runs TTS. Re-implementing all that in this handler would
        diverge the two code paths and miss things like the
        `animacion_virtual` field in IRIS's JSON response (the bug
        the user reported: chat bubble appeared but the avatar
        didn't speak or animate).
      • By calling `_respond` we get TTS + animation + scene action
        + lip-sync for free, and the only difference vs voice is
        the input source (typed text vs transcribed STT).

    Persistence: we also call `memory.remember()` for both the user
    turn and IRIS's turn. The voice loop does this in `_run_loop`
    around its STT call — chat input bypasses that loop, so it's
    on us to record both sides. Without this, reloading the page
    forgets the conversation entirely.
    """
    text = (text or "").strip()
    # Allow image-only turns (e.g. user drops an image without typing
    # — "what is this?" implied). Drop only when BOTH text and images
    # are empty, which means the message is purely accidental noise.
    if not text and not images:
        return
    if not text and images:
        # Sensible default — gives the LLM something concrete to react
        # to without the user having to type anything.
        text = "¿Qué ves en esta imagen?"

    # Hard cap on text length — the chat composer can inline whole
    # text files as fenced blocks (~256 KB each, up to several at a
    # time), and an unbounded prose payload here would blow the LLM
    # context window and burn tokens. 512 KB ≈ 130k tokens, already
    # past most context windows; anything larger is almost certainly
    # accidental. Truncate with a visible marker so the LLM (and the
    # user) knows content was dropped instead of silently corrupting.
    _MAX_TEXT_BYTES = 512 * 1024
    encoded = text.encode("utf-8", errors="ignore")
    if len(encoded) > _MAX_TEXT_BYTES:
        truncated = encoded[:_MAX_TEXT_BYTES].decode("utf-8", errors="ignore")
        text = (
            truncated
            + f"\n\n…[truncado en servidor: mensaje original ~{len(encoded)//1024} KB, "
            f"límite {_MAX_TEXT_BYTES//1024} KB]"
        )

    agent = getattr(app.state, "agent", None)
    if agent is None:
        # Agent not initialized — usually means user opened /face/ before
        # `python -m core.agent` finished bootstrapping. Tell them.
        await broadcast_to_face("speech_start", {
            "text": (
                "El cerebro todavía no está listo. Asegúrate de haber arrancado "
                "IRIS con `python -m core.agent` y vuelve a intentar."
            ),
            "emotion": "neutral",
        })
        await broadcast_to_face("speech_end", {})
        return

    # NOTE on persistence ordering:
    # We deliberately persist the user turn AFTER `_think()` returns,
    # not before. Saving before _think causes a double-send to the LLM:
    #   1. `_think` calls `memory.get_context_window()` → includes the
    #      just-saved user turn at the END of the context list
    #   2. `bridge.send_message()` then appends the *current* user
    #      message AGAIN as the final messages[] entry
    #   3. Hermes's agent runner concatenates everything into the
    #      `[Chat messages since your last reply]` block we saw in
    #      LiteLLM dumps — so each turn re-sent the entire history
    #      AND duplicated the latest message
    # Saving after _think means the LLM only sees:
    #   [system] [last N IRIS/user pairs from prior turns] [current user]
    # which is the canonical OpenAI chat format and ~half the tokens.

    # Track this whole turn (think + respond) so a barge-in can cancel
    # it cleanly via agent.interrupt(). Stored on the agent so the
    # interrupt path doesn't have to know about the WS handler's
    # local task. Cleared in the finally below regardless of outcome.
    async with agent._turn_lock:
        agent._current_turn_task = asyncio.current_task()

    visual_gate = None
    launch_offer = None
    try:
        # Reuse the agent's `_think()` pipeline — same code path as voice
        # input — so personality, memory, and JSON-parsing all behave
        # identically whether the user typed or spoke.
        response = None
        snapshot = None
        local_origin = websocket is None or _is_local_client(websocket)  # None: local voice
        screen_question = not camera_question and screen_snapshot.is_screen_request(text)
        if local_origin and not images and screen_question:
            screen_token = agent.bridge.route_context()["token"]
            outcome = await screen_snapshot.take_snapshot(
                websocket, agent, broker=_screen_snapshot_broker, store=_capability_store,
                registry=_surface_registry, is_local=_is_local_client,
                turn_is_current=lambda: turn_epoch is None or turn_epoch == _turn_epoch,
                policy_revision=lambda: _policy_revision,
                notify_cancel=_notify_screen_snapshot_cancel)
            if (outcome.get("code") in {"stale", "superseded"}
                or (turn_epoch is not None and turn_epoch != _turn_epoch)
                or agent.bridge.route_context()["token"] != screen_token):
                raise asyncio.CancelledError()
            if "image" in outcome:
                images, snapshot = [outcome["image"]], outcome
            else:
                # Keep the question: absence of an image is context for the
                # ordinary text reply, not a replacement for that reply.
                snapshot = {"source": "screen", "unavailable": outcome["code"], "route_token": screen_token}
        elif local_origin and not images and (camera_question or vision_snapshot.is_visual_request(text)):
            # F03b: a request to look now gets one photo for this turn, or an
            # honest answer without one. Never a second chat turn. A paired LAN
            # device chats as usual but never asks this PC's camera (R03).
            outcome = await _take_turn_snapshot(websocket, agent, text, turn_epoch=turn_epoch)
            if "image" in outcome:
                images, snapshot = [outcome["image"]], outcome
            else:
                response = _snapshot_reply(outcome["code"])
        if response is None:
            # Iris may decide to look once, only in a local turn that has no
            # image yet and no cancelled capture (core.visual_requests).
            if local_origin and not images and snapshot is None:
                visual_gate = await _offer_visual_requests(websocket, agent, text, turn_epoch)
            # ...and to leave a long task working while the conversation goes on.
            background_context = ""
            if local_origin:
                launch_offer, background_context = await _offer_background_task(agent, text, turn_epoch)
            context = "\n\n".join(filter(None, [
                visual_requests.context_for(visual_gate, _iris_skill_script("visual_request.py"))
                if visual_gate else "", background_context,
                _app_builder_context(agent, local_origin=local_origin, app_message=memory_text is not None),
            ]))
            extra = {"turn_context": context} if context else {}
            note, noted = await _background_history_note(agent)
            response = await agent._think(
                f"{note}\n{text}" if note else text,
                images=images,
                reasoning_effort=reasoning_effort,
                snapshot=snapshot,
                **extra,
            )
            if noted:
                await asyncio.to_thread(_background_runtime.store.mark_noted, noted)
    except asyncio.CancelledError:
        # Barge-in cancelled us mid-think. Don't send any UI events
        # (interrupt() already fired speech_end / idle). Re-raise so
        # asyncio knows the cancellation took effect.
        async with agent._turn_lock:
            agent._current_turn_task = None
        raise
    except Exception as e:
        async with agent._turn_lock:
            agent._current_turn_task = None
        logger.exception("user_text handler failed")
        await broadcast_to_face("speech_start", {
            "text": f"Hubo un error procesando tu mensaje: {e}",
            "emotion": "sad",
        })
        await broadcast_to_face("speech_end", {})
        return
    finally:
        if visual_gate is not None:
            await _close_visual_gate(visual_gate)
        if launch_offer is not None:
            _launch_offers.close(launch_offer)  # a launched task keeps running; only the code ends

    # Persist BOTH turns now that the LLM has consumed history WITHOUT
    # the current user msg duplicated. From this point onward, the
    # next `_think` call will see this turn as part of its context
    # window (canonical chat format).
    try:
        persisted_user_text = (memory_text or text).strip()
        await agent.memory.remember(
            "user", persisted_user_text, session_id=agent._session_id,
        )
        # Only persist IRIS's turn when she actually said something.
        # Empty `texto_hablado` happens when the upstream provider
        # returned an empty body (model out of quota, content filter,
        # 404 from a misconfigured route, etc) — persisting it would
        # accumulate in the Hermes session and eventually poison the
        # context (we hit exactly this bug: 168 messages of session
        # history, 4 of them empty assistants, caused Nous to start
        # rejecting NEW turns with HTTP 404).
        spoken = (response.get("texto_hablado") or "").strip()
        if spoken:
            await agent.memory.remember(
                "IRIS",
                spoken,
                emotion=response.get("emocion", "neutral"),
                session_id=agent._session_id,
                metadata={"animacion": response.get("animacion_virtual", "")},
            )
        else:
            logger.warning(
                "Skipping persistence of empty IRIS turn — "
                "provider returned no text. Response keys: %s",
                list((response or {}).keys()),
            )
    except Exception as e:
        logger.warning("Could not persist conversation turns: %s", e)

    # Hand off to the full respond pipeline — TTS, lip-sync,
    # animation, scene action, all the events. This is the SAME
    # method `_run_loop` calls after voice input.
    try:
        await agent._respond(response)
    except asyncio.CancelledError:
        # Barge-in during speech. interrupt() already cleaned up the
        # face state; just unwind silently.
        async with agent._turn_lock:
            agent._current_turn_task = None
        raise
    except Exception:
        logger.exception("agent._respond failed")
        # Fallback: at least show the text so the chat bubble updates.
        await broadcast_to_face("speech_start", {
            "text": response.get("texto_hablado") or "(sin respuesta)",
            "emotion": response.get("emocion", "neutral"),
        })
        await broadcast_to_face("speech_end", {})
    finally:
        # Always clear the turn-task pointer so the next interrupt
        # call knows there's nothing live to cancel. We do this in a
        # finally rather than at every exit point because the function
        # has many returns and forgetting one causes a stale pointer
        # — which would let interrupt() cancel the *next* turn.
        async with agent._turn_lock:
            agent._current_turn_task = None


async def handle_hermes_wake_event(event_type: str, payload: dict[str, Any]) -> None:
    """Translate native Hermes wake/voice events into one normal IRIS turn."""
    if event_type in {"manual_voice.partial", "voice.partial"}:
        # Display only: no history, memory, agent turn or speech synthesis.
        await broadcast_to_face("voice_partial", payload)
        return
    if event_type == "manual_voice.state":
        await broadcast_to_face("hermes_voice_state", payload)
        return
    if event_type == "manual_voice.transcript":
        text = str(payload.get("text") or "").strip()
        if text:
            await broadcast_to_face("voice_transcript", {
                "accepted": True, "text": text, "native_hermes": True, "mode": "manual",
                "client_id": payload.get("client_id", ""), "capture_id": payload.get("capture_id", ""),
            })
            # Manual voice is input to the current conversation, not Wake's
            # profile/new-session routing or its last routing error.
            await _handle_user_text(None, text)
        return
    runtime = getattr(app.state, "hermes_wake", None)
    base = {
        "active": bool(getattr(runtime, "listening", False)),
        "state": str(getattr(runtime, "state", "off")),
        "phrase": str(getattr(runtime, "phrase", "Hey Hermes")),
        "provider": str(getattr(runtime, "provider", "openwakeword")),
    }

    if event_type == "wake.detected":
        agent = getattr(app.state, "agent", None)
        route_warning = ""
        route_result: dict[str, Any] = {}
        if agent is not None and payload.get("start_new_session", True):
            profile = str(payload.get("profile") or "").strip()
            route_result = agent.bridge.start_new_session(profile=profile or None)
            route_warning = str(route_result.get("warning") or "")
            new_session_id = str(route_result.get("session_id") or "").strip()
            if new_session_id:
                agent._session_id = new_session_id
                agent._continuity_active = True
                try:
                    await agent.memory.save_preference(
                        "continuity.active_session_id",
                        new_session_id,
                    )
                except Exception as exc:
                    logger.warning("Could not persist wake Continuity session: %s", exc)
        app.state.hermes_wake_route_error = route_warning
        await broadcast_to_face("hermes_wake_state", {
            **base,
            **payload,
            **route_result,
            "active": True,
            "state": "detected",
            **({"notice": route_warning} if route_warning else {}),
        })
        return

    if event_type == "voice.transcript":
        text = str(payload.get("text") or "").strip()
        if not text:
            await broadcast_to_face("hermes_wake_state", {
                **base,
                "active": True,
                "state": "listening",
                "notice": "Hermes no detectó una orden; la escucha sigue activa.",
            })
            return
        route_warning = str(getattr(app.state, "hermes_wake_route_error", "") or "")
        app.state.hermes_wake_route_error = ""
        if route_warning:
            await broadcast_to_face("hermes_wake_state", {
                **base,
                "active": True,
                "state": "listening",
                "notice": route_warning,
            })
            return
        await broadcast_to_face("voice_transcript", {
            "accepted": True,
            "text": text,
            "native_hermes": True,
        })
        await broadcast_to_face("hermes_wake_state", {
            **base,
            "active": True,
            "state": "processing",
        })
        await _handle_user_text(None, text)
        return

    if event_type == "voice.stopped":
        app.state.hermes_wake_route_error = ""
        await broadcast_to_face("hermes_wake_state", {
            **base,
            **payload,
            "active": bool(payload.get("active", base["active"])),
            "state": str(payload.get("state") or "listening"),
            "notice": "Conversación por voz detenida; la frase de activación sigue disponible.",
        })
        return

    if event_type == "wake.error":
        await broadcast_to_face("hermes_wake_state", {
            **base,
            **payload,
            "active": bool(payload.get("active", base["active"])),
            "state": str(payload.get("state") or "error"),
        })
        return

    if event_type in {"voice.status", "wake.resumed"}:
        state = str(payload.get("state") or base["state"])
        await broadcast_to_face("hermes_wake_state", {
            **base,
            **payload,
            "active": bool(payload["active"]) if "active" in payload else bool(base["active"]),
            "state": state,
        })

# --- Broadcast to all face clients ---

async def broadcast_to_face(event_type: str, payload: dict[str, Any]) -> None:
    """Broadcast an event to all connected face layer clients."""
    message = json.dumps({"type": event_type, "payload": payload})
    disconnected: set[WebSocket] = set()

    for ws in tuple(_connected_clients):
        if event_type.startswith(("vision_assistant_", "inbox_", "background_")):
            peer = getattr(getattr(ws, "client", None), "host", None)
            if not peer or not is_loopback_host(peer):
                continue
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)

    for ws in disconnected:
        _disconnect_surface(ws)


async def send_tts_audio(payload: dict[str, Any], *, wait_for_end: bool = False, duration_s: float = 0.0) -> bool:
    """Send TTS bytes to exactly one audio owner.

    All other avatar-rendering surfaces receive only ``lip_sync`` so their
    mouths remain animated without producing duplicate sound.  A failed owner
    is removed and selection is retried with the next eligible surface.  The
    return value tells ``core.agent`` whether browser/WebView delivery really
    succeeded; ``False`` activates the existing server-speaker fallback.
    """
    attempted: set[WebSocket] = set()
    owner: WebSocket | None = None
    playback_id = ""

    while True:
        candidate = _surface_registry.audio_owner()
        if candidate is None or candidate in attempted:
            owner = None
            break
        owner = candidate
        attempted.add(candidate)
        record = _surface_registry.get(candidate)
        playback_id = _tts_playback.begin(candidate) if wait_for_end and record and record.supports("reports_tts_playback") else ""
        outgoing = {**payload, "playback_id": playback_id} if playback_id else payload
        try:
            await candidate.send_text(json.dumps({"type": "tts_audio", "payload": outgoing}))
            break
        except asyncio.CancelledError:
            _tts_playback.discard(playback_id)
            raise
        except Exception:
            _tts_playback.discard(playback_id)
            logger.warning("TTS owner disconnected during delivery; selecting another surface")
            _disconnect_surface(candidate)
            owner = None

    if owner is None:
        return False

    try:
        visemes = payload.get("visemes")
        if isinstance(visemes, list) and visemes:
            lip_message = json.dumps({
                "type": "lip_sync",
                "payload": {"visemes": visemes, "audio_owner": False},
            })
            disconnected: set[WebSocket] = set()
            for ws in _surface_registry.capable_clients("renders_avatar", exclude=(owner,)):
                try:
                    await ws.send_text(lip_message)
                except Exception:
                    disconnected.add(ws)
            for ws in disconnected:
                _disconnect_surface(ws)
        if playback_id:
            outcome = await _tts_playback.wait(playback_id, duration_s)
            if outcome != "ended":
                logger.warning("TTS surface playback finished: %s", outcome)
        elif wait_for_end:
            # Compatibility for cached clients that cannot acknowledge playback.
            await asyncio.sleep(max(0.5, duration_s) + 2.0)
    finally:
        _tts_playback.discard(playback_id)

    return True


def audio_clients_connected() -> int:
    """How many live surfaces explicitly support TTS playback?"""
    return _surface_registry.audio_capable_count()


async def _relay_to_others(sender: WebSocket, event_type: str, payload: dict[str, Any]) -> None:
    """Relay a WebRTC signaling message to all connected clients except the sender.

    This lets the two browser tabs (one on PC running the avatar, one on
    the phone acting as remote client) coordinate the P2P WebRTC handshake
    through the existing WebSocket infrastructure.
    """
    message = json.dumps({"type": event_type, "payload": payload})
    disconnected: set[WebSocket] = set()

    for ws in tuple(_connected_clients):
        if ws is sender:
            continue
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)

    for ws in disconnected:
        _disconnect_surface(ws)


async def send_speech_start(
    text: str,
    emotion: str = "neutral",
    *,
    animation: str = "",
    # accion_escena: a futuro, animaciones de escena no implementadas aún
    accion_escena: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """Notify face layer that IRIS is about to speak.

    The payload mirrors the visible fields IRIS consumes from the LLM
    response, not just text + emotion — the face's brain panel needs
    enough context to show animation and scene intent. Hermes-owned
    capabilities such as sub-agents and scheduled work stay inside
    Hermes instead of being forwarded as IRIS UI fields.

    Animation/emotion are STILL dispatched via their own dedicated
    messages (``send_animation`` / ``send_emotion``) earlier in the
    pipeline so the avatar reacts before audio onset — this payload
    is the snapshot for inspection, not the trigger.
    """
    payload: dict[str, Any] = {"text": text, "emotion": emotion}
    if animation:
        payload["animacion_virtual"] = animation
    if accion_escena:
        # TODO: futuro — animaciones de escena no implementadas aún.
        payload["accion_escena"] = accion_escena
    if extra:
        # Pass-through for any future LLM-side field (cost metadata,
        # safety flags, etc.) without bumping the signature again.
        payload["extra"] = extra
    await broadcast_to_face("speech_start", payload)


async def send_speech_end() -> None:
    """Notify face layer that IRIS has finished speaking."""
    await broadcast_to_face("speech_end", {})


async def send_emotion(emotion: str) -> None:
    """Update IRIS's emotional expression."""
    await broadcast_to_face("emotion", {"emotion": emotion})


async def send_animation(animation: str) -> None:
    """Trigger an animation on the avatar."""
    await broadcast_to_face("animation", {"name": animation})


async def send_lip_sync(viseme_data: list[dict[str, Any]]) -> None:
    """Send lip sync data to the avatar."""
    await broadcast_to_face("lip_sync", {"visemes": viseme_data})


async def send_subtitle(text: str, is_final: bool = False) -> None:
    """Send subtitle text for display."""
    await broadcast_to_face("subtitle", {"text": text, "final": is_final})


async def send_status(status: str, details: dict[str, Any] | None = None) -> None:
    """Send system status update."""
    await broadcast_to_face("status", {"status": status, **(details or {})})


# --- Scene Action Mapping (for 3D room) ---

# Maps LLM-detected actions to room furniture targets and visual effects.
SCENE_ACTION_MAP: dict[str, dict[str, str]] = {
    "buscar_internet":  {"mover_a": "escritorio", "efecto": "dataStream"},
    "recordar":         {"mover_a": "estanteria", "efecto": "bookFloat"},
    "pensar":           {"mover_a": "centro",     "efecto": "cloudParticles"},
    "buscar_archivos":  {"mover_a": "escritorio", "efecto": "fileSearch"},
    "respuesta_lista":  {"mover_a": "centro",     "efecto": "none"},
}


async def send_scene_action(action: str) -> None:
    """
    Emit a scene action to the 3D room.

    Sends the format expected by face/js/scene_actions.js:
        {
            "type": "scene_action",
            "payload": {
                "animacion_virtual": "buscar_internet",
                "escena": {
                    "mover_a": "escritorio",
                    "efecto": "dataStream"
                }
            }
        }

    Args:
        action: One of the keys in SCENE_ACTION_MAP
                (buscar_internet, recordar, pensar, buscar_archivos, respuesta_lista)
    """
    escena = SCENE_ACTION_MAP.get(action, {"mover_a": "centro", "efecto": "none"})
    await broadcast_to_face("scene_action", {
        "animacion_virtual": action,
        "escena": escena,
    })


# --- REST API Endpoints ---
#
# Note: ``/api/upload-and-rig`` used to live here for the face's
# drag-and-drop avatar uploader. It accepted OBJ/FBX/STL and ran the
# heuristic auto-rigger. We removed it (2026-05) because:
#   • The face uploader is restricted to VRM/GLB/GLTF — formats that
#     already ship a skeleton, so no rigging is needed.
#   • The auto-rig pipeline is fragile for arbitrary meshes and gave
#     unusable results often enough that the user surface was
#     misleading.
#   • The wizard still exposes the full pipeline via ``/api/rig-model``
#     for users who really want to import an FBX rig — that's the
#     right place for a multi-step, error-tolerant flow.


def _animation_library():
    """Use the mounted factory assets and the shared mutable profile."""
    from core.animation_library import get_animation_library

    face_root = getattr(app.state, "face_dir", None) or runtime_policy.get_runtime().assets_root
    return get_animation_library(Path(face_root) / "animations")


@app.api_route("/api/animation-manifest", methods=["GET", "HEAD"])
async def get_animation_manifest(request: Request) -> Response:
    """Revalidate committed state, including commits from a separate Wizard."""
    from core.animation_store import AnimationStoreError

    try:
        snapshot = await asyncio.to_thread(_animation_library().snapshot)
    except (OSError, ValueError, AnimationStoreError) as exc:
        raise HTTPException(503, "Biblioteca de animaciones temporalmente no disponible",
                            headers={"Retry-After": "1", "Cache-Control": "no-store"}) from exc
    etag = '"' + snapshot["revision"] + '"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if etag in [
        tag.strip().removeprefix("W/")
        for tag in request.headers.get("if-none-match", "").split(",")
    ]:
        return Response(status_code=304, headers=headers)
    response = JSONResponse(snapshot, headers=headers)
    if request.method == "HEAD":
        return Response(headers=dict(response.headers), media_type="application/json")
    return response


@asset_upload_route(app, "/api/upload-vrma")
async def upload_vrma(
    file: UploadFile = File(...),
    name: str = Form(...),
) -> JSONResponse:
    """Publish an immutable animation and atomically switch its manifest slot."""
    from core.animation_store import AnimationStoreError, normalize_animation_name
    from core.animations import synchronize_animation_upload

    try:
        safe_name = normalize_animation_name(name)
        saved = await asyncio.to_thread(
            _animation_library().save_upload, file.file, file.filename or "", safe_name,
        )
    except AnimationStoreError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    agent = getattr(app.state, "agent", None)
    personality = getattr(agent, "personality", None) if agent else None
    sync = await synchronize_animation_upload(personality)
    warnings = [saved.warning] if saved.warning else []
    warnings.extend(sync["warnings"])
    # Notify only committed state; clients revalidate its snapshot/ETag.
    try:
        await broadcast_to_face("animation_catalog_changed", {
            "name": saved.name, "filename": saved.filename, "revision": saved.revision,
        })
    except Exception:
        logger.exception("Animation committed; catalog notification pending")
        warnings.append("Animación guardada; la notificación al avatar sigue pendiente.")
    return JSONResponse({
        "status": "ok", "saved": True,
        "name": saved.name, "filename": saved.filename,
        "url": f"/face/animations/{saved.filename}", "size": saved.size,
        "revision": saved.revision, "changed": saved.changed,
        "sync_status": sync["sync_status"], "warnings": warnings,
    })


@app.get("/")
async def root() -> HTMLResponse:
    """Redirect to the face UI."""
    return HTMLResponse(
        '<html><head><meta http-equiv="refresh" content="0;url=/face/"></head></html>'
    )


@app.get("/api/status")
async def get_status() -> JSONResponse:
    """Get IRIS' current status."""
    runtime = runtime_policy.get_runtime()
    return JSONResponse({
        "name": "IRIS",
        "status": "running",
        "version": IRIS_VERSION,
        **({"runtime": runtime_policy.runtime_service_identity(get_iris_home())}
           if runtime.packaged else {}),
        "connected_clients": len(_connected_clients),
        "audio_clients": audio_clients_connected(),
        "audio_owner_surface_id": _surface_registry.audio_owner_surface_id(),
        "surfaces": [
            record.snapshot(
                audio_owner=_surface_registry.audio_owner() is record.client,
                camera_owner=_surface_registry.camera_owner() is record.client,
            )
            for record in _surface_registry.records()
        ],
    })


def _system_metrics_snapshot() -> dict[str, Any]:
    """Return honest local metrics when the optional psutil extra exists.

    The desktop must never fabricate CPU/RAM/disk percentages. A runtime
    without psutil remains fully usable and reports explicit null values.
    """
    result: dict[str, Any] = {
        "available": False,
        "source": "unavailable",
        "cpu_percent": None,
        "memory_percent": None,
        "disk_percent": None,
    }
    try:
        import psutil  # type: ignore[import-not-found]

        root = Path.home().anchor or os.path.abspath(os.sep)
        result.update({
            "available": True,
            "source": "psutil",
            "cpu_percent": round(float(psutil.cpu_percent(interval=None)), 1),
            "memory_percent": round(float(psutil.virtual_memory().percent), 1),
            "disk_percent": round(float(psutil.disk_usage(root).percent), 1),
        })
    except Exception as exc:
        result["detail"] = f"System metrics unavailable: {type(exc).__name__}"
    return result


@app.get("/api/system-metrics")
async def get_system_metrics() -> JSONResponse:
    """Best-effort CPU, memory and disk usage for the local OS dashboard."""
    return JSONResponse(_system_metrics_snapshot())


@app.get("/api/config")
async def get_config() -> JSONResponse:
    """Get current configuration (non-sensitive)."""
    config = IRISConfig.load()
    # Positive allow-list: a newly added credential-like setting must never
    # become public merely because its name did not match a deny-list suffix.
    public_fields = {
        "assistant_name",
        "avatar_vrm_path",
        "render_fps_limit",
        "render_quality",
        "room_enabled",
        "room_theme",
        "visual_theme",
        "visual_theme_selected",
        "companion_always_on_top",
        "companion_scale",
        "companion_reduced_motion",
        "companion_show_controls",
        "companion_locomotion_enabled",
        "companion_quiet_mode",
        "companion_life",
    }
    safe_config = {
        name: value
        for name, value in config.model_dump().items()
        if name in public_fields
    }
    safe_config["avatar_vrm_path"] = _public_avatar_url(config.avatar_vrm_path)
    safe_config["avatar_revision"] = _avatar_revision(config.avatar_vrm_path)
    safe_config["iris_version"] = IRIS_VERSION
    return JSONResponse(safe_config)


@app.get("/api/render-settings")
async def get_render_settings() -> JSONResponse:
    config = IRISConfig.load()
    return JSONResponse({"render_fps_limit": config.render_fps_limit, "render_quality": config.render_quality})


_render_settings_lock = asyncio.Lock()


@app.put("/api/render-settings")
async def save_render_settings(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
    if not payload or set(payload) - {"render_fps_limit", "render_quality"}:
        raise HTTPException(status_code=422, detail="Elige los FPS y la calidad gráfica.")
    if "render_fps_limit" in payload and (
        type(payload["render_fps_limit"]) is not int or payload["render_fps_limit"] not in (0, 30, 60)
    ):
        raise HTTPException(status_code=422, detail="Elige 30 FPS, 60 FPS o sin límite.")
    if "render_quality" in payload and payload["render_quality"] not in ("saving", "balanced", "high"):
        raise HTTPException(status_code=422, detail="Elige Ahorro, Equilibrado o Alta calidad.")
    async with _render_settings_lock:
        current = await asyncio.to_thread(IRISConfig.load)
        updated = IRISConfig(**{**current.model_dump(), **payload}).inherit_loaded_snapshot(current)
        try:
            await asyncio.to_thread(updated.save)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="No pude guardar el rendimiento.") from exc
        preferences = {"render_fps_limit": updated.render_fps_limit, "render_quality": updated.render_quality}
        await broadcast_to_face("render_preferences", preferences)
        return JSONResponse(preferences)


_COMPANION_PREFERENCE_FIELDS = {
    "companion_always_on_top",
    "companion_scale",
    "companion_reduced_motion",
    "companion_show_controls",
    "companion_locomotion_enabled",
    "companion_quiet_mode",
    "companion_life",
}


def _companion_preferences(config: IRISConfig | None = None) -> dict[str, Any]:
    current = config or IRISConfig.load()
    return {
        "companion_always_on_top": current.companion_always_on_top,
        "companion_scale": current.companion_scale,
        "companion_reduced_motion": current.companion_reduced_motion,
        "companion_show_controls": current.companion_show_controls,
        "companion_locomotion_enabled": current.companion_locomotion_enabled,
        "companion_quiet_mode": current.companion_quiet_mode,
        "companion_life": current.companion_life,
    }


@app.get("/api/companion/settings")
async def get_companion_settings() -> JSONResponse:
    """Return the optional shell's non-sensitive, non-autostart preferences."""
    return JSONResponse({
        **_companion_preferences(),
        "privacy": {
            "camera_default": "off",
            "microphone_capture": "hermes_native_only",
            "video_transport": "none",
        },
        "camera": _surface_registry.camera_snapshot(),
    })


@app.post("/api/companion/settings")
async def save_companion_settings(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
    """Persist and live-broadcast only preferences implemented by the shell.

    Autostart and installation are deliberately absent: those lifecycle actions
    belong to the later packaging phase and this endpoint must not imply that
    saving a preference can launch or install the Companion.
    """
    unknown = set(payload) - _COMPANION_PREFERENCE_FIELDS
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Preferencias no reconocidas: {', '.join(sorted(unknown))}",
        )
    if not payload:
        raise HTTPException(status_code=422, detail="No hay preferencias para guardar")

    updates: dict[str, Any] = {}
    for field in _COMPANION_PREFERENCE_FIELDS - {"companion_scale", "companion_life"}:
        if field not in payload:
            continue
        if type(payload[field]) is not bool:
            raise HTTPException(status_code=422, detail=f"{field} debe ser booleano")
        updates[field] = payload[field]

    if "companion_life" in payload:
        if payload["companion_life"] not in ("off", "calm", "active"):
            raise HTTPException(status_code=422, detail="Elige Apagada, Tranquila o Activa.")
        updates["companion_life"] = payload["companion_life"]

    if "companion_scale" in payload:
        value = payload["companion_scale"]
        if isinstance(value, bool):
            raise HTTPException(status_code=422, detail="companion_scale debe ser numérico")
        try:
            scale = float(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="companion_scale debe ser numérico") from exc
        if not math.isfinite(scale) or not 0.75 <= scale <= 1.5:
            raise HTTPException(status_code=422, detail="companion_scale debe estar entre 0.75 y 1.5")
        updates["companion_scale"] = scale

    try:
        with companion_operation_lock():
            current = IRISConfig.load()
            updated = IRISConfig(
                **{**current.model_dump(), **updates}
            ).inherit_loaded_snapshot(current)
            await asyncio.to_thread(updated.save)
    except OSError as exc:
        logger.exception("Could not persist Desktop Companion preferences")
        raise HTTPException(status_code=500, detail="No pude guardar la configuración de IRIS") from exc

    preferences = _companion_preferences(updated)
    await broadcast_to_face("companion_preferences", preferences)
    return JSONResponse({"status": "saved", **preferences})


_VISION_SETTING_FIELDS = {
    "enabled", "mode", "cooldown_minutes", "retention_days", "pause_minutes",
    "confirm_camera", "policy",
}


async def _broadcast_notification_summary(cfg: IRISConfig) -> None:
    snapshot = await asyncio.to_thread(
        _vision_assistant.notification_snapshot,
        retention_days=cfg.vision_assistant_log_retention_days,
    )
    await broadcast_to_face("vision_assistant_notifications", {
        "revision": snapshot["revision"], "unread": snapshot["unread"],
        "availability": snapshot["availability"],
    })
    _queue_inbox_announcement()  # F07: the general inbox lists Vision notices too


async def _broadcast_vision_notifications(payload: dict[str, Any]) -> None:
    await broadcast_to_face("vision_assistant_notifications", payload)
    _queue_inbox_announcement()


def _vision_activity_surfaces() -> tuple[str, ...]:
    return tuple(
        record.surface_id for record in _surface_registry.records()
        if record.role in {"face", "legacy_face", "desktop_companion"}
        and (record.visible or _surface_registry.camera_owner() is record.client)
        and is_loopback_host(str(getattr(getattr(record.client, "client", None), "host", "")))
    )


async def _present_vision_notification(note: dict) -> None:
    """Best-effort single-target gesture, never a replay or an audio/chat dispatch."""
    if note.get("kind") != "returned" or _vision_backend_busy():
        return
    cfg = IRISConfig.load()
    if not cfg.vision_assistant_enabled or cfg.vision_assistant_paused_until > time.time():
        return
    owner = _surface_registry.vision_owner(local_clients=(
        row.client for row in _surface_registry.records()
        if is_loopback_host(str(getattr(getattr(row.client, "client", None), "host", "")))
    ))
    record = _surface_registry.get(owner)
    if record is None:
        return
    payload = _vision_assistant.return_presentation(
        note.get("id"), activity_surfaces=_vision_activity_surfaces(),
    )
    if payload is None:
        return
    payload["surface_id"] = record.surface_id
    try:
        await asyncio.wait_for(owner.send_json({
            "type": "vision_assistant_presentation", "payload": payload,
        }), timeout=2)
    except Exception:
        # Durable inbox delivery is already complete. Never reroute/replay the
        # gesture when a socket fails: an ambiguous send could otherwise greet twice.
        logger.debug("Vision presentation unavailable; notification remains in inbox")


def _vision_backend_busy() -> bool:
    agent = getattr(app.state, "agent", None)
    for name in ("_conversation_lock", "_speak_lock"):
        lock = getattr(agent, name, None)
        if lock is not None and lock.locked():
            return True
    task = getattr(agent, "_current_turn_task", None)
    return task is not None and not task.done()


async def _calibrate_vision(action: str) -> None:
    cfg = IRISConfig.load()
    owner = _surface_registry.camera_owner()
    record = _surface_registry.get(owner)
    if (not cfg.vision_assistant_enabled or cfg.vision_assistant_paused_until > time.time()
        or record is None or not record.visible or not record.supports("tunes_vision")
        or record.role not in {"face", "desktop_companion"}
        or not owner.client or not is_loopback_host(owner.client.host)):
        raise HTTPException(status_code=409, detail=(
            "Necesitas Vision activo y una cámara ya autorizada en Face o Companion actualizado. "
            "Calibrar no abre ni transfiere la cámara."))
    now = time.time()
    try:
        await asyncio.wait_for(owner.send_json({
            "type": "vision_assistant_calibration",
            "payload": {"id": uuid.uuid4().hex, "surface_id": record.surface_id,
                        "action": action, "created_at": now, "expires_at": now + 5},
        }), timeout=2)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="La superficie de cámara no respondió.") from exc


def _vision_preferences(config: IRISConfig | None = None) -> dict[str, Any]:
    current = config or IRISConfig.load()
    return {
        "enabled": current.vision_assistant_enabled,
        "mode": current.vision_assistant_mode,
        "cooldown_minutes": current.vision_assistant_cooldown_minutes,
        "retention_days": current.vision_assistant_log_retention_days,
        "paused_until": current.vision_assistant_paused_until,
        "policy": current.vision_assistant_policy.model_dump(),
    }


def _vision_snapshot(config: IRISConfig | None = None) -> dict[str, Any]:
    current = config or IRISConfig.load()
    preferences = _vision_preferences(current)
    runtime = _vision_assistant.snapshot(
        enabled=current.vision_assistant_enabled,
        paused_until=current.vision_assistant_paused_until,
        camera=_surface_registry.camera_snapshot(),
        policy_settings=current.vision_assistant_policy,
        cooldown_minutes=current.vision_assistant_cooldown_minutes,
        activity_surfaces=_vision_activity_surfaces(),
        backend_busy=_vision_backend_busy(),
    )
    return {
        **preferences,
        **runtime,
        "permissions": {
            "camera": _capability_store.evaluate(
                "camera.capture", trigger="automatic", confirmed=False,
            ),
            "context": _capability_store.evaluate(
                "vision.context", trigger="automatic", confirmed=False,
            ),
        },
        "contract": {
            "default": "off",
            "activation": "explicit",
            "camera_owner": "surface_lease",
            "automatic_audio_upload": False,
            "automatic_image_upload": False,
            "automatic_conversation": False,
        },
    }


@app.get("/api/vision-assistant/settings")
async def get_vision_assistant_settings() -> JSONResponse:
    """Expose the persisted opt-in and the current local-only runtime state."""
    return JSONResponse(_vision_snapshot())


@app.post("/api/vision-assistant/settings")
async def save_vision_assistant_settings(
    payload: dict[str, Any] = Body(default={}),
) -> JSONResponse:
    """Persist only implemented Vision Assistant controls.

    Enabling this setting authorizes local analysis, not camera permission.
    The visible surface must still request the exclusive camera lease and the
    browser may show its own device-consent prompt.
    """
    unknown = set(payload) - _VISION_SETTING_FIELDS
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Opciones de visión no reconocidas: {', '.join(sorted(unknown))}",
        )
    if not payload:
        raise HTTPException(status_code=422, detail="No hay opciones de visión para guardar")

    current = IRISConfig.load()
    updates: dict[str, Any] = {}
    camera_confirmation_granted = False
    if "policy" in payload:
        if not isinstance(payload["policy"], dict):
            raise HTTPException(status_code=422, detail="policy debe ser un objeto")
        try:
            policy = {**current.vision_assistant_policy.model_dump(), **payload["policy"]}
            cooldowns = payload["policy"].get("kind_cooldown_minutes")
            if isinstance(cooldowns, dict):
                policy["kind_cooldown_minutes"] = {
                    **current.vision_assistant_policy.kind_cooldown_minutes.model_dump(),
                    **cooldowns,
                }
            updates["vision_assistant_policy"] = VisionPolicySettings.model_validate(policy)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422, detail="La política visual contiene opciones o valores inválidos.",
            ) from exc
    if "confirm_camera" in payload and type(payload["confirm_camera"]) is not bool:
        raise HTTPException(status_code=422, detail="confirm_camera debe ser booleano")
    if "enabled" in payload:
        if type(payload["enabled"]) is not bool:
            raise HTTPException(status_code=422, detail="enabled debe ser booleano")
        if payload["enabled"] and not current.vision_assistant_enabled:
            camera_permission = await asyncio.to_thread(
                _capability_store.evaluate,
                "camera.capture",
                trigger="manual",
                confirmed=payload.get("confirm_camera") is True,
            )
            if camera_permission["verdict"] == "deny":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "La Cámara está bloqueada en Capability Center. "
                        "Cambia su política antes de activar Vision Assistant."
                    ),
                )
            if camera_permission["verdict"] == "ask":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Confirma explícitamente el uso local de la cámara para "
                        "activar Vision Assistant."
                    ),
                )
            camera_confirmation_granted = payload.get("confirm_camera") is True
        updates["vision_assistant_enabled"] = payload["enabled"]
        if not payload["enabled"]:
            updates["vision_assistant_paused_until"] = 0.0
    if "mode" in payload:
        if payload["mode"] != "silent":
            raise HTTPException(
                status_code=422,
                detail="Por ahora solo está disponible el modo silencioso local.",
            )
        updates["vision_assistant_mode"] = "silent"

    for incoming, target, minimum, maximum in (
        ("cooldown_minutes", "vision_assistant_cooldown_minutes", 1, 180),
        ("retention_days", "vision_assistant_log_retention_days", 0, 30),
    ):
        if incoming not in payload:
            continue
        value = payload[incoming]
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise HTTPException(
                status_code=422,
                detail=f"{incoming} debe ser un entero entre {minimum} y {maximum}",
            )
        updates[target] = value

    if "pause_minutes" in payload:
        value = payload["pause_minutes"]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 480:
            raise HTTPException(
                status_code=422,
                detail="pause_minutes debe ser un entero entre 0 y 480",
            )
        updates["vision_assistant_paused_until"] = time.time() + value * 60 if value else 0.0

    updated = IRISConfig(**{**current.model_dump(), **updates}).inherit_loaded_snapshot(current)
    try:
        await asyncio.to_thread(updated.save)
    except OSError as exc:
        logger.exception("Could not persist Vision Assistant settings")
        raise HTTPException(status_code=500, detail="No pude guardar Vision Assistant") from exc

    running_agent = getattr(app.state, "agent", None)
    if running_agent is not None:
        for field, value in updates.items():
            setattr(running_agent.config, field, value)
    if not updated.vision_assistant_enabled or updated.vision_assistant_paused_until > time.time():
        _vision_assistant.reset_observation()
    if updated.vision_assistant_log_retention_days == 0:
        await asyncio.to_thread(_vision_assistant.clear_events)
    elif "retention_days" in payload:
        await asyncio.to_thread(
            _vision_assistant.enforce_retention,
            retention_days=updated.vision_assistant_log_retention_days,
        )
    snapshot = _vision_snapshot(updated)
    await broadcast_to_face("vision_assistant_state", snapshot)
    return JSONResponse({
        "status": "saved",
        **snapshot,
        # Ephemeral proof for the initiating surface only. It is deliberately
        # absent from persistence and from the broadcast to other surfaces.
        "camera_confirmation_granted": camera_confirmation_granted,
    })


@app.get("/api/vision-assistant/events")
async def get_vision_assistant_events(limit: int = 80) -> JSONResponse:
    cfg = IRISConfig.load()
    rows = await asyncio.to_thread(
        _vision_assistant.events,
        retention_days=cfg.vision_assistant_log_retention_days,
        limit=max(1, min(limit, 200)),
    )
    return JSONResponse({
        "events": rows,
        "count": len(rows),
        "contains_media": False,
        "retention_days": cfg.vision_assistant_log_retention_days,
    })


@app.delete("/api/vision-assistant/events")
async def clear_vision_assistant_events() -> JSONResponse:
    try:
        await asyncio.to_thread(_vision_assistant.clear_events)
    except OSError as exc:
        logger.exception("Could not clear Vision Assistant history")
        raise HTTPException(status_code=500, detail="No pude borrar el historial visual") from exc
    await broadcast_to_face("vision_assistant_events_cleared", {})
    await _broadcast_notification_summary(IRISConfig.load())
    return JSONResponse({"status": "cleared", "contains_media": False})


def _public_avatar_url(configured_path: str) -> str:
    """Map a configured local avatar to a browser-safe, non-disclosing URL."""
    clean = str(configured_path or "").strip()
    if not clean or clean.startswith(("/", "http://", "https://", "blob:")):
        return clean

    candidate = Path(clean).expanduser()
    filename = candidate.name
    if not filename:
        return ""
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        # Preserve the historical managed-avatar route for stale configs so
        # the frontend gets an ordinary 404 instead of an absolute OS path.
        return f"/avatars/{urllib.parse.quote(filename)}"

    from core.config import get_iris_home

    managed = (get_iris_home() / "avatars").resolve()
    if resolved.parent == managed:
        return f"/avatars/{urllib.parse.quote(filename)}"
    return "/api/avatar/current"


def _avatar_revision(configured_path: str) -> str:
    """Opaque local file version: catches same-name uploads and /current swaps."""
    import hashlib

    clean = str(configured_path or "").strip()
    if not clean or clean.startswith(("http://", "https://", "blob:")):
        return ""
    candidate = Path(clean).expanduser()
    if clean.startswith("/avatars/"):
        from core.config import get_iris_home
        candidate = get_iris_home() / "avatars" / Path(urllib.parse.unquote(clean)).name
    try:
        stat = candidate.stat()
        version = f"{candidate.resolve()}:{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        version = clean
    return hashlib.sha256(version.encode("utf-8")).hexdigest()[:20]


_OS_AVATAR_MAX_BYTES = AVATAR_MAX_BYTES
_OS_THEME_IMAGE_LIMITS = {
    "profile": 2_200_000,
    "background": 3_200_000,
}
_OS_THEME_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


async def _read_bounded_upload(file: UploadFile, max_bytes: int) -> bytes:
    """Read an upload without allowing an unbounded request into memory."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="El archivo supera el tamaño permitido")
        chunks.append(chunk)
    if not chunks:
        raise HTTPException(status_code=400, detail="El archivo está vacío")
    return b"".join(chunks)


def _atomic_write_upload(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.iris-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary and temporary.exists():
            temporary.unlink(missing_ok=True)


def _theme_image_format(content: bytes) -> tuple[str, str] | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return ".gif", "image/gif"
    if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


@asset_upload_route(app, "/api/avatar/upload")
async def upload_os_avatar(file: UploadFile = File(...)) -> JSONResponse:
    """Persist a VRM selected from IRIS OS and make it the active avatar."""
    import re

    try:
        filename = upload_filename(file.filename or "")
    except AssetUploadError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    suffix = Path(filename).suffix.casefold()
    if suffix not in {".vrm", ".glb"}:
        raise HTTPException(
            status_code=400,
            detail="IRIS OS solo guarda archivos .vrm o .glb que declaren metadatos VRM",
        )
    try:
        content = await asyncio.to_thread(read_bounded, file.file, _OS_AVATAR_MAX_BYTES)
        await asyncio.to_thread(validate_asset, content, suffix[1:], avatar=True)
    except AssetUploadError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).stem).strip(".-")[:64]
    destination_name = f"os-{safe_stem or 'avatar'}{suffix}"
    from core.config import get_iris_home

    try:
        destination = await asyncio.to_thread(
            atomic_avatar_write, get_iris_home() / "avatars", destination_name, content
        )
    except AssetUploadError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "No se pudo guardar el avatar.") from exc

    cfg = IRISConfig.load()
    cfg.avatar_vrm_path = str(destination)
    await asyncio.to_thread(cfg.save)

    avatar_url = f"/avatars/{urllib.parse.quote(destination_name)}"
    await broadcast_to_face("avatar_config_changed", {})
    return JSONResponse({
        "status": "saved",
        "avatar_url": avatar_url,
        "size_bytes": len(content),
    })


@app.post("/api/os/theme-image")
async def upload_os_theme_image(
    file: UploadFile = File(...),
    kind: str = Form(...),
) -> JSONResponse:
    """Persist Theme Studio profile/background art outside localStorage."""
    normalized_kind = str(kind or "").strip().casefold()
    max_bytes = _OS_THEME_IMAGE_LIMITS.get(normalized_kind)
    if max_bytes is None:
        raise HTTPException(status_code=400, detail="Tipo de imagen de tema no soportado")
    content = await _read_bounded_upload(file, max_bytes)
    detected = _theme_image_format(content)
    if detected is None:
        raise HTTPException(status_code=400, detail="La imagen no es PNG, JPG, GIF o WebP válido")
    suffix, _media_type = detected

    from core.config import get_iris_home

    assets_dir = get_iris_home() / "ui-assets"
    destination = assets_dir / f"os-{normalized_kind}{suffix}"
    await asyncio.to_thread(_atomic_write_upload, destination, content)
    for stale_suffix in _OS_THEME_IMAGE_TYPES:
        stale = assets_dir / f"os-{normalized_kind}{stale_suffix}"
        if stale != destination and stale.exists():
            try:
                stale.unlink()
            except OSError:
                logger.warning("Could not remove stale OS theme image: %s", stale)

    version = destination.stat().st_mtime_ns
    return JSONResponse({
        "status": "saved",
        "kind": normalized_kind,
        "url": f"/api/os/theme-image/{normalized_kind}?v={version}",
        "size_bytes": len(content),
    })


@app.get("/api/os/theme-image/{kind}")
async def current_os_theme_image(kind: str) -> FileResponse:
    normalized_kind = str(kind or "").strip().casefold()
    if normalized_kind not in _OS_THEME_IMAGE_LIMITS:
        raise HTTPException(status_code=404, detail="Imagen de tema no encontrada")
    from core.config import get_iris_home

    assets_dir = get_iris_home() / "ui-assets"
    candidates = [
        assets_dir / f"os-{normalized_kind}{suffix}"
        for suffix in _OS_THEME_IMAGE_TYPES
    ]
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        raise HTTPException(status_code=404, detail="Imagen de tema no encontrada")
    selected = max(existing, key=lambda path: path.stat().st_mtime_ns)
    return FileResponse(
        selected,
        media_type=_OS_THEME_IMAGE_TYPES[selected.suffix.casefold()],
        content_disposition_type="inline",
    )


@app.get("/api/avatar/current")
async def current_avatar_file() -> FileResponse:
    """Serve only the current legacy/external avatar selected in config."""
    configured = str(IRISConfig.load().avatar_vrm_path or "").strip()
    if not configured or configured.startswith(("http://", "https://", "blob:")):
        raise HTTPException(status_code=404, detail="No local avatar is configured")
    candidate = Path(configured).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail="Configured avatar is unavailable") from exc
    if not resolved.is_file() or resolved.suffix.casefold() not in {".vrm", ".glb"}:
        raise HTTPException(status_code=404, detail="Configured avatar is not a supported model")
    return FileResponse(
        path=resolved,
        media_type="model/gltf-binary",
        filename=resolved.name,
        content_disposition_type="inline",
    )


# ── IRIS Player — opt-in local library and bounded streaming ─────


class PlayerFolderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)


class PlayerFavoriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    favorite: StrictBool


class PlayerActionRequest(BaseModel):
    """Bounded app intent accepted from the local Hermes skill adapter."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    contract_schema: str = Field(default="iris.app-intent/v1", alias="schema", max_length=64)
    app: str = Field(default="player", max_length=32)
    action: str = Field(min_length=1, max_length=48)
    args: dict[str, Any] = Field(default_factory=dict)


def _validated_player_action(payload: PlayerActionRequest) -> dict[str, Any]:
    """The canonical Player intent, validated by its registered operation (F09.4)."""
    if payload.contract_schema != "iris.app-intent/v1" or payload.app != "player":
        raise HTTPException(status_code=422, detail="Contrato de IRIS Player no compatible")
    try:
        operation, args = _app_actions.resolve({
            "schema": payload.contract_schema, "app": "player", "action": payload.action, "args": payload.args,
        })
    except OperationRejected as exc:
        detail = "Acción de IRIS Player no admitida" if exc.detail == "Acción no admitida" else exc.detail
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc
    return {"schema": "iris.app-intent/v1", "app": "player", "action": operation.action, "args": args}


def _validated_player_action_result(payload: dict[str, Any], request_id: str) -> dict[str, Any]:
    """Keep only the public, documented result projection from the browser."""
    operation = _app_action_registry.get("player", "pause")
    public, _confirmed = _app_actions.project(operation, payload, request_id)
    return public


def _require_local_player_request(request: Request) -> None:
    """Keep music metadata and streams on the host, even when LAN is paired."""
    peer = request.client.host if request.client else ""
    if not peer or not is_loopback_host(peer):
        raise HTTPException(status_code=403, detail="IRIS Player solo está disponible localmente")


def _player_library() -> IrisPlayerLibrary:
    """Build a lightweight facade over the persisted player state."""
    return IrisPlayerLibrary()


def _player_public_snapshot(library: IrisPlayerLibrary) -> dict[str, Any]:
    """Return the UI contract without absolute or relative filesystem paths."""
    status = library.status()
    tracks = []
    for track in library.list_tracks():
        track_id = str(track.get("id") or "")
        if not track_id:
            continue
        tracks.append({
            "id": track_id,
            "title": track.get("title") or "Pista sin título",
            "artist": track.get("artist"),
            "album": track.get("album"),
            "duration_seconds": track.get("duration_seconds"),
            "extension": track.get("extension"),
            "size_bytes": track.get("size_bytes"),
            "favorite": bool(track.get("favorite", False)),
            "play_count": max(0, int(track.get("play_count") or 0)),
            "last_played": track.get("last_played"),
            "stream_url": f"/api/player/tracks/{urllib.parse.quote(track_id, safe='')}/stream",
        })
    return {
        "schema": "iris.player-library/v1",
        "id": "local",
        "configured": bool(status.get("configured")),
        "available": bool(status.get("available")),
        "folder_name": status.get("folder_name"),
        "count": len(tracks),
        "favorite_count": int(status.get("favorite_count") or 0),
        "scanned_at": status.get("refreshed_at"),
        "supported_extensions": status.get("supported_extensions") or [],
        "tracks": tracks,
        "playlists": [],
    }


def _player_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LibraryNotConfiguredError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, InvalidLibraryRootError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, TrackNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, UnsafeTrackPathError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, IrisPlayerError):
        return HTTPException(status_code=400, detail=str(exc))
    logger.exception("Unexpected IRIS Player failure")
    return HTTPException(status_code=500, detail="IRIS Player no pudo completar la operación")


def _pick_music_directory(initial_directory: str = "") -> str:
    """Open a native directory chooser only after the local user requests it."""
    root = None
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        options: dict[str, Any] = {
            "title": "Selecciona la carpeta de música para IRIS Player",
            "mustexist": True,
        }
        if initial_directory and Path(initial_directory).is_dir():
            options["initialdir"] = initial_directory
        return str(filedialog.askdirectory(parent=root, **options) or "")
    except Exception as exc:
        raise IrisPlayerError(
            "El selector nativo no está disponible. Usa la ruta manual avanzada."
        ) from exc
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


@app.get("/api/player/library")
async def player_library_snapshot(request: Request) -> JSONResponse:
    _require_local_player_request(request)
    try:
        payload = await asyncio.to_thread(_player_public_snapshot, _player_library())
    except Exception as exc:
        raise _player_http_error(exc) from exc
    return JSONResponse(payload, headers={"Cache-Control": "no-store, private"})


@app.post("/api/player/folder")
async def player_configure_folder(payload: PlayerFolderRequest, request: Request) -> JSONResponse:
    _require_local_player_request(request)
    library = _player_library()
    try:
        await asyncio.to_thread(library.configure_library, payload.path)
        await asyncio.to_thread(library.refresh)
        snapshot = await asyncio.to_thread(_player_public_snapshot, library)
    except Exception as exc:
        raise _player_http_error(exc) from exc
    return JSONResponse(snapshot, headers={"Cache-Control": "no-store, private"})


@app.post("/api/player/folder/pick")
async def player_pick_folder(request: Request) -> JSONResponse:
    _require_local_player_request(request)
    library = _player_library()
    try:
        selected = await asyncio.to_thread(_pick_music_directory)
        if not selected:
            return JSONResponse({"cancelled": True})
        await asyncio.to_thread(library.configure_library, selected)
        await asyncio.to_thread(library.refresh)
        snapshot = await asyncio.to_thread(_player_public_snapshot, library)
    except Exception as exc:
        raise _player_http_error(exc) from exc
    return JSONResponse(snapshot, headers={"Cache-Control": "no-store, private"})


@app.post("/api/player/refresh")
async def player_refresh_library(request: Request) -> JSONResponse:
    _require_local_player_request(request)
    library = _player_library()
    try:
        await asyncio.to_thread(library.refresh)
        snapshot = await asyncio.to_thread(_player_public_snapshot, library)
    except Exception as exc:
        raise _player_http_error(exc) from exc
    return JSONResponse(snapshot, headers={"Cache-Control": "no-store, private"})


@app.post("/api/player/tracks/{track_id}/favorite")
async def player_set_favorite(
    track_id: str,
    payload: PlayerFavoriteRequest,
    request: Request,
) -> JSONResponse:
    _require_local_player_request(request)
    library = _player_library()
    try:
        await asyncio.to_thread(library.set_favorite, track_id, payload.favorite)
        snapshot = await asyncio.to_thread(_player_public_snapshot, library)
    except Exception as exc:
        raise _player_http_error(exc) from exc
    return JSONResponse({"status": "saved", "library": snapshot})


@app.post("/api/player/tracks/{track_id}/played")
async def player_record_play(track_id: str, request: Request) -> JSONResponse:
    _require_local_player_request(request)
    try:
        track = await asyncio.to_thread(_player_library().record_play, track_id)
    except Exception as exc:
        raise _player_http_error(exc) from exc
    return JSONResponse({
        "status": "saved",
        "track_id": track.get("id"),
        "play_count": track.get("play_count", 0),
        "last_played": track.get("last_played"),
    })


@app.post("/api/player/actions")
async def player_execute_action(payload: PlayerActionRequest, request: Request) -> JSONResponse:
    """Deliver one validated command to exactly one active local Player surface."""
    _require_local_player_request(request)
    intent = _validated_player_action(payload)
    result = await _app_actions.execute(intent)
    return JSONResponse(result, headers={"Cache-Control": "no-store, private"})


class AppActionRequest(BaseModel):
    """iris.app-intent/v1 for any registered app operation (F09.4)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    contract_schema: str = Field(default="iris.app-intent/v1", alias="schema", max_length=64)
    app: str = Field(min_length=1, max_length=48)
    action: str = Field(min_length=1, max_length=48)
    args: dict[str, Any] = Field(default_factory=dict)


def _require_local_app_actions(request: Request) -> None:
    """App actions come from the local Hermes adapter, never from the LAN."""
    peer = request.client.host if request.client else ""
    if not peer or not is_loopback_host(peer):
        raise HTTPException(status_code=403, detail="Las acciones de apps solo están disponibles localmente")


@app.get("/api/app-actions/catalog")
async def app_actions_catalog(request: Request) -> JSONResponse:
    """The operations Hermes may ask for: names, effect and arguments only."""
    _require_local_app_actions(request)
    await _app_extensions.reconcile()
    return JSONResponse(
        {"schema": "iris.app-actions/v1", "operations": _app_action_registry.catalog()},
        headers={"Cache-Control": "no-store, private"},
    )


@app.post("/api/app-actions")
async def app_actions_execute(payload: AppActionRequest, request: Request) -> JSONResponse:
    """Validate, authorize and run one registered app operation."""
    _require_local_app_actions(request)
    await _app_extensions.reconcile()
    try:
        result = await _app_actions.execute({
            "schema": payload.contract_schema, "app": payload.app, "action": payload.action, "args": payload.args,
        })
    except OperationRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return JSONResponse(result, headers={"Cache-Control": "no-store, private"})


class VisualRequestBody(BaseModel):
    """iris.visual-request/v1: Iris asks to look once in the turn that offered the code."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    contract_schema: str = Field(default=visual_requests.SCHEMA, alias="schema", max_length=64)
    turn: str = Field(min_length=16, max_length=64)
    source: str = Field(min_length=1, max_length=16)


@app.post("/api/visual-requests")
async def visual_request_execute(payload: VisualRequestBody, request: Request) -> JSONResponse:
    """The skill's ``visual_request.py``: a photo or capture, answered with its description.

    Loopback is not proof of origin (Telegram and automations run on this PC
    too): the one-time code of a local turn is (core.visual_requests).
    """
    peer = request.client.host if request.client else ""
    if not peer or not is_loopback_host(peer):
        raise HTTPException(status_code=403, detail="Las imágenes solo se piden desde este equipo.")
    if payload.contract_schema != visual_requests.SCHEMA or payload.source not in visual_requests.SOURCES:
        raise HTTPException(status_code=422, detail="Solicitud de imagen no compatible.")
    result = await _run_visual_request(payload.turn, payload.source)
    return JSONResponse(result, headers={"Cache-Control": "no-store, private"})


class BackgroundTaskBody(BaseModel):
    """iris.background-task/v1: Iris leaves one task working while the conversation goes on."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    contract_schema: str = Field(default=background_tasks.SCHEMA, alias="schema", max_length=64)
    turn: str = Field(min_length=16, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    task: str = Field(min_length=1, max_length=8000)


@app.post("/api/background-tasks")
async def background_task_launch(payload: BackgroundTaskBody, request: Request) -> JSONResponse:
    """The skill's ``background_task.py``: answers once Hermes accepted the run, never after it ends.

    As with images, loopback is not proof of origin: the one-time code of a
    local turn is (core.background_tasks).
    """
    peer = request.client.host if request.client else ""
    if not peer or not is_loopback_host(peer):
        raise HTTPException(status_code=403, detail="Las tareas en segundo plano solo se inician desde este equipo.")
    if payload.contract_schema != background_tasks.SCHEMA:
        raise HTTPException(status_code=422, detail="Solicitud de tarea no compatible.")
    result = await _launch_background_task(payload.turn, payload.title, payload.task)
    return JSONResponse(result, headers={"Cache-Control": "no-store, private"})


@app.get("/api/player/tracks/{track_id}/stream")
async def player_stream_track(track_id: str, request: Request) -> FileResponse:
    _require_local_player_request(request)
    try:
        track = await asyncio.to_thread(_player_library().resolve_track, track_id)
    except Exception as exc:
        raise _player_http_error(exc) from exc
    return FileResponse(
        path=track,
        filename=track.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store, private"},
    )


# ── Gateway control (Hermes lifecycle from the avatar panel) ─────
#
# After a reboot Hermes is down by default. The wizard knows how to
# bring it up but launching the wizard just to click one button is
# annoying. We expose status + start endpoints here so the avatar's
# side panel has a one-click "Activar Gateway" affordance.
#
# Start delegates to ``wizard.app.start_gateway`` (lazy import) so
# there's a single source of truth for pre-flight checks, env sync,
# log rotation and liveness polling. Status is a cheap TCP probe of
# the api-server port — no third-party deps.


@app.get("/api/animation-catalog")
def animation_catalog() -> JSONResponse:
    """Diagnostic: return the animation block that's actually being
    injected into the LLM system prompt right now.

    Useful when IRIS claims she doesn't know about an animation —
    pinging this endpoint shows whether the catalog is reaching the
    prompt or the stale-import issue is at play (in which case the
    list here will be correct but IRIS's running process is still
    using the old code, so a restart of ``python -m core.agent`` is
    needed for her to pick it up).
    """
    from core.animations import (
        format_animation_catalog_for_prompt,
        get_available_animations,
    )
    clips = get_available_animations()
    return JSONResponse({
        "count": len(clips),
        "clips": clips,
        "prompt_block": format_animation_catalog_for_prompt(),
    })


@app.get("/api/gateway-status")
async def gateway_status() -> JSONResponse:
    """Cheap liveness probe of Hermes' api-server."""
    runtime = runtime_policy.get_runtime()
    import socket

    cfg = IRISConfig.load()
    configured_url = cfg.hermes_url or "http://127.0.0.1:8642"
    if runtime.packaged:
        from wizard.health_check import check_gateway_health

        health = await check_gateway_health(configured_url)
        healthy = bool(health.get("ok"))
        return JSONResponse({
            "running": healthy, "healthy": healthy, "managed": True,
            "remote": False, "port": None, "detail": health.get("detail", ""),
            "runtime": runtime_policy.runtime_public_info(),
        })
    if not is_loopback_url(configured_url):
        remote = await asyncio.to_thread(_hermes_api_request, "GET", "/health", timeout=5)
        healthy = bool(remote.get("ok"))
        return JSONResponse({
            "running": healthy,
            "healthy": healthy,
            "managed": False,
            "remote": True,
            "port": None,
            "detail": (
                "Hermes remoto saludable"
                if healthy else
                "Hermes remoto no responde; revisa URL, token y disponibilidad"
            ),
            "health_url": remote.get("url", ""),
            "health_status": remote.get("status"),
            "health_body": "",
            "health_error": remote.get("error", ""),
        })

    # Resolve the configured api-server port. Hermes defaults to 8642
    # but the user might have changed it via ~/.hermes/.env. Best-
    # effort: fall back to 8642 if the file isn't readable.
    api_port = 8642
    try:
        from wizard.hermes_config import _read_env_file
        env = await asyncio.to_thread(_read_env_file)
        api_port = int(env.get("API_SERVER_PORT") or 8642)
    except Exception:
        pass

    def _probe_tcp() -> bool:
        try:
            with socket.create_connection(("127.0.0.1", api_port), timeout=0.4):
                return True
        except OSError:
            return False

    def _probe_health() -> dict[str, Any]:
        import urllib.error
        import urllib.request

        url = f"http://127.0.0.1:{api_port}/health"
        try:
            with urllib.request.urlopen(url, timeout=0.8) as resp:
                body = resp.read(500).decode("utf-8", errors="replace")
                return {
                    "healthy": 200 <= int(resp.status) < 300,
                    "health_url": url,
                    "health_status": int(resp.status),
                    "health_body": body,
                    "health_error": "",
                }
        except urllib.error.HTTPError as exc:
            return {
                "healthy": False,
                "health_url": url,
                "health_status": int(exc.code),
                "health_body": "",
                "health_error": str(exc),
            }
        except Exception as exc:
            return {
                "healthy": False,
                "health_url": url,
                "health_status": None,
                "health_body": "",
                "health_error": str(exc),
            }

    running = await asyncio.to_thread(_probe_tcp)
    health = await asyncio.to_thread(_probe_health) if running else {
        "healthy": False,
        "health_url": f"http://127.0.0.1:{api_port}/health",
        "health_status": None,
        "health_body": "",
        "health_error": "port closed",
    }
    detail = (
        "Hermes saludable"
        if health.get("healthy")
        else "Puerto abierto, pero health no responde"
        if running
        else "Hermes esta apagado"
    )
    return JSONResponse({
        "running": running,
        "managed": True,
        "remote": False,
        "port": api_port,
        "detail": detail,
        **health,
    })


@app.post("/api/start-gateway")
async def start_gateway_proxy() -> JSONResponse:
    """Start Hermes' gateway without opening the wizard.

    Delegates to ``wizard.app.start_gateway`` — that function owns the
    pre-flight credential checks, env sync, subprocess launch and
    90s liveness wait. Returning its JSONResponse directly preserves
    the error codes (``missing_api_key`` / ``gateway_crashed`` / etc.)
    so the panel UI can show useful messages.

    Import is lazy because ``wizard.app`` builds its own FastAPI app
    at import time — we don't want that side-effect during normal
    IRIS startup, only when the user clicks the button.
    """
    runtime_policy.get_runtime()
    try:
        from wizard.app import start_gateway as _wizard_start
    except Exception as e:
        return JSONResponse({
            "status": "error",
            "code": "wizard_import_failed",
            "message": (
                "No pude cargar el módulo del wizard para arrancar Hermes. "
                f"Detalle: {e}"
            ),
        }, status_code=500)
    return await _wizard_start()


@app.post("/api/stop-gateway")
async def stop_gateway_proxy() -> JSONResponse:
    """Stop Hermes' gateway through the wizard helper."""
    runtime_policy.get_runtime()
    try:
        from wizard.app import _stop_running_gateway
    except Exception as e:
        return JSONResponse({
            "status": "error",
            "code": "wizard_import_failed",
            "message": f"No pude cargar el modulo del wizard para detener Hermes. Detalle: {e}",
        }, status_code=500)
    info = await _stop_running_gateway()
    if info.get("blocked"):
        return JSONResponse({"status": "error", "code": "gateway_runtime_conflict",
                             "message": info.get("detail"), **info}, status_code=409)
    return JSONResponse({
        "status": "success" if info.get("stopped") else "warning",
        "message": info.get("detail") or "Gateway detenido.",
        **info,
    })


@app.post("/api/restart-gateway")
async def restart_gateway_proxy() -> JSONResponse:
    """Restart Hermes' gateway through the wizard helper."""
    runtime_policy.get_runtime()
    try:
        from wizard.app import restart_gateway as _wizard_restart
    except Exception as e:
        return JSONResponse({
            "status": "error",
            "code": "wizard_import_failed",
            "message": f"No pude cargar el modulo del wizard para reiniciar Hermes. Detalle: {e}",
        }, status_code=500)
    return await _wizard_restart()


@app.get("/api/gateway-logs")
async def gateway_logs(limit: int = 120) -> JSONResponse:
    """Return the tail of IRIS/Hermes gateway logs."""
    runtime_policy.get_runtime()
    try:
        from core.config import get_iris_home
        from core.workspace_sync import get_hermes_home

        candidates: list[tuple[str, Path]] = [
            ("IRIS", get_iris_home() / "logs" / "hermes-gateway.log"),
        ]
        try:
            hermes_home = get_hermes_home()
            candidates.extend([
                ("Hermes", hermes_home / "logs" / "gateway.log"),
                ("Hermes", hermes_home / "logs" / "hermes-gateway.log"),
                ("Hermes", hermes_home / "gateway.log"),
            ])
        except Exception:
            pass

        max_lines = max(20, min(int(limit or 120), 500))
        sources = []
        combined: list[str] = []
        for label, log_path in candidates:
            source: dict[str, Any] = {
                "label": label,
                "path": str(log_path),
                "exists": log_path.exists(),
            }
            if log_path.exists():
                raw = await asyncio.to_thread(log_path.read_text, encoding="utf-8", errors="replace")
                lines = raw.splitlines()[-max_lines:]
                stat = log_path.stat()
                source.update({
                    "line_count": len(lines),
                    "mtime_ms": int(stat.st_mtime * 1000),
                })
                combined.extend(f"[{label}] {line}" for line in lines)
            sources.append(source)

        lines = combined[-max_lines:]
        if not any(source.get("exists") for source in sources):
            return JSONResponse({
                "success": True,
                "exists": False,
                "path": sources[0]["path"],
                "sources": sources,
                "lines": [],
                "text": "",
                "message": "Aun no existe log del gateway.",
            })
        return JSONResponse({
            "success": True,
            "exists": True,
            "path": next((source["path"] for source in sources if source.get("exists")), sources[0]["path"]),
            "sources": sources,
            "lines": lines,
            "text": "\n".join(lines),
            "mtime_ms": max((source.get("mtime_ms") or 0 for source in sources), default=0),
        })
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read Hermes gateway logs: {exc}") from exc


# ── Reasoning-effort selector (Hermes config bridge) ─────────────
#
# Hermes 0.20 accepts per-request reasoning through ``model_options``;
# ``extra_body`` is not that contract. These endpoints still persist the
# user's default in config.yaml so CLI/TUI/desktop and older 0.19 runtimes
# share the same setting. The bridge negotiates ``features.model_options``
# before applying a one-turn override (notably reasoning=none for images).
#
# These endpoints let the side-panel pill drive the persisted default. We
# preserve the YAML file's formatting/comments by patching the single
# `reasoning_effort:` line in place — full pyyaml re-emit would lose
# the user's section ordering and comments. If the key is missing we
# inject it under `agent:`.

_VALID_REASONING_EFFORTS = ("none", "low", "medium", "high")


def _hermes_config_path() -> Path | None:
    """Return the absolute path to Hermes' config.yaml, or None if we
    can't locate Hermes' home reliably."""
    try:
        from core.workspace_sync import get_hermes_home
        home = get_hermes_home()
        cfg = home / "config.yaml"
        return cfg if cfg.exists() else None
    except Exception as e:
        logger.warning("Could not locate Hermes config: %s", e)
        return None


def _read_current_reasoning_effort() -> str:
    """Read the current `agent.reasoning_effort` value from Hermes
    config.yaml. Defaults to 'medium' if not set or unreadable — that
    matches the upstream Hermes default."""
    cfg = _hermes_config_path()
    if not cfg:
        return "medium"
    try:
        import re
        txt = cfg.read_text(encoding="utf-8", errors="ignore")
        agent = re.search(r"^agent:[ \t]*(?:#.*)?$", txt, re.M)
        if not agent:
            return "medium"
        block_end = re.search(r"^\S", txt[agent.end() + 1:], re.M)
        end = agent.end() + 1 + block_end.start() if block_end else len(txt)
        m = re.search(r"^[ \t]+reasoning_effort:\s*([A-Za-z]+)\s*$", txt[agent.end():end], re.M)
        if m:
            v = m.group(1).strip().lower()
            return v if v in _VALID_REASONING_EFFORTS else "medium"
    except Exception as e:
        logger.warning("Failed to read reasoning_effort: %s", e)
    return "medium"


def _write_reasoning_effort(value: str) -> tuple[bool, str]:
    """Patch the `agent.reasoning_effort:` line in config.yaml in place.
    Returns (ok, detail). Preserves comments + section ordering."""
    if value not in _VALID_REASONING_EFFORTS:
        return False, f"valor inválido: {value!r}"
    cfg = _hermes_config_path()
    if not cfg:
        return False, "No encuentro ~/.hermes/config.yaml (¿Hermes instalado?)"
    try:
        import re

        from core.file_lock import interprocess_file_lock

        with interprocess_file_lock(cfg.parent / ".iris-config.lock"):
            original = cfg.read_text(encoding="utf-8")
            new_line = f"  reasoning_effort: {value}"
            agent = re.search(r"^agent:[ \t]*(?:#.*)?$", original, re.M)
            if agent:
                next_root = re.search(r"^\S", original[agent.end() + 1:], re.M)
                end = agent.end() + 1 + next_root.start() if next_root else len(original)
                block = original[agent.end():end]
                patched_block, count = re.subn(
                    r"^[ \t]+reasoning_effort:\s*[A-Za-z]+\s*$",
                    new_line,
                    block,
                    count=1,
                    flags=re.M,
                )
                if count == 0:
                    patched_block = "\n" + new_line + block
                patched = original[:agent.end()] + patched_block + original[end:]
            else:
                patched = original.rstrip() + f"\nagent:\n{new_line}\n"

            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=cfg.parent,
                    prefix=f".{cfg.name}.iris-reasoning-", suffix=".tmp", delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    handle.write(patched)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, cfg)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink(missing_ok=True)
        return True, "guardado"
    except Exception as e:
        logger.exception("Failed to patch reasoning_effort")
        return False, f"error escribiendo config: {e}"


@app.get("/api/hermes-reasoning")
async def get_hermes_reasoning() -> JSONResponse:
    """GET current reasoning_effort from Hermes' config so the panel
    pill can preload the right option on page load."""
    cfg = _hermes_config_path()
    return JSONResponse({
        "effort": _read_current_reasoning_effort(),
        "config_path": str(cfg) if cfg else None,
        "config_found": cfg is not None,
    })


@app.post("/api/hermes-reasoning")
async def set_hermes_reasoning(payload: dict = Body(...)) -> JSONResponse:
    """POST {"effort": "none"|"low"|"medium"|"high"} → edit Hermes
    config.yaml in place.

    NO restart required: Hermes' api-server calls ``_load_gateway_config()``
    at the start of every chat-completion request (gateway/platforms/
    api_server.py:736), and that function caches by the file's
    ``(mtime_ns, size)`` — so the moment we save the new YAML, the
    cache key flips and the very next call re-reads from disk.

    Confirmed by inspecting ``hermes_cli/config.py:read_raw_config``
    on Hermes ≥ 0.12: the cache is invalidated automatically on file
    edit, and ``agent.reasoning_effort`` is read fresh per request
    via ``_resolve_runtime_agent_kwargs`` → ``parse_reasoning_effort``.
    """
    effort = (payload.get("effort") or "").strip().lower()
    if effort not in _VALID_REASONING_EFFORTS:
        return JSONResponse({
            "status": "error",
            "message": f"effort debe ser uno de {_VALID_REASONING_EFFORTS}",
        }, status_code=400)

    previous = _read_current_reasoning_effort()
    ok, detail = _write_reasoning_effort(effort)
    if not ok:
        return JSONResponse({
            "status": "error",
            "message": detail,
        }, status_code=500)

    return JSONResponse({
        "status": "ok",
        "previous_effort": previous,
        "current_effort": effort,
        # Always False — Hermes hot-reloads via mtime cache. Kept in
        # the response shape for backwards-compat with any caller
        # that still inspects it.
        "needs_restart": False,
        "hot_reload": True,
        "detail": (
            f"Aplicado: reasoning_effort = {effort}. Hermes lo lee en "
            f"la próxima petición sin reiniciar."
            if previous != effort
            else f"Ya estaba en {effort}."
        ),
    })


# ── Memory Panel API (face UI's 🧠 modal) ────────────────────────
#
# Five endpoints powering the floating modal in face/ that lets the
# user remind Hermes of past turns, search/filter local memory,
# and clear local + remote sessions.
#
# Why on this server (not the wizard): the modal lives in the avatar
# page served by this app, and needs the running agent's memory
# instance + bridge. Keeping it here means zero extra processes.


def _agent_or_503():
    """Return the agent or raise 503 — the panel is useless without it."""
    agent = getattr(app.state, "agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    return agent


@app.get("/api/memory/stats")
async def memory_stats() -> JSONResponse:
    """Total turn count + time range. Powers the modal header."""
    agent = _agent_or_503()
    stats = await agent.memory.memory_stats(session_id=agent._session_id)
    return JSONResponse(stats)


@app.get("/api/memory/list")
async def memory_list(
    from_ts: float | None = None,
    to_ts:   float | None = None,
    q:       str | None = None,
    limit:   int = 200,
    offset:  int = 0,
    all_sessions: bool = False,
) -> JSONResponse:
    """Filtered + paginated turn list. `all_sessions=true` includes
    everything, otherwise scopes to the agent's current session_id."""
    agent = _agent_or_503()
    sid = None if all_sessions else agent._session_id
    turns = await agent.memory.query_turns(
        session_id=sid,
        from_ts=from_ts,
        to_ts=to_ts,
        keyword=q,
        limit=max(1, min(limit, 500)),
        offset=max(0, offset),
    )
    total = await agent.memory.count_turns(
        session_id=sid, from_ts=from_ts, to_ts=to_ts, keyword=q,
    )
    return JSONResponse({"turns": turns, "total": total})


@app.post("/api/memory/recall")
async def memory_recall(payload: dict) -> JSONResponse:
    """Package selected turns into a single priming message and send
    it to Hermes as a reminder. The chosen turns are bundled into a
    structured summary block — much more token-efficient than
    replaying them as separate user/assistant pairs."""
    agent = _agent_or_503()
    raw_ids = payload.get("turn_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="turn_ids required")
    try:
        ids = [int(i) for i in raw_ids]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="turn_ids must be integers")

    turns = await agent.memory.get_turns_by_ids(ids)
    if not turns:
        raise HTTPException(status_code=404, detail="No matching turns")

    # Build the structured priming summary. We walk the turns in
    # chronological order and label by role with a short relative
    # timestamp the LLM can use for "earlier" / "yesterday" cues.
    import datetime as _dt
    lines: list[str] = []
    for t in turns:
        when = _dt.datetime.fromtimestamp(t["timestamp"]).strftime("%Y-%m-%d %H:%M")
        speaker = "Tú" if t["role"] == "IRIS" else (
            "El usuario" if t["role"] == "user" else t["role"]
        )
        # One line per turn — keep it tight.
        text = (t["content"] or "").replace("\n", " ").strip()
        if len(text) > 280:
            text = text[:277] + "…"
        lines.append(f"• [{when}] {speaker}: \"{text}\"")
    summary_block = "\n".join(lines)

    priming = (
        f"[Recordatorio interno — no es un mensaje nuevo del usuario]\n"
        f"El usuario quiere que tengas presente este intercambio anterior:\n\n"
        f"{summary_block}\n\n"
        f"Confirma brevemente con UNA frase corta y amable que ya lo "
        f"tienes presente (p. ej. \"Listo, lo recuerdo\"). NO repitas el "
        f"contenido, NO entres en detalles, NO uses listas. Después la "
        f"conversación seguirá normal."
    )

    # Push through the same _think pipeline so Hermes's session is
    # primed AND IRIS acknowledges verbally via TTS / animation.
    try:
        response = await agent._think(priming)
    except Exception as e:
        logger.exception("memory_recall: _think failed")
        raise HTTPException(status_code=500, detail=f"Recall failed: {e}")

    # Speak the acknowledgement (TTS + animation + lipsync).
    try:
        await agent._respond(response)
    except Exception as e:
        logger.warning("memory_recall: _respond failed: %s", e)

    return JSONResponse({
        "ok":           True,
        "turns_count":  len(turns),
        "ack":          response.get("texto_hablado", ""),
    })


@app.post("/api/memory/delete-selected")
async def memory_delete_selected(payload: dict) -> JSONResponse:
    """Delete a specific list of turns by ID. Used by the modal's
    'Borrar seleccionados' button."""
    agent = _agent_or_503()
    raw_ids = payload.get("turn_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="turn_ids required")
    try:
        ids = [int(i) for i in raw_ids]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="turn_ids must be integers")
    deleted = await agent.memory.delete_turns_by_ids(ids)
    return JSONResponse({"ok": True, "deleted": deleted})


@app.delete("/api/memory/clear")
async def memory_clear(scope: str = "session", confirm: bool = False) -> JSONResponse:
    """Wipe local SQLite history. `scope=session` only the current
    session, `scope=all` everything. Requires `confirm=true` so a
    stray fetch can't nuke the DB."""
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true required")
    if scope not in ("session", "all"):
        raise HTTPException(status_code=400, detail="scope must be 'session' or 'all'")
    agent = _agent_or_503()
    sid = agent._session_id if scope == "session" else None
    deleted = await agent.memory.clear_history(session_id=sid)
    return JSONResponse({"ok": True, "deleted": deleted, "scope": scope})


@app.post("/api/memory/reset-hermes")
async def memory_reset_hermes(payload: dict = Body(default={})) -> JSONResponse:
    """Reset Hermes' server-side session store. Local memory is untouched.

    Uses ``hermes sessions prune --older-than 0 --yes`` which deletes
    every stored session — the closest equivalent to "wipe my chat
    history" in Hermes' CLI surface. We deliberately do NOT call
    ``hermes memory reset`` here because that wipes the persistent
    MEMORY.md / USER.md files (IRIS's personality + user profile),
    which is a different and more destructive operation than clearing
    persisted sessions. Verified against Hermes Agent v0.20.1's CLI:
    ``hermes sessions {list,export,delete,prune,stats,rename,browse}``.
    """
    if payload.get("confirm_all") is not True:
        raise HTTPException(status_code=400, detail="confirm_all=true required")
    if not _resolve_hermes_cli():
        raise HTTPException(status_code=500, detail="Hermes CLI no está instalado.")
    # `--older-than 0` matches every session; `--yes` skips the interactive
    # "Are you sure?" prompt (there is no TTY to answer it). The managed
    # interpreter and environment run the CLI, also when it is a .py script.
    result = await asyncio.to_thread(
        _run_hermes_cli_command, ["sessions", "prune", "--older-than", "0", "--yes"], timeout=60,
    )
    if result["returncode"] is None:
        raise HTTPException(status_code=504, detail=result["stderr"][:300] or "Hermes CLI no respondió.")
    if not result["ok"]:
        raise HTTPException(
            status_code=500,
            detail=f"hermes exited {result['returncode']}: {result['stderr'][:300]}",
        )
    return JSONResponse({"ok": True, "stdout": result["stdout"][:500]})


# ────────────────────────────────────────────────────────
# Skills Bridge (Fase 2.A)
# ────────────────────────────────────────────────────────
#
# Surface Hermes's installed skills to the face UI so the user can
# pick which ones IRIS is allowed to reference. Two halves:
#
#   GET /api/skills/list         — returns the catalogue (live from
#                                  `hermes skills list --json`) merged
#                                  with our enable/disable state.
#   POST /api/skills/toggle      — flip a skill's enabled flag.
#
# Persistence lives in ~/.iris/enabled_skills.json (a flat list of
# names). The agent reads this on every turn via
# `core/skills_bridge.get_enabled_skills_block()` and appends the
# resulting markdown to the system prompt. We keep the data file
# separate from config.json because skills change at Hermes's pace
# (much faster than user-facing config) and we don't want config writes
# racing with a UI checkbox click.

def _enabled_skills_path() -> Path:
    from core.config import get_iris_home
    return get_iris_home() / "enabled_skills.json"


def _read_enabled_skills() -> set[str]:
    p = _enabled_skills_path()
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(x) for x in data}
        return set()
    except (OSError, ValueError):
        return set()


def _write_enabled_skills(names: set[str]) -> None:
    p = _enabled_skills_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Sort for stable diffs — humans may inspect this file by hand.
    payload = json.dumps(sorted(names), indent=2, ensure_ascii=False)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=p.parent,
            prefix=f".{p.name}.iris-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, p)
    finally:
        if temporary and temporary.exists():
            temporary.unlink(missing_ok=True)


class SkillToggleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    enabled: StrictBool


class ContinuityProfileActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    confirm: StrictBool


class ContinuityProfileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=280)
    clone_mode: str = Field(default="blank", max_length=16)
    clone_from: str = Field(default="default", max_length=64)


class ContinuitySessionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=140)
    archived: StrictBool | None = None
    pinned: StrictBool | None = None


class ContinuitySessionResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: StrictBool


class ContinuitySessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=140)
    confirm: StrictBool


class ContinuitySessionForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=140)
    confirm: StrictBool


def _resolve_hermes_cli() -> str | None:
    """Locate the hermes executable.

    Hermes' Windows installer drops ``hermes.exe`` in
    ``%LOCALAPPDATA%\\hermes\\hermes-agent\\venv\\Scripts\\``. The
    installer also amends user PATH so ``shutil.which`` finds it
    after a session refresh. We front-load shutil.which (which walks
    PATHEXT) and pass the full path to ``asyncio.create_subprocess_exec``
    — a bare ``"hermes"`` arg fails on Windows because asyncio doesn't
    walk PATHEXT for non-.exe matches.

    Returns None if the CLI isn't installed.
    """
    from core.hermes_runtime import resolve_hermes_cli as _resolve
    return _resolve()


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _run_local_command(args: list[str], *, cwd: Path | None = None, timeout: int = 25) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
        }
    except FileNotFoundError:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": f"{args[0]} no esta instalado."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "Tiempo de espera agotado."}


def _hermes_api_base_url() -> str:
    cfg = IRISConfig.load()
    value = (cfg.hermes_url or "http://127.0.0.1:8642").rstrip("/")
    if not is_safe_service_url(value):
        raise ValueError("Hermes remoto requiere HTTPS y una URL sin credenciales embebidas.")
    return value


def _hermes_api_token() -> str:
    cfg = IRISConfig.load()
    if cfg.hermes_api_key:
        return cfg.hermes_api_key
    if not is_loopback_url(cfg.hermes_url):
        return ""
    if os.environ.get("API_SERVER_KEY"):
        return os.environ["API_SERVER_KEY"]
    try:
        from core.hermes_bridge import _read_api_server_key_from_hermes_env
        return _read_api_server_key_from_hermes_env()
    except Exception:
        return ""


def _hermes_api_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int = 12,
    headers: dict[str, str] | None = None,
    token: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """``headers`` adds request headers (F06: Idempotency-Key); ``token`` replaces the
    default profile's key (a named profile's own key); ``base_url`` sends to an
    endpoint resolved earlier instead of reading the configuration again (T02)."""
    base = base_url if base_url is not None else _hermes_api_base_url()
    query = urllib.parse.urlencode({
        key: value
        for key, value in (params or {}).items()
        if value is not None and value != ""
    })
    url = f"{base}{path}"
    if query:
        url = f"{url}?{query}"

    body = None
    extra_headers = headers
    headers = {"Accept": "application/json", **(extra_headers or {})}
    token = token if token is not None else _hermes_api_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw.strip():
                return {"ok": True, "status": int(resp.status), "data": None, "url": url}
            try:
                data = json.loads(raw)
            except ValueError:
                data = {"raw": raw}
            return {"ok": True, "status": int(resp.status), "data": data, "url": url}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        detail = raw
        try:
            parsed = json.loads(raw)
            detail = parsed.get("detail") or parsed.get("error") or raw
        except ValueError:
            pass
        return {"ok": False, "status": int(exc.code), "error": detail or str(exc), "url": url}
    except Exception as exc:
        return {"ok": False, "status": None, "error": str(exc), "url": url}


def _unwrap_hermes_list(data: Any, *keys: str) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
        nested = data.get("data")
        if isinstance(nested, list):
            return nested
        if isinstance(nested, dict):
            for key in keys:
                value = nested.get(key)
                if isinstance(value, list):
                    return value
    return []


def _normalize_run(item: dict[str, Any]) -> dict[str, Any]:
    run_id = item.get("id") or item.get("run_id") or item.get("uuid") or ""
    status = str(item.get("status") or item.get("state") or item.get("phase") or "unknown")
    title = (
        item.get("title")
        or item.get("name")
        or item.get("task")
        or item.get("prompt_preview")
        or item.get("goal")
        or run_id
        or "Hermes run"
    )
    agent = item.get("agent") or item.get("profile") or item.get("worker") or item.get("assignee") or "Hermes"
    created_at = item.get("created_at") or item.get("created") or item.get("started_at") or item.get("timestamp")
    updated_at = item.get("updated_at") or item.get("ended_at") or item.get("completed_at") or item.get("last_event_at")
    return {
        "id": str(run_id),
        "title": str(title),
        "status": status,
        "agent": str(agent),
        "created_at": created_at,
        "updated_at": updated_at,
        "summary": result_text(item.get("summary") or item.get("output") or item.get("result") or item.get("message") or item.get("error")),
        "result": result_text(item.get("output") or item.get("result") or item.get("summary") or item.get("message") or item.get("error")),
        "raw": item,
    }


def _hermes_capability_declared(
    payload: dict[str, Any],
    *,
    feature_names: tuple[str, ...] = (),
    endpoint_names: tuple[str, ...] = (),
) -> bool:
    """Return true only when Hermes explicitly advertises a capability.

    Older IRIS builds treated any non-empty capabilities document as proof
    that the Runs API existed. That enabled Async Agents against Hermes
    versions which merely advertised unrelated chat or session features.
    """
    feature_maps = [payload]
    for key in ("features", "supports", "capabilities"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            feature_maps.append(candidate)
    for feature_map in feature_maps:
        for name in feature_names:
            if feature_map.get(name) is True:
                return True

    endpoints = payload.get("endpoints")
    if not isinstance(endpoints, dict):
        return False
    for name in endpoint_names:
        declared = endpoints.get(name)
        if isinstance(declared, str) and declared.strip():
            return True
        if isinstance(declared, dict) and declared.get("enabled", True) is not False:
            path = str(declared.get("path") or "").strip()
            if path:
                return True
    return False


def _hermes_capabilities_snapshot() -> dict[str, Any]:
    health = _hermes_api_request("GET", "/health", timeout=5)
    caps = _hermes_api_request("GET", "/v1/capabilities", timeout=8)
    data = caps.get("data") if caps.get("ok") else {}
    features = data if isinstance(data, dict) else {}
    health_ok = bool(health.get("ok"))
    capabilities_ok = bool(caps.get("ok"))
    return {
        "success": health_ok and capabilities_ok,
        "reachable": health_ok,
        "capabilities_available": capabilities_ok,
        "api_url": _hermes_api_base_url(),
        "health": health,
        "capabilities": features,
        "supports": {
            "runs": capabilities_ok and _hermes_capability_declared(
                features,
                feature_names=("runs", "run_submission", "async_runs"),
                endpoint_names=("runs",),
            ),
            "capabilities": capabilities_ok,
            "jobs": capabilities_ok and _hermes_capability_declared(
                features,
                feature_names=("jobs", "jobs_admin"),
                endpoint_names=("jobs",),
            ),
            "sessions": capabilities_ok and _hermes_capability_declared(
                features,
                feature_names=("sessions", "session_resources"),
                endpoint_names=("sessions",),
            ),
            "toolsets": capabilities_ok and _hermes_capability_declared(
                features,
                feature_names=("toolsets",),
                endpoint_names=("toolsets",),
            ),
        },
        "error": "" if health_ok and capabilities_ok else str(
            caps.get("error") or health.get("error") or "Hermes api-server no responde."
        ),
    }


def _short_ref(value: str | None) -> str:
    return (value or "")[:12]


def _git_update_snapshot(path: Path, label: str, *, version: str | None = None) -> dict[str, Any]:
    runtime_policy.require_runtime_capability("source_update")
    item = {
        "id": label.lower().replace(" ", "-"),
        "label": label,
        "path": str(path),
        "version": version or "",
        "current_ref": "",
        "latest_ref": "",
        "status": "unknown",
        "can_update": False,
        "detail": "No se pudo verificar el repositorio.",
    }
    if not path.exists():
        item.update(status="missing", detail="No se encontro la instalacion local.")
        return item
    if not (path / ".git").exists():
        item.update(detail="Instalacion local sin repositorio Git detectable.")
        return item
    if not shutil.which("git"):
        item.update(detail="Git no esta disponible en PATH.")
        return item

    current = _run_local_command(["git", "rev-parse", "HEAD"], cwd=path, timeout=8)
    if not current["ok"]:
        item.update(detail=current["stderr"] or "No se pudo leer el commit local.")
        return item
    current_ref = current["stdout"].splitlines()[0].strip()

    local_status = _run_local_command(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=path,
        timeout=8,
    )
    if not local_status["ok"]:
        item.update(
            status="error",
            current_ref=_short_ref(current_ref),
            detail=local_status["stderr"] or "No se pudo comprobar si hay cambios locales.",
        )
        return item
    has_local_changes = bool((local_status["stdout"] or "").strip())

    branch = _run_local_command(["git", "branch", "--show-current"], cwd=path, timeout=8)
    branch_name = (branch["stdout"] or "").splitlines()[0].strip() if branch["ok"] else ""
    remote_target = f"refs/heads/{branch_name}" if branch_name else "HEAD"
    latest = _run_local_command(["git", "ls-remote", "origin", remote_target], cwd=path, timeout=20)
    if not latest["ok"] or not latest["stdout"]:
        item.update(
            current_ref=_short_ref(current_ref),
            detail=latest["stderr"] or "No se pudo consultar el remoto.",
        )
        return item
    latest_ref = latest["stdout"].split()[0].strip()

    if current_ref == latest_ref:
        status = "current_with_local_changes" if has_local_changes else "current"
        detail = (
            "Todo esta actualizado; los cambios locales se conservaran."
            if has_local_changes else
            "Todo esta actualizado."
        )
    elif has_local_changes:
        status = "blocked_local_changes"
        detail = (
            "Hay una actualizacion, pero se bloqueo para no sobrescribir cambios locales. "
            "Guarda o confirma esos cambios antes de actualizar."
        )
    else:
        status = "update_available"
        detail = "Hay una actualizacion disponible."
    item.update(
        current_ref=_short_ref(current_ref),
        latest_ref=_short_ref(latest_ref),
        status=status,
        can_update=status == "update_available",
        has_local_changes=has_local_changes,
        detail=detail,
    )
    return item


def _hermes_version() -> str:
    from core.hermes_updates import hermes_version
    return hermes_version()


def _system_update_snapshot() -> dict[str, Any]:
    from core.hermes_updates import hermes_update_snapshot

    runtime = runtime_policy.get_runtime()
    if runtime.packaged:
        return _packaged_update_snapshot()

    items = [
        _git_update_snapshot(_project_root(), "IRIS OS", version=app.version),
        hermes_update_snapshot(),
    ]
    can_update = any(item.get("can_update") for item in items)
    blocked = any(item.get("status") == "blocked_local_changes" for item in items)
    unknown = any(item.get("status") in {"unknown", "missing", "error"} for item in items)
    if can_update:
        summary = "updates_available"
        message = (
            "Hay actualizaciones instalables; otros componentes tienen cambios locales protegidos."
            if blocked else
            "Hay actualizaciones listas para instalar."
        )
    elif blocked:
        summary = "blocked"
        message = "Hay actualizaciones, pero los cambios locales deben guardarse antes de instalarlas."
    elif unknown:
        summary = "unknown"
        message = "No pude verificar todos los componentes."
    else:
        summary = "current"
        message = "IRIS y Hermes estan actualizados."
    return {
        "ok": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "message": message,
        "can_update": can_update,
        "items": items,
        "runtime": runtime_policy.runtime_public_info(),
    }


def _packaged_update_snapshot(check_channel: bool = False) -> dict[str, Any]:
    """A08.8: Iris and its bundled Hermes update together from the Iris channel."""
    from core.iris_updates import update_snapshot

    return update_snapshot(check_channel=check_channel)


def _apply_git_update(path: Path, label: str) -> dict[str, Any]:
    runtime_policy.require_runtime_capability("source_update")
    if not path.exists() or not (path / ".git").exists():
        return {"label": label, "ok": False, "detail": "No hay repositorio Git actualizable."}
    local_status = _run_local_command(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=path,
        timeout=8,
    )
    if not local_status["ok"]:
        return {
            "label": label,
            "ok": False,
            "detail": local_status["stderr"] or "No se pudo comprobar el estado local.",
        }
    if (local_status["stdout"] or "").strip():
        return {
            "label": label,
            "ok": False,
            "detail": "Actualizacion cancelada para preservar cambios locales sin guardar.",
        }
    out = _run_local_command(["git", "pull", "--ff-only"], cwd=path, timeout=90)
    return {
        "label": label,
        "ok": bool(out["ok"]),
        "detail": out["stdout"] or out["stderr"] or ("Actualizado." if out["ok"] else "No se pudo actualizar."),
    }


@app.get("/api/system-updates/check")
async def system_updates_check(force: bool = False) -> JSONResponse:
    if runtime_policy.get_runtime().packaged:
        from core.iris_updates import job_status

        if job_status().get("active"):
            snapshot = await asyncio.to_thread(_packaged_update_snapshot, False)
            snapshot.update(summary="updating", can_update=False, can_rollback=False,
                            message="Hay una actualización en curso.")
            return JSONResponse(snapshot)
        cached = _cache_get("system:updates:packaged", 60.0, force=force)
        if cached is not None:
            return JSONResponse(cached)
        snapshot = await asyncio.to_thread(_packaged_update_snapshot, True)
        return JSONResponse(_cache_set("system:updates:packaged", snapshot))
    if _system_update_job.get("active"):
        # A second browser must not fetch/rewrite Git refs during installation.
        snapshot = dict(_system_update_job.get("snapshot") or {
            "ok": True, "items": [], "summary": "updating", "can_update": False,
        })
        snapshot.update(can_update=False, message="Hay una actualización en curso.")
        return JSONResponse(snapshot)
    cache_key = "system:updates"
    cached = _cache_get(cache_key, 60.0, force=force)
    if cached is not None:
        return JSONResponse(cached)
    try:
        out = await asyncio.to_thread(_system_update_snapshot)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not check updates: {exc}") from exc
    return JSONResponse(_cache_set(cache_key, out))


_system_update_task: asyncio.Task | None = None
_system_update_job: dict[str, Any] = {"active": False, "phase": "idle"}


async def _perform_system_update(components: set[str] | None) -> None:
    from core.hermes_updates import apply_hermes_update

    results: list[dict[str, Any]] = []
    try:
        _require_system_update_targets(components)
        before = await asyncio.to_thread(_system_update_snapshot)
        targets = {
            item["id"] for item in before.get("items", [])
            if item.get("can_update") and (components is None or item["id"] in components)
        }
        _system_update_job.update(phase="updating", message="Hermes está instalando la actualización…", snapshot=before)
        if "iris-os" in targets:
            results.append(await asyncio.to_thread(_apply_git_update, _project_root(), "IRIS OS"))
        if "hermes-agent" in targets:
            results.append(await asyncio.to_thread(apply_hermes_update))
        _system_update_job.update(phase="verifying", message="Verificando el resultado de la actualización…", results=results)
        _api_cache.pop("system:updates", None)
        after = await asyncio.to_thread(_system_update_snapshot)
        success = bool(results) and all(result.get("ok") for result in results)
        errors = [result.get("detail", "") for result in results if not result.get("ok")]
        message = (
            "Actualización completada. Reinicia IRIS Core para recargar sus integraciones."
            if success else "\n".join(errors) or "No había actualizaciones instalables."
        )
        _system_update_job.update(
            active=False, phase="completed" if success else "failed", ok=success,
            message=message, results=results, snapshot=after,
        )
    except Exception as exc:
        logger.exception("System update failed")
        _system_update_job.update(active=False, phase="failed", ok=False,
                                  message=str(exc), results=results)


@app.get("/api/system-updates/status")
async def system_updates_status() -> JSONResponse:
    if runtime_policy.get_runtime().packaged:
        from core.iris_updates import job_status

        job = job_status()
        job["snapshot"] = await asyncio.to_thread(_packaged_update_snapshot, False)
        return JSONResponse(job)
    return JSONResponse(dict(_system_update_job))


_packaged_update_task: asyncio.Task | None = None


async def _start_packaged_job(operation: str) -> JSONResponse:
    """One download/activation at a time; the helper then restarts Iris."""
    global _packaged_update_task
    from core import iris_updates

    runtime_policy.require_runtime_capability("bundled_update")
    agent = getattr(app.state, "agent", None)
    turn = getattr(agent, "_current_turn_task", None)
    if turn is not None and not turn.done():
        raise HTTPException(status_code=409, detail="Espera a que IRIS termine de responder antes de actualizar.")
    if not iris_updates.begin_job(operation):
        return JSONResponse(iris_updates.job_status(), status_code=202)
    _api_cache.pop("system:updates:packaged", None)
    worker = iris_updates.run_update_job if operation == "upgrade" else iris_updates.run_rollback_job
    _packaged_update_task = asyncio.create_task(asyncio.to_thread(worker))
    return JSONResponse(iris_updates.job_status(), status_code=202)


@app.post("/api/system-updates/rollback")
async def system_updates_rollback() -> JSONResponse:
    if not runtime_policy.get_runtime().packaged:
        raise HTTPException(status_code=404, detail="Solo la versión instalada conserva una versión anterior.")
    return await _start_packaged_job("rollback")


def _require_system_update_targets(components: set[str] | None) -> None:
    runtime_policy.get_runtime()
    targets = {"iris-os", "hermes-agent"} if components is None else components
    if not targets or not targets <= {"iris-os", "hermes-agent"}:
        raise HTTPException(status_code=400, detail="Componentes de actualización no válidos.")
    for target in sorted(targets):
        runtime_policy.require_runtime_capability(
            "source_update" if target == "iris-os" else "external_hermes_update")


@app.post("/api/system-updates/apply")
async def system_updates_apply(payload: dict | None = Body(default=None)) -> JSONResponse:
    global _system_update_task
    if runtime_policy.get_runtime().packaged:
        # Never Git or ``hermes update``: the joint channel replaces both.
        return await _start_packaged_job("upgrade")
    requested = payload.get("components") if isinstance(payload, dict) else None
    if requested is not None and (
        not isinstance(requested, list) or not requested
        or any(not isinstance(value, str) or value not in {"iris-os", "hermes-agent"} for value in requested)
    ):
        raise HTTPException(status_code=400, detail="Componentes de actualización no válidos.")
    _require_system_update_targets(set(requested) if requested is not None else None)
    if _system_update_task is not None and not _system_update_task.done():
        return JSONResponse(dict(_system_update_job), status_code=202)
    agent = getattr(app.state, "agent", None)
    turn = getattr(agent, "_current_turn_task", None)
    if turn is not None and not turn.done():
        raise HTTPException(status_code=409, detail="Espera a que IRIS termine de responder antes de actualizar.")
    # There is no await between admission and retaining the task: concurrent
    # clients share one job. Browser reloads/disconnections cannot cancel it.
    _system_update_job.clear()
    _system_update_job.update(active=True, phase="checking", ok=None,
                              message="Comprobando la actualización…")
    _system_update_task = asyncio.create_task(
        _perform_system_update(set(requested) if requested is not None else None)
    )
    return JSONResponse(dict(_system_update_job), status_code=202)


@app.get("/api/hermes/diagnostics")
async def hermes_diagnostics_snapshot(force: bool = False) -> JSONResponse:
    """Return a compact, secret-free Hermes runtime diagnostics snapshot."""
    cache_key = "hermes:diagnostics"
    cached = _cache_get(cache_key, 20.0, force=force)
    if cached is not None:
        return JSONResponse(cached)
    try:
        from core.hermes_runtime import hermes_diagnostics
        out = await asyncio.to_thread(hermes_diagnostics)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not inspect Hermes runtime: {exc}") from exc
    return JSONResponse(_cache_set(cache_key, out))


@app.get("/api/hermes/commands")
async def hermes_commands_snapshot(force: bool = False) -> JSONResponse:
    """Return the installed Hermes registry with truthful IRIS availability."""
    try:
        from core.hermes_commands import hermes_command_catalogue

        out = await asyncio.to_thread(hermes_command_catalogue, refresh=force)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not inspect Hermes commands: {exc}") from exc
    commands = out.get("commands") or []
    return JSONResponse({
        "success": True,
        **out,
        "count": len(commands),
        "available_count": sum(1 for command in commands if command.get("available")),
    })


@app.get("/api/hermes/doctor")
async def hermes_doctor_snapshot(force: bool = False) -> JSONResponse:
    """Return a full, secret-free Hermes setup/health report."""
    cache_key = "hermes:doctor"
    cached = _cache_get(cache_key, 45.0, force=force)
    if cached is not None:
        return JSONResponse(cached)
    try:
        from core.hermes_doctor import hermes_doctor

        out = await asyncio.to_thread(hermes_doctor)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not run Hermes Doctor: {exc}") from exc
    return JSONResponse(_cache_set(cache_key, out))


@app.get("/api/hermes/compatibility")
async def hermes_compatibility_snapshot(force: bool = False) -> JSONResponse:
    """Return IRIS' secret-free Hermes compatibility assessment."""
    cache_key = "hermes:compatibility"
    cached = _cache_get(cache_key, 30.0, force=force)
    if cached is not None:
        return JSONResponse(cached)
    try:
        from core.hermes_compatibility import hermes_compatibility

        out = await asyncio.to_thread(hermes_compatibility)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not inspect Hermes compatibility: {exc}") from exc
    return JSONResponse(_cache_set(cache_key, out))


@app.get("/api/hermes/capabilities")
async def hermes_capabilities(force: bool = False) -> JSONResponse:
    """Return Hermes api-server capabilities in a UI-friendly shape."""
    cache_key = "hermes:capabilities"
    cached = _cache_get(cache_key, 30.0, force=force)
    if cached is not None:
        return JSONResponse(cached)
    out = await asyncio.to_thread(_hermes_capabilities_snapshot)
    return JSONResponse(_cache_set(cache_key, out))


class CapabilityPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str = Field(min_length=1, max_length=96)
    mode: str = Field(min_length=3, max_length=8)
    confirm: StrictBool


class CapabilityPresetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: str = Field(min_length=1, max_length=32)
    confirm: StrictBool


class CapabilitySectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section: str = Field(min_length=1, max_length=48)
    mode: str = Field(min_length=3, max_length=8)
    confirm: StrictBool


class CapabilityGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm: StrictBool


class CapabilityToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=96)
    enabled: StrictBool
    confirm: StrictBool


class CapabilityProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=32)
    value: str = Field(default="", max_length=320)
    confirm: StrictBool


class CapabilityConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: StrictBool


def _capability_center_snapshot() -> dict[str, Any]:
    capabilities = _hermes_capabilities_snapshot()
    return build_capability_center_snapshot(capabilities, store=_capability_store)


def _require_capability_confirmation(confirm: bool) -> None:
    if confirm is not True:
        raise HTTPException(
            status_code=409,
            detail="Confirma el cambio de permisos desde el equipo principal.",
        )


def _invalidate_capability_center_cache() -> None:
    _api_cache.pop("capability:center", None)
    _api_cache.pop("hermes:capabilities", None)


async def _reconcile_runtime_capability_policy(
    changed_capability: str | None = None,
) -> None:
    """Apply restrictive policy changes to resources already in use.

    Permission changes are not only UI metadata. Downgrading camera access
    immediately closes the active lease, while restricting perceptive context
    drops the last derived emotion before it can reach a later chat turn.
    """
    global _policy_revision
    _policy_revision += 1  # before any await: a capture still preparing sees it
    if changed_capability in {None, "vision.snapshot", "camera.capture", "screen.snapshot",
                              "vision.ai_request", "screen.ai_request"}:
        source = (None if changed_capability is None
                  else "screen" if changed_capability in {"screen.snapshot", "screen.ai_request"} else "camera")
        await _cancel_pending_snapshots("permission_changed", source=source)
    if changed_capability in {None, "vision.context"}:
        context = await asyncio.to_thread(
            _capability_store.evaluate,
            "vision.context",
            trigger="automatic",
            confirmed=False,
        )
        if context["verdict"] != "allow":
            app.state.user_emotion = None

    if changed_capability not in {None, "camera.capture"}:
        return
    camera = await asyncio.to_thread(
        _capability_store.evaluate,
        "camera.capture",
        trigger="automatic",
        confirmed=False,
    )
    if camera["verdict"] == "allow":
        return
    owner = _surface_registry.camera_owner()
    if owner is None or not _surface_registry.release_camera(owner):
        return
    app.state.user_emotion = None
    _vision_assistant.reset_observation()
    try:
        await owner.send_json({
            "type": "camera_lease_revoked",
            "payload": {"reason": "capability-policy-changed"},
        })
    except Exception:
        _disconnect_surface(owner)
    await broadcast_to_face("camera_lease_state", _surface_registry.camera_snapshot())


@app.get("/api/capability-center")
async def capability_center(force: bool = False) -> JSONResponse:
    """Return local IRIS policy plus official Hermes enforcement state."""
    cache_key = "capability:center"
    cached = _cache_get(cache_key, 15.0, force=force)
    if cached is not None:
        return JSONResponse(cached)
    try:
        snapshot = await asyncio.to_thread(_capability_center_snapshot)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo leer Capability Center: {exc}",
        ) from exc
    return JSONResponse(_cache_set(cache_key, snapshot))


@app.post("/api/capability-center/policy")
async def capability_center_policy(payload: CapabilityPolicyRequest) -> JSONResponse:
    _require_capability_confirmation(payload.confirm)
    try:
        policy = await asyncio.to_thread(
            _capability_store.set_mode,
            payload.capability,
            payload.mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _invalidate_capability_center_cache()
    await _reconcile_runtime_capability_policy(payload.capability)
    if payload.capability in {"camera.capture", "vision.context"}:
        await broadcast_to_face("vision_assistant_state", _vision_snapshot())
    return JSONResponse({
        "success": True,
        "message": "Política local actualizada. Hermes conserva sus approvals.",
        "policy": policy,
    })


@app.post("/api/capability-center/section")
async def capability_center_section(payload: CapabilitySectionRequest) -> JSONResponse:
    _require_capability_confirmation(payload.confirm)
    try:
        policy = await asyncio.to_thread(_capability_store.set_section, payload.section, payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _invalidate_capability_center_cache()
    await _reconcile_runtime_capability_policy()
    await broadcast_to_face("vision_assistant_state", _vision_snapshot())
    return JSONResponse({"success": True, "message": "Permisos de la sección actualizados.", "policy": policy})


@app.post("/api/capability-center/action-grant/remove")
async def capability_center_forget_action(payload: CapabilityGrantRequest) -> JSONResponse:
    _require_capability_confirmation(payload.confirm)
    policy = await asyncio.to_thread(_capability_store.forget_action, payload.id)
    _invalidate_capability_center_cache()
    return JSONResponse({"success": True, "message": "Permiso olvidado. Iris volverá a preguntar.", "policy": policy})


@app.post("/api/capability-center/preset")
async def capability_center_preset(payload: CapabilityPresetRequest) -> JSONResponse:
    _require_capability_confirmation(payload.confirm)
    try:
        policy = await asyncio.to_thread(_capability_store.apply_preset, payload.preset)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _invalidate_capability_center_cache()
    await _reconcile_runtime_capability_policy()
    await broadcast_to_face("vision_assistant_state", _vision_snapshot())
    return JSONResponse({
        "success": True,
        "message": f"Perfil {payload.preset} aplicado.",
        "policy": policy,
    })


@app.post("/api/capability-center/tool")
async def capability_center_tool(payload: CapabilityToolRequest) -> JSONResponse:
    _require_capability_confirmation(payload.confirm)
    try:
        result = await asyncio.to_thread(
            set_hermes_tool_enabled,
            payload.name,
            payload.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await asyncio.to_thread(
        _capability_store.record_event,
        (
            "hermes_tool_changed"
            if result.get("success")
            else "hermes_tool_change_failed"
        ),
        capability=payload.name,
        verdict=(
            ("enabled" if payload.enabled else "disabled")
            if result.get("success")
            else "error"
        ),
        detail=result.get("message") or "Hermes tools actualizado.",
    )
    _invalidate_capability_center_cache()
    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail=result.get("message") or "Hermes rechazó el cambio.",
        )
    return JSONResponse({
        "success": True,
        "message": result.get("message") or "Tool actualizado.",
        "tool": result,
    })


@app.post("/api/capability-center/probe")
async def capability_center_probe(payload: CapabilityProbeRequest) -> JSONResponse:
    _require_capability_confirmation(payload.confirm)
    action = payload.action.strip().lower()
    try:
        if action == "approval_test":
            probe = await asyncio.to_thread(approval_dry_run, payload.value)
        elif action == "mcp_test":
            probe = await asyncio.to_thread(test_mcp_server, payload.value)
        elif action == "security_audit":
            probe = await asyncio.to_thread(run_security_audit)
        else:
            raise ValueError("Diagnóstico desconocido.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    verdict = str(probe.get("verdict") or ("ok" if probe.get("success") else "error"))
    await asyncio.to_thread(
        _capability_store.record_event,
        "probe",
        capability=action,
        verdict=verdict,
        detail="Diagnóstico explícito completado; no se guardó el comando ni credenciales.",
    )
    _invalidate_capability_center_cache()
    return JSONResponse({
        "success": bool(probe.get("success")),
        "message": (
            "Diagnóstico completado."
            if probe.get("success")
            else "El diagnóstico reportó un problema."
        ),
        "probe": probe,
    })


@app.post("/api/capability-center/audit/clear")
async def capability_center_clear_audit(payload: CapabilityConfirmRequest) -> JSONResponse:
    _require_capability_confirmation(payload.confirm)
    policy = await asyncio.to_thread(_capability_store.clear_audit)
    _invalidate_capability_center_cache()
    return JSONResponse({
        "success": True,
        "message": "Ledger local eliminado.",
        "policy": policy,
    })


def _run_hermes_cli_command(args: list[str], *, timeout: int) -> dict[str, Any]:
    """Hermes CLI through its managed interpreter and environment.

    The included CLI is a ``.py`` script: started directly, Windows refuses it
    (WinError 193) and Continuity reported Hermes as unreachable (qa9).
    """
    from core.hermes_runtime import run_hermes_cli

    result = run_hermes_cli(args, timeout=timeout)
    return {
        "ok": bool(result.get("success")),
        "returncode": result.get("returncode"),
        "stdout": str(result.get("stdout") or ""),
        "stderr": str(result.get("error") or result.get("stderr") or ""),
    }


def _continuity_profile_list() -> dict[str, Any]:
    if not _resolve_hermes_cli():
        return {"ok": False, "stdout": "", "error": "Hermes CLI no está instalado."}
    result = _run_hermes_cli_command(["profile", "list"], timeout=60)
    return {
        "ok": bool(result.get("ok")),
        "stdout": str(result.get("stdout") or ""),
        "error": str(result.get("stderr") or "")[:300],
    }


def _continuity_current_session_id() -> str:
    agent = getattr(app.state, "agent", None)
    bridge = getattr(agent, "bridge", None)
    return str(getattr(bridge, "session_id", "") or "")[:192]


def _continuity_capability(caps: dict[str, Any], name: str) -> bool:
    return _hermes_capability_declared(
        caps,
        feature_names=(name,),
        endpoint_names=(name,),
    )


def _continuity_header_available(caps: dict[str, Any]) -> bool:
    """Detect Hermes' authenticated transcript-continuity header contract."""
    for source in (caps, caps.get("features"), caps.get("supports")):
        if not isinstance(source, dict):
            continue
        value = source.get("session_continuity_header")
        if isinstance(value, str) and value.strip().casefold() == "x-hermes-session-id":
            return True
        if value is True:
            return True
    return False


def _continuity_uses_local_profiles() -> bool:
    """Local CLI profiles only belong to a loopback Hermes Gateway."""
    return is_loopback_url(_hermes_api_base_url())


async def _continuity_snapshot(limit: int = 80) -> dict[str, Any]:
    from core.hermes_continuity import continuity_snapshot

    local_profiles = _continuity_uses_local_profiles()
    capabilities_task = asyncio.to_thread(_hermes_capabilities_snapshot)
    if local_profiles:
        profiles_task = asyncio.to_thread(_continuity_profile_list)
        profiles_result, capabilities = await asyncio.gather(
            profiles_task,
            capabilities_task,
        )
    else:
        capabilities = await capabilities_task
        profiles_result = {"ok": False, "stdout": "", "error": ""}
    raw_caps = capabilities.get("capabilities") if isinstance(capabilities, dict) else {}
    raw_caps = raw_caps if isinstance(raw_caps, dict) else {}
    sessions_available = bool(
        capabilities.get("reachable")
        and capabilities.get("capabilities_available")
        and _continuity_capability(raw_caps, "sessions")
    )
    session_update_available = sessions_available and _continuity_capability(raw_caps, "session_update")
    session_messages_available = sessions_available and _continuity_capability(raw_caps, "session_messages")
    session_create_available = sessions_available and _continuity_capability(raw_caps, "session_create")
    session_fork_available = sessions_available and _continuity_capability(raw_caps, "session_fork")
    session_continuity_available = bool(
        sessions_available
        and session_messages_available
        and _continuity_header_available(raw_caps)
        and _hermes_api_token()
    )
    session_payload: Any = None
    session_error = ""
    if sessions_available:
        result = await asyncio.to_thread(
            _hermes_api_request,
            "GET",
            "/api/sessions",
            params={"limit": limit, "offset": 0, "include_children": False},
            timeout=12,
        )
        if result.get("ok"):
            session_payload = result.get("data")
        else:
            sessions_available = False
            session_update_available = False
            session_messages_available = False
            session_create_available = False
            session_fork_available = False
            session_continuity_available = False
            session_error = str(result.get("error") or "No pude leer las sesiones de Hermes.")
    errors = []
    if local_profiles and not profiles_result.get("ok"):
        errors.append("Hermes CLI no pudo listar los perfiles.")
    if session_error:
        errors.append("Hermes anunció sesiones, pero no pudo entregar su catálogo.")
    snapshot = continuity_snapshot(
        profile_output=str(profiles_result.get("stdout") or ""),
        session_payload=session_payload,
        sessions_available=sessions_available,
        session_update_available=session_update_available,
        session_messages_available=session_messages_available,
        session_create_available=session_create_available,
        session_fork_available=session_fork_available,
        session_continuity_available=session_continuity_available,
        current_session_id=_continuity_current_session_id(),
        gateway_model=str(raw_caps.get("model") or ""),
        session_profile_fallback="gateway" if local_profiles else "remote",
        error=" · ".join(error for error in errors if error),
    )
    snapshot["profile_scope"] = "local" if local_profiles else "remote"
    if not local_profiles and sessions_available:
        snapshot["notice"] = (
            "Sesiones del Gateway remoto disponibles. Los perfiles del CLI local "
            "permanecen ocultos para no mezclar contextos."
        )
    elif not local_profiles:
        snapshot["notice"] = (
            "El Gateway remoto no está disponible. IRIS no mezcló sus sesiones con "
            "los perfiles del CLI local."
        )
    elif profiles_result.get("ok") and not capabilities.get("reachable"):
        snapshot["notice"] = (
            "Perfiles disponibles. Inicia el Gateway de Hermes para consultar y "
            "administrar sus sesiones."
        )
    elif profiles_result.get("ok") and not sessions_available:
        snapshot["notice"] = (
            "Perfiles disponibles. Este Gateway no anunció una Session API compatible."
        )
    snapshot["hermes_version"] = _hermes_version()
    snapshot["api_reachable"] = bool(capabilities.get("reachable"))
    snapshot["capabilities_available"] = bool(capabilities.get("capabilities_available"))
    return snapshot


@app.get("/api/hermes/continuity")
async def hermes_continuity_snapshot(force: bool = False, limit: int = 80) -> JSONResponse:
    """Project Hermes profiles/sessions without reading or copying state.db."""
    bounded_limit = max(1, min(int(limit or 80), 200))
    cache_key = f"hermes:continuity:{bounded_limit}"
    cached = _cache_get(cache_key, 8.0, force=force)
    if cached is not None:
        return JSONResponse(cached)
    try:
        out = await _continuity_snapshot(bounded_limit)
    except Exception as exc:
        logger.exception("Could not build Hermes continuity snapshot")
        raise HTTPException(
            status_code=500,
            detail="No pude inspeccionar la continuidad de Hermes.",
        ) from exc
    return JSONResponse(_cache_set(cache_key, out))


def _continuity_profile_rows() -> list[dict[str, Any]]:
    from core.hermes_continuity import parse_profile_list

    result = _continuity_profile_list()
    return parse_profile_list(result.get("stdout") or "") if result.get("ok") else []


@app.post("/api/hermes/continuity/profiles/activate")
async def hermes_continuity_activate_profile(
    payload: ContinuityProfileActivationRequest,
) -> JSONResponse:
    """Set Hermes' sticky profile for future processes via its official CLI."""
    from core.hermes_continuity import valid_profile_name

    name = payload.name.strip()
    if not _continuity_uses_local_profiles():
        raise HTTPException(
            status_code=409,
            detail="Los perfiles locales no administran un Gateway remoto.",
        )
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Confirma el cambio de perfil.")
    if not valid_profile_name(name):
        raise HTTPException(status_code=422, detail="El nombre del perfil no es válido.")
    rows = await asyncio.to_thread(_continuity_profile_rows)
    if name.casefold() not in {str(row.get("name") or "").casefold() for row in rows}:
        raise HTTPException(status_code=404, detail="Ese perfil no existe en Hermes.")
    if not _resolve_hermes_cli():
        raise HTTPException(status_code=503, detail="Hermes CLI no está instalado.")
    result = await asyncio.to_thread(
        _run_hermes_cli_command,
        ["profile", "use", name],
        timeout=60,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=result.get("stderr") or "Hermes rechazó el cambio de perfil.",
        )
    _api_cache.clear()
    return JSONResponse({
        "success": True,
        "active_profile": name,
        "requires_gateway_restart": True,
        "message": (
            f"Hermes usará el perfil {name} en próximos procesos. "
            "Reinicia el Gateway para aplicarlo al chat de IRIS."
        ),
    })


@app.post("/api/hermes/continuity/profiles")
async def hermes_continuity_create_profile(
    payload: ContinuityProfileCreateRequest,
) -> JSONResponse:
    """Create a Hermes profile through the documented CLI, never by copying files."""
    from core.hermes_continuity import valid_profile_name

    name = payload.name.strip()
    source = payload.clone_from.strip() or "default"
    clone_mode = payload.clone_mode.strip().casefold()
    if not _continuity_uses_local_profiles():
        raise HTTPException(
            status_code=409,
            detail="Los perfiles locales no administran un Gateway remoto.",
        )
    if not valid_profile_name(name) or not valid_profile_name(source):
        raise HTTPException(status_code=422, detail="El nombre del perfil no es válido.")
    if clone_mode not in {"blank", "config", "all"}:
        raise HTTPException(status_code=422, detail="El modo de creación no es válido.")
    rows = await asyncio.to_thread(_continuity_profile_rows)
    existing = {str(row.get("name") or "").casefold() for row in rows}
    if name.casefold() in existing:
        raise HTTPException(status_code=409, detail="Ese perfil ya existe en Hermes.")
    if clone_mode != "blank" and source.casefold() not in existing:
        raise HTTPException(status_code=404, detail="El perfil de origen no existe en Hermes.")
    if not _resolve_hermes_cli():
        raise HTTPException(status_code=503, detail="Hermes CLI no está instalado.")
    args = ["profile", "create", name]
    if clone_mode == "config":
        args.extend(["--clone-from", source])
    elif clone_mode == "all":
        args.extend(["--clone-from", source, "--clone-all"])
    description = " ".join(payload.description.split())
    if description:
        args.extend(["--description", description])
    result = await asyncio.to_thread(_run_hermes_cli_command, args, timeout=90)
    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=result.get("stderr") or "Hermes no pudo crear el perfil.",
        )
    _api_cache.clear()
    return JSONResponse({
        "success": True,
        "name": name,
        "needs_setup": clone_mode == "blank",
        "message": (
            f"Perfil {name} creado por Hermes. Ejecuta su setup antes de usarlo."
            if clone_mode == "blank"
            else f"Perfil {name} creado por Hermes a partir de {source}."
        ),
    })


def _continuity_session_from_response(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    session = data.get("session")
    if isinstance(session, dict):
        return session
    nested = data.get("data")
    if isinstance(nested, dict) and isinstance(nested.get("session"), dict):
        return nested["session"]
    return data if data.get("id") or data.get("session_id") else {}


def _continuity_error_text(value: Any, fallback: str) -> str:
    """Flatten Hermes/OpenAI error envelopes into one safe UI message."""
    if isinstance(value, str) and value.strip():
        return " ".join(value.split())[:500]
    if isinstance(value, dict):
        nested = value.get("message") or value.get("detail") or value.get("error")
        if nested is not value:
            return _continuity_error_text(nested, fallback)
    return fallback


async def _continuity_require_session_capabilities(
    *names: str,
    require_header: bool = False,
) -> dict[str, Any]:
    capabilities = await asyncio.to_thread(_hermes_capabilities_snapshot)
    raw = capabilities.get("capabilities") if isinstance(capabilities, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    missing = [name for name in names if not _continuity_capability(raw, name)]
    if require_header and not _continuity_header_available(raw):
        missing.append("session_continuity_header")
    if missing:
        raise HTTPException(
            status_code=409,
            detail=(
                "Esta versión de Hermes no anuncia el contrato necesario: "
                + ", ".join(missing)
                + "."
            ),
        )
    if require_header and not _hermes_api_token():
        raise HTTPException(
            status_code=409,
            detail="Hermes necesita API_SERVER_KEY para reanudar sesiones de forma autenticada.",
        )
    return raw


def _continuity_require_idle_turn() -> None:
    """Reject continuity mutations before they can race an active turn."""
    agent = getattr(app.state, "agent", None)
    conversation_lock = getattr(agent, "_conversation_lock", None)
    turn = getattr(agent, "_current_turn_task", None)
    if (
        (conversation_lock is not None and conversation_lock.locked())
        or (turn is not None and not turn.done())
    ):
        raise HTTPException(
            status_code=409,
            detail="IRIS está respondiendo. Espera a que termine antes de cambiar de sesión.",
        )


async def _continuity_resume_existing_session(
    session_id: str,
    *,
    confirm: bool,
) -> dict[str, Any]:
    """Switch Hermes, IRIS memory and the visible transcript as one action."""
    from core.hermes_continuity import normalize_messages, valid_session_id

    clean_id = str(session_id or "").strip()
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirma el cambio de conversación.")
    if not valid_session_id(clean_id):
        raise HTTPException(status_code=422, detail="El ID de sesión no es válido.")
    await _continuity_require_session_capabilities(
        "sessions",
        "session_messages",
        require_header=True,
    )

    agent = getattr(app.state, "agent", None)
    bridge = getattr(agent, "bridge", None)
    conversation_lock = getattr(agent, "_conversation_lock", None)
    if agent is None or bridge is None or conversation_lock is None:
        raise HTTPException(status_code=503, detail="IRIS Core todavía no está listo.")
    if conversation_lock.locked():
        raise HTTPException(
            status_code=409,
            detail="IRIS está respondiendo. Espera a que termine antes de cambiar de sesión.",
        )

    encoded = urllib.parse.quote(clean_id, safe="")
    metadata_result, messages_result = await asyncio.gather(
        asyncio.to_thread(
            _hermes_api_request,
            "GET",
            f"/api/sessions/{encoded}",
            timeout=12,
        ),
        asyncio.to_thread(
            _hermes_api_request,
            "GET",
            f"/api/sessions/{encoded}/messages",
            params={"limit": 120, "offset": 0, "order": "latest"},
            timeout=18,
        ),
    )
    if not metadata_result.get("ok"):
        status = int(metadata_result.get("status") or 400)
        raise HTTPException(
            status_code=status if 400 <= status < 600 else 400,
            detail=_continuity_error_text(
                metadata_result.get("error"),
                "Hermes no encontró esa sesión.",
            ),
        )
    metadata = _continuity_session_from_response(metadata_result.get("data"))
    if metadata.get("archived") is True:
        raise HTTPException(
            status_code=409,
            detail="La sesión está archivada en Hermes y no se reanudará hasta restaurarla allí.",
        )
    if not messages_result.get("ok"):
        status = int(messages_result.get("status") or 400)
        raise HTTPException(
            status_code=status if 400 <= status < 600 else 400,
            detail=_continuity_error_text(
                messages_result.get("error"),
                "Hermes no pudo entregar el transcript.",
            ),
        )
    message_payload = messages_result.get("data")
    effective_id = (
        str(message_payload.get("session_id") or "").strip()
        if isinstance(message_payload, dict)
        else ""
    ) or clean_id
    if not valid_session_id(effective_id):
        raise HTTPException(status_code=502, detail="Hermes devolvió un ID de sesión no válido.")
    messages = normalize_messages(message_payload, limit=120)

    async with conversation_lock:
        turn = getattr(agent, "_current_turn_task", None)
        if turn is not None and not turn.done():
            raise HTTPException(
                status_code=409,
                detail="IRIS está respondiendo. Espera a que termine antes de cambiar de sesión.",
            )
        previous_local_id = str(getattr(agent, "_session_id", "default") or "default")
        previous_continuity = bool(getattr(agent, "_continuity_active", False))
        try:
            # Persist first so a storage failure leaves bridge/profile routing
            # untouched. resume_session is an in-memory validated mutation.
            await agent.memory.save_preference("continuity.active_session_id", effective_id)
            bridge.resume_session(effective_id)
            agent._session_id = effective_id
            agent._continuity_active = True
        except Exception as exc:
            agent._session_id = previous_local_id
            agent._continuity_active = previous_continuity
            try:
                await agent.memory.save_preference(
                    "continuity.active_session_id",
                    str(getattr(bridge, "session_id", "") or ""),
                )
            except Exception:
                pass
            logger.exception("Could not atomically resume Hermes session %s", effective_id)
            raise HTTPException(
                status_code=500,
                detail="IRIS no pudo aplicar el cambio completo; la conversación anterior sigue activa.",
            ) from exc

    _api_cache.clear()
    await broadcast_to_face(
        "chat_history_replace",
        {
            "session_id": effective_id,
            "requested_session_id": clean_id,
            "title": str(metadata.get("title") or "Sesión de Hermes")[:140],
            "messages": messages,
        },
    )
    return {
        "success": True,
        "session_id": effective_id,
        "requested_session_id": clean_id,
        "title": str(metadata.get("title") or "Sesión de Hermes")[:140],
        "messages": messages,
        "displayed_messages": len(messages),
        "message": "IRIS y Hermes continuarán desde esta conversación.",
    }


@app.post("/api/hermes/continuity/sessions/{session_id}/resume")
async def hermes_continuity_resume_session(
    session_id: str,
    payload: ContinuitySessionResumeRequest,
) -> JSONResponse:
    return JSONResponse(await _continuity_resume_existing_session(
        session_id,
        confirm=bool(payload.confirm),
    ))


@app.post("/api/hermes/continuity/sessions")
async def hermes_continuity_create_session(
    payload: ContinuitySessionCreateRequest,
) -> JSONResponse:
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Confirma la creación de la conversación.")
    # Fail before POST /api/sessions. Otherwise Hermes could create an empty
    # session that IRIS cannot open because a conversation is still active.
    _continuity_require_idle_turn()
    # Creating a transcript is only useful to this endpoint when IRIS can also
    # authenticate, read it and switch to it.  Check the complete contract
    # before asking Hermes to create anything so a partial Gateway cannot leave
    # an orphan empty session behind.
    await _continuity_require_session_capabilities(
        "session_create",
        "sessions",
        "session_messages",
        require_header=True,
    )
    title = " ".join(payload.title.split())
    body: dict[str, Any] = {"source": "api_server"}
    if title:
        body["title"] = title
    result = await asyncio.to_thread(
        _hermes_api_request,
        "POST",
        "/api/sessions",
        payload=body,
        timeout=15,
    )
    if not result.get("ok"):
        status = int(result.get("status") or 400)
        raise HTTPException(
            status_code=status if 400 <= status < 600 else 400,
            detail=_continuity_error_text(
                result.get("error"),
                "Hermes no pudo crear la sesión.",
            ),
        )
    session = _continuity_session_from_response(result.get("data"))
    created_id = str(session.get("id") or session.get("session_id") or "").strip()
    resumed = await _continuity_resume_existing_session(created_id, confirm=True)
    resumed["created"] = True
    resumed["message"] = "Nueva conversación creada en Hermes y abierta en IRIS."
    return JSONResponse(resumed, status_code=201)


@app.post("/api/hermes/continuity/sessions/{session_id}/fork")
async def hermes_continuity_fork_session(
    session_id: str,
    payload: ContinuitySessionForkRequest,
) -> JSONResponse:
    from core.hermes_continuity import valid_session_id

    clean_id = session_id.strip()
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Confirma la ramificación de la conversación.")
    if not valid_session_id(clean_id):
        raise HTTPException(status_code=422, detail="El ID de sesión no es válido.")
    # Hermes marks the parent as branched while creating the child. Reject an
    # active IRIS turn before that mutation, not after the child already exists.
    _continuity_require_idle_turn()
    # A branch created by this action must be immediately resumable in IRIS.
    # Refuse before mutating Hermes when that full contract is unavailable.
    await _continuity_require_session_capabilities(
        "session_fork",
        "sessions",
        "session_messages",
        require_header=True,
    )
    body: dict[str, Any] = {}
    title = " ".join(payload.title.split())
    if title:
        body["title"] = title
    result = await asyncio.to_thread(
        _hermes_api_request,
        "POST",
        f"/api/sessions/{urllib.parse.quote(clean_id, safe='')}/fork",
        payload=body,
        timeout=20,
    )
    if not result.get("ok"):
        status = int(result.get("status") or 400)
        raise HTTPException(
            status_code=status if 400 <= status < 600 else 400,
            detail=_continuity_error_text(
                result.get("error"),
                "Hermes no pudo ramificar la sesión.",
            ),
        )
    session = _continuity_session_from_response(result.get("data"))
    fork_id = str(session.get("id") or session.get("session_id") or "").strip()
    resumed = await _continuity_resume_existing_session(fork_id, confirm=True)
    resumed["forked"] = True
    resumed["parent_session_id"] = clean_id
    resumed["message"] = "Rama creada por Hermes y abierta en IRIS; la conversación original se conserva."
    return JSONResponse(resumed, status_code=201)


@app.patch("/api/hermes/continuity/sessions/{session_id}")
async def hermes_continuity_update_session(
    session_id: str,
    payload: ContinuitySessionUpdateRequest,
) -> JSONResponse:
    """Rename, pin or archive a session through Hermes' advertised API."""
    from core.hermes_continuity import normalize_sessions, valid_session_id

    clean_id = session_id.strip()
    if not valid_session_id(clean_id):
        raise HTTPException(status_code=422, detail="El ID de sesión no es válido.")
    if not payload.model_fields_set:
        raise HTTPException(status_code=400, detail="No hay cambios para guardar.")
    body: dict[str, Any] = {}
    if "title" in payload.model_fields_set:
        title = " ".join(str(payload.title or "").split())
        if not title:
            raise HTTPException(status_code=422, detail="El título no puede quedar vacío.")
        body["title"] = title
    if "archived" in payload.model_fields_set:
        body["archived"] = payload.archived
    if "pinned" in payload.model_fields_set:
        body["pinned"] = payload.pinned
    caps = await asyncio.to_thread(_hermes_capabilities_snapshot)
    raw_caps = caps.get("capabilities") if isinstance(caps, dict) else {}
    if not isinstance(raw_caps, dict) or not _continuity_capability(
        raw_caps,
        "session_update",
    ):
        raise HTTPException(
            status_code=409,
            detail="Esta versión de Hermes no anuncia actualización de sesiones.",
        )
    result = await asyncio.to_thread(
        _hermes_api_request,
        "PATCH",
        f"/api/sessions/{urllib.parse.quote(clean_id, safe='')}",
        payload=body,
        timeout=12,
    )
    if not result.get("ok"):
        status = int(result.get("status") or 400)
        raise HTTPException(
            status_code=status if 400 <= status < 600 else 400,
            detail=_continuity_error_text(
                result.get("error"),
                "Hermes rechazó el cambio.",
            ),
        )
    raw = result.get("data")
    row = (
        raw.get("session")
        if isinstance(raw, dict) and isinstance(raw.get("session"), dict)
        else raw
    )
    normalized = (
        normalize_sessions([row], current_session_id=_continuity_current_session_id())
        if isinstance(row, dict)
        else []
    )
    _api_cache.clear()
    return JSONResponse({
        "success": True,
        "session": normalized[0] if normalized else None,
        "message": "La sesión fue actualizada por Hermes.",
    })


@app.get("/api/hermes/runs")
async def hermes_runs(
    limit: int = 30,
    status: str | None = None,
    force: bool = False,
) -> JSONResponse:
    """List Hermes async runs if the installed api-server exposes them."""
    limit = max(1, min(int(limit or 30), 100))
    cache_key = f"hermes:runs:{limit}:{status or 'all'}"
    cached = _cache_get(cache_key, 8.0, force=force)
    if cached is not None:
        return JSONResponse(cached)
    result = await asyncio.to_thread(
        _hermes_api_request,
        "GET",
        "/v1/runs",
        params={"limit": limit, "status": status},
        timeout=12,
    )
    if not result.get("ok"):
        return JSONResponse({
            "success": False,
            "runs": [],
            "api_url": _hermes_api_base_url(),
            "status": result.get("status"),
            "error": result.get("error") or "Hermes runs endpoint no disponible.",
        }, status_code=200)
    rows = _unwrap_hermes_list(result.get("data"), "runs", "items", "results")
    runs = [_normalize_run(row) for row in rows if isinstance(row, dict)]
    out = {
        "success": True,
        "runs": runs,
        "count": len(runs),
        "api_url": _hermes_api_base_url(),
        "raw": result.get("data"),
    }
    return JSONResponse(_cache_set(cache_key, out))


@app.get("/api/hermes/runs/{run_id}")
async def hermes_run_detail(run_id: str, force: bool = False) -> JSONResponse:
    """Read pollable status; Hermes' /events is a live SSE stream, not JSON."""
    clean_id = str(run_id or "").strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="missing run_id")
    gateway = _hermes_api_base_url()
    cache_key = f"hermes:run:{clean_id}"
    cached = _cache_get(cache_key, 5.0, force=force)
    if cached is not None:
        return JSONResponse(cached)
    store = ManualRunStore()
    try:
        saved = await asyncio.to_thread(store.get, gateway, clean_id)
    except (OSError, ValueError, sqlite3.Error):
        saved = None
    quoted_id = urllib.parse.quote(clean_id, safe="")
    response = await asyncio.to_thread(_hermes_api_request, "GET", f"/v1/runs/{quoted_id}", timeout=8)
    if not response.get("ok"):
        if saved:
            return JSONResponse({"success": True, "run": {**saved, "stale": True}, "events": [],
                                 "notice": "Mostrando el último resultado guardado; Hermes no respondió."})
        return JSONResponse({"success": False, "run": None, "events": [],
                             "error": response.get("error") or "No pude leer esa tarea."})
    data = response.get("data") or {}
    raw = data.get("run", data) if isinstance(data, dict) else {}
    run = _normalize_run(raw if isinstance(raw, dict) else {})
    run["id"] = clean_id
    if saved:
        run.update(title=saved["title"], source="manual")
        run["created_at"] = run.get("created_at") or saved.get("created_at")
        try:
            await asyncio.to_thread(store.save, gateway, run)
        except (OSError, sqlite3.Error):
            logger.warning("Could not save async run snapshot %s", clean_id)
    events = []
    if run["created_at"]:
        events.append({"kind": "Tarea creada", "created_at": run["created_at"], "message": run["title"]})
    if isinstance(raw, dict) and raw.get("last_event"):
        events.append({"kind": str(raw["last_event"]), "created_at": run["updated_at"], "message": ""})
    return JSONResponse(_cache_set(cache_key, {"success": True, "run": run, "events": events}))


@app.post("/api/hermes/runs")
async def hermes_create_run(payload: dict = Body(...)) -> JSONResponse:
    """Create an async Hermes run when supported by the installed version."""
    prompt = str(payload.get("prompt") or payload.get("input") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="missing 'prompt'")
    submission = {**payload, "input": payload.get("input") or prompt}
    result = await asyncio.to_thread(_hermes_api_request, "POST", "/v1/runs", payload=submission, timeout=18)
    for key in list(_api_cache):
        if key.startswith("hermes:runs"):
            _api_cache.pop(key, None)
    if not result.get("ok"):
        return JSONResponse({
            "success": False,
            "status": result.get("status"),
            "error": result.get("error") or "Hermes no acepto crear el run.",
        }, status_code=200)
    data = result.get("data")
    run_data = data.get("run") if isinstance(data, dict) and isinstance(data.get("run"), dict) else data
    run = _normalize_run(run_data if isinstance(run_data, dict) else {"summary": str(data or "")})
    run.update(title=prompt[:500], source="manual")
    run["created_at"] = run.get("created_at") or time.time()
    warning = ""
    try:
        await asyncio.to_thread(ManualRunStore().save, _hermes_api_base_url(), run)
    except (OSError, sqlite3.Error):
        logger.warning("Created async run %s but could not persist its index", run["id"])
        warning = "La tarea se creó, pero no se pudo guardar su historial local."
    _api_cache.pop("hermes:async-agents:manual:" + _hermes_api_base_url(), None)
    return JSONResponse({"success": True, "run": run, "warning": warning})


@app.post("/api/hermes/runs/{run_id}/stop")
async def hermes_stop_run(run_id: str) -> JSONResponse:
    """Stop a Hermes async run server-side."""
    clean_id = str(run_id or "").strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="missing run_id")
    quoted_id = urllib.parse.quote(clean_id, safe="")
    result = await asyncio.to_thread(_hermes_api_request, "POST", f"/v1/runs/{quoted_id}/stop", timeout=8)
    for key in list(_api_cache):
        if key.startswith(("hermes:runs", "hermes:async-agents:manual:")) or key == f"hermes:run:{clean_id}":
            _api_cache.pop(key, None)
    return JSONResponse({
        "success": bool(result.get("ok")),
        "status": result.get("status"),
        "message": "Run detenido." if result.get("ok") else "",
        "error": "" if result.get("ok") else str(result.get("error") or "No pude detener el run."),
        "raw": result.get("data"),
    })


@app.get("/api/skills/list")
async def skills_list(force: bool = False) -> JSONResponse:
    """Return the Hermes skill catalogue + our enable state.

    Output shape (one entry per skill):
        {
          "name": "1password",
          "emoji": "🔐",
          "description": "...",
          "eligible": bool,    # Hermes says deps are present
          "source": "bundled" | "user" | "hub" | ...,
          "enabled": bool,     # our local toggle (from enabled_skills.json)
        }

    Hermes' current CLI exposes this list as a terminal table rather
    than JSON in some versions, so we read the installed SKILL.md
    catalogue through ``core.skills_bridge`` instead.
    """
    cache_key = "skills:list"
    cached = _cache_get(cache_key, 45.0, force=force)
    if cached is not None:
        return JSONResponse(cached)
    try:
        from core.skills_bridge import list_installed_skills
        out = await asyncio.to_thread(list_installed_skills, refresh=force)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not read Hermes skills catalogue: {exc}",
        ) from exc
    enabled_count = sum(1 for s in out if s.get("enabled"))
    return JSONResponse(_cache_set(cache_key, {"skills": out, "enabled_count": enabled_count}))


@app.post("/api/chat/attachments/pdf")
async def extract_chat_pdf(file: UploadFile = File(...)) -> JSONResponse:
    """Extract PDF text locally without retaining or forwarding the raw file."""
    filename = (file.filename or "documento.pdf").replace("\\", "/").rsplit("/", 1)[-1]
    try:
        data = await file.read(MAX_PDF_BYTES + 1)
    finally:
        await file.close()
    try:
        result = await asyncio.to_thread(extract_pdf_text, data, filename=filename)
    except PdfTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except PdfNoTextError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PdfAttachmentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(result)


@app.get("/api/skills/marketplace")
async def skills_marketplace(
    page: int = 1,
    size: int = 60,
    source: str = "all",
    q: str = "",
    force: bool = False,
) -> JSONResponse:
    """Return Hermes optional skills as a marketplace-ready catalogue."""
    query = q.strip()[:120]
    cache_key = f"skills:marketplace:{page}:{size}:{source}:{query.casefold()}"
    cached = _cache_get(cache_key, 90.0, force=force)
    if cached is not None:
        return JSONResponse(cached)
    try:
        from core.hermes_skills_marketplace import list_skills_marketplace
        out = await asyncio.to_thread(
            list_skills_marketplace,
            page=page,
            size=size,
            source=source,
            query=query,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not read Hermes skills marketplace: {exc}",
        ) from exc
    return JSONResponse(_cache_set(cache_key, out))


@app.post("/api/skills/toggle")
async def skills_toggle(payload: SkillToggleRequest) -> JSONResponse:
    """Flip the enabled flag for a single skill.

    Payload: ``{"name": "<skill>", "enabled": true|false}``.
    Writes ``~/.iris/enabled_skills.json`` atomically (per-call, not
    per-batch — the UI sends one toggle per click). Returns the new
    full set so the UI doesn't have to refetch the whole catalogue.
    """
    from core.file_lock import interprocess_file_lock

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="missing 'name'")
    enabled = bool(payload.enabled)

    with interprocess_file_lock(_enabled_skills_path().with_suffix(".lock")):
        current = _read_enabled_skills()
        if enabled:
            current.add(name)
        else:
            current.discard(name)
        _write_enabled_skills(current)
    # Invalidate the catalogue cache so the next chat turn sees the
    # new set without waiting for the 60 s TTL.
    try:
        from core.skills_bridge import invalidate_cache as _invalidate
        _invalidate()
    except Exception:  # pragma: no cover — best effort
        pass
    _api_cache.pop("skills:list", None)
    return JSONResponse({"ok": True, "enabled": sorted(current)})


# ─────────────────────────────────────────────────────────────
# Hermes MCP Catalog Bridge

@app.get("/api/mcp/catalog")
async def mcp_catalog(force: bool = False) -> JSONResponse:
    """Return Hermes' official MCP catalog plus installed server state."""
    cache_key = "mcp:catalog"
    cached = _cache_get(cache_key, 60.0, force=force)
    if cached is not None:
        return JSONResponse(cached)
    try:
        from core.hermes_mcp import list_mcp_catalog

        result = await asyncio.to_thread(list_mcp_catalog)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read Hermes MCP catalog: {exc}") from exc
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Hermes MCP catalog error")
    return JSONResponse(_cache_set(cache_key, result))


# Hermes Cron Bridge
# ─────────────────────────────────────────────────────────────
#
# IRIS surfaces Hermes cron jobs in the Command Center, but Hermes
# remains the scheduler and source of truth. These endpoints are a
# small REST wrapper around Hermes' own ``cronjob`` tool.

def _cron_json_response(result: dict[str, Any]) -> JSONResponse:
    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error") or result.get("message") or "Hermes cron error",
        )
    return JSONResponse(result)


@app.get("/api/cron/jobs")
async def cron_jobs(include_disabled: bool = True) -> JSONResponse:
    try:
        from core.hermes_cron import list_cron_jobs
        result = await asyncio.to_thread(list_cron_jobs, include_disabled=include_disabled)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not list Hermes cron jobs: {exc}") from exc
    return _cron_json_response(result)


@app.get("/api/cron/outputs")
async def cron_outputs(limit: int = 20) -> JSONResponse:
    try:
        from core.hermes_cron import list_cron_outputs
        result = await asyncio.to_thread(list_cron_outputs, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read Hermes cron outputs: {exc}") from exc
    return _cron_json_response(result)


@app.get("/api/cron/jobs/{job_id}/outputs")
async def cron_job_outputs(job_id: str, limit: int = 20) -> JSONResponse:
    try:
        from core.hermes_cron import list_cron_outputs
        result = await asyncio.to_thread(list_cron_outputs, limit=limit, job_id=job_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read Hermes cron job outputs: {exc}") from exc
    return _cron_json_response(result)


@app.post("/api/cron/speak")
async def cron_speak(payload: dict = Body(...)) -> JSONResponse:
    """Speak a completed Hermes cron delivery through IRIS's normal TTS path."""
    text = result_text(payload.get("text"))
    name = str(payload.get("name") or "Rutina Hermes").strip()
    emotion = str(payload.get("emotion") or "neutral").strip() or "neutral"
    animation = str(payload.get("animation") or "idle").strip() or "idle"

    if not text:
        raise HTTPException(status_code=400, detail="missing 'text'")

    agent = _agent_or_503()
    spoken = text
    if len(spoken) > 1400:
        spoken = spoken[:1400].rstrip() + "... El resultado completo queda en el canvas."
    if name:
        spoken = f"Cron finalizado: {name}. {spoken}"

    async def _speak_delivery() -> None:
        try:
            await agent._respond({
                "texto_hablado": spoken,
                "emocion": emotion,
                "animacion_virtual": animation,
            })
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Could not speak Hermes cron delivery")

    asyncio.create_task(_speak_delivery())
    return JSONResponse({"success": True, "speaking": True})


@app.post("/api/cron/jobs")
async def cron_create(payload: dict = Body(...)) -> JSONResponse:
    prompt = str(payload.get("prompt") or "").strip()
    schedule = str(payload.get("schedule") or "").strip()
    skills = payload.get("skills")
    if not schedule:
        raise HTTPException(status_code=400, detail="missing 'schedule'")
    if not prompt and not skills and not payload.get("script"):
        raise HTTPException(status_code=400, detail="missing 'prompt' or 'skills'")

    try:
        from core.hermes_cron import create_cron_job
        result = await asyncio.to_thread(create_cron_job, payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not create Hermes cron job: {exc}") from exc
    return _cron_json_response(result)


@app.patch("/api/cron/jobs/{job_id}")
async def cron_update(job_id: str, payload: dict = Body(...)) -> JSONResponse:
    try:
        from core.hermes_cron import update_cron_job
        result = await asyncio.to_thread(update_cron_job, job_id, payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not update Hermes cron job: {exc}") from exc
    return _cron_json_response(result)


@app.post("/api/cron/jobs/{job_id}/pause")
async def cron_pause(job_id: str, payload: dict | None = Body(default=None)) -> JSONResponse:
    try:
        from core.hermes_cron import manage_cron_job
        result = await asyncio.to_thread(
            manage_cron_job,
            "pause",
            job_id,
            reason=(payload or {}).get("reason"),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not pause Hermes cron job: {exc}") from exc
    return _cron_json_response(result)


@app.post("/api/cron/jobs/{job_id}/resume")
async def cron_resume(job_id: str) -> JSONResponse:
    try:
        from core.hermes_cron import manage_cron_job
        result = await asyncio.to_thread(manage_cron_job, "resume", job_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not resume Hermes cron job: {exc}") from exc
    return _cron_json_response(result)


@app.post("/api/cron/jobs/{job_id}/run")
async def cron_run(job_id: str) -> JSONResponse:
    try:
        from core.hermes_cron import manage_cron_job
        result = await asyncio.to_thread(manage_cron_job, "run", job_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not run Hermes cron job: {exc}") from exc
    return _cron_json_response(result)


@app.delete("/api/cron/jobs/{job_id}")
async def cron_remove(job_id: str) -> JSONResponse:
    try:
        from core.hermes_cron import manage_cron_job
        result = await asyncio.to_thread(manage_cron_job, "remove", job_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not remove Hermes cron job: {exc}") from exc
    return _cron_json_response(result)


# Hermes Kanban Mission Board

@app.get("/api/kanban/board")
async def kanban_board(
    board: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
    tenant: str | None = None,
    include_archived: bool = False,
) -> JSONResponse:
    try:
        from core.hermes_kanban import read_board
        result = await asyncio.to_thread(
            read_board,
            board=board,
            status=status,
            assignee=assignee,
            tenant=tenant,
            include_archived=include_archived,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read Hermes Kanban board: {exc}") from exc
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Hermes Kanban error")
    return JSONResponse(result)


@app.get("/api/kanban/workers/active")
async def kanban_active_workers(board: str | None = None) -> JSONResponse:
    try:
        from core.hermes_kanban import active_workers
        result = await asyncio.to_thread(active_workers, board=board)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read Hermes Kanban workers: {exc}") from exc
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Hermes Kanban workers error")
    return JSONResponse(result)


@app.post("/api/kanban/tasks")
async def kanban_create_task(payload: dict = Body(...)) -> JSONResponse:
    title = str(payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="missing 'title'")
    try:
        from core.hermes_kanban import create_task
        result = await asyncio.to_thread(create_task, payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not create Hermes Kanban task: {exc}") from exc
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Hermes Kanban error")
    return JSONResponse(result)


@app.get("/api/kanban/tasks/{task_id}")
async def kanban_task_detail(task_id: str, board: str | None = None) -> JSONResponse:
    try:
        from core.hermes_kanban import task_detail
        result = await asyncio.to_thread(task_detail, task_id, board=board)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read Hermes Kanban task: {exc}") from exc
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Hermes Kanban error")
    return JSONResponse(result)


class _FaceStaticFiles(LocalWebStaticFiles):
    """Serve one effective manifest and immutable factory/profile clips."""

    async def get_response(self, path: str, scope):
        from core.animation_library import get_animation_library
        from core.animation_store import ANIMATION_EXTENSIONS, AnimationStoreError

        if scope["method"] not in {"GET", "HEAD"}:
            raise HTTPException(status_code=405)
        manifest_name = os.path.join("animations", "manifest.json")
        parts = Path(path).parts
        animation_path = bool(
            parts and os.path.normcase(parts[0]) == os.path.normcase("animations")
        )
        raw_path = str(scope.get("path", ""))
        if animation_path and (
            len(parts) != 2 or any(part.startswith(".") for part in parts[1:])
            or any(char in raw_path for char in "\\:%")
            or any(ord(char) < 32 for char in raw_path)
        ):
            raise HTTPException(status_code=404)
        is_manifest = os.path.normcase(path) == os.path.normcase(manifest_name)
        is_clip = (
            animation_path and Path(path).suffix.casefold().lstrip(".") in ANIMATION_EXTENSIONS
        )
        if not is_manifest and not is_clip:
            return await super().get_response(path, scope)
        library = get_animation_library(Path(self.directory) / "animations")
        if is_clip:
            if len(parts) != 2:
                raise HTTPException(status_code=404)
            try:
                selected = await asyncio.to_thread(library.resolve_file, parts[1])
                info = await asyncio.to_thread(selected.stat)
            except (FileNotFoundError, AnimationStoreError) as exc:
                raise HTTPException(status_code=404, detail="Animation file not found") from exc
            except (OSError, ValueError) as exc:
                raise HTTPException(
                    status_code=503,
                    detail="Animation file temporarily unavailable",
                    headers={"Retry-After": "1", "Cache-Control": "no-store"},
                ) from exc
            return self.file_response(str(selected), info, scope)
        try:
            manifest = await asyncio.to_thread(library.merged_manifest)
            raw = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Animation manifest not found") from exc
        except AnimationStoreError as exc:
            raise HTTPException(status_code=404, detail="Animation manifest unavailable") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail="Animation manifest temporarily unavailable",
                headers={"Retry-After": "1", "Cache-Control": "no-store"},
            ) from exc
        # Send the same bytes whose length was measured, not a FileResponse
        # that reopens a possibly replaced file later while streaming.
        return Response(
            content=raw if scope["method"] == "GET" else b"",
            media_type="application/json",
            headers={"Content-Length": str(len(raw)), "Cache-Control": "no-store"},
        )


def mount_static_files(face_dir: Path) -> None:
    """Mount the face/ directory and ~/.iris/avatars/ for serving static files."""
    runtime = runtime_policy.get_runtime()
    if runtime.packaged:
        face_dir = runtime.assets_root
    app.state.face_dir = face_dir.resolve()
    if face_dir.exists():
        app.mount(
            "/face", _FaceStaticFiles(directory=str(app.state.face_dir), html=True), name="face",
        )
        logger.info("Serving face UI from %s", face_dir)
    else:
        logger.warning("Face directory not found: %s", face_dir)
        
    # Mount avatars directory so Three.js can fetch the VRM files over HTTP
    from core.config import get_iris_home
    avatars_dir = get_iris_home() / "avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/avatars", StaticFiles(directory=str(avatars_dir)), name="avatars")
    logger.info("Serving avatars from %s", avatars_dir)

