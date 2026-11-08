"""F14's consent, admission ledger, results and delivery; no OAuth or full bodies."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from core.profile_sqlite import ProfileSqliteStore, StoreSpec, profile_store_path
from core.mail_classifier_worker import validate_result


class ClassificationError(ValueError):
    pass


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(default=0, ge=0)
    enabled: bool = False
    account_ids: list[str] = Field(default_factory=list, max_length=20)
    folders: list[Literal["INBOX", "STARRED", "IMPORTANT"]] = Field(default_factory=lambda: ["INBOX"], min_length=1, max_length=3)
    criteria: str = Field(default="Avisar sobre compromisos personales y asuntos que necesitan mi atención; publicidad normal.", min_length=1, max_length=2000)
    provider: str = Field(default="", max_length=100)
    model: str = Field(default="", max_length=200)
    content: Literal["snippet", "body"] = "snippet"
    interval_minutes: int = Field(default=15, ge=1, le=1440)
    daily_limit: int = Field(default=10, ge=1, le=100)


SPEC = StoreSpec(folder=("apps", "mail"), filename="classification.sqlite3", lock_name="classification.lock",
    noun="almacén de clasificación de Correo", schema_version=1, store_name="iris.mail-classification/v1",
    schema=("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,document TEXT NOT NULL,day TEXT NOT NULL)",),
    path_error=ClassificationError, version_error=ClassificationError, busy_error=ClassificationError)


class ClassificationStore(ProfileSqliteStore):
    SPEC = SPEC

    @classmethod
    def for_profile(cls, home: Path, **kwargs):
        return cls(profile_store_path(SPEC, home), root=home, **kwargs)

    @staticmethod
    def _get(conn):
        row = conn.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
        return json.loads(row[0]) if row else {"options": Options().model_dump(), "route": {}, "bindings": {}, "cursors": {}}

    @staticmethod
    def _put(conn, settings):
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('settings',?)", (json.dumps(settings, ensure_ascii=False),))

    def settings(self):
        with self._read() as conn:
            return self._get(conn)

    def configure(self, options, *, route, bindings, frontier, confirmed, profile_id="", epochs=None):
        options = Options.model_validate(options).model_dump()
        with self._transaction() as conn:
            old = self._get(conn)
            if old["options"]["revision"] != options["revision"]:
                raise ClassificationError("La clasificación cambió. Vuelve a cargarla.")
            if options["enabled"] and (not confirmed or not route or not bindings):
                raise ClassificationError("Revisa el destino y autoriza el envío antes de activar.")
            options["revision"] += 1
            settings = {"options": options, "route": route if options["enabled"] else old["route"],
                        "bindings": bindings if options["enabled"] else old["bindings"],
                        "profile_id": profile_id if options["enabled"] else old.get("profile_id", ""),
                        "baseline_epochs": epochs or {} if options["enabled"] else old.get("baseline_epochs", {}),
                        "ready_accounts": [] if options["enabled"] else old.get("ready_accounts", []),
                        "cursors": {key: frontier for key in bindings} if options["enabled"] else old["cursors"]}
            self._put(conn, settings)
        return settings

    def establish_baseline(self, revision, account, epoch, frontier):
        with self._transaction() as conn:
            settings = self._get(conn)
            if (settings["options"]["revision"] == revision and settings["options"]["enabled"]
                    and account not in settings.get("ready_accounts", [])
                    and epoch > settings.get("baseline_epochs", {}).get(account, 0)):
                settings.setdefault("ready_accounts", []).append(account)
                settings["cursors"][account] = max(frontier, settings["cursors"].get(account, 0))
                self._put(conn, settings)
                return True
        return False

    def reserve(self, settings, row, message, now):
        """One durable send intent, budget and cursor change in the same transaction."""
        revision, account = settings["options"]["revision"], row["account_id"]
        identity = hashlib.sha256(json.dumps([account, message["id"], revision]).encode()).hexdigest()[:32]
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        with self._transaction() as conn:
            current = self._get(conn)
            if not current["options"]["enabled"] or current["options"]["revision"] != revision:
                return None, "changed"
            if account not in current.get("ready_accounts", []):
                return None, "awaiting_baseline"
            if conn.execute("SELECT 1 FROM jobs WHERE id=?", (identity,)).fetchone():
                return None, "duplicate"
            active = conn.execute("SELECT document FROM jobs WHERE json_extract(document,'$.state')='unknown'").fetchall()
            if any(json.loads(row[0]).get("deadline_at", 0) > now for row in active):
                return None, "busy"
            if conn.execute("SELECT COUNT(*) FROM jobs WHERE day=?", (day,)).fetchone()[0] >= current["options"]["daily_limit"]:
                return None, "daily_limit"
            if conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= 10000:
                return None, "capacity"
            job = {"id": identity, "account_id": account, "message_id": message["id"], "remote_id": row["message"]["id"],
                   "subject": message["subject"][:120], "settings_revision": revision, "route": settings["route"],
                   "state": "unknown", "error": "request_unknown", "result": None, "correction": None,
                   "deadline_at": now + 180,
                   "created_at": now, "updated_at": now, "revision": 1,
                   "notification": False, "delivery": "pending", "read_at": None, "dismissed_at": None}
            conn.execute("INSERT INTO jobs VALUES (?,?,?)", (identity, json.dumps(job), day))
            current["cursors"][account] = max(row["seq"], current["cursors"].get(account, 0))
            self._put(conn, current)
        return job, "accepted"

    def advance(self, revision, account, sequence):
        with self._transaction() as conn:
            settings = self._get(conn)
            if settings["options"]["revision"] == revision:
                settings["cursors"][account] = max(sequence, settings["cursors"].get(account, 0))
                self._put(conn, settings)

    @staticmethod
    def _write(conn, job):
        conn.execute("UPDATE jobs SET document=? WHERE id=?", (json.dumps(job, ensure_ascii=False), job["id"]))

    def finish(self, job, result, code, now):
        with self._transaction() as conn:
            current = self._get(conn)
            if not current["options"]["enabled"] or current["options"]["revision"] != job["settings_revision"]:
                result, code = None, "cancelled"
            if result is not None:
                result = validate_result(result)
            job = {**job, "state": "completed" if result is not None else "failed", "result": result,
                   "error": code, "notification": bool(result and result["label"] == "importante"),
                   "updated_at": max(now, job["updated_at"]), "revision": job["revision"] + 1}
            self._write(conn, job)
        return job

    def rows(self):
        with self._read() as conn:
            return [json.loads(row[0]) for row in conn.execute("SELECT document FROM jobs ORDER BY rowid DESC")]

    def correct(self, identity, label, revision, now):
        if label not in {"importante", "normal", "incierto"}:
            raise ClassificationError("Elige importante, normal o incierto.")
        with self._transaction() as conn:
            row = conn.execute("SELECT document FROM jobs WHERE id=?", (identity,)).fetchone()
            job = json.loads(row[0]) if row else None
            if not job or job["revision"] != revision or job["state"] != "completed":
                raise ClassificationError("El resultado cambió o no admite corrección.")
            job.update(correction=label, notification=label == "importante", updated_at=max(now, job["updated_at"]), revision=revision + 1)
            self._write(conn, job)
        return job

    def act(self, identity, action, revision, now):
        if action not in {"read", "unread", "dismiss", "restore"}:
            raise ClassificationError("Acción de aviso no válida.")
        with self._transaction() as conn:
            row = conn.execute("SELECT document FROM jobs WHERE id=?", (identity,)).fetchone()
            job = json.loads(row[0]) if row else None
            if not job or not job["notification"] or job["revision"] != revision:
                raise ClassificationError("El aviso cambió. Vuelve a cargarlo.")
            job["delivery"] = {"read": "read", "unread": "pending", "dismiss": "dismissed", "restore": "pending"}[action]
            job["read_at"] = now if action == "read" else None
            job["dismissed_at"] = now if action == "dismiss" else None
            job.update(revision=revision + 1, updated_at=max(now, job["updated_at"]))
            self._write(conn, job)
        return job
