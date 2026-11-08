"""F10.2: Iris' Google worker. Run by Hermes' Python (the private one when packaged), never Iris'.

One job per process. Core starts it, writes one request line (``iris.google-rpc/v1``) on stdin
and keeps stdin open while it still wants the job: EOF means its owner is gone or cancelled, and
the process ends at once, loopback listener included. stdout carries protocol lines only (the
libraries' own prints go to stderr, which Core discards); no secret is ever printed.

Jobs:

* ``probe``: which Google libraries this Python has and whether DPAPI works. No network.
* ``auth.begin``: Desktop OAuth in the system browser. The loopback listener (127.0.0.1, an
  ephemeral port, one fixed path) is ready before the consent URL is emitted. PKCE S256, a
  random ``state`` and ``nonce``; a callback with another state is refused (and after a few,
  the attempt ends); one answer only. The code is exchanged with google-auth-oauthlib and the
  ID token verified with google-auth (signature, issuer, audience, expiry) plus its nonce. The
  credential is protected with DPAPI for this Windows user and the profile's key, and only then
  returned, with the verified identity, on the private part of the line.
* ``credential.refresh`` / ``credential.revoke``: open the credential, renew it (keeping the
  refresh token when Google sends none) or revoke it, and say what happened.
* ``sync.calendar_list`` / ``sync.calendar_events`` / ``sync.gmail`` (F10.3): one read round,
  its pages streamed as ``page`` lines and its cursor in the final ``result``. Read-only calls
  only (list and get, Gmail metadata), with Google's static discovery documents. A token renewed
  during the round is reported (``refreshed``) before anything else is said.

Google's endpoints are constants: no configuration can point the worker anywhere else.
Standard library and the Google libraries Hermes already ships; siblings are loaded by path,
because the private interpreter runs isolated and its sys.path holds neither Iris nor this folder.
"""

from __future__ import annotations

import base64
import datetime
import hmac
import importlib.util
import json
import math
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

HERE = Path(__file__).resolve().parent


def _sibling(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up while it runs
    spec.loader.exec_module(module)
    return module


contracts = _sibling("iris_google_contracts", HERE / "google_contracts.py")
mail_body = _sibling("iris_mail_body", HERE / "mail_body.py")
stdin_pipe = _sibling("iris_stdin_pipe", HERE.parent / "wizard" / "stdin_pipe.py")

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
CALLBACK_PATH = "/oauth2/callback"
CREDENTIAL_MAGIC = b"IGC1"
_ENTROPY = b"iris.google-credential/v1\0"
MAX_CALLBACK_QUERY = 4096
MAX_BAD_CALLBACKS = 5
HTTP_TIMEOUT_S = 20.0


class JobError(Exception):
    def __init__(self, code: str, retry_after: float | None = None) -> None:
        super().__init__(code)
        self.code = code if code in contracts.ERRORS else "provider_unavailable"
        self.retry_after = retry_after


# ── DPAPI ─────────────────────────────────────────────────────────────


def _dpapi(data: bytes, *, protect: bool, entropy: bytes) -> bytes:
    """Same user-bound primitive as the profile's imported OAuth client."""
    if os.name != "nt":
        raise JobError("client_unavailable")
    module = _sibling("iris_windows_dpapi", HERE / "windows_dpapi.py")
    try:
        return module.crypt(data, protect=protect, entropy=entropy)
    except module.ProtectionError:
        raise JobError("reauth_required") from None


def protect(document: dict, key: bytes) -> bytes:
    raw = json.dumps(document, separators=(",", ":")).encode("utf-8")
    return CREDENTIAL_MAGIC + _dpapi(raw, protect=True, entropy=_ENTROPY + key)


def unprotect(blob: bytes, key: bytes) -> dict:
    if not blob.startswith(CREDENTIAL_MAGIC):
        raise JobError("reauth_required")
    try:
        document = json.loads(_dpapi(blob[len(CREDENTIAL_MAGIC):], protect=False, entropy=_ENTROPY + key))
    except ValueError as exc:
        raise JobError("reauth_required") from exc
    if not isinstance(document, dict) or document.get("v") != 1 or not document.get("refresh_token"):
        raise JobError("reauth_required")
    return document


# ── Loopback callback ─────────────────────────────────────────────────

_PAGE = """<!doctype html><html lang="es"><meta charset="utf-8"><title>Iris</title>
<body style="font-family:system-ui,sans-serif;margin:3rem;max-width:32rem"><h1>{title}</h1><p>{text}</p></body></html>"""


class Callback:
    """The one answer the browser brings back. Anything else is refused without effect."""

    def __init__(self, state: str) -> None:
        self.state = state
        self.host = ""
        self.done = threading.Event()
        self.result: tuple[str, str] | None = None
        self.bad = 0
        self._lock = threading.Lock()

    def handle(self, request: BaseHTTPRequestHandler) -> None:
        parts = urlsplit(request.path)
        if parts.path != CALLBACK_PATH:
            return self._page(request, 404, "No encontrado", "Esta dirección no es de Iris.")
        if request.headers.get("Host") != self.host or len(parts.query) > MAX_CALLBACK_QUERY:
            return self._page(request, 400, "Respuesta no válida", "Vuelve a Iris e inténtalo de nuevo.")
        try:
            query = parse_qs(parts.query, strict_parsing=True, max_num_fields=8)
        except ValueError:
            query = {}
        with self._lock:
            if self.done.is_set():
                return self._page(request, 409, "Ya respondida", "Esta autorización ya se completó.")
            state = query.get("state", [])
            if len(state) != 1 or not hmac.compare_digest(state[0].encode("utf-8"), self.state.encode("utf-8")):
                self.bad += 1
                if self.bad >= MAX_BAD_CALLBACKS:
                    self.result = ("error", "state_mismatch")
                    self.done.set()
                return self._page(request, 400, "Respuesta no válida",
                                  "Esta respuesta no corresponde a la autorización que Iris pidió.")
            error, code = query.get("error", []), query.get("code", [])
            if error:
                self.result = ("error", "denied" if error[0] == "access_denied" else "provider_unavailable")
            elif len(code) == 1 and 0 < len(code[0]) <= 2048:
                self.result = ("code", code[0])
            else:
                self.bad += 1
                return self._page(request, 400, "Respuesta no válida", "Vuelve a Iris e inténtalo de nuevo.")
            self.done.set()
        if self.result[0] == "error":
            return self._page(request, 200, "Autorización no completada", "Puedes cerrar esta pestaña y volver a Iris.")
        return self._page(request, 200, "Autorización recibida", "Puedes cerrar esta pestaña y volver a Iris.")

    @staticmethod
    def _page(request: BaseHTTPRequestHandler, status: int, title: str, text: str) -> None:
        body = _PAGE.format(title=title, text=text).encode("utf-8")  # fixed texts: nothing reflected
        request.send_response(status)
        request.send_header("Content-Type", "text/html; charset=utf-8")
        request.send_header("Content-Length", str(len(body)))
        request.send_header("Cache-Control", "no-store")
        request.send_header("Referrer-Policy", "no-referrer")
        request.send_header("X-Content-Type-Options", "nosniff")
        request.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        request.end_headers()
        request.wfile.write(body)


def listen(state: str) -> tuple[HTTPServer, Callback]:
    """127.0.0.1 only, an ephemeral port, before any URL exists."""
    callback = Callback(state)

    class Handler(BaseHTTPRequestHandler):
        server_version = "Iris"
        sys_version = ""

        def do_GET(self) -> None:  # noqa: N802 (http.server's name)
            callback.handle(self)

        def log_message(self, *args) -> None:  # the query holds the code: never logged
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    callback.host = f"127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return server, callback


# ── Jobs ──────────────────────────────────────────────────────────────


def _key(private: dict) -> bytes:
    return base64.b64decode(private["protection"].encode("ascii"), validate=True)


def _iris_scopes(value: object) -> tuple[str, ...]:
    """Exactly a scope set Iris asks for (identity and some of its services), or refused."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise JobError("invalid_request")
    services = contracts.services_in(value)
    if not services or contracts.requested_scopes(services) != tuple(value):
        raise JobError("invalid_request")
    return tuple(value)


def _verify_identity(token: dict, client_id: str, nonce: str) -> dict:
    from google.auth.exceptions import GoogleAuthError, TransportError
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token

    raw = token.get("id_token")
    if not isinstance(raw, str):
        raise JobError("identity_unverified")
    try:
        claims = id_token.verify_oauth2_token(raw, Request(), audience=client_id, clock_skew_in_seconds=60)
    except TransportError as exc:
        raise JobError("offline") from exc
    except (ValueError, GoogleAuthError) as exc:
        raise JobError("identity_unverified") from exc
    subject, issuer = claims.get("sub"), claims.get("iss")
    if (not hmac.compare_digest(str(claims.get("nonce", "")).encode(), nonce.encode())
            or not isinstance(subject, str) or not 0 < len(subject) <= 255
            or issuer not in contracts.GOOGLE_ISSUERS):
        raise JobError("identity_unverified")
    email = claims.get("email") if claims.get("email_verified") is True else ""
    return {"subject": subject, "issuer": issuer, "client_id": client_id,
            "email": email if isinstance(email, str) and len(email) <= contracts.MAX_EMAIL else ""}


def auth_begin(request: dict, emit) -> None:
    params, private = request.get("params", {}), request.get("private", {})
    scopes = _iris_scopes(params.get("scopes"))
    timeout_s = params.get("timeout_s", 600)
    if not isinstance(timeout_s, (int, float)) or not 1 <= timeout_s <= 900 or "client" not in private:
        raise JobError("invalid_request")
    client, key = private["client"], _key(private)
    deadline = time.monotonic() + timeout_s
    # Google may grant fewer scopes than asked (granular consent): Iris compares them itself.
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    from google_auth_oauthlib.flow import Flow
    from oauthlib.oauth2 import OAuth2Error
    from requests.exceptions import RequestException

    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    server, callback = listen(state)
    try:
        flow = Flow.from_client_config(
            {"installed": {"client_id": client["client_id"], "client_secret": client["client_secret"],
                           "auth_uri": AUTH_URI, "token_uri": TOKEN_URI, "redirect_uris": ["http://127.0.0.1"]}},
            scopes=list(scopes), autogenerate_code_verifier=True)
        flow.redirect_uri = f"http://{callback.host}{CALLBACK_PATH}"
        url, _ = flow.authorization_url(state=state, nonce=nonce, access_type="offline", prompt="consent",
                                        include_granted_scopes="false")
        emit({"id": request["id"], "event": "authorization_required", "session_id": request["session_id"],
              "public": {"url": url, "expires_at": time.time() + timeout_s}})
        while not callback.done.wait(0.05):
            if time.monotonic() >= deadline:
                raise JobError("expired")
    finally:
        server.shutdown()  # late or repeated answers find nobody listening
        server.server_close()
    kind, value = callback.result
    if kind == "error":
        raise JobError(value)
    try:
        token = flow.fetch_token(code=value, timeout=max(1.0, min(HTTP_TIMEOUT_S, deadline - time.monotonic())))
    except RequestException as exc:
        raise JobError("offline") from exc
    except (OAuth2Error, ValueError) as exc:
        raise JobError("provider_unavailable") from exc
    identity = _verify_identity(dict(token), client["client_id"], nonce)
    granted = contracts.normalize_scopes(token.get("scope") or [])
    if not token.get("refresh_token"):
        raise JobError("provider_unavailable")  # without offline access there is nothing to keep
    expires_in = token.get("expires_in")
    document = {"v": 1, "client_id": client["client_id"], "refresh_token": token["refresh_token"],
                "token": token.get("access_token"), "scopes": list(granted),
                "expiry": time.time() + float(expires_in) if isinstance(expires_in, (int, float)) else None}
    emit({"id": request["id"], "event": "authorization_candidate", "session_id": request["session_id"],
          "public": {"granted_scopes": list(granted)},
          "private": {"credential": contracts.encode_credential(protect(document, key)), "identity": identity}})


def _expiry_timestamp(expiry: datetime.datetime | None) -> float | None:
    """google-auth's naive expiry is UTC; aware dates retain their actual offset."""
    if expiry is None:
        return None
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=datetime.timezone.utc)
    return expiry.timestamp()


def credential_refresh(request: dict, emit) -> None:
    private = request.get("private", {})
    if "credential" not in private or "client" not in private:
        raise JobError("invalid_request")
    client, key = private["client"], _key(private)
    document = unprotect(contracts.decode_credential(private["credential"]), key)
    if document.get("client_id") != client["client_id"]:
        raise JobError("reauth_required")  # made for another OAuth client
    from google.auth.exceptions import RefreshError, TransportError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    credentials = Credentials(token=None, refresh_token=document["refresh_token"], token_uri=TOKEN_URI,
                              client_id=client["client_id"], client_secret=client["client_secret"],
                              scopes=document.get("scopes") or None)
    try:
        credentials.refresh(Request())
    except TransportError as exc:
        raise JobError("offline") from exc
    except RefreshError as exc:
        raise JobError("reauth_required" if "invalid_grant" in str(exc) else "provider_unavailable") from exc
    renewed = dict(document, token=credentials.token,
                   refresh_token=credentials.refresh_token or document["refresh_token"],
                   expiry=_expiry_timestamp(credentials.expiry))
    emit({"id": request["id"], "event": "refreshed",
          "private": {"credential": contracts.encode_credential(protect(renewed, key))}})


def credential_revoke(request: dict, emit) -> None:
    private = request.get("private", {})
    if "credential" not in private:
        raise JobError("invalid_request")
    document = unprotect(contracts.decode_credential(private["credential"]), _key(private))
    from google.auth.exceptions import TransportError
    from google.auth.transport.requests import Request

    try:
        response = Request()(REVOKE_URI, method="POST", body=urlencode({"token": document["refresh_token"]}),
                             headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=HTTP_TIMEOUT_S)
        outcome = "confirmed" if response.status == 200 else "failed"
    except TransportError:
        outcome = "unknown"  # it may have reached Google: neither done nor failed
    emit({"id": request["id"], "event": "revoked", "public": {"outcome": outcome}})


def probe(request: dict, emit) -> None:
    from importlib import metadata

    libraries = {}
    for name in ("google-auth", "google-auth-oauthlib", "google-api-python-client", "oauthlib", "requests"):
        try:
            libraries[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            libraries[name] = None
    try:
        dpapi = unprotect(protect({"v": 1, "refresh_token": "probe"}, b"p" * 16), b"p" * 16)["refresh_token"] == "probe"
    except JobError:
        dpapi = False
    emit({"id": request["id"], "event": "result",
          "public": {"python": sys.version.split()[0], "libraries": libraries, "dpapi": dpapi}})


# ── Reads (F10.3) ────────────────────────────────────────────────────

PAGE_RECORDS = 50
CALENDAR_PAGE_SIZE = 250
CALENDAR_MAX_PAGES = 80
CALENDAR_LIST_MAX_PAGES = 20
GMAIL_LIST_PAGE = 100
HISTORY_MAX_PAGES = 50
_TEXT = 4000
_EVENT_FIELDS = ("items(id,status,summary,description,location,start,end,recurrence,recurringEventId,"
                 "originalStartTime,updated,etag,hangoutLink,conferenceData/entryPoints,htmlLink,transparency,"
                 "eventType,organizer/email,attendees(self,responseStatus)),nextPageToken,nextSyncToken")


def _text(value, limit: int = _TEXT):
    return value[:limit] if isinstance(value, str) else None


def _https(value):
    return value[:2000] if isinstance(value, str) and value.startswith("https://") else None


def _when(value):
    if not isinstance(value, dict):
        return None
    return {key: value[key][:64] for key in ("date", "dateTime", "timeZone") if isinstance(value.get(key), str)}


def _event(item: dict):
    """A calendar event as Iris keeps it, or None when it is gone (a cancelled single event or series).
    A cancelled instance of a series is kept: it hides that occurrence."""
    if item.get("status") == "cancelled" and not item.get("recurringEventId"):
        return None
    video = None
    for entry in (item.get("conferenceData") or {}).get("entryPoints") or []:
        if isinstance(entry, dict) and entry.get("entryPointType") == "video" and _https(entry.get("uri")):
            video = _https(entry.get("uri"))
            break
    response = next((attendee.get("responseStatus") for attendee in item.get("attendees") or []
                     if isinstance(attendee, dict) and attendee.get("self")), None)
    recurrence = item.get("recurrence")
    return {
        "id": item["id"], "status": _text(item.get("status"), 32), "summary": _text(item.get("summary"), 500),
        "description": _text(item.get("description")), "location": _text(item.get("location"), 500),
        "start": _when(item.get("start")), "end": _when(item.get("end")),
        "recurrence": [rule[:500] for rule in recurrence[:20] if isinstance(rule, str)] if isinstance(recurrence, list) else None,
        "recurringEventId": _text(item.get("recurringEventId"), 512),
        "originalStartTime": _when(item.get("originalStartTime")), "updated": _text(item.get("updated"), 64),
        "etag": _text(item.get("etag"), 128), "hangoutLink": _https(item.get("hangoutLink")), "video": video,
        "htmlLink": _https(item.get("htmlLink")), "transparency": _text(item.get("transparency"), 32),
        "eventType": _text(item.get("eventType"), 64),
        "organizer": _text((item.get("organizer") or {}).get("email"), 320),
        "response": _text(response, 32),
    }


def _calendar(item: dict) -> dict:
    return {"id": item["id"], "summary": _text(item.get("summaryOverride") or item.get("summary"), 500),
            "primary": item.get("primary") is True, "accessRole": _text(item.get("accessRole"), 32),
            "timeZone": _text(item.get("timeZone"), 64), "color": _text(item.get("backgroundColor"), 16),
            "selected": item.get("selected") is True}


def _message(item: dict) -> dict:
    headers = {}
    for header in (item.get("payload") or {}).get("headers") or []:
        name = str(header.get("name", "")).lower()
        if name in ("from", "to", "subject", "date") and isinstance(header.get("value"), str):
            headers[name] = header["value"][:1000]
    labels = item.get("labelIds")
    return {"id": item["id"], "threadId": _text(item.get("threadId"), 128),
            "labelIds": [label[:128] for label in labels[:50] if isinstance(label, str)] if isinstance(labels, list) else [],
            "snippet": _text(item.get("snippet"), 500), "internalDate": _text(item.get("internalDate"), 32),
            "sizeEstimate": item.get("sizeEstimate") if isinstance(item.get("sizeEstimate"), int) else None,
            "headers": headers}


def _retry_after(value) -> float | None:
    """RFC 9110 Retry-After: delay seconds or an HTTP date, never an earlier retry."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        if value.isascii() and value.isdigit():
            seconds = float(value)
        else:
            instant = parsedate_to_datetime(value)
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=datetime.timezone.utc)
            seconds = max(0.0, instant.timestamp() - time.time())
        return seconds if math.isfinite(seconds) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _http_error(exc, *, gone_means_expired: bool) -> JobError:
    """Google's answer as a contract code; never its text."""
    status = int(getattr(exc.resp, "status", 0) or 0)
    reason = ""
    try:
        reason = json.loads(exc.content.decode("utf-8"))["error"]["errors"][0].get("reason", "")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        pass
    retry_after = _retry_after(exc.resp.get("retry-after"))
    if status == 410 or (status == 404 and gone_means_expired):
        return JobError("sync_expired")
    if status == 401:
        return JobError("reauth_required")
    if status == 429 or reason in ("rateLimitExceeded", "userRateLimitExceeded"):
        return JobError("rate_limited", retry_after)
    if status == 403 and reason in ("accessNotConfigured", "SERVICE_DISABLED"):
        return JobError("api_disabled")
    if status == 403 and reason in ("insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "forbidden"):
        return JobError("permission_missing")
    return JobError("provider_unavailable", retry_after if status >= 500 else None)


class Reader:
    """One round with the account's credential: authorized HTTP, calls, pages and renewals."""

    def __init__(self, request: dict, emit) -> None:
        import datetime

        private = request.get("private", {})
        if "credential" not in private or "client" not in private:
            raise JobError("invalid_request")
        self.request, self.emit = request, emit
        self.client, self.key = private["client"], _key(private)
        self.document = unprotect(contracts.decode_credential(private["credential"]), self.key)
        if self.document.get("client_id") != self.client["client_id"]:
            raise JobError("reauth_required")
        import google_auth_httplib2
        import httplib2
        from google.oauth2.credentials import Credentials

        expiry = self.document.get("expiry")
        self.credentials = Credentials(
            token=self.document.get("token"), refresh_token=self.document["refresh_token"], token_uri=TOKEN_URI,
            client_id=self.client["client_id"], client_secret=self.client["client_secret"],
            scopes=self.document.get("scopes") or None,
            expiry=(datetime.datetime.fromtimestamp(expiry, datetime.timezone.utc).replace(tzinfo=None)
                    if isinstance(expiry, (int, float)) else None))
        self.token = self.credentials.token
        self.http = google_auth_httplib2.AuthorizedHttp(self.credentials, http=httplib2.Http(timeout=HTTP_TIMEOUT_S))

    def api(self, name: str, version: str):
        from googleapiclient.discovery import build

        return build(name, version, http=self.http, cache_discovery=False, static_discovery=True)

    def report_renewal(self) -> None:
        """A token renewed during the round goes to Core before anything else is said."""
        if self.credentials.token == self.token:
            return
        self.token = self.credentials.token
        expiry = self.credentials.expiry
        self.document = dict(self.document, token=self.credentials.token,
                             refresh_token=self.credentials.refresh_token or self.document["refresh_token"],
                             expiry=_expiry_timestamp(expiry))
        self.emit({"id": self.request["id"], "event": "refreshed",
                   "private": {"credential": contracts.encode_credential(protect(self.document, self.key))}})

    def failure(self, exc: BaseException, *, gone_means_expired: bool = False) -> JobError:
        """Any failure of a call as a contract code (a renewal that happened is reported first)."""
        import httplib2
        from google.auth.exceptions import RefreshError, TransportError
        from googleapiclient.errors import HttpError

        if isinstance(exc, RefreshError):
            return JobError("reauth_required" if "invalid_grant" in str(exc) else "provider_unavailable")
        self.report_renewal()
        if isinstance(exc, HttpError):
            return _http_error(exc, gone_means_expired=gone_means_expired)
        if isinstance(exc, (TransportError, httplib2.HttpLib2Error, OSError)):
            return JobError("offline")
        return JobError("provider_unavailable")

    def call(self, request, *, gone_means_expired: bool = False):
        try:
            result = request.execute(num_retries=0)
        except Exception as exc:
            raise self.failure(exc, gone_means_expired=gone_means_expired) from exc
        self.report_renewal()
        return result

    def page(self, records: list, deleted: list = ()) -> None:
        records, deleted = list(records), list(deleted)
        while records or deleted:
            chunk, records = records[:PAGE_RECORDS], records[PAGE_RECORDS:]
            gone, deleted = deleted[:PAGE_RECORDS * 4], deleted[PAGE_RECORDS * 4:]
            self.emit({"id": self.request["id"], "event": "page", "public": {"records": chunk, "deleted": gone}})

    def result(self, public: dict) -> None:
        self.emit({"id": self.request["id"], "event": "result", "public": public})


def _param_id(value, limit: int = 256):
    if value is None:
        return None
    if not isinstance(value, str) or not 0 < len(value) <= limit or any(ord(char) < 32 for char in value):
        raise JobError("invalid_request")
    return value


def sync_calendar_list(request: dict, emit) -> None:
    reader = Reader(request, emit)
    calendars = reader.api("calendar", "v3").calendarList()
    token = None
    for _ in range(CALENDAR_LIST_MAX_PAGES):
        result = reader.call(calendars.list(
            maxResults=250, pageToken=token, showHidden=False,
            fields="items(id,summary,summaryOverride,primary,accessRole,timeZone,backgroundColor,selected,deleted),nextPageToken"))
        reader.page([_calendar(item) for item in result.get("items") or []
                     if isinstance(item, dict) and isinstance(item.get("id"), str) and not item.get("deleted")])
        token = result.get("nextPageToken")
        if not token:
            reader.result({"complete": True})
            return
    raise JobError("too_large")


def sync_calendar_events(request: dict, emit) -> None:
    params = request.get("params", {})
    calendar_id = _param_id(params.get("calendar_id"))
    sync_token = _param_id(params.get("sync_token"), 4096)
    if calendar_id is None:
        raise JobError("invalid_request")
    reader = Reader(request, emit)
    events = reader.api("calendar", "v3").events()
    page_token = None
    for _ in range(CALENDAR_MAX_PAGES):
        arguments = {"calendarId": calendar_id, "maxResults": CALENDAR_PAGE_SIZE, "singleEvents": False,
                     "pageToken": page_token, "fields": _EVENT_FIELDS}
        if sync_token:
            arguments["syncToken"] = sync_token  # the same one for every page of the round
        result = reader.call(events.list(**arguments), gone_means_expired=False)
        kept, gone = [], []
        for item in result.get("items") or []:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            projected = _event(item)
            if projected is None:
                gone.append(item["id"])
            else:
                kept.append(projected)
        reader.page(kept, gone)
        page_token = result.get("nextPageToken")
        if not page_token:
            next_sync = result.get("nextSyncToken")
            if not isinstance(next_sync, str):
                raise JobError("provider_unavailable")
            reader.result({"sync_token": next_sync, "full": not sync_token})
            return
    raise JobError("too_large")


def _fetch_messages(reader: Reader, users, ids: list) -> None:
    from googleapiclient.errors import HttpError

    kept, gone = [], []
    for message_id in ids:
        try:
            item = users.messages().get(userId="me", id=message_id, format="metadata",
                                        metadataHeaders=["From", "To", "Subject", "Date"],
                                        fields="id,threadId,labelIds,snippet,internalDate,sizeEstimate,payload/headers"
                                        ).execute(num_retries=0)
        except Exception as exc:
            if isinstance(exc, HttpError) and int(getattr(exc.resp, "status", 0) or 0) == 404:
                gone.append(message_id)  # deleted between the list and this read
                continue
            raise reader.failure(exc) from exc
        reader.report_renewal()
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            kept.append(_message(item))
        if len(kept) >= PAGE_RECORDS:
            reader.page(kept)
            kept = []
    reader.page(kept, gone)


def sync_gmail(request: dict, emit) -> None:
    params = request.get("params", {})
    checkpoint = params.get("checkpoint")
    if checkpoint is not None and (not isinstance(checkpoint, str) or not checkpoint.isdigit() or len(checkpoint) > 32):
        raise JobError("invalid_request")
    window_days, max_messages = params.get("window_days", 30), params.get("max_messages", 200)
    if type(window_days) is not int or not 1 <= window_days <= 365 or type(max_messages) is not int \
            or not 1 <= max_messages <= 500:
        raise JobError("invalid_request")
    reader = Reader(request, emit)
    users = reader.api("gmail", "v1").users()
    coverage = None
    if checkpoint is None:
        # The history point first: whatever changes while the window is read is replayed after it.
        start = str(reader.call(users.getProfile(userId="me", fields="historyId"))["historyId"])
        ids, token, complete = [], None, False
        while len(ids) < max_messages:
            result = reader.call(users.messages().list(
                userId="me", q=f"newer_than:{window_days}d", maxResults=min(GMAIL_LIST_PAGE, max_messages - len(ids)),
                pageToken=token, includeSpamTrash=False, fields="messages(id),nextPageToken"))
            ids.extend(item["id"] for item in result.get("messages") or []
                       if isinstance(item, dict) and isinstance(item.get("id"), str))
            token = result.get("nextPageToken")
            if not token:
                complete = True
                break
        _fetch_messages(reader, users, ids[:max_messages])
        coverage = {"window_days": window_days, "max_messages": max_messages, "complete": complete,
                    "messages": min(len(ids), max_messages)}
    else:
        start = checkpoint
    changed, removed, added, last, token = set(), set(), set(), start, None
    for _ in range(HISTORY_MAX_PAGES):
        result = reader.call(users.history().list(
            userId="me", startHistoryId=start, pageToken=token, maxResults=500,
            historyTypes=["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"]), gone_means_expired=True)
        for entry in result.get("history") or []:
            for kind in ("messagesAdded", "labelsAdded", "labelsRemoved"):
                for item in entry.get(kind) or []:
                    message_id = (item.get("message") or {}).get("id")
                    if isinstance(message_id, str):
                        changed.add(message_id)
                        removed.discard(message_id)
                        if kind == "messagesAdded":
                            added.add(message_id)
            for item in entry.get("messagesDeleted") or []:
                message_id = (item.get("message") or {}).get("id")
                if isinstance(message_id, str):
                    removed.add(message_id)
                    changed.discard(message_id)
                    added.discard(message_id)
        if isinstance(result.get("historyId"), str):
            last = result["historyId"]
        token = result.get("nextPageToken")
        if not token:
            break
    else:
        raise JobError("sync_expired")  # too much changed: rebuilding the window is cheaper
    _fetch_messages(reader, users, sorted(changed))
    reader.page([], sorted(removed))
    reader.result({"checkpoint": str(last), "full": checkpoint is None, "coverage": coverage,
                   "arrivals": sorted(added)})


def read_gmail_body(request: dict, emit) -> None:
    """One explicitly selected message, as inert text; no modification or file attachments."""
    message_id = _param_id(request.get("params", {}).get("message_id"))
    if message_id is None:
        raise JobError("invalid_request")
    reader = Reader(request, emit)
    messages = reader.api("gmail", "v1").users().messages()

    def get(call):
        try:
            return reader.call(call)
        except JobError as exc:
            # A missing message is not an expired mailbox history cursor.
            cause = exc.__cause__
            if int(getattr(getattr(cause, "resp", None), "status", 0) or 0) == 404:
                raise JobError("not_found") from None
            raise

    # full + an explicit field mask guarantees sizeEstimate without requesting
    # payload; Google's minimal format only promises ID/labels.
    metadata = get(messages.get(userId="me", id=message_id, format="full", fields="id,sizeEstimate"))
    size = metadata.get("sizeEstimate")
    if type(size) is not int or size < 0:
        raise JobError("body_unavailable")
    if size > mail_body.MAX_MESSAGE_BYTES:
        raise JobError("body_too_large")
    item = get(messages.get(userId="me", id=message_id, format="full", fields="id,payload"))
    if item.get("id") != message_id:
        raise JobError("body_unavailable")
    if len(json.dumps(item).encode("utf-8")) > mail_body.MAX_MESSAGE_BYTES * 2:
        raise JobError("body_too_large")

    def load_part(identity, size):
        # Gmail may store the *text body* behind attachmentId. Only extract_body's
        # unnamed text/plain or text/html leaves can reach here, never file parts.
        result = get(messages.attachments().get(userId="me", messageId=message_id, id=identity))
        if type(result.get("size")) is not int or result["size"] > mail_body.MAX_TEXT_BYTES:
            raise JobError("body_too_large")
        return result.get("data")

    try:
        body = mail_body.extract_body(item.get("payload"), load_part)
    except mail_body.BodyError as exc:
        raise JobError(exc.code) from None
    reader.result({"message_id": message_id, "body": body})


JOBS = {"probe": probe, "auth.begin": auth_begin, "credential.refresh": credential_refresh,
        "credential.revoke": credential_revoke, "sync.calendar_list": sync_calendar_list,
        "sync.calendar_events": sync_calendar_events, "sync.gmail": sync_gmail, "read.gmail_body": read_gmail_body}


def main() -> int:
    output = sys.stdout.buffer
    sys.stdout = sys.stderr  # whatever a library prints never reaches the protocol

    def emit(message: dict) -> None:
        output.write(contracts.rpc_line(message))
        output.flush()

    # Read the one request without leaving a pipe read pending (a pending ReadFile can freeze DLL
    # loads on Windows), then watch for EOF the same way: the owner gone means stop now.
    line = stdin_pipe.LineReader().readline()
    try:
        request = contracts.parse_rpc(line.encode("utf-8"))
        job = JOBS[request["method"]]
    except (contracts.RpcError, KeyError):
        emit({"id": "invalid", "event": "error", "public": {"code": "invalid_request"}})
        return 2

    def watch_owner() -> None:
        stdin_pipe.wait_for_eof()
        os._exit(3)

    threading.Thread(target=watch_owner, daemon=True).start()
    try:
        job(request, emit)
        return 0
    except JobError as exc:
        public = {"code": exc.code}
        if exc.retry_after is not None:
            public["retry_after"] = exc.retry_after
        emit({"id": request["id"], "event": "error", "public": public})
    except Exception:  # never a provider's text or a traceback on the protocol
        emit({"id": request["id"], "event": "error", "public": {"code": "provider_unavailable"}})
    return 1


if __name__ == "__main__":
    sys.exit(main())
