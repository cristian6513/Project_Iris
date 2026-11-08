"""Authenticity of the Iris update channel (A08.8).

The channel manifest travels inside a signed envelope::

    {"schema": 1, "payload": "<base64 bytes>",
     "signatures": [{"keyid": "<16 hex>", "ed25519": "<base64 signature>"}]}

Signatures cover the exact payload bytes, so no JSON canonicalisation is
needed. Trusted public keys ship inside the installed version (``channel.json``,
covered by its staging manifest); a key never comes from the network. This is
separate from Authenticode signing of Windows executables (A08.9).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from typing import Any

MAX_ENVELOPE_BYTES = 1024 * 1024
_KEYID = re.compile(r"[0-9a-f]{16}\Z")


class SignatureError(ValueError):
    """The envelope is malformed or carries no valid trusted signature."""


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SignatureError("Campo duplicado en el documento firmado.")
        result[key] = value
    return result


def strict_json(raw: bytes, maximum: int = MAX_ENVELOPE_BYTES) -> Any:
    """UTF-8 JSON without BOM, duplicate keys or oversized input."""
    if len(raw) > maximum:
        raise SignatureError("El documento supera el tamaño permitido.")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise SignatureError("El documento no debe empezar con BOM.")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, SignatureError):
            raise
        raise SignatureError("El documento no es JSON válido.") from exc


def _b64(value: Any, *, length: int | None = None) -> bytes:
    if not isinstance(value, str) or not value:
        raise SignatureError("Valor base64 ausente.")
    try:
        data = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise SignatureError("Valor base64 inválido.") from exc
    if length is not None and len(data) != length:
        raise SignatureError("Longitud de clave o firma inválida.")
    return data


def key_id(public_raw: bytes) -> str:
    return hashlib.sha256(public_raw).hexdigest()[:16]


def parse_trusted_keys(entries: Any) -> dict[str, bytes]:
    """``[{"keyid": ..., "ed25519": "<base64 32 bytes>"}]`` → {keyid: raw}."""
    if not isinstance(entries, list) or not entries or len(entries) > 8:
        raise SignatureError("La lista de claves de confianza no es válida.")
    keys: dict[str, bytes] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"keyid", "ed25519"}:
            raise SignatureError("Clave de confianza con campos inesperados.")
        raw = _b64(entry["ed25519"], length=32)
        if entry["keyid"] != key_id(raw) or entry["keyid"] in keys:
            raise SignatureError("El identificador de la clave no coincide con la clave.")
        keys[entry["keyid"]] = raw
    return keys


def verify_envelope(raw: bytes, trusted: dict[str, bytes]) -> bytes:
    """Return the payload bytes once one trusted Ed25519 signature verifies."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    envelope = strict_json(raw)
    if not isinstance(envelope, dict) or set(envelope) != {"schema", "payload", "signatures"}:
        raise SignatureError("El sobre firmado no tiene el formato esperado.")
    if type(envelope["schema"]) is not int or envelope["schema"] != 1:
        raise SignatureError("Versión de sobre firmado desconocida.")
    payload = _b64(envelope["payload"])
    signatures = envelope["signatures"]
    if not isinstance(signatures, list) or not signatures or len(signatures) > 8:
        raise SignatureError("El sobre no contiene firmas válidas.")
    for item in signatures:
        if not isinstance(item, dict) or set(item) != {"keyid", "ed25519"}:
            raise SignatureError("Firma con campos inesperados.")
        if not isinstance(item["keyid"], str) or not _KEYID.match(item["keyid"]):
            raise SignatureError("Identificador de clave inválido.")
        signature = _b64(item["ed25519"], length=64)
        public = trusted.get(item["keyid"])
        if public is None:
            continue
        try:
            Ed25519PublicKey.from_public_bytes(public).verify(signature, payload)
        except InvalidSignature:
            continue
        return payload
    raise SignatureError("Ninguna firma del canal corresponde a una clave de confianza de esta versión.")


# ── Publishing helpers (release scripts and tests; never used by the client) ──

def generate_private_key() -> tuple[bytes, dict[str, str]]:
    """New Ed25519 key: (PKCS#8 PEM, trusted-key entry for channel.json)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return pem, public_entry(private.public_key())


def public_entry(public_key: Any) -> dict[str, str]:
    from cryptography.hazmat.primitives import serialization

    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"keyid": key_id(raw), "ed25519": base64.b64encode(raw).decode("ascii")}


def load_private_key(pem: bytes, password: bytes | None = None) -> Any:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = serialization.load_pem_private_key(pem, password=password)
    if not isinstance(key, Ed25519PrivateKey):
        raise SignatureError("La clave privada no es Ed25519.")
    return key


def sign_payload(payload: bytes, private_keys: list[Any]) -> bytes:
    signatures = [{"keyid": public_entry(key.public_key())["keyid"],
                   "ed25519": base64.b64encode(key.sign(payload)).decode("ascii")} for key in private_keys]
    envelope = {"schema": 1, "payload": base64.b64encode(payload).decode("ascii"), "signatures": signatures}
    return json.dumps(envelope, indent=1).encode("utf-8")
