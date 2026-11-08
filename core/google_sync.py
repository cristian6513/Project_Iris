"""F10.3: what a sync round is, on Core's side: limits, collections, staging and backoff.

A round reads one collection from Google in the worker (one child process for the round, its
pages streamed as ``page`` lines) and Core stages every page in memory. Only when the round's
``result`` arrives are its records and its checkpoint promoted together, in one transaction,
and only for the account generation the round started from: a failure after page 1, a
disconnection or a crash confirms nothing, and the next round starts from the last checkpoint.

* **Calendar**: the calendar list is a snapshot (replaced whole). Each calendar's events are
  synced as Google stores them: masters and exceptions, not expanded (``singleEvents`` off);
  F11 expands a window. A full read ends with ``nextSyncToken``; later rounds use it for every
  page and keep ``nextSyncToken`` only at the end. ``410`` means a full read of that calendar.
* **Gmail**: a bounded initial read (recent days, a maximum of messages), metadata only, followed
  by history from a ``historyId`` taken *before* that initial read. This is a growing history
  cache, not a maintained time window: changes can add older messages and exceed the bootstrap
  count. ``404`` on history means rebuilding the initial read. Nothing is ever modified,
  marked as read or downloaded beyond metadata.

Records are validated here again (ids, sizes): the worker's output is not trusted blindly.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

CALENDARS = "calendars"
EVENTS_PREFIX = "events:"
MAILBOX = "mailbox"
SUMMARY = "_service"  # one row per service: its last complete round and coverage

ROUND_TIMEOUT_S = 300.0
MAX_CONCURRENT_ROUNDS = 2
MAX_STAGED_RECORDS = 50_000
MAX_RECORD_BYTES = 32 * 1024
CALENDAR_MAX_PAGES = 80  # of 250 events
GMAIL_WINDOW_DAYS = 30
GMAIL_MAX_MESSAGES = 200
PERIODIC_INTERVAL_S = 15 * 60
BACKOFF_BASE_S = 30.0
BACKOFF_MAX_S = 3600.0
TRANSIENT = frozenset({"offline", "rate_limited", "provider_unavailable"})

_ID = re.compile(r"^[^\x00-\x1f\x7f]{1,512}$")


class SyncError(Exception):
    """A round that ended without a result; ``code`` is a contract error code."""

    def __init__(self, code: str, retry_after: float | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


def events_collection(calendar_id: str) -> str:
    if not isinstance(calendar_id, str) or not _ID.fullmatch(calendar_id) or len(calendar_id) > 256:
        raise SyncError("provider_unavailable")
    return EVENTS_PREFIX + calendar_id


def backoff_delay(attempts: int, retry_after: float | None = None, *, rng: random.Random | None = None) -> float:
    """Exponential with jitter; Google's Retry-After can exceed our own one-hour cap."""
    rng = rng or random.Random()
    delay = min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** max(0, attempts - 1))) * rng.uniform(0.8, 1.2)
    if retry_after is not None and retry_after > 0:
        delay = max(delay, float(retry_after))
    return delay


def _record_id(record: Any) -> str:
    if not isinstance(record, Mapping):
        raise SyncError("provider_unavailable")
    record_id = record.get("id")
    if not isinstance(record_id, str) or not _ID.fullmatch(record_id):
        raise SyncError("provider_unavailable")
    return record_id


@dataclass
class Staging:
    """One round's pages, in memory until its result. Later pages win over earlier ones."""

    upserts: dict[str, dict[str, Any]] = field(default_factory=dict)
    deletes: set[str] = field(default_factory=set)

    def add_page(self, public: Mapping[str, Any]) -> None:
        import json

        records, deleted = public.get("records", []), public.get("deleted", [])
        if not isinstance(records, list) or not isinstance(deleted, list):
            raise SyncError("provider_unavailable")
        for record in records:
            record_id = _record_id(record)
            if len(json.dumps(record, ensure_ascii=False)) > MAX_RECORD_BYTES:
                raise SyncError("provider_unavailable")
            self.upserts[record_id] = dict(record)
            self.deletes.discard(record_id)
        for record_id in deleted:
            if not isinstance(record_id, str) or not _ID.fullmatch(record_id):
                raise SyncError("provider_unavailable")
            self.upserts.pop(record_id, None)
            self.deletes.add(record_id)
        if len(self.upserts) + len(self.deletes) > MAX_STAGED_RECORDS:
            raise SyncError("too_large")
