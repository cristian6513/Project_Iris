"""F04: durable automation store in the mutable profile, never in the installation.

One SQLite database per Iris profile (``<profile>/automations/automations.sqlite3``):

* every change is one transaction (``BEGIN IMMEDIATE``), so a failed write
  leaves the previous state intact and two claimants are serialised;
* rules carry revisions: an edit must name the revision it read
  (``RevisionConflict`` otherwise), and an edit cancels the occurrences of the
  older revision that had not been sent yet;
* ``(rule_id, rule_revision, occurrence_key)`` is unique: the same episode
  never becomes two occurrences, whoever records it and however often;
* a claim is a lease with a token: only its holder advances the occurrence,
  and an expired lease can be claimed again; submitted work is never re-claimed;
* a store written by a newer Iris (``PRAGMA user_version``) is refused before
  anything is written: opening never migrates down, deletes or rewrites it.
  Every connection reads the version its last committed transaction left
  (C02): the database's header, or page 1 of a WAL left by a crash, read the
  way SQLite recovers it, before SQLite opens the store, so a newer store
  whose version is still in its WAL is not checkpointed while being refused;
* every real open checks the profile boundary again (Q02): the folder must
  still be a plain directory of the profile and the database and its sidecars
  plain files with one link, the same file before and after connecting. A
  folder replaced by a junction or a hardlinked database is refused, and its
  target is neither written nor touched;
* while a connection is open (T01) the profile root, the store's folder and
  the database are pinned on Windows: handles with read access and without
  FILE_SHARE_DELETE, so none of them can be renamed, deleted or replaced, not
  even swapped and put back while SQLite opens the file. Each connection also proves it
  reached this store (its ``store_id``), not another one under the same path;
* SQLite never opens a sidecar Iris did not establish (S01): every connection
  takes EXCLUSIVE locking before its first access, so the WAL index lives in
  its memory and no ``-shm`` is created, opened or written; the ``-wal`` of a
  writing connection is created here and pinned before SQLite can open it; and
  a new store is built in memory (C01), written by Iris to a file it creates
  under a random name, already in WAL mode with its schema, and only then given
  its real name: SQLite never opens the new file before it is the store, so
  none of its sidecars exists to be redirected while it is built, and SQLite
  never creates a file at the real path. One connection at a time (``automations.lock``, across threads
  and processes) is taken before any sidecar is touched and held until SQLite
  has closed, so pinning the ``-wal`` never races another connection's use or
  deletion of it.

F07 (schema 2) adds what delivering a result needs, in the same database and
the same transactions as the result itself: each delivery's revision,
snoozed and dismissed facts and visual receipt (``act_on_delivery``), the
inbox preferences, and the return notices (``presentations``): one local
surface claims a notice with a lease, a token and a generation, and only its
receipt marks the results presented. A v1 store is migrated when it is opened
for use; v1 delivery documents stay as they are until something changes them.

Nothing here schedules, runs or delivers: F05/F06/F07 call these methods.

F10 moved the hardened opening, unchanged, to ``core/profile_sqlite.py`` so the Google
connections store shares it.
"""

from __future__ import annotations

import json
import os  # noqa: F401 -- tests patch os.rename/os.fsync through this module
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from core.profile_sqlite import (
    Pins,
    ProfileSqliteStore,
    StoreSpec,
    committed_page_one,
    new_store_image,
)
from core.profile_sqlite import profile_store_path as _profile_store_path

from core.automation_contracts import (
    DELIVERY_ACTIONS,
    DELIVERY_SCHEMA,
    FINAL_STATES,
    AutomationDelivery,
    AutomationOccurrence,
    AutomationPreferences,
    AutomationResult,
    AutomationRule,
    Claim,
    InboxPreferences,
    InboxPresentation,
    VisualReceipt,
    apply_delivery_action,
    can_transition,
    delivery_actions,
    delivery_effect_holds,
    delivery_view,
    new_id,
)

SCHEMA_VERSION = 2
STORE_NAME = "iris.automation-store/v1"


class AutomationStoreError(RuntimeError):
    """Base class: a request the store refused without changing anything."""


class StoreVersionError(AutomationStoreError):
    """The store was written by a newer Iris; it is left untouched."""


class StorePathError(AutomationStoreError):
    """The store's folder or files stopped being plain parts of the profile; nothing was opened."""


class RevisionConflict(AutomationStoreError):
    """The rule changed since the caller read it."""


class StaleRevision(AutomationStoreError):
    """An occurrence for a revision that is not the rule's current one, or a paused rule."""


class UnknownRule(AutomationStoreError):
    pass


class InvalidTransition(AutomationStoreError):
    pass


class ClaimLost(AutomationStoreError):
    """The caller's lease expired or another claimant holds the occurrence."""


class AdmissionRefused(AutomationStoreError):
    """Admission checked something again and it no longer allows sending (nothing was recorded)."""


class UnknownDelivery(AutomationStoreError):
    """No delivered result with that id in this profile."""


class PresentationRefused(AutomationStoreError):
    """A receipt that does not belong to that notice (token, generation or surface)."""


# Sent work that holds Iris' single place in Hermes until it is settled.
ACTIVE_STATES = ("submitted", "running", "ambiguous", "stopping")
# Occurrences the inbox shows: sent ones, with or without a result yet.
INBOX_STATES = ACTIVE_STATES + ("completed", "failed")
# Return notices that may have reached the user: their items are not presented again automatically.
PRESENTED_STATES = ("claimed", "sent", "shown", "ambiguous")
PRESENTATION_KEEP_S = 7 * 86_400  # released notices
# Notices that may have been shown are remembered longer than any delivery can last (30 days),
# so a result they covered is never presented again automatically.
PRESENTATION_MEMORY_S = 31 * 86_400


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS rules (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS occurrences (id TEXT PRIMARY KEY, rule_id TEXT NOT NULL,"
    " rule_revision INTEGER NOT NULL, occurrence_key TEXT NOT NULL, state TEXT NOT NULL,"
    " document TEXT NOT NULL, UNIQUE (rule_id, rule_revision, occurrence_key))",
    "CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY, occurrence_id TEXT NOT NULL UNIQUE,"
    " document TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS deliveries (result_id TEXT PRIMARY KEY, document TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS history (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,"
    " kind TEXT NOT NULL, ref TEXT NOT NULL, detail TEXT NOT NULL)",
    # F06: the exact request sent to Hermes, so reconciling resends the same
    # bytes under the same Idempotency-Key and Hermes returns the same run, and
    # (Q07) the context it was admitted in: revision, profile, the non-secret
    # fingerprint of where and as whom it was sent, and its deadline.
    "CREATE TABLE IF NOT EXISTS submissions (occurrence_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,"
    " body TEXT NOT NULL, context TEXT NOT NULL DEFAULT '{}')",
    # F07 (schema 2): return notices, one claimant at a time.
    "CREATE TABLE IF NOT EXISTS presentations (id TEXT PRIMARY KEY, generation INTEGER NOT NULL UNIQUE,"
    " state TEXT NOT NULL, created_at REAL NOT NULL, document TEXT NOT NULL)",
)


_SPEC = StoreSpec(
    folder=("automations",), filename="automations.sqlite3", lock_name="automations.lock",
    noun="almacén de automatizaciones", schema=_SCHEMA, schema_version=SCHEMA_VERSION, store_name=STORE_NAME,
    path_error=StorePathError, version_error=StoreVersionError, busy_error=AutomationStoreError,
)


def profile_store_path(home: Path | None = None) -> Path:
    """``<profile>/automations/automations.sqlite3``, refusing links inside the profile."""
    if home is None:
        from core.config import get_iris_home

        home = Path(get_iris_home())
    return _profile_store_path(_SPEC, home)


class _Pins(Pins):
    """The automations store's pins (T01; see core/profile_sqlite.py)."""

    def __init__(self) -> None:
        super().__init__(_SPEC)


def _committed_page_one(wal) -> bytes | None:
    return committed_page_one(wal, _SPEC)


def _new_store_image() -> bytes:
    """The bytes of an empty current store, built by SQLite in memory: no file is involved."""
    return new_store_image(_SPEC, new_id())


def _dump(model) -> str:
    return json.dumps(model.model_dump(mode="json", by_alias=True), sort_keys=True, ensure_ascii=False)


class AutomationStore(ProfileSqliteStore):
    """The hardened opening (Q02, T01, S01, C01, C02) lives in ProfileSqliteStore."""

    SPEC = _SPEC

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time, root: Path | None = None) -> None:
        """``root``: the profile the store belongs to; every open checks the path from it."""
        self._clock = clock
        self._listeners: list[Callable[[str], None]] = []
        super().__init__(path, root=root)

    @classmethod
    def for_profile(cls, home: Path | None = None, **kwargs) -> AutomationStore:
        from core.profile_paths import ProfilePathError, validate_profile_home

        if home is None:
            from core.config import get_iris_home

            home = Path(get_iris_home())
        try:
            home = validate_profile_home(home)
            path = profile_store_path(home)
        except ProfilePathError as exc:
            raise StorePathError(str(exc)) from exc
        return cls(path, root=home, **kwargs)

    # Looked up here at call time, so tests can plant a name or an image.
    def _new_id(self) -> str:
        return new_id()

    def _new_image(self) -> bytes:
        return _new_store_image()

    def _pins(self) -> Pins:
        return _Pins()

    def _upgrade(self, conn: sqlite3.Connection, version: int) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(submissions)")}
        if "context" not in columns:  # a development store from before Q07
            conn.execute("ALTER TABLE submissions ADD COLUMN context TEXT NOT NULL DEFAULT '{}'")
        if 0 < version < SCHEMA_VERSION:
            # 1 → 2 only adds the presentations table; v1 deliveries stay readable as they are.
            self._history(conn, self._clock(), "store_migrated", "", {"from": version, "to": SCHEMA_VERSION})

    def subscribe(self, listener: Callable[[str], None]) -> None:
        """``listener(kind)`` after a committed change the inbox shows ("result", "delivery",
        "presentation", "preferences"). It may run on any thread and must not block."""
        self._listeners.append(listener)

    def _notify(self, kind: str) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind)
            except Exception:  # a listener never undoes or hides a committed change
                pass

    @staticmethod
    def _history(conn: sqlite3.Connection, at: float, kind: str, ref: str, detail: dict[str, Any]) -> None:
        conn.execute("INSERT INTO history (at, kind, ref, detail) VALUES (?, ?, ?, ?)",
                     (at, kind, ref, json.dumps(detail, sort_keys=True)))

    # ── Rules ───────────────────────────────────────────────────────────

    def create_rule(self, rule: AutomationRule) -> AutomationRule:
        if rule.revision != 1:
            raise RevisionConflict("Una regla nueva empieza en la revisión 1.")
        with self._transaction() as conn:
            try:
                conn.execute("INSERT INTO rules VALUES (?, ?, ?)", (rule.id, rule.revision, _dump(rule)))
            except sqlite3.IntegrityError as exc:
                raise RevisionConflict("Ya existe una regla con ese id.") from exc
            self._history(conn, self._clock(), "rule_created", rule.id, {"revision": 1})
        return rule

    def get_rule(self, rule_id: str) -> AutomationRule | None:
        with self._read() as conn:
            row = conn.execute("SELECT document FROM rules WHERE id = ?", (rule_id,)).fetchone()
        return AutomationRule.model_validate_json(row[0]) if row else None

    def list_rules(self) -> list[AutomationRule]:
        with self._read() as conn:
            rows = conn.execute("SELECT document FROM rules ORDER BY id").fetchall()
        return [AutomationRule.model_validate_json(row[0]) for row in rows]

    def update_rule(self, rule_id: str, *, expected_revision: int, **changes: Any) -> AutomationRule:
        """Edit a rule read at ``expected_revision``; the new revision is that plus one.

        Occurrences of older revisions that were not sent yet are cancelled:
        an edited rule never fires again for an episode it already saw.
        """
        allowed = {"title", "enabled", "trigger", "execution", "delivery"}
        if not changes or set(changes) - allowed:
            raise ValueError(f"Solo se editan {sorted(allowed)}.")
        now = self._clock()
        with self._transaction() as conn:
            row = conn.execute("SELECT revision, document FROM rules WHERE id = ?", (rule_id,)).fetchone()
            if row is None:
                raise UnknownRule("La regla no existe.")
            if row[0] != expected_revision:
                raise RevisionConflict("La regla cambió desde que se leyó; vuelve a cargarla.")
            current = AutomationRule.model_validate_json(row[1])
            data = current.model_dump(by_alias=True)
            for key, value in changes.items():
                data[key] = value.model_dump(by_alias=True) if hasattr(value, "model_dump") else value
            data.update(revision=current.revision + 1, updated_at=max(now, current.updated_at))
            updated = AutomationRule.model_validate(data)
            conn.execute("UPDATE rules SET revision = ?, document = ? WHERE id = ?",
                         (updated.revision, _dump(updated), rule_id))
            cancelled = self._cancel_unsent(conn, rule_id, below_revision=updated.revision, now=now)
            self._history(conn, now, "rule_updated", rule_id,
                          {"revision": updated.revision, "cancelled_occurrences": cancelled})
        self._notify("rule")
        return updated

    def _cancel_unsent(self, conn: sqlite3.Connection, rule_id: str, *, below_revision: int, now: float) -> int:
        rows = conn.execute(
            "SELECT document FROM occurrences WHERE rule_id = ? AND rule_revision < ? AND state IN ('candidate', 'claimed')",
            (rule_id, below_revision)).fetchall()
        for (document,) in rows:
            occurrence = AutomationOccurrence.model_validate_json(document)
            self._write_occurrence(conn, occurrence, state="cancelled", claim=None, error="regla editada", now=now)
        return len(rows)

    # ── Occurrences ─────────────────────────────────────────────────────

    def _write_occurrence(self, conn: sqlite3.Connection, occurrence: AutomationOccurrence, *, now: float,
                          **changes: Any) -> AutomationOccurrence:
        data = occurrence.model_dump(by_alias=True)
        data.update(changes, updated_at=max(now, occurrence.updated_at))
        updated = AutomationOccurrence.model_validate(data)
        conn.execute("UPDATE occurrences SET state = ?, document = ? WHERE id = ?",
                     (updated.state, _dump(updated), updated.id))
        if updated.state != occurrence.state:
            self._history(conn, now, "occurrence_" + updated.state, updated.id,
                          {"from": occurrence.state, "rule_id": updated.rule_id})
        return updated

    def _occurrence(self, conn: sqlite3.Connection, occurrence_id: str) -> AutomationOccurrence:
        row = conn.execute("SELECT document FROM occurrences WHERE id = ?", (occurrence_id,)).fetchone()
        if row is None:
            raise InvalidTransition("La ocurrencia no existe.")
        return AutomationOccurrence.model_validate_json(row[0])

    def record_candidate(self, rule_id: str, rule_revision: int, occurrence_key: str
                         ) -> tuple[AutomationOccurrence, bool]:
        """The occurrence for this episode, created once; ``False`` when it already existed."""
        now = self._clock()
        with self._transaction() as conn:
            row = conn.execute("SELECT revision, document FROM rules WHERE id = ?", (rule_id,)).fetchone()
            if row is None:
                raise UnknownRule("La regla no existe.")
            existing = conn.execute(
                "SELECT document FROM occurrences WHERE rule_id = ? AND rule_revision = ? AND occurrence_key = ?",
                (rule_id, rule_revision, occurrence_key)).fetchone()
            if existing:
                return AutomationOccurrence.model_validate_json(existing[0]), False
            rule = AutomationRule.model_validate_json(row[1])
            if rule.revision != rule_revision or not rule.enabled:
                raise StaleRevision("La regla cambió o está pausada; esa ocurrencia ya no corresponde.")
            occurrence = AutomationOccurrence(
                id=new_id(), rule_id=rule_id, rule_revision=rule_revision, occurrence_key=occurrence_key,
                correlation_id=new_id(), created_at=now, updated_at=now)
            conn.execute("INSERT INTO occurrences VALUES (?, ?, ?, ?, ?, ?)",
                         (occurrence.id, rule_id, rule_revision, occurrence_key, occurrence.state, _dump(occurrence)))
            self._history(conn, now, "occurrence_candidate", occurrence.id, {"rule_id": rule_id, "key": occurrence_key})
        return occurrence, True

    def claim(self, occurrence_id: str, owner: str, *, lease_s: float = 60.0) -> AutomationOccurrence | None:
        """Take the occurrence for ``lease_s``; None when another claimant holds it or it moved on."""
        now = self._clock()
        with self._transaction() as conn:
            occurrence = self._occurrence(conn, occurrence_id)
            expired = occurrence.state == "claimed" and occurrence.claim.lease_expires_at <= now
            if occurrence.state != "candidate" and not expired:
                return None
            rule_row = conn.execute("SELECT revision, document FROM rules WHERE id = ?",
                                    (occurrence.rule_id,)).fetchone()
            rule = AutomationRule.model_validate_json(rule_row[1]) if rule_row else None
            if rule is None or rule.revision != occurrence.rule_revision or not rule.enabled:
                self._write_occurrence(conn, occurrence, state="cancelled", claim=None, error="regla cambiada", now=now)
                return None
            claim = Claim(owner=owner, token=new_id(), lease_expires_at=now + max(1.0, float(lease_s)))
            return self._write_occurrence(conn, occurrence, state="claimed", claim=claim.model_dump(), now=now)

    def release(self, occurrence_id: str, token: str) -> AutomationOccurrence:
        """The holder gives the occurrence back without sending it."""
        now = self._clock()
        with self._transaction() as conn:
            occurrence = self._occurrence(conn, occurrence_id)
            self._require_claim(occurrence, token, now)
            return self._write_occurrence(conn, occurrence, state="candidate", claim=None, now=now)

    @staticmethod
    def _require_claim(occurrence: AutomationOccurrence, token: str, now: float) -> None:
        if occurrence.state != "claimed" or occurrence.claim is None or occurrence.claim.token != token:
            raise ClaimLost("Otra parte reclamó esta ocurrencia.")
        if occurrence.claim.lease_expires_at <= now:
            raise ClaimLost("La concesión venció antes de enviar.")

    def advance(self, occurrence_id: str, target: str, *, token: str = "", run_id: str = "",
                error: str = "") -> AutomationOccurrence:
        """Move an occurrence along ``TRANSITIONS``; leaving ``claimed`` needs the live claim."""
        now = self._clock()
        with self._transaction() as conn:
            occurrence = self._occurrence(conn, occurrence_id)
            if not can_transition(occurrence.state, target):
                raise InvalidTransition(f"{occurrence.state} → {target} no está permitido.")
            if target == "completed":
                raise InvalidTransition("Completar guarda su resultado: usa finish().")
            if occurrence.state == "claimed" and target != "cancelled":
                self._require_claim(occurrence, token, now)
            changes: dict[str, Any] = {"state": target, "claim": None}
            if run_id:
                changes["run_id"] = run_id
            if error:
                changes["error"] = error[:500]
            return self._write_occurrence(conn, occurrence, now=now, **changes)

    def finish(self, occurrence_id: str, *, status: str, text: str = "", sources: list[str] | None = None,
               expires_in_s: float | None = None
               ) -> tuple[AutomationOccurrence, AutomationResult, AutomationDelivery]:
        """Result, pending delivery and final state, all in one transaction.

        The delivery expires after the rule's ``delivery.expiration_s`` unless
        ``expires_in_s`` says otherwise; expiring only stops the return notice,
        the result stays in the inbox and the history.
        """
        if status not in {"completed", "failed"}:
            raise ValueError("status: completed o failed")
        now = self._clock()
        with self._transaction() as conn:
            occurrence = self._occurrence(conn, occurrence_id)
            if not can_transition(occurrence.state, status) or occurrence.state == "claimed":
                raise InvalidTransition(f"{occurrence.state} → {status} no está permitido.")
            if expires_in_s is None:
                row = conn.execute("SELECT document FROM rules WHERE id = ?", (occurrence.rule_id,)).fetchone()
                expires_in_s = (AutomationRule.model_validate_json(row[0]).delivery.expiration_s
                                if row else 86_400.0)
            result = AutomationResult(id=new_id(), occurrence_id=occurrence.id, rule_id=occurrence.rule_id,
                                      status=status, text=text, sources=list(sources or []), finished_at=now)
            delivery = AutomationDelivery(result_id=result.id, expires_at=now + max(60.0, float(expires_in_s)))
            conn.execute("INSERT INTO results VALUES (?, ?, ?)", (result.id, occurrence.id, _dump(result)))
            conn.execute("INSERT INTO deliveries VALUES (?, ?)", (result.id, _dump(delivery)))
            updated = self._write_occurrence(conn, occurrence, state=status, claim=None, result_id=result.id, now=now)
        self._notify("result")
        return updated, result, delivery

    def mark_delivery(self, result_id: str, status: str, *, voice_receipt: str | None = None) -> AutomationDelivery:
        """Record what happened to a result: presented, read, dismissed or expired (kept for voice, F08)."""
        now = self._clock()
        order = {"pending": 0, "snoozed": 0, "presented": 1, "read": 2, "dismissed": 2, "expired": 2}
        with self._transaction() as conn:
            row = conn.execute("SELECT document FROM deliveries WHERE result_id = ?", (result_id,)).fetchone()
            if row is None:
                raise InvalidTransition("La entrega no existe.")
            delivery = AutomationDelivery.model_validate_json(row[0])
            if status not in order or status == "snoozed" or order[status] < order[delivery.status]:
                raise InvalidTransition(f"{delivery.status} → {status} no está permitido.")
            data = delivery.model_dump(by_alias=True)
            data.update(schema=DELIVERY_SCHEMA, status=status, revision=delivery.revision + 1, snoozed_until=None)
            if status in {"presented", "read"} and data["presented_at"] is None:
                data["presented_at"] = now
            if status == "read":
                data["read_at"] = now
            if status == "dismissed":
                data["dismissed_at"] = now
            if voice_receipt is not None:
                data["voice_attempts"] = min(10, delivery.voice_attempts + 1)
                data["voice_receipt"] = voice_receipt
            updated = AutomationDelivery.model_validate(data)
            conn.execute("UPDATE deliveries SET document = ? WHERE result_id = ?", (_dump(updated), result_id))
            self._history(conn, now, "delivery_" + status, result_id, {})
        self._notify("delivery")
        return updated

    # ── Inbox (F07) ─────────────────────────────────────────────────────

    def act_on_delivery(self, result_id: str, action: str, *, expected_revision: int,
                        minutes: int | None = None) -> tuple[AutomationDelivery, bool]:
        """The user's action on one delivered result: read, unread, snooze, dismiss or restore.

        It names the revision it saw. If what it asks for is already true it
        changes nothing (a repeated request after a lost answer is a no-op);
        otherwise a different revision is a ``RevisionConflict``. Snoozing never
        runs the task again and never changes its result. Returns the delivery
        and whether it changed.
        """
        if action not in DELIVERY_ACTIONS:
            raise ValueError(f"Acción desconocida: {action}")
        now = self._clock()
        with self._transaction() as conn:
            row = conn.execute("SELECT document FROM deliveries WHERE result_id = ?", (result_id,)).fetchone()
            if row is None:
                raise UnknownDelivery("Ese resultado ya no está en la bandeja.")
            delivery = AutomationDelivery.model_validate_json(row[0])
            holds = delivery_effect_holds(delivery, action, now)
            allowed = action in delivery_actions(delivery, now)
            if expected_revision != delivery.revision or not allowed:
                if holds:
                    return delivery, False
                if expected_revision != delivery.revision:
                    raise RevisionConflict("Ese resultado cambió desde que se leyó; vuelve a cargarlo.")
                raise InvalidTransition(f"No se puede «{action}» un resultado {delivery_view(delivery, now)}.")
            updated = apply_delivery_action(delivery, action, now, minutes)
            conn.execute("UPDATE deliveries SET document = ? WHERE result_id = ?", (_dump(updated), result_id))
            self._history(conn, now, "delivery_" + action, result_id, {"revision": updated.revision})
        self._notify("delivery")
        return updated, True

    def inbox_rows(self, limit: int = 100) -> list[tuple[AutomationOccurrence, str, AutomationResult | None,
                                                          AutomationDelivery | None, bool]]:
        """The sent occurrences, most recent first, with their rule's title, result, delivery and
        whether a return notice about them may have been shown without a receipt (``ambiguous``)."""
        marks = ",".join("?" for _ in INBOX_STATES)
        with self._read() as conn:
            rows = conn.execute(f"SELECT document FROM occurrences WHERE state IN ({marks})", INBOX_STATES).fetchall()
            occurrences = sorted((AutomationOccurrence.model_validate_json(row[0]) for row in rows),
                                 key=lambda o: (o.updated_at, o.id), reverse=True)[:max(1, min(500, limit))]
            titles = {rule_id: AutomationRule.model_validate_json(document).title
                      for rule_id, document in conn.execute("SELECT id, document FROM rules")}
            uncertain: set[str] = set()
            for (document,) in conn.execute("SELECT document FROM presentations WHERE state = 'ambiguous'"):
                uncertain.update(InboxPresentation.model_validate_json(document).results)
            out = []
            for occurrence in occurrences:
                result = delivery = None
                if occurrence.result_id:
                    found = conn.execute("SELECT document FROM results WHERE id = ?", (occurrence.result_id,)).fetchone()
                    result = AutomationResult.model_validate_json(found[0]) if found else None
                    found = conn.execute("SELECT document FROM deliveries WHERE result_id = ?",
                                         (occurrence.result_id,)).fetchone()
                    delivery = AutomationDelivery.model_validate_json(found[0]) if found else None
                out.append((occurrence, titles.get(occurrence.rule_id, ""), result, delivery,
                            occurrence.result_id in uncertain))
        return out

    def result_for(self, occurrence_id: str) -> str | None:
        """The result id of a finished occurrence (the inbox addresses items by occurrence)."""
        occurrence = self.get_occurrence(occurrence_id)
        return occurrence.result_id if occurrence else None

    def automation_preferences(self) -> AutomationPreferences:
        with self._read() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'automation_preferences'").fetchone()
        return AutomationPreferences.model_validate_json(row[0]) if row else AutomationPreferences()

    def set_automation_preferences(self, *, expected_revision: int, **changes) -> AutomationPreferences:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("La revisión debe ser un entero no negativo.")
        if not changes or set(changes) - {"desktop_idle", "return_voice", "focus"}:
            raise ValueError("Preferencias desconocidas.")
        if any(type(value) is not bool for value in changes.values()):
            raise ValueError("Las preferencias necesitan true o false.")
        now = self._clock()
        with self._transaction() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'automation_preferences'").fetchone()
            current = AutomationPreferences.model_validate_json(row[0]) if row else AutomationPreferences()
            if current.revision != expected_revision:
                raise RevisionConflict("Las preferencias cambiaron; vuelve a cargarlas.")
            updated = AutomationPreferences.model_validate(current.model_dump(by_alias=True) | changes
                                                          | {"revision": current.revision + 1})
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('automation_preferences', ?)", (_dump(updated),))
            self._history(conn, now, "automation_preferences", "", {"revision": updated.revision})
        self._notify("preferences")
        return updated

    def inbox_preferences(self) -> InboxPreferences:
        with self._read() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'inbox_preferences'").fetchone()
        return InboxPreferences.model_validate_json(row[0]) if row else InboxPreferences()

    def set_inbox_preferences(self, *, expected_revision: int, return_notice: bool | None = None,
                              quiet_minutes: int | None = None) -> InboxPreferences:
        """Turn the return notice on or off, or silence it for a while (0 lifts the silence)."""
        if quiet_minutes is not None and quiet_minutes not in (0, 60, 240, 720):
            raise ValueError("Silencio: 0, 60, 240 o 720 minutos")
        now = self._clock()
        with self._transaction() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'inbox_preferences'").fetchone()
            current = InboxPreferences.model_validate_json(row[0]) if row else InboxPreferences()
            if current.revision != expected_revision:
                raise RevisionConflict("Las preferencias cambiaron desde que se leyeron; vuelve a cargarlas.")
            data = current.model_dump(by_alias=True) | {"revision": current.revision + 1}
            if return_notice is not None:
                data["return_notice"] = return_notice
            if quiet_minutes is not None:
                data["quiet_until"] = now + quiet_minutes * 60 if quiet_minutes else 0.0
            updated = InboxPreferences.model_validate(data)
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('inbox_preferences', ?)", (_dump(updated),))
            self._history(conn, now, "inbox_preferences", "", {"revision": updated.revision})
        self._notify("preferences")
        return updated

    # ── Return notices (F07) ────────────────────────────────────────────

    @staticmethod
    def _presentation(conn: sqlite3.Connection, presentation_id: str) -> InboxPresentation | None:
        row = conn.execute("SELECT document FROM presentations WHERE id = ?", (presentation_id,)).fetchone()
        return InboxPresentation.model_validate_json(row[0]) if row else None

    @staticmethod
    def _write_presentation(conn: sqlite3.Connection, presentation: InboxPresentation) -> None:
        conn.execute("INSERT OR REPLACE INTO presentations VALUES (?, ?, ?, ?, ?)",
                     (presentation.id, presentation.generation, presentation.state, presentation.created_at,
                      _dump(presentation)))

    def _move_presentation(self, conn: sqlite3.Connection, presentation: InboxPresentation, state: str,
                           now: float, **changes: Any) -> InboxPresentation:
        updated = InboxPresentation.model_validate(presentation.model_dump(by_alias=True) | {
            "state": state, "updated_at": max(now, presentation.updated_at), **changes})
        self._write_presentation(conn, updated)
        self._history(conn, now, "presentation_" + state, presentation.id, {"generation": presentation.generation})
        return updated

    def claim_presentation(self, surface_id: str, *, lease_s: float = 30.0,
                           quiet: Callable[[AutomationRule], bool] = lambda rule: False
                           ) -> InboxPresentation | None:
        """Claim the return notice for ``surface_id``, or None when there is nothing to show,
        another notice holds the lease, or the user silenced it.

        What it groups: results whose delivery is pending (or a snooze that came
        back), not expired, never in a notice that may have been shown, and whose
        rule is not in its quiet hours (``quiet``); plus sent work still running
        that no notice mentioned yet. A notice whose lease ran out without a
        receipt becomes ``ambiguous``: it may have been shown, so its items are
        never presented again automatically; they stay in the inbox.
        """
        now = self._clock()
        with self._transaction() as conn:
            busy = False
            covered: dict[str, float] = {}  # result id → latest notice that may have shown it
            mentioned: set[str] = set()
            for (document,) in conn.execute("SELECT document FROM presentations"):
                presentation = InboxPresentation.model_validate_json(document)
                if presentation.state in ("claimed", "sent"):
                    if presentation.lease_expires_at > now:
                        busy = True
                    else:
                        presentation = self._move_presentation(conn, presentation, "ambiguous", now)
                age = now - presentation.updated_at
                if presentation.state in ("released", "shown", "ambiguous") and age > (
                        PRESENTATION_KEEP_S if presentation.state == "released" else PRESENTATION_MEMORY_S):
                    conn.execute("DELETE FROM presentations WHERE id = ?", (presentation.id,))
                elif presentation.state in PRESENTED_STATES:
                    for result_id in presentation.results:
                        covered[result_id] = max(covered.get(result_id, 0.0), presentation.created_at)
                    mentioned.update(presentation.running)
            if busy:
                return None
            row = conn.execute("SELECT value FROM meta WHERE key = 'inbox_preferences'").fetchone()
            preferences = InboxPreferences.model_validate_json(row[0]) if row else InboxPreferences()
            if not preferences.return_notice or preferences.quiet_until > now:
                return None
            rules = {rule_id: AutomationRule.model_validate_json(document)
                     for rule_id, document in conn.execute("SELECT id, document FROM rules")}
            results: list[tuple[float, str]] = []
            for result_id, document in conn.execute("SELECT result_id, document FROM deliveries"):
                delivery = AutomationDelivery.model_validate_json(document)
                if delivery_view(delivery, now) not in ("pending", "presented"):
                    continue
                if delivery.status == "snoozed":  # a snooze that came back: once more, after it ended
                    if covered.get(result_id, -1.0) >= (delivery.snoozed_until or 0.0):
                        continue
                elif delivery.status != "pending" or result_id in covered:
                    continue
                found = conn.execute("SELECT document FROM results WHERE id = ?", (result_id,)).fetchone()
                result = AutomationResult.model_validate_json(found[0]) if found else None
                rule = rules.get(result.rule_id) if result else None
                if result is None or (rule is not None and quiet(rule)):
                    continue
                results.append((result.finished_at, result_id))
            marks = ",".join("?" for _ in ACTIVE_STATES)
            running = sorted((AutomationOccurrence.model_validate_json(document) for (document,) in conn.execute(
                f"SELECT document FROM occurrences WHERE state IN ({marks})", ACTIVE_STATES)),
                key=lambda o: o.created_at)
            running_ids = [o.id for o in running if o.id not in mentioned][:10]
            if not results and not running_ids:
                return None
            generation = conn.execute("SELECT COALESCE(MAX(generation), 0) + 1 FROM presentations").fetchone()[0]
            presentation = InboxPresentation(
                id=new_id(), generation=generation, token=new_id(), surface_id=surface_id,
                results=[result_id for _, result_id in sorted(results)[-50:]], running=running_ids,
                created_at=now, lease_expires_at=now + max(5.0, float(lease_s)), updated_at=now)
            self._write_presentation(conn, presentation)
            self._history(conn, now, "presentation_claimed", presentation.id,
                          {"generation": generation, "surface_id": surface_id})
        return presentation

    def presentation_sent(self, presentation_id: str, token: str) -> InboxPresentation | None:
        """The socket accepted the notice: still not shown until the surface's receipt."""
        now = self._clock()
        with self._transaction() as conn:
            presentation = self._presentation(conn, presentation_id)
            if presentation is None or presentation.token != token or presentation.state != "claimed":
                return presentation
            return self._move_presentation(conn, presentation, "sent", now)

    def presentation_failed(self, presentation_id: str, token: str, *, certain: bool) -> InboxPresentation | None:
        """Sending failed. ``certain``: nothing went out (its items may be presented later);
        otherwise it may have arrived, so it becomes ``ambiguous`` and nothing is resent."""
        now = self._clock()
        with self._transaction() as conn:
            presentation = self._presentation(conn, presentation_id)
            if presentation is None or presentation.token != token or presentation.state not in ("claimed", "sent"):
                return presentation
            return self._move_presentation(conn, presentation, "released" if certain else "ambiguous", now)

    def presentation_receipt(self, presentation_id: str, *, token: str, generation: int, surface_id: str,
                             shown: bool) -> InboxPresentation:
        """The surface's receipt for its notice.

        ``shown``: it was on screen, so its results become presented (never
        read), unless the user already read or dismissed them. Not shown (the
        surface deferred it: hidden, chat in use, silence): released. A
        duplicate receipt changes nothing; a late one still records what it says
        for that notice only; one with another token, generation or surface is
        refused without changing anything.
        """
        now = self._clock()
        with self._transaction() as conn:
            presentation = self._presentation(conn, presentation_id)
            if (presentation is None or presentation.token != token or presentation.generation != generation
                    or presentation.surface_id != surface_id):
                raise PresentationRefused("Ese recibo no corresponde a ningún aviso de esta superficie.")
            if not shown:
                if presentation.state in ("claimed", "sent"):
                    presentation = self._move_presentation(conn, presentation, "released", now)
                return presentation
            if presentation.state not in ("claimed", "sent", "ambiguous"):
                return presentation  # already shown (duplicate) or released: nothing to add
            presentation = self._move_presentation(conn, presentation, "shown", now, shown_at=now)
            receipt = VisualReceipt(presentation_id=presentation.id, surface_id=surface_id, shown_at=now)
            for result_id in presentation.results:
                row = conn.execute("SELECT document FROM deliveries WHERE result_id = ?", (result_id,)).fetchone()
                if row is None:
                    continue
                delivery = AutomationDelivery.model_validate_json(row[0])
                if delivery.status not in ("pending", "snoozed", "presented"):
                    continue  # read or dismissed meanwhile: the user's own act stands
                if delivery.status == "snoozed" and (delivery.snoozed_until or 0.0) > presentation.created_at:
                    continue  # snoozed again after this notice was claimed
                updated = AutomationDelivery.model_validate(delivery.model_dump(by_alias=True) | {
                    "schema": DELIVERY_SCHEMA, "status": "presented", "revision": delivery.revision + 1,
                    "presented_at": delivery.presented_at if delivery.presented_at is not None else now,
                    "snoozed_until": None, "visual_receipt": receipt.model_dump(by_alias=True)})
                conn.execute("UPDATE deliveries SET document = ? WHERE result_id = ?", (_dump(updated), result_id))
        self._notify("presentation")
        return presentation

    def presentation(self, presentation_id: str) -> InboxPresentation | None:
        with self._read() as conn:
            return self._presentation(conn, presentation_id)

    # ── Submissions (F06) ───────────────────────────────────────────────

    def admit(self, occurrence_id: str, token: str, *, body: dict[str, Any], idempotency_key: str,
              context: dict[str, Any], max_parallel: int, quota_since: float,
              guard: Callable[[AutomationRule], str] = lambda rule: "") -> AutomationOccurrence:
        """claimed → submitted, with the request and its context recorded, before anything is sent.

        One write transaction checks everything again (Q05): the live claim,
        the rule's current revision, Iris' single place in Hermes across every
        rule and every process using this store, the rule's daily quota, and
        ``guard`` (signal and permission, Q04). Two admissions are serialised:
        the second sees the first. A crash after this commit leaves a
        submission to settle with the same key, body and context, never a
        second run.
        """
        now = self._clock()
        with self._transaction() as conn:
            occurrence = self._occurrence(conn, occurrence_id)
            self._require_claim(occurrence, token, now)
            row = conn.execute("SELECT document FROM rules WHERE id = ?", (occurrence.rule_id,)).fetchone()
            rule = AutomationRule.model_validate_json(row[0]) if row else None
            if rule is None or not rule.enabled or rule.revision != occurrence.rule_revision:
                raise AdmissionRefused("La regla cambió o se pausó antes de enviar.")
            marks = ",".join("?" for _ in ACTIVE_STATES)
            active = conn.execute(f"SELECT COUNT(*) FROM occurrences WHERE state IN ({marks}) AND id != ?",
                                  (*ACTIVE_STATES, occurrence_id)).fetchone()[0]
            if active >= max_parallel:
                raise AdmissionRefused("Ya hay otra automatización en curso.")
            sent = 0
            for (document,) in conn.execute(
                    "SELECT o.document FROM occurrences o JOIN submissions s ON s.occurrence_id = o.id "
                    "WHERE o.rule_id = ?", (occurrence.rule_id,)):
                if AutomationOccurrence.model_validate_json(document).created_at >= quota_since:
                    sent += 1
            if sent >= rule.execution.max_runs_per_day:
                raise AdmissionRefused("La regla alcanzó su límite de ejecuciones de hoy.")
            if reason := guard(rule):
                raise AdmissionRefused(reason)
            conn.execute("INSERT INTO submissions VALUES (?, ?, ?, ?)",
                         (occurrence_id, idempotency_key, json.dumps(body, sort_keys=True, ensure_ascii=False),
                          json.dumps(context, sort_keys=True, ensure_ascii=False)))
            return self._write_occurrence(conn, occurrence, state="submitted", claim=None, now=now)

    def submission(self, occurrence_id: str) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
        """The recorded request: its key, its body and the context it was admitted in."""
        with self._read() as conn:
            row = conn.execute("SELECT idempotency_key, body, context FROM submissions WHERE occurrence_id = ?",
                               (occurrence_id,)).fetchone()
        return (row[0], json.loads(row[1]), json.loads(row[2] or "{}")) if row else None

    def attach_run(self, occurrence_id: str, run_id: str, *, state: str = "submitted") -> AutomationOccurrence:
        """Record Hermes' run id (``submitted``) or that it is running."""
        now = self._clock()
        with self._transaction() as conn:
            occurrence = self._occurrence(conn, occurrence_id)
            if occurrence.state not in {"submitted", "running", "ambiguous", "stopping"} or state not in {"submitted", "running"}:
                raise InvalidTransition(f"{occurrence.state} → {state} no está permitido.")
            if occurrence.run_id and occurrence.run_id != run_id:
                raise InvalidTransition("Hermes devolvió otro run para la misma ocurrencia.")
            target = state if occurrence.state == "submitted" or state == "running" else occurrence.state
            if occurrence.state == "stopping":
                target = "stopping"  # learning the run id does not take back a cancellation
            if target == occurrence.state or can_transition(occurrence.state, target):
                return self._write_occurrence(conn, occurrence, state=target, run_id=run_id, now=now)
            raise InvalidTransition(f"{occurrence.state} → {target} no está permitido.")

    def get_occurrence(self, occurrence_id: str) -> AutomationOccurrence | None:
        with self._read() as conn:
            row = conn.execute("SELECT document FROM occurrences WHERE id = ?", (occurrence_id,)).fetchone()
        return AutomationOccurrence.model_validate_json(row[0]) if row else None

    # ── Reads ───────────────────────────────────────────────────────────

    def occurrences(self, rule_id: str | None = None, *, active_only: bool = False) -> list[AutomationOccurrence]:
        query, params = "SELECT document FROM occurrences", []
        if rule_id:
            query, params = query + " WHERE rule_id = ?", [rule_id]
        with self._read() as conn:
            rows = conn.execute(query + " ORDER BY id", params).fetchall()
        items = [AutomationOccurrence.model_validate_json(row[0]) for row in rows]
        return [item for item in items if item.state not in FINAL_STATES] if active_only else items

    def result(self, result_id: str) -> AutomationResult | None:
        with self._read() as conn:
            row = conn.execute("SELECT document FROM results WHERE id = ?", (result_id,)).fetchone()
        return AutomationResult.model_validate_json(row[0]) if row else None

    def delivery(self, result_id: str) -> AutomationDelivery | None:
        with self._read() as conn:
            row = conn.execute("SELECT document FROM deliveries WHERE result_id = ?", (result_id,)).fetchone()
        return AutomationDelivery.model_validate_json(row[0]) if row else None

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._read() as conn:
            rows = conn.execute("SELECT at, kind, ref, detail FROM history ORDER BY seq DESC LIMIT ?",
                                (max(1, min(1000, limit)),)).fetchall()
        return [{"at": at, "kind": kind, "ref": ref, "detail": json.loads(detail)} for at, kind, ref, detail in rows]

    def check(self) -> list[str]:
        """Rows that no longer validate (reported, never deleted)."""
        problems: list[str] = []
        with self._read() as conn:
            for table, model in (("rules", AutomationRule), ("occurrences", AutomationOccurrence),
                                 ("results", AutomationResult)):
                for row_id, document in conn.execute(f"SELECT id, document FROM {table}"):
                    try:
                        model.model_validate_json(document)
                    except ValidationError:
                        problems.append(f"{table}:{row_id}")
        return problems
