"""Own F15 ledger: consent/send intent/results/delivery; deterministic reminders are separate."""
import json
from pathlib import Path
import time
from core.profile_sqlite import ProfileSqliteStore, StoreSpec, profile_store_path
from core.meeting_worker import validate_result


class MeetingError(ValueError):
    pass


SPEC = StoreSpec(folder=("apps", "calendar"), filename="preparations.sqlite3", lock_name="preparations.lock",
    noun="almacén de preparación de reuniones", schema_version=1, store_name="iris.meeting-preparation/v1",
    schema=("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,document TEXT NOT NULL,day TEXT NOT NULL)"),
    path_error=MeetingError, version_error=MeetingError, busy_error=MeetingError)


class MeetingStore(ProfileSqliteStore):
    SPEC = SPEC

    @classmethod
    def for_profile(cls, home: Path, **kwargs):
        return cls(profile_store_path(SPEC, home), root=home, **kwargs)

    @staticmethod
    def _write(conn, row):
        conn.execute("UPDATE jobs SET document=? WHERE id=?", (json.dumps(row, ensure_ascii=False), row["id"]))

    def reserve(self, preview, now):
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        with self._transaction() as conn:
            old = conn.execute("SELECT document FROM jobs WHERE id=?", (preview["id"],)).fetchone()
            if old:
                return json.loads(old[0]), False
            # Hard limits apply across Core instances, even if a previous send was ambiguous.
            if conn.execute("SELECT COUNT(*) FROM jobs WHERE day=?", (day,)).fetchone()[0] >= 10:
                raise MeetingError("Límite diario: 10 preparaciones. Vuelve mañana.")
            if conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= 10000:
                raise MeetingError("El historial alcanzó su límite. No se enviaron datos.")
            rows = [json.loads(row[0]) for row in conn.execute("SELECT document FROM jobs WHERE json_extract(document,'$.state')='unknown'")]
            if any(row["deadline_at"] > now for row in rows):
                raise MeetingError("Hay una preparación en curso. Espera o cancélala.")
            job = {**preview, "state": "unknown", "error": "request_unknown", "result": None,
                   "deadline_at": now + 180, "created_at": now, "updated_at": now, "revision": 1,
                   "delivery": "pending", "read_at": None, "dismissed_at": None}
            conn.execute("INSERT INTO jobs VALUES (?,?,?)", (job["id"], json.dumps(job, ensure_ascii=False), day))
        return job, True

    def rows(self):
        with self._read() as conn:
            return [json.loads(row[0]) for row in conn.execute("SELECT document FROM jobs ORDER BY rowid DESC LIMIT 200")]

    def get(self, identity):
        with self._read() as conn:
            row = conn.execute("SELECT document FROM jobs WHERE id=?", (identity,)).fetchone()
            return json.loads(row[0]) if row else None

    def finish(self, identity, result, error, now, reported=""):
        with self._transaction() as conn:
            row = conn.execute("SELECT document FROM jobs WHERE id=?", (identity,)).fetchone()
            if not row:
                raise MeetingError("Preparación no encontrada.")
            job = json.loads(row[0])
            # Cancellation/revision wins over a late worker, also across Core instances.
            if job["state"] != "unknown":
                return job
            if result is not None:
                result = validate_result(result, job["sources"])
            job.update(state="completed" if result is not None else "cancelled" if error == "cancelled" else "failed",
                       result=result, error=error, reported_model=reported, updated_at=max(now, job["updated_at"]), revision=job["revision"] + 1)
            self._write(conn, job)
            return job

    def act(self, identity, action, revision, now):
        with self._transaction() as conn:
            record = conn.execute("SELECT document FROM jobs WHERE id=?", (identity,)).fetchone()
            if not record:
                raise MeetingError("Preparación no encontrada.")
            row = json.loads(record[0])
            if row["revision"] != revision or action not in {"read", "unread", "dismiss", "restore"}:
                raise MeetingError("La preparación cambió; vuelve a cargarla.")
            row.update(delivery={"read": "read", "unread": "pending", "dismiss": "dismissed", "restore": "pending"}[action],
                       read_at=now if action == "read" else None, dismissed_at=now if action == "dismiss" else None,
                       revision=revision + 1, updated_at=max(now, row["updated_at"]))
            self._write(conn, row)
            return row, True
