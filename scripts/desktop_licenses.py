"""Notices for the native Windows apps (Iris.exe, the Companion) and their installers.

``cargo metadata`` (``--locked --offline``, Windows x64 target) gives the crates
each executable links. Build-only and proc-macro crates run at compile time and
do not end up in the executable, so they are not listed. Each crate's own
license files are copied from the local registry (A08.7).

A crate that ships none, or a binary vendored inside a crate, needs a reviewed
record in ``scripts/desktop_license_sources.json`` (A08.9): exact name and
version, the license applied, where the text comes from, and the SHA-256 of the
text and of the vendored binary. Without one it is reported as pending instead
of guessed. Nothing is downloaded and no executable is run except cargo itself.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

TARGET = "x86_64-pc-windows-msvc"
REVIEWED_SOURCES = "scripts/desktop_license_sources.json"
NOTICE_ROOT = "scripts/desktop_notices"
_LICENSE_FILE = re.compile(r"^(licen[cs]e|copying|notice|unlicense|copyright)([-._].*)?$", re.I)
_SAFE = re.compile(r"[^A-Za-z0-9._+-]")
_IDENTITY = re.compile(r"[A-Za-z0-9_.+-]+==[A-Za-z0-9_.+-]+")
_SHA = re.compile(r"[0-9a-f]{64}")
_MAX_LICENSE_BYTES = 512 * 1024
# Code a crate carries in binary form; its upstream notice needs its own review.
VENDORED = {
    "webview2-com-sys": (
        "Enlaza WebView2LoaderStatic.lib, del WebView2 SDK de Microsoft, incluido en el crate. "
        "Su aviso de licencia no viene en el crate y debe revisarse antes de publicar (A08.9)."
    ),
}
# What each native executable links and embeds from this repository.
COMPONENTS = {
    "desktop": {
        "inventory": "iris-desktop", "prefix": "desktop/licenses", "lock": "desktop/src-tauri/Cargo.lock",
        "embedded": ({"package": "Syne", "version": "", "license": "OFL-1.1",
                      "source": "desktop/ui/fonts/syne-latin-700-normal.woff2",
                      "notice": "desktop/third-party/Syne-OFL-1.1.txt"},),
        "requirements": True,
    },
    "companion": {
        "inventory": "iris-companion", "prefix": "companion/licenses", "lock": "companion/src-tauri/Cargo.lock",
        "embedded": (), "requirements": False,
    },
}
PREFIX = COMPONENTS["desktop"]["prefix"]
EMBEDDED = COMPONENTS["desktop"]["embedded"]
INSTALLER_PREFIX = "installer/licenses"
_EMPTY = {"texts": {}, "crates": {}, "installer": {}, "requirements": {}, "_bytes": {}, "_sha256": None}


class DesktopLicenseError(ValueError):
    """The crate list or a license file cannot be vouched for."""


def cargo_metadata(manifest: Path, cargo: str | Path = "cargo") -> dict[str, Any]:
    result = subprocess.run(
        [str(cargo), "metadata", "--format-version", "1", "--locked", "--offline",
         "--filter-platform", TARGET, "--manifest-path", str(manifest)],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if result.returncode != 0:
        raise DesktopLicenseError(f"cargo metadata failed: {result.stderr.strip()[-600:]}")
    return json.loads(result.stdout)


def _proc_macro_only(package: dict) -> bool:
    kinds = [kind for target in package["targets"] for kind in target["kind"]]
    return "proc-macro" in kinds and not {"lib", "rlib", "staticlib", "cdylib", "dylib"} & set(kinds)


def linked_packages(metadata: dict) -> list[dict]:
    """Crates reachable from the executable through normal (linked) dependencies."""
    packages = {package["id"]: package for package in metadata["packages"]}
    nodes = {node["id"]: node for node in metadata["resolve"]["nodes"]}
    root = metadata["resolve"]["root"]
    seen, stack = set(), [root]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for dependency in nodes[current]["deps"]:
            if not any(kind["kind"] is None for kind in dependency["dep_kinds"]):
                continue  # build-dependencies and dev-dependencies are not linked
            if _proc_macro_only(packages[dependency["pkg"]]):
                continue
            stack.append(dependency["pkg"])
    seen.discard(root)
    return sorted((packages[item] for item in seen), key=lambda p: (p["name"], p["version"]))


def _license_files(package: dict) -> list[Path]:
    directory = Path(package["manifest_path"]).parent
    found = sorted(path for path in directory.iterdir()
                   if path.is_file() and not path.is_symlink() and _LICENSE_FILE.match(path.name))
    declared = package.get("license_file")
    if not found and declared:
        candidate = (directory / declared).resolve()
        if candidate.is_file() and candidate.is_relative_to(directory.resolve()):
            found = [candidate]
    return found


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _relative(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or value.startswith("/"):
        raise DesktopLicenseError(f"Invalid reviewed notice path: {value!r}")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise DesktopLicenseError(f"Invalid reviewed notice path: {value!r}")
    return path


def tree_digest(folder: Path) -> tuple[str, int]:
    """SHA-256 of a folder: one ``<sha256>  <relative path>`` line per file, sorted by path.

    Identifies a whole toolchain (compiler, stubs, plugins, includes), not just
    the license text it ships.
    """
    folder = Path(folder)
    files = sorted((path for path in folder.rglob("*") if path.is_file()),
                   key=lambda path: path.relative_to(folder).as_posix())
    lines = "".join(f"{_sha256(path.read_bytes())}  {path.relative_to(folder).as_posix()}\n" for path in files)
    return _sha256(lines.encode("utf-8")), len(files)


def _reviewed_entry(identity: str, item: Any, texts: dict, *, exact: bool) -> None:
    if exact and not _IDENTITY.fullmatch(identity):
        raise DesktopLicenseError(f"Reviewed notices need an exact name==version: {identity!r}")
    if not isinstance(item, dict) or not isinstance(item.get("license"), str) or not item["license"].strip():
        raise DesktopLicenseError(f"Reviewed notice without a license: {identity}")
    if not isinstance(item.get("provenance"), str) or not item["provenance"].strip():
        raise DesktopLicenseError(f"Reviewed notice without provenance: {identity}")
    source = urlsplit(item.get("source") or "")
    if source.scheme != "https" or not source.hostname:
        raise DesktopLicenseError(f"Reviewed notice needs an HTTPS source: {identity}")
    files = item.get("files")
    if not isinstance(files, dict) or not files:
        raise DesktopLicenseError(f"Reviewed notice without files: {identity}")
    for name, text in files.items():
        if not isinstance(name, str) or _SAFE.search(name) or name in {".", ".."} or text not in texts:
            raise DesktopLicenseError(f"Reviewed notice file is not a reviewed text: {identity}/{name}")
    vendored = item.get("vendored", [])
    if not isinstance(vendored, list):
        raise DesktopLicenseError(f"Reviewed vendored binaries must be a list: {identity}")
    tool = item.get("tool")
    if isinstance(tool, dict) and "tree_sha256" in tool:
        if not _SHA.fullmatch(str(tool["tree_sha256"])) or type(tool.get("files")) is not int or tool["files"] < 1:
            raise DesktopLicenseError(f"Reviewed toolchain needs a tree SHA-256 and its file count: {identity}")
        tool = None
    for binary in vendored + ([tool] if tool is not None else []):
        if not isinstance(binary, dict) or not _SHA.fullmatch(str(binary.get("sha256", ""))):
            raise DesktopLicenseError(f"Reviewed binary needs an exact SHA-256: {identity}")
        _relative(binary.get("path"))


def load_reviewed(repository: Path) -> dict[str, Any]:
    """The reviewed notices of this repository, every text checked against its SHA-256.

    A repository without the file (an older checkout, the synthetic tests) has no
    reviewed notices: crates without license files then stay pending.
    """
    source = Path(repository) / REVIEWED_SOURCES
    if not source.is_file():
        return dict(_EMPTY)
    raw = source.read_bytes()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DesktopLicenseError("desktop_license_sources.json is not valid JSON") from exc
    if not isinstance(document, dict) or document.get("schema") != 1:
        raise DesktopLicenseError("Unknown desktop_license_sources.json schema")
    texts, payloads = document.get("texts"), {}
    if not isinstance(texts, dict):
        raise DesktopLicenseError("desktop_license_sources.json needs reviewed texts")
    root = (Path(repository) / NOTICE_ROOT).resolve()
    for relative, item in texts.items():
        path = Path(repository) / NOTICE_ROOT / Path(*_relative(relative).parts)
        if (not isinstance(item, dict) or not _SHA.fullmatch(str(item.get("sha256", "")))
                or not str(item.get("origin", "")).strip()):
            raise DesktopLicenseError(f"Reviewed text needs a SHA-256 and its origin: {relative}")
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise DesktopLicenseError(f"Reviewed text missing: {relative}")
        payload = path.read_bytes()
        if len(payload) > _MAX_LICENSE_BYTES or b"\x00" in payload or _sha256(payload) != item["sha256"]:
            raise DesktopLicenseError(f"Reviewed text does not match its SHA-256: {relative}")
        payloads[relative] = payload
    sections = {}
    for section, exact in (("crates", True), ("installer", True), ("requirements", False)):
        entries = document.get(section, {})
        if not isinstance(entries, dict):
            raise DesktopLicenseError(f"Invalid {section} in desktop_license_sources.json")
        for identity, item in entries.items():
            _reviewed_entry(identity, item, texts, exact=exact)
        sections[section] = entries
    return {"texts": texts, **sections, "_bytes": payloads, "_sha256": _sha256(raw)}


def _vendored_problem(package: dict, review: dict | None) -> str:
    """Why the reviewed record does not cover what this crate vendors ('' when it does)."""
    if package["name"] not in VENDORED:
        return ""
    binaries = (review or {}).get("vendored") or []
    if not binaries:
        return VENDORED[package["name"]]
    directory = Path(package["manifest_path"]).parent
    for binary in binaries:
        path = directory / Path(*_relative(binary["path"]).parts)
        if not path.is_file() or _sha256(path.read_bytes()) != binary["sha256"]:
            return (f"{binary['path']} no coincide con el binario revisado; "
                    "su aviso debe revisarse de nuevo antes de publicar.")
    return ""


class _Writer:
    def __init__(self, output_root: Path) -> None:
        self.output_root, self.files = Path(output_root), []

    def store(self, relative: str, payload: bytes) -> str:
        if len(payload) > _MAX_LICENSE_BYTES or b"\x00" in payload:
            raise DesktopLicenseError(f"Unexpected license file: {relative}")
        target = self.output_root.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write(payload)
        self.files.append({"path": relative, "size": len(payload), "sha256": _sha256(payload)})
        return relative


def _folder(prefix: str, name: str, version: str) -> str:
    return f"{prefix}/{_SAFE.sub('_', name)}" + (f"-{_SAFE.sub('_', version)}" if version else "")


def _store_reviewed(writer: _Writer, reviewed: dict, item: dict, folder: str, skip: set[str] = frozenset()) -> list[str]:
    return [writer.store(f"{folder}/{name}", reviewed["_bytes"][text])
            for name, text in sorted(item["files"].items()) if name not in skip]


def collect(metadata: dict, output_root: Path, repository: Path, component: str = "desktop",
            reviewed: dict | None = None) -> dict[str, Any]:
    """Copy notices under ``output_root/<component>/licenses`` and return the inventory."""
    spec = COMPONENTS[component]
    prefix = spec["prefix"]
    reviewed = load_reviewed(repository) if reviewed is None else reviewed
    writer, records, pending = _Writer(output_root), [], []
    linked = linked_packages(metadata)
    for package in linked:
        name, version = package["name"], package["version"]
        folder = _folder(prefix, name, version)
        own = _license_files(package)
        copied = [writer.store(f"{folder}/{path.name}", path.read_bytes()) for path in own]
        review = reviewed["crates"].get(f"{name}=={version}")
        problem = _vendored_problem(package, review)
        record = {"component": component, "package": name, "version": version,
                  "license": package.get("license") or "", "files": copied,
                  "source": package.get("repository") or f"https://crates.io/crates/{name}/{version}"}
        if review and not problem:
            record["files"] = copied = copied + _store_reviewed(writer, reviewed, review, folder,
                                                                {path.name for path in own})
            record["license"], record["provenance"] = review["license"], review["provenance"]
        reasons = [problem] if problem else []
        if not copied:
            reasons.append("El crate no incluye archivo de licencia.")
        if reasons:
            pending.append({"package": name, "version": version, "license": package.get("license") or "",
                            "reason": " ".join(reasons)})
        records.append(record)
    for item in spec["embedded"]:
        notice = Path(repository) / item["notice"]
        relative = writer.store(f"{prefix}/{Path(item['notice']).name}", notice.read_bytes())
        records.append({"component": component, "package": item["package"], "version": item["version"],
                        "license": item["license"], "files": [relative], "source": item["source"]})
    if spec["requirements"]:
        for name, item in sorted(reviewed["requirements"].items()):
            files = _store_reviewed(writer, reviewed, item, _folder(prefix, name, ""))
            records.append({"component": component, "package": name, "version": "", "license": item["license"],
                            "files": files, "source": item["source"], "provenance": item["provenance"]})
    lock = Path(repository) / spec["lock"]
    return {
        "schema": 1, "component": spec["inventory"], "target": TARGET,
        "cargo_lock_sha256": _sha256(lock.read_bytes()),
        "reviewed_sources_sha256": reviewed["_sha256"],
        "linked_crates": len(linked),
        "licenses": records, "files": sorted(writer.files, key=lambda f: f["path"]), "pending": pending,
    }


def collect_web(output_root: Path, site_packages: str, prefix: str,
                component: str = "companion") -> tuple[list[dict], list[dict]]:
    """Notices of the web libraries the Companion bundles, copied from the version's own
    web inventory (Face and the Companion use the same pinned packages, A08.3).

    Each copied file is checked against the SHA-256 that inventory records.
    Returns the records and the files written under ``prefix/web``.
    """
    base = Path(output_root) / site_packages
    inventory = json.loads((base / "face" / "web-assets.json").read_text("utf-8"))
    hashes = {item["path"]: item["sha256"] for item in inventory["files"]}
    writer, records, stored = _Writer(output_root), [], {}
    for item in inventory["licenses"]:
        files = []
        # Every distinct text is kept (a dual license ships two); Face and the
        # Wizard carry byte-identical copies, which are stored once.
        for path in item["files"]:
            payload = (base / Path(*_relative(path).parts)).read_bytes()
            digest = _sha256(payload)
            if hashes.get(path) != digest:
                raise DesktopLicenseError(f"Web notice differs from the web inventory: {path}")
            if digest not in stored:
                name = f"{prefix}/web/{_SAFE.sub('_', PurePosixPath(path).name)}"
                if any(f["path"] == name for f in writer.files):
                    name = f"{prefix}/web/{digest[:12]}-{_SAFE.sub('_', PurePosixPath(path).name)}"
                stored[digest] = writer.store(name, payload)
            if stored[digest] not in files:
                files.append(stored[digest])
        records.append({"component": component, "package": item["package"], "version": item["version"],
                        "license": item["license"], "files": files,
                        "source": item.get("source") or f"https://www.npmjs.com/package/{item['package']}/v/{item['version']}"})
    return records, writer.files


def collect_installer(output_root: Path, repository: Path, nsis_dir: Path,
                      reviewed: dict | None = None) -> dict[str, Any]:
    """Notices of the NSIS code the Iris and Companion installers carry (A08.9).

    Every reviewed tool file must match the NSIS folder used for the build; a
    different NSIS or plugin leaves its notice pending.
    """
    reviewed = load_reviewed(repository) if reviewed is None else reviewed
    writer, records, pending = _Writer(output_root), [], []
    tree = tree_digest(nsis_dir) if Path(nsis_dir).is_dir() else None
    for identity, item in sorted(reviewed["installer"].items()):
        name, version = identity.split("==", 1)
        files, spec = [], item["tool"]
        if "tree_sha256" in spec:
            matches = tree == (spec["tree_sha256"], spec["files"])
            what = "La carpeta de NSIS (compilador, stubs y plugins)"
        else:
            tool = Path(nsis_dir) / Path(*_relative(spec["path"]).parts)
            matches = tool.is_file() and _sha256(tool.read_bytes()) == spec["sha256"]
            what = spec["path"]
        if not matches:
            pending.append({"package": name, "version": version, "license": item["license"],
                            "reason": f"{what} no coincide con la versión revisada."})
        else:
            files = _store_reviewed(writer, reviewed, item, _folder(INSTALLER_PREFIX, name, version))
        records.append({"component": "installer", "package": name, "version": version, "license": item["license"],
                        "files": files, "source": item["source"], "provenance": item["provenance"]})
    if not reviewed["installer"]:
        pending.append({"package": "NSIS", "version": "", "license": "",
                        "reason": "No hay avisos revisados para el instalador."})
    return {"schema": 1, "component": "iris-installer", "reviewed_sources_sha256": reviewed["_sha256"],
            "nsis_tree_sha256": tree[0] if tree else None, "licenses": records, "files": sorted(writer.files, key=lambda f: f["path"]), "pending": pending}
