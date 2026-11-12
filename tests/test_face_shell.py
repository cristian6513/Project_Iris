"""Small regression checks for the boot-critical IRIS face shell."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_face_shell_uses_the_ids_expected_by_javascript_and_css() -> None:
    html = (ROOT / "face" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")
    activity_js = (ROOT / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    styles = (ROOT / "face" / "css" / "styles.css").read_text(encoding="utf-8")

    assert 'id="IRIS-app"' in html
    assert 'id="IRIS-canvas"' in html
    assert "getElementById('IRIS-canvas')" in app_js
    assert "getElementById('IRIS-app')" in activity_js
    assert "#IRIS-app" in styles
    assert "#IRIS-canvas" in styles

    assert 'id="alice-app"' not in html
    assert 'id="alice-canvas"' not in html
    assert "getElementById('alice-app')" not in activity_js


def test_face_shell_identity_uses_the_styled_iris_classes() -> None:
    html = (ROOT / "face" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "face" / "css" / "styles.css").read_text(encoding="utf-8")

    for class_name in ("IRIS-identity", "IRIS-name", "IRIS-version"):
        assert f'class="{class_name}"' in html
        assert f".{class_name}" in styles

    # 2026-10-18: the room labels moved to room_labels.js; app.js passes it the version.
    labels = (ROOT / "face" / "js" / "room_labels.js").read_text(encoding="utf-8")
    assert "'.IRIS-name': 'IRIS'" in labels
    assert "`v${String(irisVersion).trim()}` : 'v—'" in labels
    assert "normalizeRoomText(document, this.config.iris_version || '')" in app_js


def test_face_shell_loads_responsive_overrides_and_resets_desktop_transform() -> None:
    html = (ROOT / "face" / "index.html").read_text(encoding="utf-8")
    responsive = (ROOT / "face" / "css" / "iris_os_responsive.css").read_text(encoding="utf-8")

    assert 'href="css/iris_os_responsive.css?v=6"' in html
    assert "@media (max-width: 920px)" in responsive
    assert "transform: none" in responsive
    assert ".os-right-rail" in responsive
    assert "display: none" in responsive


def test_face_dashboard_does_not_ship_fake_health_percentages() -> None:
    activity_js = (ROOT / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")

    assert "Micrófono 100%" not in activity_js
    assert "Project Load" not in activity_js
    assert "--metric:22%" not in activity_js
    assert "'/api/system-metrics'" in activity_js
    assert "mediaDevices.enumerateDevices()" in activity_js
    assert "_responseError(response" in activity_js
    assert "await res.text()" not in activity_js


def test_threejs_uses_one_import_map_version() -> None:
    micro_life = (ROOT / "face" / "js" / "micro_life.js").read_text(encoding="utf-8")
    assert "from 'three'" in micro_life
    assert "three@0.169.0" not in micro_life


def test_pdf_attachments_are_extracted_while_unknown_binaries_stay_explicit() -> None:
    html = (ROOT / "face" / "index.html").read_text(encoding="utf-8")
    panel_js = (ROOT / "face" / "js" / "panel.js").read_text(encoding="utf-8")

    assert "Adjuntar imagen, PDF, texto o referencia de archivo" in html
    assert "'/api/chat/attachments/pdf'" in panel_js
    assert "kind: 'document'" in panel_js
    assert "Contenido extraído localmente por IRIS" in panel_js
    assert "el archivo PDF bruto no fue enviado" in panel_js
    assert "solo referencia · contenido no leído" in panel_js
    assert "IRIS no recibirá el contenido" in panel_js
    assert "_OTHER_MAX_BYTES" not in panel_js


def test_iris_os_has_a_visible_fallback_entry_point() -> None:
    html = (ROOT / "face" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")

    assert 'id="btn-iris-os"' in html
    assert 'aria-label="Abrir el escritorio de IRIS OS"' in html
    assert "getElementById('btn-iris-os')" in app_js
    assert "this.activityCanvas?.open?.()" in app_js


def test_chat_keeps_drafts_when_the_runtime_is_reconnecting() -> None:
    app_js = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")
    panel_js = (ROOT / "face" / "js" / "panel.js").read_text(encoding="utf-8")
    palette_js = (ROOT / "face" / "js" / "command_palette.js").read_text(encoding="utf-8")
    websocket_js = (ROOT / "face" / "js" / "websocket.js").read_text(encoding="utf-8")

    assert "this.panel.canSendMessage = () => Boolean(this.ws?.connected);" in app_js
    assert "return this.ws?.send('hermes_command'" in app_js
    assert "const sent = this.onSendMessage?.(finalText" in panel_js
    assert panel_js.index("if (sent === false)") < panel_js.index("this._clearAttachments();")
    assert "if (accepted === false) return true;" in palette_js
    assert "this._reconnectTimer = null;" in websocket_js
    assert "if (!this._shouldReconnect || this._reconnectTimer !== null) return;" in websocket_js
    assert "this._clearReconnectTimer();" in websocket_js
    assert "this._IMAGE_TOTAL_MAX_BYTES = 10 * 1024 * 1024" in panel_js
    assert "this._ATTACHMENT_MAX_COUNT = 8" in panel_js


def test_command_center_uses_real_state_and_accessible_navigation() -> None:
    html = (ROOT / "face" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")
    panel_js = (ROOT / "face" / "js" / "panel.js").read_text(encoding="utf-8")

    assert "gatewayViewFromProbe(d)" in panel_js
    assert "restarting ? '/api/restart-gateway' : '/api/start-gateway'" in panel_js
    assert "this.panel?.setRuntimeConnection?.(connected);" in app_js
    assert "this._openCommandModule('camera');" in panel_js
    assert "this._openCommandModule('chat');" in panel_js
    assert "this._openCommandModule('brain');" in panel_js
    assert "header?.setAttribute('aria-expanded'" in panel_js
    assert "body.inert = !expanded;" in panel_js
    assert "node.classList.contains('command-section-hidden')" in panel_js
    assert 'aria-controls="side-panel" aria-expanded="false"' in html
    assert html.index('id="panel-toggle"') < html.index('id="side-panel"')
    assert "if (this.isOpen && !document.hidden)" in panel_js


def test_model_text_and_marketplace_links_are_rendered_safely() -> None:
    app_js = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")
    subtitles_js = (ROOT / "face" / "js" / "subtitles.js").read_text(encoding="utf-8")
    activity_js = (ROOT / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")

    assert "this.container.innerHTML" not in subtitles_js
    assert "document.createTextNode(safeText.slice(1))" in subtitles_js
    assert "loading.replaceChildren(message);" in app_js
    assert "_safeExternalUrl(value)" in activity_js
    assert "url.protocol !== 'http:' && url.protocol !== 'https:'" in activity_js
    assert 'rel="noopener noreferrer"' in activity_js


def test_avatar_changes_from_iris_os_use_persistent_managed_storage() -> None:
    app_js = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")
    panel_js = (ROOT / "face" / "js" / "panel.js").read_text(encoding="utf-8")
    companion_js = (ROOT / "face" / "companion" / "companion.js").read_text(encoding="utf-8")
    # Quick Configuration's upload lives in its avatar actions (Codex qa10 review, R5).
    actions_js = (ROOT / "face" / "js" / "avatar_intent.js").read_text(encoding="utf-8")

    assert "await avatarActions.upload(file)" in panel_js
    assert "fetchImpl('/api/avatar/upload'" in actions_js
    assert "fetch('/api/avatar/upload'" in app_js
    assert "URL.createObjectURL(file)" not in panel_js
    assert "URL.createObjectURL" not in actions_js
    assert "companionAvatarSelection" in companion_js
    assert "this._avatarSync" in companion_js
    assert "coreAssetUrl" in companion_js
    # «avatar», not «VRM», since PMX ZIPs load from the same button (2026-11-09).
    assert "Usar mi propio avatar" in panel_js and "Volver a IRIS" in panel_js


def test_marketplace_is_a_separate_iris_os_application() -> None:
    activity_js = (ROOT / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    styles = (ROOT / "face" / "css" / "styles.css").read_text(encoding="utf-8")

    skills_view = activity_js[
        activity_js.index('data-workbench-view="skills"'):
        activity_js.index('data-workbench-view="marketplace"')
    ]
    # F09.1: registered by its manifest; the launcher shows its shorter label.
    manifest = json.loads((ROOT / "apps" / "marketplace" / "app.json").read_text(encoding="utf-8"))
    assert (manifest["name"], manifest["presentation"]) == ("IRIS Marketplace", "window")
    assert manifest["launcher"]["label"] == "Marketplace"
    assert 'data-os-open="marketplace"' in activity_js
    assert 'data-workbench-view="marketplace"' in activity_js
    assert 'class="os-marketplace-list"' in activity_js
    assert "loadMarketplace(force = false)" in activity_js
    assert "_hasVisibleAppSurface('marketplace')" in activity_js
    assert "os-skills-market" not in skills_view
    assert "MCP Tools instalados" in activity_js
    assert "this._mcpCatalog.filter((entry) => entry.installed)" in activity_js
    assert "data-market-action" in activity_js
    assert "marketplacePage(filtered, this._marketplaceVisibleLimit)" in activity_js
    assert "q', normalizedQuery" in activity_js
    assert "this._skillsMarketPage + 1" in activity_js
    assert "advertisedSkills.toLocaleString('es-CO')" in activity_js
    assert "notation: 'compact'" in activity_js
    assert "data-market-load-more" in activity_js
    assert "compactMarketplace" not in activity_js
    assert ".os-window-marketplace" in styles
    assert ".os-market-load-more" in styles
    assert "container-type: inline-size; container-name: marketplace;" in styles
    assert "@container marketplace (max-width: 470px)" in styles


def test_vrm_runtime_uses_supported_optimizers_and_explicit_look_at_proxy() -> None:
    avatar_js = (ROOT / "face" / "js" / "avatar.js").read_text(encoding="utf-8")
    animation_js = (ROOT / "face" / "js" / "vrm_animation_loader.js").read_text(encoding="utf-8")

    assert "VRMUtils.combineSkeletons(gltf.scene)" in avatar_js
    assert "VRMLookAtQuaternionProxy" in animation_js
    assert "ensureLookAtProxy(this.vrm);" in animation_js
