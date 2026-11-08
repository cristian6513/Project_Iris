"""F01: the bridge's session-API transport, replayed from responses recorded from the pinned Hermes.

The fixtures under tests/fixtures/hermes_session_stream are real Hermes 0.21.4
output (see its README). The transport is a real httpx client over a mock
transport, so SSE framing, status codes and bodies go through the same code
as in production.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from core import hermes_bridge as bridge_module
from core.config import IRISConfig
from core.hermes_bridge import HermesBridge
from core.model_selection import ConversationModelStore, ModelSelectionService

FIXTURES = Path(__file__).parent / "fixtures" / "hermes_session_stream"
RECORDED_ID = b'"IRIS-fixture"'


def recorded(name: str, *, session_id: str, rotate_to: str = "") -> bytes:
    raw = (FIXTURES / name).read_bytes().replace(RECORDED_ID, json.dumps(session_id).encode())
    if rotate_to:
        # Compression rotation: the terminal events name the child session.
        head, sep, tail = raw.partition(b"event: assistant.completed")
        raw = head + sep + tail.replace(json.dumps(session_id).encode(), json.dumps(rotate_to).encode())
    return raw


class FakeHermes:
    """Routes for the session API; records every request."""

    def __init__(self, stream: str | None = "locked_completed.sse", *, create_status: int = 201,
                 stream_status: int = 200, stream_error: dict | None = None, rotate_to: str = "",
                 raw_stream: bytes | None = None) -> None:
        self.stream, self.create_status, self.stream_status = stream, create_status, stream_status
        self.stream_error, self.rotate_to, self.raw_stream = stream_error, rotate_to, raw_stream
        self.requests: list[tuple[str, str, dict]] = []
        self.auth: list[tuple[str, str]] = []  # (path, Authorization) of every request
        self.on_stream = None
        self.on_path: dict[str, object] = {}  # path suffix -> runs once while that request is in flight
        self.pending_stream = None  # an async byte iterator that waits (a turn still running)
        self.gateway_default = {"provider": "fake-b", "model": "beta-1"}
        self.profile_defaults: dict[str, dict] = {}  # route prefix ("" or "/p/<name>") -> its default
        self.held: dict[str, asyncio.Event] = {}  # path suffix -> set when that request stalls, never answered

    def bodies(self, suffix: str) -> list[dict]:
        return [body for method, path, body in self.requests if path.endswith(suffix)]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}") if request.content else {}
        self.requests.append((request.method, request.url.path, body))
        self.auth.append((request.url.path, request.headers.get("authorization", "")))
        path = request.url.path
        for suffix in [suffix for suffix in self.on_path if path.endswith(suffix)]:
            self.on_path.pop(suffix)()
        for suffix in [suffix for suffix in self.held if path.endswith(suffix)]:
            reached = self.held.pop(suffix)

            async def stall():
                reached.set()
                await asyncio.sleep(3600)

            return stall()
        if path.endswith("/api/sessions"):
            return httpx.Response(self.create_status, json={"session": {"id": body.get("id")}}
                                  if self.create_status < 400 else {"error": {"message": "db down"}})
        if path.endswith("/chat/stream"):
            if self.on_stream:
                self.on_stream()
            if self.stream_status != 200:
                return httpx.Response(self.stream_status, json={"error": self.stream_error or {}})
            if self.pending_stream is not None:
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=self.pending_stream)
            session_id = path.split("/api/sessions/")[1].split("/")[0]
            content = self.raw_stream if self.raw_stream is not None else recorded(
                self.stream, session_id=session_id, rotate_to=self.rotate_to)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content)
        if path.startswith("/v1/runs/") and path.endswith("/stop"):
            return httpx.Response(200, json={"status": "stopping"})
        if path == "/health/detailed":
            return httpx.Response(200, json={"status": "ok", "pid": 4242})
        if path.endswith("/api/model/options"):
            default = self.profile_defaults.get(path[: -len("/api/model/options")], self.gateway_default)
            return httpx.Response(200, json={**default, "providers": [
                {"slug": default["provider"], "name": default["provider"], "models": [default["model"]],
                 "is_current": True, "authenticated": True}]})
        if path == "/v1/chat/completions":
            return httpx.Response(200, json={"id": "chatcmpl-x", "choices": [{"message": {"content": "legacy"}}]})
        return httpx.Response(404, json={"error": {"message": "no route"}})


def make_bridge(tmp_path, hermes: FakeHermes, *, default=("fake-a", "alpha-1"), session_runtime=True, local=True):
    bridge = HermesBridge(IRISConfig())
    # Locality is checked once and cached; tests state it (see the R6 tests for the check).
    bridge._gateway_local = (time.monotonic(), local)
    bridge._client = httpx.AsyncClient(base_url="http://hermes.test", transport=httpx.MockTransport(hermes))
    bridge._is_available = True
    bridge._supports_session_runtime = session_runtime
    bridge._supports_model_options = True
    bridge.model_selection = ModelSelectionService(
        ConversationModelStore(tmp_path / "models.json"),
        default_reader=lambda profile: {"provider": default[0], "model": default[1]},
    )
    return bridge


def run(bridge, text="hola", **kwargs):
    async def go():
        try:
            return await bridge.send_message(text, **kwargs)
        finally:
            await bridge._client.aclose()
    return asyncio.run(go())


def run_many(bridge, count):
    async def go():
        try:
            return [await bridge.send_message(f"hola {index}") for index in range(count)]
        finally:
            await bridge._client.aclose()
    return asyncio.run(go())


def test_custom_selection_rides_the_turn_and_is_confirmed(tmp_path):
    hermes = FakeHermes("locked_completed.sse")
    bridge = make_bridge(tmp_path, hermes)
    bridge.model_selection.store.set_selection(None, bridge.session_id, "fake-b", "beta-1")

    reply = run(bridge, reasoning_effort="low", progress_callback=lambda event: None)

    body = hermes.bodies("/chat/stream")[0]
    assert body["message"] == "hola" and body["system_message"].startswith("Eres IRIS")
    assert (body["provider"], body["model"], body["require_model_lock"]) == ("fake-b", "beta-1", True)
    assert body["model_options"]["reasoning"] == {"enabled": True, "effort": "low"}
    assert "messages" not in body and "temperature" not in body
    assert reply["texto_hablado"].startswith("respuesta de fake-b/beta-1")
    turn = reply["_model_turn"]
    assert turn["confirmed"] is True and turn["mode"] == "custom" and turn["status"] == "completed"
    assert turn["effective"] == {"provider": "custom", "model": "beta-1", "route_source": "raw_request"}
    assert bridge._current_run_id and bridge._current_run_id.startswith("run_")
    stored = ConversationModelStore(tmp_path / "models.json").entry(None, bridge.session_id)
    assert stored["last"]["confirmed"] is True


def test_session_turn_keeps_failed_write_footer_out_of_spoken_reply(tmp_path):
    answer = json.dumps({"texto_hablado": "Desarrollo mi caballo a f6. ¡Te toca!",
                         "emocion": "feliz", "animacion_virtual": "nod"}, ensure_ascii=False)
    answer += (
        "\n\n⚠️ File-mutation verifier: 1 file edit(s) FAILED this turn despite any wording "
        "above that may suggest otherwise.\n  • chess-move.json — [write_file] Refusing to overwrite."
    )
    raw = recorded("locked_completed.sse", session_id="IRIS-fixture").replace(
        b'respuesta de fake-b/beta-1 [[T-1]]', json.dumps(answer, ensure_ascii=False)[1:-1].encode())
    hermes = FakeHermes(raw_stream=raw)
    bridge = make_bridge(tmp_path, hermes)
    hermes.raw_stream = raw.replace(b'IRIS-fixture', bridge.session_id.encode())
    bridge.model_selection.store.set_selection(None, bridge.session_id, "fake-b", "beta-1")
    reply = run(bridge)
    assert "guardar un archivo" in reply["texto_hablado"]
    assert "¡Te toca!" not in reply["texto_hablado"]
    assert "texto_hablado" not in reply["texto_hablado"]
    assert reply["_file_mutation_error"]["failed_count"] == 1
    assert reply["_model_turn"]["confirmed"] is True, (
        "the provider/model lock remains independent of a tool's failure"
    )


def test_default_conversation_locks_only_the_default_model(tmp_path):
    hermes = FakeHermes("default_locked_completed.sse")
    reply = run(make_bridge(tmp_path, hermes))
    body = hermes.bodies("/chat/stream")[0]
    assert body["model"] == "alpha-1" and body["require_model_lock"] is True and "provider" not in body
    assert reply["_model_turn"]["mode"] == "default" and reply["_model_turn"]["confirmed"] is True


def test_session_row_is_created_once_and_409_counts_as_existing(tmp_path):
    hermes = FakeHermes("default_locked_completed.sse", create_status=409)
    replies = run_many(make_bridge(tmp_path, hermes), 2)
    assert len(hermes.bodies("/api/sessions")) == 1
    assert hermes.bodies("/api/sessions")[0]["source"] == "api_server"
    assert all(reply["_model_turn"]["confirmed"] for reply in replies)


def test_refused_model_is_a_failed_unconfirmed_turn_never_spoken_raw(tmp_path):
    hermes = FakeHermes("refused_model_failed.sse")
    bridge = make_bridge(tmp_path, hermes)
    bridge.model_selection.store.set_selection(None, bridge.session_id, "fake-a", "alpha-9")
    reply = run(bridge)
    turn = reply["_model_turn"]
    assert turn["status"] == "failed" and turn["confirmed"] is False
    assert "alpha-9" in turn["error"]
    assert "alpha-9" not in reply["texto_hablado"] and "panel" in reply["texto_hablado"]


def test_unresolvable_provider_is_failed_although_hermes_says_completed(tmp_path):
    hermes = FakeHermes("unknown_provider_accepted.sse")
    bridge = make_bridge(tmp_path, hermes)
    bridge.model_selection.store.set_selection(None, bridge.session_id, "fake-z", "zeta")
    reply = run(bridge)
    turn = reply["_model_turn"]
    assert turn["status"] == "failed" and turn["confirmed"] is False
    assert "fake-z" in turn["error"]
    assert "Provider authentication failed" not in reply["texto_hablado"]


def test_stopped_turn_is_cancelled(tmp_path):
    reply = run(make_bridge(tmp_path, FakeHermes("stopped_cancelled.sse")))
    assert reply["_model_turn"]["status"] == "cancelled" and reply["_model_turn"]["confirmed"] is False
    assert reply["texto_hablado"]


def test_stream_without_terminal_event_is_failed(tmp_path):
    raw = recorded("locked_completed.sse", session_id="IRIS-iris").split(b"event: run.completed")[0]
    reply = run(make_bridge(tmp_path, FakeHermes(raw_stream=raw.split(b"event: assistant.completed")[0])))
    assert reply["_model_turn"]["status"] == "failed"
    assert reply["_model_turn"]["confirmed"] is False


def test_error_event_is_failed_with_its_message(tmp_path):
    raw = b'event: run.started\ndata: {"run_id": "run_1"}\n\nevent: error\ndata: {"message": "boom"}\n\nevent: done\ndata: {}\n\n'
    reply = run(make_bridge(tmp_path, FakeHermes(raw_stream=raw)))
    assert reply["_model_turn"]["status"] == "failed" and reply["_model_turn"]["error"] == "boom"


def test_lock_refusal_keeps_hermes_available_and_explains(tmp_path):
    error = json.loads((FIXTURES / "lock_without_model.json").read_text("utf-8"))["body"]["error"]
    hermes = FakeHermes(stream_status=400, stream_error=error)
    bridge = make_bridge(tmp_path, hermes)
    reply = run(bridge)
    assert bridge._is_available is True
    assert reply["_model_turn"]["status"] == "failed"
    assert reply["_provider_error"]["kind"] == "model_selection"


def test_missing_session_is_forgotten_so_the_next_turn_recreates_it(tmp_path):
    hermes = FakeHermes(stream_status=404, stream_error={"code": "session_not_found", "message": "gone"})
    bridge = make_bridge(tmp_path, hermes)

    async def go():
        try:
            first = await bridge.send_message("uno")
            hermes.stream_status = 200
            hermes.stream = "default_locked_completed.sse"
            second = await bridge.send_message("dos")
            return first, second
        finally:
            await bridge._client.aclose()

    first, second = asyncio.run(go())
    assert first["_model_turn"]["status"] == "failed"
    assert second["_model_turn"]["confirmed"] is True
    assert len(hermes.bodies("/api/sessions")) == 2


def test_server_error_marks_hermes_unavailable(tmp_path):
    bridge = make_bridge(tmp_path, FakeHermes(stream_status=500, stream_error={"message": "kaboom"}))
    reply = run(bridge)
    assert bridge._is_available is False
    assert reply["_model_turn"]["status"] == "failed"


def test_session_creation_failure_sends_no_turn(tmp_path):
    hermes = FakeHermes(create_status=500)
    reply = run(make_bridge(tmp_path, hermes))
    assert hermes.bodies("/chat/stream") == []
    assert reply["_model_turn"]["status"] == "failed"


def test_rotation_adopts_the_new_session_and_carries_the_selection(tmp_path):
    hermes = FakeHermes("locked_completed.sse", rotate_to="IRIS-iris-child")
    bridge = make_bridge(tmp_path, hermes)
    store = bridge.model_selection.store
    store.set_selection(None, "IRIS-iris", "fake-b", "beta-1")

    async def go():
        try:
            first = await bridge.send_message("uno")
            hermes.rotate_to = ""
            await bridge.send_message("dos")
            return first
        finally:
            await bridge._client.aclose()

    first = asyncio.run(go())
    assert bridge.session_id == "IRIS-iris-child"
    assert first["_model_turn"]["session_id"] == "IRIS-iris-child"
    assert store.selection(None, "IRIS-iris-child") == {"provider": "fake-b", "model": "beta-1"}
    paths = [path for _method, path, _body in hermes.requests if path.endswith("/chat/stream")]
    assert paths == ["/api/sessions/IRIS-iris/chat/stream", "/api/sessions/IRIS-iris-child/chat/stream"]
    assert hermes.bodies("/chat/stream")[1]["provider"] == "fake-b"


def test_selection_change_during_a_turn_applies_to_the_next(tmp_path):
    hermes = FakeHermes("locked_completed.sse")
    bridge = make_bridge(tmp_path, hermes)
    store = bridge.model_selection.store
    store.set_selection(None, bridge.session_id, "fake-b", "beta-1")
    hermes.on_stream = lambda: store.set_selection(None, bridge.session_id, "fake-a", "alpha-2")

    replies = run_many(bridge, 2)
    first, second = hermes.bodies("/chat/stream")
    assert (first["provider"], first["model"]) == ("fake-b", "beta-1")
    assert replies[0]["_model_turn"]["model"] == "beta-1"
    assert (second["provider"], second["model"]) == ("fake-a", "alpha-2")


def test_tool_events_reach_the_avatar_without_arguments(tmp_path):
    raw = (
        b'event: run.started\ndata: {"run_id": "run_1"}\n\n'
        b'event: tool.started\ndata: {"tool_name": "web_search", "preview": "clima", "args": {"q": "secret"}}\n\n'
        b'event: tool.progress\ndata: {"tool_name": "_thinking", "delta": "..."}\n\n'
        b'event: tool.completed\ndata: {"tool_name": "web_search"}\n\n'
        + recorded("default_locked_completed.sse", session_id="IRIS-iris").split(b"event: run.started", 1)[1].partition(b"\n\n")[2]
    )
    events: list[dict] = []
    run(make_bridge(tmp_path, FakeHermes(raw_stream=raw)), progress_callback=events.append)
    assert events == [
        {"event": "tool.started", "tool": "web_search", "status": "running", "preview": "clima"},
        {"event": "tool.completed", "tool": "web_search", "status": "completed", "preview": ""},
    ]


def test_images_travel_as_the_multimodal_message(tmp_path):
    hermes = FakeHermes("default_locked_completed.sse")
    run(make_bridge(tmp_path, hermes), images=["data:image/png;base64,AAAA"])
    message = hermes.bodies("/chat/stream")[0]["message"]
    assert message == [{"type": "text", "text": "hola"},
                       {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]


def test_older_hermes_keeps_chat_completions_without_model_turn(tmp_path):
    hermes = FakeHermes()
    reply = run(make_bridge(tmp_path, hermes, session_runtime=False))
    assert [path for _m, path, _b in hermes.requests] == ["/v1/chat/completions"]
    assert reply["texto_hablado"] == "legacy" and "_model_turn" not in reply


@pytest.mark.parametrize(
    ("features", "expected"),
    [
        ({"session_chat_streaming": True, "session_model_lock": True, "model_options": True}, True),
        ({"session_chat_streaming": True, "model_options": True}, False),
        ({"session_model_lock": True}, False),
        ({}, False),
    ],
)
def test_capability_probe_gates_the_session_transport(features, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"features": features})
        return httpx.Response(200, json={"status": "ok", "data": []})

    bridge = HermesBridge(IRISConfig())
    bridge._client = httpx.AsyncClient(base_url="http://hermes.test", transport=httpx.MockTransport(handler))
    asyncio.run(bridge._check_availability())
    assert bridge._supports_session_runtime is expected
    assert bridge.supports_model_selection is expected


# ── Codex review 2026-10-08: turn context (R1), local cancellation (R4), remote Gateway (R6)


def test_a_reply_after_wake_switched_conversation_does_not_bring_the_old_one_back(tmp_path):
    hermes = FakeHermes("locked_completed.sse")
    bridge = make_bridge(tmp_path, hermes)
    store = bridge.model_selection.store
    store.set_selection(None, "IRIS-iris", "fake-b", "beta-1")
    switched = {}
    hermes.on_stream = lambda: switched.update(bridge.start_new_session())

    async def go():
        try:
            first = await bridge.send_message("uno")
            stop_target = bridge._current_run_id
            hermes.on_stream = None
            hermes.stream = "default_locked_completed.sse"
            await bridge.send_message("dos")
            return first, stop_target
        finally:
            await bridge._client.aclose()

    first, stop_target = asyncio.run(go())
    # Detener in the new conversation must not target the old run.
    assert stop_target is None
    new_id = switched["session_id"]
    assert bridge.session_id == new_id
    # The old turn is filed under its own conversation, not the new one.
    assert first["_model_turn"]["session_id"] == "IRIS-iris"
    assert store.entry(None, "IRIS-iris")["last"]["confirmed"] is True
    paths = [path for _m, path, _b in hermes.requests if path.endswith("/chat/stream")]
    assert paths == ["/api/sessions/IRIS-iris/chat/stream", f"/api/sessions/{new_id}/chat/stream"]
    # The new conversation follows the default, not the old one's selection.
    assert "provider" not in hermes.bodies("/chat/stream")[1]


def test_a_rotation_reported_after_a_switch_moves_the_selection_but_not_the_pointer(tmp_path):
    hermes = FakeHermes("locked_completed.sse", rotate_to="IRIS-iris-child")
    bridge = make_bridge(tmp_path, hermes)
    store = bridge.model_selection.store
    store.set_selection(None, "IRIS-iris", "fake-b", "beta-1")
    switched = {}
    hermes.on_stream = lambda: switched.update(bridge.start_new_session())

    reply = run(bridge)
    assert bridge.session_id == switched["session_id"]
    assert reply["_model_turn"]["session_id"] == "IRIS-iris-child"
    assert store.selection(None, "IRIS-iris-child") == {"provider": "fake-b", "model": "beta-1"}


def test_a_reply_after_a_continuity_resume_keeps_the_resumed_conversation(tmp_path):
    hermes = FakeHermes("locked_completed.sse")
    bridge = make_bridge(tmp_path, hermes)
    hermes.on_stream = lambda: bridge.resume_session("IRIS-older")
    run(bridge)
    assert bridge.session_id == "IRIS-older"


class _PendingStream:
    """SSE that sends run.started and then waits like a turn still running."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def __aiter__(self):
        yield b'event: run.started\ndata: {"run_id": "run_pending", "runtime": {"model_lock": "accepted"}}\n\n'
        self.started.set()
        await asyncio.sleep(3600)


def test_local_cancellation_files_the_turn_as_cancelled_and_publishes_it(tmp_path):
    hermes = FakeHermes("locked_completed.sse")
    bridge = make_bridge(tmp_path, hermes)
    store = bridge.model_selection.store
    store.set_selection(None, "IRIS-iris", "fake-b", "beta-1")
    published: list[dict] = []
    bridge.on_model_turn = published.append

    async def go():
        try:
            await bridge.send_message("uno")  # a confirmed answer first
            pending = _PendingStream()
            hermes.pending_stream = pending
            task = asyncio.create_task(bridge.send_message("dos"))
            await asyncio.wait_for(pending.started.wait(), 5)
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await bridge._client.aclose()

    asyncio.run(go())
    assert [turn["status"] for turn in published] == ["completed", "cancelled"]
    last = store.entry(None, "IRIS-iris")["last"]
    assert last["status"] == "cancelled" and last["confirmed"] is False
    assert bridge.last_model_turn["status"] == "cancelled"
    assert bridge._current_run_id == "run_pending"


def test_a_remote_gateway_default_comes_from_that_gateway(tmp_path):
    hermes = FakeHermes("locked_completed.sse")
    bridge = make_bridge(tmp_path, hermes, local=False)
    reply = run(bridge)
    body = hermes.bodies("/chat/stream")[0]
    assert body["model"] == "beta-1" and "provider" not in body
    assert reply["_model_turn"]["model"] == "beta-1" and reply["_model_turn"]["provider"] == "fake-b"


def _gateway_state(home, pid, recorded_home):
    home.mkdir(parents=True, exist_ok=True)
    (home / "gateway_state.json").write_text(json.dumps({"pid": pid, "hermes_home": str(recorded_home)}), "utf-8")


@pytest.mark.parametrize(
    ("state_pid", "recorded", "reported", "expected"),
    [
        (4242, "same", 4242, True),
        (4242, "same", 4243, False),
        (4242, "other", 4242, False),
        (4242, "same", "4242", False),
        (4242, "same", True, False),
    ],
)
def test_local_gateway_is_accredited_by_pid_and_home(tmp_path, monkeypatch, state_pid, recorded, reported, expected):
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _gateway_state(home, state_pid, home if recorded == "same" else tmp_path / "elsewhere")
    assert bridge_module._local_gateway_matches(reported) is expected


def test_locality_check_asks_the_gateway_for_its_pid(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _gateway_state(home, 4242, home)
    hermes = FakeHermes()
    bridge = make_bridge(tmp_path, hermes)
    bridge._gateway_local = None

    async def go():
        try:
            return await bridge.gateway_is_local()
        finally:
            await bridge._client.aclose()

    assert asyncio.run(go()) is True
    assert [path for _m, path, _b in hermes.requests] == ["/health/detailed"]


def test_a_non_loopback_gateway_is_never_local(tmp_path):
    hermes = FakeHermes()
    bridge = make_bridge(tmp_path, hermes)
    bridge._gateway_local = None
    bridge.base_url = "http://192.168.1.50:8642"

    async def go():
        try:
            return await bridge.gateway_is_local()
        finally:
            await bridge._client.aclose()

    assert asyncio.run(go()) is False
    assert hermes.requests == []


def test_agent_interrupt_publishes_the_cancelled_turn_and_stops_the_run(tmp_path, monkeypatch):
    """The real ``Agent.interrupt`` order: cancel the turn task, then stop the Hermes run."""
    from unittest.mock import AsyncMock

    from core import agent as agent_module

    broadcasts: list[tuple[str, dict]] = []

    async def fake_broadcast(event, payload):
        broadcasts.append((event, payload))

    monkeypatch.setattr(agent_module, "broadcast_to_face", fake_broadcast)
    monkeypatch.setattr(agent_module, "send_speech_end", AsyncMock())
    monkeypatch.setattr(agent_module, "send_status", AsyncMock())
    hermes = FakeHermes("locked_completed.sse")
    bridge = make_bridge(tmp_path, hermes)
    bridge.model_selection.store.set_selection(None, "IRIS-iris", "fake-b", "beta-1")
    agent = agent_module.IRISAgent.__new__(agent_module.IRISAgent)
    agent.bridge, agent.tts, agent.memory = bridge, AsyncMock(), AsyncMock()
    agent._session_id = "IRIS-iris"
    bridge.on_model_turn = agent._publish_model_turn

    async def go():
        agent._turn_lock = asyncio.Lock()
        try:
            await bridge.send_message("uno")
            pending = _PendingStream()
            hermes.pending_stream = pending
            agent._current_turn_task = asyncio.create_task(bridge.send_message("dos"))
            await asyncio.wait_for(pending.started.wait(), 5)
            await asyncio.sleep(0.05)
            await agent.interrupt(reason="test")
            await asyncio.sleep(0.05)  # let the scheduled broadcasts run
            assert agent._current_turn_task.cancelled()
        finally:
            await bridge._client.aclose()

    asyncio.run(go())
    statuses = [payload["status"] for event, payload in broadcasts if event == "model_turn"]
    assert statuses == ["completed", "cancelled"]
    assert ("POST", "/v1/runs/run_pending/stop") in [(m, path) for m, path, _b in hermes.requests]
    assert bridge.model_selection.store.entry(None, "IRIS-iris")["last"]["status"] == "cancelled"


def test_returning_to_the_same_conversation_still_follows_its_compression(tmp_path):
    hermes = FakeHermes("locked_completed.sse", rotate_to="IRIS-iris-child")
    bridge = make_bridge(tmp_path, hermes)

    def away_and_back():
        bridge.resume_session("IRIS-older")
        bridge.resume_session("IRIS-iris")

    hermes.on_stream = away_and_back
    run(bridge)
    assert bridge.session_id == "IRIS-iris-child"


def test_the_context_token_changes_on_switches_and_not_on_compression(tmp_path):
    bridge = make_bridge(tmp_path, FakeHermes())
    first = bridge.conversation_context["token"]
    bridge._adopt_session_id("IRIS-iris-child", turn=bridge.route_context())
    assert bridge.session_id == "IRIS-iris-child"
    assert bridge.conversation_context["token"] == first
    bridge.start_new_session()
    second = bridge.conversation_context["token"]
    assert second != first
    bridge.resume_session("IRIS-iris")
    assert bridge.conversation_context["token"] not in (first, second)
    asyncio.run(bridge._client.aclose())


# ── Codex recheck 2026-10-08: profile captured through default reads (C1), Stop while preparing (C2)


def _named_profiles(monkeypatch):
    """Every named profile routes to /p/<name> with its own credential."""
    monkeypatch.setattr(bridge_module, "_named_profile_route", lambda name: (name or None, f"key-{name}", ""))


@pytest.mark.parametrize("during", ["/health/detailed", "/api/model/options"])
@pytest.mark.parametrize(("origin", "other"), [(None, "two"), ("one", None)])
def test_a_profile_switch_while_reading_the_default_keeps_the_turn_on_its_profile(
    tmp_path, monkeypatch, during, origin, other,
):
    _named_profiles(monkeypatch)
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _gateway_state(home, 1, home)  # a loopback Gateway of another Hermes home: not this machine's
    hermes = FakeHermes("default_locked_completed.sse")
    origin_prefix, other_prefix = (f"/p/{name}" if name else "" for name in (origin, other))
    hermes.profile_defaults = {origin_prefix: {"provider": "fake-a", "model": "alpha-1"},
                               other_prefix: {"provider": "fake-b", "model": "beta-1"}}
    bridge = make_bridge(tmp_path, hermes)
    bridge._gateway_local = None
    bridge._default_api_key = "key-base"
    if origin:
        bridge.start_new_session(origin)
    origin_session = bridge.session_id
    hermes.on_path[during] = lambda: bridge.start_new_session(other)

    reply = run(bridge)

    credential = f"Bearer key-{origin or 'base'}"
    assert [entry for entry in hermes.auth if entry[0].endswith("/api/model/options")] == [
        (f"{origin_prefix}/api/model/options", credential)]
    chat_path, chat_auth = next(entry for entry in hermes.auth if entry[0].endswith("/chat/stream"))
    assert chat_path == f"{origin_prefix}/api/sessions/{origin_session}/chat/stream" and chat_auth == credential
    assert hermes.bodies("/chat/stream")[0]["model"] == "alpha-1"
    turn = reply["_model_turn"]
    assert (turn["profile"], turn["model"], turn["confirmed"]) == (origin or "default", "alpha-1", True)
    assert bridge._route_defaults[origin or "default"][1] == {"provider": "fake-a", "model": "alpha-1"}
    assert (other or "default") not in bridge._route_defaults
    assert bridge.active_profile == other


def _interruptible_agent(bridge, monkeypatch):
    """A real ``IRISAgent.interrupt`` around ``bridge``; returns it and the face broadcasts."""
    from unittest.mock import AsyncMock

    from core import agent as agent_module

    broadcasts: list[tuple[str, dict]] = []

    async def fake_broadcast(event, payload):
        broadcasts.append((event, payload))

    monkeypatch.setattr(agent_module, "broadcast_to_face", fake_broadcast)
    monkeypatch.setattr(agent_module, "send_speech_end", AsyncMock())
    monkeypatch.setattr(agent_module, "send_status", AsyncMock())
    agent = agent_module.IRISAgent.__new__(agent_module.IRISAgent)
    agent.bridge, agent.tts, agent.memory = bridge, AsyncMock(), AsyncMock()
    agent._session_id = bridge.session_id
    bridge.on_model_turn = agent._publish_model_turn
    return agent, broadcasts


@pytest.mark.parametrize(("stage", "local"), [
    ("/health/detailed", False), ("/api/model/options", False), ("turn_plan", False), ("turn_plan", True)])
@pytest.mark.parametrize("mode", ["custom", "default"])
def test_stop_while_the_turn_is_prepared_files_the_attempt_as_cancelled(tmp_path, monkeypatch, stage, local, mode):
    import threading

    from core.model_selection_api import conversation_state

    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _gateway_state(home, 1, home)
    hermes = FakeHermes("locked_completed.sse" if mode == "custom" else "default_locked_completed.sse")
    hermes.gateway_default = {"provider": "fake-a", "model": "alpha-1"}
    bridge = make_bridge(tmp_path, hermes, local=local)
    service = bridge.model_selection
    if mode == "custom":
        service.store.set_selection(None, "IRIS-iris", "fake-b", "beta-1")
    agent, broadcasts = _interruptible_agent(bridge, monkeypatch)
    planned, release = threading.Event(), threading.Event()
    original_plan = service.turn_plan

    def slow_plan(*args, **kwargs):
        if not planned.is_set():
            planned.set()
            release.wait(5)
        return original_plan(*args, **kwargs)

    async def go():
        agent._turn_lock = asyncio.Lock()
        try:
            await bridge.send_message("uno")  # a confirmed answer first
            assert bridge.last_model_turn["confirmed"] is True
            reached = asyncio.Event()
            if stage == "/health/detailed":
                bridge._gateway_local = None
                hermes.held[stage] = reached
            elif stage == "/api/model/options":
                bridge._route_defaults.clear()
                hermes.held[stage] = reached
            else:
                service.turn_plan = slow_plan
            agent._current_turn_task = asyncio.create_task(bridge.send_message("dos"))
            if stage == "turn_plan":
                await asyncio.to_thread(planned.wait, 5)
            else:
                await asyncio.wait_for(reached.wait(), 5)
            await agent.interrupt(reason="test")
            await asyncio.sleep(0.05)  # let the scheduled broadcasts run
            assert agent._current_turn_task.cancelled()
        finally:
            release.set()
            await bridge._client.aclose()

    asyncio.run(go())
    statuses = [payload["status"] for event, payload in broadcasts if event == "model_turn"]
    assert statuses == ["completed", "cancelled"]
    last = service.store.entry(None, "IRIS-iris")["last"]
    assert last["status"] == "cancelled" and last["confirmed"] is False
    # Nothing reached Hermes for the second turn, and Stop targeted no run.
    assert len(hermes.bodies("/chat/stream")) == 1
    assert not [path for _m, path, _b in hermes.requests if path.endswith("/stop")]
    plan = original_plan(None, "IRIS-iris", default={"provider": "fake-a", "model": "alpha-1"})
    expected = "pending" if (mode, stage) == ("default", "/api/model/options") else "cancelled"
    assert conversation_state(service.store.entry(None, "IRIS-iris"), plan) == expected
