"""A08.4 private-runtime inventory; deliberately separate from wheel policy.

This verifies integrity against an independently approved source pin. It is not
an authenticity/signature mechanism, which belongs to the Iris release channel.
No runtime, profile, interpreter or executable is launched by this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
from pathlib import Path
from typing import Any

if __package__:
    from .distribution_assets import DistributionError, member_path
    from .runtime_dependency_overrides import reviewed_dependency_overrides, verify_dependency_overrides
else:
    from distribution_assets import DistributionError, member_path
    from runtime_dependency_overrides import reviewed_dependency_overrides, verify_dependency_overrides

INVENTORY_NAME = "runtime-inventory.json"
PIN_NAME = "runtime-pin.json"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|CLOCK\$|COM[1-9¹²³]|LPT[1-9¹²³])\Z", re.I)
_MAX_JSON = 32 * 1024 * 1024
_PRIVATE_PARTS = {
    ".git",
    ".venv",
    ".hermes",
    ".iris",
    "__pycache__",
    "node_modules",
    "profiles",
    "profile",
    "credentials",
    "secrets",
    "logs",
    "artifacts",
    ".cache",
}
_PRIVATE_NAMES = {
    "pyvenv.cfg",
    "auth.json",
    "tokens.json",
    "oauth_tokens.json",
    "token.json",
    "config.private.json",
    ".python_history",
    "direct_url.json",
}
_PRIVATE_SUFFIXES = {
    ".key",
    ".p12",
    ".pfx",
    ".vrm",
    ".blend",
    ".blend1",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".pyc",
    ".pyo",
}
# Public wheel payloads whose names resemble private content. Exact Hermes runtime paths
# only; each exemption lifts a single name rule and every other rule still applies.
_HERMES_SITE = "runtimes/hermes/lib/site-packages/"
_PUBLIC_LOG_MODELS = _HERMES_SITE + "botocore/data/logs"  # AWS CloudWatch Logs API model
_PUBLIC_CLIENT_SECRET_MODULES = frozenset({
    _HERMES_SITE + "openai/types/realtime/client_secret_create_params.py",
    _HERMES_SITE + "openai/types/realtime/client_secret_create_response.py",
})
_PUBLIC_CERTIFICATES = frozenset({_HERMES_SITE + "botocore/cacert.pem"})
# IRIS' own VRM: the factory asset pinned in scripts/release_assets.json (`avatar`), as
# the IRIS wheel installs it. Every other model, the person's avatars, stays private.
_PUBLIC_FACTORY_AVATAR = "runtimes/iris/lib/site-packages/face/animations/iris.vrm"
_PE_SUFFIXES = {".exe", ".dll", ".pyd"}
_ROOTS = {"runtimes", "hermes", "tools", "licenses", "locks"}
_COMPONENTS = {
    "iris-runtime",
    "hermes-runtime",
    "hermes-source",
    "audio",
    "shell",
    "licenses",
    "locks",
    "pin",
}


def runtime_path(name: str) -> str:
    """Accept only unambiguous relative names, including on Windows hosts."""
    path = member_path(name)
    if (
        any(c in name for c in '<>"|?*%')
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in name)
        or any(_DEVICE.fullmatch(part.split(".")[0].rstrip(" ")) for part in path.parts)
    ):
        raise DistributionError(f"Non-portable runtime path: {name!r}")
    return str(path)


def _component(name: str) -> str:
    parts = runtime_path(name).split("/")
    if name == PIN_NAME:
        return "pin"
    if len(parts) > 2 and parts[:2] == ["runtimes", "iris"]:
        return "iris-runtime"
    if len(parts) > 2 and parts[:2] == ["runtimes", "hermes"]:
        return "hermes-runtime"
    if len(parts) > 1 and parts[0] == "hermes":
        return "hermes-source"
    if len(parts) > 3 and parts[:3] == ["tools", "audio", "bin"]:
        return "audio"
    if len(parts) > 2 and parts[:2] == ["tools", "shell"]:
        return "shell"
    if len(parts) > 1 and parts[0] in {"licenses", "locks"}:
        return parts[0]
    raise DistributionError(f"Runtime file outside approved layout: {name}")


def _private_path(name: str) -> None:
    path = Path(runtime_path(name))
    parts = tuple(part.casefold() for part in path.parts)
    filename = parts[-1]
    lowered = name.casefold()
    log_model = lowered == _PUBLIC_LOG_MODELS or lowered.startswith(_PUBLIC_LOG_MODELS + "/")
    private_parts = [part for part in parts if part in _PRIVATE_PARTS]
    if log_model:
        private_parts.remove("logs")
    if (
        private_parts
        or filename in _PRIVATE_NAMES
        or (
            filename == ".env"
            or filename.startswith(".env.")
            or (filename.startswith("client_secret_")
                and lowered not in _PUBLIC_CLIENT_SECRET_MODULES)
            or (
                path.suffix.casefold() == ".json"
                and filename.startswith(("credentials", "secrets"))
            )
            or (path.suffix.casefold() in _PRIVATE_SUFFIXES and lowered != _PUBLIC_FACTORY_AVATAR)
        )
    ):
        raise DistributionError(f"Private or developer runtime content: {name}")
    # Certifi's public root certificates are necessary HTTPS trust data, not a
    # developer private certificate. No other PEM is admitted by this policy.
    if (path.suffix.casefold() == ".pem" and not lowered.endswith("/certifi/cacert.pem")
            and lowered not in _PUBLIC_CERTIFICATES):
        raise DistributionError(f"Private or unapproved runtime certificate: {name}")


def _link_check(path: Path, info: os.stat_result) -> None:
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise DistributionError(f"Linked runtime input: {path}")
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise DistributionError(f"Hardlinked runtime input: {path}")


def _root(root: Path) -> Path:
    root = Path(root).absolute()
    try:
        for current in (*reversed(root.parents), root):
            info = current.lstat()
            _link_check(current, info)
            if not stat.S_ISDIR(info.st_mode):
                raise DistributionError(f"Runtime root is not a directory: {current}")
    except OSError as exc:
        raise DistributionError(f"Missing runtime directory: {root}") from exc
    return root


def _regular(root: Path, name: str) -> tuple[Path, os.stat_result]:
    runtime_path(name)
    current = _root(root)
    try:
        for index, part in enumerate(name.split("/")):
            current /= part
            info = current.lstat()
            _link_check(current, info)
            expected = stat.S_ISREG if index == len(name.split("/")) - 1 else stat.S_ISDIR
            if not expected(info.st_mode):
                raise DistributionError(f"Non-regular runtime input: {name}")
    except OSError as exc:
        raise DistributionError(f"Missing runtime input: {name}") from exc
    return current, info


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _open_regular(root: Path, name: str):
    path, before = _regular(root, name)
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        handle = os.fdopen(os.open(path, flags), "rb")
        try:
            opened = os.fstat(handle.fileno())
            _link_check(path, opened)
            if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
                raise DistributionError(f"Runtime input changed during read: {name}")
        except BaseException:
            handle.close()
            raise
        return path, before, handle
    except OSError as exc:
        raise DistributionError(f"Cannot read runtime input: {name}") from exc


def _finish_read(path: Path, before: os.stat_result, handle, name: str) -> None:
    after = path.lstat()
    _link_check(path, after)
    if _identity(before) != _identity(after) or _identity(before) != _identity(
        os.fstat(handle.fileno())
    ):
        raise DistributionError(f"Runtime input changed during read: {name}")


def _pe_machine(handle, size: int, name: str) -> None:
    handle.seek(0)
    header = handle.read(64)
    if len(header) != 64 or header[:2] != b"MZ":
        raise DistributionError(f"Invalid PE runtime binary: {name}")
    offset = struct.unpack_from("<I", header, 60)[0]
    if offset < 64 or offset > min(size - 24, 1024 * 1024):
        raise DistributionError(f"Invalid PE header offset: {name}")
    handle.seek(offset)
    pe = handle.read(24)
    if len(pe) != 24 or pe[:4] != b"PE\0\0" or struct.unpack_from("<H", pe, 4)[0] != 0x8664:
        raise DistributionError(f"Runtime PE architecture must be x86_64: {name}")


def _digest(root: Path, name: str) -> tuple[int, str]:
    path, before, handle = _open_regular(root, name)
    with handle:
        digest = hashlib.sha256()
        total = 0
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            total += len(block)
        if Path(name).suffix.casefold() in _PE_SUFFIXES:
            _pe_machine(handle, total, name)
        _finish_read(path, before, handle, name)
    return total, digest.hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DistributionError(f"Duplicate runtime JSON field: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise DistributionError(f"Non-finite runtime JSON value: {value}")


def read_json(path: Path, *, maximum: int = _MAX_JSON) -> dict:
    """Read a bounded, regular JSON contract without following linked inputs."""
    path = Path(path).absolute()
    _, before, handle = _open_regular(path.parent, path.name)
    try:
        with handle:
            raw = handle.read(maximum + 1)
            if len(raw) > maximum:
                raise DistributionError(f"Runtime JSON exceeds size limit: {path.name}")
            _finish_read(path, before, handle, path.name)
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_invalid_constant
        )
        if not isinstance(value, dict):
            raise DistributionError("Runtime JSON must be an object")
        return value
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, DistributionError):
            raise
        raise DistributionError(f"Invalid runtime JSON: {path.name}") from exc


def validate_pin(pin: dict) -> None:
    if not isinstance(pin, dict) or type(pin.get("schema")) is not int or pin["schema"] != 1:
        raise DistributionError("Unknown runtime source pin schema")
    if pin.get("platform") != "windows" or pin.get("arch") != "x86_64":
        raise DistributionError("Unsupported runtime platform or architecture")
    for component in ("python", "iris", "hermes", "audio", "locks"):
        if not isinstance(pin.get(component), dict) or not pin[component]:
            raise DistributionError(f"Missing runtime pin component: {component}")
    # Optional: Hermes' own CPython (0.21.6 needs 3.14 while Iris stays on 3.11).
    pythons = ("python", "hermes_python") if "hermes_python" in pin else ("python",)
    if "hermes_python" in pin and (not isinstance(pin["hermes_python"], dict) or not pin["hermes_python"]):
        raise DistributionError("Missing runtime pin component: hermes_python")
    for component in (*pythons, "iris", "hermes", "audio"):
        version = pin[component].get("version")
        if not isinstance(version, str) or not re.fullmatch(
            r"[0-9]+(?:\.[0-9]+)+(?:[-+][\w.-]+)?", version
        ):
            raise DistributionError(f"Unpinned runtime version: {component}")
    if not isinstance(pin["hermes"].get("revision"), str) or not _REVISION.fullmatch(
        pin["hermes"]["revision"]
    ):
        raise DistributionError("Invalid Hermes runtime revision")
    for component, keys in (
        *((python, ("sha256",)) for python in pythons),
        ("audio", ("sha256",)),
        ("hermes", ("pyproject_sha256", "lock_sha256")),
        ("iris", ("pyproject_sha256", "uv_lock_sha256")),
    ):
        for key in keys:
            if not isinstance(pin[component].get(key), str) or not _SHA256.fullmatch(
                pin[component][key]
            ):
                raise DistributionError(f"Invalid pinned hash: {component}.{key}")
    for component in (*pythons, "audio"):
        url = pin[component].get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise DistributionError(f"Missing HTTPS source provenance: {component}")
    if "shell" in pin:
        shell = pin["shell"]
        if (not isinstance(shell, dict) or shell.get("producer") != "scripts/build_shell_runtime.py"
                or any(not isinstance(shell.get(key), str) or not _SHA256.fullmatch(shell[key])
                       for key in ("sha256", "build_sources_sha256", "source_sha256"))
                or shell.get("filename") != "iris-private-shell.zip"
                or shell.get("source_filename") != "iris-private-shell-sources.zip"):
            raise DistributionError("Invalid private shell source pin")
    for component in ("iris", "hermes"):
        lock = pin["locks"].get(component)
        if (
            not isinstance(lock, dict)
            or set(lock) != {"path", "sha256"}
            or (
                lock.get("path") != f"locks/{component}.txt"
                or not isinstance(lock.get("sha256"), str)
                or not _SHA256.fullmatch(lock["sha256"])
            )
        ):
            raise DistributionError(f"Invalid independent runtime lock pin: {component}")

    try:
        reviewed_dependency_overrides(pin)
    except ValueError as exc:
        raise DistributionError(str(exc)) from exc


def _pinned_sources(pin: dict) -> dict[str, str]:
    return {
        "locks/iris-pyproject.toml": pin["iris"]["pyproject_sha256"],
        "locks/iris-uv.lock": pin["iris"]["uv_lock_sha256"],
        "hermes/pyproject.toml": pin["hermes"]["pyproject_sha256"],
        "hermes/uv.lock": pin["hermes"]["lock_sha256"],
        **{pin["locks"][name]["path"]: pin["locks"][name]["sha256"] for name in ("iris", "hermes")},
    }


def _verify_pinned_sources(root: Path, pin: dict) -> None:
    for name, expected in _pinned_sources(pin).items():
        _, digest = _digest(root, name)
        if digest != expected:
            raise DistributionError(f"Runtime source or lock differs from approved pin: {name}")

    try:
        verify_dependency_overrides(
            pin, (root / pin["locks"]["hermes"]["path"]).read_text("utf-8"), root / "hermes",
        )
    except ValueError as exc:
        raise DistributionError(str(exc)) from exc


def _names(root: Path, staged: frozenset[str] = frozenset()) -> set[str]:
    """``staged`` names top-level entries another manifest verifies (A08.7)."""
    root = _root(root)
    names: set[str] = set()
    aliases: set[str] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        for path in current.iterdir():
            name = path.relative_to(root).as_posix()
            if current == root and name in staged:
                continue
            runtime_path(name)
            if name.casefold() in aliases:
                raise DistributionError(f"Duplicate or ambiguous runtime path: {name}")
            aliases.add(name.casefold())
            info = path.lstat()
            _link_check(path, info)
            if name == INVENTORY_NAME:
                if not stat.S_ISREG(info.st_mode):
                    raise DistributionError("Runtime inventory must be a regular file")
                continue
            _private_path(name)
            if stat.S_ISDIR(info.st_mode):
                parts = name.split("/")
                if (
                    parts[0] not in _ROOTS
                    or (
                        parts[0] == "runtimes"
                        and len(parts) > 1
                        and parts[1] not in {"iris", "hermes"}
                    )
                    or (parts[0] == "tools" and parts[1:2] not in ([], ["audio"], ["shell"]))
                    or (parts[:2] == ["tools", "audio"] and len(parts) > 2 and parts[2] != "bin")
                ):
                    raise DistributionError(f"Runtime directory outside approved layout: {name}")
                stack.append(path)
            elif stat.S_ISREG(info.st_mode):
                _component(name)
                names.add(name)
            else:
                raise DistributionError(f"Non-regular runtime input: {name}")
    return names


def validate_inventory(document: dict) -> None:
    fields = {
        "schema",
        "component",
        "platform",
        "architecture",
        "hash_method",
        "pin",
        "executables",
        "licenses",
        "files",
    }
    if (
        not isinstance(document, dict)
        or set(document) != fields
        or (
            type(document.get("schema")) is not int
            or document["schema"] != 1
            or document.get("component") != "iris-private-runtime"
            or document.get("platform") != "windows"
            or document.get("architecture") != "x86_64"
            or document.get("hash_method") != "sha256"
        )
    ):
        raise DistributionError("Unknown runtime inventory schema, platform or hash method")
    validate_pin(document["pin"])
    items = document["files"]
    if not isinstance(items, list) or not items:
        raise DistributionError("Empty runtime inventory")
    names, aliases = set(), set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256", "component"}:
            raise DistributionError("Invalid runtime inventory item")
        name = runtime_path(item["path"])
        _private_path(name)
        if (
            not isinstance(item["component"], str)
            or item["component"] not in _COMPONENTS
            or item["component"] != _component(name)
        ):
            raise DistributionError(f"Incorrect runtime inventory component: {name}")
        if name.casefold() in aliases:
            raise DistributionError(f"Duplicate or ambiguous runtime inventory path: {name}")
        aliases.add(name.casefold())
        names.add(name)
        if (
            type(item["size"]) is not int
            or item["size"] < 0
            or not isinstance(item["sha256"], str)
            or not _SHA256.fullmatch(item["sha256"])
        ):
            raise DistributionError(f"Invalid runtime hash or size: {name}")
    if [item["path"] for item in items] != sorted(names) or PIN_NAME not in names:
        raise DistributionError("Runtime inventory must be sorted and include its source pin")
    actual_hashes = {item["path"]: item["sha256"] for item in items}
    for name, digest in _pinned_sources(document["pin"]).items():
        if actual_hashes.get(name) != digest:
            raise DistributionError(
                f"Runtime source or lock inventory differs from approved pin: {name}"
            )
    executables = document["executables"]
    expected_executables = sorted(name for name in names if Path(name).suffix.casefold() == ".exe")
    if not isinstance(executables, list) or executables != expected_executables or not executables:
        raise DistributionError("Runtime executables must match the explicit inventory exactly")
    for required in ("runtimes/iris/python.exe", "runtimes/hermes/python.exe"):
        if required not in executables:
            raise DistributionError(f"Missing private runtime interpreter: {required}")
    licenses = document["licenses"]
    if not isinstance(licenses, list) or not licenses:
        raise DistributionError("Missing runtime license inventory")
    for item in licenses:
        if (
            not isinstance(item, dict)
            or set(item)
            != {
                "component",
                "package",
                "version",
                "license",
                "files",
                "source",
            }
            or any(
                not isinstance(item.get(key), str) or not item[key].strip()
                for key in (
                    "component",
                    "package",
                    "version",
                    "license",
                    "source",
                )
            )
        ):
            raise DistributionError("Invalid runtime license record")
        files = item["files"]
        if (
            not isinstance(files, list)
            or not files
            or any(not isinstance(name, str) or name not in names for name in files)
        ):
            raise DistributionError("Runtime license files are not inventoried")


def build_inventory(
    root: Path,
    pin: dict,
    licenses: list[dict],
    allowed_executables: list[str],
) -> dict:
    """Inventory a prepared clean stage without approving unknown executables."""
    validate_pin(pin)
    root = _root(root)
    if read_json(root / PIN_NAME, maximum=64 * 1024) != pin:
        raise DistributionError("Prepared runtime source pin differs from trusted pin")
    _verify_pinned_sources(root, pin)
    names = _names(root)
    document: dict[str, Any] = {
        "schema": 1,
        "component": "iris-private-runtime",
        "platform": "windows",
        "architecture": "x86_64",
        "hash_method": "sha256",
        "pin": pin,
        "executables": sorted(allowed_executables),
        "licenses": licenses,
        "files": [],
    }
    for name in sorted(names):
        size, digest = _digest(root, name)
        document["files"].append(
            {
                "path": name,
                "size": size,
                "sha256": digest,
                "component": _component(name),
            }
        )
    validate_inventory(document)
    return document


def verify_runtime(
    root: Path,
    inventory_path: Path | None = None,
    expected_pin: dict | None = None,
    *,
    staged: frozenset[str] = frozenset(),
) -> dict:
    """Check exact clean stage against independent approved sources and hashes.

    ``staged`` lists top-level names that an A08.7 staging adds next to the
    runtime (launcher, descriptor, its own manifest); that manifest verifies them.
    """
    root = _root(root)
    if expected_pin is None:
        expected_pin = read_json(
            Path(__file__).with_name("runtime_sources.json"), maximum=64 * 1024
        )
    validate_pin(expected_pin)
    document = read_json(inventory_path or root / INVENTORY_NAME)
    validate_inventory(document)
    if (
        document["pin"] != expected_pin
        or read_json(
            root / PIN_NAME,
            maximum=64 * 1024,
        )
        != expected_pin
    ):
        raise DistributionError("Runtime source pin differs from approved sources")
    expected_names = {item["path"] for item in document["files"]}
    names = _names(root, staged)
    if names != expected_names:
        raise DistributionError(
            f"Runtime inventory mismatch: missing={sorted(expected_names - names)}, "
            f"unexpected={sorted(names - expected_names)}"
        )
    for item in document["files"]:
        size, digest = _digest(root, item["path"])
        if size != item["size"] or digest != item["sha256"]:
            raise DistributionError(f"Incorrect runtime hash or size: {item['path']}")
    return document
