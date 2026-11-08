"""F10.3: the service's read rounds (staging, promotion, backoff, races), with a scripted worker."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.google_api import create_google_router
from core.google_connections import GoogleConnectionsService
from core.google_contracts import requested_scopes
from core.google_store import AuthorizationCandidate
from core.google_sync import CALENDARS, MAILBOX, SUMMARY

RPC = "iris.google-rpc/v1"


def line(event: str, **fields) -> dict:
    return {"schema": RPC, "id": "job1", "event": event, **fields}


def page(*records, deleted=()) -> dict:
    return line("page", public={"records": [dict(record) for record in records], "deleted": list(deleted)})


def event(event_id: str, **fields) -> dict:
    return {"id": event_id, "status": "confirmed", **fields}


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


class ScriptedWorker:
    """Answers each round from a script: key → list of lines, or an exception to raise."""

    available = True

    def __init__(self) -> None:
        self.script: dict[tuple, list] = {}
        self.calls: list[tuple] = []
        self.closed: list[tuple] = []
        self.gates: dict[tuple, asyncio.Event] = {}

    @staticmethod
    def key(method: str, params: dict) -> tuple:
        return (method, params.get("calendar_id"), params.get("sync_token"), params.get("checkpoint"))

    def read_round(self, method, credential, *, protection, params, timeout):
        key = self.key(method, params)
        self.calls.append(key)
        lines = self.script[key]

        async def stream():
            try:
                for index, item in enumerate(lines):
                    gate = self.gates.get((*key, index))
                    if gate is not None:
                        await gate.wait()
                    yield item
            finally:
                self.closed.append(key)  # the round's worker is collected

        return stream()

    async def begin_authorization(self, **kwargs):  # pragma: no cover - not used here
        raise AssertionError

    async def refresh(self, credential, *, protection):  # pragma: no cover
        raise AssertionError

    async def revoke(self, credential, *, protection):
        return "confirmed"


def setup(tmp_path: Path, *, services=("calendar.read", "gmail.read"), subject="sub-ana"):
    home = tmp_path / "perfil"
    home.mkdir(exist_ok=True)
    clock = Clock()
    worker = ScriptedWorker()
    service = GoogleConnectionsService(home, worker=worker, clock=clock)
    store = service._store_sync(True)
    record = store.commit_authorization(AuthorizationCandidate(
        client_id="cliente.apps.googleusercontent.com", issuer="https://accounts.google.com", subject=subject,
        email="ana@example.com", requested_scopes=requested_scopes(services), granted_scopes=requested_scopes(services),
        credential=b"BLOB"))
    return service, worker, store, record.id, clock


CAL_LIST = ("sync.calendar_list", None, None, None)


def full_calendar(worker: ScriptedWorker, *events, token="tok-1", calendar="ana@example.com") -> None:
    worker.script[CAL_LIST] = [page({"id": calendar, "summary": "Ana"}), line("result", public={"complete": True})]
    worker.script[("sync.calendar_events", calendar, None, None)] = [
        page(*events), line("result", public={"sync_token": token, "full": True})]


async def finish(service: GoogleConnectionsService, account_id: str) -> None:
    task = service._sync_tasks.get(account_id)
    if task is not None:
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 10)


def test_a_round_promotes_its_records_and_checkpoint_together(tmp_path) -> None:
    async def scenario():
        service, worker, store, account_id, _ = setup(tmp_path, services=("calendar.read",))
        full_calendar(worker, event("e1", summary="Uno"), event("e2", summary="Dos"))
        await service.sync(account_id)
        await finish(service, account_id)
        collection = "events:ana@example.com"
        assert store.collection(account_id, "calendar.read", collection).checkpoint == "tok-1"
        assert [item_id for item_id, _ in store.items(account_id, "calendar.read", collection)] == ["e1", "e2"]
        state = (await service.sync_state(account_id))["calendar.read"]
        assert state["state"] == "idle" and state["records"] == 2 and state["coverage"] == {"calendars": 1}
        assert state["last_success_at"] is not None
        snapshot = json.dumps(await service.snapshot())
        assert "Uno" not in snapshot and "tok-1" not in snapshot, "numbers only, never content or cursors"
        assert worker.closed == worker.calls, "every round's worker was collected"
        await service.close()

    asyncio.run(scenario())


def test_a_failure_after_the_first_page_confirms_nothing(tmp_path) -> None:
    async def scenario():
        service, worker, store, account_id, clock = setup(tmp_path, services=("calendar.read",))
        full_calendar(worker, event("e1"))
        await service.sync(account_id)
        await finish(service, account_id)
        worker.script[("sync.calendar_events", "ana@example.com", "tok-1", None)] = [
            page(event("e9", summary="Nuevo")), line("error", public={"code": "provider_unavailable"})]
        clock.now += 10
        await service.sync(account_id)
        await finish(service, account_id)
        collection = "events:ana@example.com"
        assert store.collection(account_id, "calendar.read", collection).checkpoint == "tok-1", "not advanced"
        assert [item_id for item_id, _ in store.items(account_id, "calendar.read", collection)] == ["e1"], "page 1 not kept"
        summary = store.collection(account_id, "calendar.read", SUMMARY)
        assert summary.state == "backoff" and summary.error_code == "provider_unavailable"
        assert summary.next_attempt_at > clock.now
        await service.close()

    asyncio.run(scenario())


def test_google_asking_for_a_full_read_replaces_the_calendar(tmp_path) -> None:
    async def scenario():
        service, worker, store, account_id, clock = setup(tmp_path, services=("calendar.read",))
        full_calendar(worker, event("viejo"), event("sigue"))
        await service.sync(account_id)
        await finish(service, account_id)
        worker.script[("sync.calendar_events", "ana@example.com", "tok-1", None)] = [line("error", public={"code": "sync_expired"})]
        worker.script[("sync.calendar_events", "ana@example.com", None, None)] = [
            page(event("sigue"), event("otro")), line("result", public={"sync_token": "tok-9", "full": True})]
        clock.now += 10
        await service.sync(account_id)
        await finish(service, account_id)
        collection = "events:ana@example.com"
        assert [item_id for item_id, _ in store.items(account_id, "calendar.read", collection)] == ["otro", "sigue"]
        assert store.collection(account_id, "calendar.read", collection).checkpoint == "tok-9"
        await service.close()

    asyncio.run(scenario())


def test_a_calendar_no_longer_listed_goes_with_its_events(tmp_path) -> None:
    async def scenario():
        service, worker, store, account_id, clock = setup(tmp_path, services=("calendar.read",))
        full_calendar(worker, event("e1"), calendar="viejo@group.calendar.google.com")
        await service.sync(account_id)
        await finish(service, account_id)
        full_calendar(worker, event("e2"))
        clock.now += 10
        await service.sync(account_id)
        await finish(service, account_id)
        names = {state.collection for state in store.collections(account_id, "calendar.read")}
        assert names == {CALENDARS, SUMMARY, "events:ana@example.com"}
        assert store.items(account_id, "calendar.read", "events:viejo@group.calendar.google.com") == []
        await service.close()

    asyncio.run(scenario())


def test_an_expired_mail_history_rebuilds_the_window(tmp_path) -> None:
    async def scenario():
        service, worker, store, account_id, clock = setup(tmp_path, services=("gmail.read",))
        coverage = {"window_days": 30, "max_messages": 200, "complete": True, "messages": 2}
        worker.script[("sync.gmail", None, None, None)] = [
            page({"id": "m1"}, {"id": "m2"}), line("result", public={"checkpoint": "1000", "full": True, "coverage": coverage})]
        await service.sync(account_id)
        await finish(service, account_id)
        assert store.collection(account_id, "gmail.read", MAILBOX).checkpoint == "1000"
        worker.script[("sync.gmail", None, None, "1000")] = [line("error", public={"code": "sync_expired"})]
        worker.script[("sync.gmail", None, None, None)] = [
            page({"id": "m3"}), line("result", public={"checkpoint": "2000", "full": True, "coverage": {**coverage, "messages": 1}})]
        clock.now += 10
        await service.sync(account_id)
        await finish(service, account_id)
        assert [item_id for item_id, _ in store.items(account_id, "gmail.read", MAILBOX)] == ["m3"]
        assert store.collection(account_id, "gmail.read", MAILBOX).checkpoint == "2000"
        assert (await service.sync_state(account_id))["gmail.read"]["coverage"]["messages"] == 1
        await service.close()

    asyncio.run(scenario())


def test_backoff_holds_reading_until_its_time_and_honours_retry_after(tmp_path) -> None:
    async def scenario():
        service, worker, store, account_id, clock = setup(tmp_path, services=("calendar.read",))
        worker.script[CAL_LIST] = [line("error", public={"code": "rate_limited", "retry_after": 600})]
        await service.sync(account_id)
        await finish(service, account_id)
        summary = store.collection(account_id, "calendar.read", SUMMARY)
        assert summary.state == "backoff" and summary.next_attempt_at >= clock.now + 600
        calls = len(worker.calls)
        state = await service.sync(account_id)
        await finish(service, account_id)
        assert len(worker.calls) == calls and state["calendar.read"]["state"] == "backoff"
        assert state["calendar.read"]["next_attempt_at"] is not None
        clock.now += 601
        full_calendar(worker, event("e1"))
        await service.sync(account_id)
        await finish(service, account_id)
        assert store.collection(account_id, "calendar.read", SUMMARY).state == "idle"
        await service.close()

    asyncio.run(scenario())


def test_a_second_request_joins_the_running_round(tmp_path) -> None:
    async def scenario():
        service, worker, store, account_id, _ = setup(tmp_path, services=("calendar.read",))
        full_calendar(worker, event("e1"))
        gate = worker.gates[(*CAL_LIST, 0)] = asyncio.Event()
        first = await service.sync(account_id)
        second = await service.sync(account_id)
        assert first["calendar.read"]["state"] == second["calendar.read"]["state"] == "running"
        gate.set()
        await finish(service, account_id)
        assert worker.calls.count(CAL_LIST) == 1
        await service.close()

    asyncio.run(scenario())


def test_disconnecting_during_a_read_stops_it_and_nothing_lands(tmp_path) -> None:
    async def scenario():
        service, worker, store, account_id, _ = setup(tmp_path, services=("calendar.read",))
        full_calendar(worker, event("e1"))
        events_key = ("sync.calendar_events", "ana@example.com", None, None)
        gate = worker.gates[(*events_key, 1)] = asyncio.Event()  # after the first page
        await service.sync(account_id)
        while events_key not in worker.calls:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.02)
        disconnected = await service.disconnect(account_id, clear_cache=False)
        assert disconnected["auth_state"] == "disconnected"
        assert events_key in worker.closed, "the round's worker was collected when it was stopped"
        gate.set()
        await asyncio.sleep(0.05)
        assert store.items(account_id, "calendar.read", "events:ana@example.com") == []
        await service.close()

    asyncio.run(scenario())


def test_a_token_renewed_during_a_read_is_kept(tmp_path) -> None:
    async def scenario():
        service, worker, store, account_id, _ = setup(tmp_path, services=("calendar.read",))
        renewed = base64.b64encode(b"RENOVADA").decode()
        worker.script[CAL_LIST] = [line("refreshed", private={"credential": renewed}),
                                   page({"id": "ana@example.com"}), line("result", public={"complete": True})]
        worker.script[("sync.calendar_events", "ana@example.com", None, None)] = [
            line("result", public={"sync_token": "tok-1", "full": True})]
        before = store.credential(account_id)
        await service.sync(account_id)
        await finish(service, account_id)
        after = store.credential(account_id)
        assert after.credential == b"RENOVADA" and after.credential_revision == before.credential_revision + 1
        await service.close()

    asyncio.run(scenario())


def test_google_refusing_the_credential_mid_read_asks_to_reconnect(tmp_path) -> None:
    async def scenario():
        service, worker, store, account_id, _ = setup(tmp_path, services=("calendar.read",))
        worker.script[CAL_LIST] = [line("error", public={"code": "reauth_required"})]
        await service.sync(account_id)
        await finish(service, account_id)
        assert store.account(account_id).state == "reauth_required"
        await service.close()

    asyncio.run(scenario())


def test_two_accounts_are_read_independently(tmp_path) -> None:
    async def scenario():
        service, worker, store, ana, _ = setup(tmp_path, services=("calendar.read",))
        bea = store.commit_authorization(AuthorizationCandidate(
            client_id="cliente.apps.googleusercontent.com", issuer="https://accounts.google.com", subject="sub-bea",
            email="bea@example.com", requested_scopes=requested_scopes(["gmail.read"]),
            granted_scopes=requested_scopes(["gmail.read"]), credential=b"BLOB-B")).id
        full_calendar(worker, event("e1"))
        gate = worker.gates[(*CAL_LIST, 0)] = asyncio.Event()  # Ana's round waits
        worker.script[("sync.gmail", None, None, None)] = [
            page({"id": "m1"}), line("result", public={"checkpoint": "5", "full": True, "coverage": {"messages": 1}})]
        await service.sync(ana)
        await service.sync(bea)
        await finish(service, bea)
        assert [item_id for item_id, _ in store.items(bea, "gmail.read", MAILBOX)] == ["m1"], "Bea did not wait for Ana"
        assert store.items(ana, "gmail.read", MAILBOX) == [], "nothing crossed accounts"
        gate.set()
        await finish(service, ana)
        await service.close()

    asyncio.run(scenario())


def test_periodic_reading_covers_only_enabled_services_when_due(tmp_path) -> None:
    async def scenario():
        service, worker, store, account_id, clock = setup(tmp_path)
        assert await service.tick() == [], "nothing enabled: nothing read"
        record = store.account(account_id)
        store.set_enabled_services(account_id, ["calendar.read"], expected_revision=record.revision)
        full_calendar(worker, event("e1"))
        assert await service.tick() == [account_id]
        await finish(service, account_id)
        assert not any(call[0] == "sync.gmail" for call in worker.calls), "Gmail was not enabled"
        assert await service.tick() == [], "not due yet"
        worker.script[("sync.calendar_events", "ana@example.com", "tok-1", None)] = [
            line("result", public={"sync_token": "tok-2", "full": False})]
        clock.now += service.periodic_interval_s + 1
        assert await service.tick() == [account_id]
        await finish(service, account_id)
        await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("bad", [{"summary": "sin id"}, {"id": "x" * 600}, {"id": "e1", "description": "x" * 40000}])
def test_records_from_the_worker_are_checked_again(tmp_path, bad) -> None:
    async def scenario():
        service, worker, store, account_id, _ = setup(tmp_path, services=("calendar.read",))
        full_calendar(worker, bad)
        await service.sync(account_id)
        await finish(service, account_id)
        assert store.items(account_id, "calendar.read", "events:ana@example.com") == []
        assert store.collection(account_id, "calendar.read", SUMMARY).error_code == "provider_unavailable"
        await service.close()

    asyncio.run(scenario())


def test_reading_over_http_is_local_and_returns_numbers(tmp_path) -> None:
    service, worker, store, account_id, _ = setup(tmp_path, services=("calendar.read",))
    full_calendar(worker, event("e1", summary="Privado"))
    app = FastAPI()
    app.include_router(create_google_router(lambda: service))
    local = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    remote = TestClient(app, base_url="http://127.0.0.1", client=("192.168.1.20", 50000))
    path = f"/api/connections/google/accounts/{account_id}/sync"
    assert remote.post(path, json={}).status_code == 403 and remote.get(path).status_code == 403
    started = local.post(path, json={"services": ["calendar.read"]})
    assert started.status_code == 202 and started.headers["cache-control"] == "no-store, private"
    assert local.post(path, json={"services": ["gmail.read"]}).status_code == 409, "not granted"
    state = local.get(path).json()
    assert set(state) == {"calendar.read"} and "Privado" not in json.dumps(state)
