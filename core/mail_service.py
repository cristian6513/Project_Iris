"""F13: read-only Gmail projection over F10's single Google owner.

No separate worker, OAuth flow, HTML renderer, AI or new store. Local
pages have a snapshot fingerprint: a changed cache invalidates the next cursor
instead of silently skipping/duplicating records across incremental sync rounds.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import re
from typing import Any

from core.google_connections import GoogleConnectionsService, GoogleServiceError
from core.mail_body import BIDI_CONTROLS

SCHEMA = "iris.mail/v1"
FOLDERS = {"inbox": "INBOX", "starred": "STARRED", "sent": "SENT", "drafts": "DRAFT", "all": None}
FILTERS = frozenset({"all", "unread", "important"})
_KEY = re.compile(r"^mail_[a-f0-9]{40}$")
WARNINGS = {
    "cache_limit": "Hay más mensajes guardados de los que puede mostrar esta vista.",
    "invalid_message": "No se pudieron mostrar algunos mensajes guardados.",
    "account_changed": "Una cuenta cambió durante la lectura. Actualiza la vista.",
}


class MailError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.status = code, status


def text(value: Any, limit: int) -> str:
    # Same character policy as bodies: no controls and no bidi overrides in subjects/senders
    # (review 2026-10-19: a subject could display a file name reversed).
    if not isinstance(value, str):
        return ""
    return "".join(char for char in html.unescape(value[:limit])
                   if char in "\n\t" or (ord(char) >= 32 and ord(char) != 127 and char not in BIDI_CONTROLS))[:limit]


def live(account: dict) -> bool:
    return account.get("auth_state") in ("connected", "partial") and account.get("services", {}).get("gmail.read", {}).get("granted") is True


def project_message(account_id: str, raw: dict) -> dict | None:
    identity = raw.get("id")
    if not isinstance(identity, str) or not 0 < len(identity) <= 512 or any(ord(c) < 32 for c in identity):
        return None
    headers = raw.get("headers") if isinstance(raw.get("headers"), dict) else {}
    labels = raw.get("labelIds") if isinstance(raw.get("labelIds"), list) else []
    try:
        date = int(raw.get("internalDate", ""))
        if not 0 <= date <= 253402300799999:
            date = None
    except (ValueError, TypeError, OverflowError):
        date = None
    key = "mail_" + hashlib.sha256(json.dumps([account_id, identity], separators=(",", ":")).encode()).hexdigest()[:40]
    return {"id": key, "account_id": account_id, "subject": text(headers.get("subject"), 1000) or "Sin asunto",
            "sender": text(headers.get("from"), 1000) or "Remitente no indicado", "to": text(headers.get("to"), 1000),
            "date_header": text(headers.get("date"), 200), "date_ms": date,
            "snippet": text(raw.get("snippet"), 500), "content_kind": "snippet",
            "unread": "UNREAD" in labels, "starred": "STARRED" in labels, "important": "IMPORTANT" in labels,
            "folders": [folder for folder, label in FOLDERS.items() if label and label in labels]}


class MailService:
    def __init__(self, google: GoogleConnectionsService | None):
        self.google = google

    async def _cache(self) -> dict:
        if self.google is None:
            return {"available": False, "accounts": [], "messages": [], "warnings": []}
        return await self.google.mail_cache()

    @staticmethod
    def _project(cache: dict) -> dict:
        accounts, messages, warnings = [], [], []
        eligible = set()
        for raw in cache.get("accounts", []):
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                continue
            sync = (raw.get("sync") or {}).get("gmail.read") or {}
            granted = raw.get("services", {}).get("gmail.read", {}).get("granted") is True
            status = {key: sync.get(key) for key in ("state", "last_success_at", "next_attempt_at", "coverage")}
            status["error"] = {"code": text(sync["error"].get("code"), 80)} if isinstance(sync.get("error"), dict) else None
            accounts.append({"id": raw["id"], "email": text(raw.get("email"), 320), "auth_state": raw.get("auth_state"),
                             "granted": granted, "sync": status,
                             "sync_enabled": (
                                 raw.get("services", {}).get("gmail.read", {}).get("enabled") is True
                             )})
            if live(raw):
                eligible.add(raw["id"])
        for row in cache.get("messages", []):
            if not isinstance(row, dict) or row.get("account_id") not in eligible:
                continue
            value = project_message(row["account_id"], row.get("message") or {})
            if value:
                messages.append(value)
            else:
                warnings.append({"code": "invalid_message", "account_id": row["account_id"]})
        messages = list({message["id"]: message for message in messages}.values())
        messages.sort(key=lambda message: (-(message["date_ms"] or 0), message["id"]))
        for warning in cache.get("warnings", []):
            if isinstance(warning, dict) and warning.get("code") in WARNINGS:
                warnings.append({key: warning[key] for key in ("code", "account_id") if key in warning})
        return {"schema": SCHEMA, "available": cache.get("available") is True, "read_only": True,
                "content_kind": "snippet", "accounts": accounts, "messages": messages,
                "warnings": [dict(item, message=WARNINGS.get(item["code"], WARNINGS["invalid_message"]))
                             for item in {json.dumps(item, sort_keys=True): item for item in warnings}.values()]}

    async def snapshot(self, *, account_id: str = "", folder: str = "inbox", filter: str = "all",
                       query: str = "", cursor: str = "", limit: int = 40, focus_id: str = "") -> dict:
        if (not isinstance(account_id, str) or len(account_id) > 100 or folder not in FOLDERS or filter not in FILTERS
                or not isinstance(query, str) or len(query) > 200 or type(limit) is not int or not 1 <= limit <= 100
                or not isinstance(cursor, str) or len(cursor) > 256
                or not isinstance(focus_id, str) or (focus_id and not _KEY.fullmatch(focus_id))):
            raise MailError("invalid_request", "Los filtros de correo no son válidos.")
        return await asyncio.to_thread(self._page, await self._cache(), account_id, folder, filter, query, cursor, limit, focus_id)

    @classmethod
    def _page(cls, cache: dict, account_id: str, folder: str, filter: str, query: str, cursor: str, limit: int, focus_id: str = "") -> dict:
        # Sorting, searching and hashing a large history cache must not block Core's event loop.
        data = cls._project(cache)
        if account_id and account_id not in {account["id"] for account in data["accounts"]}:
            raise MailError("account_changed", "La cuenta ya no está en Conexiones.", 409)
        all_rows = [row for row in data.pop("messages") if not account_id or row["account_id"] == account_id]
        counts = {name: sum(name == "all" or name in row["folders"] for row in all_rows) for name in FOLDERS}
        term = query.casefold().strip()
        rows = [row for row in all_rows if (folder == "all" or folder in row["folders"])
                and (filter == "all" or row[filter])
                and (not term or term in " ".join(row[key] for key in ("sender", "to", "subject", "snippet")).casefold())]
        stamp = hashlib.sha256(json.dumps([account_id, folder, filter, query, limit, rows],
                                          sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]
        offset = 0
        if focus_id and not cursor:
            index = next((index for index, row in enumerate(rows) if row["id"] == focus_id), None)
            if index is None:
                raise MailError("not_found", "El mensaje ya no está disponible en esta cuenta.", 404)
            offset = index // limit * limit
        if cursor:
            try:
                token = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                if not isinstance(token, list) or len(token) != 2 or type(token[1]) is not int:
                    raise ValueError
                if token[0] != stamp:
                    raise MailError("page_changed", "El correo cambió. Vuelve a la primera página.", 409)
                if not 0 < token[1] < len(rows):
                    raise ValueError
                offset = token[1]
            except MailError:
                raise
            except (ValueError, TypeError, UnicodeError) as exc:
                raise MailError("invalid_cursor", "La página de correo no es válida.") from exc
        next_offset = offset + limit
        next_cursor = base64.urlsafe_b64encode(json.dumps([stamp, next_offset]).encode()).decode().rstrip("=") if next_offset < len(rows) else None
        return {**data, "messages": rows[offset:next_offset], "counts": counts, "total": len(rows), "offset": offset,
                "next_cursor": next_cursor, "query": query, "folder": folder, "filter": filter, "account_id": account_id}

    async def detail(self, key: str) -> dict:
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise MailError("invalid_request", "El mensaje no es válido.")
        data = await asyncio.to_thread(self._project, await self._cache())
        message = next((row for row in data["messages"] if row["id"] == key), None)
        if message is None:
            raise MailError("not_found", "El mensaje ya no está disponible en esta cuenta.", 404)
        return {"schema": SCHEMA, "read_only": True, "message": message}

    async def body(self, key: str) -> dict:
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise MailError("invalid_request", "El mensaje no es válido.")
        identity = await asyncio.to_thread(self._body_identity, await self._cache(), key)
        if identity is not None:
            account_id, message_id = identity
            body = await self.google.mail_body(account_id, message_id)
            return {"schema": SCHEMA, "read_only": True, "message_id": key, "account_id": account_id, "body": body}
        raise MailError("not_found", "El mensaje ya no está disponible en esta cuenta.", 404)

    @staticmethod
    def _body_identity(cache: dict, key: str):
        eligible = {row["id"] for row in cache.get("accounts", []) if live(row)}
        for row in cache.get("messages", []):
            if row.get("account_id") not in eligible:
                continue
            message = project_message(row["account_id"], row.get("message") or {})
            if message and message["id"] == key:
                return row["account_id"], row["message"]["id"]
        return None

    async def sync(self, account_ids: list[str]) -> dict:
        if (not isinstance(account_ids, list) or not account_ids or len(account_ids) > 100
                or any(not isinstance(value, str) for value in account_ids) or len(set(account_ids)) != len(account_ids)):
            raise MailError("invalid_request", "Selecciona cuentas válidas para actualizar Correo.")
        if self.google is None:
            raise GoogleServiceError("client_unavailable")
        cache = await self._cache()
        eligible = {raw["id"] for raw in cache.get("accounts", []) if live(raw)}
        if any(identity not in eligible for identity in account_ids):
            raise GoogleServiceError("service_not_granted")
        accepted, errors = [], []
        for identity in account_ids:
            try:
                await self.google.sync(identity, ["gmail.read"])
                accepted.append(identity)
            except GoogleServiceError as exc:
                errors.append({"account_id": identity, "code": exc.code})
        return {"accepted": bool(accepted), "account_ids": accepted, "errors": errors}
