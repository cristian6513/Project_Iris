"""Preserve reviewed native notices, verifying the actual staged DLL bytes.

This is evidence collection, not a replacement for upstream distribution terms.
Partial ZIP reads deliberately do not claim verification of an entire archive.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

if __package__:
    from .runtime_licenses import (
        COMPONENTS,
        UNKNOWN_LICENSES,
        RuntimeLicenseError,
        _public_https,
        _relative_notice_path,
        _safe_path,
    )
else:
    from runtime_licenses import (
        COMPONENTS,
        UNKNOWN_LICENSES,
        RuntimeLicenseError,
        _public_https,
        _relative_notice_path,
        _safe_path,
    )

DEFAULT_NATIVE_LICENSE_SOURCES = Path(__file__).with_name("runtime_native_license_sources.json")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SHA512 = re.compile(r"[0-9a-f]{128}")
_REVISION = re.compile(r"[0-9a-f]{40}")
# Static components are proven by distinct printable byte strings found in the staged DLL.
_MARKER = re.compile(r"[ -~]{8,200}")
# Binaries built here from pinned public sources by a reviewed producer script.
LOCAL_PRODUCERS = frozenset({"scripts/build_pyav_wheel.py"})


def _sha(value: Any) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise RuntimeLicenseError("Native license evidence needs an exact SHA256.")
    return value


def _url(value: Any) -> str:
    try:
        parsed = urlsplit(value) if isinstance(value, str) else None
    except ValueError as exc:
        raise RuntimeLicenseError("Native evidence needs a public HTTPS source.") from exc
    if parsed is None or not _public_https(parsed) or parsed.query or parsed.fragment:
        raise RuntimeLicenseError("Native evidence needs a public HTTPS source.")
    return value


def _relative_binary_path(value: Any, component: str) -> PurePosixPath:
    # Reuse the same Windows-safe grammar without requiring a notice basename.
    if not isinstance(value, str):
        raise RuntimeLicenseError("Native evidence needs a relative binary path.")
    _relative_notice_path(value + "/NOTICE")
    path = PurePosixPath(value)
    runtime = {"iris-runtime": "iris", "hermes-runtime": "hermes"}[component]
    if (path.parts[:4] != ("runtimes", runtime, "Lib", "site-packages")
            or path.suffix.casefold() not in {".dll", ".pyd"}):
        raise RuntimeLicenseError("Native evidence must identify a DLL inside its declared runtime.")
    return path


def _provenance(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise RuntimeLicenseError("Native licenses need reviewed source provenance.")
    for item in value:
        if not isinstance(item, dict):
            raise RuntimeLicenseError("Invalid native source provenance.")
        kind = item.get("type")
        _url(item.get("url"))
        if kind == "wheel-range":
            _sha(item.get("archive_sha256_declared"))
            if (type(item.get("archive_size")) is not int or item["archive_size"] <= 0
                    or item.get("archive_sha256_verified") is not False
                    or item.get("zip_entry_crc_verified") is not True):
                raise RuntimeLicenseError("A ranged wheel cannot claim whole-archive verification.")
        elif kind == "archive":
            _sha(item.get("archive_sha256"))
            if item.get("archive_sha256_verified") is not True:
                raise RuntimeLicenseError("Native archive provenance must be fully verified.")
        elif kind == "nuget-range":
            if (urlsplit(item["url"]).hostname != "api.nuget.org"
                    or type(item.get("archive_size")) is not int or item["archive_size"] <= 0
                    or not isinstance(item.get("archive_sha512_declared"), str)
                    or not _SHA512.fullmatch(item["archive_sha512_declared"])
                    or item.get("archive_sha256_verified") is not False
                    or item.get("zip_entry_crc_verified") is not True):
                raise RuntimeLicenseError(
                    "A ranged NuGet read cannot claim whole-archive verification.")
            _url(item.get("publisher_hash_source"))
        elif kind == "git-revision":
            revision, tag = item.get("revision"), item.get("tag")
            if (not isinstance(revision, str) or not _REVISION.fullmatch(revision)
                    or not isinstance(tag, str)
                    or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", tag)
                    or urlsplit(item["url"]).hostname != "github.com"):
                raise RuntimeLicenseError(
                    "Git provenance needs a GitHub repository, exact revision and tag.")
        elif kind == "local-build":
            artifact = item.get("artifact")
            _sha(item.get("source_sha256"))
            _sha(item.get("artifact_sha256"))
            if (item.get("source_sha256_verified") is not True
                    or item.get("producer") not in LOCAL_PRODUCERS
                    or not isinstance(artifact, str)
                    or not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", artifact)):
                raise RuntimeLicenseError(
                    "A local build needs a verified source, reviewed producer and exact artifact.")
        elif kind == "documentation":
            _sha(item.get("document_sha256"))
            if item.get("document_sha256_verified") is not True:
                raise RuntimeLicenseError("Native documentation provenance must be fully verified.")
        else:
            raise RuntimeLicenseError("Unknown native source provenance method.")
        if "build_recipe" in item:
            recipe = item["build_recipe"]
            if not isinstance(recipe, dict):
                raise RuntimeLicenseError("Invalid native build recipe provenance.")
            _url(recipe.get("url"))
            _sha(recipe.get("sha256"))
            revision = recipe.get("revision")
            if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
                raise RuntimeLicenseError("Native build recipe needs an exact Git revision.")
    return value


def _evidence(item: dict, provenance: list[dict], *, binary: bool, static: bool = False) -> None:
    evidence = item.get("evidence")
    if not isinstance(evidence, dict):
        raise RuntimeLicenseError("Native files must refer to their reviewed source evidence.")
    markers = evidence.get("markers")
    if static:
        if (not isinstance(markers, list) or not 1 <= len(markers) <= 8
                or len(set(markers)) != len(markers)
                or any(not isinstance(marker, str) or not _MARKER.fullmatch(marker)
                       for marker in markers)):
            raise RuntimeLicenseError(
                "Static native components need distinct printable byte markers.")
    elif markers is not None:
        raise RuntimeLicenseError("Only static native components are proven by byte markers.")
    index = evidence.get("provenance_index")
    if type(index) is not int or not 0 <= index < len(provenance):
        raise RuntimeLicenseError("Invalid native source evidence reference.")
    selected = provenance[index]
    if selected["type"] == "git-revision" and binary and not static:
        raise RuntimeLicenseError("A source revision cannot prove exact native binary bytes.")
    if selected["type"] in {"archive", "wheel-range", "nuget-range", "git-revision", "local-build"}:
        member = evidence.get("member")
        if not isinstance(member, str) or not member or "\\" in member or "\x00" in member:
            raise RuntimeLicenseError("Native archive evidence needs a safe member path.")
        path = PurePosixPath(member)
        if path.is_absolute() or any(part in {"", ".", ".."} or ":" in part for part in member.split("/")):
            raise RuntimeLicenseError("Native archive evidence needs a safe member path.")
    elif binary:
        raise RuntimeLicenseError("A documentation source cannot prove native binary provenance.")
    elif evidence.get("representation") == "html":
        if item["sha256"] != selected["document_sha256"]:
            raise RuntimeLicenseError("Preserved native documentation hash differs from its source.")
    elif evidence.get("representation") == "text":
        if evidence.get("extraction") != "htmlparser-bd-article-v1":
            raise RuntimeLicenseError("Unknown native documentation extraction method.")
    else:
        raise RuntimeLicenseError("Invalid native documentation representation.")


def _digest(path: Path) -> tuple[int, str]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


def prepare_native_license_records(
    stage: str | Path, sources_file: str | Path = DEFAULT_NATIVE_LICENSE_SOURCES,
    *, expected_manifest_sha256: str | None = None,
) -> list[dict[str, Any]]:
    """Verify DLL hashes and copy preserved notices without executing packages.

    The caller must pin this reviewed manifest independently. All package inputs
    are validated before the first write; a failed proof creates no partial set
    of notice files. Installed packages and their original metadata are untouched.
    """
    manifest = Path(sources_file).absolute()
    source_root = manifest.parent.resolve(strict=True)
    raw = _safe_path(source_root, manifest).read_bytes()
    if len(raw) > 1024 * 1024:
        raise RuntimeLicenseError("Native license manifest exceeds its size limit.")
    if (expected_manifest_sha256 is not None
            and hashlib.sha256(raw).hexdigest() != _sha(expected_manifest_sha256)):
        raise RuntimeLicenseError("Native license manifest SHA256 mismatch.")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeLicenseError("Invalid native license manifest JSON.") from exc
    if (not isinstance(document, dict) or set(document) != {"schema_version", "packages"}
            or type(document.get("schema_version")) is not int or document["schema_version"] != 1
            or not isinstance(document.get("packages"), dict) or not document["packages"]):
        raise RuntimeLicenseError("Unknown native license manifest schema.")
    raw_stage = Path(stage).absolute()
    stage_path = raw_stage.resolve(strict=True)
    _safe_path(stage_path, raw_stage / ".iris-native-license-check", required=False)
    planned, records, destinations, binaries_seen = [], [], set(), {}
    for identity, item in sorted(document["packages"].items()):
        if not isinstance(identity, str) or not re.fullmatch(r"[a-z0-9_.-]+==[A-Za-z0-9_.+!-]+", identity):
            raise RuntimeLicenseError("Native license identity needs an exact package/version.")
        name, version = identity.split("==", 1)
        if (not isinstance(item, dict) or set(item) != {
                "component", "package", "version", "license", "source", "provenance", "binaries", "files"}
                or not isinstance(item["component"], str) or item["component"] not in COMPONENTS
                or item["version"] != version
                or not isinstance(item["package"], str) or item["package"].casefold() != name):
            raise RuntimeLicenseError("Invalid native license package record.")
        component = item["component"]
        license_label = item["license"]
        if not isinstance(license_label, str) or license_label.strip().casefold() in UNKNOWN_LICENSES:
            raise RuntimeLicenseError("Native notices need an explicit license label.")
        source = _url(item["source"])
        provenance = _provenance(item["provenance"])
        if not isinstance(item["binaries"], list) or not item["binaries"]:
            raise RuntimeLicenseError("Native license records need actual staged binary evidence.")
        for binary in item["binaries"]:
            if (not isinstance(binary, dict)
                    or set(binary) - {"linkage"} != {"path", "size", "sha256", "evidence"}):
                raise RuntimeLicenseError("Native binary evidence needs path, size, SHA256 and source evidence.")
            if "linkage" in binary and binary["linkage"] != "static":
                raise RuntimeLicenseError("Unknown native binary linkage.")
            static = binary.get("linkage") == "static"
            _sha(binary["sha256"])
            if type(binary["size"]) is not int or binary["size"] <= 0:
                raise RuntimeLicenseError("Native binary evidence needs an exact positive size.")
            _evidence(binary, provenance, binary=True, static=static)
            relative = _relative_binary_path(binary["path"], component)
            key = relative.as_posix().casefold()
            markers = set(binary["evidence"].get("markers") or ())
            # One exact-file attribution per DLL. Several statically linked components may
            # share it, each with its own markers, so one proof is never credited twice.
            claims = binaries_seen.setdefault(key, [])
            if any(other == identity for other, _, _ in claims) or (
                    not static and any(not other_static for _, other_static, _ in claims)):
                raise RuntimeLicenseError("Duplicate native binary evidence.")
            if static and any(markers & other for _, other_static, other in claims if other_static):
                raise RuntimeLicenseError(
                    "Static native markers are already attributed to another component.")
            claims.append((identity, static, markers))
            path = _safe_path(stage_path, stage_path.joinpath(*relative.parts))
            if _digest(path) != (binary["size"], binary["sha256"]):
                raise RuntimeLicenseError(f"Native binary SHA256 or size mismatch: {identity}.")
            if static:
                data = path.read_bytes()
                if any(marker.encode("ascii") not in data for marker in markers):
                    raise RuntimeLicenseError(
                        f"Static component markers not found for {identity}.")
        if not isinstance(item["files"], list) or not item["files"]:
            raise RuntimeLicenseError("Native license records need preserved notice files.")
        output_files, documentation_originals = [], set()
        package_dir = f"{name}-{version}"
        for notice in item["files"]:
            if not isinstance(notice, dict) or set(notice) != {"path", "source_path", "sha256", "evidence"}:
                raise RuntimeLicenseError("Native notices need source_path, path, SHA256 and source evidence.")
            _sha(notice["sha256"])
            _evidence(notice, provenance, binary=False)
            if notice["evidence"].get("representation") == "html":
                documentation_originals.add(notice["evidence"]["provenance_index"])
            source_relative = _relative_notice_path(notice["source_path"])
            target = _relative_notice_path(notice["path"])
            if source_relative.parts[:2] != ("runtime_native_notices", package_dir) or target.parts[:3] != (
                    "licenses", "native", package_dir):
                raise RuntimeLicenseError("Native notices must remain inside their package notice directories.")
            key = target.as_posix().casefold()
            if key in destinations:
                raise RuntimeLicenseError("Duplicate native notice destination.")
            destinations.add(key)
            source_path = _safe_path(source_root, source_root.joinpath(*source_relative.parts))
            body = source_path.read_bytes()
            if not body.strip() or len(body) > 8 * 1024 * 1024:
                raise RuntimeLicenseError("Native notice is empty or exceeds its size limit.")
            if hashlib.sha256(body).hexdigest() != notice["sha256"]:
                raise RuntimeLicenseError(f"Native notice SHA256 mismatch: {identity}.")
            destination = _safe_path(stage_path, stage_path.joinpath(*target.parts), required=False)
            if destination.exists() and destination.read_bytes() != body:
                raise RuntimeLicenseError("Native notice would replace different staging content.")
            planned.append((destination, body))
            output_files.append(target.as_posix())
        for index, proof in enumerate(provenance):
            if proof["type"] == "documentation" and index not in documentation_originals:
                raise RuntimeLicenseError("Native documentation must preserve its verified original HTML.")
        records.append({"component": component, "package": item["package"], "version": version,
                        "license": license_label.strip(), "files": sorted(output_files), "source": source})
    for destination, body in planned:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _safe_path(stage_path, destination, required=False)
        if not destination.exists():
            with destination.open("xb") as target:
                target.write(body)
    return records
