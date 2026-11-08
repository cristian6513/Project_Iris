"""The shared, explicit factory-asset contract for Python and Companion builds."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import stat
from pathlib import Path, PurePosixPath


class DistributionError(ValueError):
    """An input or package does not meet the public distribution contract."""


def member_path(name: str) -> PurePosixPath:
    """Reject ambiguous archive paths; never repair an unsafe name."""
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise DistributionError(f"Ruta de distribución inválida: {name!r}")
    parts = name.split("/")
    if any(
        part in {"", ".", ".."}
        or part.endswith((" ", "."))
        or any(ord(char) < 32 for char in part)
        or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
        for part in parts
    ):
        raise DistributionError(f"Ruta de distribución inválida: {name!r}")
    return PurePosixPath(name)


def basename(name: str) -> str:
    if len(member_path(name).parts) != 1:
        raise DistributionError(f"El recurso debe ser un nombre local: {name!r}")
    return name


def load_contract(path: Path | None = None) -> dict:
    contract = json.loads(
        (path or Path(__file__).with_name("release_assets.json")).read_text("utf-8")
    )
    validate_contract(contract)
    return contract


def validate_contract(contract: dict) -> None:
    if contract.get("schema") != 1 or not contract.get("clips"):
        raise DistributionError("Contrato de recursos desconocido o vacío")
    filenames = set()
    for name, item in contract["clips"].items():
        basename(name)
        filename = basename(item["filename"])
        if filename.casefold() in filenames or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise DistributionError("Contrato de recursos duplicado o hash inválido")
        filenames.add(filename.casefold())
    if contract.get("rest_pose") not in contract["clips"]:
        raise DistributionError("Falta la pose de reposo obligatoria")
    avatar = contract.get("avatar")
    if avatar is not None:
        # IRIS' own VRM, with its own licence (not MIT): pinned like the clips.
        names = {basename(avatar.get("filename", "")).casefold(), basename(avatar.get("license", "")).casefold()}
        if (
            len(names) != 2
            or names & filenames
            or not avatar["filename"].casefold().endswith(".vrm")
            or not re.fullmatch(r"[0-9a-f]{64}", str(avatar.get("sha256", "")))
        ):
            raise DistributionError("Avatar de fábrica inválido en el contrato de recursos")


def public_files(contract: dict) -> set[str]:
    avatar = contract.get("avatar")
    return {"manifest.json", "contexts.json", "emotions.json", "README.md"} | {
        item["filename"] for item in contract["clips"].values()
    } | ({avatar["filename"], avatar["license"]} if avatar else set())


def read_regular(root: Path, filename: str) -> bytes:
    """Reject symlinks and Windows reparse points before reading build inputs."""
    basename(filename)
    for path in (*reversed(root.absolute().parents), root, root / filename):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise DistributionError(f"Enlace no permitido en recursos: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise DistributionError(f"Recurso no regular: {filename}")
    return (root / filename).read_bytes()


_POOLS = ("idle_pool", "return_idle_pool", "ambient_poses", "runtime_only", "body_only_clips")
_MAPS = ("_source_mapping", "facial_profiles")


def release_manifest(source: dict, contract: dict) -> dict:
    """Project the local manifest onto the approved release inventory."""
    validate_contract(contract)
    manifest = copy.deepcopy(source)
    clips = manifest.get("clips", {})
    for filename in clips.values():
        basename(filename)
    expected = {name: item["filename"] for name, item in contract["clips"].items()}
    if any(clips.get(name) != filename for name, filename in expected.items()):
        raise DistributionError("El manifest no contiene todos los clips de fábrica autorizados")
    manifest["clips"] = expected
    for field in _POOLS:
        manifest[field] = [name for name in manifest.get(field, []) if name in expected]
    allowed_menu = set(expected) | set(contract.get("procedural_actions", []))
    manifest["menu_poses"] = [
        name for name in manifest.get("menu_poses", []) if name in allowed_menu
    ]
    manifest["pose_map"] = {
        name: clip for name, clip in manifest.get("pose_map", {}).items() if clip in expected
    }
    for field in _MAPS:
        manifest[field] = {
            name: value for name, value in manifest.get(field, {}).items() if name in expected
        }
    manifest["activity_clips"] = {
        name: value
        for name, value in manifest.get("activity_clips", {}).items()
        if value.get("clip") in expected
    }
    rest = contract["rest_pose"]
    if rest not in manifest["idle_pool"] or rest not in manifest["return_idle_pool"]:
        raise DistributionError("El manifest debe conservar la pose de reposo en ambos pools")
    for state in ("listening", "thinking", "speaking"):
        if state not in manifest["activity_clips"]:
            raise DistributionError(f"Falta el estado de animación obligatorio: {state}")
    manifest["_packaging_notice"] = "Only explicitly approved factory animations are distributed."
    return manifest


def release_contexts(source: dict, contract: dict) -> dict:
    """Keep native movement while replacing excluded clips with playable fallbacks."""
    document = copy.deepcopy(source)
    allowed = set(contract["clips"]) | set(contract.get("procedural_actions", []))
    for mappings in document.get("contexts", {}).values():
        for item in mappings.values():
            animation = item["animation"]
            if animation not in allowed:
                fallback = contract.get("context_fallbacks", {}).get(animation)
                if fallback not in allowed:
                    raise DistributionError(f"Contexto sin fallback autorizado: {animation}")
                item["animation"] = fallback
    return document


def prepare_assets(root: Path, contract: dict | None = None) -> dict[str, bytes]:
    contract = contract if contract is not None else load_contract()
    validate_contract(contract)
    result = {}
    for item in contract["clips"].values():
        payload = read_regular(root, item["filename"])
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise DistributionError(f"Hash de animación incorrecto: {item['filename']}")
        result[item["filename"]] = payload
    avatar = contract.get("avatar")
    if avatar:
        payload = read_regular(root, avatar["filename"])
        if hashlib.sha256(payload).hexdigest() != avatar["sha256"]:
            raise DistributionError(f"Hash del avatar incorrecto: {avatar['filename']}")
        result[avatar["filename"]] = payload
        result[avatar["license"]] = read_regular(root, avatar["license"])
    for filename, project in (
        ("manifest.json", release_manifest),
        ("contexts.json", release_contexts),
    ):
        document = project(json.loads(read_regular(root, filename)), contract)
        result[filename] = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
    result["README.md"] = read_regular(root, "README.md")
    result["emotions.json"] = read_regular(root, "emotions.json")
    return result


def verify_assets(files: dict[str, bytes], contract: dict) -> None:
    validate_contract(contract)
    if set(files) != public_files(contract):
        raise DistributionError(
            f"Inventario de animaciones incompleto o inesperado: "
            f"faltan={sorted(public_files(contract) - set(files))}, "
            f"sobran={sorted(set(files) - public_files(contract))}"
        )
    for item in contract["clips"].values():
        if hashlib.sha256(files[item["filename"]]).hexdigest() != item["sha256"]:
            raise DistributionError(f"Hash de animación incorrecto: {item['filename']}")
    avatar = contract.get("avatar")
    if avatar and hashlib.sha256(files[avatar["filename"]]).hexdigest() != avatar["sha256"]:
        raise DistributionError(f"Hash del avatar incorrecto: {avatar['filename']}")
    for filename, project in (
        ("manifest.json", release_manifest),
        ("contexts.json", release_contexts),
    ):
        document = json.loads(files[filename])
        if project(document, contract) != document:
            raise DistributionError(
                f"Referencias no distribuidas o manifest sin preparar: {filename}"
            )
