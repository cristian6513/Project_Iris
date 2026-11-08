"""F10: the profile's Google connections, in ``<profile>/connections/google/google.sqlite3``.

Opened the hardened way every profile store is (``core/profile_sqlite.py``). Core is the only
writer; the service in ``core/google_connections.py`` is the only caller.

* An account is one ``(client_id, issuer, subject)`` and has an opaque id. Adding an identity
  that is already connected is refused (``AccountExists``); reconnecting an account requires the
  same identity (``AccountChanged``) and the generation the attempt started from.
* ``generation`` changes when the connection changes (connect again, disconnect): work that
  started before cannot write over it. ``credential_revision`` changes when the credential is
  renewed; ``revision`` when the enabled services change. Every write names what it read
  (``StaleGeneration`` / ``RevisionConflict`` otherwise) and changes nothing if it no longer holds.
* The credential is an opaque blob the worker protected (DPAPI, F10.2). It is returned only by
  ``credential()`` and ``disconnect()``; records and their public form never carry it.
* Disconnecting drops the blob, unless a remote revocation was asked for: then it stays aside,
  out of every read, until the revocation's outcome is recorded for that generation.
* F10.3 (schema 2) adds what reading needs: per account and collection its checkpoint (Google's
  sync token or history id, never shown), coverage, state and backoff, and the cached records.
  A round's records and checkpoint are promoted together (``commit_round``), for the generation
  the round started from; a failure records only its state (``record_failure``).
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from core.google_contracts import (
    ACCOUNT_STATES,
    GOOGLE_ISSUERS,
    ContractError,
    MAX_EMAIL,
    MAX_LABEL,
    REMOTE_REVOCATION,
    evaluate_grant,
    new_account_id,
    normalize_scopes,
    normalize_issuer,
    public_account,
    services_in,
)
from core.profile_sqlite import ProfileSqliteStore, StoreSpec
from core.profile_sqlite import profile_store_path as _profile_store_path

SCHEMA_VERSION = 3
STORE_NAME = "iris.google-connections-store/v1"


class GoogleStoreError(RuntimeError):
    """A request the store refused without changing anything. ``code`` is a contract error code."""

    code = "invalid_request"


class GoogleStorePathError(GoogleStoreError):
    code = "client_unavailable"


class GoogleStoreVersionError(GoogleStoreError):
    code = "client_unavailable"


class UnknownAccount(GoogleStoreError):
    code = "not_found"


class AccountExists(GoogleStoreError):
    code = "account_exists"

    def __init__(self, account_id: str) -> None:
        super().__init__("Esa cuenta de Google ya está conectada.")
        self.account_id = account_id


class AccountIdentityConflict(GoogleStoreError):
    """Legacy issuer aliases already produced two rows; never choose or merge credentials."""

    code = "identity_conflict"


class AccountChanged(GoogleStoreError):
    code = "account_changed"


class RevocationPending(GoogleStoreError):
    code = "revocation_pending"


class StaleGeneration(GoogleStoreError):
    code = "stale"


class RevisionConflict(GoogleStoreError):
    code = "revision_conflict"


class ServiceNotGranted(GoogleStoreError):
    code = "service_not_granted"


class NotConnected(GoogleStoreError):
    code = "not_connected"


class StillConnected(GoogleStoreError):
    code = "still_connected"


class ClientInUse(GoogleStoreError):
    code = "client_in_use"


@dataclass(frozen=True)
class CollectionState:
    """How far a collection was read. ``checkpoint`` (Google's cursor) never leaves Core."""

    account_id: str
    service: str
    collection: str
    checkpoint: str | None
    coverage: dict
    state: str
    error_code: str
    last_success_at: float | None
    next_attempt_at: float | None
    attempts: int

    def __repr__(self) -> str:
        return f"CollectionState({self.service}, {self.collection[:24]}, {self.state})"


_COLLECTION_COLUMNS = ("account_id", "service", "collection", "checkpoint", "coverage", "state", "error_code",
                       "last_success_at", "next_attempt_at", "attempts")


def _collection(row: tuple) -> CollectionState:
    values = dict(zip(_COLLECTION_COLUMNS, row))
    values["coverage"] = json.loads(values["coverage"])
    return CollectionState(**values)


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, client_id TEXT NOT NULL, issuer TEXT NOT NULL,"
    " subject TEXT NOT NULL, email TEXT NOT NULL, label TEXT NOT NULL, requested_scopes TEXT NOT NULL,"
    " granted_scopes TEXT NOT NULL, enabled_services TEXT NOT NULL, state TEXT NOT NULL,"
    " error_code TEXT NOT NULL, generation INTEGER NOT NULL, credential_revision INTEGER NOT NULL,"
    " revision INTEGER NOT NULL, remote_revocation TEXT NOT NULL, revocation_generation INTEGER NOT NULL,"
    " credential BLOB, created_at REAL NOT NULL, updated_at REAL NOT NULL,"
    " UNIQUE (client_id, issuer, subject))",
    # What happened, never with a secret: kinds, account ids and service names only.
    "CREATE TABLE IF NOT EXISTS history (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,"
    " kind TEXT NOT NULL, ref TEXT NOT NULL, detail TEXT NOT NULL)",
    # F10.3 (schema 2): what each collection was read up to, and what was read.
    "CREATE TABLE IF NOT EXISTS collections (account_id TEXT NOT NULL, service TEXT NOT NULL,"
    " collection TEXT NOT NULL, checkpoint TEXT, coverage TEXT NOT NULL DEFAULT '{}',"
    " state TEXT NOT NULL DEFAULT 'idle', error_code TEXT NOT NULL DEFAULT '', last_success_at REAL,"
    " next_attempt_at REAL, attempts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (account_id, service, collection))",
    "CREATE TABLE IF NOT EXISTS items (account_id TEXT NOT NULL, service TEXT NOT NULL, collection TEXT NOT NULL,"
    " item_id TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY (account_id, service, collection, item_id))",
    # Only genuinely first-seen incremental records are eligible for F14. Full
    # bootstrap/recovery records are tombstoned too, so a later label change
    # cannot turn old mail into a new arrival. Kept across cache clears.
    "CREATE TABLE IF NOT EXISTS mail_arrivals (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " account_id TEXT NOT NULL, generation INTEGER NOT NULL, item_id TEXT NOT NULL,"
    " eligible INTEGER NOT NULL, UNIQUE(account_id, generation, item_id))",
    "CREATE TABLE IF NOT EXISTS mail_epochs (account_id TEXT PRIMARY KEY,value INTEGER NOT NULL)",
)

_SPEC = StoreSpec(
    folder=("connections", "google"), filename="google.sqlite3", lock_name="google.lock",
    noun="almacén de conexiones de Google", schema=_SCHEMA, schema_version=SCHEMA_VERSION,
    store_name=STORE_NAME, path_error=GoogleStorePathError, version_error=GoogleStoreVersionError,
    busy_error=GoogleStoreError,
)

_COLUMNS = ("id", "client_id", "issuer", "subject", "email", "label", "requested_scopes", "granted_scopes",
            "enabled_services", "state", "error_code", "generation", "credential_revision", "revision",
            "remote_revocation", "revocation_generation", "credential IS NOT NULL", "created_at", "updated_at")
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM accounts"


def profile_store_path(home: Path) -> Path:
    """``<profile>/connections/google/google.sqlite3``, refusing links inside the profile."""
    return _profile_store_path(_SPEC, home)


@dataclass(frozen=True)
class AuthorizationCandidate:
    """A verified authorization the worker produced: identity, scopes and the protected credential."""

    client_id: str
    issuer: str
    subject: str
    email: str
    requested_scopes: tuple[str, ...]
    granted_scopes: tuple[str, ...]
    credential: bytes

    def __repr__(self) -> str:  # never prints the subject or the credential
        return f"AuthorizationCandidate(email={self.email!r}, scopes={len(self.granted_scopes)})"


@dataclass(frozen=True)
class CredentialRef:
    """The credential of a connected account and the versions it was read at."""

    account_id: str
    credential: bytes
    generation: int
    credential_revision: int

    def __repr__(self) -> str:
        return f"CredentialRef({self.account_id}, generation={self.generation}, revision={self.credential_revision})"


@dataclass(frozen=True)
class AccountRecord:
    """An account without its credential. ``public()`` is what may leave Core."""

    id: str
    client_id: str
    issuer: str
    subject: str
    email: str
    label: str
    requested_scopes: tuple[str, ...]
    granted_scopes: tuple[str, ...]
    enabled_services: tuple[str, ...]
    state: str
    error_code: str
    generation: int
    credential_revision: int
    revision: int
    remote_revocation: str
    revocation_generation: int
    has_credential: bool
    created_at: float
    updated_at: float

    def __repr__(self) -> str:
        return f"AccountRecord({self.id}, {self.state}, generation={self.generation})"

    @property
    def granted_services(self) -> tuple[str, ...]:
        return services_in(self.granted_scopes)

    @property
    def requested_services(self) -> tuple[str, ...]:
        return services_in(self.requested_scopes)

    def public(self) -> dict[str, Any]:
        return public_account(
            account_id=self.id, label=self.label, email=self.email, state=self.state,
            error_code=self.error_code, requested_services=self.requested_services,
            granted_services=self.granted_services, enabled_services=self.enabled_services,
            generation=self.generation, revision=self.revision, remote_revocation=self.remote_revocation,
            created_at=self.created_at, updated_at=self.updated_at,
        )


def _record(row: tuple) -> AccountRecord:
    values = dict(zip(_COLUMNS, row))
    return AccountRecord(
        id=values["id"], client_id=values["client_id"], issuer=values["issuer"], subject=values["subject"],
        email=values["email"], label=values["label"],
        requested_scopes=tuple(json.loads(values["requested_scopes"])),
        granted_scopes=tuple(json.loads(values["granted_scopes"])),
        enabled_services=tuple(json.loads(values["enabled_services"])),
        state=values["state"], error_code=values["error_code"], generation=values["generation"],
        credential_revision=values["credential_revision"], revision=values["revision"],
        remote_revocation=values["remote_revocation"], revocation_generation=values["revocation_generation"],
        has_credential=bool(values["credential IS NOT NULL"]),
        created_at=values["created_at"], updated_at=values["updated_at"],
    )


def _checked_candidate(candidate: AuthorizationCandidate) -> tuple[tuple[str, ...], tuple[str, ...], str, str]:
    """The candidate's scopes, normalized, and the state its grant gives. Refuses what F10 must not store."""
    requested = normalize_scopes(candidate.requested_scopes)
    granted = normalize_scopes(candidate.granted_scopes)
    grant = evaluate_grant(services_in(requested), granted)
    if grant.error:
        raise GoogleStoreError(f"Autorización rechazada: {grant.error}")
    for value in (candidate.client_id, candidate.issuer, candidate.subject):
        if not isinstance(value, str) or not value or len(value) > 512:
            raise GoogleStoreError("Identidad de Google no válida.")
    if not isinstance(candidate.credential, (bytes, bytearray)) or not candidate.credential:
        raise GoogleStoreError("Credencial protegida no válida.")
    try:
        issuer = normalize_issuer(candidate.issuer)
    except ContractError as exc:
        raise GoogleStoreError("Identidad de Google no válida.") from exc
    return requested, granted, "partial" if grant.partial else "connected", issuer


class GoogleStore(ProfileSqliteStore):
    SPEC = _SPEC

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time, root: Path | None = None) -> None:
        self._clock = clock
        super().__init__(path, root=root)

    @classmethod
    def for_profile(cls, home: Path, **kwargs) -> GoogleStore:
        from core.profile_paths import ProfilePathError, validate_profile_home

        try:
            home = validate_profile_home(home)
            path = profile_store_path(home)
        except ProfilePathError as exc:
            raise GoogleStorePathError(str(exc)) from exc
        return cls(path, root=home, **kwargs)

    def _upgrade(self, conn: sqlite3.Connection, version: int) -> None:
        if 0 < version < SCHEMA_VERSION:  # 1 → 2 only adds the reading tables
            self._history(conn, self._clock(), "store_migrated", "", {"from": version, "to": SCHEMA_VERSION})

    def protection_key(self) -> bytes:
        """The profile's key the worker binds every credential to (with DPAPI): a credential
        copied into another profile does not open. Made once, never shown."""
        with self._transaction() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'protection_key'").fetchone()
            if row is not None:
                return bytes.fromhex(row[0])
            key = secrets.token_bytes(32)
            conn.execute("INSERT INTO meta VALUES ('protection_key', ?)", (key.hex(),))
            return key

    # ── Reads ───────────────────────────────────────────────────────────

    def oauth_client(self) -> tuple[int, bytes | None]:
        """Private configuration; never part of public_account or history."""
        with self._read() as conn:
            rows = dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('oauth_client_revision', 'oauth_client_protected')"))
        try:
            revision = int(rows.get('oauth_client_revision', '0'))
            blob = bytes.fromhex(rows['oauth_client_protected']) if rows.get('oauth_client_protected') else None
            if revision < 0 or (blob is not None and len(blob) > 128 * 1024):
                raise ValueError
            return revision, blob
        except (TypeError, ValueError) as exc:
            raise GoogleStorePathError('No se pudo leer la configuración de Google.') from exc

    def set_oauth_client(self, blob: bytes | None, *, expected_revision: int) -> int:
        if type(expected_revision) is not int or expected_revision < 0 \
                or (blob is not None and (not isinstance(blob, bytes) or not 0 < len(blob) <= 128 * 1024)):
            raise ValueError('Invalid client configuration.')
        with self._transaction() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'oauth_client_revision'").fetchone()
            revision = int(row[0]) if row else 0
            if revision != expected_revision:
                raise RevisionConflict()
            if conn.execute("SELECT 1 FROM accounts WHERE state != 'disconnected' OR remote_revocation = 'pending' LIMIT 1").fetchone():
                raise ClientInUse()
            revision += 1
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('oauth_client_revision', ?)", (str(revision),))
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('oauth_client_protected', ?)", (blob.hex() if blob else '',))
        return revision

    def accounts(self) -> list[AccountRecord]:
        with self._read() as conn:
            return [_record(row) for row in conn.execute(f"{_SELECT} ORDER BY created_at, id")]

    def account(self, account_id: str) -> AccountRecord | None:
        with self._read() as conn:
            return self._account(conn, account_id)

    def credential(self, account_id: str) -> CredentialRef | None:
        """The credential of a connected account (connected or partial), for the worker only."""
        with self._read() as conn:
            row = conn.execute(
                "SELECT credential, generation, credential_revision FROM accounts"
                " WHERE id = ? AND state IN ('connected', 'partial') AND credential IS NOT NULL",
                (account_id,)).fetchone()
        if row is None:
            return None
        return CredentialRef(account_id, bytes(row[0]), row[1], row[2])

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._read() as conn:
            rows = conn.execute("SELECT at, kind, ref, detail FROM history ORDER BY seq DESC LIMIT ?",
                                (max(1, min(1000, limit)),)).fetchall()
        return [{"at": at, "kind": kind, "ref": ref, "detail": json.loads(detail)} for at, kind, ref, detail in rows]

    # ── Writes ──────────────────────────────────────────────────────────

    @staticmethod
    def _account(conn: sqlite3.Connection, account_id: str) -> AccountRecord | None:
        row = conn.execute(f"{_SELECT} WHERE id = ?", (account_id,)).fetchone()
        return _record(row) if row else None

    @staticmethod
    def _history(conn: sqlite3.Connection, at: float, kind: str, ref: str, detail: dict[str, Any]) -> None:
        conn.execute("INSERT INTO history (at, kind, ref, detail) VALUES (?, ?, ?, ?)",
                     (at, kind, ref, json.dumps(detail, sort_keys=True)))

    def commit_authorization(self, candidate: AuthorizationCandidate, *, account_id: str | None = None,
                             expected_generation: int | None = None) -> AccountRecord:
        """Store a verified authorization.

        ``account_id`` None adds an account: an identity already connected is refused, one that
        was disconnected comes back under its id with a new generation. With ``account_id`` it
        reconnects that account: same identity and the generation the attempt started from.
        Enabled services stay as they were, limited to what is granted now.
        """
        requested, granted, state, issuer = _checked_candidate(candidate)
        email = str(candidate.email or "")[:MAX_EMAIL]
        now = self._clock()
        with self._transaction() as conn:
            # Older stores may have persisted either accepted spelling. Match the whole
            # equivalence class before deciding whether to add or reconnect, then persist
            # only the canonical issuer. Duplicate legacy rows require an explicit choice
            # by the person; we do not merge caches, ids or encrypted credentials.
            aliases = tuple(sorted(GOOGLE_ISSUERS))
            rows = conn.execute(f"{_SELECT} WHERE client_id = ? AND issuer IN (?, ?) AND subject = ?",
                                (candidate.client_id, *aliases, candidate.subject)).fetchall()
            if len(rows) > 1:
                raise AccountIdentityConflict("Hay registros duplicados de esa cuenta de Google.")
            same = _record(rows[0]) if rows else None
            if account_id is None:
                if same is not None and same.state != "disconnected":
                    raise AccountExists(same.id)
                target = same
            else:
                target = self._account(conn, account_id)
                if target is None:
                    raise UnknownAccount("Esa cuenta no existe.")
                if target.generation != expected_generation:
                    raise StaleGeneration("La cuenta cambió mientras se autorizaba.")
                if same is None or same.id != target.id:
                    raise AccountChanged("Google devolvió otra cuenta.")
            if target is not None and target.remote_revocation == "pending":
                raise RevocationPending("La revocación de esa cuenta de Google sigue pendiente.")
            if target is None:
                new_id = new_account_id()
                conn.execute(
                    "INSERT INTO accounts (id, client_id, issuer, subject, email, label, requested_scopes,"
                    " granted_scopes, enabled_services, state, error_code, generation, credential_revision,"
                    " revision, remote_revocation, revocation_generation, credential, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, '', 1, 1, 1, 'not_requested', 0, ?, ?, ?)",
                    (new_id, candidate.client_id, issuer, candidate.subject, email, email[:MAX_LABEL],
                     json.dumps(requested), json.dumps(granted), state, bytes(candidate.credential), now, now))
                self._history(conn, now, "account_connected", new_id, {"services": list(services_in(granted))})
                return self._account(conn, new_id)
            keep = [service for service in target.enabled_services if service in services_in(granted)]
            if target.state == "disconnected":
                keep = []  # coming back is a new connection: nothing is enabled until the person says so
            conn.execute(
                "UPDATE accounts SET issuer = ?, email = ?, requested_scopes = ?, granted_scopes = ?, enabled_services = ?,"
                " state = ?, error_code = '', generation = generation + 1,"
                " credential_revision = credential_revision + 1, revision = revision + 1,"
                " remote_revocation = 'not_requested', credential = ?, updated_at = ? WHERE id = ?",
                (issuer, email, json.dumps(requested), json.dumps(granted), json.dumps(keep), state,
                 bytes(candidate.credential), now, target.id))
            self._history(conn, now, "account_reconnected", target.id, {"services": list(services_in(granted))})
            return self._account(conn, target.id)

    def replace_credential(self, account_id: str, *, generation: int, credential_revision: int,
                           credential: bytes) -> AccountRecord:
        """A renewed credential, only over the one it was renewed from (same generation and revision)."""
        if not isinstance(credential, (bytes, bytearray)) or not credential:
            raise GoogleStoreError("Credencial protegida no válida.")
        now = self._clock()
        with self._transaction() as conn:
            changed = conn.execute(
                "UPDATE accounts SET credential = ?, credential_revision = credential_revision + 1,"
                " updated_at = ? WHERE id = ? AND generation = ? AND credential_revision = ?"
                " AND state IN ('connected', 'partial')",
                (bytes(credential), now, account_id, generation, credential_revision)).rowcount
            if not changed:
                raise StaleGeneration("La credencial cambió mientras se renovaba.")
            return self._account(conn, account_id)

    def mark_reauth_required(self, account_id: str, *, generation: int) -> AccountRecord:
        """Google refused the credential (invalid_grant): reconnecting is needed. Not offline, not quota."""
        now = self._clock()
        with self._transaction() as conn:
            changed = conn.execute(
                "UPDATE accounts SET state = 'reauth_required', error_code = 'reauth_required',"
                " revision = revision + 1, updated_at = ? WHERE id = ? AND generation = ?"
                " AND state IN ('connected', 'partial')", (now, account_id, generation)).rowcount
            if not changed:
                raise StaleGeneration("La cuenta cambió mientras se comprobaba.")
            self._history(conn, now, "reauth_required", account_id, {})
            return self._account(conn, account_id)

    def set_enabled_services(self, account_id: str, services: Iterable[str], *,
                             expected_revision: int) -> AccountRecord:
        """Turn granted services on or off. New permissions need another consent, not this."""
        services = tuple(services)
        now = self._clock()
        with self._transaction() as conn:
            record = self._account(conn, account_id)
            if record is None:
                raise UnknownAccount("Esa cuenta no existe.")
            if record.revision != expected_revision:
                raise RevisionConflict("La cuenta cambió desde que se leyó.")
            if record.state not in ("connected", "partial"):
                raise NotConnected("Esa cuenta no está conectada.")
            if any(service not in record.granted_services for service in services):
                raise ServiceNotGranted("Ese servicio no está concedido para esta cuenta.")
            chosen = [service for service in record.granted_services if service in services]
            conn.execute("UPDATE accounts SET enabled_services = ?, revision = revision + 1, updated_at = ?"
                         " WHERE id = ?", (json.dumps(chosen), now, account_id))
            self._history(conn, now, "services_changed", account_id, {"enabled": chosen})
            return self._account(conn, account_id)

    def disconnect(self, account_id: str, *, revoke_remote: bool = False, clear_cache: bool = False,
                   expected_revision: int | None = None) -> tuple[AccountRecord, bytes | None]:
        """Disconnect in this Iris first: a new generation, nothing enabled. Returns the credential
        only when a remote revocation was asked for (it stays aside, out of every read)."""
        now = self._clock()
        with self._transaction() as conn:
            record = self._account(conn, account_id)
            if record is None:
                raise UnknownAccount("Esa cuenta no existe.")
            if expected_revision is not None and record.revision != expected_revision:
                raise RevisionConflict("La cuenta cambió desde que se leyó.")
            if record.state == "disconnected":
                return record, None
            row = conn.execute("SELECT credential FROM accounts WHERE id = ?", (account_id,)).fetchone()
            blob = bytes(row[0]) if row and row[0] is not None else None
            revoke = bool(revoke_remote and blob)
            conn.execute(
                "UPDATE accounts SET state = 'disconnected', error_code = '', enabled_services = '[]',"
                " generation = generation + 1, revision = revision + 1, remote_revocation = ?,"
                " revocation_generation = CASE WHEN ? THEN generation + 1 ELSE revocation_generation END,"
                " credential = CASE WHEN ? THEN credential ELSE NULL END, updated_at = ? WHERE id = ?",
                ("pending" if revoke else "not_requested", revoke, revoke, now, account_id))
            if clear_cache:
                conn.execute("DELETE FROM items WHERE account_id = ?", (account_id,))
                conn.execute("DELETE FROM collections WHERE account_id = ?", (account_id,))
                conn.execute("DELETE FROM mail_arrivals WHERE account_id = ?", (account_id,))
            self._history(conn, now, "account_disconnected", account_id,
                          {"revoke_remote": revoke, "clear_cache": bool(clear_cache)})
            return self._account(conn, account_id), (blob if revoke else None)

    def record_revocation(self, account_id: str, *, revocation_generation: int, outcome: str) -> AccountRecord | None:
        """The remote revocation's outcome, for the disconnection that asked for it only."""
        if outcome not in REMOTE_REVOCATION or outcome in ("not_requested", "pending"):
            raise GoogleStoreError("Resultado de revocación no válido.")
        now = self._clock()
        with self._transaction() as conn:
            changed = conn.execute(
                "UPDATE accounts SET remote_revocation = ?,"
                " credential = CASE WHEN ? = 'confirmed' THEN NULL ELSE credential END, updated_at = ?"
                " WHERE id = ? AND state = 'disconnected' AND remote_revocation = 'pending'"
                " AND revocation_generation = ?",
                (outcome, outcome, now, account_id, revocation_generation)).rowcount
            if changed:
                self._history(conn, now, "remote_revocation", account_id, {"outcome": outcome})
            return self._account(conn, account_id)

    # ── Reading (F10.3) ─────────────────────────────────────────────────

    @staticmethod
    def _live(conn: sqlite3.Connection, account_id: str, generation: int) -> None:
        row = conn.execute("SELECT generation, state FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None or row[0] != generation or row[1] not in ("connected", "partial"):
            raise StaleGeneration("La cuenta cambió mientras se leía.")

    def collection(self, account_id: str, service: str, collection: str) -> CollectionState | None:
        with self._read() as conn:
            row = conn.execute(f"SELECT {', '.join(_COLLECTION_COLUMNS)} FROM collections"
                               " WHERE account_id = ? AND service = ? AND collection = ?",
                               (account_id, service, collection)).fetchone()
        return _collection(row) if row else None

    def collections(self, account_id: str, service: str | None = None) -> list[CollectionState]:
        with self._read() as conn:
            rows = conn.execute(f"SELECT {', '.join(_COLLECTION_COLUMNS)} FROM collections WHERE account_id = ?"
                                " AND (? IS NULL OR service = ?) ORDER BY service, collection",
                                (account_id, service, service)).fetchall()
        return [_collection(row) for row in rows]

    def commit_round(self, account_id: str, *, generation: int, service: str, collection: str,
                     upserts: dict[str, dict], deletes: Iterable[str], checkpoint: str | None,
                     coverage: dict, replace: bool, keep_collections: Iterable[str] | None = None,
                     arrivals: Iterable[str] = ()) -> None:
        """Promote one round: its records and its checkpoint together, or nothing.

        ``replace`` drops the collection's previous records first (a full read). ``keep_collections``
        (for a snapshot like the calendar list) removes the service's other collections that are
        no longer listed, with their records."""
        now = self._clock()
        with self._transaction() as conn:
            self._live(conn, account_id, generation)
            key = (account_id, service, collection)
            if service == "gmail.read" and collection == "mailbox":
                arrivals = set(arrivals)
                conn.execute("INSERT INTO mail_epochs VALUES (?,1) ON CONFLICT(account_id) DO UPDATE SET value=value+1", (account_id,))
                # Databases upgraded from schema 2 need a conservative baseline.
                conn.execute("INSERT OR IGNORE INTO mail_arrivals (account_id,generation,item_id,eligible)"
                             " SELECT account_id,?,item_id,0 FROM items WHERE account_id=? AND service=? AND collection=?",
                             (generation, *key))
                conn.executemany("INSERT OR IGNORE INTO mail_arrivals (account_id,generation,item_id,eligible)"
                                 " VALUES (?,?,?,?)", [(account_id, generation, identity, int(not replace and identity in arrivals)) for identity in upserts])
            if replace:
                conn.execute("DELETE FROM items WHERE account_id = ? AND service = ? AND collection = ?", key)
            for item_id in deletes:
                conn.execute("DELETE FROM items WHERE account_id = ? AND service = ? AND collection = ? AND item_id = ?",
                             (*key, item_id))
            conn.executemany(
                "INSERT INTO items (account_id, service, collection, item_id, document) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT (account_id, service, collection, item_id) DO UPDATE SET document = excluded.document",
                [(*key, item_id, json.dumps(document, sort_keys=True, ensure_ascii=False))
                 for item_id, document in upserts.items()])
            conn.execute(
                "INSERT INTO collections (account_id, service, collection, checkpoint, coverage, state, error_code,"
                " last_success_at, next_attempt_at, attempts) VALUES (?, ?, ?, ?, ?, 'idle', '', ?, NULL, 0)"
                " ON CONFLICT (account_id, service, collection) DO UPDATE SET checkpoint = excluded.checkpoint,"
                " coverage = excluded.coverage, state = 'idle', error_code = '', last_success_at = excluded.last_success_at,"
                " next_attempt_at = NULL, attempts = 0",
                (*key, checkpoint, json.dumps(coverage, sort_keys=True), now))
            if keep_collections is not None:
                keep = set(keep_collections) | {collection}
                gone = [row[0] for row in conn.execute(
                    "SELECT collection FROM collections WHERE account_id = ? AND service = ?", (account_id, service))
                    if row[0] not in keep]
                for name in gone:
                    conn.execute("DELETE FROM items WHERE account_id = ? AND service = ? AND collection = ?",
                                 (account_id, service, name))
                    conn.execute("DELETE FROM collections WHERE account_id = ? AND service = ? AND collection = ?",
                                 (account_id, service, name))

    def record_failure(self, account_id: str, *, generation: int, service: str, collection: str, code: str,
                       next_attempt_at: float | None) -> CollectionState:
        """A round that ended without its result: only its state changes, never data or checkpoint."""
        state = "backoff" if next_attempt_at is not None else "error"
        with self._transaction() as conn:
            self._live(conn, account_id, generation)
            conn.execute(
                "INSERT INTO collections (account_id, service, collection, state, error_code, next_attempt_at, attempts)"
                " VALUES (?, ?, ?, ?, ?, ?, 1) ON CONFLICT (account_id, service, collection) DO UPDATE SET"
                " state = excluded.state, error_code = excluded.error_code,"
                " next_attempt_at = excluded.next_attempt_at, attempts = collections.attempts + 1",
                (account_id, service, collection, state, code, next_attempt_at))
            row = conn.execute(f"SELECT {', '.join(_COLLECTION_COLUMNS)} FROM collections"
                               " WHERE account_id = ? AND service = ? AND collection = ?",
                               (account_id, service, collection)).fetchone()
            return _collection(row)

    def items(self, account_id: str, service: str, collection: str, *, after: str = "",
              limit: int = 100) -> list[tuple[str, dict]]:
        """Cached records in id order, from ``after``: what F11/F13 page through."""
        with self._read() as conn:
            rows = conn.execute("SELECT item_id, document FROM items WHERE account_id = ? AND service = ?"
                                " AND collection = ? AND item_id > ? ORDER BY item_id LIMIT ?",
                                (account_id, service, collection, after, max(1, min(1000, limit)))).fetchall()
        return [(item_id, json.loads(document)) for item_id, document in rows]

    def item(self, account_id: str, service: str, collection: str, item_id: str) -> dict | None:
        """One account-scoped cached record; no scan or new database schema."""
        with self._read() as conn:
            row = conn.execute("SELECT document FROM items WHERE account_id = ? AND service = ?"
                               " AND collection = ? AND item_id = ?", (account_id, service, collection, item_id)).fetchone()
        return json.loads(row[0]) if row else None

    def mail_frontier(self) -> int:
        with self._read() as conn:
            return conn.execute("SELECT COALESCE(MAX(seq),0) FROM mail_arrivals").fetchone()[0]

    def mail_epoch(self, account_id):
        with self._read() as conn:
            row = conn.execute("SELECT value FROM mail_epochs WHERE account_id=?", (account_id,)).fetchone()
            return row[0] if row else 0

    def mail_candidates(self, account_id: str, generation: int, after: int, *, limit=100) -> list[dict]:
        """Internal F14 feed: no OAuth/checkpoint and no sender-date heuristic."""
        with self._read() as conn:
            rows = conn.execute("SELECT a.seq,i.document FROM mail_arrivals a JOIN items i"
                " ON i.account_id=a.account_id AND i.item_id=a.item_id AND i.service='gmail.read' AND i.collection='mailbox'"
                " WHERE a.account_id=? AND a.generation=? AND a.seq>? AND a.eligible=1 ORDER BY a.seq LIMIT ?",
                (account_id, generation, after, max(1, min(limit, 100)))).fetchall()
        return [{"seq": seq, "message": json.loads(document)} for seq, document in rows]

    def counts(self, account_id: str) -> dict[str, dict[str, int]]:
        """How many records each collection holds: numbers only, for the Conexiones window."""
        with self._read() as conn:
            rows = conn.execute("SELECT service, collection, COUNT(*) FROM items WHERE account_id = ?"
                                " GROUP BY service, collection", (account_id,)).fetchall()
        counts: dict[str, dict[str, int]] = {}
        for service, collection, count in rows:
            counts.setdefault(service, {})[collection] = count
        return counts

    def clear_cache(self, account_id: str) -> None:
        with self._transaction() as conn:
            conn.execute("DELETE FROM items WHERE account_id = ?", (account_id,))
            conn.execute("DELETE FROM collections WHERE account_id = ?", (account_id,))
            conn.execute("DELETE FROM mail_arrivals WHERE account_id = ?", (account_id,))
            self._history(conn, self._clock(), "cache_cleared", account_id, {})

    def forget(self, account_id: str) -> None:
        """Remove a disconnected account and whatever credential it kept aside."""
        with self._transaction() as conn:
            record = self._account(conn, account_id)
            if record is None:
                raise UnknownAccount("Esa cuenta no existe.")
            if record.state != "disconnected":
                raise StillConnected("Primero hay que desconectar la cuenta.")
            conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            conn.execute("DELETE FROM items WHERE account_id = ?", (account_id,))
            conn.execute("DELETE FROM collections WHERE account_id = ?", (account_id,))
            conn.execute("DELETE FROM mail_arrivals WHERE account_id = ?", (account_id,))
            self._history(conn, self._clock(), "account_forgotten", account_id, {})

    def check(self) -> list[str]:
        """Rows that no longer validate (reported, never deleted)."""
        problems = []
        with self._read() as conn:
            for row in conn.execute(_SELECT):
                record = _record(row)
                if record.state not in ACCOUNT_STATES or record.remote_revocation not in REMOTE_REVOCATION:
                    problems.append(f"accounts:{record.id}")
        return problems
