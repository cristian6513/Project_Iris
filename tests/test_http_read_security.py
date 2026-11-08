"""A01: real HTTP middleware, synthetic credentials and private static files."""

from __future__ import annotations

import hashlib
import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import core.config as config_module
from core import websocket_server as ws
from core.config import IRISConfig
from wizard import app as wizard


@pytest.fixture
def surfaces(monkeypatch, tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    avatars = home / "avatars"
    avatars.mkdir()
    (avatars / "private.vrm").write_bytes(b"PRIVATE-AVATAR")
    face = tmp_path / "face"
    (face / "js").mkdir(parents=True)
    (face / "assets").mkdir()
    (face / "animations").mkdir()
    (face / "index.html").write_text("<html>IRIS</html>", encoding="utf-8")
    (face / "js" / "bootstrap.js").write_text("/* public */", encoding="utf-8")
    for filename in ("face-local123.js", "vision-local123.mjs", "face-local123.css"):
        (face / "assets" / filename).write_text("/* compiled public bootstrap */", encoding="utf-8")
    (face / "animations" / "personal.vrma").write_bytes(b"PRIVATE-POSE")
    (face / "animations" / "private.json").write_bytes(b"PRIVATE-POSE")
    personal = "wave." + hashlib.sha256(b"PRIVATE-POSE").hexdigest() + ".vrma"
    (home / "animations").mkdir()
    (home / "animations" / personal).write_bytes(b"PRIVATE-POSE")
    (home / "animations" / "manifest.json").write_text(json.dumps({"clips": {"wave": personal}}))
    cfg = IRISConfig.model_construct(
        lan_access=True,
        websocket_host="0.0.0.0",
        lan_pairing_token="FAKE-LAN-PAIR",
        llm_provider="",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setattr(config_module, "get_iris_home", lambda: home)
    monkeypatch.setattr(wizard, "get_iris_home", lambda: home)
    monkeypatch.setattr(IRISConfig, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(
        wizard,
        "read_hermes_wake_settings",
        lambda: {
            "configured": False,
            "mode": "keep",
            "enabled": False,
            "phrase": "",
        },
    )
    monkeypatch.setattr(ws, "is_valid_companion_token", lambda value: value == "FAKE-NATIVE")
    # Mount through production code, but restore routes/state after every test.
    monkeypatch.setattr(ws.app.router, "routes", list(ws.app.router.routes))
    monkeypatch.setattr(wizard.app.router, "routes", list(wizard.app.router.routes))
    monkeypatch.setattr(ws.app.state, "face_dir", None, raising=False)
    ws.mount_static_files(face)

    async def probe():
        return {"ok": True}

    ws.app.add_api_route(
        "/api/a01-probe",
        probe,
        methods=["GET", "POST"],
        include_in_schema=False,
    )
    wizard.app.add_api_route(
        "/api/a01-probe",
        probe,
        methods=["GET", "POST"],
        include_in_schema=False,
    )
    return SimpleNamespace(
        core=ws.app,
        wizard=wizard.app,
        cfg=cfg,
        face=face,
        avatars=avatars,
        personal_url="/face/animations/" + personal,
    )


@pytest.mark.parametrize("surface", ["core", "wizard"])
@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "BREW"])
@pytest.mark.parametrize("origin", [None, "http://audit.invalid:8765"])
def test_foreign_host_is_rejected_for_all_methods(surfaces, surface, method, origin):
    headers = {"Origin": origin} if origin else {}
    client = TestClient(
        getattr(surfaces, surface),
        base_url="http://audit.invalid:8765",
        client=("127.0.0.1", 51000),
    )
    response = client.request(method, "/api/a01-probe", headers=headers)
    assert response.status_code == 403
    assert "FAKE-LAN-PAIR" not in response.text


@pytest.mark.parametrize("surface", ["core", "wizard"])
@pytest.mark.parametrize(
    "authority,peer",
    [
        ("127.0.0.1:8765", "127.0.0.1"),
        ("localhost:8765", "127.0.0.1"),
        ("[::1]:8765", "::1"),
    ],
)
def test_loopback_reads_work_without_origin(surfaces, surface, authority, peer):
    client = TestClient(
        getattr(surfaces, surface),
        base_url="http://127.0.0.1:8765",
        client=(peer, 51000),
        headers={"Host": authority},
    )
    assert client.get("/api/a01-probe").status_code == 200
    assert client.request("BREW", "/api/a01-probe").status_code == 405


@pytest.mark.parametrize(
    "host",
    [
        "audit.invalid",
        "localhost.evil",
        "127.0.0.1@audit.invalid",
        "localhost:bad",
        "localhost:65536",
        "localhost:0",
        "localhost/path",
        "localhost\\evil",
        "localhost?x",
        "localhost#x",
        "localhost, audit.invalid",
        " localhost",
        "localhost ",
    ],
)
@pytest.mark.parametrize("surface", ["core", "wizard"])
def test_malformed_or_foreign_host_is_not_trusted(surfaces, surface, host):
    client = TestClient(
        getattr(surfaces, surface),
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 51000),
    )
    assert client.get("/api/a01-probe", headers={"Host": host}).status_code == 403


@pytest.mark.parametrize("surface", ["core", "wizard"])
def test_duplicate_host_is_rejected(surfaces, surface):
    client = TestClient(
        getattr(surfaces, surface),
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 51000),
    )
    response = client.get(
        "/api/a01-probe",
        headers=[
            ("Host", "127.0.0.1:8765"),
            ("Host", "audit.invalid"),
        ],
    )
    assert response.status_code == 403


def test_forwarded_headers_cannot_authorize_a_foreign_host(surfaces):
    client = TestClient(
        surfaces.core,
        base_url="http://audit.invalid:8765",
        client=("127.0.0.1", 51000),
    )
    response = client.get(
        "/api/a01-probe",
        headers={
            "X-Forwarded-Host": "localhost",
            "X-Forwarded-For": "127.0.0.1",
            "Forwarded": "for=127.0.0.1;host=localhost",
        },
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/avatars/private.vrm",
        "/face/animations/personal.vrma",
        "/api/config",
        "/openapi.json",
    ],
)
def test_lan_private_reads_require_pairing(surfaces, path):
    client = TestClient(
        surfaces.core,
        base_url="https://192.168.1.10:8765",
        client=("192.168.1.25", 51000),
    )
    assert client.get(path).status_code == 403
    assert client.head(path).status_code == 403


@pytest.mark.parametrize("path", ["/", "/face/", "/face/index.html", "/face/js/bootstrap.js"])
def test_lan_bootstrap_remains_public(surfaces, path):
    client = TestClient(
        surfaces.core,
        base_url="http://192.168.1.10:8765",
        client=("192.168.1.25", 51000),
    )
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("filename", ["face-local123.js", "vision-local123.mjs", "face-local123.css"])
def test_lan_compiled_bootstrap_is_public_without_pairing(surfaces, filename):
    client = TestClient(
        surfaces.core,
        base_url="http://192.168.1.10:8765",
        client=("192.168.1.25", 51000),
    )
    response = client.get("/face/assets/" + filename)
    assert response.status_code == 200
    assert response.content == b"/* compiled public bootstrap */"
    assert "set-cookie" not in response.headers
    assert client.head("/face/assets/" + filename).status_code == 200


@pytest.mark.parametrize("filename", [
    "private.vrma", "private.vrm", "private.glb", "private.bvh",
    "private.fbx", "private.key", "private.env", "private.exe",
])
def test_lan_compiled_assets_do_not_make_private_extensions_public(surfaces, filename):
    (surfaces.face / "assets" / filename).write_bytes(b"PRIVATE-ASSET")
    client = TestClient(
        surfaces.core,
        base_url="http://192.168.1.10:8765",
        client=("192.168.1.25", 51000),
    )
    response = client.get("/face/assets/" + filename)
    assert response.status_code == 403
    assert b"PRIVATE-" not in response.content
    assert client.head("/face/assets/" + filename).status_code == 403


@pytest.mark.parametrize("path", [
    "/face/assets/%2e%2e/animations/personal.vrma",
    "/face/assets/%2e%2e/animations/private.json",
    "/face/assets/%2e%2e%5canimations%5cprivate.json",
    "/face/assets/..%2fanimations/private.json",
    "/face/assets/%2e%2e/%2e%2e/avatars/private.vrm",
    "/face/assets/%252e%252e/animations/private.json",
    "/face/assets/C:%5cprivate.vrm",
    "/face/assets/vision-local123.mjs:private",
])
def test_compiled_asset_paths_cannot_bypass_lan_pairing(surfaces, path):
    client = TestClient(
        surfaces.core,
        base_url="http://192.168.1.10:8765",
        client=("192.168.1.25", 51000),
    )
    response = client.get(path)
    assert response.status_code == 403
    assert b"PRIVATE-" not in response.content


def test_pair_exchange_cookie_revocation_and_no_secret_redirect(surfaces):
    client = TestClient(
        surfaces.core,
        base_url="https://192.168.1.10:8765",
        client=("192.168.1.25", 51000),
    )
    paired = client.get("/face/?pair=FAKE-LAN-PAIR&view=home", follow_redirects=False)
    assert paired.status_code == 303
    assert "pair=" not in paired.headers["location"]
    assert "view=home" in paired.headers["location"]
    assert "FAKE-LAN-PAIR" not in paired.headers["location"]
    assert paired.headers["cache-control"] == "no-store"
    assert paired.headers["referrer-policy"] == "no-referrer"
    cookie = paired.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    response = client.get("/avatars/private.vrm")
    assert response.content == b"PRIVATE-AVATAR"
    assert response.headers["cache-control"] == "no-store"
    surfaces.cfg.lan_pairing_token = "ROTATED"
    assert client.get("/avatars/private.vrm").status_code == 403


def test_pair_header_allows_read_but_not_administration(surfaces):
    client = TestClient(
        surfaces.core,
        base_url="http://192.168.1.10:8765",
        client=("192.168.1.25", 51000),
        headers={"X-IRIS-LAN-Token": "FAKE-LAN-PAIR"},
    )
    assert client.get("/avatars/private.vrm").status_code == 200
    assert client.get("/api/a01-probe").status_code == 200
    assert client.get("/api/gateway-logs").status_code == 403
    assert (
        client.post(
            "/api/a01-probe",
            headers={
                "Origin": "http://192.168.1.10:8765",
            },
        ).status_code
        == 403
    )
    assert client.get("/api/a01-probe", headers={"Host": "192.168.1.99:8765"}).status_code == 403


def test_container_proxy_requires_explicit_mode_and_loopback_authority(surfaces):
    surfaces.cfg.lan_access = False
    client = TestClient(
        surfaces.core,
        base_url="http://localhost:3000",
        client=("172.20.0.3", 51000),
    )
    assert client.get("/api/a01-probe").status_code == 403
    surfaces.cfg.container_proxy_mode = True
    assert client.get("/api/a01-probe").status_code == 200
    response = client.post("/api/a01-probe", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 200
    assert client.get("/api/a01-probe", headers={"Host": "audit.invalid:3000"}).status_code == 403


def test_wizard_rejects_lan_authority_even_when_core_lan_is_enabled(surfaces):
    client = TestClient(
        surfaces.wizard,
        base_url="http://192.168.1.10:8000",
        client=("192.168.1.25", 51000),
    )
    assert client.get("/api/current-config").status_code == 403


def test_native_pairing_and_preflight_survive_host_protection(surfaces):
    client = TestClient(
        surfaces.core,
        base_url="http://127.0.0.1:8766",
        client=("127.0.0.1", 51000),
    )
    headers = {"Origin": "http://tauri.localhost"}
    assert client.get("/avatars/private.vrm", headers=headers).status_code == 403
    headers["X-IRIS-Companion-Token"] = "FAKE-NATIVE"
    assert client.get("/avatars/private.vrm", headers=headers).content == b"PRIVATE-AVATAR"
    preflight = client.options(
        "/avatars/private.vrm",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-IRIS-Companion-Token",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://tauri.localhost"


def test_wizard_config_uses_allowlist_preserves_ui_and_hides_future_secrets(surfaces, monkeypatch):
    class FutureConfig(IRISConfig):
        future_password: str = "FAKE-FUTURE"

    cfg = FutureConfig.model_construct(
        assistant_name="Prueba",
        avatar_type="vrm",
        avatar_vrm_path="avatar.vrm",
        llm_provider="",
        llm_api_key="FAKE-LLM",
        hermes_api_key="FAKE-HERMES",
        whisper_api_key="FAKE-WHISPER",
        elevenlabs_api_key="FAKE-ELEVEN",
        minimax_api_key="FAKE-MINIMAX",
        telegram_bot_token="FAKE-TELEGRAM",
        discord_bot_token="FAKE-DISCORD",
        slack_bot_token="FAKE-SLACK",
        lan_pairing_token="FAKE-LAN-PAIR",
        future_password="FAKE-FUTURE",
        fish_speech_ref_text="texto local",
        tts_allow_remote_fallback=True,
    )
    monkeypatch.setattr(IRISConfig, "load", classmethod(lambda cls: cfg))
    client = TestClient(
        surfaces.wizard,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 51000),
    )
    response = client.get("/api/current-config")
    assert response.status_code == 200
    assert "FAKE-" not in response.text
    body = response.json()
    assert body["assistant_name"] == "Prueba" and body["avatar_vrm_path"] == "avatar.vrm"
    assert body["fish_speech_ref_text"] == "texto local"
    assert body["tts_allow_remote_fallback"] is True
    assert body["has_llm_api_key"] and body["has_whisper_api_key"]
    assert body["has_elevenlabs_api_key"] and body["has_minimax_api_key"]
    assert "future_password" not in body and "lan_pairing_token" not in body
    assert response.headers["cache-control"] == "no-store"


def test_pairing_url_is_local_only_and_not_cacheable(surfaces):
    local = TestClient(
        surfaces.core,
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 51000),
    )
    response = local.get("/api/network-info")
    assert response.status_code == 200 and "FAKE-LAN-PAIR" in response.text
    assert response.headers["cache-control"] == "no-store"
    remote = TestClient(
        surfaces.core,
        base_url="http://192.168.1.10:8765",
        client=("192.168.1.25", 51000),
        headers={"X-IRIS-LAN-Token": "FAKE-LAN-PAIR"},
    )
    response = remote.get("/api/network-info")
    assert response.status_code == 200 and "FAKE-LAN-PAIR" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/face/js/%2e%2e/animations/personal.vrma",
        "/face/js/%2e%2e%5canimations%5cpersonal.vrma",
        "/face/js/..%2fanimations/personal.vrma",
        "/face/js/C:%5cprivate.vrm",
    ],
)
def test_public_prefix_cannot_bypass_pairing_with_path_variants(surfaces, path):
    client = TestClient(
        surfaces.core,
        base_url="http://192.168.1.10:8765",
        client=("192.168.1.25", 51000),
    )
    response = client.get(path)
    assert response.status_code in {400, 403, 404}
    assert b"PRIVATE-" not in response.content


@pytest.mark.parametrize("prefix", ["js", "assets"])
def test_public_prefix_symlink_or_junction_cannot_serve_private_pose(surfaces, prefix):
    target = surfaces.face / "animations"
    link = surfaces.face / prefix / "linked"
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        client = TestClient(
            surfaces.core,
            base_url="http://192.168.1.10:8765",
            client=("192.168.1.25", 51000),
        )
        response = client.get(f"/face/{prefix}/linked/private.json")
        assert response.status_code in {403, 404}
        assert b"PRIVATE-POSE" not in response.content
    finally:
        # Remove only the link, never its target directory.
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


@pytest.mark.parametrize("surface", ["core", "wizard"])
@pytest.mark.parametrize(
    "origin,expected",
    [
        ("http://127.0.0.1:8765", 200),
        ("https://127.0.0.1:8765", 403),
        ("http://127.0.0.1:3000", 403),
        ("http://localhost:8765", 403),
    ],
)
def test_host_check_preserves_exact_mutation_origin(surfaces, surface, origin, expected):
    client = TestClient(
        getattr(surfaces, surface),
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 51000),
    )
    response = client.post("/api/a01-probe", headers={"Origin": origin})
    assert response.status_code == expected


@pytest.mark.parametrize(
    "authority,server,bind,lan,expected",
    [
        ("localhost", None, None, False, True),
        ("LOCALHOST:3000", None, None, False, True),
        ("127.0.0.1:65535", None, None, False, True),
        ("[::1]:8765", None, None, False, True),
        ("192.168.1.10:8765", "192.168.1.10", "0.0.0.0", True, True),
        ("192.168.1.10", None, "192.168.1.10", True, True),
        ("[fd00::10]:8765", "fd00::10", "::", True, True),
        ("192.168.1.10", "192.168.1.10", "0.0.0.0", False, False),
        ("192.168.1.99", "192.168.1.10", "0.0.0.0", True, False),
        ("0.0.0.0", "0.0.0.0", "0.0.0.0", True, False),
        ("[::]", "::", "::", True, False),
        ("224.0.0.1", "224.0.0.1", None, True, False),
        ("audit.invalid", "audit.invalid", "audit.invalid", True, False),
        ("", None, None, False, False),
        ("localhost:", None, None, False, False),
        ("[::1]extra", None, None, False, False),
        ("[localhost]", None, None, False, False),
        ("::1", None, None, False, False),
        ("http://localhost", None, None, False, False),
        ("localhost:65536", None, None, False, False),
        ("localhost:-1", None, None, False, False),
        ("localhost:0", None, None, False, False),
        ("localhost\n", None, None, False, False),
        ("localhost\t", None, None, False, False),
        ("localhost\x00", None, None, False, False),
        ("[::1%25lo]:8765", None, None, False, False),
    ],
)
def test_http_host_policy_has_no_dns_or_wildcard_trust(
    monkeypatch,
    authority,
    server,
    bind,
    lan,
    expected,
):
    import socket

    from core.request_security import is_trusted_http_host

    def no_dns(*_args, **_kwargs):
        pytest.fail("Host authorization must not resolve DNS")

    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    assert (
        is_trusted_http_host(
            authority,
            server_host=server,
            bind_host=bind,
            allow_lan=lan,
        )
        is expected
    )


@pytest.mark.parametrize("surface", ["core", "wizard"])
def test_missing_host_fails_closed(surfaces, surface):
    client = TestClient(
        getattr(surfaces, surface),
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 51000),
    )
    assert client.get("/api/a01-probe", headers={"Host": ""}).status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/face/js/%2Fprivate.json",
        "/face/js/%5C%5Cserver%5Cprivate.json",
        "/face/js/C:%5Cprivate.json",
        "/face/js/%252e%252e/animations/private.json",
        "/face/js/%2e%2e/animations/private.json",
        "/face/js/..%5Canimations%5Cprivate.json",
    ],
)
def test_absolute_and_encoded_paths_are_not_public(surfaces, path):
    client = TestClient(
        surfaces.core,
        base_url="http://192.168.1.10:8765",
        client=("192.168.1.25", 51000),
    )
    response = client.get(path)
    assert response.status_code == 403
    assert b"PRIVATE-" not in response.content


def test_wrong_pairing_token_does_not_reach_files_or_leak_in_error(surfaces):
    client = TestClient(
        surfaces.core,
        base_url="http://192.168.1.10:8765",
        client=("192.168.1.25", 51000),
    )
    response = client.get("/avatars/private.vrm?pair=FAKE-WRONG", follow_redirects=False)
    assert response.status_code == 403
    assert "FAKE-" not in response.text
    assert "location" not in response.headers
    assert "set-cookie" not in response.headers


def test_desktop_wizard_iframe_assets_and_configuration_remain_available(surfaces):
    from core.desktop import create_desktop_app

    client = TestClient(
        create_desktop_app(),
        base_url="http://127.0.0.1:9123",
        client=("127.0.0.1", 51000),
    )
    assert client.get("/ui/").status_code == 200
    assert client.get("/ui/wizard.js").status_code == 200
    response = client.get("/api/current-config")
    assert response.status_code == 200
    assert "FAKE-LAN-PAIR" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/current-config", headers={"Host": "audit.invalid"}).status_code == 403


def test_paired_static_head_range_and_revoked_conditional_read(surfaces):
    client = TestClient(
        surfaces.core,
        base_url="http://192.168.1.10:8765",
        client=("192.168.1.25", 51000),
        headers={"X-IRIS-LAN-Token": "FAKE-LAN-PAIR"},
    )
    response = client.get(surfaces.personal_url)
    assert response.content == b"PRIVATE-POSE"
    assert response.headers["cache-control"] == "no-store"
    head = client.head("/avatars/private.vrm")
    assert head.status_code == 200 and head.content == b""
    assert head.headers["cache-control"] == "no-store"
    partial = client.get("/avatars/private.vrm", headers={"Range": "bytes=0-6"})
    assert partial.status_code == 206 and partial.content == b"PRIVATE"
    assert partial.headers["cache-control"] == "no-store"
    surfaces.cfg.lan_pairing_token = ""
    denied = client.get(
        surfaces.personal_url,
        headers={"If-None-Match": response.headers["etag"]},
    )
    assert denied.status_code == 403
    assert denied.headers["cache-control"] == "no-store"
