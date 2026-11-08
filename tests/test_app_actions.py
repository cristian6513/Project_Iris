"""F09.4: the app action bridge. Reviewed operations only; validated, authorized and run once."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from core.app_actions import (
    APP_SURFACE_ROUTE,
    AppActionBridge,
    AppActionRegistry,
    AppOperation,
    OperationRejected,
)
from core.app_services import AppServiceError, include_app_services, load_app_backend
from core.capability_permissions import CapabilityPermissionStore
from core.player_actions import register_player_actions
from core.surface_registry import SurfaceRegistry

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "apps" / "_plantilla-servicio"


class _Surface:
    """A socket that answers each intent the way a script says."""

    def __init__(self, reply=None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.bridge: AppActionBridge | None = None
        self.reply = reply
        self.client = type("Peer", (), {"host": "127.0.0.1"})()

    async def send_text(self, message: str) -> None:
        outbound = json.loads(message)
        self.sent.append(outbound)
        if self.reply is not None and outbound["type"].endswith("intent"):
            answer = self.reply(outbound["payload"])
            if answer is not None:
                self.bridge.answer(self, outbound["type"].replace("intent", "result"), answer)


def _registry_with_surface(capability: str, surface: _Surface) -> SurfaceRegistry:
    registry = SurfaceRegistry()
    registry.connect(surface)
    registry.register_ready(surface, {"role": "face", "surface_id": "main", "visible": True, "focused": True,
                                      "capabilities": {capability: True}})
    return registry


def _bridge(tmp_path: Path, surfaces: SurfaceRegistry | None = None, mode: str | None = None, registry: AppActionRegistry | None = None):
    store = CapabilityPermissionStore(tmp_path / "permissions.json")
    if mode:
        store.set_mode("player.control", mode)
        store.set_mode("apps.actions", mode)
    if registry is None:
        registry = AppActionRegistry()
        register_player_actions(registry)
    disconnected: list[Any] = []
    bridge = AppActionBridge(registry, surfaces=lambda: surfaces or SurfaceRegistry(), capabilities=lambda: store,
                             on_disconnect=disconnected.append)
    return bridge, store, disconnected


def _ok(intent: dict[str, Any], **data) -> dict[str, Any]:
    return {"schema": "iris.app-result/v1", "request_id": intent["request_id"], "status": "ok", "message": "hecho", "data": data}


def test_operations_are_declared_once_with_one_place_to_run() -> None:
    registry = AppActionRegistry()
    register_player_actions(registry)
    with pytest.raises(ValueError, match="ya está registrada"):
        register_player_actions(registry)
    common = dict(label="x", capability="apps.actions", effect="read", validate_args=dict, project_data=dict)
    with pytest.raises(ValueError, match="uno de los dos"):
        AppOperation(app="notas", action="leer", **common)
    with pytest.raises(ValueError, match="uno de los dos"):
        AppOperation(app="notas", action="leer", surface=APP_SURFACE_ROUTE, handler=lambda args: {}, **common)
    with pytest.raises(ValueError, match="nombre no válido"):
        AppOperation(app="../x", action="leer", handler=lambda args: {}, **common)
    with pytest.raises(ValueError, match="Efecto"):
        AppOperation(app="notas", action="leer", handler=lambda args: {}, **{**common, "effect": "delete"})


@pytest.mark.parametrize(("payload", "detail"), [
    ({"app": "player", "action": "unknown", "args": {}}, "Acción no admitida"),
    ({"app": "correo", "action": "send", "args": {}}, "Acción no admitida"),
    ({"app": "player", "action": "pause", "args": {}, "confirmed": True}, "campos no admitidos"),
    ({"app": "player", "action": "pause", "args": {}, "request_id": "mine"}, "campos no admitidos"),
    ({"app": "player", "action": "pause", "args": []}, "objeto"),
    ({"schema": "iris.app-intent/v9", "app": "player", "action": "pause"}, "no compatible"),
    ({"app": "player", "action": "set_volume", "args": {"percent": 101}}, "entre 0 y 100"),
    ({"app": "player", "action": "set_volume", "args": {"percent": True}}, "entre 0 y 100"),
    ({"app": "player", "action": "pause", "args": {"path": "C:/Music"}}, "no admitidos"),
    ({"app": "player", "action": "play", "args": {"track_id": "../song.mp3"}}, "ID de pista"),
])
def test_an_intent_that_is_not_a_registered_operation_never_runs(tmp_path: Path, payload: dict, detail: str) -> None:
    surface = _Surface(reply=_ok)
    bridge, _, _ = _bridge(tmp_path, _registry_with_surface("controls_local_player", surface))
    surface.bridge = bridge
    with pytest.raises(OperationRejected, match=detail):
        asyncio.run(bridge.execute(payload))
    assert surface.sent == []


def test_deny_stops_before_any_surface_and_is_recorded(tmp_path: Path) -> None:
    surface = _Surface(reply=_ok)
    bridge, store, _ = _bridge(tmp_path, _registry_with_surface("controls_local_player", surface), mode="deny")
    surface.bridge = bridge
    result = asyncio.run(bridge.execute({"app": "player", "action": "pause", "args": {}}))
    assert result["status"] == "denied" and "Capability Center" in result["message"]
    assert surface.sent == []
    assert store.snapshot()["audit"][0]["kind"] == "action_blocked"


def test_one_surface_receives_it_and_only_its_answer_counts(tmp_path: Path) -> None:
    other = _Surface()
    owner = _Surface()
    surfaces = _registry_with_surface("controls_local_player", owner)
    bridge, _, _ = _bridge(tmp_path, surfaces, mode="allow")
    owner.bridge = other.bridge = bridge

    def answer_after_noise(intent: dict[str, Any]) -> dict[str, Any]:
        # Another socket, the wrong message family and a forged id are all ignored.
        assert bridge.answer(other, "iris_player_result", _ok(intent, playback_state="paused")) is False
        assert bridge.answer(owner, "iris_app_result", _ok(intent, playback_state="paused")) is False
        assert bridge.answer(owner, "iris_player_result", {**_ok(intent), "request_id": "forged"}) is False
        return {**_ok(intent, playback_state="playing", path="C:/private/song.mp3", token="secret")}

    owner.reply = answer_after_noise
    result = asyncio.run(bridge.execute({"app": "player", "action": "pause", "args": {}}))
    assert result["status"] == "ok"
    assert result["data"] == {"playback_state": "playing"}, "only the projected fields leave"
    assert len(owner.sent) == 1 and other.sent == []
    sent = owner.sent[0]
    assert sent["type"] == "iris_player_intent"
    assert sent["payload"]["operation"] == {"version": 1, "effect": "local", "label": "pausar la música", "summary": ""}
    assert bridge.pending() == 0


def test_a_late_or_missing_answer_is_not_success_and_is_never_retried(tmp_path: Path) -> None:
    owner = _Surface()  # never answers
    bridge, _, _ = _bridge(tmp_path, _registry_with_surface("controls_local_player", owner), mode="allow")
    owner.bridge = bridge
    # A fresh registry (from _bridge), so shortening its wait touches no other test.
    object.__setattr__(bridge.registry.get("player", "next"), "timeout_s", 0.05)
    result = asyncio.run(bridge.execute({"app": "player", "action": "next", "args": {}}))
    assert result["status"] == "unavailable"
    assert "pudo haber comenzado" in result["message"]
    assert len(owner.sent) == 1, "sent once, not again"
    assert bridge.pending() == 0


def test_a_broken_surface_is_disconnected_and_reported(tmp_path: Path) -> None:
    class Broken(_Surface):
        async def send_text(self, message: str) -> None:
            raise ConnectionError("gone")

    owner = Broken()
    bridge, _, disconnected = _bridge(tmp_path, _registry_with_surface("controls_local_player", owner), mode="allow")
    result = asyncio.run(bridge.execute({"app": "player", "action": "pause", "args": {}}))
    assert result["status"] == "unavailable" and "desconectó" in result["message"]
    assert disconnected == [owner]


@pytest.mark.parametrize("confirmed", [True, False])
def test_the_persons_answer_on_the_surface_reaches_capability_center(tmp_path: Path, confirmed: bool) -> None:
    # Before F09.4 the projection dropped permission_confirmed and this event was never written.
    def reply(intent: dict[str, Any]) -> dict[str, Any]:
        assert intent["permission"]["requires_confirmation"] is True
        if not confirmed:
            return {"schema": "iris.app-result/v1", "request_id": intent["request_id"], "status": "denied",
                    "message": "no", "data": {"permission_confirmed": False}}
        return _ok(intent, playback_state="paused", permission_confirmed=True)

    owner = _Surface(reply=reply)
    bridge, store, _ = _bridge(tmp_path, _registry_with_surface("controls_local_player", owner))  # default: ask
    owner.bridge = bridge
    result = asyncio.run(bridge.execute({"app": "player", "action": "pause", "args": {}}))
    assert result["status"] == ("ok" if confirmed else "denied")
    assert "permission_confirmed" not in result["data"]
    assert store.snapshot()["audit"][0]["kind"] == ("action_confirmed" if confirmed else "action_blocked")


def _core_registry(calls: list) -> AppActionRegistry:
    registry = AppActionRegistry()

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        calls.append(args)
        return {"status": "ok", "data": {"count": 2, "secret": "x"}}

    registry.register(AppOperation(
        app="notas", action="contar", label="contar tus notas", capability="apps.actions", effect="read",
        validate_args=lambda args: {}, project_data=lambda data: {"count": data.get("count")}, handler=handler,
    ))
    return registry


def test_a_core_operation_runs_once_when_allowed(tmp_path: Path) -> None:
    calls: list = []
    bridge, _, _ = _bridge(tmp_path, mode="allow", registry=_core_registry(calls))
    result = asyncio.run(bridge.execute({"app": "notas", "action": "contar", "args": {}}))
    assert result == {"schema": "iris.app-result/v1", "request_id": "", "status": "ok", "message": "", "data": {"count": 2}}
    assert calls == [{}]


@pytest.mark.parametrize(("answer", "status", "runs"), [
    ("yes", "ok", True),
    ("no", "denied", False),
    ("silence", "denied", False),
    ("nobody", "unavailable", False),
])
def test_ask_for_a_core_operation_is_answered_on_the_iris_os_surface(tmp_path: Path, answer: str, status: str, runs: bool) -> None:
    calls: list = []

    def reply(intent: dict[str, Any]):
        assert intent["confirm_only"] is True and intent["operation"]["label"] == "contar tus notas"
        if answer == "yes":
            return _ok(intent, permission_confirmed=True)
        if answer == "no":
            return {"schema": "iris.app-result/v1", "request_id": intent["request_id"], "status": "denied",
                    "message": "No confirmada.", "data": {"permission_confirmed": False}}
        return None  # silence

    owner = _Surface(reply=reply)
    surfaces = SurfaceRegistry() if answer == "nobody" else _registry_with_surface("hosts_iris_apps", owner)
    registry = _core_registry(calls)
    object.__setattr__(registry.get("notas", "contar"), "timeout_s", 0.05)
    object.__setattr__(registry.get("notas", "contar"), "confirmation_timeout_s", 0.05)
    bridge, _, _ = _bridge(tmp_path, surfaces, registry=registry)  # apps.actions default: ask
    owner.bridge = bridge
    result = asyncio.run(bridge.execute({"app": "notas", "action": "contar", "args": {}}))
    assert result["status"] == status
    assert bool(calls) is runs, "the handler runs only after an explicit yes"


def test_a_failing_core_operation_says_so(tmp_path: Path) -> None:
    registry = AppActionRegistry()
    registry.register(AppOperation(
        app="notas", action="contar", label="contar", capability="apps.actions", effect="read",
        validate_args=lambda args: {}, project_data=dict, handler=lambda args: 1 / 0,
    ))
    bridge, _, _ = _bridge(tmp_path, mode="allow", registry=registry)
    result = asyncio.run(bridge.execute({"app": "notas", "action": "contar", "args": {}}))
    assert result["status"] == "error" and "no se pudo completar" in result["message"]


def test_the_catalog_names_operations_without_data() -> None:
    import core.websocket_server as server

    catalog = server._app_action_registry.catalog()
    assert [(entry["app"], entry["action"]) for entry in catalog] == [
        ("calendar", "list_events"), ("conexiones", "status"), ("mail", "list_messages"), ("mail", "read_message"),
        ("player", "next"), ("player", "pause"), ("player", "play"), ("player", "play_by_intent"),
        ("player", "play_by_query"), ("player", "previous"), ("player", "set_volume"),
    ]
    assert all((entry["runs_on"], entry["capability"]) == (("surface", "player.control") if entry["app"] == "player" else ("core", "google.read")) for entry in catalog)
    assert set(catalog[0]) == {"app", "action", "version", "label", "effect", "capability", "arguments", "runs_on"}


def _request(host: str, path: str = "/api/app-actions") -> Request:
    return Request({"type": "http", "method": "POST", "path": path, "headers": [], "client": (host, 54000),
                    "server": ("127.0.0.1", 8765), "scheme": "http", "query_string": b""})


def test_app_action_endpoints_are_local_only_and_reject_unknown_operations(tmp_path: Path) -> None:
    import core.websocket_server as server

    async def run(payload, host="127.0.0.1"):
        with patch("core.websocket_server._surface_registry", SurfaceRegistry()), \
             patch("core.websocket_server._capability_store", CapabilityPermissionStore(tmp_path / "p.json")):
            return await server.app_actions_execute(server.AppActionRequest(**payload), _request(host))

    with pytest.raises(HTTPException) as lan:
        asyncio.run(run({"app": "player", "action": "pause"}, host="192.168.1.20"))
    assert lan.value.status_code == 403
    with pytest.raises(HTTPException) as unknown:
        asyncio.run(run({"app": "correo", "action": "send"}))
    assert unknown.value.status_code == 422
    response = asyncio.run(run({"app": "player", "action": "pause"}))
    assert json.loads(response.body)["status"] == "unavailable"
    with pytest.raises(HTTPException) as catalog_lan:
        asyncio.run(server.app_actions_catalog(_request("192.168.1.20", "/api/app-actions/catalog")))
    assert catalog_lan.value.status_code == 403


def test_a_reviewed_app_registers_only_its_own_operations(tmp_path: Path, monkeypatch) -> None:
    import core.app_services as services

    registry = AppActionRegistry()
    register_player_actions(registry)
    monkeypatch.setattr(services, "app_source_dirs", lambda app_id: [TEMPLATE])
    app = FastAPI()
    assert include_app_services(app, home=tmp_path, ids=["mi-servicio"], operations=registry) == ["mi-servicio"]
    assert registry.get("mi-servicio", "list_items").handler is not None
    assert registry.get("mi-servicio", "show_item").surface == APP_SURFACE_ROUTE

    # The read Hermes asks for and the interface's GET use the same domain function.
    bridge, _, _ = _bridge(tmp_path, mode="allow", registry=registry)
    via_hermes = asyncio.run(bridge.execute({"app": "mi-servicio", "action": "list_items", "args": {}}))
    via_ui = TestClient(app).get("/api/apps/mi-servicio/items").json()
    assert via_hermes["data"] == via_ui
    with pytest.raises(OperationRejected, match="item"):
        asyncio.run(bridge.execute({"app": "mi-servicio", "action": "show_item", "args": {"item": 3}}))

    # Registering the same app again leaves it out instead of replacing anything.
    assert include_app_services(FastAPI(), home=tmp_path, ids=["mi-servicio"], operations=registry) == []


@pytest.mark.parametrize(("source", "message"), [
    ("ops = [AppOperation(app='player', action='stop', label='x', capability='apps.actions', effect='local', "
     "validate_args=dict, project_data=dict, surface=APP_SURFACE_ROUTE)]", "no puede registrar operaciones de player"),
    ("ops = [AppOperation(app='rota', action='leer', label='x', capability='player.control', effect='read', "
     "validate_args=dict, project_data=dict, handler=dict)]", "capacidad apps.actions"),
    ("ops = [AppOperation(app='rota', action='leer', label='x', capability='apps.actions', effect='read', "
     "validate_args=dict, project_data=dict, surface=PLAYER_ROUTE)]", "solo puede ejecutarse en Core o en IRIS OS"),
    ("ops = 'no es una lista'", "debe devolver una lista"),
])
def test_an_app_cannot_borrow_another_apps_name_capability_or_route(tmp_path: Path, source: str, message: str) -> None:
    backend = tmp_path / "rota" / "backend"
    backend.mkdir(parents=True)
    (backend / "service.py").write_text(
        "from core.app_actions import APP_SURFACE_ROUTE, AppOperation\n"
        "from core.player_actions import PLAYER_ROUTE\n"
        f"{source}\n"
        "def operations(context):\n    return ops\n",
        encoding="utf-8",
    )
    with pytest.raises(AppServiceError, match=message):
        load_app_backend("rota", home=tmp_path, source_dir=tmp_path / "rota")


def test_the_skill_adapter_reads_a_file_talks_to_loopback_and_reports_honestly(tmp_path: Path, monkeypatch, capsys) -> None:
    import importlib.util
    import sys

    # Loaded by path, as the skill runs it: no help from sys.path (that hid R01).
    script = ROOT / ".hermes" / "skills" / "iris-os" / "scripts" / "app_action.py"
    monkeypatch.setenv("IRIS_WEBSOCKET_PORT", "8765")
    spec = importlib.util.spec_from_file_location("app_action_under_test", script)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    seen: list[tuple[str, str, Any]] = []

    class Answer:
        def __init__(self, body: dict) -> None:
            self.body = json.dumps(body).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, limit: int) -> bytes:
            return self.body

    def fake_urlopen(request, timeout):
        seen.append((request.get_method(), request.full_url, request.data and json.loads(request.data)))
        if request.full_url.endswith("/catalog"):
            return Answer({"schema": "iris.app-actions/v1", "operations": []})
        return Answer({"schema": "iris.app-result/v1", "request_id": "r", "status": "denied", "message": "no", "data": {}})

    monkeypatch.setattr(adapter.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sys, "argv", ["app_action.py", "--catalog"])
    assert adapter.main() == 0
    request = tmp_path / "intent.json"
    request.write_text(json.dumps({"schema": "iris.app-intent/v1", "app": "notas", "action": "contar", "args": {}, "request_id": "mine"}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["app_action.py", "--payload-file", str(request)])
    assert adapter.main() == 2, "denied is not success"
    assert seen == [
        ("GET", "http://127.0.0.1:8765/api/app-actions/catalog", None),
        ("POST", "http://127.0.0.1:8765/api/app-actions", {"schema": "iris.app-intent/v1", "app": "notas", "action": "contar", "args": {}}),
    ], "loopback only, and the model's request_id never travels"
    assert '"status": "denied"' in capsys.readouterr().out


ISOLATED_PYTHONS = [pytest.param([__import__("sys").executable, "-I", "-S", "-X", "utf8"], id="dev-isolated")]
_PRIVATE = ROOT / "artifacts" / "a08-9" / "v0.9.2-qa11" / "runtime" / "runtimes" / "hermes" / "python.exe"
if _PRIVATE.is_file():
    ISOLATED_PYTHONS.append(pytest.param([str(_PRIVATE)], id="iris-private-python"))


@pytest.mark.parametrize("python", ISOLATED_PYTHONS)
def test_the_real_adapter_command_runs_in_an_isolated_python(tmp_path: Path, python: list[str]) -> None:
    """R01: the command the skill announces, in a Python that does not put the script's folder
    on sys.path (Iris' private runtime). No Core is listening: it must still start and answer."""
    import socket
    import subprocess

    script = ROOT / ".hermes" / "skills" / "iris-os" / "scripts" / "app_action.py"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    # As Hermes launches it: PYTHONUTF8=1 (tools/environments/local.py), output read as UTF-8.
    env = {"IRIS_WEBSOCKET_PORT": str(closed_port), "PYTHONUTF8": "1",
           "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")}

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([*python, str(script), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, timeout=60)

    assert run("--help").returncode == 0
    catalog = run("--catalog")
    assert catalog.returncode == 1 and "no está disponible" in catalog.stdout
    request = tmp_path / "intent.json"
    request.write_text(json.dumps({"schema": "iris.app-intent/v1", "app": "player", "action": "pause", "args": {}}), encoding="utf-8")
    sent = run("--payload-file", str(request))
    assert sent.returncode == 1 and "no está disponible" in sent.stdout
    assert "ModuleNotFoundError" not in catalog.stderr + sent.stderr


def test_the_confirmation_carries_what_the_action_works_on(tmp_path: Path) -> None:
    """Codex A03: reviewed code describes the object from the validated arguments."""
    owner = _Surface(reply=lambda intent: _ok(intent, playback_state="playing"))
    bridge, _, _ = _bridge(tmp_path, _registry_with_surface("controls_local_player", owner), mode="allow")
    owner.bridge = bridge
    asyncio.run(bridge.execute({"app": "player", "action": "set_volume", "args": {"percent": 35}}))
    asyncio.run(bridge.execute({"app": "player", "action": "play_by_query", "args": {"query": "  Believer  "}}))
    summaries = [sent["payload"]["operation"]["summary"] for sent in owner.sent]
    assert summaries == ["Volumen al 35 %", "Buscar: «Believer»"]

    calls: list = []
    registry = _core_registry(calls)
    noisy = registry.get("notas", "contar")
    object.__setattr__(noisy, "describe", lambda args: "x" * 500)
    assert len(noisy.summary({})) == 160
    object.__setattr__(noisy, "describe", lambda args: 1 / 0)
    assert noisy.summary({}) == "", "a broken description never blocks or leaks"


def test_a_deny_set_while_the_person_is_asked_stops_the_core_operation(tmp_path: Path) -> None:
    """Codex A02: the policy is checked again after the wait, before the effect."""
    calls: list = []
    registry = _core_registry(calls)
    holder: dict[str, Any] = {}

    def reply(intent: dict[str, Any]) -> dict[str, Any]:
        holder["store"].set_mode("apps.actions", "deny")  # changed while the dialog is open
        return _ok(intent, permission_confirmed=True)

    owner = _Surface(reply=reply)
    bridge, store, _ = _bridge(tmp_path, _registry_with_surface("hosts_iris_apps", owner), registry=registry)
    holder["store"] = store
    owner.bridge = bridge
    result = asyncio.run(bridge.execute({"app": "notas", "action": "contar", "args": {}}))
    assert result["status"] == "denied"
    assert calls == [], "the yes given before the deny does not run it"
    audit = store.snapshot()["audit"]
    assert audit[0]["kind"] == "action_blocked" and "cambió durante la confirmación" in audit[0]["detail"]
    assert all(entry["kind"] != "action_confirmed" for entry in audit)


@pytest.mark.parametrize("revoke", [True, False], ids=["deny-while-recording", "control"])
def test_a_deny_saved_while_the_yes_is_recorded_still_stops_it(tmp_path: Path, revoke: bool) -> None:
    """Codex R08: the last check sits right before the effect, after the «yes» is recorded.
    A barrier holds the real store's record_event while a deny is saved (or, as control, not)."""
    import threading

    calls: list = []
    registry = _core_registry(calls)
    owner = _Surface(reply=lambda intent: _ok(intent, permission_confirmed=True))
    bridge, store, _ = _bridge(tmp_path, _registry_with_surface("hosts_iris_apps", owner), registry=registry)
    owner.bridge = bridge
    entered, release = threading.Event(), threading.Event()
    record_event = store.record_event

    def held(kind: str, **fields: Any) -> None:
        if kind == "action_confirmed":
            entered.set()
            release.wait(10)
        record_event(kind, **fields)

    store.record_event = held

    async def scenario() -> dict[str, Any]:
        running = asyncio.ensure_future(bridge.execute({"app": "notas", "action": "contar", "args": {}}))
        assert await asyncio.to_thread(entered.wait, 10), "the yes is recorded before the effect"
        if revoke:
            await asyncio.to_thread(store.set_mode, "apps.actions", "deny")
        release.set()
        return await running

    outcome = asyncio.run(scenario())
    audit = [entry["kind"] for entry in reversed(store.snapshot()["audit"])]  # oldest first
    if revoke:
        assert outcome["status"] == "denied" and calls == [], "a deny saved before the effect stops it"
        # In order: the deny, the person's yes, and the block that followed it.
        assert audit == ["policy_changed", "action_confirmed", "action_blocked"]
    else:
        assert outcome["status"] == "ok" and calls == [{}]
        assert audit == ["action_confirmed"]


def test_a_lan_surface_never_confirms_or_runs_this_pcs_app_actions(tmp_path: Path) -> None:
    """Codex A01: hosts_iris_apps is a control of this PC, like the Player."""
    import core.websocket_server as server

    claimed = {"capabilities": {"hosts_iris_apps": True, "controls_local_player": True, "accepts_chat": True}}
    assert server._surface_payload_for_peer(claimed, "192.168.1.20")["capabilities"]["hosts_iris_apps"] is False
    assert server._surface_payload_for_peer(claimed, "127.0.0.1")["capabilities"]["hosts_iris_apps"] is True

    calls: list = []
    local = _Surface(reply=lambda intent: _ok(intent, permission_confirmed=True))
    remote = _Surface(reply=lambda intent: _ok(intent, permission_confirmed=True))
    remote.client = type("Peer", (), {"host": "192.168.1.20"})()
    surfaces = SurfaceRegistry()
    for socket, focused in ((local, False), (remote, True)):  # the LAN one is focused
        surfaces.connect(socket)
        surfaces.register_ready(socket, {"role": "face", "surface_id": str(id(socket)), "visible": True,
                                         "focused": focused, "capabilities": {"hosts_iris_apps": True}})
    store = CapabilityPermissionStore(tmp_path / "permissions.json")
    bridge = AppActionBridge(_core_registry(calls), surfaces=lambda: surfaces, capabilities=lambda: store,
                             owner_allowed=server._app_actions._owner_allowed)
    local.bridge = remote.bridge = bridge
    assert asyncio.run(bridge.execute({"app": "notas", "action": "contar", "args": {}}))["status"] == "ok"
    assert remote.sent == [] and [m['type'] for m in local.sent] == ['iris_app_intent', 'iris_app_intent_closed'], "the PC's own surface asks and closes the request, never the LAN one"

    surfaces.disconnect(local)
    result = asyncio.run(bridge.execute({"app": "notas", "action": "contar", "args": {}}))
    assert result["status"] == "unavailable" and remote.sent == []
    assert calls == [{}], "ran once, after the local yes only"


def test_a_slow_plain_handler_neither_blocks_core_nor_pretends(tmp_path: Path) -> None:
    """Codex A04: a plain function runs off the loop; its limit stops the wait, not the work."""
    import time

    registry = AppActionRegistry()
    registry.register(AppOperation(
        app="notas", action="lento", label="tardar", capability="apps.actions", effect="local",
        validate_args=lambda args: {}, project_data=dict, handler=lambda args: time.sleep(0.4) or {"status": "ok"},
        timeout_s=0.05,
    ))

    async def slow_async(args):
        await asyncio.sleep(0.4)
        return {"status": "ok"}

    registry.register(AppOperation(
        app="notas", action="lento-async", label="tardar", capability="apps.actions", effect="local",
        validate_args=lambda args: {}, project_data=dict, handler=slow_async, timeout_s=0.05,
    ))
    bridge, _, _ = _bridge(tmp_path, mode="allow", registry=registry)

    async def run(action: str):
        ticks: list[float] = []
        start = time.monotonic()

        async def ticker():
            for _ in range(5):
                await asyncio.sleep(0.01)
                ticks.append(time.monotonic() - start)

        outcome, _ = await asyncio.gather(bridge.execute({"app": "notas", "action": action, "args": {}}), ticker())
        return outcome, ticks

    for action in ("lento", "lento-async"):
        outcome, ticks = asyncio.run(run(action))
        assert outcome["status"] == "unavailable" and "Pudo haberse hecho" in outcome["message"], action
        assert ticks[0] < 0.2, f"{action}: Core kept running while the handler worked"


def test_one_deadline_covers_a_plain_function_that_returns_an_awaitable(tmp_path: Path) -> None:
    """Codex R10: 600 ms in the function and 600 ms in what it returns, with a 900 ms limit, is
    past the limit: unavailable and honest, never ok. Within the limit it still works."""
    import time

    calls: list = []

    def two_phases(pause: float):
        async def rest():
            await asyncio.sleep(pause)
            return {"status": "ok"}

        def handler(args):
            calls.append(pause)
            time.sleep(pause)
            return rest()
        return handler

    registry = AppActionRegistry()
    for action, pause in (("mixta", 0.6), ("mixta-breve", 0.05)):
        registry.register(AppOperation(
            app="notas", action=action, label="tardar", capability="apps.actions", effect="local",
            validate_args=lambda args: {}, project_data=dict, handler=two_phases(pause), timeout_s=0.9,
        ))
    bridge, _, _ = _bridge(tmp_path, mode="allow", registry=registry)

    async def run(action: str) -> tuple[dict[str, Any], float]:
        start = time.monotonic()
        outcome = await bridge.execute({"app": "notas", "action": action, "args": {}})
        return outcome, time.monotonic() - start

    outcome, elapsed = asyncio.run(run("mixta"))
    assert outcome["status"] == "unavailable" and "Pudo haberse hecho" in outcome["message"]
    assert elapsed < 1.1, f"one 0.9 s budget, not two: {elapsed:.2f} s"
    outcome, _ = asyncio.run(run("mixta-breve"))
    assert outcome["status"] == "ok"
    assert calls == [0.6, 0.05], "each ran once"


def test_playing_a_track_names_it_by_title_never_by_path(tmp_path: Path) -> None:
    """Codex R07 (P3): two different tracks never produce the same confirmation."""
    from core.iris_player import IrisPlayerLibrary

    music = tmp_path / "música" / "privado"
    music.mkdir(parents=True)
    (music / "Blue_in_Green.mp3").write_bytes(b"\0" * 16)
    library = IrisPlayerLibrary(tmp_path / "home")
    library.configure_library(tmp_path / "música")
    library.refresh()
    known = library.list_tracks()[0]["id"]
    other = "trk_" + "b" * 32

    owner = _Surface(reply=lambda intent: _ok(intent, playback_state="playing"))
    registry = AppActionRegistry()
    register_player_actions(registry, track_label=library.track_label)
    bridge, _, _ = _bridge(tmp_path, _registry_with_surface("controls_local_player", owner), mode="allow", registry=registry)
    owner.bridge = bridge
    for track_id in (known, other):
        asyncio.run(bridge.execute({"app": "player", "action": "play", "args": {"track_id": track_id}}))
    summaries = [sent["payload"]["operation"]["summary"] for sent in owner.sent]
    assert summaries == ["Pista: «Blue in Green»", f"Pista {other}"]
    assert not any("privado" in summary or ".mp3" in summary for summary in summaries), "never the file's path"

    import core.websocket_server as server  # Core's own registry reads the person's library

    with patch.object(server, "_player_library", lambda: library):
        assert server._app_action_registry.get("player", "play").summary({"track_id": known}) == "Pista: «Blue in Green»"
