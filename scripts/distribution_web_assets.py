"""Trusted inventory and offline package contract for local Iris web resources."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
from html.parser import HTMLParser
from pathlib import Path

if __package__:
    from .distribution_assets import DistributionError, member_path
else:
    _spec = importlib.util.spec_from_file_location(
        "iris_web_factory_contract", Path(__file__).with_name("distribution_assets.py")
    )
    _factory = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_factory)
    DistributionError = _factory.DistributionError
    member_path = _factory.member_path

WEB_MANIFEST = "face/web-assets.json"
_REQUIRED_ENTRYPOINTS = {
    "face": "face/index.html",
    "companion": "face/companion/index.html",
    "wizard": "wizard/ui/index.html",
}
_SUFFIXES = {
    ".html", ".js", ".mjs", ".css", ".json", ".png", ".svg", ".jpg",
    ".jpeg", ".webp", ".woff", ".woff2", ".wasm", ".task", ".md", ".txt",
    ".ktx2",  # pinned compressed room textures
    ".glb",  # room furniture (face/public/assets/room); clips stay in face/animations
}
_PRIVATE_PARTS = {
    ".git", ".venv", "node_modules", "__pycache__", "avatars", "profiles",
    "logs", "credentials", "secrets",
}
_BOOTSTRAP_HOSTS = re.compile(
    r"(?:https?:)?//(?:cdn\.jsdelivr\.net|fonts\.googleapis\.com|"
    r"fonts\.gstatic\.com|storage\.googleapis\.com/mediapipe-models)(?:[/\"'\s]|$)",
    re.IGNORECASE,
)


def web_path(name: str) -> str:
    path = member_path(name)
    parts = path.parts
    if not (
        (len(parts) >= 2 and parts[0] == "face")
        or (len(parts) >= 3 and parts[:2] == ("wizard", "ui"))
    ):
        raise DistributionError(f"Web resource outside installed roots: {name}")
    if name == WEB_MANIFEST or name.startswith("face/animations/"):
        raise DistributionError(f"Web inventory includes an independent contract: {name}")
    if any(part.startswith(".") or part.casefold() in _PRIVATE_PARTS for part in parts):
        raise DistributionError(f"Private web resource: {name}")
    if path.suffix.lower() not in _SUFFIXES and not re.fullmatch(
        r"(?i)(LICENSE|NOTICE)(?:[._-].*)?", path.name
    ):
        raise DistributionError(f"Unexpected web resource type: {name}")
    if path.name.lower().startswith(("credentials", "secrets", ".env")):
        raise DistributionError(f"Private web resource: {name}")
    return str(path)


def validate_web_contract(contract: dict) -> None:
    if not isinstance(contract, dict) or contract.get("schema") != 1 or (
        contract.get("component") != "iris-local-web"
    ):
        raise DistributionError("Unknown local web contract")
    inventory = contract.get("files")
    if not isinstance(inventory, list) or not inventory:
        raise DistributionError("Empty local web inventory")
    names = set()
    aliases = set()
    for item in inventory:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise DistributionError("Invalid local web inventory item")
        name = web_path(item["path"])
        if name.casefold() in aliases:
            raise DistributionError(f"Duplicate or ambiguous web resource: {name}")
        names.add(name)
        aliases.add(name.casefold())
        if not isinstance(item["sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", item["sha256"]
        ):
            raise DistributionError(f"Invalid web resource hash: {name}")
        if type(item["size"]) is not int or item["size"] < 0:
            raise DistributionError(f"Invalid web resource size: {name}")
    if [item["path"] for item in inventory] != sorted(names):
        raise DistributionError("Local web inventory must be sorted")
    entrypoints = contract.get("entrypoints")
    if not isinstance(entrypoints, dict) or any(
        entrypoints.get(key) != value for key, value in _REQUIRED_ENTRYPOINTS.items()
    ):
        raise DistributionError("Missing local web entrypoint")
    if any(value not in names for value in entrypoints.values()):
        raise DistributionError("Web entrypoint is not inventoried")
    versions = contract.get("versions")
    if not isinstance(versions, dict) or not versions or any(
        not isinstance(key, str) or not key
        or not isinstance(value, str) or not value.strip()
        or value.lower() == "latest" or any(char in value for char in "^~*")
        for key, value in versions.items()
    ):
        raise DistributionError("Local web dependency versions must be pinned")
    licenses = contract.get("licenses")
    if not isinstance(licenses, list) or not licenses:
        raise DistributionError("Missing local web licenses")
    for item in licenses:
        if not isinstance(item, dict) or any(
            not isinstance(item.get(key), str) or not item[key].strip()
            for key in ("package", "version", "license")
        ):
            raise DistributionError("Invalid local web license")
        license_files = item.get("files")
        if not isinstance(license_files, list) or not license_files or any(
            name not in names for name in license_files
        ):
            raise DistributionError("Local web license files are not inventoried")


def load_web_contract(path: Path | None = None) -> dict:
    try:
        contract = json.loads(
            (path or Path(__file__).with_name("release_web_assets.json")).read_text("utf-8")
        )
        validate_web_contract(contract)
        return contract
    except DistributionError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise DistributionError(f"Cannot load trusted local web inventory: {exc}") from exc


def web_assets_root(default: Path) -> Path:
    value = os.environ.get("IRIS_WEB_ASSETS_ROOT")
    return Path(value) if value else default


def read_web_regular(root: Path, name: str) -> bytes:
    member_path(name)
    root = root.absolute()
    paths = [*reversed(root.parents), root]
    current = root
    for part in name.split("/"):
        current = current / part
        paths.append(current)
    for candidate in paths:
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise DistributionError(f"Linked local web input: {candidate}")
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise DistributionError(f"Non-regular or hardlinked local web input: {name}")
    return current.read_bytes()


def is_web_member(name: str) -> bool:
    return (
        name.startswith("face/") and not name.startswith("face/animations/")
        and name != "face/__init__.py"
    ) or name.startswith("wizard/ui/")


def _web_input_names(root: Path) -> set[str]:
    names = set()
    for base in (root / "face", root / "wizard" / "ui"):
        if not base.is_dir():
            raise DistributionError(f"Missing local web input directory: {base}")
        stack = [base]
        while stack:
            current = stack.pop()
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise DistributionError(f"Linked local web input: {current}")
            for child in current.iterdir():
                name = child.relative_to(root).as_posix()
                if name == "face/animations" or name == "face/__init__.py":
                    continue
                info = child.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise DistributionError(f"Linked local web input: {child}")
                if stat.S_ISDIR(info.st_mode):
                    stack.append(child)
                elif stat.S_ISREG(info.st_mode):
                    names.add(name)
                else:
                    raise DistributionError(f"Non-regular local web input: {name}")
    return names


class _BootstrapParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.remote = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        target = attributes.get("src") if tag in {"script", "img", "iframe"} else None
        if tag == "link" and any(
            rel in attributes.get("rel", "").lower().split()
            for rel in ("stylesheet", "preload", "modulepreload", "preconnect")
        ):
            target = attributes.get("href")
        if target and re.match(r"(?i)(?:https?:)?//", target):
            self.remote.append(target)


def verify_local_bootstrap(name: str, payload: bytes) -> None:
    suffix = Path(name).suffix.lower()
    if suffix not in {".html", ".js", ".mjs", ".css"}:
        return
    text = payload.decode("utf-8")
    if _BOOTSTRAP_HOSTS.search(text):
        raise DistributionError(f"Remote bootstrap resource: {name}")
    if suffix == ".html":
        parser = _BootstrapParser()
        parser.feed(text)
        if parser.remote:
            raise DistributionError(f"Remote HTML bootstrap resource: {name}")
        for match in re.finditer(
            r"<script\b[^>]*type\s*=\s*[\"']importmap[\"'][^>]*>(.*?)</script>",
            text, re.IGNORECASE | re.DOTALL,
        ):
            imports = json.loads(match.group(1)).get("imports", {})
            if any(re.match(r"(?i)(?:https?:)?//", value) for value in imports.values()):
                raise DistributionError(f"Remote import map resource: {name}")
    elif suffix == ".css" and re.search(
        r"(?:url\s*\(\s*[\"']?|@import\s*[\"'])(?:https?:)?//", text, re.IGNORECASE
    ):
        raise DistributionError(f"Remote CSS bootstrap resource: {name}")
    elif suffix in {".js", ".mjs"} and re.search(
        r"(?:\bfrom\s*[\"']|\bimport\s*(?:\(\s*)?[\"'])(?:https?:)?//", text
    ):
        raise DistributionError(f"Remote module import resource: {name}")


def verify_web_assets(files: dict[str, bytes], contract: dict) -> None:
    validate_web_contract(contract)
    expected = {item["path"] for item in contract["files"]} | {WEB_MANIFEST}
    if set(files) != expected:
        raise DistributionError(
            f"Local web inventory mismatch: missing={sorted(expected - set(files))}, "
            f"unexpected={sorted(set(files) - expected)}"
        )
    manifest = json.loads(files[WEB_MANIFEST])
    if manifest != contract:
        raise DistributionError("Packaged web manifest differs from trusted inventory")
    for item in contract["files"]:
        name = item["path"]
        payload = files[name]
        if len(payload) != item["size"] or hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise DistributionError(f"Incorrect web resource hash or size: {name}")
        verify_local_bootstrap(name, payload)


def prepare_web_assets(root: Path, contract: dict | None = None) -> dict[str, bytes]:
    contract = contract if contract is not None else load_web_contract()
    validate_web_contract(contract)
    names = _web_input_names(root)
    expected = {item["path"] for item in contract["files"]} | {WEB_MANIFEST}
    if names != expected:
        raise DistributionError(
            f"Local web input inventory mismatch: missing={sorted(expected - names)}, "
            f"unexpected={sorted(names - expected)}"
        )
    result = {name: read_web_regular(root, name) for name in sorted(expected)}
    verify_web_assets(result, contract)
    return result
