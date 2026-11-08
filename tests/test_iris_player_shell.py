"""Static integration checks for the IRIS Player application surface."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_player_is_registered_as_a_real_os_window() -> None:
    html = (ROOT / "face" / "index.html").read_text(encoding="utf-8")
    shell = (ROOT / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")

    assert 'css/iris_player.css?v=3' in html
    # F09.1: registered by its manifest; the shell keeps the view.
    manifest = json.loads((ROOT / "apps" / "player" / "app.json").read_text(encoding="utf-8"))
    assert (manifest["name"], manifest["presentation"]) == ("IRIS Player", "window")
    assert 'data-workbench-view="player"' in shell
    assert 'data-os-open="player"' in shell
    assert "new IrisPlayerApp" in shell


def test_player_structured_control_requires_capability_and_acknowledgement() -> None:
    app = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")
    protocol = (ROOT / "face" / "js" / "surface_protocol.js").read_text(encoding="utf-8")
    player = (ROOT / "face" / "js" / "iris_player.js").read_text(encoding="utf-8")
    server = (ROOT / "core" / "websocket_server.py").read_text(encoding="utf-8")

    assert "controls_local_player: true" in app
    assert "'controls_local_player'" in protocol
    assert "case 'iris_player_intent'" in app
    assert "handleIntent(intent = {})" in player
    activity = (ROOT / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    assert "permission_confirmed: false" in activity
    assert "permission_confirmed: true" in activity
    assert "_confirmPlaybackStart" in player
    assert "addEventListener?.('playing'" in player
    assert '@app.post("/api/player/actions")' in server
    # F09.4: Player is an operation of the app action bridge; its wait stays at 20 s.
    from core.player_actions import PLAYER_CONFIRMATION_TIMEOUT_S

    assert PLAYER_CONFIRMATION_TIMEOUT_S == 20.0
    assert 'playback_state' in player


def test_project_skill_uses_bounded_adapter_instead_of_raw_audio_access() -> None:
    skill_root = ROOT / ".hermes" / "skills" / "iris-os"
    skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    adapter = (skill_root / "scripts" / "control_player.py").read_text(encoding="utf-8")

    assert "requires_toolsets: [terminal, file]" in skill
    assert "--payload-file" in skill
    assert "127.0.0.1" in adapter
    assert "/api/player/actions" in adapter
    assert "timeout=24" in adapter
    assert "timeout=30" in skill
    assert "subprocess" not in adapter


def test_player_controller_preserves_search_focus_and_reconciles_audio() -> None:
    player = (ROOT / "face" / "js" / "iris_player.js").read_text(encoding="utf-8")

    assert "this._loadPromise" in player
    assert "restoreSearchFocus" in player
    assert "_stopAudioForLibraryChange" in player
    assert "_reconcileAudioAfterRefresh" in player
