"""Windows installer operations (A08.9), run with an Iris version's own Python.

The NSIS installer lays files down and shows its pages; every decision that
needs Iris' rules runs here, with the code the update channel uses (A08.8):

``plan``      verify a just-extracted version folder (every hash) and decide:
              fresh install, repair or upgrade. Exit code = action (see ACTIONS).
``commit``    finish that action from the version's final folder: write the
              installation state, refresh the launcher, remove the copy a repair
              replaced, or hand an upgrade to the activation helper of the
              version that is running now.
``close``     close this installation's window, services, leftovers and the
              Companion Iris installed, before the installer copies a new
              version (exit code 3 when something was open).
``uninstall`` close, remove the Companion app Iris installed and, only when
              asked, the Iris profile; then unlink every link inside the
              installation, so the installer's recursive delete cannot follow
              one. Hermes' home is never removed: Hermes Agent and other apps
              share it.
``profile``   print the profile folders (for the uninstaller's confirmation).

Only real version folders of this installation (``v/<8 hex>``, no link, inside
``v/`` once resolved, files matching their own manifests) ever run a program.
Every delete unlinks links instead of entering them.

The installer runs it as ``<version>\\runtimes\\iris\\python.exe -E -s -B -P -X utf8
-m core.iris_installer ...`` with ``IRIS_RUNTIME_DESCRIPTOR`` pointing at that
version's runtime.json, hidden (no console). Output: one JSON line; the plain
message the installer shows when the command refuses (exit code 1); or, for
``uninstall``, the plain warnings it shows before finishing (exit code 2).
With ``IRIS_INSTALLER_OUTPUT=utf-16`` the output is UTF-16, which NSIS' nsExec
recognises; otherwise it would read it as ANSI and garble accents.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from core import iris_install as inst
from core.iris_install import Install, InstallError

# Exit codes of ``plan``: what NSIS does with the extracted folder next.
ACTIONS = {
    "install": 10,          # rename it to v/<id>, then commit --mode install
    "repair-keep": 11,      # the installed copy is intact: delete it, commit --mode repair
    "repair-replace": 12,   # Iris was closed: swap the damaged or missing v/<id> for it, commit --mode repair
    "upgrade": 13,          # rename it to v/<id>, then commit --mode upgrade
    "upgrade-keep": 14,     # v/<id> already holds this version intact: delete it, commit --mode upgrade
    "upgrade-replace": 15,  # v/<id> holds a damaged copy: swap it, commit --mode upgrade
}
REFUSED = 1
WARNINGS = 2  # uninstall: done, but something was kept or could not be cleaned
CLOSED = 3  # close: Iris or the Companion was open and is now closed (reopen it if the install fails)
_NO_WINDOW = 0x08000000
_REPARSE_POINT = 0x400
# v/ entries the installer itself creates: _<7 hex> extraction, -<7 hex> the copy a
# swap replaced, ~ ! = + <7 hex> a stale extraction set aside before a new one.
_INSTALLER_PREFIXES = "_-~!=+"
_TRASH_PREFIXES = "-~!=+"
# A leftover only counts as Iris' own if it holds one of these (an interrupted
# extraction writes some of them first).
_VERSION_MARKERS = (inst.STAGING_MANIFEST, inst.INVENTORY, inst.PIN, inst.DESCRIPTOR, inst.CHANNEL, inst.EXECUTABLE)


def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """Every program this module starts goes through here (one seam for the tests)."""
    return subprocess.run(command, **kwargs)


def _python(folder: Path) -> Path:
    return folder / "runtimes" / "iris" / "python.exe"


def _version_env(folder: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in {"PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "VIRTUAL_ENV"}}
    env.update(IRIS_RUNTIME_DESCRIPTOR=str(folder / inst.DESCRIPTOR), PYTHONNOUSERSITE="1",
               PYTHONDONTWRITEBYTECODE="1")
    return env


def _staged_name(version: str) -> str:
    """``v/_xxxxxxx``: the extraction folder, as long as ``v/<id>`` (A08.8 path budget)."""
    return "_" + version[1:]


def _backup_name(version: str) -> str:
    """``v/-xxxxxxx``: where a swap moves the copy it replaces until commit removes it."""
    return "-" + version[1:]


def _installer_folder_name(name: str, prefixes: str = _INSTALLER_PREFIXES) -> bool:
    return bool(inst._ID.match(name) or (name[:1] in prefixes and inst._ID.match("0" + name[1:])))


def _writable_retry(function, path, _exc) -> None:
    """rmtree helper: clear the read-only attribute once and retry."""
    os.chmod(path, 0o700)
    function(path)


def _newer(left: str, right: str) -> bool:
    from core.iris_updates import _version

    return _version(left) > _version(right)


# ── Links, containment and safe deletes ────────────────────────────────────

def is_link(path: Path | str) -> bool:
    """A symlink or any reparse point (a Windows junction is not ``islink`` before 3.12)."""
    try:
        info = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _canonical(path: Path | str) -> str:
    return os.path.normcase(os.path.realpath(path))


def overlaps(left: Path | str, right: Path | str) -> bool:
    """Same folder, or one inside the other, once links and aliases are resolved."""
    a, b = _canonical(left), _canonical(right)
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep) or b.startswith(a.rstrip(os.sep) + os.sep)


def remove_tree(path: Path) -> None:
    """Delete a folder without following links: a link is unlinked, never entered.

    ``shutil.rmtree`` already unlinks junctions it meets inside the tree (3.8+)
    but refuses a link as its argument; that case is unlinked here.
    """
    if not os.path.lexists(path):
        return
    if is_link(path):
        try:
            os.rmdir(path)
        except OSError:
            os.unlink(path)
        return
    shutil.rmtree(path, onerror=_writable_retry)


def unlink_links(root: Path) -> list[str]:
    """Remove every link (symlink or junction) under *root*, without entering it.

    ``os.walk`` follows junctions on Windows even with ``followlinks=False``,
    so each entry is checked explicitly. NSIS ``RMDir /r`` follows junctions:
    the uninstaller calls this before deleting the installation.
    """
    removed = []
    for current, directories, files in os.walk(root, topdown=True):
        for name in list(directories):
            path = Path(current) / name
            if is_link(path):
                remove_tree(path)
                directories.remove(name)
                removed.append(path.relative_to(root).as_posix())
        for name in files:
            path = Path(current) / name
            if is_link(path):
                os.unlink(path)
                removed.append(path.relative_to(root).as_posix())
    return removed


def version_folders(install: Install) -> list[Path]:
    """Real version folders of this installation: ``v/<8 hex>``, not a link,
    directly inside ``v/`` once resolved. Anything else never runs a program."""
    if not install.versions.is_dir() or is_link(install.versions):
        return []
    base = _canonical(install.versions)
    return [entry for entry in sorted(install.versions.iterdir())
            if inst._ID.match(entry.name) and not is_link(entry) and entry.is_dir()
            and os.path.dirname(_canonical(entry)) == base]


def _intact(folder: Path, *, hashes: bool = True) -> bool:
    """Files match the version's own manifests; links inside are refused (A08.8)."""
    try:
        inst.verify_version(folder, hashes=hashes, executables=True)
        return True
    except (InstallError, OSError, KeyError, ValueError):
        return False


def _remove_installer_trash(install: Install, keep: Path | None = None) -> list[str]:
    """Remove what earlier interrupted runs left in v/: set-aside extractions and
    replaced copies (never a real version, never the current extraction)."""
    removed = []
    if not install.versions.is_dir() or is_link(install.versions):
        return removed
    for entry in sorted(install.versions.iterdir()):
        if entry == keep or not _installer_folder_name(entry.name, _TRASH_PREFIXES + "_"):
            continue
        if inst._ID.match(entry.name):
            continue  # a real version folder
        remove_tree(entry)
        removed.append(entry.name)
    return removed


# ── Closing an installation ────────────────────────────────────────────────

def close_installation(install: Install, timeout: float = 30) -> dict[str, Any]:
    """Close the window, services and any process started from this installation.

    Only verified version folders run their own programs (``Iris.exe --iris-exit``,
    ``stop-services``); each stops its services with its own rules (A08.8).
    Whatever still runs from the installation afterwards is terminated. Never
    touches a process outside the installation, nor this process.
    """
    from core import iris_updates as upd

    closed: dict[str, Any] = {"windows": [], "services": [], "terminated": [], "not_run": []}
    versions = []
    for folder in version_folders(install):
        if _intact(folder, hashes=False):
            versions.append(folder)
        else:
            closed["not_run"].append(folder.name)
    for folder in versions:
        if upd.processes_of(folder, profile_only=False, name=inst.EXECUTABLE):
            _run([str(folder / inst.EXECUTABLE), "--iris-exit"], cwd=str(folder), env=_version_env(folder),
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=timeout, creationflags=_NO_WINDOW if os.name == "nt" else 0, check=False)
            closed["windows"].append(folder.name)
    for folder in versions:
        try:
            upd._stop_version_services(install, folder.name)
            closed["services"].append(folder.name)
        except (OSError, subprocess.SubprocessError):
            pass
    remaining = upd._wait_gone(upd.processes_of(install.root, profile_only=False), timeout)
    for process in remaining:
        try:
            process.terminate()
            closed["terminated"].append(process.info.get("name") or str(process.pid))
        except Exception:
            pass
    remaining = upd._wait_gone(remaining, 10)
    if remaining:
        names = sorted({str(process.info.get("name") or process.pid) for process in remaining})
        raise InstallError(f"Un proceso de Iris sigue en uso ({', '.join(names)}). Ciérralo y vuelve a intentarlo.")
    from core.companion_lifecycle import close_companion_for_update

    try:
        closed["companion"] = close_companion_for_update()
    except RuntimeError as exc:
        raise InstallError(f"No se pudo cerrar el Companion: {exc}") from exc
    return closed


def closed_anything(closed: dict[str, Any]) -> bool:
    return bool(closed.get("windows") or closed.get("terminated") or closed.get("companion"))


# ── plan / commit ──────────────────────────────────────────────────────────

def _iris_leftover(entry: Path) -> bool:
    """A folder an interrupted Iris install left in v/: Iris name, no link, Iris files."""
    return (_installer_folder_name(entry.name) and not is_link(entry) and entry.is_dir()
            and any(os.path.lexists(entry / marker) for marker in _VERSION_MARKERS))


def plan(root: Path, staged: Path) -> tuple[str, dict[str, Any]]:
    """Decide what to do with a version the installer has just extracted."""
    install = Install(Path(root).absolute())
    staged = Path(staged).absolute()
    if is_link(install.versions) or is_link(staged):
        raise InstallError("La carpeta de la instalación contiene un enlace; no se instaló nada.")
    info = inst.verify_version(staged)  # every file against the version's own manifests
    if staged.parent != install.versions or staged.name != _staged_name(info["id"]):
        raise InstallError("El instalador extrajo la versión en una carpeta inesperada.")
    if not inst.fits(install.root, info["max_relative_path"]):
        raise InstallError("La carpeta de instalación es demasiado larga para Windows: elige una ruta más corta.")
    target = install.folder(info["id"])
    result = {"id": info["id"], "iris": info["iris"], "hermes": info["hermes"]}
    if not install.state_path.exists():
        # A fresh install needs an empty folder. What an interrupted Iris attempt
        # left in v/ (Iris names and Iris files, nothing else) is removed; anything
        # else, also in a silent install that skips the folder page, is refused.
        leftovers = [entry for entry in install.versions.iterdir() if entry != staged]
        if (any(entry.name != inst.VERSIONS for entry in install.root.iterdir())
                or not all(_iris_leftover(entry) for entry in leftovers)):
            raise InstallError("La carpeta elegida no está vacía y no es una instalación de Iris OS.")
        for entry in leftovers:
            remove_tree(entry)
        return "install", result
    state = inst.read_state(install)
    if state.get("pending"):
        raise InstallError("Hay un cambio de versión de Iris en curso. Espera a que termine y vuelve a intentarlo.")
    result["cleaned"] = _remove_installer_trash(install, keep=staged)  # also any stale -<tail> before a swap
    if os.path.lexists(target) and (is_link(target) or not target.is_dir()):
        raise InstallError("La carpeta de la versión es un enlace; no se instaló nada.")
    active = state["active"]
    result["previous"] = state["versions"].get(active, {}).get("iris")
    if info["id"] == active:
        if target.is_dir() and _intact(target):
            return "repair-keep", result
        result["closed"] = close_installation(install)  # a missing target: an interrupted swap is resumed
        return "repair-replace", result
    if _newer(state["versions"][active]["iris"], info["iris"]):
        raise InstallError(f"Ya tienes Iris OS {state['versions'][active]['iris']}, más nueva que "
                           f"{info['iris']}. Para volver a una versión anterior usa «Volver a…» en Iris.")
    if not _intact(install.folder(active), hashes=False):
        raise InstallError("La versión instalada está dañada y no puede ceder el paso a la nueva. Desinstala Iris "
                           "OS (tu perfil se conserva) y vuelve a ejecutar este instalador.")
    if not target.exists():
        return "upgrade", result
    return ("upgrade-keep" if _intact(target) else "upgrade-replace"), result


def _launcher_from(install: Install, version: str) -> None:
    source = install.folder(version) / inst.EXECUTABLE
    if not install.launcher.exists() or inst.sha256_file(source) != inst.sha256_file(install.launcher):
        staged = install.root / (inst.EXECUTABLE + ".new")
        shutil.copyfile(source, staged)
        os.replace(staged, install.launcher)


def commit(root: Path, version: str, mode: str) -> dict[str, Any]:
    """Finish ``plan``'s action once NSIS placed the folder at ``v/<version>``."""
    install = Install(Path(root).absolute())
    folder = install.folder(version)
    if is_link(folder) or folder not in version_folders(install):
        raise InstallError("La carpeta de la versión no es una carpeta real de esta instalación.")
    info = inst.verify_version(folder, hashes=False, executables=True)  # fully hashed by plan
    if info["id"] != version or not inst.fits(install.root, info["max_relative_path"]):
        raise InstallError("La versión instalada no coincide con la verificada.")
    install.updates.mkdir(exist_ok=True)
    # The copy a swap replaced is kept until here, the version that replaced it verified.
    replaced = install.versions / _backup_name(version)
    remove_tree(replaced)
    if mode == "install":
        if install.state_path.exists():
            raise InstallError("Ya existe una instalación en esta carpeta.")
        _launcher_from(install, version)
        inst.write_state(install, {
            "schema": 1, "active": version, "previous": None, "confirmed": True, "pending": None,
            "channel_sequences": {}, "last_activation": None,
            "versions": {version: inst.version_record(info, "installer")},
        })
        return {"result": "installed", "active": version, "iris": info["iris"]}
    if mode == "repair":
        with inst.state_lock(install):
            state = inst.read_state(install)
            if state["active"] != version:
                raise InstallError("La versión reparada ya no es la activa.")
        _launcher_from(install, version)
        inst.clear_trial(install)
        inst.sync_windows_entry(install)
        return {"result": "repaired", "active": version, "iris": info["iris"]}
    if mode != "upgrade":
        raise InstallError(f"Modo desconocido: {mode}")
    with inst.state_lock(install):
        state = inst.read_state(install)
        own = state["active"]
        if state.get("pending") or own == version:
            raise InstallError("La instalación cambió mientras se instalaba la versión nueva.")
        state["versions"].setdefault(version, inst.version_record(info, "installer"))
        state["pending"] = {"operation": "upgrade", "from": own, "to": version, "phase": "starting",
                            "started_at": inst.now(), "helper": None, "backup": None,
                            "previous_before": state.get("previous")}
        inst.write_state(install, state)
    # The running version switches itself, as for a channel update: it closes Iris,
    # backs up the profile if its format or Hermes changes, starts the new version
    # on trial and goes back if it does not confirm. Installed apps keeps showing
    # the running version until the new one confirms (inst.sync_windows_entry).
    helper = spawn_activation(install, own, version)
    return {"result": "upgrading", "from": own, "to": version, "iris": info["iris"], "helper": helper}


def spawn_activation(install: Install, own: str, target: str) -> int:
    import psutil

    from core.iris_updates import _spawn_detached

    folder = install.folder(own)
    if folder not in version_folders(install) or not _intact(folder, hashes=False):
        with inst.state_lock(install):
            state = inst.read_state(install)
            state["pending"] = None
            inst.write_state(install, state)
        raise InstallError("La versión instalada no es una carpeta íntegra de esta instalación.")
    command = [str(_python(folder)), "-E", "-s", "-B", "-P", "-X", "utf8", "-m", "core.iris_updates", "activate",
               "--root", str(install.root), "--to", target, "--operation", "upgrade"]
    try:
        process = _spawn_detached(command, install.updates, _version_env(folder), install.updates / "activation.log")
        created = psutil.Process(process.pid).create_time()
    except Exception as exc:
        with inst.state_lock(install):
            state = inst.read_state(install)
            state["pending"] = None
            inst.write_state(install, state)
        raise InstallError(f"No se pudo iniciar el cambio de versión: {exc}") from exc
    with inst.state_lock(install):
        state = inst.read_state(install)
        if state.get("pending") and state["pending"].get("helper") is None:
            state["pending"]["helper"] = {"pid": process.pid, "created": created}
            inst.write_state(install, state)
    return process.pid


# ── Uninstall ──────────────────────────────────────────────────────────────

def profile_folders() -> dict[str, str]:
    from core.config import get_iris_home
    from core.workspace_sync import get_hermes_home

    return {"iris_home": str(get_iris_home()), "hermes_home": str(get_hermes_home())}


def _remove_companion_entry(folder: Path) -> bool:
    """Drop the Companion's own Installed-apps entry when it names this folder."""
    if os.name != "nt":
        return False
    import winreg

    branch = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\IRIS Desktop Companion"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, branch) as key:
            location = str(winreg.QueryValueEx(key, "InstallLocation")[0]).strip().strip('"')
    except OSError:
        return False
    if not location or _canonical(location) != _canonical(folder):
        return False
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, branch)
        return True
    except OSError:
        return False


def companion_preflight(managed: Path) -> str:
    """Why the Companion folder must not be touched ('' when it may be closed or removed).

    The folder must be exactly ``<Iris profile>/companion/app`` with no link on
    the way from the profile (resolving the target instead would make an outside
    folder look authorised), and must not hold Hermes' home, which Iris never
    deletes. Checked before diagnosing, stopping, running or deleting anything.
    """
    from core.config import get_iris_home
    from core.workspace_sync import get_hermes_home

    anchor = Path(get_iris_home())  # the validated, resolved profile
    expected = anchor / "companion" / "app"
    if os.path.normcase(str(managed)) != os.path.normcase(str(expected)):
        return f"{managed} no es la carpeta del Companion que gestiona Iris"
    for segment in (anchor / "companion", expected):
        if is_link(segment):
            return f"{segment} es un enlace"
    if os.path.lexists(expected) and _canonical(expected) != os.path.normcase(
            os.path.join(os.path.realpath(anchor), "companion", "app")):
        return f"{expected} no está dentro de tu perfil de Iris"
    from core.companion_lifecycle import _EXECUTABLE_NAMES

    for program in (expected / "uninstall.exe", *(expected / name for name in _EXECUTABLE_NAMES)):
        if is_link(program):
            return f"{program} es un enlace"
    hermes = Path(get_hermes_home())
    if overlaps(hermes, expected):
        return f"contiene los datos de Hermes ({hermes}), que nunca se borran"
    return ""


def remove_companion(timeout: float = 120) -> dict[str, Any]:
    """Uninstall the Companion app Iris installed in its own profile folder,
    healthy or damaged. A Companion installed anywhere else is kept untouched,
    and so is the managed folder when it is reached through a link or holds
    Hermes' home (``companion_preflight``)."""
    from core import companion_lifecycle as companion
    from core import iris_updates as upd

    managed = companion._controlled_install_dir()
    if os.path.lexists(managed) or os.path.lexists(managed.parent):
        reason = companion_preflight(managed)
        if reason:
            return {"installed": True, "warning": f"El Companion no se tocó: {reason}."}
    try:
        installation = companion._installation_diagnostic()
    except Exception:
        installation = {}
    executable = installation.get("_executable")
    present = managed.is_dir() and any(managed.iterdir()) if os.path.lexists(managed) else False
    if not present:
        if isinstance(executable, Path):
            return {"installed": True, "kept": "instalado fuera de Iris OS; se conserva"}
        return {"installed": False}
    report: dict[str, Any] = {"installed": True, "state": installation.get("state") or "damaged"}
    if isinstance(executable, Path) and overlaps(executable.parent, managed):
        try:
            report["stop"] = companion._stop_companion()[0]
        except Exception as exc:
            report["stop"] = f"{getattr(exc, 'detail', exc)}"
    leftovers = upd.processes_of(managed, profile_only=False)  # only programs inside the managed folder
    for process in leftovers:
        try:
            process.terminate()
        except Exception:
            pass
    upd._wait_gone(leftovers, 10)
    startup = companion._startup_path()
    if startup and startup.is_file():
        try:
            if _canonical(managed) in os.path.normcase(companion._read_autostart_script(startup)):
                startup.unlink()
                report["autostart"] = "removed"
        except OSError:
            pass
    # The Companion's own NSIS uninstaller deletes with RMDir /r, which follows
    # junctions: unlink every link inside its folder first.
    report["links_removed"] = unlink_links(managed)
    uninstaller = managed / "uninstall.exe"
    if uninstaller.is_file() and not is_link(uninstaller):
        # _?= runs the NSIS uninstaller in place and waits for it.
        try:
            result = _run([str(uninstaller), "/S", f"_?={managed}"], timeout=timeout, check=False,
                                    creationflags=_NO_WINDOW if os.name == "nt" else 0)
            report["uninstaller"] = result.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            report["uninstaller"] = str(exc)
    report["entry_removed"] = _remove_companion_entry(managed)
    try:
        remove_tree(managed)
    except OSError:
        pass
    for record in (companion._install_record_path(), companion._install_journal_path()):
        try:
            record.unlink(missing_ok=True)
        except OSError:
            pass
    report["removed"] = not os.path.lexists(managed)
    if not report["removed"]:
        report["warning"] = f"No se pudo quitar por completo el Companion de {managed}."
    return report


def delete_profile() -> dict[str, Any]:
    """Remove the Iris profile folder after explicit confirmation in the uninstaller.

    Kept, with the reason, when it is not an idle Iris profile, is a link, or
    holds Hermes' home (the promise is to never delete Hermes data).
    """
    from core.profile_paths import ProfilePaths, _existing_compatible_writer_pids, get_profile_paths

    paths = get_profile_paths()
    home = paths.home
    folders = profile_folders()
    hermes = folders["hermes_home"]

    def kept(reason: str) -> dict[str, Any]:
        return {"deleted": False, "reason": reason, "kept_hermes": hermes}

    if is_link(home):
        return kept("La carpeta del perfil es un enlace; se conserva.")
    if overlaps(hermes, home) and not Path(_canonical(home)).is_relative_to(Path(_canonical(hermes))):
        return kept(f"Los datos de Hermes ({hermes}) están dentro de tu perfil de Iris; para no borrarlos, "
                    "el perfil se conserva.")
    if _canonical(home) == _canonical(hermes):
        return kept("Hermes usa la misma carpeta que tu perfil de Iris; para no borrarlo, el perfil se conserva.")
    if home == Path.home() or len(home.parts) < 3 or not (paths.state / "format.json").is_file():
        return kept("La carpeta no parece un perfil de Iris; se conserva.")
    if _existing_compatible_writer_pids(ProfilePaths(home)):
        return kept("Otra instalación de Iris está usando el perfil; se conserva.")
    remove_tree(home)
    return {"deleted": str(home), "kept_hermes": hermes}


def uninstall(root: Path, delete_data: bool) -> dict[str, Any]:
    """Everything before the installer deletes the installation folder.

    Refused, with nothing changed, when the Iris profile or Hermes' home is
    inside the installation: deleting the folder would delete them.
    """
    install = Install(Path(root).absolute())
    try:
        folders = profile_folders()
    except ValueError as exc:
        raise InstallError(f"No se pudo comprobar dónde está tu perfil ({exc}); no se desinstaló nada.") from exc
    for label, folder in (("tu perfil de Iris", folders["iris_home"]), ("los datos de Hermes", folders["hermes_home"])):
        if overlaps(folder, install.root):
            raise InstallError(f"{label.capitalize()} ({folder}) está dentro de la carpeta de instalación, que se "
                               "borra al desinstalar. Muévelo a otra carpeta y vuelve a intentarlo; no se borró nada.")
    report: dict[str, Any] = {"closed": close_installation(install), "companion": remove_companion()}
    warnings = []
    if report["companion"].get("warning"):
        warnings.append(report["companion"]["warning"])
    if delete_data:
        report["profile"] = delete_profile()
        if not report["profile"].get("deleted"):
            warnings.append(f"Tu perfil de Iris no se borró: {report['profile']['reason']}")
    report["links_removed"] = unlink_links(install.root)
    report["warnings"] = warnings
    return report


# ── Command line ───────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    if os.environ.get("IRIS_INSTALLER_OUTPUT") == "utf-16":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-16", newline="\r\n", write_through=True)
    parser = argparse.ArgumentParser(prog="python -m core.iris_installer")
    commands = parser.add_subparsers(dest="command", required=True)
    plan_cmd = commands.add_parser("plan")
    plan_cmd.add_argument("--root", required=True, type=Path)
    plan_cmd.add_argument("--staged", required=True, type=Path)
    commit_cmd = commands.add_parser("commit")
    commit_cmd.add_argument("--root", required=True, type=Path)
    commit_cmd.add_argument("--version", required=True)
    commit_cmd.add_argument("--mode", required=True, choices=("install", "repair", "upgrade"))
    close_cmd = commands.add_parser("close")
    close_cmd.add_argument("--root", required=True, type=Path)
    uninstall_cmd = commands.add_parser("uninstall")
    uninstall_cmd.add_argument("--root", required=True, type=Path)
    uninstall_cmd.add_argument("--delete-profile", action="store_true")
    commands.add_parser("profile")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            action, result = plan(args.root, args.staged)
            print(json.dumps({"action": action, **result}, ensure_ascii=False))
            return ACTIONS[action]
        if args.command == "commit":
            print(json.dumps(commit(args.root, args.version, args.mode), ensure_ascii=False))
        elif args.command == "close":
            closed = close_installation(Install(args.root.absolute()))
            print(json.dumps(closed, ensure_ascii=False))
            return CLOSED if closed_anything(closed) else 0
        elif args.command == "uninstall":
            report = uninstall(args.root, args.delete_profile)
            if report["warnings"]:
                print("Iris OS se desinstaló con avisos:\n- " + "\n- ".join(report["warnings"]))
                return WARNINGS
            print(json.dumps(report, ensure_ascii=False))
        else:
            print(json.dumps(profile_folders(), ensure_ascii=False))
        return 0
    except (InstallError, OSError, ValueError) as exc:
        print(str(exc) or "No se pudo completar la operación.")
        return REFUSED


if __name__ == "__main__":
    sys.exit(main())
