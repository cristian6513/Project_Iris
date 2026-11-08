"""Mutable profile paths and cooperative writer/migration exclusion.

The persistent coordination file is never removed. Each active writer holds a
native lock on its own lease, so a crash releases ownership without trusting a
PID. Known pre-contract Core/Wizard entrypoints are conservatively checked
before the first preparation. Arbitrary binaries and custom entrypoints cannot
be detected by this protocol; service checks remain a separate safeguard.
No credential store is copied or relocated here.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

PROFILE_FORMAT_VERSION = 1
_MAX_METADATA_BYTES = 16 * 1024
_COORDINATION_LOCKS: dict[str, threading.RLock] = {}
_COORDINATION_GUARD = threading.Lock()
_COORDINATION_STATE = threading.local()


class ProfilePathError(ValueError):
    """A profile path aliases a resource or belongs to the installation."""


class ProfileBusyError(RuntimeError):
    """Another writer or profile format prevents a compatible mutation."""

    status_code = 409
    code = "profile_incompatible_writer"


def validate_profile_home(path: str | Path) -> Path:
    """Validate before creating a home or running an existing home migration."""
    from core.runtime_descriptor import get_runtime

    runtime = get_runtime()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ProfilePathError("El perfil debe usar una ruta absoluta.")
    try:
        canonical = candidate.resolve(strict=False)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ProfilePathError("La ruta del perfil no es válida.") from exc
    if runtime.packaged:
        installation = runtime.base.resolve(strict=True)
        # A version of a versioned installation (A08.8) lives in <root>/v/<id>; the
        # uninstaller deletes the whole <root>, so the profile must stay out of it.
        if installation.parent.name.casefold() == "v":
            installation = installation.parent.parent
        if canonical.is_relative_to(installation) or installation.is_relative_to(canonical):
            raise ProfilePathError("El perfil mutable debe quedar fuera de la instalación.")
    if canonical.exists() and not canonical.is_dir():
        raise ProfilePathError("La raíz del perfil debe ser un directorio.")
    return canonical


def _plain_directory(path: Path, home: Path) -> None:
    """Forbid internal symlinks/junctions, including aliases inside the home."""
    try:
        relative = path.relative_to(home)
        current = home
        for part in relative.parts:
            current = current / part
            if (
                current.is_symlink()
                or (hasattr(current, "is_junction") and current.is_junction())
                or current.resolve(strict=False) != current
            ):
                raise ProfilePathError("El perfil contiene un enlace de directorio no permitido.")
            if current.exists() and not current.is_dir():
                raise ProfilePathError("Un directorio del perfil tiene un tipo inválido.")
    except (OSError, ValueError, RuntimeError) as exc:
        if isinstance(exc, ProfilePathError):
            raise
        raise ProfilePathError("No se pudo validar el directorio del perfil.") from exc


@dataclass(frozen=True)
class ProfilePaths:
    home: Path
    animations: Path = field(init=False)
    state: Path = field(init=False)
    migrations: Path = field(init=False)

    def __post_init__(self) -> None:
        home = validate_profile_home(self.home)
        object.__setattr__(self, "home", home)
        for name, directory in (
            ("animations", home / "animations"),
            ("state", home / ".profile"),
            ("migrations", home / "migrations"),
        ):
            validate_profile_home(directory)
            _plain_directory(directory, home)
            object.__setattr__(self, name, directory)


def get_profile_paths() -> ProfilePaths:
    from core.config import get_iris_home

    return ProfilePaths(get_iris_home())


def _profile_key(paths: ProfilePaths) -> str:
    return os.path.normcase(str(paths.home))


def _open_plain_file(path: Path, *, create: bool = True) -> BinaryIO:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ProfilePathError("El control del perfil contiene un archivo no permitido.")
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    if create:
        flags |= os.O_CREAT
    descriptor = os.open(path, flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or opened.st_nlink != 1
            or current.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ProfilePathError("El control del perfil cambió o contiene un alias.")
        handle = os.fdopen(descriptor, "r+b")
    except BaseException:
        os.close(descriptor)
        raise
    return handle


def _try_lock(handle: BinaryIO) -> bool:
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def profile_coordination(
    *,
    paths: ProfilePaths | None = None,
    timeout: float = 10.0,
) -> Iterator[ProfilePaths]:
    """Serialize registration and migration; reentrant within the same thread."""
    paths = ProfilePaths(paths.home) if paths is not None else get_profile_paths()
    key = _profile_key(paths)
    with _COORDINATION_GUARD:
        mutex = _COORDINATION_LOCKS.setdefault(key, threading.RLock())
    with mutex:
        depths = getattr(_COORDINATION_STATE, "depths", None)
        if depths is None:
            depths = _COORDINATION_STATE.depths = {}
        if key in depths:
            depths[key] += 1
            try:
                yield paths
            finally:
                depths[key] -= 1
            return
        paths.home.mkdir(parents=True, exist_ok=True)
        _plain_directory(paths.state, paths.home)
        paths.state.mkdir(exist_ok=True)
        with _open_plain_file(paths.state / "coordination.lock") as handle:
            deadline = time.monotonic() + max(0.0, timeout)
            while not _try_lock(handle):
                if time.monotonic() >= deadline:
                    raise ProfileBusyError("Otro proceso está preparando este perfil.")
                time.sleep(0.02)
            try:
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                depths[key] = 1
                try:
                    yield paths
                finally:
                    depths.pop(key, None)
            finally:
                _unlock(handle)


def _document(raw: bytes) -> dict:
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate profile field")
            result[key] = value
        return result

    if len(raw) > _MAX_METADATA_BYTES:
        raise ValueError("Profile metadata exceeds its size limit")
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_fields)
    if not isinstance(data, dict):
        raise ValueError("Invalid profile metadata")
    return data


def _persisted_format(paths: ProfilePaths) -> int | None:
    target = paths.state / "format.json"
    if not target.exists() and not target.is_symlink():
        return None
    try:
        with _open_plain_file(target) as handle:
            data = _document(handle.read(_MAX_METADATA_BYTES + 1))
        if (
            set(data) != {"schema_version", "format_version"}
            or type(data["schema_version"]) is not int
            or data["schema_version"] != 1
            or type(data["format_version"]) is not int
            or data["format_version"] < 0
        ):
            raise ValueError("Unknown profile format")
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise ProfileBusyError("El formato persistido del perfil no es compatible.") from exc
    return data["format_version"]


def persisted_profile_format(paths: ProfilePaths | None = None) -> int | None:
    """Committed profile format, or ``None`` for a profile that never had one (A08.8)."""
    return _persisted_format(paths if paths is not None else get_profile_paths())


def _live_lease_metadata(handle: BinaryIO, format_version: int) -> dict:
    # Byte zero is natively locked on Windows. Metadata starts at byte one.
    handle.seek(1)
    data = _document(handle.read(_MAX_METADATA_BYTES + 1))
    if (
        set(data) != {"schema_version", "format_version", "role", "pid"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or type(data["format_version"]) is not int
        or data["format_version"] != format_version
        or not isinstance(data["role"], str)
        or not data["role"]
        or len(data["role"]) > 64
        or type(data["pid"]) is not int
        or data["pid"] <= 0
    ):
        raise ValueError("Incompatible profile writer")
    return data


def _compatible_writers(
    paths: ProfilePaths,
    format_version: int,
    *,
    active_pids: set[int] | None = None,
) -> Path:
    directory = paths.state / "writers"
    _plain_directory(directory, paths.home)
    directory.mkdir(exist_ok=True)
    for path in directory.glob("*.lease"):
        with _open_plain_file(path) as handle:
            if _try_lock(handle):
                _unlock(handle)
                stale = True
            else:
                stale = False
                try:
                    data = _live_lease_metadata(handle, format_version)
                    if active_pids is not None:
                        active_pids.add(data["pid"])
                except (OSError, UnicodeError, ValueError, RecursionError) as exc:
                    raise ProfileBusyError(
                        "Otro escritor activo usa un formato incompatible del perfil.",
                    ) from exc
        if stale:
            path.unlink(missing_ok=True)
    return directory


def _existing_compatible_writer_pids(paths: ProfilePaths) -> set[int]:
    """Probe existing leases without creating, repairing or deleting anything.

    Windows requires a writable descriptor for its lock probe, but this helper
    never writes through it. Metadata alone is never proof of a live lease.
    """
    directory = paths.state / "writers"
    _plain_directory(directory, paths.home)
    if not directory.exists():
        return set()
    pids = set()
    for path in directory.glob("*.lease"):
        try:
            with _open_plain_file(path, create=False) as handle:
                if _try_lock(handle):
                    _unlock(handle)
                else:
                    pids.add(_live_lease_metadata(handle, PROFILE_FORMAT_VERSION)["pid"])
        except FileNotFoundError:
            # A compatible writer may have closed and removed its own lease.
            continue
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise ProfileBusyError(
                "No se pudo acreditar el escritor existente del perfil."
            ) from exc
    return pids


def _legacy_marker_pids(paths: ProfilePaths) -> set[int]:
    """Read only bounded regular PID hints, never following profile aliases."""
    pids: set[int] = set()
    for name in ("IRIS.pid", "IRIS.pid.json", "companion_runtime.json"):
        path = paths.home / name
        try:
            if path.is_symlink() or not path.is_file():
                continue
            current = path.lstat()
            if current.st_nlink != 1 or current.st_size > _MAX_METADATA_BYTES:
                continue
            with path.open("rb") as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                    continue
                raw = handle.read(_MAX_METADATA_BYTES + 1)
            if len(raw) > _MAX_METADATA_BYTES:
                continue
            text = raw.decode("utf-8").strip()
            if text.isdecimal():
                pid = int(text)
            else:
                pid = _document(raw).get("pid")
            if type(pid) is int and pid > 0:
                pids.add(pid)
        except (OSError, UnicodeError, ValueError, RecursionError):
            # A stale or malformed hint is not proof of a writer. Enumeration
            # still checks a standard Wizard whose marker was never published.
            continue
    return pids


def _legacy_entrypoint(argv: list[str]) -> bool:
    """Recognize standard CLI, module, uvicorn and source-script invocations."""
    for value in argv[:2]:
        parts = value.replace("\\", "/").lower().split("/")
        if (
            len(parts) > 1
            and parts[-2] in {"scripts", "bin"}
            and parts[-1]
            in {
                "iris",
                "iris.exe",
                "alice",
                "alice.exe",
            }
        ):
            return True
    modules = {"core.agent", "core.desktop", "wizard", "wizard.app"}
    for index, value in enumerate(argv):
        if value == "-m" and index + 1 < len(argv) and argv[index + 1] in modules:
            return True
    if "wizard.app:app" in argv and any(
        Path(value).name.lower() in {"uvicorn", "uvicorn.exe"} for value in argv
    ):
        return True
    scripts = ("/core/agent.py", "/core/desktop.py", "/wizard/app.py", "/wizard/__main__.py")
    return any(
        ("/" + value.replace("\\", "/").lstrip("/")).lower().endswith(scripts) for value in argv[1:]
    )


def _possible_legacy_python(name: str | None) -> bool:
    if not name:
        return False
    stem = name.lower().removesuffix(".exe")
    return stem in {"python", "pythonw", "uvicorn", "iris", "alice"} or (
        stem.startswith("python") and stem[6:].replace(".", "").isdigit()
    )


def _legacy_environment_home(environment: dict[str, str]) -> Path | None:
    env = {key.upper(): value for key, value in environment.items()}
    default = env.get("HOME", "")
    if os.name == "nt":
        default = (
            env.get("USERPROFILE")
            or (env.get("HOMEDRIVE", "") + env.get("HOMEPATH", ""))
            or default
        )
    explicit = env.get("IRIS_HOME", "").strip()
    if explicit:
        if explicit == "~" or explicit.startswith(("~/", "~\\")):
            if not default:
                return None
            candidate = Path(default) / explicit[2:]
        else:
            candidate = Path(explicit)
    elif default:
        candidate = Path(default) / ".iris"
    else:
        return None
    if not candidate.is_absolute():
        return None
    return candidate.resolve(strict=False)


def _own_launchers() -> set[int]:
    """Ancestors that only relay this process with the very same arguments.

    On Windows a virtual environment's ``Scripts\\python.exe`` is a launcher
    that starts the base interpreter as its child and stays alive. It looks
    like another writer of this profile (same entrypoint and environment), so
    a development Wizard, Core or Desktop refused every new profile (A08.7).
    Only the unbroken chain of identical-argument parents is excluded.
    """
    import psutil

    # Best effort: if the chain cannot be inspected nothing is excluded, which
    # keeps the stricter (blocking) behaviour. Our own arguments come from the
    # interpreter itself; this process is never inspected through psutil.
    arguments = list(getattr(sys, "orig_argv", [])[1:])
    if not arguments:
        return set()
    try:
        parent = psutil.Process(os.getppid())
    except Exception:
        return set()
    launchers: set[int] = set()
    while parent is not None and len(launchers) < 4:
        try:
            if parent.cmdline()[1:] != arguments:
                break
            launchers.add(parent.pid)
            parent = parent.parent()
        except Exception:
            break
    return launchers


def _legacy_profile_writers(
    paths: ProfilePaths,
    *,
    compatible_pids: set[int] | None = None,
) -> None:
    """Block known live pre-contract writers; PID hints alone never authorize it.

    Process environments are consulted only after recognizing a standard Iris
    entrypoint. An inaccessible possible Python/marker candidate fails closed.
    This does not promise discovery of renamed binaries or custom launch code.
    """
    import psutil

    excluded = {os.getpid()} | _own_launchers() | (compatible_pids or set())
    markers = _legacy_marker_pids(paths)
    candidates = {}
    try:
        for process in psutil.process_iter(attrs=["pid", "name"]):
            pid = process.info["pid"]
            if pid in excluded:
                continue
            if pid in markers or _possible_legacy_python(process.info.get("name")):
                candidates[pid] = process
        for pid in markers - excluded - set(candidates):
            try:
                candidates[pid] = psutil.Process(pid)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
        for process in candidates.values():
            try:
                if not _legacy_entrypoint(process.cmdline()):
                    continue
                home = _legacy_environment_home(process.environ())
                if home is None:
                    raise ProfileBusyError("No se pudo descartar un escritor anterior del perfil.")
                if os.path.normcase(str(home)) == _profile_key(paths):
                    raise ProfileBusyError(
                        "Cierra el Core o Wizard anterior de este perfil antes de migrarlo.",
                    )
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except (OSError, ValueError, RuntimeError) as exc:
                if isinstance(exc, ProfileBusyError):
                    raise
                raise ProfileBusyError(
                    "No se pudo descartar un escritor anterior del perfil."
                ) from exc
    except psutil.AccessDenied as exc:
        raise ProfileBusyError(
            "No se pudo inspeccionar un posible escritor anterior del perfil."
        ) from exc


def assert_legacy_home_migration_idle(target: Path, *legacy_homes: Path) -> None:
    """Guard the existing home-name migration before any rename/copy/repair.

    Scan both the destination and every existing former home. This function
    creates no directories, leases or format markers and reads no credentials.
    """
    seen = set()
    for home in (target, *(home for home in legacy_homes if home.exists())):
        paths = ProfilePaths(home)
        key = _profile_key(paths)
        if key in seen:
            continue
        seen.add(key)
        compatible = _existing_compatible_writer_pids(paths)
        _legacy_profile_writers(paths, compatible_pids=compatible)


def _commit_format(paths: ProfilePaths, format_version: int) -> None:
    target = paths.state / "format.json"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=paths.state,
            prefix=".format-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump({"schema_version": 1, "format_version": format_version}, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def profile_writer(
    role: str,
    *,
    paths: ProfilePaths | None = None,
    format_version: int = PROFILE_FORMAT_VERSION,
    prepare: Callable[[], object] | None = None,
) -> Iterator[ProfilePaths]:
    """Prepare and register one compatible writer, holding its lease for life.

    No threading lock spans the yielded lifetime, so async callers may enter
    and exit using different executor workers. A failed prepare leaves no lease
    or committed profile format. Its own migration journal controls resumption.
    """
    if (
        not isinstance(role, str)
        or not role
        or len(role) > 64
        or any(ord(char) < 32 for char in role)
    ):
        raise ValueError("Invalid profile writer role")
    if type(format_version) is not int or format_version < 1:
        raise ValueError("Invalid profile format version")
    paths = ProfilePaths(paths.home) if paths is not None else get_profile_paths()
    lease: BinaryIO | None = None
    lease_path: Path | None = None
    try:
        with profile_coordination(paths=paths):
            persisted = _persisted_format(paths)
            if persisted is not None and (
                persisted > format_version or (persisted != format_version and prepare is None)
            ):
                raise ProfileBusyError("Este proceso no puede escribir el formato del perfil.")
            active_pids: set[int] = set()
            directory = _compatible_writers(paths, format_version, active_pids=active_pids)
            if prepare is not None:
                if persisted is None or persisted < PROFILE_FORMAT_VERSION:
                    _legacy_profile_writers(paths, compatible_pids=active_pids)
                prepare()
            # Revalidate after a caller's migration before any control writes.
            ProfilePaths(paths.home)
            _plain_directory(directory, paths.home)
            lease_path = directory / f"{uuid.uuid4().hex}.lease"
            lease = lease_path.open("x+b")
            if not _try_lock(lease):
                raise ProfileBusyError("No se pudo acreditar el escritor del perfil.")
            lease.write(b"\0")
            lease.write(
                json.dumps(
                    {
                        "schema_version": 1,
                        "format_version": format_version,
                        "role": role,
                        "pid": os.getpid(),
                    }
                ).encode("utf-8")
            )
            lease.flush()
            os.fsync(lease.fileno())
            if persisted != format_version:
                _commit_format(paths, format_version)
        yield paths
    finally:
        if lease is not None:
            # Closing a native locked descriptor always releases ownership,
            # including when coordination cannot be reacquired on shutdown.
            lease.close()
        if lease_path is not None:
            with profile_coordination(paths=paths):
                lease_path.unlink(missing_ok=True)


async def _settle_worker(task: asyncio.Task) -> tuple[object, bool]:
    """Finish a shielded native operation even during repeated cancellation."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled


@asynccontextmanager
async def async_profile_writer(
    role: str,
    *,
    paths: ProfilePaths | None = None,
    format_version: int = PROFILE_FORMAT_VERSION,
    prepare: Callable[[], object] | None = None,
):
    """Offload locks/preparation and always release a lease after cancellation."""
    context = profile_writer(role, paths=paths, format_version=format_version, prepare=prepare)
    entering = asyncio.create_task(asyncio.to_thread(context.__enter__))
    try:
        entered = await asyncio.shield(entering)
    except asyncio.CancelledError:
        try:
            await _settle_worker(entering)
        except BaseException:
            # Failed entry unwinds its own lease and no __exit__ is needed.
            pass
        else:
            cleanup = asyncio.create_task(asyncio.to_thread(context.__exit__, None, None, None))
            await _settle_worker(cleanup)
        raise
    try:
        yield entered
    finally:
        cleanup = asyncio.create_task(asyncio.to_thread(context.__exit__, None, None, None))
        _, cancelled = await _settle_worker(cleanup)
        if cancelled:
            raise asyncio.CancelledError
