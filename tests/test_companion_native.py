"""Security and packaging checks for the optional native companion."""

from __future__ import annotations

import json
import hashlib
import os
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import core.companion_auth as companion_auth
from core.websocket_server import _CORS_ORIGIN_REGEX, _public_avatar_url, app


ROOT = Path(__file__).resolve().parents[1]


def test_pairing_token_is_persistent_private_and_not_guessable(tmp_path: Path) -> None:
    path = tmp_path / "companion_token"
    first = companion_auth.get_or_create_companion_token(path)
    second = companion_auth.get_or_create_companion_token(path)

    assert first == second
    assert len(first) >= 32
    assert companion_auth.is_valid_companion_token(first, path) is True
    assert companion_auth.is_valid_companion_token("wrong", path) is False
    assert path.read_text(encoding="utf-8").strip() == first
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0


def test_pairing_token_waits_for_the_other_process_to_finish_writing(tmp_path: Path) -> None:
    path = tmp_path / "companion_token"
    path.write_text("", encoding="utf-8")
    expected = "a" * 43

    def finish_write() -> None:
        time.sleep(0.03)
        path.write_text(expected + "\n", encoding="utf-8")

    writer = threading.Thread(target=finish_write)
    writer.start()
    try:
        assert companion_auth.get_or_create_companion_token(path) == expected
    finally:
        writer.join(timeout=1)


def test_tauri_http_origin_requires_pairing_token(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(companion_auth, "get_iris_home", lambda: tmp_path)
    token = companion_auth.get_or_create_companion_token()
    client = TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))

    rejected = client.get(
        "/api/system-metrics",
        headers={"Origin": "http://tauri.localhost"},
    )
    accepted = client.get(
        "/api/system-metrics",
        headers={
            "Origin": "http://tauri.localhost",
            companion_auth.COMPANION_TOKEN_HEADER: token,
        },
    )
    preflight = client.options(
        "/api/system-metrics",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": companion_auth.COMPANION_TOKEN_HEADER,
        },
    )

    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://tauri.localhost"


def test_tauri_websocket_origin_requires_pairing_token(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(companion_auth, "get_iris_home", lambda: tmp_path)
    token = companion_auth.get_or_create_companion_token()
    client = TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))

    with client.websocket_connect(
        "/ws",
        headers={"Origin": "http://tauri.localhost"},
        subprotocols=["iris-companion-v1", f"iris-token.{token}"],
    ) as websocket:
        hello = websocket.receive_json()
        assert hello["type"] == "hello"
        assert websocket.accepted_subprotocol == "iris-companion-v1"


def test_tauri_http_query_does_not_authenticate_or_leak_token(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(companion_auth, "get_iris_home", lambda: tmp_path)
    token = companion_auth.get_or_create_companion_token()
    response = TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000)).get(
        f"/api/system-metrics?companion_token={token}",
        headers={"Origin": "http://tauri.localhost"},
    )

    assert response.status_code == 403


def test_tauri_origins_are_exact_not_wildcarded() -> None:
    assert re.fullmatch(_CORS_ORIGIN_REGEX, "http://tauri.localhost")
    assert re.fullmatch(_CORS_ORIGIN_REGEX, "https://tauri.localhost")
    assert not re.fullmatch(_CORS_ORIGIN_REGEX, "https://evil.tauri.localhost")
    assert "*" not in _CORS_ORIGIN_REGEX


def test_tauri_shell_is_minimal_persistent_and_single_instance() -> None:
    package = json.loads((ROOT / "companion" / "package.json").read_text(encoding="utf-8"))
    config = json.loads(
        (ROOT / "companion" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    capability = json.loads(
        (ROOT / "companion" / "src-tauri" / "capabilities" / "default.json").read_text(
            encoding="utf-8"
        )
    )
    rust = (ROOT / "companion" / "src-tauri" / "src" / "lib.rs").read_text(
        encoding="utf-8"
    )
    window = config["app"]["windows"][0]

    assert package["dependencies"]["three"] == "0.170.0"
    assert package["dependencies"]["@mediapipe/tasks-vision"] == "0.10.35"
    assert window["transparent"] is True
    assert window["decorations"] is False
    assert window["alwaysOnTop"] is True
    assert window["skipTaskbar"] is True
    assert window["visible"] is False
    assert config["app"]["withGlobalTauri"] is False
    assert config["bundle"]["targets"] == ["nsis"]
    assert set(config["bundle"]["resources"]) == {
        "../third-party/Outfit-OFL-1.1.txt",
        "../third-party/Syne-OFL-1.1.txt",
        "../third-party/MediaPipe-Apache-2.0.txt",
    }
    assert all((ROOT / "companion" / path.removeprefix("../")).is_file() for path in config["bundle"]["resources"])
    assert capability["permissions"] == ["core:window:allow-start-dragging"]
    assert "tauri_plugin_single_instance::init" in rust
    assert "tauri_plugin_window_state" in rust
    assert "TrayIconBuilder" in rust
    assert "CloseRequested" in rust and "prevent_close" in rust
    assert "ensure_window_visible" in rust
    assert "companion_scale.clamp(0.75, 1.5)" in rust
    assert "life: companion_life(&config.companion_life)" in rust
    assert "start_window_walk" in rust and "cancel_window_walk" in rust
    assert "start_window_drag" in rust and "hide_companion" in rust
    assert 'argument == "--iris-exit"' in rust
    assert "monitor.work_area()" in rust
    locomotion = rust.split("fn start_window_walk", 1)[1].split("fn open_external_url", 1)[0]
    assert ".set_focus" not in locomotion
    assert "tauri-plugin-shell" not in rust


def test_native_csp_and_bundle_do_not_bootstrap_from_the_network() -> None:
    config = json.loads(
        (ROOT / "companion" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    csp = config["app"]["security"]["csp"]
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "script-src 'self' 'wasm-unsafe-eval'" in csp
    assert "http://127.0.0.1:*" in csp
    assert "connect-src 'self' blob:" in csp
    # KTX2Loader decodes in workers it creates from blob: URLs (face/js/ktx2_support.js).
    assert "worker-src 'self' blob:" in csp
    assert "https://" not in csp

    built = ROOT / "companion" / "dist" / "face" / "companion" / "index.html"
    if not built.is_file():
        pytest.skip("requires `npm --prefix companion run build`")
    html = built.read_text(encoding="utf-8")

    assert "cdn.jsdelivr.net" not in html
    assert "fonts.googleapis.com" not in html
    assert 'type="importmap"' not in html

    mediapipe = ROOT / "companion" / "dist" / "face" / "companion" / "mediapipe"
    model = mediapipe / "face_landmarker.task"
    assert model.stat().st_size > 1_000_000
    assert hashlib.sha256(model.read_bytes()).hexdigest() == (
        "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
    )
    assert (mediapipe / "wasm" / "vision_wasm_internal.wasm").is_file()


def test_companion_preferences_persist_only_implemented_controls(monkeypatch, tmp_path: Path) -> None:
    iris_home = tmp_path / ".iris"
    iris_home.mkdir()
    monkeypatch.setattr("core.config.get_iris_home", lambda: iris_home)
    client = TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))

    initial = client.get("/api/companion/settings")
    saved = client.post("/api/companion/settings", json={
        "companion_always_on_top": False,
        "companion_scale": 1.25,
        "companion_reduced_motion": True,
        "companion_show_controls": False,
        "companion_locomotion_enabled": True,
        "companion_quiet_mode": True,
        "companion_life": "active",
    })
    rejected = client.post("/api/companion/settings", json={"companion_autostart": True})
    bad_life = client.post("/api/companion/settings", json={"companion_life": "wild"})
    persisted = json.loads((iris_home / "config.json").read_text(encoding="utf-8"))

    assert initial.status_code == 200
    assert initial.json()["companion_locomotion_enabled"] is False
    assert initial.json()["companion_quiet_mode"] is False
    assert initial.json()["companion_life"] == "calm"
    assert bad_life.status_code == 422
    assert initial.json()["privacy"] == {
        "camera_default": "off",
        "microphone_capture": "hermes_native_only",
        "video_transport": "none",
    }
    assert saved.status_code == 200
    assert saved.json()["status"] == "saved"
    assert persisted["companion_always_on_top"] is False
    assert persisted["companion_scale"] == 1.25
    assert persisted["companion_reduced_motion"] is True
    assert persisted["companion_show_controls"] is False
    assert persisted["companion_locomotion_enabled"] is True
    assert persisted["companion_quiet_mode"] is True
    assert rejected.status_code == 422
    assert persisted["companion_autostart"] is False


def test_companion_preferences_reject_invalid_scale(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("core.config.get_iris_home", lambda: tmp_path / ".iris")
    client = TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))

    assert client.post(
        "/api/companion/settings",
        json={"companion_scale": 9},
    ).status_code == 422
    assert client.post(
        "/api/companion/settings",
        json={"companion_scale": True},
    ).status_code == 422
    assert client.post(
        "/api/companion/settings",
        json={"companion_locomotion_enabled": "yes"},
    ).status_code == 422


def test_companion_preferences_reject_dns_rebinding_before_persisting(tmp_path: Path) -> None:
    attacker = TestClient(
        app,
        base_url="http://attacker.example:8765",
        client=("127.0.0.1", 51999),
    )
    response = attacker.post(
        "/api/companion/settings",
        json={"companion_quiet_mode": True},
        headers={"Origin": "http://attacker.example:8765"},
    )

    assert response.status_code == 403


def test_companion_preferences_reject_a_different_loopback_origin_port(tmp_path: Path) -> None:
    client = TestClient(
        app,
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 51999),
    )
    response = client.post(
        "/api/companion/settings",
        json={"companion_quiet_mode": True},
        headers={"Origin": "http://127.0.0.1:3000"},
    )

    assert response.status_code == 403


def test_native_animation_manifest_contains_only_files_it_can_ship() -> None:
    animation_dir = ROOT / "companion" / "dist" / "face" / "animations"
    manifest_path = animation_dir / "manifest.json"
    if not manifest_path.is_file():
        pytest.skip("requires `npm --prefix companion run build`")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    excluded = {
        "idle_smile.vrma",
        "idle_love.vrma",
        "idle_noble.vrma",
        "idle_weave.vrma",
        "think.vrma",
        "shy.vrma",
    }

    assert not excluded.intersection(path.name for path in animation_dir.iterdir())
    assert all((animation_dir / filename).is_file() for filename in manifest["clips"].values())
    # companion/scripts/animation_assets.mjs ships only approved factory clips.
    assert manifest["_packaging_notice"] == "Only explicitly approved factory animations are distributed."


def test_native_runtime_endpoint_document_is_owned_by_current_process(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(companion_auth, "get_iris_home", lambda: tmp_path)
    path = companion_auth.publish_companion_runtime(
        api_port=8766,
        iris_os_url="https://127.0.0.1:8765/face/",
    )
    document = json.loads(path.read_text(encoding="utf-8"))

    assert document["api_port"] == 8766
    assert document["iris_os_url"].startswith("https://")
    companion_auth.clear_companion_runtime()
    assert not path.exists()


def test_existing_external_avatar_uses_safe_current_route(monkeypatch, tmp_path: Path) -> None:
    iris_home = tmp_path / ".iris"
    managed = iris_home / "avatars"
    managed.mkdir(parents=True)
    external = tmp_path / "legacy" / "IRIS.vrm"
    external.parent.mkdir()
    external.write_bytes(b"glTF")
    managed_avatar = managed / "managed.vrm"
    managed_avatar.write_bytes(b"glTF")
    monkeypatch.setattr("core.config.get_iris_home", lambda: iris_home)

    assert _public_avatar_url(str(external)) == "/api/avatar/current"
    assert _public_avatar_url(str(managed_avatar)) == "/avatars/managed.vrm"
    assert str(tmp_path) not in _public_avatar_url(str(external))
