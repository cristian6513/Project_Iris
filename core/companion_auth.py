"""Authentication helpers for the packaged IRIS Desktop Companion.

The browser UI remains same-origin and does not need a bearer secret.  The
packaged Tauri surface is cross-origin (``tauri.localhost``), so it pairs with
IRIS Core through a random token stored in the user's private IRIS directory.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import time
from pathlib import Path

from core.config import get_iris_home


COMPANION_TOKEN_HEADER = "X-IRIS-Companion-Token"
COMPANION_WS_PROTOCOL = "iris-companion-v1"
_COMPANION_WS_TOKEN_PREFIX = "iris-token."
TAURI_COMPANION_ORIGINS = frozenset(
    {
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    }
)


def companion_token_path() -> Path:
    """Return the private pairing-token path used by Core and Tauri."""
    return get_iris_home() / "companion_token"


def publish_companion_runtime(*, api_port: int, iris_os_url: str) -> Path:
    """Atomically publish the active loopback endpoint for the native shell."""
    home = get_iris_home()
    target = home / "companion_runtime.json"
    temporary = home / f".companion_runtime.{os.getpid()}.tmp"
    document = {
        "api_port": int(api_port),
        "iris_os_url": str(iris_os_url),
        "pid": os.getpid(),
    }
    try:
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def clear_companion_runtime() -> None:
    """Remove this process' endpoint document without deleting a newer one."""
    target = get_iris_home() / "companion_runtime.json"
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return
    if document.get("pid") == os.getpid():
        target.unlink(missing_ok=True)


def get_or_create_companion_token(path: Path | None = None) -> str:
    """Read or atomically create the persistent local pairing token."""
    token_path = path or companion_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        existing = token_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    if _looks_like_token(existing):
        _restrict_token_permissions(token_path)
        return existing

    candidate = secrets.token_urlsafe(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(token_path, flags, 0o600)
    except FileExistsError:
        # Core and the Tauri process can start together. The loser of this
        # create-new race waits until the winner has flushed the complete file.
        for _ in range(20):
            try:
                existing = token_path.read_text(encoding="utf-8").strip()
            except (FileNotFoundError, OSError, UnicodeError):
                existing = ""
            if _looks_like_token(existing):
                _restrict_token_permissions(token_path)
                return existing
            time.sleep(0.01)
        raise RuntimeError("IRIS companion token is invalid or incomplete")

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(candidate)
            handle.write("\n")
    except Exception:
        token_path.unlink(missing_ok=True)
        raise
    _restrict_token_permissions(token_path)
    return candidate


def is_tauri_companion_origin(origin: str) -> bool:
    """Recognise only the fixed production origins used by Tauri 2."""
    return origin.strip().casefold() in TAURI_COMPANION_ORIGINS


def is_valid_companion_token(candidate: str, path: Path | None = None) -> bool:
    """Compare a supplied token without leaking matching-prefix timing."""
    if not _looks_like_token(candidate):
        return False
    try:
        expected = get_or_create_companion_token(path)
    except (OSError, RuntimeError, UnicodeError):
        return False
    return hmac.compare_digest(candidate, expected)


def companion_token_from_websocket_protocol(value: str) -> str:
    """Extract the pairing token from an offered WebSocket subprotocol list."""
    for item in value.split(","):
        protocol = item.strip()
        if protocol.startswith(_COMPANION_WS_TOKEN_PREFIX):
            return protocol.removeprefix(_COMPANION_WS_TOKEN_PREFIX)
    return ""


def _looks_like_token(value: str) -> bool:
    # token_urlsafe(32) is 43 characters. A bounded tolerance permits future
    # entropy changes while rejecting empty, truncated or absurd files.
    return 32 <= len(value) <= 128 and all(char.isalnum() or char in "-_" for char in value)


def _restrict_token_permissions(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        # Windows keeps the inherited per-user profile ACL.
        pass
