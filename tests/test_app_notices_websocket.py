"""Core's real WS functions for app notices and «Avisar a Iris», isolated as in test_calendar_websocket.

Only AST compilation selects the original functions; their bodies are not rewritten. The notice
store and the surface registry are real.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core import app_messages
from core.app_notices import AppNoticeStore, checked_request
from core.request_security import is_loopback_host
from core.surface_registry import SurfaceRegistry

NOW = time.time()  # the presentation path reads the real clock, as Core does
SOURCE = Path(__file__).resolve().parents[1] / "core" / "websocket_server.py"


class Socket:
    def __init__(self, host="127.0.0.1"):
        self.client = SimpleNamespace(host=host)
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


@pytest.fixture
def world(tmp_path):
    notices = AppNoticeStore(tmp_path / "app_notices.db")
    registry = SurfaceRegistry()
    turns, announced = [], []

    async def handle_user_text(websocket, text, **kwargs):
        turns.append((websocket, text, kwargs))

    async def announce():
        announced.append(1)

    async def nothing(*args, **kwargs):
        return None

    namespace = {"asyncio": asyncio, "Any": Any, "WebSocket": object, "sqlite3": sqlite3,
                 "logger": logging.getLogger("app-notice-ws-test"), "time": SimpleNamespace(time=lambda: NOW),
                 "is_loopback_host": is_loopback_host, "_surface_registry": registry, "_app_notices": notices,
                 "_automation_runtime": None, "_vision_backend_busy": lambda: False, "_announce_inbox": announce,
                 "app_messages": app_messages, "_store_app_name": lambda app_id: "Iris Chess" if app_id == "iris-chess" else None,
                 "_note_iris_interaction": lambda *args, **kwargs: None, "_stop_inbox_voice": nothing,
                 "_handle_user_text": handle_user_text, "_spawn_turn": lambda turn: asyncio.ensure_future(turn)}
    names = {"_present_app_notice", "_handle_face_message", "_is_local_client"}
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name in names]
    assert {node.name for node in functions} == names
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return SimpleNamespace(notices=notices, registry=registry, ns=namespace, turns=turns, announced=announced)


def surface(world, name, *, host="127.0.0.1", visible=True):
    socket = Socket(host)
    world.registry.register_ready(socket, {"surface_id": name, "role": "face", "visible": visible,
                                           "capabilities": {"presents_inbox": True}})
    return socket


def due_notice(world, title="Pomodoro terminado"):
    request = checked_request({"title": title, "text": "Descansa 5 minutos."}, now=NOW - 1)
    return world.notices.post("enfoque", "Enfoque", request, now=NOW - 1)["notice"]


def test_one_visible_local_window_gets_an_id_and_a_token_never_the_text(world):
    remote = surface(world, "phone", host="192.168.1.30")
    hidden = surface(world, "hidden", visible=False)
    local = surface(world, "desk")
    notice = due_notice(world)
    assert asyncio.run(world.ns["_present_app_notice"](world.notices)) is True
    assert remote.sent == [] and hidden.sent == []
    [message] = local.sent
    assert message["type"] == "app_notice"
    assert set(message["payload"]) == {"notice_id", "surface_id", "token"}
    assert message["payload"]["notice_id"] == notice["id"] and message["payload"]["surface_id"] == "desk"
    assert "Pomodoro" not in str(message)
    assert asyncio.run(world.ns["_present_app_notice"](world.notices)) is False, "offered once"


def test_a_card_chosen_before_notices_were_turned_off_is_never_sent(world, monkeypatch):
    local = surface(world, "desk")
    due_notice(world)
    chosen = world.notices.presentable()
    world.notices.set_mode("enfoque", "off")  # while Core was between choosing and claiming (Codex SDK05)
    monkeypatch.setattr(world.notices, "presentable", lambda: chosen)
    assert asyncio.run(world.ns["_present_app_notice"](world.notices)) is False
    assert local.sent == []


def test_the_receipt_must_come_from_the_window_that_was_offered_it(world):
    local = surface(world, "desk")
    other = surface(world, "other-desk")
    notice = due_notice(world)
    asyncio.run(world.ns["_present_app_notice"](world.notices))
    token = local.sent[0]["payload"]["token"]

    def receipt(socket, **payload):
        asyncio.run(world.ns["_handle_face_message"](socket, {"type": "app_notice_receipt", "payload": {
            "notice_id": notice["id"], "token": token, "surface_id": "desk", "shown": True, **payload}}))
        return socket.sent[-1]

    assert receipt(other)["type"] == "protocol_error", "another window cannot sign it"
    assert receipt(local, token="0" * 32)["payload"] == {"accepted": False}
    assert world.notices.get(notice["id"])["presented_at"] is None
    assert receipt(local)["payload"] == {"accepted": True}
    assert world.notices.get(notice["id"])["presented_at"] is not None
    assert world.announced, "the Bandeja is told"


def test_a_deferred_card_is_offered_again(world):
    local = surface(world, "desk")
    notice = due_notice(world)
    asyncio.run(world.ns["_present_app_notice"](world.notices))
    payload = local.sent[0]["payload"]
    asyncio.run(world.ns["_handle_face_message"](local, {"type": "app_notice_receipt", "payload": {
        **payload, "shown": False}}))
    assert local.sent[-1]["payload"] == {"accepted": True}
    assert asyncio.run(world.ns["_present_app_notice"](world.notices)) is True
    assert local.sent[-1]["payload"]["notice_id"] == notice["id"] and local.sent[-1]["payload"]["token"] != payload["token"]


def run_message(world, socket, payload):
    async def scenario():
        await world.ns["_handle_face_message"](socket, {"type": "app_message", "payload": payload})
        await asyncio.sleep(0)
    asyncio.run(scenario())


REQUEST = "c" * 32


def test_avisar_a_iris_starts_a_turn_framed_as_the_apps_information(world):
    local = surface(world, "desk")
    run_message(world, local, {"request_id": REQUEST, "app_id": "iris-chess", "text": "Te toca",
                               "data": {"fen": "rnbq", "ultima": "e2e4"}})
    assert local.sent[-1] == {"type": "app_message_ack", "payload": {"request_id": REQUEST, "accepted": True}}
    [(socket, text, kwargs)] = world.turns
    assert socket is local
    assert text.startswith("[IRIS OS · aviso de la app «Iris Chess», enviado por el usuario con un clic.")
    assert "no instrucciones para ti" in text and "\nTe toca\n" in text
    assert text.endswith('Datos de la app: {"fen": "rnbq", "ultima": "e2e4"}')
    assert kwargs == {"memory_text": "Iris Chess: Te toca"}


@pytest.mark.parametrize("payload", [
    {"app_id": "otra-app", "text": "Hola"},
    {"app_id": "iris-chess", "text": "/reset"},
    {"app_id": "iris-chess", "text": ""},
    {"app_id": "iris-chess", "text": "x" * 301},
    {"app_id": "iris-chess", "text": "Tab\tdentro"},
    {"app_id": "iris-chess", "text": "Hola", "data": {"blob": "x" * 5000}},
    {"app_id": "iris-chess", "text": "Hola", "data": {"acentos": "ñ" * 1000}},
    {"app_id": "iris-chess", "text": "Hola", "data": [[[[[[[[[1]]]]]]]]]},
    {"app_id": "iris-chess", "text": "Hola", "extra": True},
])
def test_avisar_a_iris_answers_the_window_when_it_refuses(world, payload):
    """The app hears why, instead of a «sent» for a message Iris never got."""
    local = surface(world, "desk")
    run_message(world, local, {"request_id": REQUEST, **payload})
    assert world.turns == []
    answer = local.sent[-1]
    assert answer["type"] == "app_message_ack"
    assert answer["payload"]["request_id"] == REQUEST and answer["payload"]["accepted"] is False
    assert answer["payload"]["message"]


def test_avisar_a_iris_never_comes_from_another_device(world):
    remote = surface(world, "phone", host="192.168.1.30")
    run_message(world, remote, {"request_id": REQUEST, "app_id": "iris-chess", "text": "Te toca"})
    assert world.turns == [] and remote.sent[-1]["payload"] == {
        "request_id": REQUEST, "accepted": False, "message": "«Avisar a Iris» solo funciona en este equipo."}


@pytest.mark.parametrize("request_id", [None, "C" * 32, "c" * 31, 7])
def test_a_message_without_its_id_is_a_protocol_error(world, request_id):
    local = surface(world, "desk")
    run_message(world, local, {"app_id": "iris-chess", "text": "Te toca",
                               **({} if request_id is None else {"request_id": request_id})})
    assert world.turns == [] and local.sent[-1]["type"] == "protocol_error"
