"""Personal Desktop OAuth configuration. No network, account login or token is imported here."""
from __future__ import annotations

import json

from core.windows_dpapi import crypt

MAX_CLIENT_BYTES = 64 * 1024
MAGIC = b'IGO1'
ENTROPY = b'iris.google-oauth-client/v1\0'


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field.')
        result[key] = value
    return result


def parse_client(text: str):
    from core.google_worker_client import GoogleClientConfig

    if not isinstance(text, str) or not 0 < len(text.encode('utf-8')) <= MAX_CLIENT_BYTES:
        raise ValueError('Invalid client JSON size.')
    document = json.loads(text.lstrip('\ufeff'), object_pairs_hook=unique_object)
    return GoogleClientConfig.from_document(document)


def protect_client(client, protection: bytes) -> bytes:
    raw = json.dumps({'installed': client.private()}, separators=(',', ':')).encode('utf-8')
    return MAGIC + crypt(raw, protect=True, entropy=ENTROPY + protection)


def open_client(blob: bytes, protection: bytes):
    if not blob.startswith(MAGIC) or len(blob) > 2 * MAX_CLIENT_BYTES:
        raise ValueError('Invalid protected OAuth client.')
    return parse_client(crypt(blob[len(MAGIC):], protect=False, entropy=ENTROPY + protection).decode('utf-8'))
