"""Reject private or machine-local files from IRIS OS distributions."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

if __package__:
    from .distribution_assets import DistributionError, load_contract, member_path, verify_assets
    from .distribution_web_assets import is_web_member, load_web_contract, verify_web_assets
else:
    from distribution_assets import DistributionError, load_contract, member_path, verify_assets
    from distribution_web_assets import is_web_member, load_web_contract, verify_web_assets

PRIVATE_ANIMATION_SUFFIXES = {".bvh", ".fbx", ".glb", ".gltf", ".vrma"}
PRIVATE_MODEL_SUFFIXES = {".vrm", ".blend", ".blend1", ".ckpt", ".pth", ".pt", ".safetensors"}
PRIVATE_SECRET_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
BYTECODE_SUFFIXES = {".pyc", ".pyo"}


_normalise = member_path
_DEFAULT_WEB_CONTRACT = object()


def _factory_avatar(path, contract: dict | None) -> bool:
    """IRIS' own VRM (the contract's `avatar`), only as face/animations/<that exact name>.

    Every other model stays private: the person's avatars never ship.
    """
    avatar = (contract if contract is not None else load_contract()).get("avatar")
    return bool(avatar) and path.parts[-3:] == ("face", "animations", avatar["filename"])


def forbidden_reason(name: str, *, contract: dict | None = None) -> str | None:
    try:
        path = _normalise(name)
    except DistributionError as exc:
        return str(exc)
    parts = tuple(part.lower() for part in path.parts)
    filename = parts[-1] if parts else ""
    suffix = path.suffix.lower()

    if "__pycache__" in parts or suffix in BYTECODE_SUFFIXES:
        return "bytecode/cache de Python"
    if filename == ".env" or filename.startswith(".env."):
        return "archivo de entorno privado"
    if suffix in PRIVATE_SECRET_SUFFIXES:
        return "credencial o certificado privado"
    if suffix == ".json" and filename.startswith(("credentials", "secrets")):
        return "archivo de credenciales privado"
    if suffix in PRIVATE_MODEL_SUFFIXES and not _factory_avatar(path, contract):
        return "modelo o workspace local"
    for index, part in enumerate(parts[:-1]):
        if part == "face" and parts[index + 1] == "animations":
            if suffix in PRIVATE_ANIMATION_SUFFIXES and str(path.name) not in {
                item["filename"]
                for item in (contract if contract is not None else load_contract())[
                    "clips"
                ].values()
            }:
                return "paquete de animación privado"
    if suffix == ".exe":
        joined = "/".join(parts)
        allowed = "/dist/companion/" in f"/{joined}" or "/share/iris-os/companion/" in f"/{joined}"
        if not allowed:
            return "ejecutable fuera del canal Companion"
    return None


def _check_names(names: list[str]) -> None:
    seen = set()
    for name in names:
        canonical = str(member_path(name)).casefold()
        if canonical in seen:
            raise DistributionError(f"Miembro duplicado o ambiguo: {name}")
        seen.add(canonical)


def _zip_reader(archive: zipfile.ZipFile) -> tuple[list[str], Callable[[str], bytes]]:
    items = archive.infolist()
    _check_names(
        [item.orig_filename[:-1] if item.is_dir() else item.orig_filename for item in items]
    )
    for item in items:
        kind = stat.S_IFMT(item.external_attr >> 16)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or (
            kind == stat.S_IFDIR and not item.is_dir()
        ):
            raise DistributionError(f"Enlace o miembro no regular: {item.filename}")
        if item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise DistributionError(f"Método de compresión desconocido: {item.compress_type}")
        if item.flag_bits & 1:
            raise DistributionError(f"Miembro cifrado no permitido: {item.filename}")
    return [item.filename for item in items if not item.is_dir()], archive.read


def _tar_reader(archive: tarfile.TarFile) -> tuple[list[str], Callable[[str], bytes]]:
    items = archive.getmembers()
    _check_names([item.name.rstrip("/") if item.isdir() else item.name for item in items])
    if any(not (item.isfile() or item.isdir()) for item in items):
        raise DistributionError("Enlace o miembro no regular en sdist")
    members = {item.name: item for item in items if item.isfile()}

    def read(name: str) -> bytes:
        stream = archive.extractfile(members[name])
        if stream is None:
            raise DistributionError(f"No se pudo leer {name}")
        return stream.read()

    return list(members), read


def _verify_members(
    names: list[str], read: Callable[[str], bytes], contract: dict, *, sdist: bool,
    web_contract: dict | None | object = _DEFAULT_WEB_CONTRACT,
) -> None:
    violations = [
        f"{name}: {reason}"
        for name in names
        if (reason := forbidden_reason(name, contract=contract)) is not None
    ]
    if violations:
        raise DistributionError("Contenido privado o local detectado:\n" + "\n".join(violations))

    prefix = ""
    if sdist:
        roots = {name.split("/")[0] for name in names}
        if len(roots) != 1:
            raise DistributionError("El sdist debe tener una única raíz")
        prefix = next(iter(roots)) + "/"
    asset_prefix = prefix + "face/animations/"
    for name in names:
        if "/face/animations/" in f"/{name.casefold()}" and not name.startswith(asset_prefix):
            raise DistributionError(f"Recurso fuera de la ruta instalada: {name}")
    files = {
        name[len(asset_prefix) :]: read(name) for name in names if name.startswith(asset_prefix)
    }
    verify_assets(files, contract)

    # Only an explicit None opts fixture archives out of the web contract.
    # Production callers always load the trusted checkout inventory.
    if web_contract is _DEFAULT_WEB_CONTRACT:
        web_contract = load_web_contract()
    if web_contract is not None:
        web_files = {}
        for name in names:
            local = name[len(prefix):] if name.startswith(prefix) else name
            if (
                "/face/" in f"/{name.casefold()}"
                or "/wizard/ui/" in f"/{name.casefold()}"
            ) and not (
                local.startswith("face/") or local.startswith("wizard/ui/")
            ):
                raise DistributionError(f"Web resource outside installed path: {name}")
            if is_web_member(local):
                web_files[local] = read(name)
        verify_web_assets(web_files, web_contract)

    executables = [name for name in names if _normalise(name).suffix.lower() == ".exe"]
    if not executables:
        return
    if len(executables) != 1:
        raise DistributionError("El paquete debe contener como máximo un instalador Companion")
    manifests = [
        name
        for name in names
        if str(_normalise(name)).lower().endswith("companion/release-manifest.json")
    ]
    if len(manifests) != 1:
        raise DistributionError(
            "Un instalador Companion exige exactamente un release-manifest.json"
        )
    try:
        manifest = json.loads(read(manifests[0]))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DistributionError("El manifiesto Companion del paquete no es JSON válido") from exc
    executable = executables[0]
    if PurePosixPath(executable).name != manifest.get("filename"):
        raise DistributionError("El nombre del instalador no coincide con el manifiesto Companion")
    digest = hashlib.sha256(read(executable)).hexdigest()
    if digest != manifest.get("sha256"):
        raise DistributionError("El hash del instalador no coincide con el manifiesto Companion")


def verify_archive(
    path: Path, *, contract: dict | None = None,
    web_contract: dict | None | object = _DEFAULT_WEB_CONTRACT,
) -> Path:
    # Tests may supply a small, explicit fixture contract. CLI/release builds
    # always use the trusted inventory from this checkout, never from the archive.
    contract = contract if contract is not None else load_contract()
    try:
        if path.suffix in {".whl", ".zip"}:
            with zipfile.ZipFile(path) as archive:
                names, read = _zip_reader(archive)
                _verify_members(names, read, contract, sdist=False, web_contract=web_contract)
            return path
        if path.name.endswith(".tar.gz"):
            with tarfile.open(path, "r:gz") as archive:
                names, read = _tar_reader(archive)
                _verify_members(names, read, contract, sdist=True, web_contract=web_contract)
            return path
        raise DistributionError(f"Formato de distribución no soportado: {path.name}")
    except DistributionError:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        NotImplementedError,
        RuntimeError,
        zipfile.BadZipFile,
        tarfile.TarError,
    ) as exc:
        raise DistributionError(f"Distribución inválida: {path.name}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)
    archives = sorted(
        path
        for path in args.directory.iterdir()
        if path.name.startswith("iris_os-")
        and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    )
    if not archives:
        raise DistributionError(f"No hay distribuciones para verificar en {args.directory}")
    for archive in archives:
        verify_archive(archive)
        print(f"distribution verified: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
