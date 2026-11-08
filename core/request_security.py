"""Browser-origin policy for local IRIS mutation and WebSocket surfaces."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import urlparse


def is_loopback_host(value: str | None) -> bool:
    """Return whether a Host/peer value names the local loopback."""
    clean = (value or "").casefold().strip().strip("[]")
    if clean == "localhost":
        return True
    try:
        return ipaddress.ip_address(clean).is_loopback
    except ValueError:
        return False


def is_trusted_http_host(
    authority: str,
    *,
    server_host: str | None = None,
    bind_host: str | None = None,
    allow_lan: bool = False,
) -> bool:
    """Validate the raw HTTP Host without DNS or forwarded-header trust.

    A wildcard bind is not an authority. LAN literals must match the local
    socket address supplied by ASGI or an explicitly configured bind address.
    Ports may differ from the socket behind the opt-in local container proxy.
    """
    if not authority or any(
        char.isspace() or ord(char) < 33 or ord(char) == 127
        or char in "/\\@?#,%"
        for char in authority
    ):
        return False
    try:
        parsed = urlparse("http://" + authority)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if not hostname or authority.endswith(":") or port == 0:
        return False
    # Brackets are only valid for IPv6; urlparse also understands IPvFuture,
    # which is not an address that IRIS listens on.
    if authority.startswith("["):
        try:
            if ipaddress.ip_address(hostname).version != 6:
                return False
        except ValueError:
            return False
        tail = authority[authority.index("]") + 1:]
        if tail and (not tail.startswith(":") or not tail[1:].isascii()
                     or not tail[1:].isdigit()):
            return False
    elif "[" in authority or "]" in authority:
        return False
    if is_loopback_host(hostname):
        return True
    if not allow_lan:
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    if address.is_unspecified or address.is_multicast:
        return False
    for candidate in (server_host, bind_host):
        try:
            if address == ipaddress.ip_address(candidate or ""):
                return True
        except ValueError:
            continue
    return False


_PUBLIC_FACE_DIRS = frozenset({"js", "css", "assets", "vendor", "public", "companion"})
_PUBLIC_FACE_SUFFIXES = frozenset({
    ".html", ".js", ".mjs", ".css", ".json", ".png", ".jpg", ".jpeg",
    ".webp", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".wasm", ".task",
})


def is_public_bootstrap_path(path: str, face_dir: Path | None) -> bool:
    """Exempt only the UI bootstrap, never avatars or uploaded animations.

    ASGI supplies a decoded path. Reject ambiguous Windows/path spellings
    before classifying it, and resolve links so a public prefix cannot alias
    a personal resource elsewhere in the static mount.
    """
    if path == "/":
        return True
    if not face_dir or not path.startswith("/face"):
        return False
    if any(char in path for char in "\\:%") or any(ord(c) < 32 for c in path):
        return False
    if path in {"/face", "/face/"}:
        relative = "index.html"
    elif path in {"/face/companion", "/face/companion/"}:
        relative = "companion/index.html"
    elif path.startswith("/face/"):
        relative = path[len("/face/"):]
    else:
        return False
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    if relative != "index.html" and (
        parts[0] not in _PUBLIC_FACE_DIRS
        or Path(relative).suffix.lower() not in _PUBLIC_FACE_SUFFIXES
    ):
        return False
    try:
        root = face_dir.resolve()
        candidate = root.joinpath(*parts)
        # StaticFiles confines links to its root, but that alone would allow
        # /face/js/link to expose /face/animations within the same root.
        return candidate.resolve() == candidate
    except (OSError, RuntimeError, ValueError):
        return False


def is_loopback_url(value: str | None) -> bool:
    """Return whether an HTTP(S) endpoint is explicitly local loopback."""
    try:
        parsed = urlparse((value or "").strip())
        hostname = parsed.hostname
        parsed.port  # Reject malformed ports instead of treating them local.
    except (TypeError, ValueError):
        return False
    return parsed.scheme in {"http", "https"} and is_loopback_host(hostname)


def is_safe_service_url(value: str | None) -> bool:
    """Allow local HTTP(S) services and remote HTTPS without URL credentials."""
    try:
        parsed = urlparse((value or "").strip())
        hostname = parsed.hostname
        parsed.port
    except (TypeError, ValueError):
        return False
    if not hostname or parsed.username or parsed.password or parsed.fragment:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    return is_loopback_host(hostname) or parsed.scheme == "https"


def is_trusted_local_origin(
    origin: str,
    request_host: str | None = None,
    *,
    request_scheme: str | None = None,
    request_port: int | None = None,
) -> bool:
    """Accept only same-host or loopback browser origins.

    A LAN client is same-origin with the IRIS host it opened. Trusting every
    private IP would let an unrelated page elsewhere on the Wi-Fi submit
    mutations to a loopback IRIS instance.
    """
    try:
        parsed = urlparse(origin)
        hostname = parsed.hostname.casefold() if parsed.hostname else ""
        parsed_port = parsed.port
    except (TypeError, ValueError):
        return False
    if parsed.scheme not in {"http", "https"} or not hostname:
        return False

    request_hostname = (request_host or "").casefold().strip("[]")
    strict_origin = bool(request_scheme)
    if strict_origin:
        normalized_scheme = "https" if request_scheme in {"https", "wss"} else "http"
        origin_port = parsed_port or (443 if parsed.scheme == "https" else 80)
        expected_port = request_port or (443 if normalized_scheme == "https" else 80)
        if parsed.scheme != normalized_scheme or origin_port != expected_port:
            return False
        # Different loopback aliases are different browser origins too. Read
        # CORS may remain convenient cross-port, but every mutation is exact.
        if request_hostname and hostname != request_hostname:
            return False
    if request_hostname and hostname != request_hostname:
        # Compatibility mode is used only by non-mutating diagnostics/tests.
        if not ({hostname, request_hostname} <= {"localhost", "127.0.0.1", "::1"}):
            return False
    if hostname == "localhost":
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    # A literal LAN address can be same-origin when LAN mode is explicitly
    # enabled by Core. Arbitrary DNS hostnames are never trusted: accepting
    # them would reintroduce DNS-rebinding writes through Host+Origin.
    return address.is_loopback or hostname == request_hostname
