"""Regression coverage for the IRIS OS runtime dashboard and LAN controls."""

from __future__ import annotations

import asyncio
import json
import os
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

import core.config as config_module
from core.config import IRISConfig
from core.identity_api import IdentityUpdate
from core.request_security import is_trusted_local_origin
from core.websocket_server import (
    _CORS_ORIGIN_REGEX,
    _apply_git_update,
    _git_update_snapshot,
    _handle_face_message,
    _hermes_capabilities_snapshot,
    _read_current_reasoning_effort,
    _write_reasoning_effort,
    _system_metrics_snapshot,
    app,
    cron_jobs,
    gateway_status,
    get_config,
    get_status,
    network_info,
    update_lan_access,
)
from core import __version__ as CORE_VERSION
from core.version import __version__ as IRIS_VERSION
from wizard.app import ConfigUpdate, app as wizard_app, save_config
from wizard.hermes_config import write_api_server_host


def _json_body(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def _minimal_vrm_glb() -> bytes:
    document = json.dumps({
        "asset": {"version": "2.0"},
        "nodes": [{}],
        "extensionsUsed": ["VRMC_vrm"],
        "extensions": {
            "VRMC_vrm": {
                "specVersion": "1.0",
                "humanoid": {"humanBones": {"hips": {"node": 0}}},
            },
        },
    }, separators=(",", ":")).encode("utf-8")
    document += b" " * ((-len(document)) % 4)
    json_chunk = len(document).to_bytes(4, "little") + b"JSON" + document
    total_length = 12 + len(json_chunk)
    return b"glTF" + (2).to_bytes(4, "little") + total_length.to_bytes(4, "little") + json_chunk


def test_config_save_merges_fields_from_stale_readers(monkeypatch, tmp_path: Path) -> None:
    iris_home = tmp_path / ".iris"
    iris_home.mkdir()
    monkeypatch.setattr("core.config.get_iris_home", lambda: iris_home)
    IRISConfig().save()

    voice_writer = IRISConfig.load()
    companion_writer = IRISConfig.load()
    voice_writer.tts_provider = "kokoro"
    companion_writer.companion_scale = 1.25
    voice_writer.save()
    companion_writer.save()

    persisted = json.loads((iris_home / "config.json").read_text(encoding="utf-8"))
    assert persisted["tts_provider"] == "kokoro"
    assert persisted["companion_scale"] == 1.25


def test_system_metrics_never_invent_values() -> None:
    snapshot = _system_metrics_snapshot()
    assert isinstance(snapshot["available"], bool)
    assert snapshot["source"] in {"psutil", "unavailable"}
    for key in ("cpu_percent", "memory_percent", "disk_percent"):
        value = snapshot[key]
        if snapshot["available"]:
            assert isinstance(value, (int, float))
            assert 0 <= value <= 100
        else:
            assert value is None


def test_async_runs_require_an_explicit_hermes_capability() -> None:
    unrelated = {
        "object": "hermes.api_server.capabilities",
        "features": {"chat_completions": True, "session_resources": True},
        "endpoints": {"sessions": {"method": "GET", "path": "/api/sessions"}},
    }
    with patch(
        "core.websocket_server._hermes_api_request",
        side_effect=[
            {"ok": True, "status": 200, "data": {"status": "ok"}},
            {"ok": True, "status": 200, "data": unrelated},
        ],
    ):
        snapshot = _hermes_capabilities_snapshot()

    assert snapshot["success"] is True
    assert snapshot["supports"]["runs"] is False
    assert snapshot["supports"]["sessions"] is True


def test_async_runs_accept_the_official_submission_contract() -> None:
    official = {
        "features": {"run_submission": True},
        "endpoints": {"runs": {"method": "POST", "path": "/v1/runs"}},
    }
    with patch(
        "core.websocket_server._hermes_api_request",
        side_effect=[
            {"ok": True, "status": 200, "data": {"status": "ok"}},
            {"ok": True, "status": 200, "data": official},
        ],
    ):
        snapshot = _hermes_capabilities_snapshot()

    assert snapshot["success"] is True
    assert snapshot["supports"]["runs"] is True


def test_cron_bridge_does_not_block_the_runtime_event_loop() -> None:
    def slow_list(**_kwargs):
        time.sleep(0.2)
        return {"success": True, "jobs": [], "count": 0}

    async def scenario() -> float:
        task = asyncio.create_task(cron_jobs())
        started = asyncio.get_running_loop().time()
        await asyncio.sleep(0.02)
        responsiveness = asyncio.get_running_loop().time() - started
        await task
        return responsiveness

    with patch("core.hermes_cron.list_cron_jobs", side_effect=slow_list):
        responsiveness = asyncio.run(scenario())

    assert responsiveness < 0.1


def test_gateway_status_respects_a_remote_hermes_configuration() -> None:
    cfg = IRISConfig(hermes_url="https://hermes.example/api", hermes_api_key="remote-secret")
    with (
        patch("core.websocket_server.IRISConfig.load", return_value=cfg),
        patch(
            "core.websocket_server._hermes_api_request",
            return_value={
                "ok": True,
                "status": 200,
                "url": "https://hermes.example/api/health",
                "data": {"status": "ok"},
            },
        ) as request,
    ):
        payload = _json_body(asyncio.run(gateway_status()))

    request.assert_called_once_with("GET", "/health", timeout=5)
    assert payload["managed"] is False
    assert payload["remote"] is True
    assert payload["healthy"] is True


def test_iris_os_frontend_distinguishes_offline_from_incompatible_hermes() -> None:
    source = (Path(__file__).resolve().parents[1] / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")

    assert "asyncRunCapabilities" in source
    assert "/api/hermes/async-agents" in source
    assert "caps.canCreate" in source
    assert "El servicio de IA está detenido o no responde" in source
    assert "actualiza Hermes si quieres Async Agents completo" not in source
    assert "this._hasVisibleAppSurface('marketplace')" in source
    assert "this._activeTab !== 'skills'" not in source


def test_lan_panel_distinguishes_network_bind_and_offline_qr() -> None:
    source = (Path(__file__).resolve().parents[1] / "face" / "js" / "panel.js").read_text(encoding="utf-8")

    assert "Acceso LAN desactivado" in source
    assert "Acceso LAN pendiente de reinicio" in source
    assert "QR local no disponible · copia la URL" in source


def test_desktop_has_no_runtime_pulse() -> None:
    # The user removed «Runtime Pulse» (2026-10-14): mostly the page's own memory, it read
    # like a load figure without being one. The dock sits low so windows get that height.
    root = Path(__file__).resolve().parents[1] / "face"
    source = (root / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    styles = (root / "css" / "styles.css").read_text(encoding="utf-8")

    assert "Runtime Pulse" not in source
    assert "os-home-load" not in source and "os-home-load" not in styles
    assert "Activity Load" not in source
    assert "padding-bottom: clamp(14px, 2.4vh, 24px);" in styles


def test_wake_dependency_probe_only_runs_when_settings_are_open() -> None:
    source = (Path(__file__).resolve().parents[1] / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    constructor = source[source.index("constructor("):source.index("_bindMonitor()")]
    loader_start = source.index("_loadActiveTab({")
    active_tab_loader = source[loader_start:source.index("_syncMissionPolling()", loader_start)]
    adapters = source[source.index("_createAppAdapters() {"):source.index("_loadApp(name = ''")]
    settings_adapter = adapters[adapters.index("settings: {"):]

    assert "this._loadWakeSettings();" not in constructor
    # Only the settings adapter probes wake, and only when the settings tab loads.
    assert "this._loadApp(this._activeTab, { force });" in active_tab_loader
    assert adapters.count("this._loadWakeSettings();") == 1
    assert "this._loadWakeSettings();" in settings_adapter


def test_os_window_reopen_clears_persisted_minimized_state() -> None:
    source = (Path(__file__).resolve().parents[1] / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    open_window = source[source.index("_openWindow(name = '',"):source.index("_closeWindow(name = '')")]

    assert "existing.el.classList.remove('minimized');" in open_window
    assert "minimized: false" in open_window
    assert open_window.index("minimized: false") < open_window.index("this._focusWindow(name, { moveFocus: true });")
    assert "minimized: preserveMinimized && !!this._windowLayout?.[name]?.minimized" in open_window
    assert "this._openWindow(name, { preserveMinimized: true })" in source


def test_os_window_resize_respects_compact_workspace_limits() -> None:
    source = (Path(__file__).resolve().parents[1] / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    pointer_start = source.index("\n    _handleWindowPointerDown(ev) {")
    resize_logic = source[pointer_start:source.index("\n    _finishWindowDrag() {", pointer_start)]
    responsive = (Path(__file__).resolve().parents[1] / "face" / "css" / "iris_os_responsive.css").read_text(encoding="utf-8")

    assert "minW: Math.min(360, availableW)" in resize_logic
    assert "minH: Math.min(300, availableH)" in resize_logic
    assert "Math.max(this._dragWindow.minW" in resize_logic
    # QA12: a west or north edge may grow only up to the layer's start.
    assert "edge.includes('w') ? startLeft + rect.width" in resize_logic
    assert "edge.includes('n') ? startTop + rect.height" in resize_logic
    assert ".os-window-titlebar nav" in responsive
    assert "flex: 0 0 24px" in responsive


def test_os_windows_resize_from_every_edge_and_corner() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    css = (root / "face" / "css" / "styles.css").read_text(encoding="utf-8")
    assert "const OS_WINDOW_EDGES = Object.freeze(['n', 's', 'e', 'w', 'ne', 'nw', 'sw']);" in source
    assert 'data-window-resize="se"' in source
    assert 'data-window-resize="${edge}"' in source
    for edge in ("n", "s", "e", "w", "ne", "nw", "sw"):
        assert f".os-window-edge-{edge}" in css
    assert ".os-window.maximized .os-window-edge," in css


def test_all_os_windows_reserve_the_bottom_desktop_rails() -> None:
    source = (Path(__file__).resolve().parents[1] / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    start = source.index("\n    _windowBottomClearance(")
    geometry = source[start:source.index("\n    _reflowOpenWindows()", start)]

    assert "let top = dockRect.top;" in geometry
    # When the window bar sits above the dock (no room beside it), windows stop above the bar too.
    assert "if (this._windowBarPlacement()?.stacked) top -= OS_WINDOW_BAR.height + OS_WINDOW_BAR.gap;" in geometry
    assert "layerRect.bottom - top + 10" in geometry
    assert "return Number(layerHeight) < 760 ? 194 : 92;" in geometry
    assert "layerHeight - bottomClearance - topInset" in geometry
    assert "layerHeight - bottomClearance - height" in geometry
    assert "compactMarketplace" not in geometry


def test_os_windows_expose_dialog_semantics_focus_and_escape_close() -> None:
    source = (Path(__file__).resolve().parents[1] / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    open_window = source[source.index("_openWindow(name = '',"):source.index("_minimizeWindow(name = '')")]

    assert "win.setAttribute('role', 'dialog');" in open_window
    assert "win.setAttribute('aria-labelledby', titleId);" in open_window
    assert "this._focusWindow(name, { moveFocus: true });" in open_window
    assert "_handleWorkbenchKeydown(ev)" in source
    assert "if (ev.key !== 'Escape'" in source
    assert ".os-home-launcher:not(.open)" in source


def test_agent_events_only_render_visible_os_applications() -> None:
    source = (Path(__file__).resolve().parents[1] / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    panel = source[source.index("_renderPanel() {"):source.index("_gatewayRuntimeState()")]

    assert "if (!this._open) return;" in panel
    assert "this._renderVisibleAppSurfaces();" in panel
    assert "if (this._hasVisibleAppSurface('marketplace')) this._renderSkillsMarketplace();" in panel
    assert "if (this._hasVisibleAppSurface('skills')) this._renderSkills();" in panel


def test_launcher_exposes_visual_device_and_system_apps() -> None:
    source = (Path(__file__).resolve().parents[1] / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    styles = (Path(__file__).resolve().parents[1] / "face" / "css" / "styles.css").read_text(encoding="utf-8")
    launcher = source[source.index('<div class="os-launcher-grid">'):source.index('</section>', source.index('<div class="os-launcher-grid">'))]
    renderer = source[source.index("_launcherButtonsHtml() {"):source.index(".join('');", source.index("_launcherButtonsHtml() {"))]

    # F09.1: the launcher lists the registry's visible apps, theme, camera and settings included.
    assert "${this._launcherButtonsHtml()}" in launcher
    assert "OS_APPS.launcher().map(" in renderer and 'data-os-open="${this._escapeAttr(app.id)}"' in renderer
    for app_id in ("theme", "camera", "settings"):
        manifest = json.loads((Path(__file__).resolve().parents[1] / "apps" / app_id / "app.json").read_text(encoding="utf-8"))
        assert manifest["launcher"]["visible"] is True
    assert 'data-settings-jump="settings-system"' in source
    assert "this._windowZ = nextZ;" in source
    launcher_css = styles[styles.index(".os-home-launcher {"):styles.index(".os-home-launcher.open")]
    assert "z-index: 40;" in launcher_css


def test_os_wake_settings_hide_html_and_invalid_json_errors() -> None:
    source = (Path(__file__).resolve().parents[1] / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    wake_loader = source[source.index("async _loadWakeSettings()"):source.index("_wakeNameFromPhrase")]
    saver_start = source.index("async _saveWakeSettings()")
    wake_saver = source[saver_start:source.index("\n    _renderWakeSettings() {", saver_start)]

    assert "this._responseJson(response, 'Activación de Hermes')" in wake_loader
    assert "this._responseJson(response, 'Guardar activación de Hermes')" in wake_saver
    assert "respuesta inválida del backend" in source
    assert "<!doctype" in source


def test_cron_speech_reports_backend_failures() -> None:
    source = (Path(__file__).resolve().parents[1] / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    start = source.index("async _speakCronOutput(outputId = '')")
    speak_action = source[start:source.index("async loadMcpCatalog", start)]

    assert "const response = await fetch('/api/cron/speak'" in speak_action
    assert "if (!response.ok)" in speak_action
    assert "this._responseError(response, 'Narración de la rutina')" in speak_action


def test_release_version_has_one_python_source_and_is_exposed() -> None:
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert pyproject["project"]["dynamic"] == ["version"]
    assert "version" not in pyproject["project"]
    assert pyproject["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "core.version.__version__"
    }
    assert app.version == IRIS_VERSION
    assert wizard_app.version == IRIS_VERSION
    assert CORE_VERSION == IRIS_VERSION
    assert "tu-usuario" not in str(pyproject)
    assert "Development Status :: 4 - Beta" in pyproject["project"]["classifiers"]

    status = _json_body(asyncio.run(get_status()))
    with patch("core.websocket_server.IRISConfig.load", return_value=IRISConfig()):
        config = _json_body(asyncio.run(get_config()))
    assert status["version"] == IRIS_VERSION
    assert config["iris_version"] == IRIS_VERSION


def test_public_config_is_an_allowlist_and_never_exposes_credentials() -> None:
    configured = IRISConfig(
        assistant_name="Nova",
        hermes_api_key="hermes-secret",
        telegram_bot_token="telegram-secret",
        discord_bot_token="discord-secret",
        slack_bot_token="slack-secret",
        whisper_api_key="whisper-secret",
        elevenlabs_api_key="eleven-secret",
        minimax_api_key="minimax-secret",
        lan_pairing_token="lan-secret",
    )
    with patch("core.websocket_server.IRISConfig.load", return_value=configured):
        payload = _json_body(asyncio.run(get_config()))

    assert payload["assistant_name"] == "Nova"
    assert payload["iris_version"] == IRIS_VERSION
    for field_name in (
        "hermes_api_key",
        "telegram_bot_token",
        "discord_bot_token",
        "slack_bot_token",
        "whisper_api_key",
        "elevenlabs_api_key",
        "minimax_api_key",
        "lan_pairing_token",
    ):
        assert field_name not in payload
    assert all("secret" not in str(value) for value in payload.values())


def test_the_avatar_is_always_a_vrm_and_old_avatar_kinds_load_as_iris() -> None:
    """2026-10-28: the geometric and particle avatars were removed. An old config or
    client naming them still loads: no model of the person's own means IRIS' VRM."""
    for kind in ("procedural", "particle", "vrm"):
        config = IRISConfig(avatar_type=kind, particle_style="jarvis")
        assert config.avatar_vrm_path == ""
        assert "avatar_type" not in config.model_dump() and "particle_style" not in config.model_dump()
    assert not hasattr(IdentityUpdate(avatar_type="particle"), "avatar_type")
    assert IdentityUpdate(avatar_vrm_path="").avatar_vrm_path == ""


def test_os_avatar_upload_persists_the_model_and_active_config(monkeypatch, tmp_path: Path) -> None:
    iris_home = tmp_path / ".iris"
    iris_home.mkdir()
    monkeypatch.setattr("core.config.get_iris_home", lambda: iris_home)
    IRISConfig().save()

    with TestClient(
        app,
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 50000),
    ) as client:
        response = client.post(
            "/api/avatar/upload",
            files={"file": ("Mi Avatar.vrm", _minimal_vrm_glb(), "model/gltf-binary")},
        )
        public_config = client.get("/api/config")

    assert response.status_code == 200
    assert response.json()["avatar_url"] == "/avatars/os-Mi-Avatar.vrm"
    persisted = IRISConfig.load()
    assert Path(persisted.avatar_vrm_path).read_bytes() == _minimal_vrm_glb()
    assert public_config.json()["avatar_vrm_path"] == "/avatars/os-Mi-Avatar.vrm"
    assert "avatar_type" not in public_config.json() and "particle_style" not in public_config.json()


def test_os_avatar_upload_rejects_a_fake_vrm_without_replacing_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    iris_home = tmp_path / ".iris"
    iris_home.mkdir()
    monkeypatch.setattr("core.config.get_iris_home", lambda: iris_home)
    IRISConfig().save()

    with TestClient(
        app,
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 50000),
    ) as client:
        response = client.post(
            "/api/avatar/upload",
            files={"file": ("fake.vrm", b"not-a-binary-gltf", "model/gltf-binary")},
        )

    assert response.status_code == 400
    assert IRISConfig.load().avatar_vrm_path == "", "IRIS stays"
    assert not (iris_home / "avatars").exists()


def test_os_theme_image_is_stored_outside_browser_local_storage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    iris_home = tmp_path / ".iris"
    iris_home.mkdir()
    monkeypatch.setattr("core.config.get_iris_home", lambda: iris_home)
    png = b"\x89PNG\r\n\x1a\n" + b"IRIS" * 8

    with TestClient(
        app,
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 50000),
    ) as client:
        stored = client.post(
            "/api/os/theme-image",
            data={"kind": "profile"},
            files={"file": ("portrait.png", png, "image/png")},
        )
        loaded = client.get(stored.json()["url"])

    assert stored.status_code == 200
    assert stored.json()["url"].startswith("/api/os/theme-image/profile?v=")
    assert (iris_home / "ui-assets" / "os-profile.png").read_bytes() == png
    assert loaded.status_code == 200
    assert loaded.headers["content-type"].startswith("image/png")


def test_reasoning_patch_only_changes_the_agent_yaml_block(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "model:\n  reasoning_effort: high\nagent:\n  reasoning_effort: low\ntools:\n  reasoning_effort: none\n",
        encoding="utf-8",
    )
    with patch("core.websocket_server._hermes_config_path", return_value=config_path):
        assert _read_current_reasoning_effort() == "low"
        assert _write_reasoning_effort("medium") == (True, "guardado")

    updated = config_path.read_text(encoding="utf-8")
    assert "model:\n  reasoning_effort: high" in updated
    assert "agent:\n  reasoning_effort: medium" in updated
    assert "tools:\n  reasoning_effort: none" in updated


def test_final_wizard_save_rejects_an_older_browser_generation() -> None:
    async def scenario() -> tuple[int, int]:
        newer_request = SimpleNamespace(headers={
            "x-iris-save-session": "test-session",
            "x-iris-save-generation": "2",
        })
        older_request = SimpleNamespace(headers={
            "x-iris-save-session": "test-session",
            "x-iris-save-generation": "1",
        })
        accepted = await save_config(ConfigUpdate(), newer_request)
        stale = await save_config(ConfigUpdate(), older_request)
        return accepted.status_code, stale.status_code

    with patch(
        "wizard.app._save_config_impl",
        new=AsyncMock(return_value=JSONResponse({"status": "saved"})),
    ) as impl:
        accepted_status, stale_status = asyncio.run(scenario())

    assert accepted_status == 200
    assert stale_status == 409
    impl.assert_awaited_once()


def test_compose_build_context_has_a_runnable_iris_image() -> None:
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    ignored = (root / ".dockerignore").read_text(encoding="utf-8")
    nginx = (root / "docker" / "nginx.conf").read_text(encoding="utf-8")

    assert 'CMD ["python", "-m", "core.agent"]' in dockerfile
    assert "http://127.0.0.1:8765/api/status" in dockerfile
    assert compose.count("dockerfile: Dockerfile") == 2
    assert "./docker/nginx.conf:/etc/nginx/nginx.conf:ro" in compose
    assert "proxy_pass http://iris-core:8765/ws" in nginx
    assert "proxy_pass http://iris-core:8765" in nginx
    assert "location /avatars/" in nginx
    assert "IRIS_CONTAINER_PROXY_MODE=true" in compose
    assert "IRIS_WEBSOCKET_HOST=0.0.0.0" in compose
    assert '"127.0.0.1:8765:8765"' in compose
    assert '"127.0.0.1:3000:80"' in compose
    assert "companion/src-tauri/target" in ignored


def test_config_uses_pydantic_v2_settings_configuration() -> None:
    assert IRISConfig.model_config["env_prefix"] == "IRIS_"
    assert IRISConfig.model_config["env_file"] == ".env"
    assert IRISConfig.model_config["extra"] == "ignore"


def test_iris_home_merges_missing_legacy_data_without_overwriting_current(
    monkeypatch,
    tmp_path: Path,
) -> None:
    legacy = tmp_path / ".alice"
    current = tmp_path / ".iris"
    legacy.mkdir()
    current.mkdir()
    (legacy / "config.json").write_text('{"assistant_name":"Legacy"}', encoding="utf-8")
    (legacy / "voice.bin").write_bytes(b"voice")
    (current / "config.json").write_text('{"assistant_name":"Current"}', encoding="utf-8")
    monkeypatch.delenv("IRIS_HOME", raising=False)
    monkeypatch.setattr(config_module.Path, "home", lambda: tmp_path)

    resolved = config_module.get_iris_home()

    assert resolved == current
    assert json.loads((current / "config.json").read_text(encoding="utf-8"))["assistant_name"] == "Current"
    assert (current / "voice.bin").read_bytes() == b"voice"
    assert (legacy / "config.json").is_file()


def test_iris_home_rewrites_absolute_profile_references_after_copy_fallback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    legacy = tmp_path / ".alice"
    current = tmp_path / ".iris"
    legacy_executable = legacy / "companion" / "app" / "iris-desktop-companion.exe"
    legacy_voice = legacy / "voices" / "reference.wav"
    legacy_avatar = legacy / "avatars" / "iris.vrm"
    legacy_executable.parent.mkdir(parents=True)
    legacy_voice.parent.mkdir(parents=True)
    legacy_avatar.parent.mkdir(parents=True)
    legacy_executable.write_bytes(b"running-copy")
    legacy_voice.write_bytes(b"voice")
    legacy_avatar.write_bytes(b"avatar")
    (legacy / "config.json").write_text(json.dumps({
        "fish_speech_ref_audio": str(legacy_voice),
        "avatar_vrm_path": str(legacy_avatar),
    }), encoding="utf-8")
    (legacy / "companion_install.json").write_text(json.dumps({
        "schema": 1,
        "product": "IRIS Desktop Companion",
        "executable": str(legacy_executable),
    }), encoding="utf-8")
    appdata = tmp_path / "AppData" / "Roaming"
    startup = (
        appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        / "IRIS-Desktop-Companion.vbs"
    )
    startup.parent.mkdir(parents=True)
    startup.write_text(f'CreateObject("WScript.Shell").Run "{legacy_executable}"', encoding="utf-16")
    original_replace = config_module.Path.replace

    def copy_only(path: Path, target: Path):
        if path == legacy:
            raise OSError("simulated open native executable")
        return original_replace(path, target)

    monkeypatch.delenv("IRIS_HOME", raising=False)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(config_module.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(config_module.Path, "replace", copy_only)

    resolved = config_module.get_iris_home()
    # Migration repair runs on every startup and must remain idempotent: the
    # former process marker cannot be rewritten to the new profile.
    assert config_module.get_iris_home() == current
    config = json.loads((current / "config.json").read_text(encoding="utf-8"))
    record = json.loads((current / "companion_install.json").read_text(encoding="utf-8"))

    assert resolved == current
    assert config["fish_speech_ref_audio"] == str(current / "voices" / "reference.wav")
    assert config["avatar_vrm_path"] == str(current / "avatars" / "iris.vrm")
    assert record["executable"] == str(
        current / "companion" / "app" / "iris-desktop-companion.exe"
    )
    assert record["legacy_executable"] == str(legacy_executable)
    assert str(current) in startup.read_text(encoding="utf-16")


def test_iris_home_keeps_using_complete_legacy_profile_after_partial_copy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    legacy = tmp_path / ".alice"
    current = tmp_path / ".iris"
    legacy.mkdir()
    legacy_voice = legacy / "voices" / "reference.wav"
    legacy_voice.parent.mkdir()
    legacy_voice.write_bytes(b"voice")
    (legacy / "config.json").write_text(json.dumps({
        "fish_speech_ref_audio": str(legacy_voice),
    }), encoding="utf-8")
    original_copytree = config_module.shutil.copytree
    marker_was_present_before_copy = False

    def partial_copy(source: Path, destination: Path, **kwargs):
        nonlocal marker_was_present_before_copy
        marker_was_present_before_copy = (
            destination / ".legacy-migration-incomplete"
        ).read_text(encoding="utf-8") == ".alice"
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "config.json").write_text('{"fish_speech_ref_audio":', encoding="utf-8")
        partial_voice = destination / "voices" / "reference.wav"
        partial_voice.parent.mkdir()
        partial_voice.write_bytes(b"v")
        raise OSError("simulated full disk")

    monkeypatch.delenv("IRIS_HOME", raising=False)
    monkeypatch.setattr(config_module.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(config_module.Path, "replace", lambda path, target: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(config_module.shutil, "copytree", partial_copy)

    assert config_module.get_iris_home() == legacy
    assert marker_was_present_before_copy is True
    assert (current / "config.json").read_text(encoding="utf-8").endswith(":")
    assert (current / "voices" / "reference.wav").read_bytes() == b"v"
    assert (current / ".legacy-migration-incomplete").is_file()

    monkeypatch.setattr(config_module.shutil, "copytree", original_copytree)
    assert config_module.get_iris_home() == current
    repaired = json.loads((current / "config.json").read_text(encoding="utf-8"))
    assert repaired["fish_speech_ref_audio"] == str(current / "voices" / "reference.wav")
    assert not (current / ".legacy-migration-incomplete").exists()


def test_partial_alice_retry_does_not_let_preview_home_overwrite_repaired_data(
    monkeypatch,
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("Windows identifica .IRIS y .iris como el mismo directorio")
    legacy = tmp_path / ".alice"
    preview = tmp_path / ".IRIS"
    current = tmp_path / ".iris"
    legacy.mkdir()
    preview.mkdir()
    current.mkdir()
    (legacy / "config.json").write_text('{"assistant_name":"Canonical legacy"}', encoding="utf-8")
    (preview / "config.json").write_text('{"assistant_name":"Old preview"}', encoding="utf-8")
    (current / "config.json").write_text("{", encoding="utf-8")
    (current / ".legacy-migration-incomplete").write_text(".alice", encoding="utf-8")
    monkeypatch.delenv("IRIS_HOME", raising=False)
    monkeypatch.setattr(config_module.Path, "home", lambda: tmp_path)

    assert config_module.get_iris_home() == current
    repaired = json.loads((current / "config.json").read_text(encoding="utf-8"))
    assert repaired["assistant_name"] == "Canonical legacy"


def test_companion_frontend_invalidates_stale_settings_and_releases_timed_out_camera() -> None:
    root = Path(__file__).resolve().parents[1]
    activity = (root / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    companion = (root / "face" / "companion" / "companion.js").read_text(encoding="utf-8")
    loader = activity[
        activity.index("async _loadCompanionSettings()"):
        activity.index("async _loadCompanionLifecycle()")
    ]
    timeout = companion[
        companion.index("this._cameraLeaseTimer = setTimeout"):
        companion.index("async _handleCameraLeaseResult")
    ]

    assert loader.index("this._companionSettingsLoaded = false") < loader.index("await fetch(")
    assert "this.ws?.send('camera_lease_release', {});" in timeout


def test_iris_environment_wins_while_alice_environment_remains_compatible(monkeypatch) -> None:
    monkeypatch.setenv("ALICE_ASSISTANT_NAME", "Legacy")
    monkeypatch.delenv("IRIS_ASSISTANT_NAME", raising=False)
    assert IRISConfig().assistant_name == "Legacy"

    monkeypatch.setenv("IRIS_ASSISTANT_NAME", "Canonical")
    assert IRISConfig().assistant_name == "Canonical"


def test_iris_environment_overrides_persisted_config_without_rewriting_it(
    monkeypatch, tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"websocket_port": 8765}), encoding="utf-8")
    monkeypatch.setenv("IRIS_WEBSOCKET_PORT", "9999")
    with (
        patch("core.config.get_iris_home", return_value=tmp_path),
        patch("core.config._derive_llm_from_hermes", return_value={}),
    ):
        loaded = IRISConfig.load()

    assert loaded.websocket_port == 9999
    assert json.loads(config_path.read_text(encoding="utf-8"))["websocket_port"] == 8765


def test_legacy_dotenv_is_read_but_canonical_dotenv_wins(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ALICE_ASSISTANT_NAME", raising=False)
    monkeypatch.delenv("IRIS_ASSISTANT_NAME", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "ALICE_ASSISTANT_NAME=Legacy file\nIRIS_ASSISTANT_NAME=Canonical file\n",
        encoding="utf-8",
    )

    assert IRISConfig().assistant_name == "Canonical file"

    (tmp_path / ".env").write_text("ALICE_ASSISTANT_NAME=Legacy file\n", encoding="utf-8")
    assert IRISConfig().assistant_name == "Legacy file"


def test_runtime_defaults_to_loopback_and_repairs_legacy_open_bind(tmp_path: Path) -> None:
    assert IRISConfig().websocket_host == "127.0.0.1"
    (tmp_path / "config.json").write_text(
        json.dumps({"websocket_host": "0.0.0.0", "lan_access": False}),
        encoding="utf-8",
    )
    with (
        patch("core.config.get_iris_home", return_value=tmp_path),
        patch("core.config._derive_llm_from_hermes", return_value={}),
    ):
        loaded = IRISConfig.load()
    assert loaded.websocket_host == "127.0.0.1"
    assert loaded.lan_access is False


def test_container_proxy_mode_keeps_internal_open_bind_without_enabling_lan(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({
            "websocket_host": "0.0.0.0",
            "lan_access": False,
            "container_proxy_mode": True,
        }),
        encoding="utf-8",
    )
    with (
        patch("core.config.get_iris_home", return_value=tmp_path),
        patch("core.config._derive_llm_from_hermes", return_value={}),
    ):
        loaded = IRISConfig.load()
    assert loaded.websocket_host == "0.0.0.0"
    assert loaded.lan_access is False


def test_hermes_bind_writer_accepts_only_safe_supported_values() -> None:
    written: list[dict[str, str]] = []
    with (
        patch("wizard.hermes_config._read_env_file", return_value={"KEEP": "yes"}),
        patch("wizard.hermes_config._write_env_file", side_effect=written.append),
        patch("wizard.hermes_config.secrets.token_urlsafe", return_value="lan-api-secret"),
    ):
        assert write_api_server_host("0.0.0.0") is True
    assert written == [{
        "KEEP": "yes",
        "API_SERVER_HOST": "0.0.0.0",
        "API_SERVER_KEY": "lan-api-secret",
    }]

    repaired: list[dict[str, str]] = []
    with (
        patch(
            "wizard.hermes_config._read_env_file",
            return_value={"API_SERVER_HOST": "0.0.0.0"},
        ),
        patch("wizard.hermes_config._write_env_file", side_effect=repaired.append),
        patch("wizard.hermes_config.secrets.token_urlsafe", return_value="repaired-secret"),
    ):
        assert write_api_server_host("0.0.0.0") is True
    assert repaired == [{
        "API_SERVER_HOST": "0.0.0.0",
        "API_SERVER_KEY": "repaired-secret",
    }]

    with pytest.raises(ValueError):
        write_api_server_host("8.8.8.8")


def test_lan_toggle_updates_both_runtimes_and_reports_restarts() -> None:
    cfg = SimpleNamespace(
        lan_access=False,
        websocket_host="127.0.0.1",
        save=Mock(),
    )
    with (
        patch("core.websocket_server.IRISConfig.load", return_value=cfg),
        patch("wizard.hermes_config.has_hermes_workspace", return_value=True),
        patch("wizard.hermes_config.read_api_server_host", return_value="127.0.0.1"),
        patch("wizard.hermes_config.write_api_server_host", return_value=True) as write_host,
    ):
        payload = _json_body(asyncio.run(update_lan_access(True)))

    assert cfg.lan_access is True
    assert cfg.websocket_host == "0.0.0.0"
    assert len(cfg.lan_pairing_token) >= 32
    cfg.save.assert_called_once_with()
    write_host.assert_called_once_with("0.0.0.0")
    assert payload["needs_runtime_restart"] is True
    assert payload["needs_gateway_restart"] is True


def test_lan_toggle_does_not_create_a_fake_hermes_install() -> None:
    cfg = SimpleNamespace(
        lan_access=False,
        websocket_host="127.0.0.1",
        save=Mock(),
    )
    with (
        patch("core.websocket_server.IRISConfig.load", return_value=cfg),
        patch("wizard.hermes_config.has_hermes_workspace", return_value=False),
        patch("wizard.hermes_config.write_api_server_host") as write_host,
    ):
        payload = _json_body(asyncio.run(update_lan_access(True)))

    write_host.assert_not_called()
    assert len(cfg.lan_pairing_token) >= 32
    assert payload["hermes_bind"] == "unavailable"
    assert payload["needs_gateway_restart"] is False


def test_network_info_reveals_pairing_url_only_to_the_local_owner() -> None:
    cfg = IRISConfig(
        lan_access=True,
        websocket_host="0.0.0.0",
        lan_pairing_token="pair-secret",
    )
    local = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    remote = SimpleNamespace(client=SimpleNamespace(host="192.168.1.25"))
    with patch("core.websocket_server.IRISConfig.load", return_value=cfg):
        local_payload = _json_body(asyncio.run(network_info(local)))
        remote_payload = _json_body(asyncio.run(network_info(remote)))

    assert local_payload["avatar_url"].endswith("/face/?pair=pair-secret")
    assert "pair-secret" not in remote_payload["avatar_url"]
    assert local_payload["wizard_url"] == "http://127.0.0.1:8000/ui/"


def test_lan_api_requires_pairing_and_remote_writes_still_require_origin() -> None:
    cfg = IRISConfig(
        lan_access=True,
        websocket_host="0.0.0.0",
        lan_pairing_token="pair-secret",
    )
    with (
        patch("core.websocket_server.IRISConfig.load", return_value=cfg),
        TestClient(
            app,
            base_url="http://192.168.1.10:8765",
            client=("192.168.1.25", 50000),
        ) as client,
    ):
        denied = client.get("/api/system-metrics")
        paired = client.get(
            "/api/system-metrics?pair=pair-secret",
            follow_redirects=False,
        )
        after_pair = client.get("/api/system-metrics")
        no_origin_write = client.post("/api/lan-access", json={"enabled": False})

    assert denied.status_code == 403
    assert paired.status_code == 303
    assert paired.headers["location"].endswith("/api/system-metrics")
    assert "pair-secret" not in paired.headers["location"]
    assert "HttpOnly" in paired.headers.get("set-cookie", "")
    assert after_pair.status_code == 200
    assert no_origin_write.status_code == 403


def test_paired_lan_client_cannot_administer_the_host() -> None:
    cfg = IRISConfig(
        lan_access=True,
        websocket_host="0.0.0.0",
        lan_pairing_token="pair-secret",
    )
    with (
        patch("core.websocket_server.IRISConfig.load", return_value=cfg),
        TestClient(
            app,
            base_url="http://192.168.1.10:8765",
            client=("192.168.1.25", 50000),
        ) as client,
    ):
        paired = client.get("/api/status?pair=pair-secret", follow_redirects=False)
        blocked = client.post(
            "/api/restart-gateway",
            headers={"Origin": "http://192.168.1.10:8765"},
        )
        logs_blocked = client.get("/api/gateway-logs")

    assert paired.status_code == 303
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "Local administration required"
    assert logs_blocked.status_code == 403


def test_remote_http_and_websocket_are_rejected_when_lan_access_is_disabled() -> None:
    cfg = IRISConfig(lan_access=False, websocket_host="0.0.0.0")
    with (
        patch("core.websocket_server.IRISConfig.load", return_value=cfg),
        TestClient(
            app,
            base_url="http://192.168.1.10:8765",
            client=("192.168.1.25", 50000),
        ) as client,
    ):
        response = client.get("/api/status")
        loopback_host_response = client.get(
            "/api/status", headers={"Host": "127.0.0.1:8765"},
        )
        with pytest.raises(WebSocketDisconnect) as disconnect:
            with client.websocket_connect(
                "/ws",
                headers={"Origin": "http://192.168.1.10:8765"},
            ):
                pass

    assert response.status_code == 403
    assert response.json()["detail"] == "Untrusted HTTP Host"
    assert loopback_host_response.status_code == 403
    assert loopback_host_response.json()["detail"] == "LAN access disabled"
    assert disconnect.value.code == 1008


def test_global_hermes_session_reset_requires_explicit_confirmation() -> None:
    with TestClient(
        app,
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 50000),
    ) as client:
        response = client.post("/api/memory/reset-hermes", json={})

    assert response.status_code == 400
    assert response.json()["detail"] == "confirm_all=true required"


def test_remote_websocket_cannot_launch_the_local_wizard() -> None:
    class RemoteSocket:
        client = SimpleNamespace(host="192.168.1.25")

        def __init__(self) -> None:
            self.messages: list[dict] = []

        async def send_json(self, message: dict) -> None:
            self.messages.append(message)

    socket = RemoteSocket()
    asyncio.run(_handle_face_message(socket, {"type": "launch_wizard", "payload": {}}))

    assert socket.messages[-1]["type"] == "protocol_error"
    assert "equipo principal" in socket.messages[-1]["payload"]["message"]


def test_runtime_rejects_untrusted_browser_mutations_and_wildcard_cors() -> None:
    assert "*" not in _CORS_ORIGIN_REGEX
    with TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/api/lan-access",
            json={"enabled": True},
            headers={"Origin": "https://evil.example"},
        )
    assert response.status_code == 403


def test_browser_origin_policy_allows_same_lan_host_but_not_other_private_sites() -> None:
    assert is_trusted_local_origin("https://192.168.1.20:8765", "192.168.1.20") is True
    assert is_trusted_local_origin("https://192.168.1.99:8765", "192.168.1.20") is False
    assert is_trusted_local_origin("http://127.0.0.1:5177", "localhost") is True
    assert is_trusted_local_origin(
        "http://127.0.0.1:8765",
        "127.0.0.1",
        request_scheme="http",
        request_port=8765,
    ) is True
    assert is_trusted_local_origin(
        "http://127.0.0.1:3000",
        "127.0.0.1",
        request_scheme="http",
        request_port=8765,
    ) is False
    assert is_trusted_local_origin(
        "http://127.0.0.1:bad",
        "127.0.0.1",
        request_scheme="http",
        request_port=8765,
    ) is False


def test_iris_home_rejects_ambiguous_relative_override(monkeypatch) -> None:
    monkeypatch.setenv("IRIS_HOME", "relative/profile")
    with pytest.raises(ValueError, match="ruta absoluta"):
        config_module.get_iris_home()


def test_malformed_websocket_json_values_do_not_break_the_session() -> None:
    class Socket:
        def __init__(self) -> None:
            self.messages: list[dict] = []

        async def send_json(self, message: dict) -> None:
            self.messages.append(message)

    async def scenario() -> list[dict]:
        socket = Socket()
        await _handle_face_message(socket, ["not", "an", "object"])
        await _handle_face_message(socket, {"type": "user_text", "payload": "invalid"})
        await _handle_face_message(socket, {
            "type": "user_text",
            "payload": {"text": "inspect", "images": ["http://127.0.0.1/private"]},
        })
        await _handle_face_message(socket, {"type": "ping"})
        return socket.messages

    messages = asyncio.run(scenario())
    assert [message["type"] for message in messages] == [
        "protocol_error",
        "protocol_error",
        "protocol_error",
        "pong",
    ]


def test_update_check_protects_a_dirty_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "IRIS"
    (repo / ".git").mkdir(parents=True)

    def command(argv, **_kwargs):
        if argv[1:3] == ["rev-parse", "HEAD"]:
            return {"ok": True, "stdout": "aaaaaaaaaaaaaaaa\n", "stderr": ""}
        if argv[1:3] == ["status", "--porcelain"]:
            return {"ok": True, "stdout": " M face/js/app.js\n", "stderr": ""}
        if argv[1:3] == ["branch", "--show-current"]:
            return {"ok": True, "stdout": "main\n", "stderr": ""}
        if argv[1] == "ls-remote":
            return {"ok": True, "stdout": "bbbbbbbbbbbbbbbb\trefs/heads/main\n", "stderr": ""}
        raise AssertionError(argv)

    with (
        patch("core.websocket_server.shutil.which", return_value="git"),
        patch("core.websocket_server._run_local_command", side_effect=command),
    ):
        snapshot = _git_update_snapshot(repo, "IRIS OS")

    assert snapshot["status"] == "blocked_local_changes"
    assert snapshot["can_update"] is False
    assert snapshot["has_local_changes"] is True


def test_update_apply_rechecks_local_changes_before_pull(tmp_path: Path) -> None:
    repo = tmp_path / "IRIS"
    (repo / ".git").mkdir(parents=True)
    status = {
        "ok": True,
        "stdout": "?? user-notes.md\n",
        "stderr": "",
    }
    with patch("core.websocket_server._run_local_command", return_value=status) as run:
        result = _apply_git_update(repo, "IRIS OS")

    assert result["ok"] is False
    assert "preservar cambios locales" in result["detail"]
    run.assert_called_once_with(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=repo,
        timeout=8,
    )
