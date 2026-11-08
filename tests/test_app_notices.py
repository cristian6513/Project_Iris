"""App notices: Core keeps and schedules them, shows each one once and keeps it in the Bandeja."""
import asyncio
import copy

import pytest

from core import app_notices
from core.app_notices import AppNoticeStore, NoticeRefused, apply_requests, checked_request
from core.inbox_api import InboxService

NOW = 1_800_000_000.0
INSTALLATION = "ins_" + "a" * 32


def store(tmp_path):
    return AppNoticeStore(tmp_path / "app_notices.db")


def post(notices, *, app="enfoque", now=NOW, **fields):
    raw = {"title": "Pomodoro terminado", "text": "Toca descansar 5 minutos.", **fields}
    return notices.post(app, "Enfoque", checked_request(raw, now=now), now=now)


def test_requests_are_checked_before_anything_is_stored():
    assert checked_request({"title": " Hola ", "at": 0}, now=NOW)["due_at"] == NOW
    assert checked_request({"title": "x", "at": NOW - 50}, now=NOW)["due_at"] == NOW, "a past time means now"
    assert checked_request({"key": "t1", "cancel": True}, now=NOW) == {"cancel": True, "key": "t1"}
    for bad in ({"title": ""}, {"title": "x" * 121}, {"title": "x", "text": "y" * 1001}, {"title": "x", "at": -1},
                {"title": "x", "at": True}, {"title": "x", "at": NOW + 400 * 86400}, {"title": "x", "key": "../a"},
                {"title": "x", "extra": 1}, {"title": "a\x07b"}, {"key": "t1", "cancel": True, "title": "x"},
                {"cancel": True}, {"title": "x", "cancel": False}, "texto"):
        with pytest.raises(NoticeRefused):
            checked_request(bad, now=NOW)


def test_a_key_moves_its_reminder_and_an_identical_notice_is_not_repeated(tmp_path):
    notices = store(tmp_path)
    first = post(notices, key="pomodoro", at=NOW + 1500)
    assert first["status"] == "scheduled"
    moved = post(notices, key="pomodoro", at=NOW + 900, title="Termina antes")
    assert moved["status"] == "replaced" and moved["notice"]["id"] == first["notice"]["id"]
    assert [row["due_at"] for row in notices.scheduled("enfoque", now=NOW)] == [NOW + 900]
    assert post(notices, at=NOW)["status"] == "scheduled"
    assert post(notices, at=NOW, now=NOW + 5)["status"] == "duplicate", "the same notice twice in a minute"
    assert post(notices, at=NOW + 61, now=NOW + 61)["status"] == "scheduled", "later it is a new notice"
    assert notices.cancel("enfoque", "pomodoro", now=NOW) is True
    assert notices.cancel("enfoque", "pomodoro", now=NOW) is False
    assert "pomodoro" not in [row["key"] for row in notices.scheduled("enfoque", now=NOW)]


def test_limits_and_the_persons_choice_per_app(tmp_path, monkeypatch):
    notices = store(tmp_path)
    monkeypatch.setattr(app_notices, "MAX_PENDING", 2)
    post(notices, key="a", at=NOW + 10)
    post(notices, key="b", at=NOW + 10)
    with pytest.raises(NoticeRefused) as limit:
        post(notices, key="c", at=NOW + 10)
    assert limit.value.code == "limit"
    monkeypatch.setattr(app_notices, "MAX_PER_HOUR", 3)
    post(notices, title="Uno")
    with pytest.raises(NoticeRefused) as rate:
        post(notices, title="Dos")
    assert rate.value.code == "rate"
    notices.set_mode("otra", "off")
    with pytest.raises(NoticeRefused) as off:
        post(notices, app="otra")
    assert off.value.code == "off"
    assert notices.modes() == {"otra": "off"} and notices.mode("enfoque") == "show"
    with pytest.raises(NoticeRefused):
        notices.set_mode("otra", "loud")


def test_a_due_notice_is_offered_once_and_a_deferred_card_comes_back_a_few_times(tmp_path):
    notices = store(tmp_path)
    notice = post(notices, at=NOW + 60)["notice"]
    assert notices.presentable(now=NOW) == [] and notices.next_due(now=NOW) == NOW + 60
    assert notices.public(notice["id"], now=NOW) is None, "nothing is readable before its time"
    due = NOW + 60
    assert [row["id"] for row in notices.presentable(now=due)] == [notice["id"]]
    for attempt in range(app_notices.MAX_ATTEMPTS):
        claimed = notices.claim(notice["id"], "surface-a", now=due)
        assert claimed is not None, attempt
        assert notices.claim(notice["id"], "surface-b", now=due) is None, "one window at a time"
        assert notices.receipt(notice["id"], token="0" * 32, surface_id="surface-a", shown=True, now=due) is False
        assert notices.receipt(notice["id"], token=claimed["presentation_token"], surface_id="surface-a",
                               shown=False, now=due) is True
    assert notices.presentable(now=due) == [], "deferred too often: it stays in the Bandeja without a card"
    other = post(notices, title="Otro", key="x", at=NOW + 60)["notice"]
    claimed = notices.claim(other["id"], "surface-a", now=due)
    assert notices.receipt(other["id"], token=claimed["presentation_token"], surface_id="surface-a", shown=True, now=due)
    assert notices.get(other["id"])["presented_at"] == due
    assert notices.public(other["id"], now=due) == {"id": other["id"], "app_id": "enfoque", "app_name": "Enfoque",
                                                     "title": "Otro", "text": "Toca descansar 5 minutos.",
                                                     "due_at": NOW + 60}


def test_a_late_card_is_not_shown_and_inbox_only_apps_never_get_one(tmp_path):
    notices = store(tmp_path)
    post(notices, at=NOW)
    assert notices.presentable(now=NOW + app_notices.PRESENT_WINDOW_S + 1) == []
    assert len(notices.inbox_rows(now=NOW + app_notices.PRESENT_WINDOW_S + 1)) == 1
    notices.set_mode("pendientes", "inbox")
    post(notices, app="pendientes", title="Llamar a mamá")
    assert [row["app_id"] for row in notices.presentable(now=NOW)] == ["enfoque"]
    assert {row["app_id"] for row in notices.inbox_rows(now=NOW)} == {"enfoque", "pendientes"}


def test_the_bandeja_shows_arrived_notices_with_their_app_and_its_actions(tmp_path):
    notices = store(tmp_path)
    post(notices, key="later", at=NOW + 3600)
    arrived = post(notices, at=NOW)["notice"]
    service = InboxService(store=lambda: None, store_problem=lambda: "", clock=lambda: NOW, apps=lambda: notices)
    snapshot = service.snapshot()
    items = [item for item in snapshot["items"] if item["origin"] == "app"]
    assert [item["source_id"] for item in items] == [arrived["id"]], "a future notice is not there yet"
    item = items[0]
    assert (item["app_id"], item["app_name"], item["outcome"], item["actions"]) == ("enfoque", "Enfoque", "notice",
                                                                                     ["read", "dismiss"])
    assert snapshot["unread"] == 1 and snapshot["sources"]["app"]["status"] == "ok"
    answer = service.act(item["key"], "read", expected_revision=item["delivery"]["revision"])
    assert answer["changed"] and answer["item"]["delivery"]["status"] == "read" and answer["unread"] == 0
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as stale:
        service.act(item["key"], "dismiss", expected_revision=item["delivery"]["revision"])
    assert stale.value.status_code == 409


def test_an_uninstalled_app_never_fires_again_but_what_arrived_stays(tmp_path):
    notices = store(tmp_path)
    post(notices, key="later", at=NOW + 3600)
    post(notices, title="Ya llegó")
    assert notices.forget_app("enfoque", now=NOW) == 1
    assert notices.next_due(now=NOW) is None
    assert [row["title"] for row in notices.inbox_rows(now=NOW + 7200)] == ["Ya llegó"]


def test_backend_notices_are_applied_one_by_one(tmp_path):
    notices = store(tmp_path)
    summary = apply_requests(notices, "pendientes", "Pendientes", [
        {"title": "Llamar a mamá", "text": "", "at": NOW + 3600, "key": "t1"},
        {"title": ""},
        {"key": "t1", "cancel": True},
    ], now=NOW)
    assert summary["scheduled"] == 1 and summary["cancelled"] == 1 and len(summary["refused"]) == 1
    assert apply_requests(notices, "pendientes", "Pendientes", "no", now=NOW)["refused"]


def test_a_backend_schedules_notices_named_by_core_and_its_data_comes_back_clean(tmp_path):
    from core.app_actions import AppActionRegistry
    from core.app_extensions import AppExtensions
    from tests.test_app_extensions import MANIFEST, package

    notices = store(tmp_path)
    changes = []
    backend = ('def handle(action, args, context):\n'
               '    return {"count": 1, "iris_notices": [{"title": "Recordatorio", "text": "", "at": 0, "key": "r1"}]}\n')
    manifest = copy.deepcopy(MANIFEST)
    manifest['name'] = 'Contador de prueba'

    async def scenario():
        manager = AppExtensions(lambda: tmp_path / 'iris', registry=AppActionRegistry(),
                                hermes_home=lambda: tmp_path / 'hermes', notices=lambda: notices,
                                notices_changed=lambda: changes.append(1))
        try:
            await manager.install(package(manifest=manifest, backend=backend), expected=None)
            answer = await manager.call('contador', 'status', {}, caller='iris')
            assert answer['data'] == {'count': 1}, 'Core takes the notices out of the data'
            assert answer['notices'] == {'scheduled': 1, 'cancelled': 0, 'refused': []}
        finally:
            await manager.close()
    asyncio.run(scenario())
    rows = notices.inbox_rows()
    assert [(row["app_id"], row["app_name"], row["title"]) for row in rows] == [("contador", "Contador de prueba",
                                                                                 "Recordatorio")]
    assert changes == [1]


def test_the_routes_serve_this_pc_and_only_installed_apps(tmp_path):
    import time

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.google_api import require_local_peer

    notices = store(tmp_path)
    changes = []
    server = FastAPI()

    async def installed(app_id, installation, work):  # as AppExtensions.for_installation
        return work("Enfoque") if (app_id, installation) == ("enfoque", INSTALLATION) else None

    server.include_router(app_notices.app_notices_router(
        store=lambda: notices, app_name=lambda app_id: "Enfoque" if app_id == "enfoque" else None,
        installed=installed, changed=lambda: changes.append(1), require_local=require_local_peer))
    with TestClient(server, base_url="http://127.0.0.1", client=("127.0.0.1", 5000)) as api:
        posted = api.post("/api/app-notices", json={"app": "enfoque", "installation": INSTALLATION, "title": "Pomodoro",
                                                    "key": "p", "at": 0})
        assert posted.status_code == 200, posted.text
        body = posted.json()
        assert body["status"] == "scheduled" and body["mode"] == "show" and set(body["notice"]) == {"id", "key", "title", "due_at"}
        assert api.get(f"/api/app-notices/{body['notice']['id']}").json()["app_name"] == "Enfoque"
        assert api.post("/api/app-notices", json={"app": "otra", "installation": INSTALLATION, "title": "x"}).status_code == 404
        stale = api.post("/api/app-notices", json={"app": "enfoque", "installation": "ins_" + "b" * 32, "title": "x"})
        assert stale.status_code == 404, "a view of another version of the app schedules nothing"
        assert api.post("/api/app-notices", json={"app": "enfoque", "title": "x"}).status_code == 422
        assert api.post("/api/app-notices", json={"app": "enfoque", "installation": INSTALLATION, "title": ""}).status_code == 400
        assert api.post("/api/app-notices", json={"app": "enfoque", "installation": INSTALLATION, "title": "x",
                                                  "app_name": "Falsa"}).status_code == 422
        future = api.post("/api/app-notices", json={"app": "enfoque", "installation": INSTALLATION, "title": "Luego",
                                                    "key": "later", "at": time.time() + 3600}).json()
        assert [row["key"] for row in api.get("/api/app-notices/scheduled/enfoque").json()["notices"]] == ["later"]
        assert api.delete("/api/app-notices/enfoque/later").json() == {"cancelled": True}
        assert api.get(f"/api/app-notices/{future['notice']['id']}").status_code == 404
        assert api.put("/api/app-notices/modes/enfoque", json={"mode": "inbox"}).json()["mode"] == "inbox"
        assert api.get("/api/app-notices/modes").json()["modes"] == {"enfoque": "inbox"}
        assert api.put("/api/app-notices/modes/enfoque", json={"mode": "loud"}).status_code == 400
    assert changes
    with TestClient(server, base_url="http://127.0.0.1", client=("192.168.1.20", 5000)) as remote:
        assert remote.get("/api/app-notices/modes").status_code == 403, "another device on the network never reads them"


def test_a_card_chosen_before_the_person_turned_notices_off_is_not_offered_or_drawn(tmp_path):
    notices = store(tmp_path)
    first = post(notices)["notice"]
    second = post(notices, title="Otro")["notice"]
    assert {row["id"] for row in notices.presentable(now=NOW)} == {first["id"], second["id"]}
    notices.set_mode("enfoque", "off")  # while Core was about to offer them (Codex SDK05)
    assert notices.claim(first["id"], "desk", now=NOW) is None
    notices.set_mode("enfoque", "show")
    claimed = notices.claim(second["id"], "desk", now=NOW)
    notices.set_mode("enfoque", "inbox")  # after the window was told, before it read the text
    assert notices.public(second["id"], now=NOW) is None, "the late card finds nothing to draw"
    assert claimed is not None and len(notices.inbox_rows(now=NOW)) == 2, "both stay in the Bandeja"


def test_an_uninstall_waits_for_a_backend_notice_and_its_cleanup_then_removes_it(tmp_path, monkeypatch):
    """Codex SDK03: the write is checked and done under the lock an uninstall takes."""
    import threading

    from core.app_actions import AppActionRegistry
    from core.app_extensions import AppExtensions
    from tests.test_app_extensions import package

    notices = store(tmp_path)
    entered, release = threading.Event(), threading.Event()
    real_apply = app_notices.apply_requests

    def held_apply(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(app_notices, "apply_requests", held_apply)
    backend = ('import time\n'
               'def handle(action, args, context):\n'
               '    return {"ok": True, "iris_notices": [{"title": "Luego", "text": "", "at": time.time() + 3600, "key": "r"}]}\n')

    async def scenario():
        manager = AppExtensions(lambda: tmp_path / 'iris', registry=AppActionRegistry(),
                                hermes_home=lambda: tmp_path / 'hermes', notices=lambda: notices)
        try:
            installed = await manager.install(package(backend=backend), expected=None)
            assert await manager.for_installation('contador', 'ins_' + 'f' * 32, lambda name: name) is None
            assert await manager.for_installation('contador', installed['installation'], lambda name: name) == 'Contador'
            call = asyncio.create_task(manager.call('contador', 'status', {}, caller='iris'))
            while not entered.is_set():
                await asyncio.sleep(0.01)

            async def uninstall_route():  # Core's route: uninstall, then cancel the app's notices
                await manager.uninstall('contador')
                notices.forget_app('contador')

            uninstall = asyncio.create_task(uninstall_route())
            await asyncio.sleep(0.5)
            assert not uninstall.done(), "the uninstall waits for the notice being written"
            release.set()
            answer = await call
            assert answer['notices']['scheduled'] == 1
            await uninstall
        finally:
            release.set()
            await manager.close()
    asyncio.run(scenario())
    assert notices.next_due() is None, "no reminder survives the uninstall"


@pytest.mark.parametrize("entry", ["backend", "window"])
def test_a_cancelled_write_keeps_the_uninstall_waiting_until_the_thread_ends(tmp_path, monkeypatch, entry):
    """Codex SDK03-R1: the action's timeout (or a closed request) cancels the call, not the thread
    writing the notice. The lock stays taken until that thread ends, so the cleanup comes after."""
    import threading
    import time

    from core.app_actions import AppActionRegistry
    from core.app_extensions import AppExtensions
    from tests.test_app_extensions import package

    notices = store(tmp_path)
    entered, release = threading.Event(), threading.Event()
    real_apply = app_notices.apply_requests

    def held_apply(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return real_apply(*args, **kwargs)

    def window_write(name):  # what the route's work does, held while writing
        entered.set()
        assert release.wait(10)
        return notices.post("contador", name, checked_request({"title": "Luego", "at": time.time() + 3600}, now=time.time()))

    monkeypatch.setattr(app_notices, "apply_requests", held_apply)
    backend = ('import time\n'
               'def handle(action, args, context):\n'
               '    return {"ok": True, "iris_notices": [{"title": "Luego", "text": "", "at": time.time() + 3600, "key": "r"}]}\n')

    async def scenario():
        manager = AppExtensions(lambda: tmp_path / 'iris', registry=AppActionRegistry(),
                                hermes_home=lambda: tmp_path / 'hermes', notices=lambda: notices)
        writer = uninstall = None
        try:
            installed = await manager.install(package(backend=backend), expected=None)
            if entry == "backend":
                writer = asyncio.create_task(manager.call('contador', 'status', {}, caller='iris'))
            else:
                writer = asyncio.create_task(manager.for_installation('contador', installed['installation'], window_write))
            while not entered.is_set():
                await asyncio.sleep(0.01)
            writer.cancel()

            async def uninstall_route():  # Core's route: uninstall, then cancel the app's notices
                await manager.uninstall('contador')
                notices.forget_app('contador')

            uninstall = asyncio.create_task(uninstall_route())
            await asyncio.sleep(0.5)
            assert manager.lock.locked() and not writer.done(), "the cancelled call holds the lock while its thread writes"
            assert not uninstall.done()
            writer.cancel()  # cancelling again changes nothing
            await asyncio.sleep(0.1)
            assert not writer.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await writer  # the cancellation still arrives, after the write
            await uninstall
        finally:
            release.set()
            for task in (writer, uninstall):
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
            await manager.close()
    asyncio.run(scenario())
    assert notices.next_due() is None, "no reminder survives the uninstall"
