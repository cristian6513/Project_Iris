"""Regressions for worker ownership, UTC expiry and server-directed retry delays.

Children are local synthetic processes. No OAuth client file or Google account is used.
"""

from __future__ import annotations

import asyncio
import datetime
import importlib.util
import random
import sys
import types
from pathlib import Path

import pytest

import core.google_worker_client as client_module
from core.google_sync import backoff_delay
from core.google_worker_client import GoogleWorkerClient, _Job


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("google_worker_review_target", ROOT / "core/google_worker.py")
worker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = worker
spec.loader.exec_module(worker)


@pytest.mark.parametrize("real_worker", [False, True])
@pytest.mark.parametrize("cancellations", [1, 2, 4])
def test_cancel_during_process_delivery_waits_for_collection(monkeypatch, cancellations, real_worker):
    """A repeated cancel cannot abandon a process whose creation already reached the OS."""
    private_python = ROOT / "artifacts/a08-9/v0.9.2-qa11/runtime/runtimes/hermes/python.exe"
    if real_worker and not private_python.is_file():
        pytest.skip("no packaged Hermes Python here")
    command = ([str(private_python), "-B", str(ROOT / "core/google_worker.py")] if real_worker else
               [sys.executable, "-c", "import time; time.sleep(30)"])

    async def scenario():
        ready, deliver = asyncio.Event(), asyncio.Event()
        real_create = asyncio.create_subprocess_exec
        children = []

        async def delayed_create(*args, **kwargs):
            process = await real_create(*args, **kwargs)
            children.append(process)
            ready.set()
            await deliver.wait()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_create)
        client = GoogleWorkerClient(client=None, command=lambda: command, env=lambda: None)
        task = asyncio.create_task(client._spawn())
        try:
            await asyncio.wait_for(ready.wait(), 15)
            for _ in range(cancellations):
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done(), "cancellation must retain ownership until process delivery and collection"
            deliver.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), 15)
            assert children[0].returncode is not None, "the child must be waited before cancellation returns"
        finally:
            deliver.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            for process in children:
                if process.returncode is None:
                    process.kill()
                await process.wait()

    asyncio.run(scenario())


def test_job_close_waits_through_repeated_cancellation_and_shares_cleanup(monkeypatch):
    async def scenario():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(30)", stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        job = _Job(process, "synthetic")
        real_wait = process.wait
        collected, deliver_wait = asyncio.Event(), asyncio.Event()
        kills = []
        real_kill = process.kill

        def kill():
            kills.append(True)
            real_kill()

        async def delayed_wait():
            code = await real_wait()
            collected.set()
            await deliver_wait.wait()
            return code

        monkeypatch.setattr(process, "wait", delayed_wait)
        monkeypatch.setattr(process, "kill", kill)
        monkeypatch.setattr(client_module, "CLOSE_GRACE_S", 0.02)
        first = asyncio.create_task(job.close())
        second = asyncio.create_task(job.close())
        try:
            await asyncio.wait_for(collected.wait(), 15)
            for _ in range(3):
                first.cancel()
                await asyncio.sleep(0)
            assert not first.done() and not second.done(), "both callers wait for the same complete collection"
            deliver_wait.set()
            with pytest.raises(asyncio.CancelledError):
                await first
            await second
            assert process.returncode is not None and kills == [True]
        finally:
            deliver_wait.set()
            await asyncio.gather(first, second, return_exceptions=True)
            if process.returncode is None:
                real_kill()
            await real_wait()

    asyncio.run(scenario())


@pytest.mark.parametrize(("header", "minimum"), [
    ("600", 600), ("7200", 7200), (" 86400 ", 86400),
    ("Thu, 15 Oct 2026 14:00:00 GMT", 7200),
    ("Thu, 15 Oct 2026 10:00:00 GMT", 0),
])
def test_http_retry_after_keeps_remote_minimum_in_both_formats(monkeypatch, header, minimum):
    now = datetime.datetime(2026, 10, 15, 12, tzinfo=datetime.timezone.utc).timestamp()
    monkeypatch.setattr(worker.time, "time", lambda: now)
    response = type("Response", (dict,), {"status": 429})({"retry-after": header})
    error = types.SimpleNamespace(resp=response, content=b'{"error":{"errors":[{"reason":"rateLimitExceeded"}]}}')
    result = worker._http_error(error, gone_means_expired=False)
    assert result.code == "rate_limited" and result.retry_after == minimum
    delay = backoff_delay(1, result.retry_after, rng=random.Random(0))
    assert delay >= minimum
    if minimum > 3600:
        assert delay == minimum, "our backoff cap must never shorten a server deadline"


@pytest.mark.parametrize("header", [None, "", "tomorrow", "-20", "inf", "NaN", "9" * 400])
def test_invalid_retry_after_uses_ordinary_backoff(header):
    assert worker._retry_after(header) is None
    assert 24 <= backoff_delay(1, worker._retry_after(header), rng=random.Random(0)) <= 36


@pytest.mark.parametrize("aware", [False, True])
def test_explicit_refresh_persists_utc_expiry_independently_of_local_zone(monkeypatch, aware):
    """Exercise credential_refresh, including a UTC-naive library value on a non-UTC host."""
    class Expiry(datetime.datetime):
        def timestamp(self):
            if self.tzinfo is None:
                # Emulate Windows/Bogota even when this test runs on a UTC machine.
                return self.replace(tzinfo=datetime.timezone.utc).timestamp() + 18000
            return super().timestamp()

    offset = datetime.timezone(datetime.timedelta(hours=-5))
    expiry = Expiry(2026, 10, 15, 12) if not aware else Expiry(2026, 10, 15, 7, tzinfo=offset)

    class Credentials:
        token = "synthetic-new-token"
        refresh_token = None

        def __init__(self, **kwargs):
            self.expiry = expiry

        def refresh(self, request):
            pass

    class RefreshError(Exception):
        pass

    exceptions = types.ModuleType("google.auth.exceptions")
    exceptions.RefreshError = RefreshError
    exceptions.TransportError = type("TransportError", (Exception,), {})
    transport = types.ModuleType("google.auth.transport.requests")
    transport.Request = lambda: object()
    credentials = types.ModuleType("google.oauth2.credentials")
    credentials.Credentials = Credentials
    for module in (exceptions, transport, credentials):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    saved = []
    monkeypatch.setattr(worker, "unprotect", lambda blob, key: {
        "client_id": "synthetic", "refresh_token": "old-refresh", "scopes": []})
    monkeypatch.setattr(worker, "protect", lambda document, key: saved.append(document) or b"protected")
    monkeypatch.setattr(worker, "_key", lambda private: b"profile")
    request = {"id": "review", "private": {"credential": worker.contracts.encode_credential(b"old"),
                                              "client": {"client_id": "synthetic", "client_secret": "synthetic"}}}
    output = []
    worker.credential_refresh(request, output.append)
    expected = datetime.datetime(2026, 10, 15, 12, tzinfo=datetime.timezone.utc).timestamp()
    assert saved[0]["expiry"] == expected
    assert saved[0]["refresh_token"] == "old-refresh" and output[0]["event"] == "refreshed"
