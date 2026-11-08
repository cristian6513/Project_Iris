"""Static integration coverage for the Continuity Center OS application."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_continuity_center_is_a_real_lazy_loaded_os_window() -> None:
    shell = (ROOT / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")

    assert "import { ContinuityCenterApp }" in shell
    assert "./continuity_center.js?v=9" in shell
    # F09.1: registered by its manifest; the shell keeps the view and its load adapter.
    manifest = json.loads((ROOT / "apps" / "continuity" / "app.json").read_text(encoding="utf-8"))
    # 2026-10-18: the launcher name is now in Spanish; the window is still Continuity Center.
    assert (manifest["name"], manifest["presentation"]) == ("Perfiles y sesiones", "window")
    assert 'data-os-open="continuity"' in shell
    assert 'data-workbench-view="continuity"' in shell
    assert "new ContinuityCenterApp" in shell
    assert "continuity: reload((force) => this._continuityApp?.load?.(force))" in shell


def test_continuity_ui_is_truthful_about_hermes_ownership_and_atomic_resume() -> None:
    ui = (ROOT / "face" / "js" / "continuity_center.js").read_text(encoding="utf-8")
    backend = (ROOT / "core" / "websocket_server.py").read_text(encoding="utf-8")

    assert "Hermes conserva perfiles, memoria y sesiones" in ui
    assert "no abre ni replica la base de datos de Hermes" in ui
    assert "Consultando perfiles" in ui
    assert "Reanudar de forma segura" in ui
    assert "data-continuity-resume" in ui
    assert "data-continuity-session-create" in ui
    assert "data-continuity-fork" in ui
    assert "IRIS:continuity-session-resumed" in ui
    assert '"/api/hermes/continuity"' in backend
    assert '@app.patch("/api/hermes/continuity/sessions/{session_id}")' in backend
    assert '@app.post("/api/hermes/continuity/sessions/{session_id}/resume")' in backend
    assert '@app.post("/api/hermes/continuity/sessions/{session_id}/fork")' in backend
    assert '@app.post("/api/hermes/continuity/sessions")' in backend
    assert "Pulsa «Iniciar IRIS» para habilitar esta vista" in ui
    assert "Hermes ${escapeHtml(this.snapshot.hermesVersion" not in ui
    assert "perfiles del CLI local" in ui
    assert "API keys, identidad y memoria" in ui
    assert "data-continuity-archived" not in ui
    assert '"PATCH"' in backend


def test_resumed_transcript_replaces_instead_of_appending_in_the_chat_surface() -> None:
    app = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")
    panel = (ROOT / "face" / "js" / "panel.js").read_text(encoding="utf-8")

    assert "case 'chat_history_replace':" in app
    assert "this._replaceChatHistory(payload);" in app
    assert "_continuityHistoryFingerprint" in app
    assert "replaceMessages(entries = [])" in panel
    assert "this.messages = [];" in panel


def test_continuity_styles_follow_iris_theme_tokens() -> None:
    styles = (ROOT / "face" / "css" / "styles.css").read_text(encoding="utf-8")

    marker = styles.index("/* ── Continuity Center")
    continuity_css = styles[marker:]
    assert "var(--iris-os-accent)" in continuity_css
    assert "var(--iris-os-accent-2)" in continuity_css
    assert "var(--iris-ready-color)" in continuity_css
    assert "@container (max-width: 620px)" in continuity_css
