"""Actual Core WS functions, isolated from module startup and all personal profiles.

Only AST compilation selects the original functions; their bodies are not rewritten.
The store, calendar projection, reminder runtime and surface registry remain real.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core.calendar_runtime import CalendarRuntime
from core.calendar_store import CalendarStoreError
from core.request_security import is_loopback_host
from core.surface_registry import SurfaceRegistry
from test_calendar_models import timed
from test_calendar_service import Cache

NOW = datetime(2026, 10, 16, 15, tzinfo=timezone.utc).timestamp()
SOURCE = Path(__file__).resolve().parents[1] / "core" / "websocket_server.py"


class Socket:
    def __init__(self, host="127.0.0.1", *, fail=False):
        self.client = SimpleNamespace(host=host)
        self.sent = []
        self.attempts = []
        self.fail = fail

    async def send_json(self, message):
        self.attempts.append(message)
        if self.fail:
            raise ConnectionError("synthetic ambiguous send")
        self.sent.append(message)


@pytest.fixture
def world(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    clock, mono, busy = [NOW], [100.0], [False]
    cache = Cache()
    cache.value["calendars"][0]["events"] = [timed(start="2026-10-16T11:00:00-05:00",
        end="2026-10-16T12:00:00-05:00", summary="PRIVATE SYNTHETIC TITLE",
        description="PRIVATE SYNTHETIC DESCRIPTION", video="https://meet.google.com/qa-test")]
    calendar = CalendarRuntime(home, google=lambda: cache, clock=lambda: clock[0], monotonic=lambda: mono[0])
    calendar.set_preferences(revision=0, reminders_enabled=True, time_zone="America/Bogota")
    registry = SurfaceRegistry()
    namespace = {"asyncio": asyncio, "Any": Any, "WebSocket": object,
        "logger": logging.getLogger("calendar-ws-test"), "time": SimpleNamespace(time=lambda: clock[0]),
        "CalendarStoreError": CalendarStoreError, "is_loopback_host": is_loopback_host,
        "_calendar_runtime": calendar, "_mail_classification": None, "_automation_runtime": None, "_surface_registry": registry,
        "_vision_backend_busy": lambda: busy[0]}
    names = {"_tick_calendar", "_handle_face_message", "_is_local_client"}
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name in names]
    assert {node.name for node in functions} == names
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return SimpleNamespace(home=home, clock=clock, mono=mono, busy=busy, cache=cache,
                           calendar=calendar, registry=registry, ns=namespace)


def surface(world, name, *, host="127.0.0.1", role="face", visible=True, capable=True, fail=False):
    socket = Socket(host, fail=fail)
    record = world.registry.register_ready(socket, {"surface_id": name, "role": role, "visible": visible,
                                                    "capabilities": {"presents_inbox": capable}})
    return socket, record


async def tick(world):
    await world.ns["_tick_calendar"]()
    world.mono[0] += 16


async def acknowledge(world, socket, payload, *, shown=True, **changes):
    await world.ns["_handle_face_message"](socket, {"type": "calendar_reminder_receipt",
        "payload": {**payload, "shown": shown, **changes}})


def test_only_one_visible_local_face_receives_an_opaque_intent_without_content(world):
    remote, _ = surface(world, "lan", host="192.168.1.25")
    hidden, _ = surface(world, "hidden", visible=False)
    companion, _ = surface(world, "companion", role="desktop_companion")
    first, first_record = surface(world, "first-local")
    second, _ = surface(world, "second-local")
    asyncio.run(tick(world))
    assert not remote.sent and not hidden.sent and not companion.sent and not second.sent
    message, = first.sent
    assert message["type"] == "calendar_reminder"
    assert set(message["payload"]) == {"notice_id", "surface_id", "token"}
    payload = message["payload"]
    notice, = world.calendar.inbox_rows()
    assert payload == {"notice_id": notice.id, "surface_id": first_record.surface_id,
                       "token": notice.presentation_token}
    assert notice.presentation_uncertain and notice.presented_at is None and notice.read_at is None
    assert "PRIVATE SYNTHETIC" not in json.dumps(message)
    assert world.cache.syncs == []


def test_a_mail_pass_failure_does_not_stop_deterministic_calendar_reminders(world):
    calls=[]
    class Mail:
        async def tick(self):
            calls.append('tick')
            raise RuntimeError('synthetic mail store unavailable')
    world.ns['_mail_classification']=Mail()
    socket,_=surface(world,'local')
    asyncio.run(tick(world))
    assert calls==['tick'] and socket.sent[0]['type']=='calendar_reminder'


@pytest.mark.parametrize("kind", ["lan", "hidden", "companion", "incapable"])
def test_ineligible_surface_never_receives_or_claims_a_notice(world, kind):
    socket, _ = surface(world, kind, host="192.168.1.10" if kind == "lan" else "127.0.0.1",
        visible=kind != "hidden", role="desktop_companion" if kind == "companion" else "face",
        capable=kind != "incapable")
    asyncio.run(tick(world))
    assert socket.attempts == []
    notice, = world.calendar.inbox_rows()
    assert notice.presentation_attempted_at is None


def test_matching_socket_receipt_marks_actual_display_and_never_reading(world):
    socket, _ = surface(world, "local")

    async def run():
        await tick(world)
        payload = socket.sent[0]["payload"]
        world.clock[0] += 1
        await acknowledge(world, socket, payload)
        notice, = world.calendar.inbox_rows()
        assert socket.sent[-1] == {"type": "calendar_reminder_ack", "payload": {"accepted": True}}
        assert notice.presented_at == world.clock[0] and notice.read_at is None and notice.status == "presented"
        await acknowledge(world, socket, payload)
        assert not socket.sent[-1]["payload"]["accepted"]
        assert world.calendar.inbox_rows()[0] == notice

    asyncio.run(run())


def test_negative_receipt_preserves_uncertainty_and_does_not_cause_resend(world):
    socket, _ = surface(world, "local")

    async def run():
        await tick(world)
        payload = socket.sent[0]["payload"]
        await acknowledge(world, socket, payload, shown=False)
        notice, = world.calendar.inbox_rows()
        assert notice.presentation_uncertain and notice.presented_at is None and notice.read_at is None
        await tick(world)
        assert sum(message["type"] == "calendar_reminder" for message in socket.sent) == 1

    asyncio.run(run())


@pytest.mark.parametrize("forgery", ["wrong-token", "other-surface", "other-socket", "lan", "nonboolean"])
def test_forged_or_remote_receipt_does_not_change_the_notice(world, forgery):
    socket, _ = surface(world, "owner")

    async def run():
        await tick(world)
        payload = socket.sent[0]["payload"]
        attempted, = world.calendar.inbox_rows()
        sender = socket
        changes = {}
        if forgery == "wrong-token":
            changes["token"] = "a" * 32 if payload["token"] != "a" * 32 else "b" * 32
        elif forgery == "other-surface":
            changes["surface_id"] = "forged-surface"
        elif forgery == "other-socket":
            sender, record = surface(world, "second-local")
            changes["surface_id"] = record.surface_id
        elif forgery == "lan":
            sender, record = surface(world, "remote", host="192.168.1.25")
            changes["surface_id"] = record.surface_id
        elif forgery == "nonboolean":
            changes["shown"] = 1
        await acknowledge(world, sender, payload, **changes)
        assert world.calendar.inbox_rows() == [attempted]
        assert sender.sent[-1]["type"] == "protocol_error" or sender.sent[-1]["payload"] == {"accepted": False}
        assert payload["token"] not in json.dumps(sender.sent[-1])

    asyncio.run(run())


def test_ambiguous_socket_loss_and_restart_never_repeat_the_intent(world):
    socket, _ = surface(world, "local", fail=True)

    async def run():
        await tick(world)
        attempted, = world.calendar.inbox_rows()
        assert attempted.presentation_uncertain and len(socket.attempts) == 1
        socket.fail = False
        await tick(world)
        assert len(socket.attempts) == 1
        await world.calendar.close()
        restarted = CalendarRuntime(world.home, google=lambda: world.cache,
            clock=lambda: world.clock[0], monotonic=lambda: world.mono[0])
        world.ns["_calendar_runtime"] = restarted
        await tick(world)
        assert len(socket.attempts) == 1 and restarted.inbox_rows()[0].presentation_uncertain
        await restarted.close()

    asyncio.run(run())


@pytest.mark.parametrize("hold", ["busy", "quiet"])
def test_busy_or_quiet_defers_without_claiming_the_notice(world, hold):
    socket, _ = surface(world, "local")
    quiet = SimpleNamespace(quiet_until=NOW + 300)
    if hold == "busy":
        world.busy[0] = True
    else:
        world.ns["_automation_runtime"] = SimpleNamespace(store=lambda: SimpleNamespace(inbox_preferences=lambda: quiet))

    async def run():
        await tick(world)
        assert not socket.attempts and world.calendar.inbox_rows()[0].presentation_attempted_at is None
        world.busy[0] = False
        quiet.quiet_until = 0
        await tick(world)
        assert len(socket.sent) == 1

    asyncio.run(run())


@pytest.mark.parametrize("change", ["hide", "busy", "disconnect"])
def test_surface_and_busy_state_are_rechecked_after_the_durable_claim(world, monkeypatch, change):
    socket, record = surface(world, "local")
    original = world.calendar.claim_notice

    def transition(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == "hide":
            record.visible = False
        elif change == "busy":
            world.busy[0] = True
        else:
            world.registry.disconnect(socket)
        return result

    monkeypatch.setattr(world.calendar, "claim_notice", transition)
    asyncio.run(tick(world))
    assert socket.attempts == []
    assert world.calendar.inbox_rows()[0].presentation_uncertain
