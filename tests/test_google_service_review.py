"""F10 review regressions: real service/store, synthetic provider, disposable profiles."""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time

import pytest

from core.google_connections import GoogleConnectionsService, GoogleServiceError
from core.google_store import GoogleStore, SCHEMA_VERSION
from test_google_connections import FakeWorker, connect, outcome, service, until
from test_google_sync import CAL_LIST, full_calendar, setup, event, line, page, finish


def test_parallel_initial_claims_are_idempotent_with_one_real_lease(tmp_path):
    async def scenario():
        svc, _ = service(tmp_path)
        real_acquire = svc._lease.acquire
        active = 0
        peak = 0
        guard = threading.Lock()

        def slow_acquire():
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            try:
                time.sleep(0.03)
                real_acquire()
            finally:
                with guard:
                    active -= 1

        svc._lease.acquire = slow_acquire
        try:
            answers = await asyncio.gather(*(svc.start_session(["gmail.read"], request_id="one-click-123") for _ in range(4)))
            assert len({item["id"] for item in answers}) == 1
            assert peak == 1
            other = GoogleConnectionsService(svc.home, worker=FakeWorker())
            try:
                with pytest.raises(GoogleServiceError, match="Otra instancia"):
                    await other.start_session(["gmail.read"], request_id="other-click-123")
            finally:
                await other.close()
        finally:
            await svc.close()
    asyncio.run(scenario())


def test_snapshot_cannot_migrate_a_store_owned_by_another_service(tmp_path):
    async def scenario():
        home = tmp_path / "profile"
        home.mkdir()
        db = GoogleStore.for_profile(home)
        with sqlite3.connect(db.path) as conn:
            conn.execute("DROP TABLE collections")
            conn.execute("DROP TABLE items")
            conn.execute("PRAGMA user_version = 1")
        conn.close()
        owner = GoogleConnectionsService(home, worker=None)
        observer = GoogleConnectionsService(home, worker=None)
        owner._lease.acquire()
        try:
            before = db.path.read_bytes()
            with pytest.raises(GoogleServiceError) as error:
                await observer.snapshot()
            assert error.value.code == "service_busy"
            assert db.path.read_bytes() == before
            with sqlite3.connect(db.path) as conn:
                assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
            conn.close()
            await owner.close()
            assert (await observer.snapshot())["accounts"] == []
            with sqlite3.connect(db.path) as conn:
                assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            conn.close()
        finally:
            await owner.close()
            await observer.close()
    asyncio.run(scenario())


def test_cancel_and_core_close_share_one_cancellation(tmp_path):
    async def scenario():
        svc, worker = service(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        async def close():
            entered.set()
            await release.wait()
        real_begin = worker.begin_authorization
        async def begin(**kwargs):
            handle = await real_begin(**kwargs)
            handle.close = close
            return handle
        worker.begin_authorization = begin
        session = await svc.start_session(["gmail.read"], request_id="one-click-123")
        await until(lambda: bool(worker.handles))
        cancelling = asyncio.create_task(svc.cancel_session(session["id"]))
        await asyncio.wait_for(entered.wait(), 2)
        closing = asyncio.create_task(svc.close())
        await asyncio.sleep(0.02)
        assert not cancelling.done() and not closing.done()
        release.set()
        final = await cancelling
        await closing
        assert final["state"] == "cancelled"
        assert svc._sessions[session["id"]].task.cancelling() == 1
    asyncio.run(scenario())


def test_parallel_sync_requests_keep_one_worker_and_disconnect_collects_it(tmp_path):
    async def scenario():
        svc, worker, store, account_id, _ = setup(tmp_path)
        full_calendar(worker, event("one"))
        worker.gates[(*CAL_LIST, 0)] = asyncio.Event()
        real_collection = store.collection
        entered, release = threading.Event(), threading.Event()
        once = False
        guard = threading.Lock()

        def collection(*args):
            nonlocal once
            with guard:
                first = not once
                once = True
            if first:
                entered.set()
                assert release.wait(3)
            return real_collection(*args)

        store.collection = collection
        try:
            first = asyncio.create_task(svc.sync(account_id, ["calendar.read"]))
            await until(entered.is_set)
            second = asyncio.create_task(svc.sync(account_id, ["calendar.read"]))
            await asyncio.sleep(0.02)
            release.set()
            await asyncio.gather(first, second)
            await until(lambda: bool(worker.calls))
            assert worker.calls == [CAL_LIST]
            await svc.disconnect(account_id)
            assert worker.closed == [CAL_LIST]
            assert account_id not in svc._sync_tasks
        finally:
            release.set()
            await svc.close()
    asyncio.run(scenario())


def test_gmail_requested_during_calendar_runs_after_it_without_a_lost_request(tmp_path):
    async def scenario():
        svc, worker, store, account_id, _ = setup(tmp_path)
        full_calendar(worker, event("one"))
        gate = worker.gates[(*CAL_LIST, 0)] = asyncio.Event()
        mail_key = ("sync.gmail", None, None, None)
        worker.script[mail_key] = [page({"id": "mail-one"}), line("result", public={
            "checkpoint": "100", "coverage": {"complete": True, "messages": 1}, "full": True})]
        try:
            await svc.sync(account_id, ["calendar.read"])
            await until(lambda: bool(worker.calls))
            state = await svc.sync_state(account_id)
            assert state["calendar.read"]["state"] == "running"
            assert state["gmail.read"]["state"] == "never", "no phantom Gmail read"
            await svc.sync(account_id, ["gmail.read"])
            assert (await svc.sync_state(account_id))["gmail.read"]["state"] == "running", "queued work is tracked"
            gate.set()
            await finish(svc, account_id)
            assert mail_key in worker.calls
            assert store.items(account_id, "gmail.read", "mailbox")[0][0] == "mail-one"
            assert worker.closed == worker.calls
        finally:
            gate.set()
            await svc.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("forget_row", [False, True])
def test_new_consent_waits_for_the_previous_remote_revocation(tmp_path, forget_row):
    async def scenario():
        worker = FakeWorker()
        worker.revoke_gate = asyncio.Event()
        svc, _ = service(tmp_path, worker)
        try:
            initial = await connect(svc, worker, outcome())
            account_id = initial["result_account_id"]
            await svc.disconnect(account_id, revoke_remote=True)
            await until(lambda: bool(worker.revokes))
            if forget_row:
                await svc.forget(account_id)
            with pytest.raises(GoogleServiceError) as error:
                await svc.start_session(["gmail.read"], request_id="next-click-123")
            assert error.value.code == "revocation_pending"
            assert len(worker.handles) == 1, "no new OAuth while the old remote effect is pending"
            worker.revoke_gate.set()
            await svc.drain()
            result = await connect(svc, worker, outcome(), request_id="last-click-123")
            assert result["state"] == "committed"
        finally:
            worker.revoke_gate.set()
            await svc.close()
    asyncio.run(scenario())


def test_new_owner_marks_interrupted_revocation_unknown_without_replaying_it(tmp_path):
    async def scenario():
        svc, worker = service(tmp_path)
        initial = await connect(svc, worker, outcome())
        store = svc._store_sync(False)
        store.disconnect(initial["result_account_id"], revoke_remote=True)
        await svc.close()  # simulate a previous owner with no recorded remote outcome
        new_worker = FakeWorker()
        new = GoogleConnectionsService(svc.home, worker=new_worker)
        try:
            account = (await new.snapshot())["accounts"][0]
            assert account["auth_state"] == "disconnected"
            assert account["remote_revocation"] == "unknown"
            assert new_worker.revokes == []
        finally:
            await new.close()
    asyncio.run(scenario())


def test_core_close_collects_explicit_refresh_before_releasing_the_profile(tmp_path):
    async def scenario():
        svc, worker = service(tmp_path)
        initial = await connect(svc, worker, outcome())
        account_id = initial["result_account_id"]
        worker.refresh_gate = asyncio.Event()
        refreshing = asyncio.create_task(svc.refresh_credential(account_id))
        await until(lambda: bool(worker.refreshes))
        await svc.close()
        assert refreshing.done()
        assert (await asyncio.gather(refreshing, return_exceptions=True))[0].__class__ is asyncio.CancelledError
        other = GoogleConnectionsService(svc.home, worker=FakeWorker())
        try:
            assert (await other.snapshot())["accounts"][0]["auth_state"] == "connected"
            assert other._store_sync(False).credential(account_id).credential == b"BLOB-ANA"
            with pytest.raises(GoogleServiceError):
                await svc.refresh_credential(account_id)
        finally:
            await other.close()
    asyncio.run(scenario())
