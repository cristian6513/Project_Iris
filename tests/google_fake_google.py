"""Test-only stand-in for Google, for the F10.2 worker. Never shipped, never imported by Core.

Run by Hermes' Python: ``python -B tests/google_fake_google.py <scenario.json> <worker.py>``. It
loads the real worker by path and answers the worker's HTTP calls itself:

* the token endpoint checks the authorization code and PKCE (the verifier against the challenge
  of the consent URL the worker produced) and returns the scenario's grant and an ID token
  **signed with a test RSA key**, so google-auth really verifies signature, issuer, audience and
  expiry, and the worker its nonce;
* Google's certificates endpoint returns that key; refresh and revoke answer as scripted.

Any other network call fails: no request leaves this machine. Each request is logged (without
codes or tokens) to the scenario's ``log`` file for the test to inspect.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import socket
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SCENARIO = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
WORKER = Path(sys.argv[2])
LOG = Path(SCENARIO["log"])


def log(entry: dict) -> None:
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


_real_create_connection = socket.create_connection


def _loopback_only(address, *args, **kwargs):
    if address[0] not in ("127.0.0.1", "localhost"):
        raise OSError("network blocked by the test stand-in")
    return _real_create_connection(address, *args, **kwargs)


socket.create_connection = _loopback_only

import requests  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from google.auth import crypt, jwt  # noqa: E402
from google_auth_oauthlib.flow import Flow  # noqa: E402

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE = _key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode()
_PUBLIC = _key.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo).decode()
_SIGNER = crypt.RSASigner.from_string(_PRIVATE, key_id="iris-test-key")
_SEEN: dict = {}

_real_authorization_url = Flow.authorization_url


def _authorization_url(self, **kwargs):
    url, state = _real_authorization_url(self, **kwargs)
    query = parse_qs(urlsplit(url).query)
    _SEEN.update(challenge=query["code_challenge"][0], method=query["code_challenge_method"][0],
                 nonce=query.get("nonce", [""])[0], redirect=query["redirect_uri"][0],
                 scope=query["scope"][0], access_type=query.get("access_type", [""])[0],
                 prompt=query.get("prompt", [""])[0])
    log({"kind": "consent_url", "host": urlsplit(url).hostname, "method": _SEEN["method"],
         "scope": _SEEN["scope"], "access_type": _SEEN["access_type"], "prompt": _SEEN["prompt"],
         "redirect_host": urlsplit(_SEEN["redirect"]).hostname, "has_nonce": bool(_SEEN["nonce"])})
    return url, state


Flow.authorization_url = _authorization_url


def _response(url: str, status: int, payload) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    response.url = url
    response.encoding = "utf-8"
    response.request = requests.Request("POST", url).prepare()  # requests-oauthlib logs it
    return response


def _id_token() -> str:
    spec = SCENARIO["token"]["id_token"]
    now = int(time.time())
    claims = {
        "iss": spec.get("iss", "https://accounts.google.com"),
        "aud": spec.get("aud", SCENARIO["client_id"]),
        "sub": spec.get("sub", "sub-sintetico"),
        "email": spec.get("email", "prueba@example.com"),
        "email_verified": spec.get("email_verified", True),
        "iat": now - 5,
        "exp": now + spec.get("exp_offset", 3600),
        "nonce": _SEEN.get("nonce") if spec.get("nonce", "echo") == "echo" else spec["nonce"],
    }
    signer = _SIGNER if not spec.get("foreign_key") else crypt.RSASigner.from_string(
        rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
        key_id="iris-test-key")
    return jwt.encode(signer, claims).decode("ascii")


def _token_endpoint(url: str, data: dict) -> requests.Response:
    grant = data.get("grant_type", [""])[0]
    if grant == "authorization_code":
        verifier = data.get("code_verifier", [""])[0]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        pkce_ok = bool(verifier) and challenge == _SEEN.get("challenge")
        code_ok = data.get("code", [""])[0] == SCENARIO["code"]
        log({"kind": "exchange", "pkce_ok": pkce_ok, "code_ok": code_ok,
             "redirect_ok": data.get("redirect_uri", [""])[0] == _SEEN.get("redirect")})
        if not (pkce_ok and code_ok):
            return _response(url, 400, {"error": "invalid_grant"})
        token = SCENARIO["token"]
        payload = {"access_token": "ya29.sintetico-acceso", "expires_in": 3599, "token_type": "Bearer",
                   "scope": token["scope"], "id_token": _id_token()}
        if not token.get("omit_refresh_token"):
            payload["refresh_token"] = token.get("refresh_token", "1//sintetico-refresco")
        return _response(url, 200, payload)
    if grant == "refresh_token":
        refresh = SCENARIO.get("refresh", {})
        log({"kind": "refresh", "refresh_token": data.get("refresh_token", [""])[0]})
        if refresh.get("network"):
            raise requests.exceptions.ConnectionError("simulated outage")
        if refresh.get("error"):
            return _response(url, 400, {"error": refresh["error"], "error_description": "Token has been expired or revoked."})
        payload = {"access_token": "ya29.sintetico-renovado", "expires_in": 3599, "token_type": "Bearer",
                   "scope": data.get("scope", [""])[0]}
        if refresh.get("new_refresh_token"):
            payload["refresh_token"] = refresh["new_refresh_token"]
        return _response(url, 200, payload)
    return _response(url, 400, {"error": "unsupported_grant_type"})


def _fake_request(self, method, url, **kwargs):
    parts = urlsplit(url)
    if parts.hostname == "oauth2.googleapis.com" and parts.path == "/token":
        body = kwargs.get("data") or ""
        if isinstance(body, bytes):
            body = body.decode()
        data = parse_qs(body) if isinstance(body, str) else {key: [value] for key, value in dict(body).items()}
        return _token_endpoint(url, data)
    if parts.hostname == "oauth2.googleapis.com" and parts.path == "/revoke":
        revoke = SCENARIO.get("revoke", {})
        body = kwargs.get("data") or b""
        token = parse_qs(body.decode() if isinstance(body, bytes) else body).get("token", [""])[0]
        log({"kind": "revoke", "token": token})
        if revoke.get("network"):
            raise requests.exceptions.ConnectionError("simulated outage")
        return _response(url, revoke.get("status", 200), {})
    if parts.hostname == "www.googleapis.com" and parts.path.startswith("/oauth2/") and "certs" in parts.path:
        log({"kind": "certs"})
        return _response(url, 200, {"iris-test-key": _PUBLIC})
    log({"kind": "blocked", "host": parts.hostname})
    raise requests.exceptions.ConnectionError("network blocked by the test stand-in")


requests.Session.request = _fake_request

# ── The read APIs (F10.3), over httplib2 as google-api-python-client uses it ──

import re  # noqa: E402
from urllib.parse import unquote  # noqa: E402

import httplib2  # noqa: E402

API = SCENARIO.get("api", {})
_API_STATE = {"unauthorized": bool(API.get("unauthorized_once"))}
_LOGGED = ("pageToken", "syncToken", "startHistoryId", "singleEvents", "format", "fields", "q", "showDeleted", "maxResults")


def _api(status: int, payload, extra: dict | None = None):
    headers = {"status": str(status), "content-type": "application/json", **(extra or {})}
    return httplib2.Response(headers), json.dumps(payload).encode("utf-8")


def _api_error(status: int, reason: str = "", extra: dict | None = None):
    return _api(status, {"error": {"code": status, "message": "sintético", "errors": [{"reason": reason}]}}, extra)


def _paged(pages: list, query: dict, final: dict | None = None):
    index = int(query.get("pageToken", ["page-0"])[0].split("-")[1])
    body = dict(pages[index])
    if index + 1 < len(pages):
        body["nextPageToken"] = f"page-{index + 1}"
    elif final:
        body.update(final)
    return _api(200, body)


def _fake_http(self, uri, method="GET", body=None, headers=None, redirections=5, connection_type=None):
    parts = urlsplit(uri)
    query = parse_qs(parts.query)
    if parts.hostname == "oauth2.googleapis.com" and parts.path == "/token":  # a renewal during a read
        data = parse_qs(body.decode() if isinstance(body, bytes) else (body or ""))
        response = _token_endpoint(uri, data)
        return httplib2.Response({"status": str(response.status_code), "content-type": "application/json"}), response.content
    lowered = {key.lower(): value for key, value in (headers or {}).items()}
    log({"kind": "api", "method": method, "path": parts.path,
         "query": {key: values[0] for key, values in query.items() if key in _LOGGED},
         "token": str(lowered.get("authorization", ""))[-9:]})
    if method != "GET":
        return _api_error(405, "forbidden")  # Iris only reads
    if _API_STATE["unauthorized"]:
        _API_STATE["unauthorized"] = False
        return _api_error(401, "authError")  # the access token is no longer accepted: renew and retry
    fail = API.get("fail")
    if fail and fail["match"] in parts.path:
        return _api_error(fail["status"], fail.get("reason", ""),
                          {"retry-after": fail["retry_after"]} if "retry_after" in fail else None)
    path = parts.path
    if path == "/calendar/v3/users/me/calendarList":
        return _paged(API["calendar_list"], query)
    match = re.fullmatch(r"/calendar/v3/calendars/([^/]+)/events", path)
    if match:
        rounds = API["events"][unquote(match.group(1))]
        current = rounds[query.get("syncToken", [""])[0]]
        if "status" in current:
            return _api_error(current["status"])
        return _paged(current["pages"], query, {"nextSyncToken": current["next"]})
    gmail = API.get("gmail", {})
    if path == "/gmail/v1/users/me/profile":
        return _api(200, {"historyId": gmail["historyId"]})
    if path == "/gmail/v1/users/me/messages":
        return _paged(gmail["list"], query)
    match = re.fullmatch(r"/gmail/v1/users/me/messages/([^/]+)/attachments/([^/]+)", path)
    if match:
        body = gmail.get("bodies", {}).get(unquote(match.group(2)))
        return _api(200, body) if body is not None else _api_error(404)
    match = re.fullmatch(r"/gmail/v1/users/me/messages/([^/]+)", path)
    if match:
        message = gmail["messages"].get(unquote(match.group(1)), {"status": 404})
        return _api_error(404) if "status" in message else _api(200, message)
    if path == "/gmail/v1/users/me/history":
        current = gmail["history"][query["startHistoryId"][0]]
        if "status" in current:
            return _api_error(current["status"])
        return _paged(current["pages"], query, {"historyId": current["historyId"]})
    return _api_error(404)


httplib2.Http.request = _fake_http

spec = importlib.util.spec_from_file_location("iris_google_worker_under_test", WORKER)
worker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = worker
spec.loader.exec_module(worker)
sys.exit(worker.main())
