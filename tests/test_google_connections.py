"""F10.1: the Google connections service and its local HTTP contract, with a fake worker.

No network, browser, account or real OAuth client: the worker is a fake that answers what each
test scripts, and the store lives in a disposable profile.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.google_api import create_google_router
from core.google_connections import (
    AuthorizationOutcome,
    GoogleConnectionsService,
    GoogleServiceError,
    RefreshOutcome,
)
from core.google_contracts import SERVICE_SCOPES, requested_scopes
from core.google_store import GoogleStore, profile_store_path

URL = "https://accounts.google.com/o/oauth2/v2/auth?client_id=sintetico&state=s"
SUBJECT = "sub-sintetico-ana"
CLIENT = "cliente-sintetico.apps.example"


class FakeHandle:
    def __init__(self) -> None:
        self.url = URL
        self.expires_at = time.time() + 300
        self.answer: asyncio.Future = asyncio.get_running_loop().create_future()
        self.closed = 0
        self.stubborn = False  # a misbehaving worker that ignores being cancelled

    async def result(self) -> AuthorizationOutcome:
        while True:
            try:
                return await asyncio.shield(self.answer)  # a late answer can still arrive after close
            except asyncio.CancelledError:
                if not self.stubborn:
                    raise

    async def close(self) -> None:
        self.closed += 1


class FakeWorker:
    available = True

    def __init__(self) -> None:
        self.handles: list[FakeHandle] = []
        self.spawning: asyncio.Event | None = None  # set: begin_authorization waits for it
        self.refreshes: list[bytes] = []
        self.refresh_gate: asyncio.Event | None = None
        self.refresh_answer = lambda credential: RefreshOutcome(credential=credential + b"+")
        self.revokes: list[bytes] = []
        self.revoke_gate: asyncio.Event | None = None
        self.revoke_answer = "confirmed"

    async def begin_authorization(self, *, session_id: str, scopes: tuple[str, ...], protection: bytes) -> FakeHandle:
        assert len(protection) == 32
        if self.spawning is not None:
            await self.spawning.wait()
        handle = FakeHandle()
        handle.scopes = scopes
        self.handles.append(handle)
        return handle

    async def refresh(self, credential: bytes, *, protection: bytes) -> RefreshOutcome:
        self.refreshes.append(credential)
        if self.refresh_gate is not None:
            await self.refresh_gate.wait()
        return self.refresh_answer(credential)

    async def revoke(self, credential: bytes, *, protection: bytes) -> str:
        self.revokes.append(credential)
        if self.revoke_gate is not None:
            await self.revoke_gate.wait()
        return self.revoke_answer


def granted(*services: str, extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    return (*requested_scopes(services or ("calendar.read",)), *extra)


def outcome(scopes=None, *, subject: str = SUBJECT, blob: bytes = b"BLOB-ANA", error: str = "") -> AuthorizationOutcome:
    if error:
        return AuthorizationOutcome(error=error)
    return AuthorizationOutcome(client_id=CLIENT, issuer="https://accounts.google.com", subject=subject,
                                email="ana@example.com", granted_scopes=scopes or granted("calendar.read", "gmail.read"),
                                credential=blob)


def service(tmp_path: Path, worker: FakeWorker | None = None, **kwargs) -> tuple[GoogleConnectionsService, FakeWorker]:
    worker = worker or FakeWorker()
    (tmp_path / "perfil").mkdir(exist_ok=True)
    return GoogleConnectionsService(tmp_path / "perfil", worker=worker, **kwargs), worker


async def until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


async def connect(svc: GoogleConnectionsService, worker: FakeWorker, answer: AuthorizationOutcome,
                  services=("calendar.read", "gmail.read"), request_id: str = "click-0001", account_id=None) -> dict:
    session = await svc.start_session(list(services), request_id=request_id, account_id=account_id)
    await until(lambda: svc.session(session["id"])["state"] != "starting")
    worker.handles[-1].answer.set_result(answer)
    await until(lambda: svc.session(session["id"])["state"] not in ("awaiting_user", "validating"))
    return svc.session(session["id"])


def test_connecting_stores_the_account_and_shows_only_public_facts(tmp_path) -> None:
    async def scenario():
        svc, worker = service(tmp_path)
        assert not profile_store_path(tmp_path / "perfil").exists()
        assert (await svc.snapshot())["accounts"] == []
        assert not (tmp_path / "perfil" / "connections").exists(), "reading creates nothing"

        session = await svc.start_session(["calendar.read"], request_id="click-0001")
        assert session["state"] == "starting" and session["authorization_url"] is None
        await until(lambda: svc.session(session["id"])["state"] == "awaiting_user")
        assert svc.session(session["id"])["authorization_url"] == URL
        assert worker.handles[0].scopes == requested_scopes(["calendar.read"]), "only what was chosen"
        worker.handles[0].answer.set_result(outcome(granted("calendar.read")))
        await until(lambda: svc.session(session["id"])["state"] == "committed")
        final = svc.session(session["id"])
        assert final["authorization_url"] is None and worker.handles[0].closed >= 1

        snapshot = await svc.snapshot()
        account = snapshot["accounts"][0]
        assert account["id"] == final["result_account_id"] and account["auth_state"] == "connected"
        assert account["services"]["calendar.read"] == {"granted": True, "enabled": False, "availability": "not_checked"}
        assert account["services"]["gmail.read"]["availability"] == "not_requested"
        text = json.dumps(snapshot)
        assert SUBJECT not in text and CLIENT not in text and "BLOB" not in text
        await svc.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(("answer", "state", "code"), [
    (outcome(granted("calendar.read")), "partial", ""),
    (outcome(error="denied"), "denied", "denied"),
    (outcome(error="state_mismatch"), "error", "state_mismatch"),
    (outcome(granted("calendar.read", "gmail.read", extra=("https://www.googleapis.com/auth/gmail.modify",))),
     "error", "unexpected_grant"),
    (outcome(("openid", "email")), "error", "permission_missing"),
    (outcome(("email", *SERVICE_SCOPES["gmail.read"])), "error", "identity_unverified"),
    (outcome(error="whatever the provider said"), "error", "provider_unavailable"),
])
def test_each_answer_from_google_ends_honestly(tmp_path, answer, state, code) -> None:
    async def scenario():
        svc, worker = service(tmp_path)
        final = await connect(svc, worker, answer)
        assert (final["state"], (final["error"] or {}).get("code", "")) == (state, code)
        accounts = (await svc.snapshot())["accounts"]
        assert len(accounts) == (1 if state == "partial" else 0)
        if state == "partial":
            assert accounts[0]["auth_state"] == "partial"
            assert accounts[0]["services"]["gmail.read"]["availability"] == "permission_missing"
        await svc.close()

    asyncio.run(scenario())


def test_a_failed_reconnection_keeps_the_previous_connection(tmp_path) -> None:
    async def scenario():
        svc, worker = service(tmp_path)
        first = await connect(svc, worker, outcome())
        account_id = first["result_account_id"]
        store = GoogleStore.for_profile(tmp_path / "perfil")
        for answer, code in ((outcome(subject="sub-otra", blob=b"OTRA"), "account_changed"),
                             (outcome(granted("calendar.read", "gmail.read", extra=("https://www.googleapis.com/auth/drive",)),
                                      blob=b"AMPLIA"), "unexpected_grant")):
            final = await connect(svc, worker, answer, request_id=f"again-{code}"[:30], account_id=account_id)
            assert (final["state"], final["error"]["code"]) == ("error", code)
            assert store.credential(account_id).credential == b"BLOB-ANA", "the old connection is intact"
        added = await connect(svc, worker, outcome(blob=b"DUPLICADA"), request_id="add-the-same")
        assert added["error"]["code"] == "account_exists"
        assert len((await svc.snapshot())["accounts"]) == 1
        await svc.close()

    asyncio.run(scenario())


def test_a_consent_url_that_is_not_google_is_never_shown(tmp_path) -> None:
    async def scenario():
        worker = FakeWorker()
        real_begin = worker.begin_authorization

        async def evil_begin(**kwargs):
            handle = await real_begin(**kwargs)
            handle.url = "https://accounts.google.com.evil.example/o/oauth2/v2/auth"
            return handle

        worker.begin_authorization = evil_begin
        svc, _ = service(tmp_path, worker)
        session = await svc.start_session(["gmail.read"], request_id="click-0001")
        await until(lambda: svc.session(session["id"])["state"] == "error")
        final = svc.session(session["id"])
        assert final["authorization_url"] is None and final["error"]["code"] == "provider_unavailable"
        assert worker.handles[0].closed >= 1
        await svc.close()

    asyncio.run(scenario())


def test_one_authorization_at_a_time_and_a_double_click_is_the_same_one(tmp_path) -> None:
    async def scenario():
        svc, worker = service(tmp_path)
        first = await svc.start_session(["gmail.read"], request_id="click-0001")
        again = await svc.start_session(["gmail.read"], request_id="click-0001")
        assert again["id"] == first["id"]
        for services, request_id in ((["gmail.read"], "click-0002"), (["calendar.read"], "click-0001")):
            with pytest.raises(GoogleServiceError) as refused:
                await svc.start_session(services, request_id=request_id)
            assert refused.value.code == "busy"
        await until(lambda: len(worker.handles) == 1)
        assert len(worker.handles) == 1, "one worker for one click"
        await svc.close()

    asyncio.run(scenario())


def test_one_deadline_from_the_start_and_the_listener_is_closed(tmp_path) -> None:
    async def scenario():
        svc, worker = service(tmp_path, authorization_timeout_s=0.2)
        session = await svc.start_session(["gmail.read"], request_id="click-0001")
        await until(lambda: svc.session(session["id"])["state"] == "expired")
        assert worker.handles[0].closed >= 1
        worker.handles[0].answer.set_result(outcome())  # Google answers after the deadline
        await asyncio.sleep(0.05)
        assert (await svc.snapshot())["accounts"] == []
        await svc.close()

    asyncio.run(scenario())


def test_cancelling_while_the_worker_starts_leaves_nothing_running(tmp_path) -> None:
    async def scenario():
        worker = FakeWorker()
        worker.spawning = asyncio.Event()
        svc, _ = service(tmp_path, worker)
        session = await svc.start_session(["gmail.read"], request_id="click-0001")
        await asyncio.sleep(0.01)
        cancelled = await svc.cancel_session(session["id"])
        assert cancelled["state"] == "cancelled" and cancelled["error"]["code"] == "cancelled"
        worker.spawning.set()
        await asyncio.sleep(0.05)
        assert worker.handles == [], "the start was cancelled: no listener was left behind"
        assert svc.session(session["id"])["state"] == "cancelled"
        await svc.close()

    asyncio.run(scenario())


def test_cancelling_while_waiting_closes_first_and_a_late_answer_is_never_stored(tmp_path) -> None:
    async def scenario():
        svc, worker = service(tmp_path)
        session = await svc.start_session(["gmail.read"], request_id="click-0001")
        await until(lambda: svc.session(session["id"])["state"] == "awaiting_user")
        cancelled = await svc.cancel_session(session["id"])
        assert cancelled["state"] == "cancelled" and worker.handles[0].closed >= 1, "closed before confirming"
        worker.handles[0].answer.set_result(outcome())  # the browser finishes anyway
        await asyncio.sleep(0.05)
        assert (await svc.snapshot())["accounts"] == []
        assert (await svc.cancel_session(session["id"]))["state"] == "cancelled", "idempotent"
        await svc.close()

    asyncio.run(scenario())


def test_cancel_waits_for_a_protected_start_to_be_collected(tmp_path) -> None:
    """A process being created cannot be interrupted (F10.2): cancelled meanwhile, the worker
    finishes creating it and then collects it. «Cancelled» is reported only after that."""
    async def scenario():
        worker = FakeWorker()
        worker.spawning = asyncio.Event()
        collected: list[str] = []

        async def protected_begin(**kwargs):
            try:
                await worker.spawning.wait()
            except asyncio.CancelledError:
                await worker.spawning.wait()  # the creation completes...
                collected.append("child")  # ...and the child is collected before giving up
                raise
            return await FakeWorker.begin_authorization(worker, **kwargs)

        worker.begin_authorization = protected_begin
        svc, _ = service(tmp_path, worker)
        session = await svc.start_session(["gmail.read"], request_id="click-0001")
        await asyncio.sleep(0.01)
        cancelling = asyncio.create_task(svc.cancel_session(session["id"]))
        await asyncio.sleep(0.05)
        assert not cancelling.done(), "not cancelled while the child may still exist"
        worker.spawning.set()
        result = await cancelling
        assert result["state"] == "cancelled" and collected == ["child"]
        await svc.close()

    asyncio.run(scenario())


def test_a_worker_that_ignores_the_cancel_still_cannot_commit(tmp_path) -> None:
    async def scenario():
        svc, worker = service(tmp_path)
        session = await svc.start_session(["gmail.read"], request_id="click-0001")
        await until(lambda: svc.session(session["id"])["state"] == "awaiting_user")
        handle = worker.handles[0]
        handle.stubborn = True  # it keeps waiting for Google after the cancel
        store = svc._store_sync(True)
        commits: list = []
        real = store.commit_authorization
        store.commit_authorization = lambda *args, **kwargs: commits.append(1) or real(*args, **kwargs)
        cancelling = asyncio.create_task(svc.cancel_session(session["id"]))
        await asyncio.sleep(0.05)
        handle.answer.set_result(outcome(granted("gmail.read")))  # the answer arrives after the cancel
        result = await cancelling
        assert result["state"] == "cancelled" and commits == [], "the confirmed cancel holds"
        assert (await svc.snapshot())["accounts"] == []
        await svc.close()

    asyncio.run(scenario())


def test_cancel_against_commit_when_the_commit_is_already_running(tmp_path) -> None:
    async def scenario():
        svc, worker = service(tmp_path)
        session = await svc.start_session(["gmail.read"], request_id="click-0001")
        await until(lambda: svc.session(session["id"])["state"] == "awaiting_user")
        store = svc._store_sync(True)
        real, entered, release = store.commit_authorization, threading.Event(), threading.Event()

        def held(*args, **kwargs):
            entered.set()
            release.wait(10)
            return real(*args, **kwargs)

        store.commit_authorization = held
        worker.handles[0].answer.set_result(outcome(granted("gmail.read")))
        assert await asyncio.to_thread(entered.wait, 10)
        cancelling = asyncio.create_task(svc.cancel_session(session["id"]))
        await asyncio.sleep(0.05)
        assert not cancelling.done(), "cancel waits for the commit it raced"
        release.set()
        result = await cancelling
        assert result["state"] == "committed", "the commit won: cancel reports what really happened"
        assert len((await svc.snapshot())["accounts"]) == 1
        await svc.close()

    asyncio.run(scenario())


def test_cancel_against_commit_when_cancel_is_first(tmp_path) -> None:
    async def scenario():
        svc, worker = service(tmp_path)
        session = await svc.start_session(["gmail.read"], request_id="click-0001")
        await until(lambda: svc.session(session["id"])["state"] == "awaiting_user")
        store = svc._store_sync(True)
        commits: list = []
        real = store.commit_authorization
        store.commit_authorization = lambda *args, **kwargs: commits.append(1) or real(*args, **kwargs)

        await svc._arbiter.acquire()  # hold the arbiter: cancel queues first, then the answer arrives
        try:
            cancelling = asyncio.create_task(svc.cancel_session(session["id"]))
            await asyncio.sleep(0.01)
            worker.handles[0].answer.set_result(outcome(granted("gmail.read")))
            await until(lambda: svc.session(session["id"])["state"] == "validating")
            await asyncio.sleep(0.01)  # the commit is now queued behind the cancel
        finally:
            svc._arbiter.release()
        result = await cancelling
        await asyncio.sleep(0.05)
        assert result["state"] == "cancelled" and commits == [], "confirmed cancel: no late commit"
        assert (await svc.snapshot())["accounts"] == []
        await svc.close()

    asyncio.run(scenario())


def _connected(tmp_path, worker=None):
    svc, worker = service(tmp_path, worker)

    async def setup():
        final = await connect(svc, worker, outcome(blob=b"C1"))
        return final["result_account_id"]

    return svc, worker, setup


def test_two_renewals_of_one_account_run_one_after_the_other(tmp_path) -> None:
    async def scenario():
        svc, worker, setup = _connected(tmp_path)
        account_id = await setup()
        worker.refresh_gate = asyncio.Event()
        both = asyncio.gather(svc.refresh_credential(account_id), svc.refresh_credential(account_id))
        await until(lambda: len(worker.refreshes) == 1)
        await asyncio.sleep(0.05)
        assert len(worker.refreshes) == 1, "the second waits for the first"
        worker.refresh_gate.set()
        await both
        assert worker.refreshes == [b"C1", b"C1+"], "the second renewed what the first stored"
        ref = svc._store_sync(False).credential(account_id)
        assert ref.credential == b"C1++" and ref.credential_revision == 3
        await svc.close()

    asyncio.run(scenario())


def test_a_disconnection_during_a_renewal_wins(tmp_path) -> None:
    async def scenario():
        svc, worker, setup = _connected(tmp_path)
        account_id = await setup()
        worker.refresh_gate = asyncio.Event()
        renewing = asyncio.create_task(svc.refresh_credential(account_id))
        await until(lambda: len(worker.refreshes) == 1)
        record = await svc.disconnect(account_id)
        assert record["auth_state"] == "disconnected"
        worker.refresh_gate.set()
        after = await renewing
        assert after["auth_state"] == "disconnected"
        store = svc._store_sync(False)
        assert store.credential(account_id) is None and not store.account(account_id).has_credential, \
            "the late renewal did not revive it"
        await svc.close()

    asyncio.run(scenario())


def test_google_refusing_the_credential_asks_to_reconnect(tmp_path) -> None:
    async def scenario():
        svc, worker, setup = _connected(tmp_path)
        account_id = await setup()
        worker.refresh_answer = lambda credential: RefreshOutcome(error="offline")
        with pytest.raises(GoogleServiceError) as offline:
            await svc.refresh_credential(account_id)
        assert offline.value.code == "offline"
        assert (await svc.snapshot())["accounts"][0]["auth_state"] == "connected", "offline is not reauth"
        worker.refresh_answer = lambda credential: RefreshOutcome(error="reauth_required")
        assert (await svc.refresh_credential(account_id))["auth_state"] == "reauth_required"
        assert svc._store_sync(False).credential(account_id) is None, "not used for reads any more"
        await svc.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(("answer", "outcome_state", "kept"), [
    ("confirmed", "confirmed", False),
    ("failed", "failed", True),
    ("never", "unknown", True),  # the worker does not answer in time
])
def test_revoking_in_google_is_separate_and_its_outcome_is_recorded(tmp_path, answer, outcome_state, kept) -> None:
    async def scenario():
        worker = FakeWorker()
        if answer == "never":
            worker.revoke_gate = asyncio.Event()
        else:
            worker.revoke_answer = answer
        svc, _, setup = _connected(tmp_path, worker)
        svc.revoke_timeout_s = 0.1
        account_id = await setup()
        record = await svc.disconnect(account_id, revoke_remote=True)
        assert record["auth_state"] == "disconnected" and record["remote_revocation"] == "pending"
        await svc.drain()
        after = (await svc.snapshot())["accounts"][0]
        assert after["remote_revocation"] == outcome_state
        assert worker.revokes == [b"C1"]
        assert svc._store_sync(False).account(account_id).has_credential is kept
        await svc.close()

    asyncio.run(scenario())


def test_one_service_per_profile(tmp_path) -> None:
    async def scenario():
        first, worker = service(tmp_path)
        second, _ = service(tmp_path, FakeWorker())
        await connect(first, worker, outcome())
        with pytest.raises(GoogleServiceError) as refused:
            await second.start_session(["gmail.read"], request_id="click-0002")
        assert refused.value.code == "service_busy"
        # Opening a store can migrate it; an observer cannot bypass its current owner.
        with pytest.raises(GoogleServiceError) as observer:
            await second.snapshot()
        assert observer.value.code == "service_busy"
        await first.close()
        third, _ = service(tmp_path, FakeWorker())
        await third.start_session(["gmail.read"], request_id="click-0003")  # the lease was released
        await third.close()
        await second.close()

    asyncio.run(scenario())


def test_without_a_worker_nothing_starts(tmp_path) -> None:
    async def scenario():
        (tmp_path / "perfil").mkdir()
        svc = GoogleConnectionsService(tmp_path / "perfil", worker=None)
        assert (await svc.snapshot())["available"] is False
        with pytest.raises(GoogleServiceError) as refused:
            await svc.start_session(["gmail.read"], request_id="click-0001")
        assert refused.value.code == "client_unavailable"
        assert not (tmp_path / "perfil" / "connections").exists()

    asyncio.run(scenario())


# ── HTTP ──────────────────────────────────────────────────────────────


def _app(svc: GoogleConnectionsService | None) -> FastAPI:
    app = FastAPI()
    app.include_router(create_google_router(lambda: svc))
    return app


ROUTES = [
    ("get", "/api/connections/google", None),
    ("post", "/api/connections/google/sessions", {"services": ["gmail.read"], "request_id": "click-0001"}),
    ("get", "/api/connections/google/sessions/gses_" + "0" * 32, None),
    ("post", "/api/connections/google/sessions/gses_" + "0" * 32 + "/cancel", None),
    ("patch", "/api/connections/google/accounts/gacc_" + "0" * 32 + "/services", {"services": [], "revision": 1}),
    ("post", "/api/connections/google/accounts/gacc_" + "0" * 32 + "/disconnect", {}),
    ("delete", "/api/connections/google/accounts/gacc_" + "0" * 32, None),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_route_answers_only_to_this_pc(tmp_path, method, path, body) -> None:
    svc, _ = service(tmp_path)
    app = _app(svc)
    for peer in ("192.168.1.20", "10.0.0.5", "testclient"):
        remote = TestClient(app, base_url="http://127.0.0.1", client=(peer, 50000))
        response = getattr(remote, method)(path, **({"json": body} if body is not None else {}))
        assert response.status_code == 403, (peer, path)
    assert not (tmp_path / "perfil" / "connections").exists(), "a refused peer changed nothing"


def test_the_local_contract_answers_without_secrets_or_cache(tmp_path) -> None:
    worker = FakeWorker()
    svc, _ = service(tmp_path, worker)
    local = TestClient(_app(svc), base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    listed = local.get("/api/connections/google")
    assert listed.status_code == 200 and listed.headers["cache-control"] == "no-store, private"
    assert listed.json() == {"schema": "iris.google-connections/v1", "available": True, "accounts": [], "session": None}

    started = local.post("/api/connections/google/sessions", json={"services": ["gmail.read"], "request_id": "click-0001"})
    assert started.status_code == 202
    session_id = started.json()["id"]
    assert local.post("/api/connections/google/sessions",
                      json={"services": ["gmail.read"], "request_id": "click-0002"}).status_code == 409
    assert local.post("/api/connections/google/sessions",
                      json={"services": ["gmail.read"], "request_id": "click-0003", "path": "x"}).status_code == 422
    assert local.get("/api/connections/google/sessions/gses_" + "f" * 32).status_code == 404
    cancelled = local.post(f"/api/connections/google/sessions/{session_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
    assert local.post("/api/connections/google/accounts/gacc_" + "0" * 32 + "/disconnect", json={}).status_code == 404
    assert _app(None) and TestClient(_app(None), client=("127.0.0.1", 1)).get("/api/connections/google").status_code == 503


def test_forgetting_takes_only_a_disconnected_account_and_a_late_revocation_finds_nothing(tmp_path) -> None:
    async def scenario():
        worker = FakeWorker()
        worker.revoke_gate = asyncio.Event()
        svc, _, setup = _connected(tmp_path, worker)
        account_id = await setup()
        with pytest.raises(GoogleServiceError) as refused:
            await svc.forget(account_id)
        assert refused.value.code == "still_connected"
        await svc.disconnect(account_id, revoke_remote=True)
        assert (await svc.forget(account_id)) == {"id": account_id, "forgotten": True}
        assert (await svc.snapshot())["accounts"] == []
        worker.revoke_gate.set()  # the revocation answers after the account is gone
        await svc.drain()
        assert (await svc.snapshot())["accounts"] == []
        with pytest.raises(GoogleServiceError) as missing:
            await svc.forget(account_id)
        assert missing.value.code == "not_found"
        await svc.close()

    asyncio.run(scenario())
