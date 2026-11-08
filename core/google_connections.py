"""F10: the one owner of a profile's Google connections.

``GoogleConnectionsService`` is the only writer of ``core/google_store.py`` and the only caller of
the Google worker. Apps (Conexiones, then Calendario and Correo) and future Hermes reads go
through it; none of them holds a credential.

* **One owner per profile.** The service holds ``connections/google/service.lock`` exclusively
  from its first write until it closes; a second service on the same profile (another Core,
  another version) is refused (``service_busy``). Core's profile-writer lease does not give this:
  it admits several compatible writers.
* **One authorization at a time.** A repeated start with the same ``request_id`` returns the
  same session; a different one while it is pending is ``busy``. Each session has one deadline,
  from its start. A session never outlives Core.
* **Cancel against commit.** Both take the same lock: if the commit happened first, cancelling
  returns the real outcome; if cancelling was confirmed first, no late answer is stored.
  Cancelling waits until the worker's handle (child process, loopback listener) is closed.
* **Refresh.** One renewal per account at a time; each reads the latest credential and stores
  its result only over the credential it renewed. A disconnection meanwhile wins.
* **Disconnect** changes the generation before waiting for anything, so late work cannot revive
  the account. Revoking in Google is a separate, explicit request with its own recorded outcome.
* **Reading (F10.3).** «Actualizar datos» reads the granted services of one account; a second
  request while it runs joins it. One round per account at a time (under its lock) and a small
  number at once overall. Each round is promoted, records and checkpoint together, only for the
  generation it started from (see ``core/google_sync.py``); a failure records its state and,
  if transient, a backoff the next request respects. Periodic reading only covers the services
  the person enabled. Nothing read is sent to Hermes or to the model.

The worker is a protocol: F10.2 provides the real one (Hermes' private Python); tests use fakes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from core.google_contracts import (
    ERRORS,
    GOOGLE_ISSUERS,
    RpcError,
    decode_credential,
    iso,
    PUBLIC_SCHEMA,
    SERVICES,
    SESSION_ACTIVE,
    SESSION_TERMINAL,
    ContractError,
    evaluate_grant,
    is_account_id,
    is_request_id,
    new_session_id,
    normalize_scopes,
    public_session,
    requested_scopes,
    valid_authorization_url,
    validate_services,
)
from core.google_store import (
    AuthorizationCandidate,
    CredentialRef,
    GoogleStore,
    GoogleStoreError,
    StaleGeneration,
    UnknownAccount,
    profile_store_path,
)
from core.google_sync import (
    CALENDARS,
    GMAIL_MAX_MESSAGES,
    GMAIL_WINDOW_DAYS,
    MAILBOX,
    MAX_CONCURRENT_ROUNDS,
    PERIODIC_INTERVAL_S,
    ROUND_TIMEOUT_S,
    SUMMARY,
    TRANSIENT,
    Staging,
    SyncError,
    backoff_delay,
    events_collection,
)

MAX_CALENDAR_CACHE_RECORDS = 50_000
MAX_MAIL_CACHE_RECORDS = 50_000
from core.profile_sqlite import plain_file

logger = logging.getLogger(__name__)

AUTHORIZATION_TIMEOUT_S = 600.0
REFRESH_TIMEOUT_S = 60.0
REVOKE_TIMEOUT_S = 30.0
_KEEP_SESSIONS = 20


class GoogleServiceError(RuntimeError):
    """A refused request; ``code`` is one of the contract's error codes."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or ERRORS.get(code, code))
        self.code = code


# ── The worker, as the service sees it ────────────────────────────────


@dataclass(frozen=True)
class AuthorizationOutcome:
    """What the worker got back from Google: an error code, or a verified identity and its grant."""

    error: str = ""
    client_id: str = ""
    issuer: str = ""
    subject: str = ""
    email: str = ""
    granted_scopes: tuple[str, ...] = ()
    credential: bytes = b""

    def __repr__(self) -> str:
        return f"AuthorizationOutcome(error={self.error!r}, scopes={len(self.granted_scopes)})"


@dataclass(frozen=True)
class RefreshOutcome:
    """A renewed credential (None: unchanged), or an error code (``reauth_required``, ``offline``…)."""

    credential: bytes | None = None
    error: str = ""


class AuthorizationHandle(Protocol):
    url: str  # the consent page, already listened for
    expires_at: float  # wall-clock epoch

    async def result(self) -> AuthorizationOutcome: ...

    async def close(self) -> None:
        """Stop the listener and collect the child; idempotent; returns once they are gone."""


class GoogleWorker(Protocol):
    available: bool

    async def begin_authorization(self, *, session_id: str, scopes: tuple[str, ...],
                                  protection: bytes) -> AuthorizationHandle:
        """Start listening and return the consent URL. Cancelled, it leaves nothing running."""

    async def refresh(self, credential: bytes, *, protection: bytes) -> RefreshOutcome: ...

    async def revoke(self, credential: bytes, *, protection: bytes) -> str:
        """``confirmed``, ``failed`` or ``unknown``."""

    def read_round(self, method: str, credential: bytes, *, protection: bytes, params: dict,
                   timeout: float):
        """An async iterator of the round's lines until its ``result`` or ``error`` (F10.3)."""


# ── The service ───────────────────────────────────────────────────────


@dataclass
class _Session:
    id: str
    request_id: str
    services: tuple[str, ...]
    account_id: str | None
    expected_generation: int | None
    deadline: float  # loop time
    expires_at: float  # wall clock
    state: str = "starting"
    error: str = ""
    url: str | None = None
    result_account_id: str | None = None
    cancel_requested: bool = False
    handle: Any = None
    task: asyncio.Task | None = None
    cancel_task: asyncio.Task | None = None
    finished_at: float = field(default=0.0)

    def public(self) -> dict[str, Any]:
        return public_session(
            session_id=self.id, state=self.state, services=self.services, account_id=self.account_id,
            authorization_url=self.url, expires_at=self.expires_at, error_code=self.error,
            result_account_id=self.result_account_id,
        )


class _ServiceLease:
    """``connections/google/service.lock``: one service per profile, across processes."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            return
        from core.google_store import _SPEC

        folder = profile_store_path(self.home).parent
        folder.mkdir(parents=True, exist_ok=True)
        folder = profile_store_path(self.home).parent  # still a plain part of the profile once created
        path = folder / "service.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            plain_file(path, _SPEC)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise GoogleServiceError("service_busy") from None
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class GoogleConnectionsService:
    def __init__(
        self,
        home: Path,
        *,
        worker: GoogleWorker | None,
        clock: Callable[[], float] = time.time,
        authorization_timeout_s: float = AUTHORIZATION_TIMEOUT_S,
        refresh_timeout_s: float = REFRESH_TIMEOUT_S,
        revoke_timeout_s: float = REVOKE_TIMEOUT_S,
        client_configurable: bool = False,
        client_source: str = "none",
    ) -> None:
        self.home = Path(home)
        self._worker = worker
        self._client_configurable = client_configurable
        self._client_source = client_source
        self._client_loaded = not client_configurable
        self._client_lock = asyncio.Lock()
        self._client_revision = 0
        self._client_problem = False
        self._clock = clock
        self.authorization_timeout_s = authorization_timeout_s
        self.refresh_timeout_s = refresh_timeout_s
        self.revoke_timeout_s = revoke_timeout_s
        self._lease = _ServiceLease(self.home)
        self._store_obj: GoogleStore | None = None
        self._store_lock = asyncio.Lock()
        self._close_task: asyncio.Task | None = None
        self._arbiter = asyncio.Lock()  # sessions: start, cancel and commit
        self._account_locks: dict[str, asyncio.Lock] = {}
        self._sessions: dict[str, _Session] = {}
        self._tasks: set[asyncio.Task] = set()
        self._refresh_tasks: set[asyncio.Task] = set()
        self._mail_tasks: dict[asyncio.Task, str] = {}
        self._closed = False
        self._sync_tasks: dict[str, asyncio.Task] = {}
        self._sync_control = asyncio.Lock()
        self._sync_pending: dict[str, set[str]] = {}
        self._sync_active: dict[str, str] = {}
        self._sync_generations: dict[str, int] = {}
        self._rounds = asyncio.Semaphore(MAX_CONCURRENT_ROUNDS)
        self._rng = random.Random()
        self.round_timeout_s = ROUND_TIMEOUT_S
        self.gmail_window_days = GMAIL_WINDOW_DAYS
        self.gmail_max_messages = GMAIL_MAX_MESSAGES
        self.periodic_interval_s = PERIODIC_INTERVAL_S

    # ── Plumbing ────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._worker is not None and bool(getattr(self._worker, "available", True))

    def _store_sync(self, create: bool) -> GoogleStore | None:
        if self._store_obj is None:
            if not create and not profile_store_path(self.home).exists():
                return None  # reading never creates anything
            # Constructing a store can migrate it. Even a snapshot must own the lease first.
            self._lease.acquire()
            try:
                self._store_obj = GoogleStore.for_profile(self.home, clock=self._clock)
                # A previous owner ended without recording its remote result. Never replay it.
                for record in self._store_obj.accounts():
                    if record.remote_revocation == "pending":
                        self._store_obj.record_revocation(record.id,
                            revocation_generation=record.revocation_generation, outcome="unknown")
            except BaseException:
                self._store_obj = None
                self._lease.release()
                raise
        return self._store_obj

    @staticmethod
    async def _settle(task: asyncio.Task):
        """Collect owned work before propagating cancellation, including repeated cancellation."""
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def _store(self, *, create: bool) -> GoogleStore | None:
        try:
            async with self._store_lock:
                if self._closed:
                    raise GoogleServiceError("client_unavailable")
                return await self._settle(asyncio.create_task(asyncio.to_thread(self._store_sync, create)))
        except GoogleStoreError as exc:
            raise GoogleServiceError("client_unavailable", str(exc)) from exc

    async def _claim(self) -> GoogleStore:
        """Become the profile's owner (once) and open the store for writing."""
        if self._closed:
            raise GoogleServiceError("client_unavailable")
        store = await self._store(create=True)
        assert store is not None
        return store

    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Wait for background work (revocations). For shutdown and tests."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await self._settle(self._close_task)

    async def _close(self) -> None:
        self._closed = True
        for session in list(self._sessions.values()):
            # A terminal answer can still be collecting its child in _run's finally block.
            await self.cancel_session(session.id)
        async with self._sync_control:
            await self._stop_mail_locked()
            for account_id in list(self._sync_tasks):
                await self._stop_sync_locked(account_id)
        refreshes = list(self._refresh_tasks)
        for task in refreshes:
            task.cancel()
        await asyncio.gather(*refreshes, return_exceptions=True)
        await self.drain()
        async with self._store_lock:
            self._lease.release()

    # ── Reads ───────────────────────────────────────────────────────────

    async def _ensure_client(self) -> None:
        if self._client_loaded:
            return
        async with self._client_lock:
            if self._client_loaded:
                return
            from core.google_client_config import open_client
            try:
                store = await self._store(create=False)
                revision, blob = await self._settle(asyncio.create_task(asyncio.to_thread(store.oauth_client))) if store else (0, None)
                self._client_revision = revision
                if revision:
                    protection = await self._settle(asyncio.create_task(asyncio.to_thread(store.protection_key))) if blob else b''
                    client = open_client(blob, protection) if blob else None
                    self._worker.configure_client(client)
                    self._client_source = 'personal' if client else 'none'
                self._client_loaded = True
            except asyncio.CancelledError:
                raise
            except Exception:
                self._worker.configure_client(None)
                self._client_source = 'none'
                self._client_problem = True
                self._client_loaded = True

    def _client_busy(self) -> bool:
        return (any(s.state in SESSION_ACTIVE or (s.task is not None and not s.task.done()) for s in self._sessions.values())
                or any(not task.done() for task in [*self._sync_tasks.values(), *self._refresh_tasks, *self._tasks, *self._mail_tasks]))

    async def client_configuration(self) -> dict:
        if not self._client_configurable:
            raise GoogleServiceError('client_unavailable')
        await self._ensure_client()
        store = await self._store(create=False)
        accounts = await asyncio.to_thread(store.accounts) if store else []
        blocked = self._client_busy() or any(a.state != 'disconnected' or a.remote_revocation == 'pending' for a in accounts)
        configured = self._client_source != 'none' and not self._client_problem
        return {'configured': configured, 'source': self._client_source, 'revision': self._client_revision,
                'can_change': not blocked, 'error': 'client_unavailable' if self._client_problem else None,
                'shared_available': False}

    async def configure_client(self, json_text: str | None, *, revision: int) -> dict:
        if not self._client_configurable or self._closed:
            raise GoogleServiceError('client_unavailable')
        from core.google_client_config import parse_client, protect_client
        if type(revision) is not int or revision < 0:
            raise GoogleServiceError('invalid_request')
        try:
            client = parse_client(json_text) if json_text is not None else None
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise GoogleServiceError('client_invalid') from None
        await self._ensure_client()
        async with self._sync_control:
            async with self._arbiter:
                async with self._client_lock:
                    if self._closed:
                        raise GoogleServiceError('client_unavailable')
                    if self._client_busy():
                        raise GoogleServiceError('client_in_use')
                    store = await self._claim()
                    try:
                        def save():
                            protection = store.protection_key()
                            blob = protect_client(client, protection) if client else None
                            return store.set_oauth_client(blob, expected_revision=revision)
                        result = await self._settle(asyncio.create_task(asyncio.to_thread(save)))
                    except asyncio.CancelledError:
                        # Saving may have committed; reload its durable value on the next call.
                        self._client_loaded = False
                        raise
                    except GoogleStoreError as exc:
                        raise GoogleServiceError(exc.code) from None
                    except Exception:
                        raise GoogleServiceError('client_unavailable') from None
                    self._worker.configure_client(client)
                    self._client_revision = result
                    self._client_source = 'personal' if client else 'none'
                    self._client_problem = False
                    self._client_loaded = True
        return await self.client_configuration()

    async def snapshot(self) -> dict[str, Any]:
        """Accounts and the pending authorization. Never renews a credential or reads Google."""
        await self._ensure_client()
        store = await self._store(create=False)
        accounts = []
        for record in (await asyncio.to_thread(store.accounts) if store else []):
            account = record.public()
            account["sync"] = await self._sync_state(store, record.id, record.granted_services)
            accounts.append(account)
        active = next((s for s in self._sessions.values() if s.state in SESSION_ACTIVE), None)
        result = {"schema": PUBLIC_SCHEMA, "available": self.available, "accounts": accounts,
                  "session": active.public() if active else None}
        if self._client_configurable:
            result['client'] = await self.client_configuration()
        return result

    def session(self, session_id: str) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if session is None:
            raise GoogleServiceError("not_found")
        return session.public()

    async def calendar_cache(self) -> dict[str, Any]:
        """F11's bounded local projection. Never starts OAuth, sync or token renewal.

        Use this owner's store, not a second connection-service instance. Disconnect
        and authorization commits wait for the projection; cancellation collects the
        thread before releasing those guards. No credential or sync cursor escapes.
        """
        store = await self._store(create=False)
        if store is None:
            return {"available": self.available, "accounts": [], "calendars": [], "warnings": []}
        await self._ensure_client()
        async with self._sync_control:
            async with self._arbiter:
                if self._closed:
                    raise GoogleServiceError("client_unavailable")
                busy = {key for key, task in self._sync_tasks.items()
                        if not task.done() and (self._sync_active.get(key) == "calendar.read"
                                               or "calendar.read" in self._sync_pending.get(key, ()))}
                return await self._settle(asyncio.create_task(asyncio.to_thread(
                    self._calendar_cache_sync, store, busy)))

    def _calendar_cache_sync(self, store: GoogleStore, busy: set[str]) -> dict[str, Any]:
        accounts, calendars, warnings = [], [], []
        remaining = MAX_CALENDAR_CACHE_RECORDS

        def state(collection):
            return {"state": collection.state if collection else "never",
                    "last_success_at": iso(collection.last_success_at) if collection else None,
                    "next_attempt_at": iso(collection.next_attempt_at) if collection else None,
                    "error": ({"code": collection.error_code, "message": ERRORS.get(collection.error_code, "")}
                              if collection and collection.error_code else None),
                    "coverage": dict(collection.coverage) if collection else {}}

        def records(account_id, collection):
            nonlocal remaining
            rows, after = [], ""
            while remaining:
                requested = min(1000, remaining)
                page = store.items(account_id, "calendar.read", collection, after=after, limit=requested)
                if not page:
                    return rows
                rows.extend(document for _, document in page)
                remaining -= len(page)
                after = page[-1][0]
                if len(page) < requested:
                    return rows
            if store.items(account_id, "calendar.read", collection, after=after, limit=1):
                if not any(warning["code"] == "cache_limit" for warning in warnings):
                    warnings.append({"code": "cache_limit"})
            return rows

        for record in store.accounts():
            account = record.public()
            summary = state(store.collection(record.id, "calendar.read", SUMMARY))
            if record.id in busy:
                summary["state"] = "running"
            account["sync"] = {"calendar.read": summary} if "calendar.read" in record.granted_services else {}
            accounts.append(account)
            if record.state not in ("connected", "partial") or "calendar.read" not in record.granted_services:
                continue
            own = []
            for calendar in records(record.id, CALENDARS):
                try:
                    collection = events_collection(calendar.get("id"))
                except (SyncError, AttributeError):
                    warnings.append({"code": "invalid_calendar", "account_id": record.id})
                    continue
                own.append({"account_id": record.id, "calendar": calendar,
                            "sync": state(store.collection(record.id, "calendar.read", collection)),
                            "events": records(record.id, collection)})
            latest = store.account(record.id)
            if (latest is None or latest.generation != record.generation
                    or latest.state not in ("connected", "partial") or "calendar.read" not in latest.granted_services):
                warnings.append({"code": "account_changed", "account_id": record.id})
                accounts.pop()
                if latest is not None:
                    updated = latest.public()
                    updated["sync"] = {}
                    accounts.append(updated)
                continue
            calendars.extend(own)
        return {"available": self.available, "accounts": accounts, "calendars": calendars, "warnings": warnings}

    def mail_classifier_live(self, bindings):
        """Cache-only delivery guard, called in Inbox's thread; never opens a new owner."""
        store = self._store_obj
        if store is None or self._closed:
            raise GoogleServiceError("client_unavailable")
        eligible = set()
        for identity, binding in bindings.items():
            record = store.account(identity)
            if (record and record.state in ("connected", "partial") and "gmail.read" in record.granted_services
                    and record.generation == binding["generation"] and record.revision == binding["revision"]):
                eligible.add(identity)
        return eligible

    def mail_classifier_target_live(self, account_id, binding, message_id):
        if account_id not in self.mail_classifier_live({account_id: binding}):
            return False
        return self._store_obj.item(account_id, "gmail.read", MAILBOX, message_id) is not None

    async def mail_classifier_snapshot(self, account_ids, after=0):
        """Private F14 feed. Only the Google owner reads its store, without credentials."""
        store = await self._store(create=False)
        if store is None or self._closed:
            raise GoogleServiceError("client_unavailable")
        def read():
            accounts, messages, epochs = {}, [], {}
            for identity in account_ids:
                record = store.account(identity)
                self._check_mail_account(record)
                accounts[identity] = {"generation": record.generation, "revision": record.revision}
                epochs[identity] = store.mail_epoch(identity)
                cursor = after.get(identity, 0) if isinstance(after, dict) else after
                for row in store.mail_candidates(identity, record.generation, cursor):
                    messages.append({**row, "account_id": identity})
            return {"accounts": accounts, "frontier": store.mail_frontier(), "messages": messages, "epochs": epochs}
        async with self._sync_control:
            async with self._arbiter:
                return await self._settle(asyncio.create_task(asyncio.to_thread(read)))

    async def start_mail_classification(self, account_id, generation, revision, factory):
        """Own the entire classification until collected, including its external model call.

        Disconnect/client replacement cancels these along with body reads. Admission
        checks the exact connection for which the user enabled classification.
        """
        store = await self._store(create=False)
        if store is None:
            raise GoogleServiceError("client_unavailable")
        async with self._sync_control:
            async with self._arbiter:
                if self._closed:
                    raise GoogleServiceError("client_unavailable")
                record = await asyncio.to_thread(store.account, account_id)
                self._check_mail_account(record, generation)
                if record.revision != revision:
                    raise GoogleServiceError("account_changed")
                task = asyncio.create_task(factory())
                self._mail_tasks[task] = account_id
                task.add_done_callback(lambda finished: self._mail_tasks.pop(finished, None))
                return task

    async def start_calendar_preparation(self, account_id, generation, revision, factory):
        """F15 owns its model process in the same cancellable content-work pool.

        Account/service changes and client replacement collect the job before committing.
        Calendar permission is checked independently of Gmail permission.
        """
        store = await self._store(create=False)
        if store is None:
            raise GoogleServiceError("client_unavailable")
        async with self._sync_control:
            async with self._arbiter:
                if self._closed:
                    raise GoogleServiceError("client_unavailable")
                record = await self._settle(asyncio.create_task(asyncio.to_thread(store.account, account_id)))
                if (record is None or record.state not in ("connected", "partial")
                        or "calendar.read" not in record.granted_services
                        or record.generation != generation or record.revision != revision):
                    raise GoogleServiceError("account_changed")
                if sum(not task.done() for task in self._mail_tasks) >= MAX_CONCURRENT_ROUNDS:
                    raise GoogleServiceError("busy")
                task = asyncio.create_task(factory())
                self._mail_tasks[task] = account_id
                task.add_done_callback(lambda finished: self._mail_tasks.pop(finished, None))
                return task

    async def mail_cache(self) -> dict[str, Any]:
        """F13's cache-only view, serialized with disconnect and sync admission.

        No credential, checkpoint, Calendar data or arbitrary stored field reaches
        the consumer. The same owner/guards as Calendar collect cancelled threads.
        """
        store = await self._store(create=False)
        if store is None:
            return {"available": self.available, "accounts": [], "messages": [], "warnings": []}
        await self._ensure_client()
        async with self._sync_control:
            async with self._arbiter:
                if self._closed:
                    raise GoogleServiceError("client_unavailable")
                busy = {key for key, task in self._sync_tasks.items() if not task.done()
                        and (self._sync_active.get(key) == "gmail.read"
                             or "gmail.read" in self._sync_pending.get(key, ()))}
                return await self._settle(asyncio.create_task(asyncio.to_thread(self._mail_cache_sync, store, busy)))

    def _mail_cache_sync(self, store: GoogleStore, busy: set[str]) -> dict[str, Any]:
        accounts, messages, warnings = [], [], []
        remaining = MAX_MAIL_CACHE_RECORDS
        for record in store.accounts():
            account = record.public()
            summary = store.collection(record.id, "gmail.read", SUMMARY)
            account["services"] = {"gmail.read": account["services"]["gmail.read"]} if "gmail.read" in account["services"] else {}
            account["sync"] = {"gmail.read": {
                "state": "running" if record.id in busy else summary.state if summary else "never",
                "last_success_at": iso(summary.last_success_at) if summary else None,
                "next_attempt_at": iso(summary.next_attempt_at) if summary else None,
                "error": {"code": summary.error_code} if summary and summary.error_code else None,
                "coverage": {key: summary.coverage[key] for key in ("window_days", "max_messages", "complete", "messages", "limits_apply_to", "maintained_window")
                             if key in summary.coverage} if summary else {},
            }} if "gmail.read" in record.granted_services else {}
            accounts.append(account)
            if record.state not in ("connected", "partial") or "gmail.read" not in record.granted_services:
                continue
            own, after = [], ""
            while remaining:
                count = min(1000, remaining)
                page = store.items(record.id, "gmail.read", MAILBOX, after=after, limit=count)
                for _, item in page:
                    if not isinstance(item, dict):
                        warnings.append({"code": "invalid_message", "account_id": record.id})
                        continue
                    own.append({"account_id": record.id, "message": {
                        key: item.get(key) for key in ("id", "threadId", "labelIds", "snippet", "internalDate", "headers")}})
                remaining -= len(page)
                if not page:
                    break
                after = page[-1][0]
                if len(page) < count:
                    break
            if not remaining and store.items(record.id, "gmail.read", MAILBOX, after=after, limit=1):
                warnings.append({"code": "cache_limit", "account_id": record.id})
            latest = store.account(record.id)
            if (latest is None or latest.generation != record.generation
                    or latest.state not in ("connected", "partial") or "gmail.read" not in latest.granted_services):
                accounts.pop()
                if latest:
                    accounts.append(latest.public())
                warnings.append({"code": "account_changed", "account_id": record.id})
                continue
            messages.extend(own)
        return {"available": self.available, "accounts": accounts, "messages": messages, "warnings": warnings}

    async def mail_body(self, account_id: str, message_id: str) -> dict:
        """Explicit selection only. No persistent body cache, new OAuth, or Gmail writes.

        Admission is serialized with disconnect/client changes; the account lock
        owns credential refreshes. Cancelling the caller collects its worker.
        """
        if (not is_account_id(account_id) or not isinstance(message_id, str)
                or not 0 < len(message_id) <= 512 or any(ord(c) < 32 for c in message_id)):
            raise GoogleServiceError("invalid_request")
        await self._ensure_client()
        if not self.available:
            raise GoogleServiceError("client_unavailable")
        store = await self._store(create=False)
        if store is None:
            raise GoogleServiceError("not_found")
        async with self._sync_control:
            async with self._arbiter:
                if self._closed:
                    raise GoogleServiceError("client_unavailable")
                if sum(not task.done() for task in self._mail_tasks) >= MAX_CONCURRENT_ROUNDS:
                    raise GoogleServiceError("busy")
                record = await self._settle(asyncio.create_task(asyncio.to_thread(store.account, account_id)))
                self._check_mail_account(record)
                task = asyncio.create_task(self._read_mail_body(store, account_id, record.generation, message_id))
                self._mail_tasks[task] = account_id
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            task.cancel()
            await self._settle(asyncio.create_task(self._collect_mail([task])))
            if asyncio.current_task().cancelling():
                raise
            raise GoogleServiceError("not_connected") from None
        finally:
            self._mail_tasks.pop(task, None)

    @staticmethod
    def _check_mail_account(record, generation=None):
        if record is None or record.state not in ("connected", "partial"):
            raise GoogleServiceError("not_connected")
        if generation is not None and record.generation != generation:
            raise GoogleServiceError("account_changed")
        if "gmail.read" not in record.granted_services:
            raise GoogleServiceError("service_not_granted")

    @staticmethod
    async def _collect_mail(tasks):
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _stop_mail_locked(self, account_id=None):
        tasks = [task for task, identity in self._mail_tasks.items() if account_id is None or identity == account_id]
        for task in tasks:
            task.cancel()
        if tasks:
            await self._settle(asyncio.create_task(self._collect_mail(tasks)))

    async def _read_mail_body(self, store, account_id, generation, message_id):
        from core.mail_body import BodyError, validate_body

        async def db(function, *args, **kwargs):
            return await self._settle(asyncio.create_task(asyncio.to_thread(function, *args, **kwargs)))

        async with self._rounds:
            async with self._account_locks.setdefault(account_id, asyncio.Lock()):
                self._check_mail_account(await db(store.account, account_id), generation)
                if await db(store.item, account_id, "gmail.read", MAILBOX, message_id) is None:
                    raise GoogleServiceError("not_found")
                for name in (SUMMARY, "mail_body"):
                    summary = await db(store.collection, account_id, "gmail.read", name)
                    if summary and summary.state == "backoff" and (summary.next_attempt_at or 0) > self._clock():
                        raise GoogleServiceError("rate_limited")
                try:
                    _, result = await self._round(store, account_id, generation, "read.gmail_body", {"message_id": message_id})
                    if result.get("message_id") != message_id:
                        raise BodyError()
                    body = validate_body(result.get("body"))
                except SyncError as exc:
                    if exc.code == "reauth_required":
                        with contextlib.suppress(StaleGeneration):
                            await db(store.mark_reauth_required, account_id, generation=generation)
                    elif exc.code in TRANSIENT:
                        summary = await db(store.collection, account_id, "gmail.read", "mail_body")
                        attempts = (summary.attempts if summary else 0) + 1
                        with contextlib.suppress(StaleGeneration):
                            await db(store.record_failure, account_id, generation=generation, service="gmail.read",
                                     collection="mail_body", code=exc.code,
                                     next_attempt_at=self._clock() + backoff_delay(attempts, exc.retry_after, rng=self._rng))
                    raise GoogleServiceError(exc.code) from None
                except BodyError:
                    raise GoogleServiceError("body_unavailable") from None
                except StaleGeneration:
                    raise GoogleServiceError("account_changed") from None
                # No stale result survives reconnect/disconnect or a sync tombstone.
                async with self._arbiter:
                    self._check_mail_account(await db(store.account, account_id), generation)
                    if self._closed or await db(store.item, account_id, "gmail.read", MAILBOX, message_id) is None:
                        raise GoogleServiceError("not_found")
                    return body

    # ── Authorization ───────────────────────────────────────────────────

    async def start_session(self, services: Any, *, request_id: Any, account_id: Any = None) -> dict[str, Any]:
        """Start one authorization (an explicit local click). 409-like ``busy`` if another is pending."""
        try:
            services = validate_services(services)
        except ContractError as exc:
            raise GoogleServiceError(exc.code, str(exc)) from exc
        if not is_request_id(request_id):
            raise GoogleServiceError("invalid_request")
        if account_id is not None and not is_account_id(account_id):
            raise GoogleServiceError("invalid_request")
        await self._ensure_client()
        if not self.available:
            raise GoogleServiceError("client_unavailable")
        store = await self._claim()
        async with self._arbiter:
            if self._closed:
                raise GoogleServiceError("client_unavailable")
            for session in self._sessions.values():
                if session.request_id == request_id:
                    if (session.services, session.account_id) == (services, account_id):
                        return session.public()  # the same click, again
                    raise GoogleServiceError("busy")
                if session.state in SESSION_ACTIVE:
                    raise GoogleServiceError("busy")
            if await self._revocation_pending(store):
                raise GoogleServiceError("revocation_pending")
            expected_generation = None
            if account_id is not None:
                record = await asyncio.to_thread(store.account, account_id)
                if record is None:
                    raise GoogleServiceError("not_found")
                expected_generation = record.generation
            loop = asyncio.get_running_loop()
            session = _Session(
                id=new_session_id(), request_id=request_id, services=services, account_id=account_id,
                expected_generation=expected_generation, deadline=loop.time() + self.authorization_timeout_s,
                expires_at=self._clock() + self.authorization_timeout_s,
            )
            self._sessions[session.id] = session
            self._prune()
            protection = await asyncio.to_thread(store.protection_key)
            session.task = asyncio.create_task(self._run(session, store, protection))
            return session.public()

    def _prune(self) -> None:
        finished = sorted((s for s in self._sessions.values() if s.state in SESSION_TERMINAL),
                          key=lambda s: s.finished_at)
        for session in finished[:max(0, len(self._sessions) - _KEEP_SESSIONS)]:
            self._sessions.pop(session.id, None)

    def _finish(self, session: _Session, state: str, error: str = "") -> None:
        if session.state not in SESSION_TERMINAL:
            session.state, session.error = state, error
            session.url = None
            session.finished_at = self._clock()

    async def _authorize(self, session: _Session, protection: bytes) -> AuthorizationOutcome | None:
        handle = await self._worker.begin_authorization(session_id=session.id,
                                                        scopes=requested_scopes(session.services),
                                                        protection=protection)
        session.handle = handle  # no await in between: a handle is never lost
        async with self._arbiter:
            if session.cancel_requested:
                return None
            if not valid_authorization_url(getattr(handle, "url", None)):
                raise GoogleServiceError("provider_unavailable", "El worker devolvió una URL no válida.")
            session.url = handle.url
            session.expires_at = min(session.expires_at, float(getattr(handle, "expires_at", session.expires_at)))
            session.state = "awaiting_user"
        return await handle.result()

    async def _run(self, session: _Session, store: GoogleStore, protection: bytes) -> None:
        try:
            try:
                # One deadline from the start. asyncio.timeout_at, not wait_for: on 3.11 wait_for can
                # swallow a cancellation that lands as its inner task finishes.
                async with asyncio.timeout_at(session.deadline):
                    outcome = await self._authorize(session, protection)
            except TimeoutError:
                self._finish(session, "expired", "expired")
                return
            if outcome is None:
                return  # cancelled before the consent page was shown
            session.state = "validating"
            error = self._refusal(session, outcome)
            if error:
                self._finish(session, "denied" if error == "denied" else "error", error)
                return
            candidate = AuthorizationCandidate(
                client_id=outcome.client_id, issuer=outcome.issuer, subject=outcome.subject,
                email=outcome.email, requested_scopes=requested_scopes(session.services),
                granted_scopes=normalize_scopes(outcome.granted_scopes), credential=bytes(outcome.credential),
            )
            async with self._arbiter:  # cancel against commit: one winner
                if session.cancel_requested:
                    return
                if await self._revocation_pending(store):
                    self._finish(session, "error", "revocation_pending")
                    return
                try:
                    record = await asyncio.to_thread(store.commit_authorization, candidate,
                                                     account_id=session.account_id,
                                                     expected_generation=session.expected_generation)
                except GoogleStoreError as exc:
                    self._finish(session, "error", exc.code)
                    return
                session.result_account_id = record.id
                self._finish(session, "partial" if record.state == "partial" else "committed")
        except asyncio.CancelledError:
            raise  # cancel_session records the outcome
        except GoogleServiceError as exc:
            self._finish(session, "error", exc.code)
        except Exception:
            logger.warning("Google authorization %s failed", session.id[:13], exc_info=True)
            self._finish(session, "error", "provider_unavailable")
        finally:
            await self._close_handle(session)

    @staticmethod
    def _refusal(session: _Session, outcome: AuthorizationOutcome) -> str:
        """Why this outcome cannot be stored, or ''."""
        if outcome.error:
            return outcome.error if outcome.error in ERRORS else "provider_unavailable"
        try:
            grant = evaluate_grant(session.services, outcome.granted_scopes)
        except ContractError as exc:
            return exc.code
        if grant.error:
            return grant.error
        if outcome.issuer not in GOOGLE_ISSUERS or not outcome.subject or not outcome.client_id:
            return "identity_unverified"
        if not outcome.credential:
            return "provider_unavailable"
        return ""

    @staticmethod
    async def _close_handle(session: _Session) -> None:
        handle = session.handle
        if handle is None:
            return
        try:
            await handle.close()
        except Exception:
            logger.warning("Google authorization %s did not close cleanly", session.id[:13], exc_info=True)

    async def cancel_session(self, session_id: str) -> dict[str, Any]:
        """Cancel and wait until nothing of the attempt runs. A commit that won is reported as such."""
        session = self._sessions.get(session_id)
        if session is None:
            raise GoogleServiceError("not_found")
        if session.cancel_task is None:
            session.cancel_task = asyncio.create_task(self._cancel_session(session))
        return await self._settle(session.cancel_task)

    async def _cancel_session(self, session: _Session) -> dict[str, Any]:
        async with self._arbiter:
            terminal = session.state in SESSION_TERMINAL
            if not terminal:
                session.cancel_requested = True
        task = session.task
        if task is not None and not task.done():
            if not terminal:
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._close_handle(session)  # idempotent: also when the task ended before closing it
        self._finish(session, "cancelled", "cancelled")
        return session.public()

    async def _revocation_pending(self, store: GoogleStore) -> bool:
        # The OAuth client's project is shared; the account of a new consent is not known yet.
        # Do not race a new grant with a revocation that is still in flight.
        return any(not task.done() for task in self._tasks) or any(
            record.remote_revocation == "pending" for record in await asyncio.to_thread(store.accounts))

    # ── Accounts ────────────────────────────────────────────────────────

    async def set_services(self, account_id: Any, services: Any, *, expected_revision: Any) -> dict[str, Any]:
        if not is_account_id(account_id) or type(expected_revision) is not int:
            raise GoogleServiceError("invalid_request")
        if (not isinstance(services, list) or any(service not in SERVICES for service in services)
                or len(set(services)) != len(services)):
            raise GoogleServiceError("invalid_request")
        store = await self._claim()
        async with self._sync_control:
            try:
                record = await self._settle(asyncio.create_task(asyncio.to_thread(store.set_enabled_services,
                    account_id, services, expected_revision=expected_revision)))
            except GoogleStoreError as exc:
                raise GoogleServiceError(exc.code) from exc
            # F14 consent binds this connection revision; collect its work now.
            await self._stop_mail_locked(account_id)
        return record.public()

    async def refresh_credential(self, account_id: str) -> dict[str, Any]:
        """Renew one account's credential, one renewal per account at a time."""
        await self._ensure_client()
        if self._closed:
            raise GoogleServiceError("client_unavailable")
        task = asyncio.create_task(self._refresh_credential(account_id))
        self._refresh_tasks.add(task)
        try:
            return await self._settle(task)
        finally:
            self._refresh_tasks.discard(task)

    async def _refresh_credential(self, account_id: str) -> dict[str, Any]:
        if not is_account_id(account_id):
            raise GoogleServiceError("invalid_request")
        if not self.available:
            raise GoogleServiceError("client_unavailable")
        store = await self._claim()
        lock = self._account_locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            ref = await asyncio.to_thread(store.credential, account_id)  # the latest one, read under the lock
            if ref is None:
                raise GoogleServiceError("not_connected")
            try:
                protection = await asyncio.to_thread(store.protection_key)
                outcome = await asyncio.wait_for(self._worker.refresh(ref.credential, protection=protection),
                                                 timeout=self.refresh_timeout_s)
            except TimeoutError:
                raise GoogleServiceError("provider_unavailable") from None
            if outcome.error == "reauth_required":
                try:
                    record = await asyncio.to_thread(store.mark_reauth_required, account_id,
                                                     generation=ref.generation)
                except StaleGeneration:
                    record = await asyncio.to_thread(store.account, account_id)
                return record.public()
            if outcome.error:
                raise GoogleServiceError(outcome.error if outcome.error in ERRORS else "provider_unavailable")
            if outcome.credential and outcome.credential != ref.credential:
                try:
                    await asyncio.to_thread(store.replace_credential, account_id, generation=ref.generation,
                                            credential_revision=ref.credential_revision,
                                            credential=outcome.credential)
                except StaleGeneration:
                    pass  # disconnected or reconnected meanwhile: that change wins
            record = await asyncio.to_thread(store.account, account_id)
            return record.public()

    async def disconnect(self, account_id: Any, *, revoke_remote: bool = False, clear_cache: bool = False,
                         expected_revision: Any = None) -> dict[str, Any]:
        """Disconnect in this Iris now; revoking in Google, if asked, completes in the background."""
        if not is_account_id(account_id) or (expected_revision is not None and type(expected_revision) is not int):
            raise GoogleServiceError("invalid_request")
        await self._ensure_client()
        store = await self._claim()
        async with self._sync_control:
            async with self._arbiter:
                if self._closed:
                    raise GoogleServiceError("client_unavailable")
                try:
                    record, credential = await asyncio.to_thread(store.disconnect, account_id,
                                                                 revoke_remote=bool(revoke_remote),
                                                                 clear_cache=bool(clear_cache),
                                                                 expected_revision=expected_revision)
                except GoogleStoreError as exc:
                    raise GoogleServiceError(exc.code) from exc
                if credential is not None and self.available:
                    # Track before waiting: forgetting the row cannot hide the remote work.
                    self._spawn(self._revoke(store, account_id, record.revocation_generation, credential))
            await self._stop_sync_locked(account_id)  # invalidate first, then collect the owned round
            await self._stop_mail_locked(account_id)
        if credential is not None:
            if not self.available:
                await asyncio.to_thread(store.record_revocation, account_id,
                                        revocation_generation=record.revocation_generation, outcome="failed")
                record = await asyncio.to_thread(store.account, account_id)
        return record.public()

    async def forget(self, account_id: Any) -> dict[str, Any]:
        """Remove a disconnected account from this Iris: its record, any credential kept aside for a
        revocation and what was read from it. Connected accounts are disconnected first."""
        if not is_account_id(account_id):
            raise GoogleServiceError("invalid_request")
        store = await self._claim()
        try:
            await asyncio.to_thread(store.forget, account_id)
        except GoogleStoreError as exc:
            raise GoogleServiceError(exc.code) from exc
        await self._stop_sync(account_id)
        return {"id": account_id, "forgotten": True}

    async def _revoke(self, store: GoogleStore, account_id: str, generation: int, credential: bytes) -> None:
        try:
            protection = await asyncio.to_thread(store.protection_key)
            outcome = await asyncio.wait_for(self._worker.revoke(credential, protection=protection),
                                             timeout=self.revoke_timeout_s)
        except TimeoutError:
            outcome = "unknown"  # it may still happen in Google: never reported as done or failed
        except Exception:
            logger.warning("Google revocation of %s failed", account_id[:13], exc_info=True)
            outcome = "failed"
        if outcome not in ("confirmed", "failed", "unknown"):
            outcome = "unknown"
        try:
            await asyncio.to_thread(store.record_revocation, account_id, revocation_generation=generation,
                                    outcome=outcome)
        except UnknownAccount:
            pass  # forgotten meanwhile

    # ── Reading (F10.3) ─────────────────────────────────────────────────

    async def sync(self, account_id: Any, services: Any = None) -> dict[str, Any]:
        """«Actualizar datos»: one bounded read of the account's granted services.

        Joins a round already running for the account. A service in backoff is not read before
        its time (its state says when). Returns the account's sync state."""
        if not is_account_id(account_id):
            raise GoogleServiceError("invalid_request")
        if services is not None and (not isinstance(services, list) or any(item not in SERVICES for item in services)
                                     or len(set(services)) != len(services)):
            raise GoogleServiceError("invalid_request")
        await self._ensure_client()
        if not self.available:
            raise GoogleServiceError("client_unavailable")
        store = await self._claim()
        async with self._sync_control:
            if self._closed:
                raise GoogleServiceError("client_unavailable")
            record = await asyncio.to_thread(store.account, account_id)
            if record is None:
                raise GoogleServiceError("not_found")
            if record.state not in ("connected", "partial"):
                raise GoogleServiceError("not_connected")
            granted = record.granted_services
            if services is not None and any(service not in granted for service in services):
                raise GoogleServiceError("service_not_granted")
            running = self._sync_tasks.get(account_id)
            if running is not None and not running.done() and self._sync_generations.get(account_id) != record.generation:
                await self._stop_sync_locked(account_id)
                running = None
            now = self._clock()
            due = []
            for service in (services if services is not None else granted):
                if running is not None and not running.done() and self._sync_active.get(account_id) == service:
                    continue  # this request is covered by the round already running
                summary = await asyncio.to_thread(store.collection, account_id, service, SUMMARY)
                if summary is None or summary.state != "backoff" or (summary.next_attempt_at or 0) <= now:
                    due.append(service)
            if due:
                if running is None or running.done():
                    self._sync_pending[account_id] = set(due)
                    self._sync_generations[account_id] = record.generation
                    self._sync_tasks[account_id] = asyncio.create_task(self._sync_account(store, account_id, record.generation))
                else:
                    self._sync_pending[account_id].update(due)
        return await self._sync_state(store, account_id, granted)

    async def sync_state(self, account_id: Any) -> dict[str, Any]:
        if not is_account_id(account_id):
            raise GoogleServiceError("invalid_request")
        store = await self._store(create=False)
        record = await asyncio.to_thread(store.account, account_id) if store else None
        if record is None:
            raise GoogleServiceError("not_found")
        return await self._sync_state(store, account_id, record.granted_services)

    async def _sync_state(self, store: GoogleStore, account_id: str, granted: tuple[str, ...]) -> dict[str, Any]:
        """Per granted service: state, last complete read, coverage and counts. Never content."""
        running = self._sync_tasks.get(account_id)
        busy = running is not None and not running.done()
        counts = await asyncio.to_thread(store.counts, account_id)
        state = {}
        for service in granted:
            summary = await asyncio.to_thread(store.collection, account_id, service, SUMMARY)
            records = sum(count for name, count in counts.get(service, {}).items() if name != CALENDARS)
            coverage = dict(summary.coverage) if summary else {}
            if service == "gmail.read":
                # The saved numeric limits describe the initial read, not the current cache.
                coverage.update(mode="bootstrap_then_history", limits_apply_to="initial_read", maintained_window=False)
            state[service] = {
                "state": "running" if busy and (service == self._sync_active.get(account_id)
                            or service in self._sync_pending.get(account_id, ())) else (summary.state if summary else "never"),
                "last_success_at": iso(summary.last_success_at) if summary else None,
                "next_attempt_at": iso(summary.next_attempt_at) if summary and summary.state == "backoff" else None,
                "error": ({"code": summary.error_code, "message": ERRORS.get(summary.error_code, "")}
                          if summary and summary.error_code else None),
                "coverage": coverage,
                "records": records,
            }
        return state

    async def _stop_sync(self, account_id: str) -> None:
        async with self._sync_control:
            await self._stop_sync_locked(account_id)

    async def _stop_sync_locked(self, account_id: str) -> None:
        task = self._sync_tasks.get(account_id)
        if task is not None and not task.done():
            task.cancel()
            async def collect():
                await asyncio.gather(task, return_exceptions=True)
            await self._settle(asyncio.create_task(collect()))
        if self._sync_tasks.get(account_id) is task:
            self._sync_tasks.pop(account_id, None)
            self._sync_pending.pop(account_id, None)
            self._sync_active.pop(account_id, None)
            self._sync_generations.pop(account_id, None)

    async def _sync_account(self, store: GoogleStore, account_id: str, generation: int) -> None:
        try:
            async with self._rounds:
                lock = self._account_locks.setdefault(account_id, asyncio.Lock())
                async with lock:
                    pending = self._sync_pending[account_id]
                    while pending:
                        service = next(item for item in SERVICES if item in pending)
                        pending.remove(service)
                        self._sync_active[account_id] = service
                        if not await self._sync_service(store, account_id, generation, service):
                            return
                        self._sync_active.pop(account_id, None)
        except StaleGeneration:
            return  # disconnected or reconnected meanwhile: that change wins, nothing is recorded
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Google read of %s failed", account_id[:13], exc_info=True)
        finally:
            if self._sync_tasks.get(account_id) is asyncio.current_task():
                self._sync_pending.pop(account_id, None)
                self._sync_active.pop(account_id, None)

    async def _sync_service(self, store: GoogleStore, account_id: str, generation: int, service: str) -> bool:
        """One service of the account; False when the account itself can no longer be read."""
        try:
            if service == "calendar.read":
                coverage = await self._sync_calendar(store, account_id, generation)
            else:
                coverage = await self._sync_gmail(store, account_id, generation)
            await asyncio.to_thread(store.commit_round, account_id, generation=generation, service=service,
                                    collection=SUMMARY, upserts={}, deletes=(), checkpoint=None,
                                    coverage=coverage, replace=False)
            return True
        except SyncError as exc:
            if exc.code == "reauth_required":
                with contextlib.suppress(StaleGeneration):
                    await asyncio.to_thread(store.mark_reauth_required, account_id, generation=generation)
                return False
            summary = await asyncio.to_thread(store.collection, account_id, service, SUMMARY)
            attempts = (summary.attempts if summary else 0) + 1
            next_at = (self._clock() + backoff_delay(attempts, exc.retry_after, rng=self._rng)
                       if exc.code in TRANSIENT else None)
            await asyncio.to_thread(store.record_failure, account_id, generation=generation, service=service,
                                    collection=SUMMARY, code=exc.code, next_attempt_at=next_at)
            return True

    async def _round(self, store: GoogleStore, account_id: str, generation: int, method: str,
                     params: dict[str, Any]) -> tuple[Staging, dict[str, Any]]:
        """One round in the worker: pages staged here, the credential kept current, the result returned."""
        ref = await self._settle(asyncio.create_task(asyncio.to_thread(store.credential, account_id)))
        if ref is None or ref.generation != generation:
            raise StaleGeneration("La cuenta cambió antes de leer.")
        protection = await self._settle(asyncio.create_task(asyncio.to_thread(store.protection_key)))
        staging = Staging()
        try:
            lines = self._worker.read_round(method, ref.credential, protection=protection, params=params,
                                            timeout=self.round_timeout_s)
            async with contextlib.aclosing(lines):
                async for message in lines:
                    kind, public = message.get("event"), message.get("public", {})
                    if kind == "refreshed":
                        ref = await self._keep_renewal(store, ref, message)
                    elif kind == "page":
                        staging.add_page(public)
                    elif kind == "result":
                        return staging, public
                    elif kind == "error":
                        code = public.get("code")
                        retry = public.get("retry_after")
                        raise SyncError(code if code in ERRORS else "provider_unavailable",
                                        float(retry) if isinstance(retry, (int, float)) else None)
                    else:
                        raise SyncError("provider_unavailable")
        except GoogleServiceError as exc:
            raise SyncError(exc.code) from exc
        raise SyncError("provider_unavailable")  # the worker ended without a result

    @staticmethod
    async def _keep_renewal(store: GoogleStore, ref: CredentialRef, message: dict) -> CredentialRef:
        """A token renewed during a round: stored over the credential it renewed, or the round stops."""
        try:
            renewed = decode_credential(message.get("private", {}).get("credential"))
        except RpcError as exc:
            raise SyncError("provider_unavailable") from exc
        record = await GoogleConnectionsService._settle(asyncio.create_task(asyncio.to_thread(
            store.replace_credential, ref.account_id, generation=ref.generation,
            credential_revision=ref.credential_revision, credential=renewed)))
        return CredentialRef(ref.account_id, renewed, record.generation, record.credential_revision)

    async def _sync_calendar(self, store: GoogleStore, account_id: str, generation: int) -> dict[str, Any]:
        staging, _ = await self._round(store, account_id, generation, "sync.calendar_list", {})
        calendars = sorted(staging.upserts)
        collections = [events_collection(calendar_id) for calendar_id in calendars]
        await asyncio.to_thread(store.commit_round, account_id, generation=generation, service="calendar.read",
                                collection=CALENDARS, upserts=staging.upserts, deletes=(), checkpoint=None,
                                coverage={"calendars": len(calendars)}, replace=True,
                                keep_collections=[CALENDARS, SUMMARY, *collections])
        for calendar_id, collection in zip(calendars, collections):
            state = await asyncio.to_thread(store.collection, account_id, "calendar.read", collection)
            token = state.checkpoint if state else None
            try:
                staging, result = await self._round(store, account_id, generation, "sync.calendar_events",
                                                    {"calendar_id": calendar_id, "sync_token": token})
            except SyncError as exc:
                if exc.code != "sync_expired" or token is None:
                    raise
                token = None  # 410: Google asks for this calendar from the start
                staging, result = await self._round(store, account_id, generation, "sync.calendar_events",
                                                    {"calendar_id": calendar_id, "sync_token": None})
            next_token = result.get("sync_token")
            if not isinstance(next_token, str) or not next_token:
                raise SyncError("provider_unavailable")
            await asyncio.to_thread(store.commit_round, account_id, generation=generation, service="calendar.read",
                                    collection=collection, upserts=staging.upserts, deletes=staging.deletes,
                                    checkpoint=next_token, coverage={"full_read": token is None},
                                    replace=token is None)
        return {"calendars": len(calendars)}

    async def _sync_gmail(self, store: GoogleStore, account_id: str, generation: int) -> dict[str, Any]:
        state = await asyncio.to_thread(store.collection, account_id, "gmail.read", MAILBOX)
        checkpoint = state.checkpoint if state else None
        params = {"checkpoint": checkpoint, "window_days": self.gmail_window_days,
                  "max_messages": self.gmail_max_messages}
        try:
            staging, result = await self._round(store, account_id, generation, "sync.gmail", params)
        except SyncError as exc:
            if exc.code != "sync_expired" or checkpoint is None:
                raise
            checkpoint = None  # history too old: the window is read again
            staging, result = await self._round(store, account_id, generation, "sync.gmail",
                                                {**params, "checkpoint": None})
        new_checkpoint = result.get("checkpoint")
        if not isinstance(new_checkpoint, str) or not new_checkpoint.isdigit():
            raise SyncError("provider_unavailable")
        full = checkpoint is None
        arrivals = result.get("arrivals", [])
        if (not isinstance(arrivals, list) or len(arrivals) > 50000
                or any(not isinstance(identity, str) or len(identity) > 512 for identity in arrivals)):
            raise SyncError("provider_unavailable")
        coverage = result.get("coverage") if full else (state.coverage if state else {})
        if not isinstance(coverage, dict):
            raise SyncError("provider_unavailable")
        await asyncio.to_thread(store.commit_round, account_id, generation=generation, service="gmail.read",
                                collection=MAILBOX, upserts=staging.upserts, deletes=staging.deletes,
                                checkpoint=new_checkpoint, coverage=coverage, replace=full, arrivals=arrivals)
        return dict(coverage)

    async def tick(self) -> list[str]:
        """Periodic reading: enabled services only, when due and not in backoff. Returns the accounts started."""
        await self._ensure_client()
        if not self.available:
            return []
        store = await self._store(create=False)
        if store is None:
            return []
        started = []
        now = self._clock()
        for record in await asyncio.to_thread(store.accounts):
            if record.state not in ("connected", "partial"):
                continue
            due = []
            for service in record.enabled_services:
                if service not in record.granted_services:
                    continue
                summary = await asyncio.to_thread(store.collection, account_id := record.id, service, SUMMARY)
                if summary is not None and summary.state == "backoff" and (summary.next_attempt_at or 0) > now:
                    continue
                if summary is None or summary.last_success_at is None \
                        or now - summary.last_success_at >= self.periodic_interval_s:
                    due.append(service)
            if due:
                running = self._sync_tasks.get(account_id)
                if running is None or running.done():
                    await self.sync(account_id, due)
                    started.append(account_id)
        return started

    async def run_periodic(self, interval: float = 60.0) -> None:
        """Core's loop: check every minute what is due. Closing the service ends it."""
        while not self._closed:
            try:
                await self.tick()
            except GoogleServiceError:
                pass  # another Iris owns this profile, or no worker: try again later
            except Exception:
                logger.warning("Periodic Google reading failed", exc_info=True)
            await asyncio.sleep(interval)
