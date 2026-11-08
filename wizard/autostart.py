"""
Hermes Autostart Manager
────────────────────────

Keep the Hermes api-server running across reboots without asking the user
to touch anything scary (services, `sudo`, UAC prompts).

Strategy per OS:
    • Windows — drop a hidden-window `.vbs` file into the user's Startup
      folder. No admin rights needed, nothing lives in the registry,
      removing the file instantly disables the autostart. The user sees
      it under *Settings → Apps → Startup* and can turn it off there too.
      The script just runs `hermes` (or `hermes.exe`) — Hermes' .env has
      ``API_SERVER_ENABLED=true`` pre-seeded by the wizard, so the
      api-server comes up automatically on first boot.
    • Linux — drop a `~/.config/autostart/iris-hermes.desktop` entry
      so XDG-compliant desktop sessions launch Hermes at login. No
      systemd unit needed; works for desktop users which is our target
      audience here.
    • macOS — write a `~/Library/LaunchAgents/com.iris.hermes.plist`
      and `launchctl load` it. User-scoped, no sudo.

Everything here is best-effort and idempotent: re-enabling is a no-op if
it's already enabled, disabling is silent if there was nothing to undo.

Hermes does NOT ship its own ``daemon install`` subcommand, so we
own the OS integration here.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
from pathlib import Path
from typing import Any, Literal
from core import runtime_descriptor as runtime_policy

# Filename we drop in the Startup folder. Keep stable: it is also used
# to detect and remove the entry.
WINDOWS_AUTOSTART_FILENAME = "IRIS-Hermes-Gateway.vbs"
LINUX_AUTOSTART_FILENAME = "iris-hermes.desktop"
MACOS_LAUNCHAGENT_LABEL = "com.iris.hermes"
MACOS_LAUNCHAGENT_FILENAME = f"{MACOS_LAUNCHAGENT_LABEL}.plist"

OS = Literal["windows", "linux", "darwin", "unknown"]


def _os() -> OS:
    sysname = platform.system().lower()
    if sysname == "windows":
        return "windows"
    if sysname == "linux":
        return "linux"
    if sysname == "darwin":
        return "darwin"
    return "unknown"


def _resolve_hermes() -> str:
    """Return the full path to the hermes executable, or ``""``."""
    runtime_policy.require_runtime_capability("external_autostart")
    return (
        shutil.which("hermes")
        or shutil.which("hermes.exe")
        or ""
    )


# ─────────────────────────────────────────────────────────────────────
# Windows — Startup folder shortcut via a hidden .vbs wrapper
# ─────────────────────────────────────────────────────────────────────

def _windows_startup_dir() -> Path:
    """`%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup`.

    Per-user, no admin needed. Files here run at login.
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata)
    else:
        # Very old Windows or stripped env — fall back to the usual path
        base = Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _windows_autostart_file() -> Path:
    return _windows_startup_dir() / WINDOWS_AUTOSTART_FILENAME


def _vbs_string_literal(value: str) -> str:
    """Return a VBScript double-quoted literal for ``value``.

    In VBScript, ``"`` inside a string is escaped by doubling it
    (``""``). Backslashes are NOT escape characters — they're literal,
    which is what we want for Windows paths. Newlines and other
    control characters are not valid inside a single-line VBS literal,
    so we reject them outright rather than try to split-and-concat.
    """
    if "\r" in value or "\n" in value or "\x00" in value:
        raise ValueError("Path contains control characters; refusing to embed in VBS.")
    return '"' + value.replace('"', '""') + '"'


def _enable_windows() -> dict[str, Any]:
    hermes = _resolve_hermes()
    if not hermes:
        return {
            "ok": False,
            "detail": "No se encontró `hermes` en PATH. Instálalo primero.",
        }

    # Resolve to an absolute, canonical path so the VBS embeds a path
    # we've actually verified — not one a malicious PATH entry could
    # have shadowed at lookup time.
    hermes_path = Path(hermes).resolve(strict=False)
    if not hermes_path.exists():
        return {"ok": False, "detail": f"`hermes` no existe en {hermes_path}."}

    # Build the VBS via a proper literal-quoter that handles spaces,
    # quotes inside the path, and rejects control chars. The outer
    # ``"`` chars in the produced literal become the inner ``"`` that
    # WshShell.Run needs around the executable path so the Windows
    # shell parser treats the whole quoted block as a single argv[0].
    inner_quoted = _vbs_string_literal(f'"{hermes_path}"')
    vbs = (
        '\' IRIS - auto-arranca el api-server de Hermes al iniciar sesion.\r\n'
        '\' Generado por el wizard de IRIS. Borralo para desactivar.\r\n'
        'Set WshShell = CreateObject("WScript.Shell")\r\n'
        f'WshShell.Run {inner_quoted}, 0, False\r\n'
    )

    path = _windows_autostart_file()
    # Atomic write to dodge a TOCTOU symlink-swap window between the
    # ``mkdir`` and the ``write_text``. We:
    #   1. Create the parent dir if missing.
    #   2. Write to a sibling temp file with restrictive permissions.
    #   3. ``os.replace`` it onto the target — atomic on Windows + POSIX
    #      and refuses to follow a symlink at the target (the dest gets
    #      replaced, not the link's target).
    import tempfile  # local import — autostart is rarely called
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Reject if the existing file is a symlink to somewhere outside
        # the startup dir — an attacker who can write into ``%APPDATA%``
        # could pre-create a symlink pointing at, say, the user's
        # ``rundll32`` config, so unlink it before our write.
        if path.is_symlink():
            try:
                path.unlink()
            except OSError as e:
                return {"ok": False, "detail": f"Symlink hostil en {path}: {e}"}
        fd, tmp_name = tempfile.mkstemp(
            prefix=".iris-autostart-",
            suffix=".vbs",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(vbs)
            os.replace(tmp_name, path)
        except OSError:
            with __import__("contextlib").suppress(OSError):
                os.unlink(tmp_name)
            raise
    except (OSError, ValueError) as e:
        return {"ok": False, "detail": f"No se pudo escribir {path}: {e}"}

    return {
        "ok": True,
        "path": str(path),
        "detail": (
            f"Arranque automático activado. El archivo está en:\n{path}\n"
            "Puedes desactivarlo desde aquí o desde Configuración → Apps → "
            "Inicio."
        ),
    }


def _disable_windows() -> dict[str, Any]:
    runtime_policy.require_runtime_capability("external_autostart")
    path = _windows_autostart_file()
    if not path.exists():
        return {"ok": True, "detail": "Ya estaba desactivado.", "path": str(path)}
    try:
        path.unlink()
    except OSError as e:
        return {"ok": False, "detail": f"No se pudo borrar {path}: {e}"}
    return {"ok": True, "detail": "Arranque automático desactivado.", "path": str(path)}


def _status_windows() -> dict[str, Any]:
    path = _windows_autostart_file()
    return {
        "enabled": path.exists(),
        "method": "startup-folder",
        "path": str(path),
    }


# ─────────────────────────────────────────────────────────────────────
# Linux — XDG Desktop Entry under ~/.config/autostart/
# ─────────────────────────────────────────────────────────────────────

def _linux_autostart_dir() -> Path:
    """`$XDG_CONFIG_HOME/autostart`, or `~/.config/autostart` by default."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "autostart"


def _linux_autostart_file() -> Path:
    return _linux_autostart_dir() / LINUX_AUTOSTART_FILENAME


def _enable_linux() -> dict[str, Any]:
    hermes = _resolve_hermes()
    if not hermes:
        return {
            "ok": False,
            "detail": "No se encontró `hermes` en PATH. Instálalo primero.",
        }

    desktop = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=IRIS - Hermes Gateway\n"
        "Comment=Auto-arranca el api-server de Hermes al iniciar sesión.\n"
        f"Exec={hermes}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )

    path = _linux_autostart_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(desktop, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "detail": f"No se pudo escribir {path}: {e}"}

    return {
        "ok": True,
        "path": str(path),
        "detail": (
            f"Arranque automático activado: {path}\n"
            "Tu sesión XDG (GNOME, KDE, XFCE) lo lanzará al iniciar."
        ),
    }


def _disable_linux() -> dict[str, Any]:
    runtime_policy.require_runtime_capability("external_autostart")
    path = _linux_autostart_file()
    if not path.exists():
        return {"ok": True, "detail": "Ya estaba desactivado.", "path": str(path)}
    try:
        path.unlink()
    except OSError as e:
        return {"ok": False, "detail": f"No se pudo borrar {path}: {e}"}
    return {"ok": True, "detail": "Arranque automático desactivado.", "path": str(path)}


def _status_linux() -> dict[str, Any]:
    path = _linux_autostart_file()
    return {
        "enabled": path.exists(),
        "method": "xdg-autostart",
        "path": str(path),
    }


# ─────────────────────────────────────────────────────────────────────
# macOS — LaunchAgent under ~/Library/LaunchAgents/
# ─────────────────────────────────────────────────────────────────────

def _macos_launchagent_file() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / MACOS_LAUNCHAGENT_FILENAME


async def _macos_launchctl(action: str, path: Path) -> tuple[int, str]:
    """Run `launchctl <action> <path>` and capture combined output."""
    runtime_policy.require_runtime_capability("external_autostart")
    try:
        proc = await asyncio.create_subprocess_exec(
            "launchctl", action, str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        return proc.returncode or 0, (out or b"").decode("utf-8", errors="replace").strip()
    except (FileNotFoundError, OSError) as e:
        return 127, f"launchctl no disponible: {e}"
    except asyncio.TimeoutError:
        return 124, "launchctl no respondió a tiempo."


async def _enable_macos() -> dict[str, Any]:
    hermes = _resolve_hermes()
    if not hermes:
        return {
            "ok": False,
            "detail": "No se encontró `hermes` en PATH. Instálalo primero.",
        }

    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        f'    <key>Label</key><string>{MACOS_LAUNCHAGENT_LABEL}</string>\n'
        '    <key>ProgramArguments</key>\n'
        '    <array>\n'
        f'        <string>{hermes}</string>\n'
        '    </array>\n'
        '    <key>RunAtLoad</key><true/>\n'
        '    <key>KeepAlive</key><false/>\n'
        '</dict>\n'
        '</plist>\n'
    )

    path = _macos_launchagent_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(plist, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "detail": f"No se pudo escribir {path}: {e}"}

    rc, out = await _macos_launchctl("load", path)
    if rc != 0:
        return {
            "ok": False,
            "detail": f"Plist creado pero `launchctl load` falló: {out}",
            "path": str(path),
        }
    return {
        "ok": True,
        "path": str(path),
        "detail": f"Arranque automático activado vía launchd: {path}",
    }


async def _disable_macos() -> dict[str, Any]:
    runtime_policy.require_runtime_capability("external_autostart")
    path = _macos_launchagent_file()
    if not path.exists():
        return {"ok": True, "detail": "Ya estaba desactivado.", "path": str(path)}
    # Best-effort unload — ignore errors (the plist might never have loaded).
    await _macos_launchctl("unload", path)
    try:
        path.unlink()
    except OSError as e:
        return {"ok": False, "detail": f"No se pudo borrar {path}: {e}"}
    return {"ok": True, "detail": "Arranque automático desactivado.", "path": str(path)}


async def _status_macos() -> dict[str, Any]:
    path = _macos_launchagent_file()
    return {
        "enabled": path.exists(),
        "method": "launchd",
        "path": str(path),
    }


# ─────────────────────────────────────────────────────────────────────
# Public async API — what wizard/app.py calls
# ─────────────────────────────────────────────────────────────────────

async def enable_autostart() -> dict[str, Any]:
    """Make Hermes start at login. Returns a result dict:

        {"ok": bool, "detail": str, ...}
    """
    runtime_policy.require_runtime_capability("external_autostart")
    if _os() == "windows":
        return _enable_windows()
    if _os() == "linux":
        return _enable_linux()
    if _os() == "darwin":
        return await _enable_macos()
    return {"ok": False, "detail": f"SO no soportado: {platform.system()}"}


async def disable_autostart() -> dict[str, Any]:
    """Undo `enable_autostart()`. Safe to call repeatedly."""
    runtime_policy.require_runtime_capability("external_autostart")
    if _os() == "windows":
        return _disable_windows()
    if _os() == "linux":
        return _disable_linux()
    if _os() == "darwin":
        return await _disable_macos()
    return {"ok": False, "detail": f"SO no soportado: {platform.system()}"}


async def autostart_status() -> dict[str, Any]:
    """Is autostart currently active?"""
    runtime_policy.get_runtime()
    if _os() == "windows":
        return _status_windows()
    if _os() == "linux":
        return _status_linux()
    if _os() == "darwin":
        return await _status_macos()
    return {"enabled": False, "method": "unknown"}
