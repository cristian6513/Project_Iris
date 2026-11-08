"""F10: contracts of Iris' Google connections, shared by Core (3.11) and its worker.

Standard library only: the worker runs in Hermes' private Python (3.14, isolated) and loads
this file by its path, never through Core's packages. Nothing here talks to Google.

* **Services and scopes.** Each service Iris may connect is read-only and names the full scope
  URIs it needs; identity is ``openid`` + ``email``. A grant that brings any scope Iris did not
  ask for is refused (``unexpected_grant``): a broader grant is never trimmed into «read only».
* **Identity.** An account is one ``(client_id, issuer, sub)``; apps see an opaque ``gacc_…`` id.
  The two documented Google issuer spellings share one identity; the e-mail is only a label.
* **Public and private.** ``public_account`` and ``public_session`` are allow-lists: credentials,
  the Google subject, the OAuth client and tokens never appear in what the UI, the WebSocket,
  logs or the model can read.
* **Private RPC.** Core and the worker exchange JSON lines (``iris.google-rpc/v1``) with a closed
  set of methods and events. A line's ``private`` part (a protected credential) never leaves
  Core; ``public_part`` drops it.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

PUBLIC_SCHEMA = "iris.google-connections/v1"
RPC_SCHEMA = "iris.google-rpc/v1"

CANONICAL_GOOGLE_ISSUER = "https://accounts.google.com"
GOOGLE_ISSUERS = frozenset({CANONICAL_GOOGLE_ISSUER, "accounts.google.com"})
AUTHORIZATION_HOST = "accounts.google.com"

_AUTH = "https://www.googleapis.com/auth/"
EMAIL_SCOPE = _AUTH + "userinfo.email"
IDENTITY_SCOPES: tuple[str, ...] = ("openid", EMAIL_SCOPE)
SERVICE_SCOPES: Mapping[str, tuple[str, ...]] = {
    "calendar.read": (_AUTH + "calendar.calendarlist.readonly", _AUTH + "calendar.events.readonly"),
    "gmail.read": (_AUTH + "gmail.readonly",),
}
SERVICES: tuple[str, ...] = tuple(SERVICE_SCOPES)
# Google documents these short names as the same scopes as their URIs.
_SCOPE_ALIASES = {"email": EMAIL_SCOPE, "profile": _AUTH + "userinfo.profile"}

SESSION_ACTIVE = ("starting", "awaiting_user", "validating")
SESSION_TERMINAL = ("committed", "partial", "cancelled", "expired", "denied", "error")
ACCOUNT_STATES = ("connected", "partial", "reauth_required", "disconnected")
REMOTE_REVOCATION = ("not_requested", "pending", "confirmed", "unknown", "failed")

# Stable error codes and what the person reads. Never a provider's own text.
ERRORS: Mapping[str, str] = {
    "body_unavailable": "No se pudo leer el texto del correo. Puedes reintentarlo.",
    "body_too_large": "Este correo supera el límite de lectura segura de Iris. El extracto sigue disponible.",
    "body_unsupported": "Este correo no contiene un cuerpo de texto compatible (puede estar cifrado o contener solo archivos).",
    "client_unavailable": "La conexión con Google no está disponible en esta instalación.",
    "busy": "Ya hay una autorización de Google en curso.",
    "service_busy": "Otra instancia de Iris administra las conexiones de Google de este perfil.",
    "permission_missing": "Google no concedió ninguno de los permisos pedidos.",
    "unexpected_grant": "Google concedió permisos que Iris no pidió; la conexión no se guardó.",
    "identity_unverified": "No se pudo verificar la cuenta de Google.",
    "account_exists": "Esa cuenta de Google ya está conectada.",
    "identity_conflict": "Hay registros duplicados de esta cuenta; desconecta y elimina el duplicado antes de reconectar.",
    "revocation_pending": "Espera a que termine la revocación de Google antes de volver a conectar la cuenta.",
    "account_changed": "Google devolvió otra cuenta distinta de la que se estaba reconectando.",
    "stale": "La cuenta cambió mientras se completaba la operación.",
    "reauth_required": "Google ya no acepta esta conexión; hay que volver a conectarla.",
    "not_connected": "Esa cuenta no está conectada.",
    "still_connected": "Primero hay que desconectar la cuenta.",
    "not_found": "No existe esa cuenta o autorización.",
    "revision_conflict": "Otra ventana cambió esta cuenta; vuelve a cargarla.",
    "service_not_granted": "Ese servicio no está concedido para esta cuenta.",
    "state_mismatch": "La respuesta de Google no corresponde a esta autorización.",
    "denied": "La autorización se rechazó en Google.",
    "expired": "La autorización venció antes de completarse.",
    "cancelled": "La autorización se canceló.",
    "offline": "No hay conexión con Google.",
    "rate_limited": "Google limitó temporalmente las peticiones.",
    "provider_unavailable": "Google no respondió como se esperaba.",
    "sync_expired": "Google pidió volver a leer esta colección desde el principio.",
    "too_large": "La colección es más grande de lo que Iris lee de una vez.",
    "api_disabled": "La API de Google necesaria no está activada para este cliente.",
    "invalid_request": "La petición no es válida.",
    "client_invalid": "Importa el JSON de un cliente OAuth de tipo Aplicación de escritorio de Google Cloud.",
    "client_in_use": "Desconecta las cuentas y termina las autorizaciones pendientes antes de cambiar el cliente de Google.",
}

MAX_LABEL = 120
MAX_EMAIL = 254
MAX_SCOPES = 32
MAX_SCOPE_LENGTH = 256
MAX_CREDENTIAL_BYTES = 64 * 1024
MAX_RPC_LINE_BYTES = 2 * 1024 * 1024
MAX_AUTHORIZATION_URL = 4096

_ACCOUNT_ID = re.compile(r"^gacc_[0-9a-f]{32}$")
_SESSION_ID = re.compile(r"^gses_[0-9a-f]{32}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_RPC_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ContractError(ValueError):
    """Input that does not satisfy a contract; ``code`` is one of ``ERRORS``."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or ERRORS.get(code, code))
        self.code = code


# ── Ids and time ──────────────────────────────────────────────────────


def normalize_issuer(value: object) -> str:
    """Google's two documented issuer spellings are one identity, never arbitrary URLs."""
    if not isinstance(value, str) or value not in GOOGLE_ISSUERS:
        raise ContractError("identity_unverified")
    return CANONICAL_GOOGLE_ISSUER


def new_account_id() -> str:
    return "gacc_" + secrets.token_hex(16)


def new_session_id() -> str:
    return "gses_" + secrets.token_hex(16)


def is_account_id(value: object) -> bool:
    return isinstance(value, str) and bool(_ACCOUNT_ID.fullmatch(value))


def is_session_id(value: object) -> bool:
    return isinstance(value, str) and bool(_SESSION_ID.fullmatch(value))


def is_request_id(value: object) -> bool:
    return isinstance(value, str) and bool(_REQUEST_ID.fullmatch(value))


def iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Services, scopes and grants ───────────────────────────────────────


def validate_services(value: object) -> tuple[str, ...]:
    """A non-empty list of known services, without repeats, in canonical order."""
    if not isinstance(value, (list, tuple)) or not value:
        raise ContractError("invalid_request", "Elige al menos un servicio de Google.")
    if any(not isinstance(item, str) or item not in SERVICE_SCOPES for item in value):
        raise ContractError("invalid_request", "Servicio de Google no admitido.")
    if len(set(value)) != len(value):
        raise ContractError("invalid_request", "Un servicio aparece dos veces.")
    return tuple(service for service in SERVICES if service in value)


def requested_scopes(services: Iterable[str]) -> tuple[str, ...]:
    """Exactly what Iris asks Google for: identity and the chosen services' scopes."""
    chosen = set(services)
    scopes = list(IDENTITY_SCOPES)
    for service in SERVICES:
        if service in chosen:
            scopes.extend(SERVICE_SCOPES[service])
    return tuple(scopes)


def normalize_scopes(value: str | Iterable[str]) -> tuple[str, ...]:
    """Scopes as Google may return them (space-separated or a list, short aliases), as a sorted set."""
    items = value.split() if isinstance(value, str) else list(value)
    if len(items) > MAX_SCOPES:
        raise ContractError("unexpected_grant", "Google devolvió demasiados permisos.")
    normalized = set()
    for item in items:
        if not isinstance(item, str) or not item.strip() or len(item) > MAX_SCOPE_LENGTH:
            raise ContractError("invalid_request", "Permiso de Google no válido.")
        item = item.strip()
        normalized.add(_SCOPE_ALIASES.get(item, item))
    return tuple(sorted(normalized))


def services_in(scopes: Iterable[str]) -> tuple[str, ...]:
    """The services whose every scope is among ``scopes``."""
    present = set(normalize_scopes(list(scopes)))
    return tuple(service for service in SERVICES if set(SERVICE_SCOPES[service]) <= present)


@dataclass(frozen=True)
class Grant:
    requested: tuple[str, ...]  # services asked for
    services: tuple[str, ...]  # requested services Google granted fully
    missing: tuple[str, ...]  # requested services not (fully) granted
    unexpected: tuple[str, ...]  # scopes granted that Iris did not ask for
    identity: bool  # ``openid`` granted: the account can be verified

    @property
    def error(self) -> str | None:
        if self.unexpected:
            return "unexpected_grant"
        if not self.identity:
            return "identity_unverified"
        if not self.services:
            return "permission_missing"
        return None

    @property
    def partial(self) -> bool:
        return bool(self.missing)


def evaluate_grant(services: Iterable[str], granted: str | Iterable[str]) -> Grant:
    """Compare what Google granted with what Iris asked for, after normalizing aliases."""
    requested = validate_services(list(services))
    asked = set(normalize_scopes(list(requested_scopes(requested))))
    got = set(normalize_scopes(granted))
    full = tuple(service for service in requested if set(SERVICE_SCOPES[service]) <= got)
    return Grant(
        requested=requested,
        services=full,
        missing=tuple(service for service in requested if service not in full),
        unexpected=tuple(sorted(got - asked)),
        identity="openid" in got,
    )


# ── Public projections (allow-lists) ──────────────────────────────────


def public_account(
    *,
    account_id: str,
    label: str,
    email: str,
    state: str,
    error_code: str,
    requested_services: Iterable[str],
    granted_services: Iterable[str],
    enabled_services: Iterable[str],
    generation: int,
    revision: int,
    remote_revocation: str,
    created_at: float,
    updated_at: float,
) -> dict[str, Any]:
    """What the local UI may know about an account. Every field is named here."""
    requested, granted, enabled = set(requested_services), set(granted_services), set(enabled_services)
    services = {}
    for service in SERVICES:
        if service in granted:
            availability = "not_checked"
        elif service in requested:
            availability = "permission_missing"
        else:
            availability = "not_requested"
        services[service] = {
            "granted": service in granted,
            "enabled": service in enabled and service in granted and state in ("connected", "partial"),
            "availability": availability,
        }
    return {
        "id": account_id,
        "label": label[:MAX_LABEL],
        "email": email[:MAX_EMAIL],
        "auth_state": state,
        "error": {"code": error_code, "message": ERRORS.get(error_code, "")} if error_code else None,
        "services": services,
        "generation": int(generation),
        "revision": int(revision),
        "remote_revocation": remote_revocation,
        "connected_at": iso(created_at),
        "updated_at": iso(updated_at),
    }


def valid_authorization_url(url: object) -> bool:
    """A Google consent page over HTTPS, nothing else."""
    if not isinstance(url, str) or len(url) > MAX_AUTHORIZATION_URL:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return (parts.scheme == "https" and parts.hostname == AUTHORIZATION_HOST and parts.port is None
            and not parts.username and not parts.password)


def public_session(
    *,
    session_id: str,
    state: str,
    services: Iterable[str],
    account_id: str | None,
    authorization_url: str | None,
    expires_at: float | None,
    error_code: str,
    result_account_id: str | None,
) -> dict[str, Any]:
    """An authorization attempt as the local UI sees it. The consent URL only while it waits."""
    showing = state == "awaiting_user" and valid_authorization_url(authorization_url)
    return {
        "id": session_id,
        "state": state,
        "services": list(services),
        "account_id": account_id,
        "authorization_url": authorization_url if showing else None,
        "expires_at": iso(expires_at),
        "error": {"code": error_code, "message": ERRORS.get(error_code, "")} if error_code else None,
        "result_account_id": result_account_id,
    }


# ── Private RPC between Core and the worker ───────────────────────────

RPC_METHODS = frozenset({"probe", "auth.begin", "auth.cancel", "credential.refresh", "credential.revoke",
                         "sync.calendar_list", "sync.calendar_events", "sync.gmail", "read.gmail_body"})
RPC_EVENTS = frozenset({"ready", "authorization_required", "authorization_candidate", "refreshed",
                        "revoked", "page", "result", "error"})
_RPC_KEYS = frozenset({"schema", "id", "method", "event", "session_id", "account_id", "generation",
                       "params", "public", "private"})


class RpcError(ValueError):
    """A line that is not a valid message of the private protocol (dropped, never half-read)."""


def encode_credential(blob: bytes) -> str:
    if not isinstance(blob, (bytes, bytearray)) or not 0 < len(blob) <= MAX_CREDENTIAL_BYTES:
        raise RpcError("Credencial protegida de tamaño no válido.")
    return base64.b64encode(bytes(blob)).decode("ascii")


def decode_credential(text: object) -> bytes:
    if not isinstance(text, str) or len(text) > (MAX_CREDENTIAL_BYTES * 4) // 3 + 4:
        raise RpcError("Credencial protegida no válida.")
    try:
        blob = base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise RpcError("Credencial protegida no válida.") from exc
    if not 0 < len(blob) <= MAX_CREDENTIAL_BYTES:
        raise RpcError("Credencial protegida de tamaño no válido.")
    return blob


def rpc_line(message: Mapping[str, Any]) -> bytes:
    """One validated message as a JSON line."""
    document = dict(message)
    document.setdefault("schema", RPC_SCHEMA)
    line = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    parse_rpc(line)  # what Iris writes satisfies the same contract it reads
    return line


def parse_rpc(line: bytes) -> dict[str, Any]:
    """Validate one received line; anything outside the contract raises RpcError."""
    if not isinstance(line, (bytes, bytearray)) or len(line) > MAX_RPC_LINE_BYTES:
        raise RpcError("Mensaje privado demasiado grande.")
    try:
        message = json.loads(bytes(line).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RpcError("Mensaje privado que no es JSON.") from exc
    if not isinstance(message, dict) or message.get("schema") != RPC_SCHEMA:
        raise RpcError("Mensaje privado de otro contrato.")
    if set(message) - _RPC_KEYS:
        raise RpcError("Mensaje privado con campos no admitidos.")
    if not isinstance(message.get("id"), str) or not _RPC_ID.fullmatch(message["id"]):
        raise RpcError("Mensaje privado sin identificador válido.")
    method, event = message.get("method"), message.get("event")
    if (method is None) == (event is None):
        raise RpcError("Un mensaje privado es una petición o un evento, no ambos.")
    if method is not None and method not in RPC_METHODS:
        raise RpcError("Método privado no admitido.")
    if event is not None and event not in RPC_EVENTS:
        raise RpcError("Evento privado no admitido.")
    if "session_id" in message and not is_session_id(message["session_id"]):
        raise RpcError("Sesión privada no válida.")
    if "account_id" in message and not is_account_id(message["account_id"]):
        raise RpcError("Cuenta privada no válida.")
    if "generation" in message and (type(message["generation"]) is not int or message["generation"] < 0):
        raise RpcError("Generación privada no válida.")
    for key in ("params", "public", "private"):
        if key in message and not isinstance(message[key], dict):
            raise RpcError("Parte de un mensaje privado que no es un objeto.")
    if "private" in message:
        _check_private(message["private"])
    return message


def _short_strings(value: object, keys: set[str], *, required: set[str], limit: int = 512) -> None:
    if not isinstance(value, dict) or set(value) - keys or required - set(value):
        raise RpcError("Parte privada con forma no admitida.")
    if any(not isinstance(item, str) or len(item) > limit for item in value.values()):
        raise RpcError("Parte privada con valores no válidos.")


def _check_private(private: dict) -> None:
    """``credential`` (the protected blob), ``protection`` (the profile's key for it), ``client``
    (the OAuth client) and ``identity`` (the verified account). Nothing else, in no other shape."""
    if set(private) - {"credential", "protection", "client", "identity"}:
        raise RpcError("Parte privada con campos no admitidos.")
    if "credential" in private:
        decode_credential(private["credential"])
    if "protection" in private:
        key = private["protection"]
        try:
            raw = base64.b64decode(key.encode("ascii"), validate=True) if isinstance(key, str) else b""
        except (binascii.Error, UnicodeEncodeError):
            raw = b""
        if not 16 <= len(raw) <= 64:
            raise RpcError("Clave de protección no válida.")
    if "client" in private:
        _short_strings(private["client"], {"client_id", "client_secret"}, required={"client_id", "client_secret"})
    if "identity" in private:
        _short_strings(private["identity"], {"subject", "issuer", "client_id", "email"},
                       required={"subject", "issuer", "client_id"})


def public_part(message: Mapping[str, Any]) -> dict[str, Any]:
    """A message without its private part: the only form that may be logged."""
    return {key: value for key, value in message.items() if key != "private"}
