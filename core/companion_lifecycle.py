"""Lifecycle and diagnostics for the optional IRIS Desktop Companion.

The Companion is deliberately independent from IRIS Core and the Hermes
gateway.  Read-only diagnostics never install, build or download anything;
every side effect requires a loopback client plus an explicit intent header.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import ipaddress
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool

import core.config as config_module
from core.config import IRISConfig, get_iris_home


router = APIRouter(prefix="/api/companion", tags=["Desktop Companion"])

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_RELEASE_MANIFEST = _PROJECT_ROOT / "companion" / "release-manifest.json"
_INSTALL_RECORD_NAME = "companion_install.json"
_INSTALL_JOURNAL_NAME = "companion_installing.json"
_INSTALL_DIR_NAME = "app"
_STARTUP_FILE_NAME = "IRIS-Desktop-Companion.vbs"
_EXECUTABLE_NAMES = ("iris-desktop-companion.exe", "IRIS Desktop Companion.exe")
_LIFECYCLE_LOCK = threading.Lock()


class CompanionAutostartUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


def _is_loopback(value: str) -> bool:
    value = (value or "").strip().strip("[]")
    if value.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _enforce_local_action(request: Request, intent: str) -> None:
    """Reject cross-site, DNS-rebound and non-loopback privileged actions."""
    if request.headers.get("x-iris-intent", "") != intent:
        raise HTTPException(status_code=403, detail="Falta la intención explícita para el Companion.")
    peer = request.client.host if request.client else ""
    if not _is_loopback(peer):
        raise HTTPException(status_code=403, detail="Esta acción solo está disponible en este equipo.")
    # Checking only the socket peer is not enough: a public hostname can be
    # rebound to 127.0.0.1 and appear as a loopback peer. Privileged lifecycle
    # routes accept only an explicitly loopback Host and browser Origin.
    try:
        request_hostname = request.url.hostname or ""
        request_port = request.url.port or (443 if request.url.scheme == "https" else 80)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="El Host local no es válido.") from exc
    if not _is_loopback(request_hostname):
        raise HTTPException(status_code=403, detail="El Host no pertenece al loopback local.")
    fetch_site = request.headers.get("sec-fetch-site", "").casefold()
    if fetch_site in {"cross-site", "same-site"}:
        raise HTTPException(status_code=403, detail="La acción exige el mismo origen exacto de IRIS.")
    origin = request.headers.get("origin", "").strip()
    if origin:
        try:
            parsed_origin = urlparse(origin)
            origin_hostname = parsed_origin.hostname or ""
            origin_port = parsed_origin.port or (443 if parsed_origin.scheme == "https" else 80)
        except (TypeError, ValueError):
            parsed_origin = None
            origin_hostname = ""
            origin_port = -1
        if (
            not parsed_origin
            or parsed_origin.scheme != request.url.scheme
            or origin_hostname.casefold() != request_hostname.casefold()
            or origin_port != request_port
        ):
            raise HTTPException(status_code=403, detail="El origen no coincide con IRIS en este equipo.")


@contextmanager
def companion_operation_lock():
    """Serialize Companion/config mutations across Core and wizard processes."""
    if not _LIFECYCLE_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Ya hay una operación del Companion en curso.")

    handle = None
    platform_lock = ""
    try:
        # Resolve dynamically so tests and embedded runtimes that redirect the
        # canonical config home also redirect this interprocess lock.
        lock_path = config_module.get_iris_home() / "companion_lifecycle.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                platform_lock = "windows"
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                platform_lock = "posix"
        except (ImportError, OSError) as exc:
            raise HTTPException(
                status_code=409,
                detail="Otra instancia de IRIS está modificando el Companion.",
            ) from exc
        yield
    finally:
        if handle and platform_lock:
            try:
                handle.seek(0)
                if platform_lock == "windows":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
        if handle:
            handle.close()
        _LIFECYCLE_LOCK.release()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().lower()


def _version_key(value: str) -> tuple[int, ...]:
    """Compare ordinary release versions without treating 0.10 as 0.1."""
    match = re.match(r"^\s*[vV]?(\d+(?:\.\d+)*)", value or "")
    if not match:
        return ()
    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _is_valid_setup_pe(path: Path) -> bool:
    """NSIS commonly uses an x86 bootstrapper even for an x64 payload."""
    return _pe_architecture(path) in {"x86", "x64"}


def _pe_architecture(path: Path) -> str:
    """Return the Windows PE architecture, rejecting truncated placeholders."""
    try:
        size = path.stat().st_size
        if size < 65_536:
            return ""
        with path.open("rb") as handle:
            header = handle.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                return ""
            pe_offset = int.from_bytes(header[0x3C:0x40], "little")
            if pe_offset < 64 or pe_offset + 6 > size:
                return ""
            handle.seek(pe_offset)
            pe_header = handle.read(6)
    except OSError:
        return ""
    if len(pe_header) != 6 or pe_header[:4] != b"PE\0\0":
        return ""
    return {
        0x014C: "x86",
        0x8664: "x64",
        0xAA64: "arm64",
    }.get(int.from_bytes(pe_header[4:6], "little"), "unknown")


def _authenticode_status(path: Path, declared_signed: bool) -> tuple[bool, str]:
    """Never present a manifest declaration as a verified Windows signature."""
    if not declared_signed:
        return False, "unsigned"
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        return False, "unverified"
    try:
        environment = {**os.environ, "IRIS_COMPANION_TARGET": str(path)}
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-AuthenticodeSignature -LiteralPath $env:IRIS_COMPANION_TARGET).Status.ToString()",
            ],
            capture_output=True,
            text=True,
            timeout=12,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "unverified"
    status = (result.stdout or "").strip().casefold()
    return status == "valid", "valid" if status == "valid" else status or "unverified"


_VERSION_FIELDS = {
    "product": "ProductName", "description": "FileDescription", "company": "CompanyName",
    "file_version": "FileVersion", "product_version": "ProductVersion",
}


def _windows_file_identity(path: Path) -> dict[str, str]:
    """Read the Windows version resources through version.dll, without a PE parser.

    This used to start PowerShell with a 12 s limit. On a busy PC PowerShell
    started too slowly, a correct installation looked damaged and every repair
    asked for another repair (qa9). The Windows API answers in milliseconds.
    """
    if platform.system() != "Windows":
        return {}
    import ctypes
    from ctypes import wintypes

    try:
        version = ctypes.WinDLL("version", use_last_error=True)
        version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        version.GetFileVersionInfoW.restype = wintypes.BOOL
        version.VerQueryValueW.argtypes = [
            ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT),
        ]
        version.VerQueryValueW.restype = wintypes.BOOL
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size or size > 1 << 20:
            return {}
        block = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, block):
            return {}

        def query(name: str) -> tuple[int, int]:
            pointer, length = ctypes.c_void_p(), wintypes.UINT()
            if not version.VerQueryValueW(block, name, ctypes.byref(pointer), ctypes.byref(length)):
                return 0, 0
            return pointer.value or 0, length.value

        address, length = query("\\VarFileInfo\\Translation")
        translations = (
            [(language, codepage) for language, codepage in
             (ctypes.cast(address, ctypes.POINTER(wintypes.WORD * 2 * (length // 4))).contents)]
            if address and length >= 4 else []
        )
        # The Companion declares US English / Unicode; keep the usual fallbacks.
        for language, codepage in [*translations, (0x0409, 0x04B0), (0x0409, 0x04E4)]:
            identity = {}
            for key, field in _VERSION_FIELDS.items():
                address, length = query(f"\\StringFileInfo\\{language:04x}{codepage:04x}\\{field}")
                if address and length:
                    identity[key] = ctypes.wstring_at(address, length).rstrip("\x00").strip()
            if identity:
                return identity
    except (OSError, ValueError, AttributeError):
        return {}
    return {}


def _is_expected_companion_binary(path: Path, expected_version: str = "") -> bool:
    if _pe_architecture(path) != "x64":
        return False
    identity = _windows_file_identity(path)
    if identity.get("product") != "IRIS Desktop Companion":
        return False
    file_version = identity.get("product_version") or identity.get("file_version") or ""
    return not expected_version or _version_key(file_version) == _version_key(expected_version)


def _packaged_base() -> Path | None:
    """The installation folder when running packaged, else None (A08.7).

    The packaged runtime only trusts a Companion package shipped inside its own
    installation: environment overrides and checkout build folders belong to
    development, like any other external discovery (A08.1).
    """
    from core.runtime_descriptor import get_runtime

    runtime = get_runtime()
    return runtime.base if runtime.packaged else None


def _within(path: Path, base: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(base.resolve(strict=False))
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _manifest_candidates() -> list[Path]:
    candidates: list[Path] = []
    base = _packaged_base()
    override = os.environ.get("IRIS_COMPANION_MANIFEST", "").strip() if base is None else ""
    if override:
        candidates.append(Path(override).expanduser())
    if base is not None:
        candidates.append(base / "companion" / "release-manifest.json")
    candidates.extend((
        _RELEASE_MANIFEST,
        Path(sys.prefix) / "share" / "iris-os" / "companion" / "release-manifest.json",
        Path(sys.executable).resolve().parent / "companion" / "release-manifest.json",
    ))
    try:
        distribution = importlib.metadata.distribution("iris-os")
        for entry in distribution.files or ():
            if entry.as_posix().casefold().endswith(
                "share/iris-os/companion/release-manifest.json"
            ):
                candidates.append(Path(distribution.locate_file(entry)))
    except (importlib.metadata.PackageNotFoundError, OSError, TypeError, ValueError):
        pass
    if base is not None:
        candidates = [path for path in candidates if _within(path, base)]
    return list(dict.fromkeys(candidates))


def _release_manifest() -> dict[str, Any]:
    first_existing: dict[str, Any] = {}
    for path in _manifest_candidates():
        if not path.is_file():
            continue
        manifest = _read_json(path)
        manifest["_manifest_path"] = path
        if not first_existing:
            first_existing = manifest
        if not _manifest_error(manifest):
            return manifest
    return first_existing


def _manifest_error(manifest: dict[str, Any]) -> str:
    expected = {
        "schema": 1,
        "product": "IRIS Desktop Companion",
        "platform": "windows",
        "arch": "x64",
    }
    for field, value in expected.items():
        if manifest.get(field) != value:
            return f"El manifiesto del Companion tiene {field} inválido."
    filename = str(manifest.get("filename") or "")
    if Path(filename).name != filename or not filename.casefold().endswith(".exe"):
        return "El nombre del instalador en el manifiesto no es válido."
    if not _version_key(str(manifest.get("version") or "")):
        return "La versión del manifiesto no es válida."
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(manifest.get("sha256") or "")):
        return "El SHA-256 del manifiesto no es válido."
    if type(manifest.get("signed")) is not bool:
        return "El campo signed del manifiesto no es válido."
    if manifest.get("webview2_mode") not in {
        "downloadBootstrapper",
        "embedBootstrapper",
        "offlineInstaller",
        "fixedRuntime",
        "skip",
    }:
        return "El modo de WebView2 del manifiesto no es válido."
    return ""


def _installer_candidates(manifest: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    base = _packaged_base()
    override = os.environ.get("IRIS_COMPANION_INSTALLER", "").strip() if base is None else ""
    if override:
        candidates.append(Path(override).expanduser())
    filename = str(manifest.get("filename") or "").strip()
    if filename:
        manifest_path = manifest.get("_manifest_path")
        if isinstance(manifest_path, Path):
            candidates.append(manifest_path.parent / filename)
        candidates.extend((
            _PROJECT_ROOT / "companion" / "src-tauri" / "target" / "release" / "bundle" / "nsis" / filename,
            _PROJECT_ROOT / "dist" / "companion" / filename,
        ))
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique and (base is None or _within(candidate, base)):
            unique.append(candidate)
    return unique


def _package_diagnostic() -> dict[str, Any]:
    manifest = _release_manifest()
    manifest_error = _manifest_error(manifest) if manifest else "No se encontró el manifiesto del Companion."
    expected_hash = str(manifest.get("sha256") or "").casefold()
    candidates = [path for path in _installer_candidates(manifest) if path.is_file()]
    candidate: Path | None = None
    actual_hash = ""
    candidate_arch = ""
    error = ""
    # An obsolete environment override must not hide a valid packaged
    # fallback. Select the first candidate that satisfies the release
    # contract; retain the first existing file only to explain a failure.
    examined: list[tuple[Path, str, str]] = []
    for path in candidates:
        try:
            path_hash = _sha256(path)
        except OSError:
            path_hash = ""
        path_arch = _pe_architecture(path)
        examined.append((path, path_hash, path_arch))
        if not manifest_error and path_hash == expected_hash and _is_valid_setup_pe(path):
            candidate, actual_hash, candidate_arch = path, path_hash, path_arch
            break
    if candidate is None and examined:
        candidate, actual_hash, candidate_arch = examined[0]
    declared_signed = bool(manifest.get("signed", False))
    signed, signature_status = _authenticode_status(
        candidate,
        declared_signed,
    ) if candidate else (False, "not_available")
    trusted = bool(
        candidate
        and not manifest_error
        and expected_hash
        and actual_hash == expected_hash
        and _is_valid_setup_pe(candidate)
        and (not declared_signed or signed)
    )
    if manifest_error:
        error = manifest_error
    elif not candidate:
        error = "No se encontró el instalador local incluido con IRIS OS."
    elif candidate and actual_hash != expected_hash and not error:
        error = "El SHA-256 no coincide con el manifiesto de IRIS."
    elif candidate and candidate_arch not in {"x86", "x64"} and not error:
        error = "El paquete no es un instalador PE válido."
    elif declared_signed and not signed and not error:
        error = "La firma Authenticode declarada no es válida."
    return {
        "available": candidate is not None,
        "trusted": trusted,
        "version": str(manifest.get("version") or ""),
        "filename": candidate.name if candidate else str(manifest.get("filename") or ""),
        "signed": signed,
        "signature_status": signature_status,
        "webview2_mode": str(manifest.get("webview2_mode") or "unknown"),
        "arch": str(manifest.get("arch") or ""),
        "installer_arch": candidate_arch,
        "error": error,
        "_path": candidate,
        "_hash": actual_hash,
    }


def _controlled_install_dir() -> Path:
    return get_iris_home() / "companion" / _INSTALL_DIR_NAME


def _install_record_path() -> Path:
    return get_iris_home() / _INSTALL_RECORD_NAME


def _install_journal_path() -> Path:
    return get_iris_home() / _INSTALL_JOURNAL_NAME


def _normalise_registry_location(value: object) -> Path | None:
    """Return a safe absolute NSIS InstallLocation.

    Tauri's NSIS template stores ``InstallLocation`` as a quoted registry
    string.  ``pathlib`` treats those quotes as literal filename characters,
    so the former parser discarded otherwise healthy per-user installs.
    """
    raw = str(value or "").strip()
    if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1].strip()
    if (
        not raw
        or '"' in raw
        or any(character in raw for character in ("\0", "\r", "\n"))
        or raw.startswith(("\\\\", "//"))
    ):
        return None
    location = Path(raw)
    return location if location.is_absolute() else None


def _nsis_install_invocation(installer: Path, destination: Path) -> tuple[str, str]:
    """Build a direct CreateProcess invocation with NSIS' exact /D syntax.

    ``subprocess`` quotes list arguments containing spaces. NSIS explicitly
    requires ``/D=`` to be the final, unquoted portion of the command line, so
    pass a trusted command-line string while pinning the executable separately.
    No command shell is involved.
    """
    installer_path = installer.resolve(strict=False)
    destination_path = destination.resolve(strict=False)
    installer_text = str(installer_path)
    destination_text = str(destination_path)
    if (
        not installer_path.is_absolute()
        or not destination_path.is_absolute()
        or destination_text.startswith(("\\\\", "//"))
        or any(
            character in text
            for text in (installer_text, destination_text)
            for character in ('"', "\0", "\r", "\n")
        )
    ):
        raise HTTPException(
            status_code=409,
            detail="La ruta local del Companion no es válida para NSIS.",
        )
    # Iris opens and manages this optional component; NSIS must not create a
    # second app shortcut on the desktop or in Start when installing it silently.
    return f'"{installer_text}" /NS /S /D={destination_text}', installer_text


def _safe_record_executable(record: dict[str, Any]) -> Path | None:
    raw = str(record.get("executable") or "").strip()
    if not raw:
        return None
    try:
        candidate = Path(raw).expanduser().resolve(strict=False)
        root = _controlled_install_dir().resolve(strict=False)
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    if candidate.name.casefold() not in {name.casefold() for name in _EXECUTABLE_NAMES}:
        return None
    return candidate


def _safe_legacy_process_executable(record: dict[str, Any]) -> Path | None:
    """Accept a former profile path only as a process-detection candidate."""
    raw = str(record.get("legacy_executable") or "").strip()
    if not raw:
        return None
    try:
        candidate = Path(raw).expanduser().resolve(strict=False)
        roots = tuple(
            (Path.home() / name / "companion" / _INSTALL_DIR_NAME).resolve(strict=False)
            for name in (".alice", ".IRIS")
        )
        if not any(candidate.is_relative_to(root) for root in roots):
            return None
    except (OSError, ValueError):
        return None
    if candidate.name.casefold() not in {name.casefold() for name in _EXECUTABLE_NAMES}:
        return None
    return candidate


def _managed_executable_valid(executable: Path | None, record: dict[str, Any]) -> bool:
    if (
        record.get("schema") != 1
        or record.get("product") != "IRIS Desktop Companion"
        or record.get("executable_arch") != "x64"
        or not _version_key(str(record.get("version") or ""))
        or not executable
        or _pe_architecture(executable) != "x64"
    ):
        return False
    expected_hash = str(record.get("executable_sha256") or "").casefold()
    expected_size = record.get("executable_size")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        return False
    try:
        if int(expected_size) != executable.stat().st_size:
            return False
        return _sha256(executable) == expected_hash
    except (OSError, TypeError, ValueError):
        return False


def _registry_installation() -> tuple[Path | None, str, str]:
    """Find a conventional NSIS install without relying on a generated GUID."""
    if platform.system() != "Windows":
        return None, "", ""
    try:
        import winreg
    except ImportError:
        return None, "", ""
    roots = (
        (winreg.HKEY_CURRENT_USER, "user"),
        (winreg.HKEY_LOCAL_MACHINE, "machine"),
    )
    branches = (
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
        r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    )
    for root, scope in roots:
        for branch in branches:
            try:
                parent = winreg.OpenKey(root, branch)
            except OSError:
                continue
            with parent:
                index = 0
                while True:
                    try:
                        name = winreg.EnumKey(parent, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        child = winreg.OpenKey(parent, name)
                    except OSError:
                        continue
                    with child:
                        try:
                            display_name = str(winreg.QueryValueEx(child, "DisplayName")[0])
                        except OSError:
                            continue
                        if display_name.casefold() != "iris desktop companion":
                            continue
                        try:
                            raw_location = winreg.QueryValueEx(child, "InstallLocation")[0]
                        except OSError:
                            raw_location = ""
                        location = _normalise_registry_location(raw_location)
                        try:
                            version = str(winreg.QueryValueEx(child, "DisplayVersion")[0])
                        except OSError:
                            version = ""
                        if not _version_key(version):
                            continue
                        for executable_name in _EXECUTABLE_NAMES:
                            executable = location / executable_name if location else None
                            if executable and _is_expected_companion_binary(executable, version):
                                return executable, version, scope
    return None, "", ""


def _registry_residue() -> dict[str, Any]:
    """Return a registered but incomplete install without calling it healthy."""
    if platform.system() != "Windows":
        return {}
    try:
        import winreg
    except ImportError:
        return {}
    roots = (
        (winreg.HKEY_CURRENT_USER, "user"),
        (winreg.HKEY_LOCAL_MACHINE, "machine"),
    )
    branches = (
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
        r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    )
    for root, scope in roots:
        for branch in branches:
            try:
                parent = winreg.OpenKey(root, branch)
            except OSError:
                continue
            with parent:
                index = 0
                while True:
                    try:
                        name = winreg.EnumKey(parent, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        child = winreg.OpenKey(parent, name)
                    except OSError:
                        continue
                    with child:
                        try:
                            display_name = str(winreg.QueryValueEx(child, "DisplayName")[0])
                        except OSError:
                            continue
                        if display_name.casefold() != "iris desktop companion":
                            continue
                        try:
                            raw_location = winreg.QueryValueEx(child, "InstallLocation")[0]
                        except OSError:
                            raw_location = ""
                        location = _normalise_registry_location(raw_location)
                        try:
                            version = str(winreg.QueryValueEx(child, "DisplayVersion")[0])
                        except OSError:
                            version = ""
                        candidate = next(
                            (
                                location / executable_name
                                for executable_name in _EXECUTABLE_NAMES
                                if location and (location / executable_name).is_file()
                            ),
                            location / _EXECUTABLE_NAMES[0] if location else None,
                        )
                        return {
                            "source": "registry",
                            "scope": scope,
                            "version": version if _version_key(version) else "",
                            # DisplayVersion alone is not authoritative when
                            # the registered payload failed product/PE checks.
                            "version_trusted": False,
                            "directory": location,
                            "process_candidate": candidate,
                            "update_supported": bool(
                                scope == "user"
                                and location
                                and location.is_dir()
                                and os.access(location, os.W_OK)
                            ),
                        }
    return {}


def _journal_registry_context(journal: dict[str, Any]) -> dict[str, Any]:
    """Recover a previously verified registry target from an install journal."""
    if (
        journal.get("schema") != 1
        or journal.get("product") != "IRIS Desktop Companion"
        or journal.get("previous_source") != "registry"
    ):
        return {}
    raw_destination = str(journal.get("destination") or "").strip()
    if not raw_destination or raw_destination.startswith(("\\\\", "//")):
        return {}
    destination = Path(raw_destination)
    if not destination.is_absolute():
        return {}
    raw_executable = str(journal.get("previous_executable") or "").strip()
    executable = Path(raw_executable) if raw_executable else destination / _EXECUTABLE_NAMES[0]
    try:
        if executable.parent.resolve(strict=False) != destination.resolve(strict=False):
            return {}
    except OSError:
        return {}
    if executable.name.casefold() not in {name.casefold() for name in _EXECUTABLE_NAMES}:
        return {}
    scope = str(journal.get("previous_registry_scope") or "")
    version = str(journal.get("previous_version") or "")
    return {
        "source": "registry",
        "scope": scope,
        "version": version if _version_key(version) else "",
        "version_trusted": bool(_version_key(version)),
        "directory": destination,
        "process_candidate": executable,
        "update_supported": bool(
            scope == "user"
            and destination.is_dir()
            and os.access(destination, os.W_OK)
        ),
    }


def _completed_registry_installation(journal: dict[str, Any]) -> dict[str, Any]:
    """Recognise an NSIS install that completed after IRIS wrote its journal.

    Recovery is intentionally strict: only the expected product schema and an
    exact version match between journal, registry and verified PE identity can
    retire the interrupted-operation marker. Corrupt managed records remain
    authoritative and never use this path.
    """
    if journal.get("schema") != 1 or journal.get("product") != "IRIS Desktop Companion":
        return {}
    journal_version = str(journal.get("version") or "")
    if not _version_key(journal_version):
        return {}
    executable, registry_version, scope = _registry_installation()
    if (
        not executable
        or _version_key(registry_version) != _version_key(journal_version)
        or _pe_architecture(executable) != "x64"
    ):
        return {}
    directory = executable.parent
    return {
        "executable": executable,
        "source": "registry",
        "scope": scope,
        "version": registry_version,
        "version_trusted": True,
        "directory": directory,
        "process_candidate": executable,
        "update_supported": bool(
            scope == "user" and directory.is_dir() and os.access(directory, os.W_OK)
        ),
    }


def _process_state(executable: Path | None) -> tuple[bool, bool]:
    """Return (running, known); degrade safely when psutil is unavailable."""
    if not executable:
        return False, True
    try:
        import psutil
    except ImportError:
        return False, False
    expected = os.path.normcase(str(executable.resolve(strict=False)))
    try:
        for process in psutil.process_iter(["exe"]):
            value = process.info.get("exe")
            if value and os.path.normcase(str(Path(value).resolve(strict=False))) == expected:
                return True, True
    except (OSError, psutil.Error):
        return False, False
    return False, True


def _matching_companion_processes(candidates: list[Path]) -> list[Any]:
    """Return only processes whose executable exactly matches a verified candidate."""
    try:
        import psutil
    except ImportError as exc:
        raise HTTPException(
            status_code=409,
            detail="No pude cerrar el Companion porque falta la dependencia local psutil.",
        ) from exc
    expected = {
        os.path.normcase(str(candidate.resolve(strict=False)))
        for candidate in candidates
    }
    matches: list[Any] = []
    try:
        for process in psutil.process_iter(["exe"]):
            value = process.info.get("exe")
            if value and os.path.normcase(str(Path(value).resolve(strict=False))) in expected:
                matches.append(process)
    except (OSError, psutil.Error) as exc:
        raise HTTPException(
            status_code=409,
            detail="Windows no permitió comprobar el proceso exacto del Companion.",
        ) from exc
    return matches


def _wait_for_companion_processes(processes: list[Any], timeout: float) -> tuple[list[Any], list[Any]]:
    import psutil

    return psutil.wait_procs(processes, timeout=timeout)


def _installation_diagnostic() -> dict[str, Any]:
    record_path = _install_record_path()
    record = _read_json(record_path)
    journal = _read_json(_install_journal_path())
    executable = _safe_record_executable(record)
    legacy_process_candidate = _safe_legacy_process_executable(record)
    process_candidate = executable
    managed_valid = _managed_executable_valid(executable, record)
    journal_exists = _install_journal_path().is_file()
    controlled_residue = False
    try:
        controlled_residue = any(_controlled_install_dir().iterdir())
    except (FileNotFoundError, NotADirectoryError, OSError):
        pass
    recovered_registry: dict[str, Any] = {}
    if journal_exists and not record_path.exists() and not controlled_residue:
        recovered_registry = _completed_registry_installation(journal)
        if recovered_registry:
            try:
                _install_journal_path().unlink(missing_ok=True)
                journal_exists = False
            except OSError:
                # The verified executable remains usable. Keep exposing that
                # the stale marker could not be retired instead of returning
                # to a permanent false-negative repair loop.
                journal_exists = True
    if process_candidate is None and (journal_exists or controlled_residue):
        process_candidate = next(
            (
                _controlled_install_dir() / name
                for name in _EXECUTABLE_NAMES
                if (_controlled_install_dir() / name).is_file()
            ),
            None,
        )
    source = "managed" if managed_valid else ""
    registry_scope = ""
    registry_update_supported = True
    version = str(record.get("version") or "") if managed_valid else ""
    version_trusted = managed_valid
    repair_directory: Path | None = _controlled_install_dir() if managed_valid else None
    damaged = bool(
        not recovered_registry
        and (
            journal_exists
            or (record_path.exists() and not managed_valid)
            or controlled_residue and not managed_valid
        )
    )
    # An invalid managed record or an interrupted managed install is
    # authoritative evidence of damage. Never let the NSIS registry entry for
    # that same directory bypass the stored hash and reclassify it as healthy.
    if recovered_registry:
        executable = recovered_registry["executable"]
        source = "registry"
        registry_scope = str(recovered_registry.get("scope") or "")
        registry_update_supported = bool(recovered_registry.get("update_supported"))
        version = str(recovered_registry.get("version") or "")
        version_trusted = True
        repair_directory = recovered_registry.get("directory")
        process_candidate = recovered_registry.get("process_candidate")
        damaged = False
    elif managed_valid and not journal_exists:
        pass
    elif not damaged:
        executable, registry_version, registry_scope = _registry_installation()
        if executable:
            source = "registry"
            version = registry_version
            version_trusted = True
            repair_directory = executable.parent
            damaged = False
            registry_update_supported = bool(
                registry_scope == "user" and os.access(executable.parent, os.W_OK)
            )
        else:
            residue = _registry_residue()
            if residue:
                source = "registry"
                registry_scope = str(residue.get("scope") or "")
                registry_update_supported = bool(residue.get("update_supported"))
                version = str(residue.get("version") or "")
                version_trusted = bool(residue.get("version_trusted"))
                repair_directory = residue.get("directory")
                process_candidate = residue.get("process_candidate")
                damaged = True
    else:
        executable = None
        source = "managed" if record_path.exists() or controlled_residue else ""
        version = ""
        version_trusted = False
        repair_directory = _controlled_install_dir() if source == "managed" else None
        registry_context = _journal_registry_context(journal) if journal_exists else {}
        if registry_context:
            source = "registry"
            registry_scope = str(registry_context.get("scope") or "")
            registry_update_supported = bool(registry_context.get("update_supported"))
            version = str(registry_context.get("version") or "")
            version_trusted = bool(registry_context.get("version_trusted"))
            repair_directory = registry_context.get("directory")
            process_candidate = registry_context.get("process_candidate")
    installed = bool(executable and _pe_architecture(executable) == "x64")
    running, running_known = _process_state(
        executable if installed else process_candidate if damaged else None
    )
    if legacy_process_candidate and legacy_process_candidate != process_candidate:
        legacy_running, legacy_running_known = _process_state(legacy_process_candidate)
        running = running or legacy_running
        running_known = running_known and legacy_running_known
    return {
        "installed": installed,
        "state": "installed" if installed else "damaged" if damaged else "missing",
        "version": version,
        "version_trusted": version_trusted,
        "source": source,
        "registry_scope": registry_scope,
        "registry_update_supported": registry_update_supported,
        "running": running,
        "running_known": running_known,
        "process_check_required": bool(
            (process_candidate or legacy_process_candidate) and damaged and not running_known
        ),
        "interrupted": journal_exists,
        "_executable": executable if installed else None,
        "_repair_directory": repair_directory,
        "_process_candidate": process_candidate,
        "_legacy_process_candidate": legacy_process_candidate,
    }


def _webview2_diagnostic() -> dict[str, Any]:
    if platform.system() != "Windows":
        return {"available": False, "version": "", "method": "unsupported"}
    locations = []
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(variable, "")
        if base:
            locations.append(Path(base) / "Microsoft" / "EdgeWebView" / "Application")
    versions: list[str] = []
    for location in locations:
        try:
            versions.extend(
                child.name for child in location.iterdir()
                if child.is_dir() and (child / "msedgewebview2.exe").is_file()
            )
        except OSError:
            continue
    if versions:
        def version_key(value: str) -> tuple[int, ...]:
            try:
                return tuple(int(part) for part in value.split("."))
            except ValueError:
                return (0,)
        return {"available": True, "version": max(versions, key=version_key), "method": "filesystem"}
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                clients = winreg.OpenKey(hive, r"Software\Microsoft\EdgeUpdate\Clients")
            except OSError:
                continue
            with clients:
                index = 0
                while True:
                    try:
                        name = winreg.EnumKey(clients, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        child = winreg.OpenKey(clients, name)
                        display = str(winreg.QueryValueEx(child, "name")[0])
                        version = str(winreg.QueryValueEx(child, "pv")[0])
                        child.Close()
                    except OSError:
                        continue
                    if "webview2" in display.casefold():
                        return {"available": True, "version": version, "method": "registry"}
    except ImportError:
        pass
    return {"available": False, "version": "", "method": "not_found"}


def _core_diagnostic() -> dict[str, Any]:
    document = _read_json(get_iris_home() / "companion_runtime.json")
    try:
        port = int(document.get("api_port") or IRISConfig.load().websocket_port)
    except (TypeError, ValueError):
        port = 8765
    available = False
    version = ""
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/status",
            headers={"Accept": "application/json"},
            method="GET",
        )
        direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with direct_opener.open(request, timeout=0.4) as response:
            payload = json.loads(response.read(32_768).decode("utf-8"))
        available = bool(
            isinstance(payload, dict)
            and payload.get("name") == "IRIS"
            and payload.get("status") == "running"
        )
        if available:
            version = str(payload.get("version") or "")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        pass
    return {
        "available": available,
        "verified": available,
        "version": version,
        "port": port,
        "transport": "loopback_http",
    }


def _startup_path() -> Path | None:
    if platform.system() != "Windows":
        return None
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / _STARTUP_FILE_NAME


def _autostart_script(executable: Path) -> str:
    quoted = str(executable).replace('"', '""')
    return f'Set shell = CreateObject("WScript.Shell")\r\nshell.Run """{quoted}""", 0, False\r\n'


def _read_autostart_script(path: Path) -> str:
    """Read Unicode-safe VBS while accepting the former ASCII-only format."""
    payload = path.read_bytes()
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16")
    if payload.isascii():
        return payload.decode("ascii")
    # WSH interprets a BOM-less script using the active ANSI code page. A
    # former UTF-8 file containing a non-ASCII username is therefore stale.
    return ""


def _autostart_diagnostic(installation: dict[str, Any] | None = None) -> dict[str, Any]:
    path = _startup_path()
    exists = bool(path and path.is_file())
    target_matches = False
    if exists:
        current = installation or _installation_diagnostic()
        executable = current.get("_executable")
        if current.get("installed") and isinstance(executable, Path):
            try:
                script = _read_autostart_script(path)
                target_matches = script.replace("\r\n", "\n") == _autostart_script(executable).replace("\r\n", "\n")
            except (OSError, UnicodeError):
                pass
    return {
        "supported": path is not None,
        "enabled": exists and target_matches,
        "stale": exists and not target_matches,
        "method": "windows_startup" if path else "unsupported",
    }


def companion_lifecycle_diagnostic() -> dict[str, Any]:
    package = _package_diagnostic()
    installation = _installation_diagnostic()
    system = platform.system()
    machine = platform.machine().casefold()
    supported = system == "Windows" and machine in {"amd64", "x86_64"}
    installed_version = _version_key(str(installation["version"]))
    package_version = _version_key(str(package["version"]))
    update_available = bool(
        installation["installed"]
        and package["trusted"]
        and package_version
        and installed_version
        and package_version > installed_version
    )
    version_trusted = installation.get("version_trusted")
    if version_trusted is None:
        # Keep the contract tolerant for older diagnostics/plugins. A current
        # damaged record always emits an explicit False, so this fallback
        # cannot turn an unverified damaged version into a downgrade block.
        version_trusted = bool(installation.get("installed") and installed_version)
    package_older = bool(
        version_trusted
        and package_version
        and installed_version
        and package_version < installed_version
    )
    if not supported:
        action = "unavailable"
    elif (
        installation["running"]
        and package["trusted"]
    ):
        action = "close_required"
    elif (
        installation["installed"] or installation.get("process_check_required")
    ) and not installation["running_known"]:
        action = "diagnostic_required"
    elif (
        installation.get("source") == "registry"
        and not installation.get("registry_update_supported", True)
    ):
        action = (
            "manual_migration_required"
            if update_available or installation["state"] == "damaged"
            else "ready"
        )
    elif installation["state"] == "damaged" and package_older:
        action = "newer_repair_package_required"
    elif not installation["installed"]:
        action = "repair" if installation["state"] == "damaged" and package["trusted"] else "install"
    elif update_available:
        action = "update"
    elif package["trusted"] and not package_older:
        action = "reinstall"
    else:
        action = "ready"
    if action in {"install", "repair", "update", "reinstall"} and not package["trusted"]:
        action = "unavailable" if not installation["installed"] else "ready"
    public_package = {key: value for key, value in package.items() if not key.startswith("_")}
    public_installation = {key: value for key, value in installation.items() if not key.startswith("_")}
    public_installation["update_available"] = update_available
    public_installation["package_older"] = package_older
    return {
        "platform": {"os": system, "arch": platform.machine(), "supported": supported},
        "package": public_package,
        "installation": public_installation,
        "webview2": _webview2_diagnostic(),
        "core": _core_diagnostic(),
        "autostart": _autostart_diagnostic(installation),
        "action": action,
        "package_download": False,
        "automatic_download": False,
        "automatic_build": False,
    }


def _save_companion_flags(*, enabled: bool | None = None, autostart: bool | None = None) -> str:
    """Persist convenience flags without falsifying an already-completed OS action."""
    try:
        config = IRISConfig.load()
        if enabled is not None:
            config.companion_enabled = enabled
        if autostart is not None:
            config.companion_autostart = autostart
        config.save()
    except OSError as exc:
        return f"La acción terminó, pero IRIS no pudo guardar la preferencia: {exc}"
    return ""


def _install_companion() -> list[str]:
    if platform.system() != "Windows" or platform.machine().casefold() not in {"amd64", "x86_64"}:
        raise HTTPException(status_code=409, detail="Este paquete del Companion solo admite Windows x64.")
    package = _package_diagnostic()
    installer = package.get("_path")
    if not package["trusted"] or not isinstance(installer, Path):
        raise HTTPException(status_code=409, detail=package["error"] or "No hay un paquete local validado para instalar.")
    installation = _installation_diagnostic()
    if installation["running"]:
        raise HTTPException(status_code=409, detail="Cierra el Desktop Companion antes de instalar o reparar.")
    if (
        installation["installed"] or installation.get("process_check_required")
    ) and not installation["running_known"]:
        raise HTTPException(
            status_code=409,
            detail="No pude comprobar si el Companion está abierto; cierra la app y reintenta con el diagnóstico disponible.",
        )
    if installation.get("source") == "registry" and not installation.get(
        "registry_update_supported", True
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "La instalación existente es de ámbito máquina o no permite escritura. "
                "Desinstálala con permisos de administrador y luego usa la instalación por usuario."
            ),
        )
    installed_version = _version_key(str(installation.get("version") or ""))
    package_version = _version_key(str(package.get("version") or ""))
    version_trusted = installation.get("version_trusted")
    if version_trusted is None:
        version_trusted = bool(installation.get("installed") and installed_version)
    if (
        version_trusted
        and installed_version
        and package_version
        and package_version < installed_version
    ):
        raise HTTPException(status_code=409, detail="IRIS bloqueó un downgrade del Desktop Companion.")
    repair_directory = installation.get("_repair_directory")
    install_dir = (
        repair_directory
        if installation.get("source") == "registry" and isinstance(repair_directory, Path)
        else _controlled_install_dir()
    )
    install_dir.mkdir(parents=True, exist_ok=True)
    autostart_was_requested = bool(
        (_startup_path() and _startup_path().is_file())
        or getattr(IRISConfig.load(), "companion_autostart", False)
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        with tempfile.TemporaryDirectory(prefix="iris-companion-installer-") as staging_directory:
            staged_installer = Path(staging_directory) / installer.name
            shutil.copy2(installer, staged_installer)
            if (
                _sha256(staged_installer) != package["_hash"]
                or not _is_valid_setup_pe(staged_installer)
            ):
                raise HTTPException(
                    status_code=409,
                    detail="El paquete cambió mientras IRIS lo preparaba; vuelve a diagnosticar.",
                )
            _write_json_atomic(_install_journal_path(), {
                "schema": 1,
                "product": "IRIS Desktop Companion",
                "version": package["version"],
                "destination": str(install_dir.resolve()),
                "previous_source": str(installation.get("source") or ""),
                "previous_version": str(installation.get("version") or ""),
                "previous_registry_scope": str(installation.get("registry_scope") or ""),
                "previous_executable": str(
                    installation.get("_executable")
                    or installation.get("_process_candidate")
                    or ""
                ),
                "started_at": datetime.now(timezone.utc).isoformat(),
            })
            command, application = _nsis_install_invocation(staged_installer, install_dir)
            result = subprocess.run(
                command,
                executable=application,
                check=False,
                timeout=240,
                creationflags=creationflags,
            )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="El instalador no terminó dentro de 4 minutos; la instalación quedó marcada para reparar.") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Windows no pudo abrir el instalador: {exc}") from exc
    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"El instalador terminó con código {result.returncode}; la instalación quedó marcada para reparar.",
        )
    executable = next(
        (
            install_dir / name
            for name in _EXECUTABLE_NAMES
            if _is_expected_companion_binary(install_dir / name, str(package["version"]))
        ),
        None,
    )
    installed_source = "managed"
    if not executable:
        registry_executable, registry_version, _registry_scope = _registry_installation()
        if (
            registry_executable
            and _version_key(registry_version) == _version_key(str(package["version"]))
            and _is_expected_companion_binary(registry_executable, str(package["version"]))
        ):
            executable = registry_executable
            installed_source = "registry"
    if not executable:
        raise HTTPException(status_code=500, detail="El instalador terminó, pero no dejó un ejecutable verificable.")
    executable_hash = _sha256(executable)
    if installation.get("source") == "registry" or installed_source == "registry":
        _install_record_path().unlink(missing_ok=True)
    else:
        _write_json_atomic(_install_record_path(), {
            "schema": 1,
            "product": "IRIS Desktop Companion",
            "version": package["version"],
            "executable": str(executable.resolve()),
            "executable_arch": "x64",
            "executable_size": executable.stat().st_size,
            "executable_sha256": executable_hash,
            "installer_sha256": package["_hash"],
            "installed_at": datetime.now(timezone.utc).isoformat(),
        })
    _install_journal_path().unlink(missing_ok=True)
    warnings: list[str] = []
    flag_warning = _save_companion_flags(enabled=True)
    if flag_warning:
        warnings.append(flag_warning)
    if autostart_was_requested:
        try:
            autostart_warning = _set_autostart(True)
            if autostart_warning:
                warnings.append(autostart_warning)
        except (OSError, HTTPException) as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            warnings.append(
                f"El Companion quedó instalado, pero no se pudo reparar el autoinicio: {detail}"
            )
    return warnings


def _set_autostart(enabled: bool) -> str:
    path = _startup_path()
    if not path:
        raise HTTPException(status_code=409, detail="El autoinicio del Companion solo está disponible en Windows.")
    if enabled:
        if platform.machine().casefold() not in {"amd64", "x86_64"}:
            raise HTTPException(
                status_code=409,
                detail="Este Companion solo admite autoinicio en Windows x64.",
            )
        installation = _installation_diagnostic()
        executable = installation.get("_executable")
        if not installation["installed"] or not isinstance(executable, Path):
            raise HTTPException(status_code=409, detail="Instala el Desktop Companion antes de activar su autoinicio.")
        path.parent.mkdir(parents=True, exist_ok=True)
        script = _autostart_script(executable)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(script, encoding="utf-16", newline="")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    else:
        path.unlink(missing_ok=True)
    return _save_companion_flags(autostart=enabled)


def _launch_companion() -> tuple[Literal["launched", "focus_requested"], str]:
    if platform.system() != "Windows" or platform.machine().casefold() not in {"amd64", "x86_64"}:
        raise HTTPException(status_code=409, detail="El Desktop Companion solo puede abrirse en Windows x64.")
    installation = _installation_diagnostic()
    executable = installation.get("_executable")
    if not installation["installed"] or not isinstance(executable, Path):
        raise HTTPException(status_code=409, detail="El Desktop Companion no está instalado.")
    was_running = bool(installation["running"])
    if not was_running and not installation["running_known"]:
        raise HTTPException(status_code=409, detail="No pude comprobar si el Companion ya está abierto.")
    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
    )
    try:
        subprocess.Popen(
            [str(executable)],
            cwd=str(executable.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Windows no pudo abrir el Companion: {exc}") from exc
    return (
        "focus_requested" if was_running else "launched",
        _save_companion_flags(enabled=True),
    )


def _stop_companion() -> tuple[Literal["stopped", "already_stopped"], str]:
    """Close only the verified Companion process, never a process by name alone."""
    if platform.system() != "Windows" or platform.machine().casefold() not in {"amd64", "x86_64"}:
        raise HTTPException(status_code=409, detail="El Desktop Companion solo puede cerrarse desde Windows x64.")
    return _close_verified_companion(_installation_diagnostic()), _save_companion_flags(enabled=False)


def close_companion_for_update() -> bool:
    """Close the Companion before the installer replaces Iris; preferences are kept.

    On a PC without a graphics card its avatar competes with the installer for
    the processor (qa9). Returns whether it was open.

    The folder passes the uninstaller's own checks first, before it is diagnosed:
    a link on the way from the profile made a copy outside it look managed, and
    that copy would have been started with ``--iris-exit`` (Codex qa10 closure,
    C3). Such a folder is refused visibly, with nothing started, stopped or saved.
    """
    if platform.system() != "Windows" or platform.machine().casefold() not in {"amd64", "x86_64"}:
        return False
    # Imported here: the installer module imports this one inside its functions.
    from core.iris_installer import companion_preflight

    reason = companion_preflight(_controlled_install_dir())
    if reason:
        raise RuntimeError(f"no se tocó porque {reason}.")
    installation = _installation_diagnostic()
    # Like the uninstaller, only the Companion Iris installed and manages: one
    # installed on its own (a developer build, another copy) is left alone.
    if installation.get("source") != "managed":
        return False
    try:
        return _close_verified_companion(installation) == "stopped"
    except HTTPException as exc:
        raise RuntimeError(str(exc.detail)) from exc


def _close_verified_companion(installation: dict[str, Any]) -> Literal["stopped", "already_stopped"]:
    candidates = list(dict.fromkeys(
        candidate
        for candidate in (
            installation.get("_executable"),
            installation.get("_process_candidate"),
            installation.get("_legacy_process_candidate"),
        )
        if isinstance(candidate, Path)
    ))
    if not installation.get("running"):
        if installation.get("running_known") is False:
            raise HTTPException(
                status_code=409,
                detail="No pude comprobar si el Companion sigue abierto. Ejecuta Diagnosticar y reintenta.",
            )
        return "already_stopped"
    if not candidates:
        raise HTTPException(
            status_code=409,
            detail="IRIS detectó el Companion abierto, pero no pudo validar su ejecutable para cerrarlo.",
        )

    processes = _matching_companion_processes(candidates)
    if not processes:
        return "already_stopped"

    # Companion 0.1.1+ understands this single-instance control argument and
    # exits through Tauri so its window position is persisted. Older builds
    # safely fall back to terminating the already path-verified process.
    executable = candidates[0]
    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
    )
    try:
        subprocess.Popen(
            [str(executable), "--iris-exit"],
            cwd=str(executable.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
        _, alive = _wait_for_companion_processes(processes, 2.5)
    except OSError:
        alive = processes

    if alive:
        import psutil

        for process in alive:
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as exc:
                raise HTTPException(
                    status_code=409,
                    detail="Windows no permitió cerrar el proceso verificado del Companion.",
                ) from exc
        _, alive = _wait_for_companion_processes(alive, 2.5)
        for process in alive:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as exc:
                raise HTTPException(
                    status_code=409,
                    detail="Windows no permitió forzar el cierre del Companion.",
                ) from exc
        _, alive = _wait_for_companion_processes(alive, 2.0)
    if alive:
        raise HTTPException(
            status_code=500,
            detail="Windows no pudo cerrar por completo el Desktop Companion.",
        )
    return "stopped"


@router.get("/lifecycle")
def companion_lifecycle() -> dict[str, Any]:
    return companion_lifecycle_diagnostic()


@router.post("/install")
def companion_install(request: Request) -> dict[str, Any]:
    _enforce_local_action(request, "install-companion")
    with companion_operation_lock():
        warnings = _install_companion()
        return {"status": "installed", "warnings": warnings, "lifecycle": companion_lifecycle_diagnostic()}


@router.post("/autostart")
def companion_autostart(payload: CompanionAutostartUpdate, request: Request) -> dict[str, Any]:
    _enforce_local_action(request, "configure-companion-autostart")
    with companion_operation_lock():
        warning = _set_autostart(payload.enabled)
    return {
        "status": "enabled" if payload.enabled else "disabled",
        "warnings": [warning] if warning else [],
        "autostart": _autostart_diagnostic(),
    }


@router.post("/launch")
def companion_launch(request: Request) -> dict[str, Any]:
    _enforce_local_action(request, "launch-companion")
    with companion_operation_lock():
        status, warning = _launch_companion()
    return {
        "status": status,
        "warnings": [warning] if warning else [],
        "lifecycle": companion_lifecycle_diagnostic(),
    }


@router.post("/stop")
def companion_stop(request: Request) -> dict[str, Any]:
    _enforce_local_action(request, "stop-companion")
    with companion_operation_lock():
        status, warning = _stop_companion()
    return {
        "status": status,
        "warnings": [warning] if warning else [],
        "lifecycle": companion_lifecycle_diagnostic(),
    }
