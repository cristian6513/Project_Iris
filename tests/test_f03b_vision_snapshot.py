"""F03b: one camera photo when the user asks Iris to look.

The broker, validation and consent are tested directly; the turn flow runs
Core's real handlers with fake sockets, a fake agent and a fake vision route.
"""

from __future__ import annotations

import asyncio
import base64
import struct
import zlib
from types import SimpleNamespace

import pytest

from core import vision_model, vision_snapshot
from core.capability_permissions import CapabilityPermissionStore

# ── Requests to look ──────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "¿Qué tengo en la mano?", "que tengo en la mano", "¿Qué estoy sosteniendo en mi mano?",
    "Mira lo que te muestro", "mira esto", "¿De qué color es esto?", "¿Qué ves en la cámara?",
    "¿puedes ver lo que tengo?", "léeme esto", "¿Qué es esto que te muestro?", "Tómame una foto",
    "What am I holding?", "look at this",
])
def test_requests_to_look_are_recognised(text):
    assert vision_snapshot.is_visual_request(text)


@pytest.mark.parametrize("text", [
    "Mira, ayer fui al cine", "¿Qué tengo que hacer mañana?", "¿qué ves en este código?",
    "Pon música", "¿De qué color es el cielo?", "Explícame la fotosíntesis", "", "   ",
])
def test_other_turns_take_no_photo(text):
    assert not vision_snapshot.is_visual_request(text)


@pytest.mark.parametrize("text", [
    "puedes ver lo que estoy viendo en pantalla??", "Mira esto en mi pantalla",
    "¿Puedes ver lo que tengo en el escritorio?", "Lee esto en el monitor",
    "look at this screenshot", "look at this on my screen",
])
def test_desktop_requests_do_not_borrow_the_camera(text):
    assert not vision_snapshot.is_visual_request(text)


def test_screen_explicitly_shown_through_the_camera_keeps_its_requested_source():
    assert vision_snapshot.is_visual_request("Mira esto en mi pantalla por la cámara")


# ── The photo ─────────────────────────────────────────────────────────────


def png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + b"\x10\x20\x30" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def jpeg_header(width: int, height: int) -> bytes:
    # SOI, APP0, SOF0 with the dimensions, EOI: what Iris reads to validate.
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


PHOTO = data_url(png(640, 480))


def test_a_jpeg_or_png_of_sane_size_is_accepted():
    assert vision_snapshot.validate_image(PHOTO) == {"mime": "image/png", "bytes": len(png(640, 480)),
                                                     "width": 640, "height": 480}
    assert vision_snapshot.validate_image(data_url(jpeg_header(1280, 720), "image/jpeg"))["width"] == 1280


@pytest.mark.parametrize("value, code", [
    (None, "invalid_image"),
    ("https://example.invalid/foto.png", "invalid_image"),
    ("data:image/gif;base64,R0lGODlh", "invalid_image"),
    ("data:image/png;base64,%%%", "invalid_image"),
    (data_url(jpeg_header(640, 480), "image/png"), "invalid_image"),  # the bytes are not what the type says
    (data_url(png(32, 32)), "invalid_dimensions"),
    (data_url(png(5000, 64)), "invalid_dimensions"),
    ("data:image/jpeg;base64," + "A" * (6 * 1024 * 1024), "image_too_large"),
], ids=["none", "url", "gif", "not-base64", "type-mismatch", "too-small", "too-wide", "too-large"])
def test_anything_else_is_refused(value, code):
    with pytest.raises(vision_snapshot.SnapshotError) as refused:
        vision_snapshot.validate_image(value)
    assert refused.value.code == code


def test_a_remembered_destination_covers_that_provider_and_model_only(tmp_path):
    consent = vision_snapshot.SnapshotConsent(tmp_path / "consent.json")
    destination = {"provider": "openai-codex", "model": "gpt-5.5"}
    assert not consent.covers(destination)
    consent.remember(destination)
    assert consent.covers(destination)
    assert not consent.covers({"provider": "openrouter", "model": "gpt-5.5"})
    assert not consent.covers({"provider": "openai-codex", "model": "otro"})
    consent.forget()
    assert not consent.covers(destination)
    (tmp_path / "consent.json").write_text("{dañado", "utf-8")
    assert consent.load() == {}


def test_the_snapshot_permission_is_separate_and_asks_by_default(tmp_path):
    store = CapabilityPermissionStore(tmp_path / "policy.json")
    assert store.evaluate("vision.snapshot")["verdict"] == "ask"
    store.set_mode("camera.capture", "allow")
    store.set_mode("vision.context", "allow")
    assert store.evaluate("vision.snapshot")["verdict"] == "ask"  # neither one authorises a photo
    store.set_mode("vision.snapshot", "deny")
    assert store.evaluate("vision.snapshot", confirmed=True)["verdict"] == "deny"


# ── One-use requests ──────────────────────────────────────────────────────


def test_an_answer_counts_once_from_the_surface_asked_before_its_deadline():
    async def scenario():
        now = [0.0]
        broker = vision_snapshot.SnapshotBroker(clock=lambda: now[0])
        asked, other = object(), object()
        request = broker.open(asked, {"token": "t"}, timeout=5)
        assert broker.answer(other, {"request_id": request.request_id, "status": "ok"}) is False
        assert broker.answer(asked, {"request_id": "snap-inventado"}) is False
        assert broker.answer(asked, {"request_id": request.request_id, "status": "ok"}) is True
        assert (await request.future)["status"] == "ok"
        assert broker.answer(asked, {"request_id": request.request_id, "status": "ok"}) is False  # duplicate
        late = broker.open(asked, {}, timeout=5)
        now[0] = 6
        assert broker.answer(asked, {"request_id": late.request_id, "status": "ok"}) is False
        assert late.future.cancelled()
        gone = broker.open(asked, {}, timeout=5)
        broker.drop_surface(asked)
        assert gone.future.cancelled() and broker.pending() == 0

    asyncio.run(scenario())


# ── Core's turn ───────────────────────────────────────────────────────────


class Socket:
    def __init__(self, host="127.0.0.1"):
        self.client = SimpleNamespace(host=host)
        self.sent: list[dict] = []
        self.reply = None  # called with each request payload

    async def send_json(self, message):
        self.sent.append(message)
        if message["type"] == "vision_snapshot_request" and self.reply:
            asyncio.get_running_loop().call_soon(self.reply, message["payload"])

    def requests(self):
        return [m["payload"] for m in self.sent if m["type"] == "vision_snapshot_request"]

    def cancels(self):
        return [m["payload"] for m in self.sent if m["type"] == "vision_snapshot_cancel"]


class Bridge:
    is_available = True

    def __init__(self):
        self.generation = 0

    def route_context(self):
        return {"profile": None, "session_id": "s", "generation": self.generation,
                "token": f"default:{self.generation}", "api_key": ""}


@pytest.fixture
def core(monkeypatch, tmp_path):
    from core import websocket_server as server

    server._surface_registry.clear()
    store = CapabilityPermissionStore(tmp_path / "policy.json")
    consent = vision_snapshot.SnapshotConsent(tmp_path / "consent.json")
    broker = vision_snapshot.SnapshotBroker()
    monkeypatch.setattr(server, "_capability_store", store)
    monkeypatch.setattr(server, "_snapshot_consent", consent)
    monkeypatch.setattr(server, "_snapshot_broker", broker)
    monkeypatch.setattr(vision_snapshot, "CONFIRM_TIMEOUT_S", 2.0)
    monkeypatch.setattr(vision_snapshot, "CAPTURE_TIMEOUT_S", 2.0)
    destination = {"provider": "openai-codex", "model": "gpt-5.5", "available": True}
    planned = SimpleNamespace(value=destination)

    async def planned_route(home):
        return dict(planned.value)

    monkeypatch.setattr(vision_model, "planned_route", planned_route)
    monkeypatch.setattr(vision_model, "profile_home", lambda profile: tmp_path / "hermes")
    agent = SimpleNamespace(bridge=Bridge())

    def surface(host="127.0.0.1", *, snapshots=True, visible=True, role="face"):
        socket = Socket(host)
        server._surface_registry.register_ready(socket, {"role": role, "visible": visible,
                                                         "capabilities": {"takes_snapshots": snapshots}})
        return socket

    def answer(socket, **fields):
        def reply(payload):
            broker.answer(socket, {"request_id": payload["request_id"], **fields})
        return reply

    yield SimpleNamespace(server=server, store=store, consent=consent, broker=broker, agent=agent,
                          surface=surface, answer=answer, planned=planned)
    server._surface_registry.clear()


def take(core, origin, question="¿qué tengo en la mano?"):
    return asyncio.run(core.server._take_turn_snapshot(origin, core.agent, question))


def test_a_confirmed_photo_comes_back_once_with_its_destination(core):
    face = core.surface()
    face.reply = core.answer(face, status="ok", image=PHOTO)
    outcome = take(core, face)
    assert outcome["image"] == PHOTO and outcome["meta"]["width"] == 640
    assert outcome["destination"] == {"provider": "openai-codex", "model": "gpt-5.5"}
    [request] = face.requests()
    assert request["needs_confirmation"] is True  # "ask" by default
    assert request["destination"] == {"provider": "openai-codex", "model": "gpt-5.5"}
    assert request["question"] == "¿qué tengo en la mano?"
    assert core.broker.pending() == 0


def test_a_denied_permission_or_no_vision_model_asks_no_surface(core):
    face = core.surface()
    core.store.set_mode("vision.snapshot", "deny")
    assert take(core, face) == {"code": "permission_denied"}
    core.store.set_mode("vision.snapshot", "ask")
    core.planned.value = {"provider": "", "model": "", "available": False}
    assert take(core, face) == {"code": "no_vision_model"}
    assert face.requests() == []


def test_lan_hidden_or_old_surfaces_are_never_asked(core):
    assert take(core, core.surface("192.168.1.20")) == {"code": "no_surface"}
    assert take(core, core.surface(visible=False)) == {"code": "no_surface"}
    assert take(core, core.surface(snapshots=False)) == {"code": "no_surface"}


def test_the_camera_owner_is_asked_and_a_lease_is_never_taken_from_another_window(core):
    face, companion = core.surface(), core.surface(role="desktop_companion", snapshots=False)
    core.server._surface_registry.request_camera(companion)
    assert take(core, face) == {"code": "camera_elsewhere"}
    assert face.requests() == [] and companion.requests() == []
    core.server._surface_registry.release_camera(companion)
    owner = core.surface()
    core.server._surface_registry.request_camera(owner)
    owner.reply = core.answer(owner, status="ok", image=PHOTO)
    assert "image" in take(core, face)  # asked from Face, taken where the camera is open
    assert face.requests() == [] and len(owner.requests()) == 1


def test_a_remembered_destination_skips_the_question_only_with_the_camera_on(core):
    face = core.surface()
    face.reply = core.answer(face, status="ok", image=PHOTO, remember=True)
    take(core, face)
    assert core.store.evaluate("vision.snapshot")["verdict"] == "allow"
    assert core.consent.covers({"provider": "openai-codex", "model": "gpt-5.5"})
    core.server._surface_registry.request_camera(face)
    take(core, face)
    assert face.requests()[-1]["needs_confirmation"] is False
    core.server._surface_registry.release_camera(face)
    take(core, face)
    assert face.requests()[-1]["needs_confirmation"] is True  # a camera that is off needs a click
    core.server._surface_registry.request_camera(face)
    core.planned.value = {"provider": "openrouter", "model": "gemini", "available": True}
    take(core, face)
    assert face.requests()[-1]["needs_confirmation"] is True  # another destination asks again


@pytest.mark.parametrize("fields, code", [
    ({"status": "cancelled"}, "cancelled"),
    ({"status": "camera_denied"}, "camera_denied"),
    ({"status": "unavailable"}, "camera_unavailable"),
    ({"status": "ok", "image": "data:image/png;base64,AAAA"}, "invalid_image"),
    ({"status": "ok", "image": data_url(png(32, 32))}, "invalid_dimensions"),
])
def test_no_valid_photo_means_an_honest_answer(core, fields, code):
    face = core.surface()
    face.reply = core.answer(face, **fields)
    assert take(core, face) == {"code": code}


def test_a_foreign_answer_is_ignored_and_a_late_one_times_out(core):
    face, other = core.surface(), core.surface()
    face.reply = core.answer(other, status="ok", image=PHOTO)  # another surface answers for it
    assert take(core, face) == {"code": "timeout"}
    assert face.cancels()[-1]["reason"] == "timeout"


def test_stopping_the_turn_cancels_the_photo(core):
    face = core.surface()

    async def scenario():
        task = asyncio.create_task(core.server._take_turn_snapshot(face, core.agent, "mira esto"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert face.cancels()[-1]["reason"] == "cancelled" and core.broker.pending() == 0


def test_a_surface_that_leaves_or_a_conversation_that_changes_sends_nothing(core):
    face = core.surface()
    face.reply = lambda payload: core.server._disconnect_surface(face)
    assert take(core, face) == {"code": "surface_changed"}

    face = core.surface()

    def switch_then_answer(payload):
        core.agent.bridge.generation += 1  # Wake/Continuity switched meanwhile
        core.broker.answer(face, {"request_id": payload["request_id"], "status": "ok", "image": PHOTO})

    face.reply = switch_then_answer
    assert take(core, face) == {"code": "stale"}


def test_a_lease_moved_while_taking_the_photo_discards_it(core):
    face, other = core.surface(), core.surface()
    core.server._surface_registry.request_camera(face)

    def move_then_answer(payload):
        core.server._surface_registry.request_camera(other)
        core.broker.answer(face, {"request_id": payload["request_id"], "status": "ok", "image": PHOTO})

    face.reply = move_then_answer
    assert take(core, face) == {"code": "surface_changed"}


# ── One chat turn ─────────────────────────────────────────────────────────


class Agent:
    def __init__(self):
        self.bridge = Bridge()
        self.thoughts: list[dict] = []
        self.responses: list[dict] = []
        self._turn_lock = asyncio.Lock()
        self._current_turn_task = None
        self._session_id = "s"
        self.memory = SimpleNamespace(remember=self._remember, recalled=[])

    async def _remember(self, role, text, **kwargs):
        self.memory.recalled.append((role, text))

    async def _think(self, text, images=None, reasoning_effort=None, *, snapshot=None, turn_context=""):
        thought = {"text": text, "images": images, "snapshot": snapshot}
        if turn_context:  # a local turn's offers: to look, to work in the background
            thought["turn_context"] = turn_context
        self.thoughts.append(thought)
        return {"texto_hablado": "Parece una manzana.", "emocion": "feliz", "animacion_virtual": "idle"}

    async def _respond(self, response, *, still_relevant=None):
        if still_relevant is not None and not still_relevant():
            return False  # like IRISAgent._respond after generating the audio
        self.responses.append(response)
        return True


@pytest.fixture
def turn(core, monkeypatch):
    agent = Agent()
    monkeypatch.setattr(core.server.app.state, "agent", agent, raising=False)
    core.agent = agent

    async def broadcast(event, payload):
        pass

    monkeypatch.setattr(core.server, "broadcast_to_face", broadcast)
    return agent


def test_a_visual_question_becomes_one_turn_with_its_photo(core, turn):
    face = core.surface()
    face.reply = core.answer(face, status="ok", image=PHOTO)
    asyncio.run(core.server._handle_user_text_unlocked(face, "¿Qué tengo en la mano?"))
    [thought] = turn.thoughts
    assert thought["images"] == [PHOTO]
    assert thought["snapshot"]["destination"] == {"provider": "openai-codex", "model": "gpt-5.5"}
    assert turn.responses == [{"texto_hablado": "Parece una manzana.", "emocion": "feliz", "animacion_virtual": "idle"}]
    assert ("user", "¿Qué tengo en la mano?") in turn.memory.recalled  # the text, never the photo


def test_a_screen_question_routes_one_reviewed_capture_and_never_borrows_the_camera(core, turn, monkeypatch):
    broker = vision_snapshot.SnapshotBroker()
    monkeypatch.setattr(core.server, "_screen_snapshot_broker", broker)
    face = core.surface()
    core.server._surface_registry.register_ready(face, {
        "role": "face", "visible": True, "capabilities": {"takes_screen_snapshots": True}})
    original = face.send_json

    async def send(message):
        await original(message)
        if message["type"] == "screen_snapshot_request":
            await core.server._handle_face_message(face, {"type": "screen_snapshot_result", "payload": {
                "request_id": message["payload"]["request_id"], "status": "ok", "confirmed": True, "image": PHOTO}})

    face.send_json = send
    asyncio.run(core.server._handle_user_text_unlocked(face, "Mira mi pantalla"))
    assert len(turn.thoughts) == 1 and turn.thoughts[0]["snapshot"]["source"] == "screen"
    assert turn.thoughts[0]["images"] == [PHOTO] and face.requests() == []
    assert ("user", "Mira mi pantalla") in turn.memory.recalled


def test_remote_chat_cannot_request_this_computers_screen(core, turn):
    face = core.surface("192.168.1.20")
    asyncio.run(core.server._handle_user_text_unlocked(face, "Mira mi pantalla"))
    assert turn.thoughts[0]["snapshot"] is None
    assert not any(m["type"] == "screen_snapshot_request" for m in face.sent)


@pytest.mark.parametrize('code',['cancelled','permission_denied','no_vision_model','no_surface',
    'timeout','unsupported','unavailable','error'])
def test_without_a_screen_image_the_original_question_still_gets_one_text_turn(core,turn,monkeypatch,code):
    async def fail(*args,**kwargs):return {'code':code}
    monkeypatch.setattr(core.server.screen_snapshot,'take_snapshot',fail)
    question='Mira mi pantalla y dime cómo compartirla en Zoom'
    asyncio.run(core.server._handle_user_text_unlocked(core.surface(),question))
    [thought]=turn.thoughts
    assert thought['text']==question and not thought['images']
    assert thought['snapshot']['source']=='screen' and thought['snapshot']['unavailable']==code
    assert len(turn.responses)==1 and ('user',question) in turn.memory.recalled


def test_a_profile_change_during_cancel_does_not_send_the_old_question_to_the_new_profile(core,turn,monkeypatch):
    async def fail(*args,**kwargs):
        turn.bridge.generation+=1
        return {'code':'cancelled'}
    monkeypatch.setattr(core.server.screen_snapshot,'take_snapshot',fail)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(core.server._handle_user_text_unlocked(core.surface(),'Mira mi pantalla'))
    assert turn.thoughts==[] and turn.responses==[]


@pytest.mark.parametrize('question',['La pantalla se ve muy oscura, ¿qué hago?',
    '¿Cómo puedo compartir pantalla en Zoom?', 'Quiero ver una película en pantalla completa',
    '¿Qué monitor me recomiendas para ver series?', 'Mira lo que tengo en el escritorio'])
def test_ordinary_screen_questions_go_straight_to_chat_without_a_picker(core,turn,monkeypatch,question):
    async def unexpected(*args,**kwargs):pytest.fail('ordinary question opened capture')
    monkeypatch.setattr(core.server.screen_snapshot,'take_snapshot',unexpected)
    asyncio.run(core.server._handle_user_text_unlocked(core.surface(),question))
    [thought]=turn.thoughts
    assert thought['text']==question and thought['snapshot'] is None and not thought['images']


def test_without_a_photo_iris_answers_honestly_and_asks_no_model(core, turn):
    face = core.surface()
    face.reply = core.answer(face, status="cancelled")
    asyncio.run(core.server._handle_user_text_unlocked(face, "mira esto"))
    assert turn.thoughts == []
    assert turn.responses[0]["texto_hablado"] == vision_snapshot.OUTCOMES["cancelled"]


def test_ordinary_turns_and_attached_images_take_no_photo(core, turn):
    face = core.surface()
    asyncio.run(core.server._handle_user_text_unlocked(face, "Pon música"))
    asyncio.run(core.server._handle_user_text_unlocked(face, "¿qué tengo en la mano?", images=[PHOTO]))
    assert face.requests() == []
    assert [t["snapshot"] for t in turn.thoughts] == [None, None]


def test_the_camera_button_asks_for_a_photo_whatever_the_words(core, turn):
    face = core.surface()
    face.reply = core.answer(face, status="ok", image=PHOTO)
    asyncio.run(core.server._handle_user_text_unlocked(face, "explícame", camera_question=True))
    assert len(face.requests()) == 1 and turn.thoughts[0]["images"] == [PHOTO]


def test_a_spoken_question_uses_the_camera_owner(core, turn):
    face = core.surface()
    core.server._surface_registry.request_camera(face)
    face.reply = core.answer(face, status="ok", image=PHOTO)
    asyncio.run(core.server._handle_user_text_unlocked(None, "¿de qué color es esto?"))
    assert len(face.requests()) == 1 and turn.thoughts[0]["images"] == [PHOTO]


def test_the_socket_keeps_reading_while_its_visual_turn_waits_for_the_photo(core, turn):
    face = core.surface()

    async def scenario():
        await core.server._handle_face_message(face, {"type": "user_text", "payload": {"text": "mira esto"}})
        # The message handler returned: the turn runs beside the socket loop.
        for _ in range(50):
            if face.requests():
                break
            await asyncio.sleep(0.01)
        [request] = face.requests()
        await core.server._handle_face_message(face, {"type": "vision_snapshot_result", "payload": {
            "request_id": request["request_id"], "status": "ok", "image": PHOTO}})
        await asyncio.gather(*core.server._background_turns)

    asyncio.run(scenario())
    assert turn.thoughts[0]["images"] == [PHOTO]


# ── The photo reaches the vision route the user saw, and only it ─────────


def test_the_photo_is_analysed_by_the_destination_shown(monkeypatch, tmp_path):
    from tests.test_f02_vision_model import TurnBridge, turn_agent

    bridge = TurnBridge()
    seen = {}

    async def analyze(home, images, prompt, *, expected=None):
        seen.update(images=images, prompt=prompt, expected=expected)
        return {"analyses": ["Una manzana roja, nítida."], "planned": {}, "route": {"provider": "openai-codex",
                                                                                   "model": "gpt-5.5"}}

    monkeypatch.setattr(vision_model, "profile_home", lambda profile: tmp_path)
    monkeypatch.setattr(vision_model, "read_configured", lambda home: pytest.fail("the photo route is fixed"))
    monkeypatch.setattr(vision_model, "analyze", analyze)
    snapshot = {"image": PHOTO, "destination": {"provider": "openai-codex", "model": "gpt-5.5"}}
    asyncio.run(turn_agent(bridge)._think("¿Qué tengo en la mano?", images=[PHOTO], snapshot=snapshot))
    assert seen["expected"] == {"provider": "openai-codex", "model": "gpt-5.5"}
    assert "¿Qué tengo en la mano?" in seen["prompt"] and "no adivines" in seen["prompt"]
    [sent] = bridge.sent
    assert sent["images"] is None
    assert sent["text"].startswith("[Foto de la cámara tomada ahora a petición del usuario.")
    assert "gpt-5.5 (openai-codex)" in sent["text"] and sent["text"].endswith("¿Qué tengo en la mano?")


def test_the_text_fallback_tells_the_model_there_is_no_image_without_rewriting_the_question(monkeypatch):
    from tests.test_f02_vision_model import TurnBridge, turn_agent
    bridge=TurnBridge();sent=[]
    async def send(text,**kwargs):
        sent.append({'text':text,**kwargs})
        return {'texto_hablado':'Puedes abrir Compartir pantalla en Zoom.','emocion':'neutral','animacion_virtual':'idle'}
    bridge.send_message=send
    monkeypatch.setattr(vision_model,'analyze',lambda *a,**k:pytest.fail('no image may be analyzed'))
    question='Mira mi pantalla y dime cómo compartirla en Zoom'
    snapshot={'source':'screen','unavailable':'cancelled','route_token':bridge.route_context()['token']}
    result=asyncio.run(turn_agent(bridge)._think(question,snapshot=snapshot))
    [request]=sent
    assert request['text']==question and request['images'] is None
    assert 'No se recibió ni analizó ninguna imagen' in request['memory_summary']
    assert 'Responde la pregunta original' in request['memory_summary']
    assert 'No inventes' in request['memory_summary'] and 'herramientas, cámara o navegador' in request['memory_summary']
    assert result['texto_hablado'].startswith('Puedes abrir')


def test_reconnect_during_the_text_fallback_cannot_move_the_question_to_another_profile():
    from tests.test_f02_vision_model import TurnBridge, turn_agent
    bridge=TurnBridge();bridge.is_available=False
    original=bridge.route_context()['token']
    async def reconnect():
        bridge.active_profile='another'
        return True
    bridge.reconnect=reconnect
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(turn_agent(bridge)._think('Mira mi pantalla',snapshot={
            'source':'screen','unavailable':'no_vision_model','route_token':original}))
    assert bridge.sent==[]
