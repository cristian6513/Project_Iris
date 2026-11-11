"""
IRIS TLS helper — Zero-config HTTPS for the avatar / face UI.

WHY THIS EXISTS
---------------
Browsers expose ``navigator.mediaDevices`` (camera, mic, getUserMedia,
WebRTC capture) ONLY inside a "secure context". HTTPS *and* localhost
qualify; plain HTTP over a LAN IP does not. The result is that opening
IRIS from a phone at ``http://192.168.x.x:PORT`` silently disables the
microphone, the camera, and WebRTC capture — the user only sees
"cannot read properties of undefined (reading 'getUserMedia')" deep
inside the browser media or WebRTC client.

Asking users to install ``mkcert`` themselves and trust a CA was the
original plan, but it's exactly the kind of friction we want to avoid.
This module makes IRIS always start over HTTPS with a cert it generated
itself, so the secure-context gate flips to "on" without any manual
setup. The first time a new device hits IRIS it'll see the standard
browser warning ("not private, proceed?") — one tap and the origin is
treated as a secure context for that session, which is what we need.

STRATEGY
--------
1. Prefer ``mkcert`` (locally-trusted CA, no browser warning) when the
   binary is on PATH and the user has installed the root cert.
2. Fall back to a self-signed cert generated with the ``cryptography``
   library. Works everywhere, requires a one-tap "proceed" on each
   device on the first visit.
3. Cache the cert at ``~/.iris/certs/IRIS.{pem,key}``. Regenerate
   when the SAN list no longer covers the current LAN IP or the cert
   is within 7 days of expiry.
"""

from __future__ import annotations

import datetime as _dt
import ipaddress
import logging
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Iterable

from core.config import get_iris_home

logger = logging.getLogger("IRIS.tls")

# ─── Constants ─────────────────────────────────────────────────────

_CERT_VALIDITY_DAYS = 365 * 2   # 2 years — plenty for a dev cert
_RENEW_BEFORE_DAYS  = 7         # regenerate if it expires within a week
_DEFAULT_HOSTS: tuple[str, ...] = ("localhost", "IRIS.local")
_DEFAULT_IPS:   tuple[str, ...] = ("127.0.0.1", "::1")


# ─── Public API ────────────────────────────────────────────────────

def detect_lan_ip() -> str | None:
    """Return the host's primary LAN IPv4 address, or None if loopback-only.

    Uses the same UDP-socket trick as ``/api/network-info`` — opens a
    socket to 8.8.8.8 (no packets actually flow on UDP without sendto),
    asks the OS which local address would have been used for that route.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip if ip and ip != "127.0.0.1" else None
    except OSError:
        return None


def ensure_tls_cert(extra_hosts: Iterable[str] = ()) -> tuple[Path, Path] | tuple[None, None]:
    """Make sure ``~/.iris/certs/IRIS.pem`` + ``IRIS.key`` exist and
    cover the requested host list.

    Returns ``(cert_path, key_path)`` on success or ``(None, None)`` if
    no method could generate a certificate. The caller should fall back
    to plain HTTP in that case and log a warning explaining the LAN
    impact.
    """
    cert_dir = get_iris_home() / "certs"
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / "IRIS.pem"
    key_path  = cert_dir / "IRIS.key"

    # Build the canonical host/IP lists.
    hosts: list[str] = list(_DEFAULT_HOSTS)
    ips:   list[str] = list(_DEFAULT_IPS)
    for item in extra_hosts:
        if not item:
            continue
        try:
            ipaddress.ip_address(item)
            if item not in ips:
                ips.append(item)
        except ValueError:
            if item not in hosts:
                hosts.append(item)

    # Reuse the existing cert if it's still valid AND covers everything
    # the caller wants. Otherwise regenerate.
    if cert_path.exists() and key_path.exists() and _cert_covers(cert_path, hosts, ips):
        return cert_path, key_path

    # Try mkcert first (browser-trusted), fall back to self-signed.
    if _try_mkcert(cert_path, key_path, hosts, ips):
        logger.info("TLS cert generated with mkcert at %s", cert_path)
        return cert_path, key_path

    try:
        _generate_self_signed(cert_path, key_path, hosts, ips)
        logger.info("TLS cert (self-signed) generated at %s", cert_path)
        return cert_path, key_path
    except Exception as exc:
        logger.warning("TLS cert generation failed (%s) — IRIS will fall back to HTTP. "
                       "Phones on the LAN won't be able to use mic/camera.", exc)
        return None, None


# ─── mkcert path ───────────────────────────────────────────────────

def _try_mkcert(cert: Path, key: Path, hosts: list[str], ips: list[str]) -> bool:
    """Run ``mkcert`` if it's on PATH. Returns True on success."""
    exe = shutil.which("mkcert")
    if not exe:
        return False
    args = [exe, "-cert-file", str(cert), "-key-file", str(key), *hosts, *ips]
    try:
        # ``mkcert -install`` is required once per machine to seed the
        # local CA into the system trust store. It's a no-op if already
        # installed, so calling it every boot is safe — and means the
        # very first run does the right thing automatically.
        subprocess.run([exe, "-install"], check=False, capture_output=True, timeout=30)
        result = subprocess.run(args, check=False, capture_output=True, timeout=30)
        if result.returncode == 0 and cert.exists() and key.exists():
            return True
        logger.debug("mkcert exited %s: %s", result.returncode, result.stderr.decode("utf-8", "replace"))
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("mkcert invocation failed: %s", exc)
    return False


# ─── Self-signed fallback (cryptography) ───────────────────────────

def _generate_self_signed(cert: Path, key: Path, hosts: list[str], ips: list[str]) -> None:
    """Generate a self-signed RSA cert covering the given SANs.

    Imported lazily so ``cryptography`` only loads when the mkcert path
    misses — the wheel is small but parsing it adds startup latency we
    don't need on the happy path.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "IRIS Local"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Project IRIS"),
    ])

    san_entries: list[x509.GeneralName] = [x509.DNSName(h) for h in hosts]
    for raw in ips:
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(raw)))
        except ValueError:
            pass

    now = _dt.datetime.now(_dt.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=1))
        .not_valid_after(now + _dt.timedelta(days=_CERT_VALIDITY_DAYS))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    certificate = builder.sign(private_key=private_key, algorithm=hashes.SHA256())

    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    # Restrict permissions where possible (POSIX). On Windows this is a
    # no-op; the file already inherits the user's profile ACL.
    try:
        key.chmod(0o600)
    except OSError:
        pass


# ─── Cert introspection ────────────────────────────────────────────

def _cert_covers(cert_path: Path, hosts: list[str], ips: list[str]) -> bool:
    """Return True iff the existing PEM at *cert_path* covers every
    DNS name in *hosts* and every IP in *ips*, AND is not expiring
    within ``_RENEW_BEFORE_DAYS`` days. On any parse error we say
    "doesn't cover" so the caller regenerates."""
    try:
        from cryptography import x509
    except Exception:
        return False

    try:
        cert_bytes = cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_bytes)
    except Exception:
        return False

    # Expiry check.
    not_after = cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after
    if not_after.tzinfo is None:
        not_after = not_after.replace(tzinfo=_dt.timezone.utc)
    if not_after - _dt.datetime.now(_dt.timezone.utc) < _dt.timedelta(days=_RENEW_BEFORE_DAYS):
        return False

    # SAN coverage check.
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False

    # `get_values_for_type` returns the unwrapped values directly —
    # str for DNSName, ipaddress.IPv4Address / IPv6Address for IPAddress.
    cert_hosts = {str(n).lower() for n in san.get_values_for_type(x509.DNSName)}
    cert_ips   = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}

    if not all(h.lower() in cert_hosts for h in hosts):
        return False
    if not all(ip in cert_ips for ip in ips):
        return False
    return True

