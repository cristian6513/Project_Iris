"""A08.6: the packaged Wizard leads to the bundled Hermes, never to an installer.

Fake runtimes and profiles only: no Hermes process, account or network is used.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from core import hermes_wake
from core.runtime_descriptor import DESCRIPTOR_ENV, get_runtime
from tests.runtime_descriptor_fixtures import write_runtime_fixture
from wizard import app as wizard
from wizard import hermes_messaging_worker as worker
from wizard import provider_auth

ROOT = Path(__file__).resolve().parents[1]
LOCAL = {"base_url": "http://127.0.0.1:8765", "client": ("127.0.0.1", 50123)}

# Operations that install, update or register an external Hermes. They must
# refuse in packaged; everything else edits the profile, reads state or drives
# the bundled services and Iris' own optional components.
EXTERNAL_HERMES = {
    ("POST", "/api/install-hermes-stream"),
    ("POST", "/api/autostart"),
}
REVIEWED = {
    ("GET", "/"), ("GET", "/api/runtime-info"), ("GET", "/api/licenses"), ("GET", "/api/licenses/text"),
    ("POST", "/api/update-identity"), ("GET", "/api/current-identity"), ("GET", "/api/identity-options"),
    ("POST", "/api/identity-preview"), ("POST", "/api/identity-preview/sample"),
    ("GET", "/api/identity-profile/export"), ("POST", "/api/identity-profile/export"),
    ("POST", "/api/identity-profile/import"), ("GET", "/api/identity-history"),
    ("POST", "/api/identity-history/{entry_id}/restore"), ("POST", "/api/identity-legacy/import"),
    ("GET", "/api/hermes-wake/settings"), ("POST", "/api/hermes-wake/settings"),
    ("POST", "/api/hermes-wake/runtime"), ("GET", "/api/hermes-voice/status"),
    ("POST", "/api/hermes-voice/runtime"),
    # F16: edits the profile's stt.streaming only; no install/update or capture.
    ("POST", "/api/hermes-voice/settings"),
    ("GET", "/api/companion/lifecycle"), ("POST", "/api/companion/install"),
    ("POST", "/api/companion/autostart"), ("POST", "/api/companion/launch"), ("POST", "/api/companion/stop"),
    ("POST", "/api/provider-auth/sessions"), ("GET", "/api/provider-auth/sessions/{session_id}"),
    ("DELETE", "/api/provider-auth/sessions/{session_id}"),
    ("GET", "/api/messaging/platforms"), ("PUT", "/api/messaging/platforms/{platform}"),
    ("POST", "/api/messaging/prepare"), ("POST", "/api/messaging/platforms/{platform}/test"),
    ("POST", "/api/messaging/restart"), ("POST", "/api/messaging/whatsapp/pairing"),
    ("GET", "/api/messaging/whatsapp/pairing/{pairing_id}"),
    ("DELETE", "/api/messaging/whatsapp/pairing/{pairing_id}"),
    ("POST", "/api/messaging/whatsapp/pairing/{pairing_id}/apply"),
    ("POST", "/api/save-visual-theme"), ("POST", "/api/hermes-security-ack"),
    ("GET", "/api/detect-hardware"), ("GET", "/api/detect-dependencies"),
    ("POST", "/api/start-gateway"), ("POST", "/api/restart-gateway"), ("GET", "/api/autostart-status"),
    ("GET", "/api/providers"), ("POST", "/api/providers/refresh"), ("GET", "/api/auth-provider-stream"),
    ("POST", "/api/provider-models"),  # Metadata only; private engine probe, no installer or draft credential write.
    ("GET", "/api/hermes-status"), ("GET", "/api/test-hermes"), ("GET", "/api/voices"),
    ("POST", "/api/test-audio"), ("POST", "/api/test-tts-audio"), ("POST", "/api/test-tts"),
    ("GET", "/api/tts-voices"), ("POST", "/api/clone-voice"), ("POST", "/api/upload-model"),
    ("POST", "/api/fish-audio/voices"),  # Reads the Fish catalog; no Hermes install/update or write.
    ("POST", "/api/fish-audio/sample"),  # Relays a voice's published sample; no install, update or write.
    ("POST", "/api/save-voice-selection"),  # Only the active voice card in Iris' profile; no Hermes or provider calls.
    ("GET", "/api/animation-slots"), ("POST", "/api/upload-animation"), ("POST", "/api/rig-model"),
    ("POST", "/api/save-config"), ("POST", "/api/save-llm-selection"), ("POST", "/api/import-hermes-config"),
    ("GET", "/api/llm-setup"), ("POST", "/api/add-llm-provider"),
    ("GET", "/shared/{name}"),
    ("GET", "/api/check-extras"), ("POST", "/api/install-extra-stream"), ("POST", "/api/extras/cancel"),
    ("POST", "/api/extras/remove"), ("GET", "/api/health-check-stream"), ("GET", "/api/current-config"),
    ("POST", "/api/iris/launch"), ("POST", "/api/launch-IRIS"), ("POST", "/api/iris/stop"),
    ("POST", "/api/stop-IRIS"), ("GET", "/api/iris/status"), ("GET", "/api/IRIS-status"),
}


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    descriptor, spec = write_runtime_fixture(tmp_path / "Iris instalada á")
    monkeypatch.setenv(DESCRIPTOR_ENV, str(descriptor))
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "perfil"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "perfil/hermes"))
    return SimpleNamespace(context=get_runtime(), descriptor=descriptor, spec=spec, root=tmp_path)


@pytest.fixture
def development(tmp_path, monkeypatch):
    monkeypatch.delenv(DESCRIPTOR_ENV, raising=False)
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "perfil"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "perfil/hermes"))
    return get_runtime()


def _routes():
    return {
        (method, route.path)
        for route in wizard.app.routes if isinstance(route, APIRoute)
        for method in route.methods
    }


# ── Routes: nothing in packaged installs or updates an external Hermes ──────

def test_every_wizard_route_is_reviewed_for_external_hermes_effects():
    # A new route fails here until someone decides whether it may touch Hermes.
    assert _routes() == EXTERNAL_HERMES | REVIEWED


@pytest.mark.parametrize("method,path", sorted(EXTERNAL_HERMES))
def test_packaged_refuses_external_hermes_routes_without_effects(packaged, monkeypatch, method, path):
    effect = Mock(side_effect=AssertionError("external Hermes operation must not run"))
    monkeypatch.setattr(wizard, "_download_official_installer", effect)
    monkeypatch.setattr("wizard.autostart.enable_autostart", effect)
    monkeypatch.setattr("wizard.autostart.disable_autostart", effect)
    with TestClient(wizard.app, **LOCAL) as client:
        response = client.request(method, path, json={"action": "enable"},
                                  headers={"X-IRIS-Intent": "install-hermes", "Origin": LOCAL["base_url"]})
    assert response.status_code == 403
    assert response.json()["code"] == "runtime_operation_unavailable"
    effect.assert_not_called()


def test_runtime_info_lets_the_ui_derive_every_control(packaged):
    with TestClient(wizard.app, **LOCAL) as client:
        info = client.get("/api/runtime-info").json()
    assert info["mode"] == "packaged" and info["iris_version"]
    assert info["capabilities"]["external_hermes_install"] is False
    assert info["capabilities"]["external_autostart"] is False
    assert info["capabilities"]["profile_configuration"] is True
    assert str(packaged.root) not in json.dumps(info)


def test_packaged_dependency_precheck_never_asks_for_python_git_or_uv(packaged, monkeypatch):
    effect = Mock(side_effect=AssertionError("no external tool probe"))
    monkeypatch.setattr(wizard, "_hermes_install_readiness_legacy", effect)
    with TestClient(wizard.app, **LOCAL) as client:
        data = client.get("/api/detect-dependencies").json()
    assert data["install_action"] == "unavailable" and data["deps"] == {}
    assert data["all_required_ok"] is True
    effect.assert_not_called()


# ── Install states become package states ─────────────────────────────────

def _legacy(status, **extra):
    return {"status": status, "mode": {"installed": "quick", "damaged": "repair"}.get(status, "full"),
            "message": extra.pop("message", "Hermes funciona, pero todavía no tiene proveedor y modelo explícitos."),
            "cli_path": "", "version": "0.21.4", **extra}


@pytest.mark.parametrize("status", ["missing", "damaged"])
def test_packaged_missing_or_damaged_engine_points_to_repairing_iris(packaged, monkeypatch, status):
    monkeypatch.setattr(wizard, "_hermes_installation_state_legacy",
                        Mock(return_value=_legacy(status, residual_launcher=True)))
    with TestClient(wizard.app, **LOCAL) as client:
        data = client.get("/api/hermes-status").json()
    assert data["mode"] == "package_repair"
    assert data["installation_kind"] == "bundled" and data["beta_warning"] is False
    assert data["residual_launcher"] is False
    assert "Repara Iris" in data["message"] and "no se instalará otro Hermes" in data["message"]


def test_development_keeps_the_external_install_path(development, monkeypatch):
    monkeypatch.setattr(wizard, "_hermes_installation_state_legacy", Mock(return_value=_legacy("missing")))
    with TestClient(wizard.app, **LOCAL) as client:
        data = client.get("/api/hermes-status").json()
    assert data["mode"] == "full" and data["beta_warning"] is True
    assert data["runtime"]["capabilities"]["external_hermes_install"] is True


def test_packaged_needs_setup_and_installed_messages_describe_the_bundled_engine(packaged, monkeypatch):
    monkeypatch.setattr(wizard, "_hermes_installation_state_legacy", Mock(return_value=_legacy("needs_setup")))
    assert wizard._hermes_installation_state()["message"].startswith("El motor incluido funciona")
    monkeypatch.setattr(wizard, "_hermes_installation_state_legacy", Mock(return_value=_legacy("installed")))
    state = wizard._hermes_installation_state()
    assert state["mode"] == "quick" and "motor incluido" in state["message"]


@pytest.mark.parametrize("status", ["installed", "needs_setup", "damaged"])
def test_packaged_stopped_gateway_reports_the_engine_state_not_an_install(packaged, monkeypatch, status):
    monkeypatch.setattr(wizard, "_hermes_installation_state_legacy",
                        Mock(return_value=_legacy(status, diagnostic="detalle")))
    monkeypatch.setattr("wizard.health_check.check_gateway_health",
                        AsyncMock(return_value={"ok": False, "detail": "Sin Gateway"}))
    with TestClient(wizard.app, **LOCAL) as client:
        data = client.get("/api/test-hermes").json()
    assert data["status"] == status and data["installation_kind"] == "bundled"
    assert data["code"] == "gateway_runtime_unverified"


def test_packaged_hardware_view_shows_no_launcher_or_checkout(packaged, monkeypatch):
    from wizard import system_profile

    monkeypatch.setattr(wizard, "_hermes_installation_state_legacy", Mock(return_value=_legacy("installed")))
    for name, value in {"cpu_profile": {"logical_cores": 4}, "gpu_profile": {}, "total_ram_gb": 16}.items():
        monkeypatch.setattr(system_profile, name, Mock(return_value=value))
    monkeypatch.setattr(system_profile, "probe_local_model_servers", AsyncMock(return_value={}))
    monkeypatch.setattr(wizard, "_probe_command", Mock(return_value=(False, "")))
    with TestClient(wizard.app, **LOCAL) as client:
        data = client.get("/api/detect-hardware").json()
    assert data["hermes_bundled"] is True
    assert data["hermes_path"] == "" and data["hermes_repo"] == ""


def test_packaged_health_check_names_the_included_engine(packaged, monkeypatch):
    from wizard import health_check

    monkeypatch.setattr("core.hermes_runtime.resolve_hermes_cli", Mock(return_value=""))
    result = asyncio.run(health_check.check_cli_in_path())
    assert result["label"] == "Motor Hermes Agent incluido"
    assert result["hint"] == "Repara la instalación de Iris."
    assert "irm " not in json.dumps(result)


# ── Provider sign-in copy matches what the package can do ───────────────

@pytest.mark.parametrize("code", ["not_installed", "unsupported_runtime"])
def test_provider_login_errors_never_ask_a_packaged_user_to_install_hermes(packaged, code):
    login = provider_auth.Login("x" * 24, "openai", "openai-codex", status="error", code=code)
    message = login.public()["message"]
    assert "Instala Hermes" not in message and "Revisa su instalación" not in message
    assert "Iris" in message


def test_provider_login_errors_keep_development_guidance(development):
    login = provider_auth.Login("x" * 24, "openai", "openai-codex", status="error", code="not_installed")
    assert login.public()["message"] == provider_auth.MESSAGES["not_installed"]


def test_packaged_messaging_without_engine_points_to_repairing_iris(packaged, monkeypatch):
    from wizard import messaging as bridge

    monkeypatch.setattr(bridge, "resolve_hermes_python", Mock(return_value=None))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(bridge.HermesMessaging().call("catalog"))
    assert caught.value.status_code == 503 and "Repara Iris" in caught.value.detail


def test_packaged_wake_hint_does_not_promise_a_download(packaged, monkeypatch):
    payload = {"available": True, "deps_available": False, "audio_available": True, "capture": "local"}
    result = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
    monkeypatch.setattr("core.hermes_runtime.subprocess.run", Mock(return_value=result))
    probe = hermes_wake.probe_hermes_wake_requirements()
    assert probe["ready"] is False
    assert "no descarga componentes" in probe["hint"] and "instalar" not in probe["hint"]


# ── Messaging availability is the runtime's real capability ─────────────

@pytest.fixture
def fake_hermes(tmp_path, monkeypatch):
    """Minimal stand-ins for the pinned Hermes helpers the worker consults."""
    state = SimpleNamespace(unsupported={}, missing={}, lazy_allowed=False, which={},
                            home=tmp_path / "hermes-home", repo=tmp_path / "hermes-repo")
    state.home.mkdir()
    state.repo.mkdir()
    lazy = types.ModuleType("tools.lazy_deps")
    lazy.LAZY_DEPS = {"platform.telegram": ("python-telegram-bot==22.8",),
                      "platform.teams": ("microsoft-teams-apps==2.0.13.4",),
                      "platform.matrix": ("mautrix[encryption]==0.21.1",)}
    lazy._unsupported_feature_reason = lambda feature: state.unsupported.get(feature)
    lazy.feature_missing = lambda feature: tuple(state.missing.get(feature, ()))
    lazy._allow_lazy_installs = lambda: state.lazy_allowed
    lazy._pkg_name_from_spec = lambda spec: spec.split("==")[0].split("[")[0]
    tools = types.ModuleType("tools")
    tools.lazy_deps = lazy
    constants = types.ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: state.home
    constants.iter_hermes_node_dirs = lambda: [state.home / "node"]
    constants.find_node_executable_on_path = lambda name: state.which.get(name)
    monkeypatch.setitem(sys.modules, "tools", tools)
    monkeypatch.setitem(sys.modules, "tools.lazy_deps", lazy)
    monkeypatch.setitem(sys.modules, "hermes_constants", constants)
    monkeypatch.setitem(sys.modules, "pm", None)  # Hermes 0.21.4 has no package manager
    monkeypatch.setattr("shutil.which", lambda name: state.which.get(name))
    return state


def test_channel_with_bundled_dependencies_is_available(fake_hermes):
    status = worker.requirements("telegram", fake_hermes.repo)
    assert status == {"available": True, "code": "", "detail": "", "packages": []}


def test_sealed_runtime_reports_missing_channel_packages_as_unavailable(fake_hermes):
    fake_hermes.missing["platform.teams"] = ["microsoft-teams-apps==2.0.13.4"]
    status = worker.requirements("teams", fake_hermes.repo)
    assert status["available"] is False and status["code"] == "missing_packages"
    assert status["packages"] == ["microsoft-teams-apps"]
    with pytest.raises(HTTPException) as caught:
        worker.require_available("teams", fake_hermes.repo)
    assert caught.value.status_code == 409 and "no está disponible" in caught.value.detail


def test_development_may_let_hermes_install_channel_packages(fake_hermes):
    fake_hermes.missing["platform.teams"] = ["microsoft-teams-apps==2.0.13.4"]
    fake_hermes.lazy_allowed = True
    status = worker.requirements("teams", fake_hermes.repo)
    assert status["available"] is True and status["code"] == "installs_on_start"


def test_platform_unsupported_on_this_os_is_unavailable_everywhere(fake_hermes):
    fake_hermes.unsupported["platform.matrix"] = "unsupported on Windows"
    fake_hermes.lazy_allowed = True
    assert worker.requirements("matrix", fake_hermes.repo)["code"] == "unsupported_platform"


def test_whatsapp_qr_needs_the_bridge_and_node(fake_hermes):
    assert worker.requirements("whatsapp", fake_hermes.repo)["code"] == "missing_bridge"
    bridge = fake_hermes.repo / "scripts/whatsapp-bridge/bridge.js"
    bridge.parent.mkdir(parents=True)
    bridge.write_text("// bridge", encoding="utf-8")
    assert worker.requirements("whatsapp", fake_hermes.repo)["code"] == "missing_program"
    node = fake_hermes.home / "node" / "node.exe"
    node.parent.mkdir()
    node.write_bytes(b"MZ")
    assert worker.requirements("whatsapp", fake_hermes.repo)["available"] is True


def test_cli_based_channel_needs_its_program(fake_hermes):
    assert worker.requirements("raft", fake_hermes.repo)["code"] == "missing_program"
    fake_hermes.which["raft"] = "C:/tools/raft.exe"
    assert worker.requirements("raft", fake_hermes.repo)["available"] is True


# Hermes 0.21.6: channel dependencies are ``pm`` extras and Node is PM-managed (A08.4b).

@pytest.fixture
def fake_hermes_pm(tmp_path, monkeypatch):
    """Minimal stand-ins for Hermes 0.21.6's ``pm`` and Node lookup."""
    state = SimpleNamespace(unsupported=set(), missing={}, lazy_allowed=False, which={}, pm_node=None,
                            asked=[], home=tmp_path / "hermes-home", repo=tmp_path / "hermes-repo")
    state.home.mkdir()
    state.repo.mkdir()
    pm = types.ModuleType("pm")
    extras = types.ModuleType("pm.extras")

    def extra_supported(name):
        state.asked.append(name)
        return name not in state.unsupported

    extras.extra_supported = extra_supported
    extras.missing = lambda name: tuple(state.missing.get(name, ()))
    install = types.ModuleType("pm.install")
    install.lazy_installs_allowed = lambda: state.lazy_allowed
    pm.extras, pm.install = extras, install
    constants = types.ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: state.home
    constants.find_node_executable = lambda name: state.pm_node if name == "node" else None
    for name, module in (("pm", pm), ("pm.extras", extras), ("pm.install", install),
                         ("hermes_constants", constants), ("tools", None), ("tools.lazy_deps", None)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr("shutil.which", lambda name: state.which.get(name))
    return state


def test_hermes_0216_channel_with_its_extra_installed_is_available(fake_hermes_pm):
    status = worker.requirements("telegram", fake_hermes_pm.repo)
    assert status == {"available": True, "code": "", "detail": "", "packages": []}
    assert fake_hermes_pm.asked == ["telegram"]


def test_hermes_0216_sealed_runtime_reports_a_missing_extra(fake_hermes_pm):
    fake_hermes_pm.missing["teams"] = ("microsoft_teams.apps",)
    status = worker.requirements("teams", fake_hermes_pm.repo)
    assert status["available"] is False and status["code"] == "missing_packages"
    assert status["packages"] == ["teams"]
    fake_hermes_pm.lazy_allowed = True
    assert worker.requirements("teams", fake_hermes_pm.repo)["code"] == "installs_on_start"


def test_hermes_0216_extra_gated_off_this_os_is_unsupported(fake_hermes_pm):
    fake_hermes_pm.unsupported.add("matrix")
    fake_hermes_pm.lazy_allowed = True
    assert worker.requirements("matrix", fake_hermes_pm.repo)["code"] == "unsupported_platform"


def test_hermes_0216_channel_extras_follow_the_old_feature_names(fake_hermes_pm):
    fake_hermes_pm.missing["google-chat"] = ("google.cloud.pubsub_v1",)
    status = worker.requirements("google_chat", fake_hermes_pm.repo)
    assert status["code"] == "missing_packages" and status["packages"] == ["google-chat"]
    assert fake_hermes_pm.asked == ["google", "google-chat"]
    fake_hermes_pm.missing["wecom"] = ("defusedxml",)
    assert worker.requirements("wecom_callback", fake_hermes_pm.repo)["packages"] == ["wecom"]
    # A channel without its own extra asks for none.
    fake_hermes_pm.asked.clear()
    assert worker.requirements("signal", fake_hermes_pm.repo)["available"] is True
    assert fake_hermes_pm.asked == []


def test_hermes_0216_whatsapp_qr_needs_the_node_hermes_installed_not_the_path_one(fake_hermes_pm):
    bridge = fake_hermes_pm.repo / "scripts/whatsapp-bridge/bridge.js"
    bridge.parent.mkdir(parents=True)
    bridge.write_text("// bridge", encoding="utf-8")
    fake_hermes_pm.which["node"] = "C:/Program Files/nodejs/node.exe"
    assert worker.requirements("whatsapp", fake_hermes_pm.repo)["code"] == "missing_program"
    fake_hermes_pm.pm_node = str(fake_hermes_pm.home / "tools" / "node" / "node.exe")
    assert worker.requirements("whatsapp", fake_hermes_pm.repo)["available"] is True


def test_unavailable_channel_refusal_reaches_the_browser(packaged, monkeypatch):
    from wizard import messaging as bridge

    refusal = HTTPException(409, "Este canal no está disponible: falta su puente.")
    monkeypatch.setattr(bridge.messaging, "call", AsyncMock(side_effect=refusal))
    with TestClient(wizard.app, **LOCAL) as client:
        response = client.put("/api/messaging/platforms/whatsapp", json={"enabled": True})
    assert response.status_code == 409 and "no está disponible" in response.json()["detail"]


def test_worker_enforces_availability_before_a_channel_could_start():
    source = (ROOT / "wizard/hermes_messaging_worker.py").read_text(encoding="utf-8")
    # New values could enable a channel too (WHATSAPP_ENABLED=true); switching
    # off and clearing stay allowed. The check precedes every write.
    assert "if body.enabled or body.env:" in source
    assert source.index("if body.enabled or body.env:") < source.index(
        "result = await native.update_messaging_platform(pid, body)")
    assert 'if action == "whatsapp_start":\n        require_available("whatsapp", REPO)' in source
    assert 'require_available("whatsapp", REPO)\n        status = await native.get_whatsapp_onboarding_status' in source
    # The catalog carries the same verdict the save path enforces.
    assert '"availability": requirements(p["id"], REPO)' in source


# ── Local voice previews survive the first model download ───────────────

def test_local_engine_preview_uses_the_isolated_worker_limits(packaged, monkeypatch):
    from core import tts

    timeouts = []
    original = asyncio.wait_for

    async def recording(awaitable, timeout):
        timeouts.append(timeout)
        return await original(awaitable, timeout)

    class FakeEngine:
        def __init__(self, cfg):
            pass

        async def initialize(self):
            return None

        async def speak(self, text):
            return b"RIFF" + b"\0" * 64, []

        async def close(self):
            return None

    monkeypatch.setattr(tts, "TextToSpeech", FakeEngine)
    monkeypatch.setattr(asyncio, "wait_for", recording)
    wizard._TTS_PREVIEW_CACHE.clear()
    headers = {"X-IRIS-Intent": "preview-tts", "Origin": LOCAL["base_url"]}
    with TestClient(wizard.app, **LOCAL) as client:
        local = client.post("/api/test-tts-audio", json={"provider": "kokoro", "voice": "ef_dora"}, headers=headers)
        remote = client.post("/api/test-tts-audio", json={"provider": "edge-tts", "voice": "es-MX-DaliaNeural"},
                             headers=headers)
    wizard._TTS_PREVIEW_CACHE.clear()
    assert local.status_code == 200 and remote.status_code == 200
    load, speak = tts.IsolatedExtraTTSProvider.LOAD_TIMEOUT, tts.IsolatedExtraTTSProvider.SYNTH_TIMEOUT
    assert timeouts[:2] == [load + 30.0, speak + 30.0]
    assert timeouts[2:4] == [30.0, 30.0]


# ── Switching to the packaged runtime never migrates or erases credentials ──

def _digest(paths):
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def test_switching_to_packaged_keeps_profile_credentials_untouched(packaged, monkeypatch):
    iris_home = packaged.root / "perfil"
    hermes_home = iris_home / "hermes"
    hermes_home.mkdir(parents=True)
    files = {
        iris_home / "config.json": json.dumps({"assistant_name": "IRIS", "llm_provider": "openrouter",
                                               "llm_model": "x/y", "tts_provider": "edge-tts"}),
        hermes_home / ".env": "OPENROUTER_API_KEY=fixture-not-a-real-key\nTELEGRAM_BOT_TOKEN=fixture\n",
        hermes_home / "auth.json": json.dumps({"providers": {"nous": {"access_token": "fixture"}}}),
        hermes_home / "config.yaml": "model:\n  provider: openrouter\n  default: x/y\n",
    }
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
    before = _digest(files)
    monkeypatch.setattr(wizard, "_hermes_installation_state_legacy", Mock(return_value=_legacy("installed")))
    monkeypatch.setattr("wizard.health_check.check_gateway_health",
                        AsyncMock(return_value={"ok": False, "detail": "Sin Gateway"}))
    with TestClient(wizard.app, **LOCAL) as client:
        for path in ("/api/runtime-info", "/api/hermes-status", "/api/test-hermes",
                     "/api/current-config", "/api/licenses", "/api/check-extras"):
            assert client.get(path).status_code == 200, path
    assert _digest(files) == before


# ── UI wiring: controls follow capabilities, copy follows the mode ──────

def test_a_hidden_wizard_button_stays_hidden_whatever_its_style():
    # QA12: `.btn-primary`'s display beat `hidden`, so «Configurar IA ahora» showed with AI ready.
    css = (ROOT / "wizard/ui/wizard.css").read_text(encoding="utf-8")
    assert "\nbutton[hidden] { display: none !important; }\n" in css
    html = (ROOT / "wizard/ui/index.html").read_text(encoding="utf-8")
    assert 'id="summary-configure-ai" class="btn-primary" hidden' in html


def test_wizard_ui_hides_external_installer_controls_unless_the_runtime_grants_them():
    css = (ROOT / "wizard/ui/wizard.css").read_text(encoding="utf-8")
    html = (ROOT / "wizard/ui/index.html").read_text(encoding="utf-8")
    js = (ROOT / "wizard/ui/wizard.js").read_text(encoding="utf-8")
    for selector in ("#btn-install-hermes", "#oc-os-precheck", "#beta-warning"):
        assert f'body:not([data-hermes-install="on"]) {selector}' in css
    for selector in ("#oc-step-daemon", ".autostart-row", "#autostart-detail"):
        assert f'body:not([data-hermes-autostart="on"]) {selector}' in css
    assert "body.dataset.hermesInstall = wizardRuntime.canInstallHermes ? 'on' : 'off';" in js
    assert '<script src="/ui/wizard_runtime.js?v=7"></script>' in html
    scripts = [html.index(f'<script src="/ui/{name}') for name in ("wizard_runtime.js", "llm_setup.js", "messaging.js", "wizard.js")]
    assert scripts == sorted(scripts)
    assert 'id="licenses-link"' in html and 'href="help.html#licenses"' in html
    assert 'data-runtime-only="development">python -m core.agent' in html


def test_packaged_copy_never_offers_installing_or_updating_hermes():
    runtime_js = (ROOT / "wizard/ui/wizard_runtime.js").read_text(encoding="utf-8")
    start = runtime_js.index("const PACKAGED_TEXT")
    packaged_copy = runtime_js[start:runtime_js.index("});", start)]
    for forbidden in ("Instalar Hermes", "instalador oficial", "BETA", "Actualiza Hermes", "PATH",
                      "python -m", "pip ", "git ", " uv "):
        assert forbidden not in packaged_copy
    html = (ROOT / "wizard/ui/index.html").read_text(encoding="utf-8")
    keys = set(__import__("re").findall(r'data-runtime-text="([^"]+)"', html))
    for key in keys:
        assert f"'{key}':" in packaged_copy, key


# ── Consent to Hermes' security notice lives in the profile ────────────

def test_security_consent_is_saved_alone_in_the_profile(packaged):
    iris_home = packaged.root / "perfil"
    iris_home.mkdir(parents=True, exist_ok=True)
    config = iris_home / "config.json"
    # Includes legacy LLM fields that IRISConfig.save would normalise away.
    original = {"assistant_name": "Luna", "tts_provider": "edge-tts",
                "llm_provider": "openrouter", "llm_model": "x/y", "custom": [1, "ñ"]}
    config.write_text(json.dumps(original), encoding="utf-8")
    with TestClient(wizard.app, **LOCAL) as client:
        before = client.get("/api/current-config").json()
        saved = client.post("/api/hermes-security-ack", json={"accepted": True}, headers={"Origin": LOCAL["base_url"]})
        after = client.get("/api/current-config").json()
    assert before["hermes_security_acknowledged"] is False
    assert saved.status_code == 200 and after["hermes_security_acknowledged"] is True
    stored = json.loads(config.read_text(encoding="utf-8"))
    assert stored.pop("hermes_security_acknowledged") is True
    assert stored == original


def test_security_consent_never_replaces_an_unreadable_config(packaged):
    iris_home = packaged.root / "perfil"
    iris_home.mkdir(parents=True, exist_ok=True)
    config = iris_home / "config.json"
    config.write_text("{ not json", encoding="utf-8")
    with TestClient(wizard.app, **LOCAL) as client:
        response = client.post("/api/hermes-security-ack", json={"accepted": True},
                               headers={"Origin": LOCAL["base_url"]})
    assert response.status_code == 500
    assert config.read_text(encoding="utf-8") == "{ not json"


def test_security_consent_refuses_foreign_origins_and_unknown_fields(packaged):
    with TestClient(wizard.app, **LOCAL) as client:
        foreign = client.post("/api/hermes-security-ack", json={"accepted": True},
                              headers={"Origin": "https://foreign.example", "Sec-Fetch-Site": "cross-site"})
        extra = client.post("/api/hermes-security-ack", json={"accepted": True, "provider": "x"},
                            headers={"Origin": LOCAL["base_url"]})
    assert foreign.status_code == 403 and extra.status_code == 422


def test_consent_dialog_guards_every_engine_start_in_the_wizard():
    js = (ROOT / "wizard/ui/wizard.js").read_text(encoding="utf-8")
    messaging_js = (ROOT / "wizard/ui/messaging.js").read_text(encoding="utf-8")
    html = (ROOT / "wizard/ui/index.html").read_text(encoding="utf-8")
    assert 'id="engine-consent-dialog"' in html and 'value="accept"' in html
    for function in ("async function startGateway()", "async function _startGatewayUsingSavedConfig(",
                     "async function launchIris()"):
        body = js[js.index(function):]
        guard = body.index("_ensureEngineConsent()")
        first_request = min(i for i in (body.find("fetch("), body.find("_persistLLMSelectionBeforeGateway()"),
                                         body.find("postMessage(")) if i >= 0)
        assert guard < first_request, function
    restart = messaging_js[messaging_js.index("async restart()"):]
    assert restart.index("irisEnsureEngineConsent") < restart.index("this.request('/restart'")


# ── GitHub Copilot ACP: the pinned Hermes' launch contract ───────────────
# Hermes uses the whole HERMES_COPILOT_ACP_COMMAND value as argv[0] and takes
# arguments only from HERMES_COPILOT_ACP_ARGS (agent/copilot_acp_client.py).

@pytest.fixture
def copilot_exe(tmp_path):
    executable = tmp_path / "Program Files" / "GitHub Copilot" / "copilot.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    return executable


COPILOT_KEYS = ("HERMES_COPILOT_ACP_COMMAND", "COPILOT_CLI_PATH", "HERMES_COPILOT_ACP_ARGS")


def _preflight(monkeypatch, env, inherited=None):
    """``env`` is the profile .env (None: the profile has none)."""
    monkeypatch.setattr("wizard.hermes_config.read_hermes_dotenv_if_present",
                        lambda: None if env is None else dict(env))
    for name in COPILOT_KEYS:
        monkeypatch.delenv(name, raising=False)
    for name, value in (inherited or {}).items():
        monkeypatch.setenv(name, value)
    return asyncio.run(wizard._external_provider_preflight("copilot-acp"))


def test_copilot_launch_matches_the_pinned_hermes_resolver(copilot_exe):
    from wizard.hermes_config import copilot_acp_launch

    assert copilot_acp_launch({"HERMES_COPILOT_ACP_COMMAND": f"  {copilot_exe}  "}) == (
        str(copilot_exe), ["--acp", "--stdio"])
    assert copilot_acp_launch({"COPILOT_CLI_PATH": str(copilot_exe),
                               "HERMES_COPILOT_ACP_ARGS": '--acp --stdio --log-dir "C:/x y"'}) == (
        str(copilot_exe), ["--acp", "--stdio", "--log-dir", "C:/x y"])
    assert copilot_acp_launch({}) == ("copilot", ["--acp", "--stdio"])
    with pytest.raises(ValueError):
        copilot_acp_launch({"HERMES_COPILOT_ACP_ARGS": '--log-dir "unterminated'})


def test_packaged_copilot_accepts_an_absolute_path_with_spaces(packaged, monkeypatch, copilot_exe):
    assert " " in str(copilot_exe)
    assert _preflight(monkeypatch, {"HERMES_COPILOT_ACP_COMMAND": str(copilot_exe)}) is None


@pytest.mark.parametrize("shape", ["quoted", "with_args", "bare_on_path", "relative"])
def test_packaged_copilot_rejects_values_hermes_could_not_start(packaged, monkeypatch, copilot_exe, shape):
    value = {
        "quoted": f'"{copilot_exe}"',
        "with_args": f'"{copilot_exe}" --acp --stdio',
        "bare_on_path": "copilot",
        "relative": "GitHub Copilot/copilot.exe",
    }[shape]
    monkeypatch.setenv("PATH", str(copilot_exe.parent))  # the bundled engine never sees this PATH
    monkeypatch.chdir(copilot_exe.parent.parent)
    result = _preflight(monkeypatch, {"HERMES_COPILOT_ACP_COMMAND": value})
    assert result["code"] == "external_cli_missing"
    assert "ruta completa" in result["message"] and "HERMES_COPILOT_ACP_ARGS" in result["message"]


def test_copilot_invalid_arguments_are_reported_not_guessed(packaged, monkeypatch, copilot_exe):
    result = _preflight(monkeypatch, {"HERMES_COPILOT_ACP_COMMAND": str(copilot_exe),
                                      "HERMES_COPILOT_ACP_ARGS": '--log-dir "unterminated'})
    assert result["code"] == "external_command_invalid"


@pytest.mark.skipif(os.name != "nt", reason="CreateProcess search rules are Windows-specific")
def test_development_copilot_bare_name_follows_createprocess_search(development, monkeypatch, tmp_path):
    shim_dir, exe_dir = tmp_path / "npm", tmp_path / "bin"
    shim_dir.mkdir()
    exe_dir.mkdir()
    (shim_dir / "copilot.cmd").write_text("@echo off", encoding="utf-8")
    monkeypatch.setenv("PATH", str(shim_dir))
    # An npm .cmd shim is found by shutil.which, but Popen(["copilot"]) cannot start it.
    assert _preflight(monkeypatch, {})["code"] == "external_cli_missing"
    (exe_dir / "copilot.exe").write_bytes(b"MZ")
    monkeypatch.setenv("PATH", os.pathsep.join([str(shim_dir), str(exe_dir)]))
    assert _preflight(monkeypatch, {}) is None


def test_health_check_uses_the_same_copilot_contract(packaged, monkeypatch, copilot_exe):
    from wizard import health_check

    monkeypatch.setattr("wizard.health_check.effective_auth_mode_for", lambda provider: "external", raising=False)
    for value, ok in ((str(copilot_exe), True), (f'"{copilot_exe}" --acp', False)):
        monkeypatch.setattr("wizard.hermes_config.read_hermes_dotenv_if_present",
                            lambda value=value: {"HERMES_COPILOT_ACP_COMMAND": value})
        result = asyncio.run(health_check.check_provider_credentials("copilot-acp"))
        assert result["ok"] is ok, (value, result)


# Codex r06: inherited Copilot keys. With a profile .env, Hermes' loader
# (hermes_cli/env_loader.py) overrides them, an explicit empty value included,
# and removes those the file does not define; only without .env are they used.
OTHER = "C:/Otra ruta/copilot.exe"


@pytest.mark.parametrize("env_file,inherited,expected", [
    (None, {"HERMES_COPILOT_ACP_COMMAND": "EXE"}, ("EXE", ["--acp", "--stdio"])),
    ({}, {"HERMES_COPILOT_ACP_COMMAND": "EXE"}, ("copilot", ["--acp", "--stdio"])),
    ({"HERMES_COPILOT_ACP_COMMAND": ""}, {"HERMES_COPILOT_ACP_COMMAND": "EXE"}, ("copilot", ["--acp", "--stdio"])),
    ({"HERMES_COPILOT_ACP_COMMAND": "EXE"}, {"HERMES_COPILOT_ACP_COMMAND": OTHER}, ("EXE", ["--acp", "--stdio"])),
    ({}, {"COPILOT_CLI_PATH": "EXE"}, ("copilot", ["--acp", "--stdio"])),
    (None, {"COPILOT_CLI_PATH": "EXE"}, ("EXE", ["--acp", "--stdio"])),
    ({"HERMES_COPILOT_ACP_COMMAND": "EXE"}, {"HERMES_COPILOT_ACP_ARGS": "--acp --stdio --x"}, ("EXE", ["--acp", "--stdio"])),
    ({"HERMES_COPILOT_ACP_COMMAND": "EXE", "HERMES_COPILOT_ACP_ARGS": ""},
     {"HERMES_COPILOT_ACP_ARGS": "--acp --stdio --x"}, ("EXE", ["--acp", "--stdio"])),
    (None, {"HERMES_COPILOT_ACP_COMMAND": "EXE", "HERMES_COPILOT_ACP_ARGS": "--acp --stdio --x"},
     ("EXE", ["--acp", "--stdio", "--x"])),
], ids=["no_env_inherits", "env_without_key_scrubs", "env_empty_overrides", "env_value_wins",
        "env_scrubs_cli_path", "no_env_inherits_cli_path", "env_scrubs_args", "env_empty_args",
        "no_env_inherits_args"])
def test_copilot_launch_follows_the_pinned_profile_loader(monkeypatch, copilot_exe, env_file, inherited, expected):
    from wizard.hermes_config import copilot_acp_launch

    fill = lambda value: str(copilot_exe) if value == "EXE" else value
    for name in COPILOT_KEYS:
        monkeypatch.delenv(name, raising=False)
    for name, value in inherited.items():
        monkeypatch.setenv(name, fill(value))
    env = None if env_file is None else {k: fill(v) for k, v in env_file.items()}
    assert copilot_acp_launch(env) == (fill(expected[0]), expected[1])


@pytest.mark.parametrize("env_file", [{}, {"HERMES_COPILOT_ACP_COMMAND": ""}], ids=["absent_key", "empty_key"])
def test_packaged_preflight_ignores_an_inherited_command_the_profile_overrides(packaged, monkeypatch, copilot_exe, env_file):
    # Iris used to validate the inherited executable while Hermes resolved "copilot".
    result = _preflight(monkeypatch, env_file, {"HERMES_COPILOT_ACP_COMMAND": str(copilot_exe)})
    assert result["code"] == "external_cli_missing"
    assert _preflight(monkeypatch, None, {"HERMES_COPILOT_ACP_COMMAND": str(copilot_exe)}) is None


def test_profile_dotenv_distinguishes_missing_and_empty(packaged):
    from wizard.hermes_config import get_env_path, read_hermes_dotenv_if_present

    assert read_hermes_dotenv_if_present() is None
    get_env_path().parent.mkdir(parents=True, exist_ok=True)
    get_env_path().write_text("", encoding="utf-8")
    assert read_hermes_dotenv_if_present() == {}
    get_env_path().write_text("HERMES_COPILOT_ACP_COMMAND=\n", encoding="utf-8")
    assert read_hermes_dotenv_if_present() == {"HERMES_COPILOT_ACP_COMMAND": ""}


FACE = "http://127.0.0.1:9877/face/"


def _desktop_run(monkeypatch, *, accepted, face_url):
    from core import desktop
    from core.config import get_iris_home
    from fastapi.responses import JSONResponse

    iris_home = get_iris_home()
    iris_home.mkdir(parents=True, exist_ok=True)
    (iris_home / "config.json").write_text(json.dumps({"hermes_security_acknowledged": accepted}), encoding="utf-8")
    before = (iris_home / "config.json").read_bytes()
    monkeypatch.setattr(desktop, "probe_iris", AsyncMock(return_value=face_url))
    monkeypatch.setattr("core.hermes_runtime.resolve_hermes_python", Mock(return_value="python.exe"))
    launch = Mock(side_effect=AssertionError("Desktop must not launch another Iris here"))
    monkeypatch.setattr(desktop, "launch_iris", launch)
    gateway = AsyncMock(return_value=JSONResponse({"status": "running", "already_running": True}))
    monkeypatch.setattr("wizard.app.start_gateway", gateway)
    state = desktop.DesktopRuntime()
    asyncio.run(state._start())
    assert (iris_home / "config.json").read_bytes() == before
    return state.state, gateway


@pytest.mark.parametrize("face_url", [None, FACE], ids=["iris_closed", "iris_open"])
def test_packaged_desktop_without_consent_starts_nothing_and_reuses_nothing(packaged, monkeypatch, face_url):
    # Codex r06: with an Iris already open, Desktop still called start_gateway.
    # Reusing that Iris is refused too: Face can start the engine from there.
    state, gateway = _desktop_run(monkeypatch, accepted=False, face_url=face_url)
    gateway.assert_not_called()
    assert state["phase"] == "setup" and "aviso de seguridad" in state["message"]
    assert state["face_url"] == "" and state["reused"] is False


def test_packaged_desktop_with_consent_reuses_an_open_iris(packaged, monkeypatch):
    state, gateway = _desktop_run(monkeypatch, accepted=True, face_url=FACE)
    gateway.assert_awaited_once()
    assert state["phase"] == "ready" and state["reused"] is True and state["face_url"] == FACE


def test_packaged_desktop_with_consent_reaches_the_engine_preflight(packaged, monkeypatch):
    from fastapi.responses import JSONResponse

    gateway = AsyncMock(return_value=JSONResponse({"status": "error", "code": "missing_api_key", "message": "x"}))
    monkeypatch.setattr("wizard.app.start_gateway", gateway)
    from core import desktop
    from core.config import get_iris_home

    get_iris_home().mkdir(parents=True, exist_ok=True)
    (get_iris_home() / "config.json").write_text(json.dumps({"hermes_security_acknowledged": True}), encoding="utf-8")
    monkeypatch.setattr(desktop, "probe_iris", AsyncMock(return_value=None))
    monkeypatch.setattr("core.hermes_runtime.resolve_hermes_python", Mock(return_value="python.exe"))
    state = desktop.DesktopRuntime()
    asyncio.run(state._start())
    gateway.assert_awaited_once()
    assert state.state["phase"] == "setup"


def test_development_desktop_reuses_an_open_iris_without_the_notice(development, monkeypatch):
    state, gateway = _desktop_run(monkeypatch, accepted=False, face_url=FACE)
    gateway.assert_awaited_once()
    assert state["phase"] == "ready" and state["reused"] is True


# ── Backend barrier: no route starts or restarts the bundled engine without it ─

def _engine_effects(monkeypatch):
    effect = AsyncMock(side_effect=AssertionError("the engine must not be touched"))
    monkeypatch.setattr(wizard, "_start_gateway_impl", effect)
    monkeypatch.setattr(wizard, "_stop_running_gateway", effect)
    monkeypatch.setattr("wizard.messaging.messaging.call", effect)
    monkeypatch.setattr("core.process_launcher.launch_iris", Mock(side_effect=AssertionError("no Iris launch")))
    return effect


def _write_consent(accepted):
    from core.config import get_iris_home

    get_iris_home().mkdir(parents=True, exist_ok=True)
    (get_iris_home() / "config.json").write_text(json.dumps({"hermes_security_acknowledged": accepted}), encoding="utf-8")


@pytest.mark.parametrize("path", ["/api/start-gateway", "/api/restart-gateway", "/api/iris/launch", "/api/messaging/restart"])
def test_packaged_backend_refuses_every_engine_start_without_consent(packaged, monkeypatch, path):
    _write_consent(False)
    effect = _engine_effects(monkeypatch)
    with TestClient(wizard.app, **LOCAL) as client:
        response = client.post(path)
    effect.assert_not_called()
    assert response.status_code == 409
    body = response.json()
    assert body.get("code") == "security_ack_required" or "aviso de seguridad" in str(body.get("detail"))
    assert "aviso de seguridad" in json.dumps(body, ensure_ascii=False)


def test_packaged_backend_reaches_the_engine_once_the_profile_accepted(packaged, monkeypatch):
    from fastapi.responses import JSONResponse

    _write_consent(True)
    start = AsyncMock(return_value=JSONResponse({"status": "running", "already_running": True}))
    monkeypatch.setattr(wizard, "_start_gateway_impl", start)
    with TestClient(wizard.app, **LOCAL) as client:
        response = client.post("/api/start-gateway")
    start.assert_awaited_once()
    assert response.json()["status"] == "running"


def test_development_backend_does_not_ask_for_the_packaged_notice(development, monkeypatch):
    from fastapi.responses import JSONResponse

    _write_consent(False)
    start = AsyncMock(return_value=JSONResponse({"status": "running", "already_running": True}))
    monkeypatch.setattr(wizard, "_start_gateway_impl", start)
    with TestClient(wizard.app, **LOCAL) as client:
        response = client.post("/api/start-gateway")
    start.assert_awaited_once()
    assert response.json()["status"] == "running"


def test_unreadable_profile_counts_as_not_accepted(packaged):
    from core.config import engine_consent_missing, get_iris_home

    get_iris_home().mkdir(parents=True, exist_ok=True)
    (get_iris_home() / "config.json").write_text("{ roto", encoding="utf-8")
    assert engine_consent_missing() is True
