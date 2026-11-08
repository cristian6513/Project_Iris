"""Validated desktop runtime contract shared by Core and Wizard.

Schema 1 remains the checkout preview. Schema 2 can describe private runtimes;
packaged paths are portable, confined to installation and never discovered on
PATH. Packaged versions update only through the joint channel (A08.8).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


DESCRIPTOR_ENV = "IRIS_RUNTIME_DESCRIPTOR"
MAX_DESCRIPTOR_BYTES = 64 * 1024
PYTHON_ENV_KEYS = frozenset({
    "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "VIRTUAL_ENV",
    "__PYVENV_LAUNCHER__", "UV_PROJECT_ENVIRONMENT",
})
_EXTERNAL_CAPABILITIES = frozenset({
    "external_hermes_install", "external_hermes_update", "source_update",
    "external_autostart",
})
# Allowed in every mode: they only write the mutable profile. Optional extras
# install approved locks into isolated profile environments, never into the
# running runtime (A08.5 replaced the former ``base_package_install``).
_PROFILE_CAPABILITIES = frozenset({"profile_configuration", "profile_extras"})
_CAPABILITIES = _EXTERNAL_CAPABILITIES | _PROFILE_CAPABILITIES | {"bundled_update"}
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
# Windows MAX_PATH (260 UTF-16 units) minus the terminating NUL. A packaged
# staging declares its longest relative path, so the installation folder can
# be checked before any deep file fails with an obscure error (A08.7).
WINDOWS_MAX_PATH = 259
_MAX_RELATIVE_PATH = 4096
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|CLOCK\$|COM[1-9¹²³]|LPT[1-9¹²³])\Z", re.I | re.ASCII)


class RuntimeDescriptorError(ValueError):
    """Invalid or incomplete installation; never fall back to external tools."""


class RuntimePolicyError(RuntimeError):
    code = "runtime_operation_unavailable"
    status_code = 403


@dataclass(frozen=True)
class BundledHermes:
    root: Path
    python: Path
    cli: Path
    version: str
    revision: str
    metadata: Path


@dataclass(frozen=True)
class RuntimeContext:
    schema_version: int
    mode: str
    descriptor_path: Path | None
    base: Path
    iris_root: Path
    iris_python: Path
    assets_root: Path
    iris_revision: str = ""
    hermes: BundledHermes | None = None
    max_relative_path: int | None = None

    @property
    def packaged(self) -> bool:
        return self.mode == "packaged"


def _object(value: Any, keys: set[str], label: str, optional: set[str] | None = None) -> dict:
    optional = optional or set()
    if not isinstance(value, dict) or set(value) - keys - optional or keys - set(value):
        raise RuntimeDescriptorError(f"Campos de runtime inválidos: {label}.")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    value: dict = {}
    for key, item in pairs:
        if key in value:
            raise RuntimeDescriptorError("El descriptor contiene campos duplicados.")
        value[key] = item
    return value


def _read_document(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_DESCRIPTOR_BYTES + 1)
        if len(data) > MAX_DESCRIPTOR_BYTES:
            raise RuntimeDescriptorError("El descriptor supera el tamaño permitido.")
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise RuntimeDescriptorError("No se pudo leer el descriptor de runtime.") from exc
    if not isinstance(value, dict):
        raise RuntimeDescriptorError("El descriptor debe ser un objeto JSON.")
    return value


def _text(value: Any, label: str, *, maximum: int = 8192) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or any(
        ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value
    ):
        raise RuntimeDescriptorError(f"Valor de runtime inválido: {label}.")
    return value


def _revision(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _REVISION.fullmatch(value):
        raise RuntimeDescriptorError(f"Revisión de runtime inválida: {label}.")
    return value


def _contained(path: Path, base: Path, label: str) -> None:
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise RuntimeDescriptorError(f"Ruta de runtime fuera de instalación: {label}.") from exc


def _resolve_path(value: Any, base: Path, label: str, *, packaged: bool, directory: bool) -> Path:
    if not isinstance(value, str) or not value or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise RuntimeDescriptorError(f"Valor de runtime inválido: {label}.")
    raw = _text(value, label, maximum=MAX_DESCRIPTOR_BYTES) if packaged else value
    if packaged:
        parts = raw.split("/")
        if any(c in raw for c in '\\:<>"|?*%') or any(
            part in {"", ".", ".."} or part.endswith((".", " "))
            or _DEVICE.fullmatch(part.split(".")[0].rstrip(" "))
            for part in parts
        ):
            raise RuntimeDescriptorError(f"Ruta de runtime no portable: {label}.")
    try:
        path = (base / raw).resolve(strict=True)
    except (OSError, ValueError, RuntimeError) as exc:
        raise RuntimeDescriptorError(f"No se encuentra el recurso de runtime: {label}.") from exc
    if packaged:
        _contained(path, base, label)
    if not (path.is_dir() if directory else path.is_file()):
        raise RuntimeDescriptorError(f"Tipo de recurso de runtime inválido: {label}.")
    return path


def _anchor(root: Path, relative: str, base: Path, packaged: bool) -> None:
    _resolve_path(str(root / relative), base, relative, packaged=False, directory=False)
    if packaged:
        _contained((root / relative).resolve(strict=True), root, relative)


def windows_path_length(path: str | Path) -> int:
    """Length as Windows counts it for MAX_PATH: UTF-16 code units.

    Iris.exe passes the canonical descriptor path, which carries the verbatim
    prefix (``\\\\?\\``); it is not part of the MAX_PATH budget and the native
    resolver does not count it either.
    """
    text = str(path)
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return len(text.encode("utf-16-le")) // 2


def install_root_limit(max_relative_path: int) -> int:
    """Longest installation folder that keeps every staged file under MAX_PATH."""
    return WINDOWS_MAX_PATH - 1 - max_relative_path


def _install_limits(value: Any, base: Path, packaged: bool) -> int:
    install = _object(value, {"max_relative_path"}, "install")
    limit = install["max_relative_path"]
    if type(limit) is not int or not 1 <= limit <= _MAX_RELATIVE_PATH:
        raise RuntimeDescriptorError("Límite de ruta de instalación inválido.")
    if packaged and os.name == "nt" and windows_path_length(base) > install_root_limit(limit):
        raise RuntimeDescriptorError(
            f"Iris está en una carpeta con una ruta demasiado larga para Windows "
            f"({windows_path_length(base)} caracteres; máximo {install_root_limit(limit)}). "
            "Mueve la carpeta de Iris a una ruta más corta, por ejemplo C:\\Iris."
        )
    return limit


def plain_windows_path(value: str | Path) -> str:
    """Drop the verbatim prefix (``\\\\?\\``) of a canonical Windows path.

    Iris.exe passes canonical paths, which Windows writes with that prefix,
    while the system reports processes (executable, cwd) without it. Paths
    derived from the descriptor must compare equal to those (A08.7: the bundled
    Gateway could not be credited). The MAX_PATH budget keeps the plain form valid.
    """
    text = str(value)
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\") and len(text) > 6 and text[5] == ":":
        return text[4:]
    return text


def load_runtime_descriptor(path: str | Path) -> RuntimeContext:
    """Validate a descriptor independently of cwd and browser-provided data."""
    path = Path(plain_windows_path(path))
    if not path.is_absolute():
        raise RuntimeDescriptorError("La ruta del descriptor debe ser absoluta.")
    try:
        base = path.parent.resolve(strict=True)
        canonical = path.resolve(strict=True)
    except (OSError, ValueError, RuntimeError) as exc:
        raise RuntimeDescriptorError("No se encuentra el descriptor de runtime.") from exc
    spec = _read_document(canonical)
    schema = spec.get("schema_version")
    mode = spec.get("mode")
    if type(schema) is not int or schema not in {1, 2} or not isinstance(mode, str) or mode not in {"development", "packaged"}:
        raise RuntimeDescriptorError("Esquema o modo de runtime no admitido.")
    packaged = mode == "packaged"
    if packaged:
        _contained(canonical, base, "runtime.json")
        if canonical.parent != base:
            raise RuntimeDescriptorError("El descriptor empaquetado debe pertenecer a la carpeta de instalación.")
    if schema == 1:
        _object(spec, {"schema_version", "mode", "project_root", "python"}, "schema 1")
        if packaged:
            raise RuntimeDescriptorError("Packaged requiere el descriptor versión 2.")
        root = _resolve_path("." if spec["project_root"] == "" else spec["project_root"], base, "project_root", packaged=False, directory=True)
        python = _resolve_path(spec["python"], base, "python", packaged=False, directory=False)
        _anchor(root, "core/desktop.py", base, False)
        return RuntimeContext(1, mode, canonical, base, root, python, root / "face")

    _object(spec, {"schema_version", "mode", "iris"}, "schema 2", {"hermes", "install"})
    max_relative_path = _install_limits(spec["install"], base, packaged) if "install" in spec else None
    iris = _object(spec["iris"], {"root", "python", "assets", "revision"}, "iris")
    root = _resolve_path(iris["root"], base, "iris.root", packaged=packaged, directory=True)
    python = _resolve_path(iris["python"], base, "iris.python", packaged=packaged, directory=False)
    assets = _resolve_path(iris["assets"], base, "iris.assets", packaged=packaged, directory=True)
    iris_revision = _revision(iris["revision"], "iris.revision")
    _anchor(root, "core/desktop.py", base, packaged)
    hermes = None
    if "hermes" in spec:
        hs = _object(spec["hermes"], {"root", "python", "cli", "version", "revision", "metadata"}, "hermes")
        hroot = _resolve_path(hs["root"], base, "hermes.root", packaged=packaged, directory=True)
        hpython = _resolve_path(hs["python"], base, "hermes.python", packaged=packaged, directory=False)
        hcli = _resolve_path(hs["cli"], base, "hermes.cli", packaged=packaged, directory=False)
        metadata = _resolve_path(hs["metadata"], base, "hermes.metadata", packaged=packaged, directory=False)
        revision = _revision(hs["revision"], "hermes.revision")
        version = _text(hs["version"], "hermes.version", maximum=128)
        if not version.strip():
            raise RuntimeDescriptorError("La versión de Hermes no puede estar vacía.")
        _anchor(hroot, "hermes_cli/__init__.py", base, packaged)
        _contained(metadata, hroot, "hermes.metadata")
        pin = _object(_read_document(metadata), {"schema_version", "version", "revision"}, "hermes.metadata")
        if type(pin["schema_version"]) is not int or pin["schema_version"] != 1 or pin["version"] != version or pin["revision"] != revision:
            raise RuntimeDescriptorError("La revisión de Hermes no coincide con el runtime declarado.")
        hermes = BundledHermes(hroot, hpython, hcli, version, revision, metadata)
    if packaged and hermes is None:
        raise RuntimeDescriptorError("Falta el runtime Hermes incluido.")
    return RuntimeContext(2, mode, canonical, base, root, python, assets, iris_revision, hermes,
                          max_relative_path)


def get_runtime() -> RuntimeContext:
    """No descriptor means source development; a supplied invalid one is fatal."""
    if DESCRIPTOR_ENV in os.environ:
        return load_runtime_descriptor(os.environ[DESCRIPTOR_ENV])
    root = Path(__file__).resolve().parent.parent
    return RuntimeContext(1, "development", None, root, root, Path(sys.executable), root / "face")


def runtime_capability_allowed(name: str) -> bool:
    if not isinstance(name, str) or name not in _CAPABILITIES:
        raise RuntimeDescriptorError("Operación de runtime desconocida.")
    runtime = get_runtime()
    if name == "bundled_update":
        # A08.8: the joint Iris + Hermes channel replaces the external updaters.
        return runtime.packaged
    return name in _PROFILE_CAPABILITIES or (name in _EXTERNAL_CAPABILITIES and not runtime.packaged)


def require_runtime_capability(name: str) -> None:
    if not runtime_capability_allowed(name):
        raise RuntimePolicyError("Esta operación no está disponible en el runtime incluido de Iris.")


def runtime_public_info() -> dict[str, Any]:
    from core.version import __version__

    runtime = get_runtime()
    return {
        "schema_version": runtime.schema_version, "mode": runtime.mode,
        "iris_version": __version__, "iris_revision": runtime.iris_revision,
        "hermes_version": runtime.hermes.version if runtime.hermes else "",
        "hermes_revision": runtime.hermes.revision if runtime.hermes else "",
        "capabilities": {name: runtime_capability_allowed(name) for name in sorted(_CAPABILITIES)},
    }


def runtime_service_identity(profile_path: str | Path) -> dict[str, Any]:
    """Authenticated probes compare origins without publishing filesystem paths."""
    from core.workspace_sync import get_hermes_home

    runtime = get_runtime()
    profile = str(Path(profile_path).resolve(strict=False))
    profile_id = hashlib.sha256(os.path.normcase(profile).encode("utf-8")).hexdigest()
    hermes_profile = str(get_hermes_home().resolve(strict=False))
    hermes_profile_id = hashlib.sha256(os.path.normcase(hermes_profile).encode("utf-8")).hexdigest()
    origin = {
        "base": os.path.normcase(str(runtime.base)),
        "iris_root": os.path.normcase(str(runtime.iris_root)),
        "iris_python": os.path.normcase(str(runtime.iris_python)),
        "assets": os.path.normcase(str(runtime.assets_root)),
        "iris_revision": runtime.iris_revision, "mode": runtime.mode,
        "hermes_root": os.path.normcase(str(runtime.hermes.root)) if runtime.hermes else "",
        "hermes_python": os.path.normcase(str(runtime.hermes.python)) if runtime.hermes else "",
        "hermes_cli": os.path.normcase(str(runtime.hermes.cli)) if runtime.hermes else "",
        "hermes_revision": runtime.hermes.revision if runtime.hermes else "",
    }
    runtime_id = hashlib.sha256(json.dumps(origin, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "schema_version": 1, "mode": runtime.mode, "runtime_id": runtime_id,
        "profile_id": profile_id, "hermes_profile_id": hermes_profile_id,
        "iris_revision": runtime.iris_revision,
        "hermes_revision": runtime.hermes.revision if runtime.hermes else "",
    }


PYCACHE_BASE_MARKER = "runtime-base.txt"
_PYCACHE_KEY = re.compile(r"[0-9a-f]{12}\Z")
_MAX_MARKER_BYTES = 8192


def _reparse_point(path: Path) -> bool:
    info = os.lstat(path)
    return os.path.islink(path) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _plain(path: Path, kind: int) -> bool:
    """An existing folder or file (``stat.S_IFDIR``/``S_IFREG``) that is no link or junction."""
    try:
        info = os.lstat(path)
        return stat.S_IFMT(info.st_mode) == kind and not _reparse_point(path)
    except OSError:
        return False


def _cache_key(base: str | Path) -> str:
    return hashlib.sha256(os.path.normcase(str(base)).encode("utf-8")).hexdigest()[:12]


def _cache_folder(home: Path, names: tuple[str, ...], *, create: bool) -> Path | None:
    """``home/<names...>`` once every folder below the profile is plain and inside it.

    Checked one folder at a time, before anything is created, read, written or
    deleted there: a link or junction anywhere on the way (``cache``,
    ``pycache`` or a version's folder) could lead outside the profile, and a
    cleanup through it would delete foreign files (Codex qa10 review, R1).
    """
    try:
        real_home = home.resolve(strict=True)
    except OSError:
        return None
    current = home
    for name in names:
        current = current / name
        if create and not os.path.lexists(current):
            try:
                os.mkdir(current)  # one level at a time: never parents=True through a link
            except FileExistsError:
                pass
            except OSError:
                return None
        if not _plain(current, stat.S_IFDIR):
            return None
        try:
            if current.resolve(strict=True) != real_home.joinpath(*current.relative_to(home).parts):
                return None
        except OSError:
            return None
    return current


def _read_marker(folder: Path) -> str:
    marker = folder / PYCACHE_BASE_MARKER
    if not _plain(marker, stat.S_IFREG):
        return ""
    try:
        with marker.open("rb") as handle:
            raw = handle.read(_MAX_MARKER_BYTES + 1)
        return "" if len(raw) > _MAX_MARKER_BYTES else raw.decode("utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def runtime_pycache_path() -> Path | None:
    """Where a packaged version caches bytecode: the profile, never the installation.

    Without a cache every Hermes command recompiled its imports (``hermes
    status`` took 12-27 s instead of 6 s), so commands ran past their timeouts
    on slower PCs (qa9). Python stores each file under its absolute source path,
    so every version gets its own folder, named after its installation folder.
    """
    runtime = get_runtime()
    if not runtime.packaged:
        return None
    from core.config import get_iris_home

    return get_iris_home() / "cache" / "pycache" / _cache_key(runtime.base)


def runtime_pycache_dir() -> Path | None:
    """The prepared cache folder, or None when it cannot be used safely.

    Like the extras' caches (A08.5), a folder that is a link or resolves
    outside the profile is refused before anything is created: processes then
    run without a cache (-B). The whole chain and the marker are checked again
    on every call, never remembered: a folder that was fine once can be replaced
    by a link before the next process starts (Codex qa10 closure, C1).
    """
    path = runtime_pycache_path()
    if path is None:
        return None
    ready = _cache_folder(path.parents[2], ("cache", "pycache", path.name), create=True)
    if ready is None:
        return None
    base = str(get_runtime().base)
    marker = ready / PYCACHE_BASE_MARKER
    if not os.path.lexists(marker):
        try:
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(base.encode("utf-8"))
        except FileExistsError:
            pass  # another process wrote it first: read it like any other marker
        except OSError:
            return None
    # Not this version's marker, a linked marker or a folder that changed: no cache.
    return ready if _read_marker(ready) == base else None


def runtime_bytecode_flags() -> list[str]:
    """Interpreter flags for a long-lived packaged process: cache outside the installation."""
    cache = runtime_pycache_dir()
    return ["-X", f"pycache_prefix={cache}"] if cache is not None else ["-B"]


def prune_runtime_pycache() -> list[str]:
    """Remove the caches of versions whose folder no longer exists (removed or uninstalled).

    Only a folder this code created is removed: a cache key name, no link on the
    way from the profile, a plain marker naming a missing version folder, and a
    key that is that folder's own. Anything else is kept.
    """
    import shutil

    current = runtime_pycache_path()
    if current is None:
        return []
    root = _cache_folder(current.parents[2], ("cache", "pycache"), create=False)
    if root is None:
        return []
    removed = []
    try:
        names = sorted(entry.name for entry in os.scandir(root))
    except OSError:
        return []
    for name in names:
        if name == current.name or not _PYCACHE_KEY.fullmatch(name):
            continue
        folder = _cache_folder(current.parents[2], ("cache", "pycache", name), create=False)
        if folder is None:
            continue
        base = _read_marker(folder)
        if not base or not Path(base).is_absolute() or _cache_key(base) != name:
            continue
        try:
            if os.path.lexists(base):
                continue
            # shutil.rmtree unlinks a junction it finds inside instead of entering it.
            shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            continue
        removed.append(name)
    return removed


def runtime_subprocess_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    runtime = get_runtime()
    env = dict(os.environ if source is None else source)
    for key in list(env):
        if key.upper() in PYTHON_ENV_KEYS:
            env.pop(key, None)
    if runtime.descriptor_path:
        env[DESCRIPTOR_ENV] = str(runtime.descriptor_path)
    else:
        env.pop(DESCRIPTOR_ENV, None)
    if runtime.packaged:
        env["PYTHONNOUSERSITE"] = "1"
        # Hermes starts helpers with a bare ``sys.executable`` (shutdown watcher,
        # cron worker...): flags are not inherited, the environment is. Their
        # bytecode must never land inside the installation, which would then
        # fail its own inventory (A08.7): it goes to the profile's cache, or
        # nowhere when that cache is unavailable.
        for key in list(env):
            if key.upper() in {"HERMES_REPO", "HERMES_AGENT_ROOT", "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"}:
                env.pop(key, None)
        cache = runtime_pycache_dir()
        if cache is not None:
            env["PYTHONPYCACHEPREFIX"] = str(cache)
        else:
            env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env
