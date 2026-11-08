"""Notices that installed apps schedule: Core keeps them and shows them when they are due.

An app asks for a notice with a title, a text, an optional time and an optional key: from its
window or widget (``context.notify``) or from its Python backend (``iris_notices`` in a result).
Core stores it, so it is shown at its time even if the app's window was never opened again or
Iris restarted. When it is due, one visible local Iris window shows it as a card, as Calendar
reminders are shown. It also stays in the Bandeja (origin «Apps») until read or dismissed.

Who: Core names the app. Only apps installed from Iris Store may post; their name comes from
the Store, never from the notice. A window or widget is identified by the shell that gave it its
context. Apps share the page, so this is honest attribution, not isolation between apps. For
Python, Core knows which worker answered.

Against repetition:
- a key names one reminder: posting it again replaces the pending one, and cancelling it
  removes it;
- an identical notice within a minute is the same notice;
- an app keeps at most 32 pending notices and posts at most 30 an hour.

The person decides per app: «show» (card and Bandeja), «inbox» (Bandeja only) or «off» (refused).
"""

from __future__ import annotations

import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

TITLE_CHARS = 120
TEXT_CHARS = 1000
KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
APP_ID = re.compile(r"[a-z][a-z0-9-]{1,31}")
_ID = re.compile(r"[0-9a-f]{32}")
MODES = ("show", "inbox", "off")
MAX_PENDING = 32  # scheduled for later, per app
MAX_PER_HOUR = 30  # new notices per app
DUPLICATE_S = 60.0
MAX_AHEAD_S = 366 * 86400.0
# A card is a timely signal: a notice that could not be shown within this window stays in the
# Bandeja without a late card (a pomodoro that ended an hour ago is not news).
PRESENT_WINDOW_S = 600.0
KEEP_S = 30 * 86400.0
MAX_ATTEMPTS = 5  # a window that deferred the card (another card, a full-screen game) gets it again
MAX_BACKEND_NOTICES = 8  # per backend answer


# The app's current mode allows a card: no row, or «show».
_SHOWN = ("NOT EXISTS (SELECT 1 FROM app_notice_modes m WHERE m.app_id=app_notices.app_id AND m.mode!='show')")


class NoticeRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def _now() -> float:
    return time.time()


def _clean(value: Any, limit: int, what: str) -> str:
    if not isinstance(value, str):
        raise NoticeRefused("invalid", f"{what}: se esperaba texto.")
    text = value.replace("\r\n", "\n").strip()
    if any(ord(char) < 32 and char != "\n" for char in text):
        raise NoticeRefused("invalid", f"{what}: sin caracteres de control.")
    if len(text) > limit:
        raise NoticeRefused("invalid", f"{what}: hasta {limit} caracteres.")
    return text


def checked_request(raw: Any, *, now: float) -> dict[str, Any]:
    """A notice (``title``, ``text``, ``at``, ``key``) or a cancel (``key``, ``cancel``), validated."""
    if not isinstance(raw, dict) or not set(raw) <= {"title", "text", "at", "key", "cancel"}:
        raise NoticeRefused("invalid", "Un aviso lleva title, text, at y key; para cancelarlo, key y cancel.")
    key = raw.get("key", "")
    if key not in ("", None) and (not isinstance(key, str) or not KEY.fullmatch(key)):
        raise NoticeRefused("invalid", "key: letras, números y _ . : -, hasta 64.")
    key = key or ""
    if raw.get("cancel") is True:
        if not key or set(raw) - {"key", "cancel"}:
            raise NoticeRefused("invalid", "Para cancelar un aviso basta su key.")
        return {"cancel": True, "key": key}
    if "cancel" in raw:
        raise NoticeRefused("invalid", "cancel: solo true.")
    title = _clean(raw.get("title"), TITLE_CHARS, "title")
    if not title:
        raise NoticeRefused("invalid", "title: el aviso necesita un título.")
    text = _clean(raw.get("text", ""), TEXT_CHARS, "text")
    at = raw.get("at", 0)
    if isinstance(at, bool) or not isinstance(at, (int, float)) or at != at or at < 0:
        raise NoticeRefused("invalid", "at: segundos desde 1970, o 0 para ahora.")
    if at > now + MAX_AHEAD_S:
        raise NoticeRefused("invalid", "at: como mucho dentro de un año.")
    return {"cancel": False, "key": key, "title": title, "text": text, "due_at": max(float(at), now) if at else now}


class AppNoticeStore:
    """Each app notice, its delivery and the person's choice per app. Synchronous: run in a thread."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS app_notices ("
                "id TEXT PRIMARY KEY, app_id TEXT NOT NULL, app_name TEXT NOT NULL, key TEXT NOT NULL,"
                "title TEXT NOT NULL, text TEXT NOT NULL, due_at REAL NOT NULL, created_at REAL NOT NULL,"
                "updated_at REAL NOT NULL, state TEXT NOT NULL, presentation_token TEXT NOT NULL DEFAULT '',"
                "presentation_surface TEXT NOT NULL DEFAULT '', presentation_attempted_at REAL,"
                "presented_at REAL, inbox_status TEXT NOT NULL DEFAULT 'pending', read_at REAL,"
                "dismissed_at REAL, revision INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0)")
            conn.execute("CREATE INDEX IF NOT EXISTS app_notices_due ON app_notices (state, due_at)")
            conn.execute("CREATE TABLE IF NOT EXISTS app_notice_modes ("
                         "app_id TEXT PRIMARY KEY, mode TEXT NOT NULL, updated_at REAL NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _rows(self, where: str, args: tuple = ()) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            return [dict(row) for row in conn.execute(
                f"SELECT * FROM app_notices WHERE {where} ORDER BY due_at, created_at", args)]

    def get(self, notice_id: str) -> dict[str, Any] | None:
        found = self._rows("id=?", (str(notice_id),)) if _ID.fullmatch(str(notice_id)) else []
        return found[0] if found else None

    # ── The person's choice per app ──────────────────────────────────────

    def mode(self, app_id: str) -> str:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT mode FROM app_notice_modes WHERE app_id=?", (app_id,)).fetchone()
        return row["mode"] if row else "show"

    def modes(self) -> dict[str, str]:
        with closing(self._connect()) as conn:
            return {row["app_id"]: row["mode"] for row in conn.execute("SELECT * FROM app_notice_modes")}

    def set_mode(self, app_id: str, mode: str) -> None:
        if not APP_ID.fullmatch(str(app_id)) or mode not in MODES:
            raise NoticeRefused("invalid", "Elige show, inbox u off para una app válida.")
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT INTO app_notice_modes VALUES (?,?,?) ON CONFLICT(app_id) "
                         "DO UPDATE SET mode=excluded.mode, updated_at=excluded.updated_at", (app_id, mode, _now()))

    # ── What apps ask for ────────────────────────────────────────────────

    def post(self, app_id: str, app_name: str, request: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        """Schedule a checked notice; answers ``status`` (scheduled, replaced, duplicate) and the notice."""
        now = _now() if now is None else now
        if self.mode(app_id) == "off":
            raise NoticeRefused("off", "Desactivaste los avisos de esta app.")
        key, title, text, due_at = request["key"], request["title"], request["text"], request["due_at"]
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            # Retention: cancelled notices go after a day; read or dismissed ones after a month.
            conn.execute("DELETE FROM app_notices WHERE (state='cancelled' AND updated_at<?) OR "
                         "(inbox_status IN ('read','dismissed') AND updated_at<?)", (now - 86400, now - KEEP_S))
            if key:
                current = conn.execute("SELECT * FROM app_notices WHERE app_id=? AND key=? AND state='scheduled' "
                                       "AND due_at>?", (app_id, key, now)).fetchone()
                if current is not None:  # the same reminder again: it moves, it does not multiply
                    conn.execute("UPDATE app_notices SET title=?, text=?, due_at=?, app_name=?, updated_at=?, "
                                 "revision=revision+1 WHERE id=?", (title, text, due_at, app_name, now, current["id"]))
                    return {"status": "replaced", "notice": dict(conn.execute(
                        "SELECT * FROM app_notices WHERE id=?", (current["id"],)).fetchone())}
            same = conn.execute("SELECT * FROM app_notices WHERE app_id=? AND key=? AND title=? AND text=? "
                                "AND state='scheduled' AND created_at>? AND abs(due_at-?)<?",
                                (app_id, key, title, text, now - DUPLICATE_S, due_at, DUPLICATE_S)).fetchone()
            if same is not None:
                return {"status": "duplicate", "notice": dict(same)}
            recent = conn.execute("SELECT count(*) FROM app_notices WHERE app_id=? AND created_at>?",
                                  (app_id, now - 3600)).fetchone()[0]
            if recent >= MAX_PER_HOUR:
                raise NoticeRefused("rate", "Esta app ya envió muchos avisos en la última hora.")
            pending = conn.execute("SELECT count(*) FROM app_notices WHERE app_id=? AND state='scheduled' "
                                   "AND due_at>?", (app_id, now)).fetchone()[0]
            if due_at > now and pending >= MAX_PENDING:
                raise NoticeRefused("limit", f"Esta app ya tiene {MAX_PENDING} avisos programados.")
            notice_id = secrets.token_hex(16)
            conn.execute("INSERT INTO app_notices (id, app_id, app_name, key, title, text, due_at, created_at, "
                         "updated_at, state) VALUES (?,?,?,?,?,?,?,?,?, 'scheduled')",
                         (notice_id, app_id, app_name, key, title, text, due_at, now, now))
            return {"status": "scheduled",
                    "notice": dict(conn.execute("SELECT * FROM app_notices WHERE id=?", (notice_id,)).fetchone())}

    def cancel(self, app_id: str, key: str, *, now: float | None = None) -> bool:
        """Cancel the app's pending notice with ``key``; one already due stays in the Bandeja."""
        now = _now() if now is None else now
        with closing(self._connect()) as conn, conn:
            return conn.execute("UPDATE app_notices SET state='cancelled', updated_at=?, revision=revision+1 "
                                "WHERE app_id=? AND key=? AND state='scheduled' AND due_at>?",
                                (now, app_id, key, now)).rowcount > 0

    def forget_app(self, app_id: str, *, now: float | None = None) -> int:
        """An uninstalled app's future notices never fire; what already arrived stays readable."""
        now = _now() if now is None else now
        with closing(self._connect()) as conn, conn:
            return conn.execute("UPDATE app_notices SET state='cancelled', updated_at=?, revision=revision+1 "
                                "WHERE app_id=? AND state='scheduled' AND due_at>?", (now, app_id, now)).rowcount

    def scheduled(self, app_id: str, *, now: float | None = None) -> list[dict[str, Any]]:
        now = _now() if now is None else now
        return self._rows("app_id=? AND state='scheduled' AND due_at>?", (app_id, now))

    # ── Showing them ─────────────────────────────────────────────────────

    def next_due(self, *, now: float | None = None) -> float | None:
        """When the next notice falls due; what is already due is ``presentable``'s."""
        now = _now() if now is None else now
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT min(due_at) FROM app_notices WHERE state='scheduled' AND due_at>?",
                               (now,)).fetchone()
        return row[0] if row and row[0] is not None else None

    def presentable(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """Due notices of apps set to «show», not yet offered to a window and still timely."""
        now = _now() if now is None else now
        rows = self._rows("state='scheduled' AND presentation_attempted_at IS NULL AND inbox_status='pending' "
                          "AND due_at<=? AND due_at>?", (now, now - PRESENT_WINDOW_S))
        modes = self.modes()
        return [row for row in rows if modes.get(row["app_id"], "show") == "show"]

    def claim(self, notice_id: str, surface_id: str, *, now: float | None = None) -> dict[str, Any] | None:
        """Offer one notice to one window, once: written before the window is told.

        The app's mode is read again in the same statement: a card chosen just before the person
        set «Solo en la Bandeja» or «Desactivados» is not offered (Codex SDK05)."""
        now = _now() if now is None else now
        token = secrets.token_hex(16)
        with closing(self._connect()) as conn, conn:
            changed = conn.execute("UPDATE app_notices SET presentation_token=?, presentation_surface=?, "
                                   "presentation_attempted_at=?, attempts=attempts+1 WHERE id=? AND state='scheduled' AND "
                                   "presentation_attempted_at IS NULL AND due_at<=? AND " + _SHOWN,
                                   (token, surface_id, now, notice_id, now)).rowcount
        return self.get(notice_id) if changed else None

    def receipt(self, notice_id: str, *, token: str, surface_id: str, shown: bool,
                now: float | None = None) -> bool:
        """The window that was offered the notice says whether it drew the card.

        A deferred card is offered again, a few times, while it is still timely."""
        if not _ID.fullmatch(str(notice_id)) or not _ID.fullmatch(str(token)):
            return False
        now = _now() if now is None else now
        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT presentation_token, presentation_surface, presented_at, attempts "
                               "FROM app_notices WHERE id=?", (notice_id,)).fetchone()
            if (row is None or not secrets.compare_digest(row["presentation_token"], token)
                    or row["presentation_surface"] != surface_id):
                return False
            if shown and row["presented_at"] is None:
                conn.execute("UPDATE app_notices SET presented_at=?, revision=revision+1, updated_at=? WHERE id=?",
                             (now, now, notice_id))
            elif not shown and row["presented_at"] is None and row["attempts"] < MAX_ATTEMPTS:
                conn.execute("UPDATE app_notices SET presentation_token='', presentation_surface='', "
                             "presentation_attempted_at=NULL WHERE id=?", (notice_id,))
            return True

    def public(self, notice_id: str, *, now: float | None = None) -> dict[str, Any] | None:
        """What a card shows: a due, uncancelled notice of an app still set to «show», without its
        delivery secrets. A card offered before the mode changed finds nothing to draw (SDK05)."""
        now = _now() if now is None else now
        row = self.get(notice_id)
        if row is None or row["state"] != "scheduled" or row["due_at"] > now or self.mode(row["app_id"]) != "show":
            return None
        return {"id": row["id"], "app_id": row["app_id"], "app_name": row["app_name"], "title": row["title"],
                "text": row["text"], "due_at": row["due_at"]}

    # ── The Bandeja ──────────────────────────────────────────────────────

    def inbox_rows(self, *, now: float | None = None) -> list[dict[str, Any]]:
        now = _now() if now is None else now
        return self._rows("state='scheduled' AND due_at<=?", (now,))

    def act(self, notice_id: str, action: str, *, expected_revision: int,
            now: float | None = None) -> tuple[dict[str, Any], bool]:
        """Bandeja actions on an arrived notice; a repeated action changes nothing."""
        if not _ID.fullmatch(str(notice_id)):
            raise KeyError(notice_id)
        target = {"read": "read", "unread": "pending", "dismiss": "dismissed", "restore": "pending"}.get(action)
        if target is None:
            raise ValueError("Acción no admitida en la Bandeja.")
        now = _now() if now is None else now
        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT * FROM app_notices WHERE id=? AND state='scheduled' AND due_at<=?",
                               (notice_id, now)).fetchone()
            if row is None:
                raise KeyError(notice_id)
            if int(row["revision"]) != int(expected_revision):
                raise ValueError("revision")
            if row["inbox_status"] == target:
                return dict(row), False
            conn.execute("UPDATE app_notices SET inbox_status=?, revision=revision+1, updated_at=?, "
                         "read_at=CASE WHEN ?='read' THEN ? ELSE read_at END, "
                         "dismissed_at=CASE WHEN ?='dismissed' THEN ? ELSE dismissed_at END WHERE id=?",
                         (target, now, target, now, target, now, notice_id))
            return dict(conn.execute("SELECT * FROM app_notices WHERE id=?", (notice_id,)).fetchone()), True


def public_notice(row: dict[str, Any]) -> dict[str, Any]:
    """What an app learns about its own notice."""
    return {"id": row["id"], "key": row["key"], "title": row["title"], "due_at": row["due_at"]}


def apply_requests(store: AppNoticeStore, app_id: str, app_name: str, requests: Any,
                   *, now: float | None = None) -> dict[str, Any]:
    """Notices a Python backend returned under ``iris_notices``: each one applied or refused alone."""
    now = _now() if now is None else now
    summary = {"scheduled": 0, "cancelled": 0, "refused": []}
    if not isinstance(requests, list) or len(requests) > MAX_BACKEND_NOTICES:
        summary["refused"].append(f"iris_notices: una lista de hasta {MAX_BACKEND_NOTICES} avisos.")
        return summary
    for raw in requests:
        try:
            request = checked_request(raw, now=now)
            if request["cancel"]:
                summary["cancelled"] += int(store.cancel(app_id, request["key"], now=now))
            else:
                store.post(app_id, app_name, request, now=now)
                summary["scheduled"] += 1
        except NoticeRefused as exc:
            summary["refused"].append(exc.message)
    return summary


class NoticeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app: str = Field(pattern=APP_ID.pattern)
    # The installation whose window or widget asks: a view left over from an uninstalled or
    # updated version cannot schedule anything (Codex SDK03).
    installation: str = Field(pattern=r"^ins_[0-9a-f]{32}$")
    title: str = ""
    text: str = ""
    at: float = 0
    key: str = ""


class ModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str


def app_notices_router(*, store: Callable[[], AppNoticeStore | None], app_name: Callable[[str], str | None],
                       installed: Callable[[str, str, Callable[[str], Any]], Awaitable[Any]],
                       changed: Callable[[], None], require_local: Callable[[Request], None]) -> APIRouter:
    """``/api/app-notices``: what the shell asks for an app, the cards and the per-app choice.

    ``app_name`` names an app installed from Iris Store, or None for any other id.
    ``installed(app, installation, work)`` runs ``work(name)`` only while that installation is the
    app's current one, coordinated with uninstalling it; None otherwise.
    """
    import asyncio

    router = APIRouter(prefix="/api/app-notices", dependencies=[Depends(require_local)])

    def current() -> AppNoticeStore:
        value = store()
        if value is None:
            raise HTTPException(503, detail={"code": "unavailable", "message": "Los avisos de apps no están disponibles."})
        return value

    async def named(app_id: str) -> str:
        name = await asyncio.to_thread(app_name, app_id) if APP_ID.fullmatch(app_id) else None
        if not name:
            raise HTTPException(404, detail={"code": "unknown_app",
                                             "message": "Solo las apps instaladas desde Iris Store pueden avisar."})
        return name

    def refused(exc: NoticeRefused) -> HTTPException:
        return HTTPException(429 if exc.code == "rate" else 409 if exc.code in ("off", "limit") else 400,
                             detail={"code": exc.code, "message": exc.message})

    @router.post("")
    async def post(body: NoticeRequest):
        notices = current()
        try:
            request = checked_request(body.model_dump(exclude={"app", "installation"}), now=_now())
            answer = await installed(body.app, body.installation,
                                     lambda name: notices.post(body.app, name, request))
        except NoticeRefused as exc:
            raise refused(exc) from None
        if answer is None:
            raise HTTPException(404, detail={"code": "unknown_app", "message":
                                             "Esta versión de la app ya no está instalada; vuelve a abrirla."})
        changed()
        return {"status": answer["status"], "notice": public_notice(answer["notice"]),
                "mode": await asyncio.to_thread(notices.mode, body.app)}

    @router.delete("/{app_id}/{key}")
    async def cancel(app_id: str, key: str):
        await named(app_id)
        if not KEY.fullmatch(key):
            raise HTTPException(400, detail={"code": "invalid", "message": "key no válida."})
        cancelled = await asyncio.to_thread(current().cancel, app_id, key)
        changed()
        return {"cancelled": cancelled}

    @router.get("/scheduled/{app_id}")
    async def scheduled(app_id: str):
        await named(app_id)
        rows = await asyncio.to_thread(current().scheduled, app_id)
        return {"notices": [public_notice(row) for row in rows]}

    @router.get("/modes")
    async def modes():
        return {"modes": await asyncio.to_thread(current().modes), "default": "show"}

    @router.put("/modes/{app_id}")
    async def set_mode(app_id: str, body: ModeRequest):
        await named(app_id)
        try:
            await asyncio.to_thread(current().set_mode, app_id, body.mode)
        except NoticeRefused as exc:
            raise refused(exc) from None
        changed()
        return {"app": app_id, "mode": body.mode}

    @router.get("/{notice_id}")
    async def card(notice_id: str):
        value = await asyncio.to_thread(current().public, notice_id)
        if value is None:
            raise HTTPException(404, detail={"code": "unknown", "message": "Aviso no disponible."})
        return value

    return router
