"""Collect license evidence from sealed Python trees without importing packages."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import io
import ipaddress
import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

COMPONENTS = frozenset({"iris-runtime", "hermes-runtime"})
NOTICE_NAME = re.compile(
    r"^(?:licen[cs]e|copying|notice|authors|copyright)(?:[._-].*)?$|third.?party.?notices?",
    re.IGNORECASE,
)
UNKNOWN_LICENSES = frozenset({"", "unknown", "none", "n/a", "unspecified"})
DEFAULT_RUNTIME_LICENSE_SOURCES = Path(__file__).with_name("runtime_license_sources.json")


class RuntimeLicenseError(ValueError):
    """Missing or unsafe license evidence prevents a distributable inventory."""

    def __init__(self, message: str, *, missing: list[dict[str, Any]] | None = None,
                 records: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.missing = [] if missing is None else missing
        self.records = [] if records is None else records


def _relative_notice_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise RuntimeLicenseError("Reviewed notice paths must use safe relative POSIX paths.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} or ":" in part for part in value.split("/")):
        raise RuntimeLicenseError("Reviewed notice paths must use safe relative POSIX paths.")
    for part in path.parts:
        if part != part.rstrip(" .") or re.fullmatch(
                r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", part.split(".", 1)[0], re.IGNORECASE):
            raise RuntimeLicenseError("Reviewed notice paths must avoid Windows reserved names.")
    if not _notice_name(value):
        raise RuntimeLicenseError("A reviewed license source may contain only notice files.")
    return path


def _validate_provenance(item: Any) -> None:
    if not isinstance(item, dict):
        raise RuntimeLicenseError("Reviewed notices need exact-version source provenance.")
    kind = item.get("type")
    if kind == "sdist":
        url, digest = item.get("url"), item.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeLicenseError("Reviewed sdist provenance needs an exact SHA256.")
    elif kind == "git":
        url, revision = item.get("repository"), item.get("revision")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise RuntimeLicenseError("Reviewed Git provenance needs an exact revision.")
    else:
        raise RuntimeLicenseError("Unknown reviewed notice provenance type.")
    try:
        parsed = urlsplit(url) if isinstance(url, str) else None
    except ValueError as exc:
        raise RuntimeLicenseError("Reviewed provenance needs a public HTTPS source.") from exc
    if parsed is None or not _public_https(parsed) or parsed.query or parsed.fragment:
        raise RuntimeLicenseError("Reviewed provenance needs a public HTTPS source.")


def prepare_python_license_fallbacks(
    stage: str | Path, sources_file: str | Path = DEFAULT_RUNTIME_LICENSE_SOURCES,
    *, expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Copy reviewed public notices into staging; never change installed wheels."""
    manifest = Path(sources_file).absolute()
    source_root = manifest.parent.resolve(strict=True)
    manifest = _safe_path(source_root, manifest)
    raw = manifest.read_bytes()
    if len(raw) > 1024 * 1024:
        raise RuntimeLicenseError("The reviewed notice manifest exceeds its size limit.")
    if expected_manifest_sha256 is not None:
        if (not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256)
                or hashlib.sha256(raw).hexdigest() != expected_manifest_sha256):
            raise RuntimeLicenseError("Reviewed notice manifest SHA256 mismatch.")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeLicenseError("Invalid reviewed notice manifest JSON.") from exc
    if (not isinstance(document, dict) or document.get("schema_version") != 1
            or not isinstance(document.get("packages"), dict) or not document["packages"]):
        raise RuntimeLicenseError("Invalid reviewed notice manifest schema.")
    raw_stage = Path(stage).absolute()
    stage_path = raw_stage.resolve(strict=True)
    _safe_path(stage_path, raw_stage / ".iris-license-copy-check", required=False)
    planned, fallbacks, seen = [], {}, set()
    for identity, item in sorted(document["packages"].items()):
        if not isinstance(identity, str) or not re.fullmatch(r"[a-z0-9_.-]+==[A-Za-z0-9_.+!-]+", identity):
            raise RuntimeLicenseError("Reviewed notice identity needs an exact package/version.")
        name, version = identity.split("==", 1)
        if not isinstance(item, dict):
            raise RuntimeLicenseError("Invalid reviewed notice package record.")
        _validate_provenance(item.get("provenance"))
        label, url, files = item.get("license"), item.get("source"), item.get("files")
        if not isinstance(label, str) or label.strip().casefold() in UNKNOWN_LICENSES:
            raise RuntimeLicenseError("Reviewed notices need an explicit license.")
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
        except ValueError as exc:
            raise RuntimeLicenseError("Reviewed notices need a public HTTPS source.") from exc
        if parsed is None or not _public_https(parsed) or parsed.query or parsed.fragment:
            raise RuntimeLicenseError("Reviewed notices need a public HTTPS source.")
        if not isinstance(files, list) or not files:
            raise RuntimeLicenseError("Reviewed package records need notice files.")
        package_dir = f"{name}-{version}"
        output_files = []
        for record in files:
            if not isinstance(record, dict) or set(record) != {"path", "source_path", "sha256"}:
                raise RuntimeLicenseError("Reviewed notice files need path, source_path and SHA256.")
            source = _relative_notice_path(record["source_path"])
            target = _relative_notice_path(record["path"])
            if source.parts[:2] != ("runtime_notices", package_dir) or target.parts[:3] != (
                    "licenses", "python", package_dir):
                raise RuntimeLicenseError("Reviewed notices must remain inside their package notice directories.")
            key = str(target).casefold()
            if key in seen:
                raise RuntimeLicenseError("Duplicate reviewed notice destination.")
            seen.add(key)
            digest = record["sha256"]
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise RuntimeLicenseError("Reviewed notices need exact SHA256 values.")
            source_path = _safe_path(source_root, source_root.joinpath(*source.parts))
            body = source_path.read_bytes()
            if not body.strip() or len(body) > 8 * 1024 * 1024:
                raise RuntimeLicenseError("Reviewed notice is empty or exceeds its size limit.")
            if hashlib.sha256(body).hexdigest() != digest:
                raise RuntimeLicenseError(f"Reviewed notice SHA256 mismatch: {identity}.")
            destination = _safe_path(stage_path, stage_path.joinpath(*target.parts), required=False)
            if destination.exists() and destination.read_bytes() != body:
                raise RuntimeLicenseError("A reviewed notice would replace different staging content.")
            planned.append((destination, body))
            output_files.append({"path": target.as_posix(), "sha256": digest})
        fallbacks[identity] = {"license": label.strip(), "source": url, "files": output_files}
    # Validate all source hashes and destinations before writing the first file.
    for destination, body in planned:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _safe_path(stage_path, destination, required=False)
        if not destination.exists():
            with destination.open("xb") as output:
                output.write(body)
    return fallbacks


def _safe_path(stage: Path, path: Path, *, required: bool = True) -> Path:
    if not path.is_absolute():
        path = stage / path
    if not Path(os.path.abspath(path)).is_relative_to(stage):
        raise RuntimeLicenseError("License evidence is outside the declared staging root.")
    # Resolve only after checking the lexical path and every existing parent.
    for current in (path, *path.parents):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeLicenseError(f"License path traverses a symlink or junction: {current.name}.")
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise RuntimeLicenseError(f"License evidence cannot be a hardlink: {current.name}.")
    resolved = path.resolve(strict=required)
    if not resolved.is_relative_to(stage):
        raise RuntimeLicenseError("License evidence is outside the declared staging root.")
    if required and not resolved.is_file():
        raise RuntimeLicenseError(f"License evidence is not a regular file: {resolved.name}.")
    return resolved


def _notice_name(path: str) -> bool:
    # Detect malformed Windows/ADS notice spellings before the path validator
    # rejects them; otherwise they could be silently mistaken for non-notices.
    parts = PurePosixPath(path.replace("\\", "/")).parts
    return bool(parts and (
        NOTICE_NAME.search(parts[-1].split(":", 1)[0])
        or any(part.casefold() in {"licenses", "licences", "notices"} for part in parts[:-1])
    ))


def _dist_notice_files(stage: Path, dist_info: Path) -> set[Path]:
    """Walk manually so a Windows junction is rejected before its contents are read."""
    notices, pending = set(), [dist_info]
    while pending:
        directory = pending.pop()
        for candidate in directory.iterdir():
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise RuntimeLicenseError("Distribution license metadata contains a linked path.")
            if stat.S_ISDIR(info.st_mode):
                pending.append(candidate)
            elif _notice_name(candidate.relative_to(dist_info).as_posix()):
                notices.add(_safe_path(stage, candidate))
    return notices


def _record_path(site: Path, value: str) -> Path:
    # Some reviewed Windows wheels (e.g. jiter) record backslashes. Treat them
    # as separators, then apply the same drive/UNC/ADS and confinement checks.
    value = value.replace("\\", "/")
    if not value or "\x00" in value or value.startswith("/"):
        raise RuntimeLicenseError("Unsafe license path in package RECORD.")
    parts = value.split("/")
    if any(part in {"", "."} or ":" in part for part in parts):
        raise RuntimeLicenseError("Unsafe license path in package RECORD.")
    # Wheel RECORD may legitimately use ../ for files installed into a prefix
    # data directory. _safe_path confines the resolved result to this staging.
    return site.joinpath(*parts)


def _public_source(metadata, package: str, version: str) -> str:
    candidates = []
    for value in metadata.get_all("Project-URL", []):
        label, separator, url = value.partition(",")
        if separator:
            rank = {"source": 0, "source code": 0, "repository": 0, "homepage": 1}.get(label.strip().casefold(), 2)
            candidates.append((rank, url.strip()))
    candidates.append((3, str(metadata.get("Home-page", "")).strip()))
    for _, value in sorted(candidates):
        try:
            parsed = urlsplit(value)
            if not _public_https(parsed):
                continue
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        except ValueError:
            continue
    return f"https://pypi.org/project/{quote(package, safe='')}/{quote(version, safe='')}/"


def _public_https(parsed) -> bool:
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.casefold().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True  # Public DNS names are not resolved or contacted by this collector.


def _license_label(metadata) -> str | None:
    for field in ("License-Expression", "License"):
        value = str(metadata.get(field, "")).strip()
        if value.casefold() not in UNKNOWN_LICENSES:
            return value
    classifiers = [value for value in metadata.get_all("Classifier", [])
                   if value.startswith("License ::") and value != "License :: OSI Approved"
                   and not value.endswith(":: Other/Proprietary License")]
    if classifiers:
        # Preserve upstream's classifier literally instead of guessing a SPDX
        # version or 'only/or-later' distinction that it did not declare.
        return "; ".join(f"Classifier: {value}" for value in classifiers)
    return None


def _metadata_license_body(metadata) -> bool:
    value = str(metadata.get("License", ""))
    return len(value.strip()) >= 100 and any(text in value.casefold() for text in (
        "permission is hereby granted", "redistribution and use", "copyright",
        "gnu general public license", "apache license",
    ))


def _verified_fallback(
    stage: Path, package: str, version: str, fallbacks: Mapping[str, Any],
) -> tuple[str | None, str | None, set[Path]]:
    item = fallbacks.get(f"{package.casefold()}=={version}")
    if item is None:
        return None, None, set()
    if not isinstance(item, dict):
        raise RuntimeLicenseError("A license fallback must be a reviewed package/version record.")
    label, url, files = item.get("license"), item.get("source"), item.get("files")
    if not isinstance(label, str) or label.strip().casefold() in UNKNOWN_LICENSES:
        raise RuntimeLicenseError("A license fallback needs an explicit reviewed license.")
    try:
        parsed = urlsplit(url) if isinstance(url, str) else None
    except ValueError as exc:
        raise RuntimeLicenseError("A license fallback needs a public HTTPS provenance URL.") from exc
    if parsed is None or not _public_https(parsed) or parsed.query or parsed.fragment:
        raise RuntimeLicenseError("A license fallback needs a public HTTPS provenance URL.")
    if not isinstance(files, list) or not files:
        raise RuntimeLicenseError("A license fallback needs reviewed notice files.")
    notices = set()
    for record in files:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise RuntimeLicenseError("A license fallback file needs path and SHA256.")
        path, digest = record["path"], record["sha256"]
        if not isinstance(path, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeLicenseError("Invalid fallback notice path or SHA256.")
        resolved = _safe_path(stage, _record_path(stage, path))
        if hashlib.sha256(resolved.read_bytes()).hexdigest() != digest:
            raise RuntimeLicenseError(f"Reviewed license fallback hash mismatch: {package}.")
        if not resolved.read_bytes().strip():
            raise RuntimeLicenseError(f"Reviewed license fallback is empty: {package}.")
        notices.add(resolved)
    return label.strip(), url, notices


def inventory_python_licenses(
    stage: str | Path, component: str, site_path: str | Path,
    *, fallbacks: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Inventory every installed distribution's preserved notices or fail closed."""
    if component not in COMPONENTS:
        raise RuntimeLicenseError(f"Unknown Python runtime component: {component!r}.")
    raw_stage = Path(stage).absolute()
    stage_path = raw_stage.resolve(strict=True)
    _safe_path(stage_path, raw_stage / ".iris-license-root-check", required=False)
    raw_site = Path(site_path)
    site = raw_site if raw_site.is_absolute() else stage_path / raw_site
    # A directory is not a notice file, but still cannot escape through links.
    _safe_path(stage_path, site / ".iris-license-path-check", required=False)
    site = site.resolve(strict=True)
    if not site.is_dir():
        raise RuntimeLicenseError("The declared site-packages path is not a directory.")
    records, missing, seen = [], [], set()
    for dist_info in sorted(site.glob("*.dist-info")):
        metadata_path = _safe_path(stage_path, dist_info / "METADATA")
        distribution = importlib.metadata.PathDistribution(dist_info)
        metadata = distribution.metadata
        package, version = str(metadata.get("Name", "")).strip(), str(metadata.get("Version", "")).strip()
        if (not re.fullmatch(r"[A-Za-z0-9_.-]+", package)
                or not re.fullmatch(r"[A-Za-z0-9_.+!-]+", version)):
            raise RuntimeLicenseError("A staged distribution has invalid name/version metadata.")
        identity = re.sub(r"[-_.]+", "-", package).casefold()
        if identity in seen:
            raise RuntimeLicenseError(f"Duplicate staged Python distribution: {package}.")
        seen.add(identity)
        notices, record_notices = _dist_notice_files(stage_path, dist_info), set()
        record_path = dist_info / "RECORD"
        if record_path.exists():
            checked_record = _safe_path(stage_path, record_path)
            for row in csv.reader(io.StringIO(checked_record.read_text("utf-8"))):
                if not row or not _notice_name(row[0]):
                    continue
                target = _safe_path(stage_path, _record_path(site, row[0]))
                record_notices.add(target)
            notices.update(record_notices)
        reasons, declared_notices = [], set()
        for declared in metadata.get_all("License-File", []):
            if (not declared or "\\" in declared or ":" in declared
                    or PurePosixPath(declared).is_absolute()
                    or any(part in {"", ".", ".."} for part in declared.split("/"))):
                raise RuntimeLicenseError(f"Unsafe License-File metadata for {package}.")
            possible = [dist_info / declared, dist_info / "licenses" / declared]
            found = {item for item in possible if item.exists()}
            found.update(item for item in record_notices if item.name == PurePosixPath(declared).name)
            if found:
                verified = {_safe_path(stage_path, item) for item in found}
                notices.update(verified)
                declared_notices.update(verified)
            else:
                reasons.append(f"Missing declared License-File: {declared}")
        label = _license_label(metadata)
        url = _public_source(metadata, package, version)
        fallback_label, fallback_source, fallback_files = _verified_fallback(
            stage_path, package, version, {} if fallbacks is None else fallbacks)
        if fallback_files:
            notices.update(fallback_files)
            label, url = fallback_label, fallback_source
            # Explicit hashed source notices substitute missing wheel bodies;
            # their exact package/version and provenance remain reviewable.
            reasons = []
        if label is None:
            reasons.append("Missing explicit license expression, metadata or classifier")
        if _metadata_license_body(metadata):
            notices.add(metadata_path)
        body_notices = [path for path in notices if path.name.casefold().startswith(
            ("license", "licence", "copying", "notice", "copyright"))
            or "thirdparty" in path.name.replace("_", "").replace("-", "").casefold()
            or path == metadata_path or path in fallback_files or path in declared_notices]
        if not body_notices or not any(path.read_bytes().strip() for path in body_notices):
            reasons.append("No preserved license/notice body")
        if reasons:
            missing.append({"package": package, "version": version, "reasons": reasons})
        else:
            records.append({
                "component": component, "package": package, "version": version,
                "license": label, "files": sorted(path.relative_to(stage_path).as_posix() for path in notices),
                "source": url,
            })
    if not seen:
        raise RuntimeLicenseError("No installed distributions were found in the declared runtime.")
    if missing:
        raise RuntimeLicenseError("Runtime license evidence is incomplete: " + "; ".join(
            f"{item['package']}=={item['version']} ({', '.join(item['reasons'])})" for item in missing),
            missing=missing, records=records)
    return records
