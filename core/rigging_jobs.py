"""Constrain the experimental rigging API to managed inputs and job outputs.

Converters only receive a private copy with embedded resources. This is an
API boundary, not an OS sandbox for a compromised converter/local process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import stat
import struct
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core.file_lock import interprocess_file_lock

logger = logging.getLogger("IRIS.rigging_jobs")
RigMethod = Literal["auto", "anymate", "blender", "heuristic"]
RIG_METHODS = frozenset({"auto", "anymate", "blender", "heuristic"})
# Anymate currently copies the original into its GLB injector: it requires GLB.
# The Blender script has no PLY importer. Do not guess optional trimesh loaders.
RIG_FORMATS = {
    "anymate": frozenset({".glb"}),
    "blender": frozenset({".obj", ".glb", ".gltf", ".stl"}),
    "heuristic": frozenset({".obj", ".glb", ".gltf", ".stl", ".ply"}),
}
MAX_MODEL_BYTES = 100 * 1024 * 1024
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


class RiggingError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def validate_rig_request(filename: str, method: str) -> str:
    if method not in RIG_METHODS:
        raise RiggingError("Método de rigging desconocido.")
    if (
        not filename or len(filename) > 200 or filename != filename.strip()
        or filename.startswith(".") or filename.endswith(".") or ".." in filename
        or any(ord(c) < 32 or c in '/\\:<>"|?*' for c in filename)
        or filename.split(".", 1)[0].upper() in _RESERVED
    ):
        raise RiggingError("Selecciona un nombre de archivo dentro de avatars, sin rutas.")
    extension = Path(filename).suffix.lower()
    formats = set().union(*RIG_FORMATS.values()) if method == "auto" else RIG_FORMATS[method]
    if extension not in formats:
        raise RiggingError(
            "Formato no admitido por este método. Para FBX/DAE o modelos con recursos "
            "externos, exporta un GLB con todos los recursos incrustados.", 415,
        )
    return extension


def _directory(path: Path) -> Path:
    absolute = path.absolute()
    try:
        if absolute.is_symlink() or absolute.resolve(strict=True) != absolute:
            raise RiggingError("El directorio administrado no puede ser un enlace.", 409)
        if not absolute.is_dir():
            raise RiggingError("El directorio de avatares no está disponible.", 404)
    except (OSError, RuntimeError) as exc:
        raise RiggingError("El directorio de avatares no está disponible.", 404) from exc
    return absolute


def _regular_file(path: Path, parent: Path) -> os.stat_result:
    try:
        if path.parent != parent or path.is_symlink() or path.resolve(strict=True) != path:
            raise RiggingError(
                "El archivo contiene un enlace o está fuera del área permitida.", 409,
            )
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RiggingError("Se requiere un archivo regular sin enlaces.", 409)
        return info
    except FileNotFoundError as exc:
        raise RiggingError("El modelo no existe en avatars.", 404) from exc
    except (OSError, RuntimeError) as exc:
        raise RiggingError("No se pudo verificar el archivo del modelo.", 409) from exc


def _read_file(path: Path, parent: Path, limit: int = MAX_MODEL_BYTES) -> bytes:
    expected = _regular_file(path, parent)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        actual = os.fstat(handle.fileno())
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise RiggingError("El modelo cambió mientras se preparaba la operación.", 409)
        raw = handle.read(limit + 1)
    if not raw:
        raise RiggingError("El archivo del modelo está vacío.")
    if len(raw) > limit:
        raise RiggingError("El modelo supera el tamaño permitido.", 413)
    return raw


def validate_embedded_resources(raw: bytes, extension: str) -> None:
    """Reject sidecar access instead of handing arbitrary paths to a parser."""
    try:
        if extension in {".gltf", ".glb"}:
            if extension == ".glb":
                if len(raw) < 20:
                    raise ValueError("Incomplete GLB")
                magic, version, total, size, kind = struct.unpack_from("<4sIIII", raw)
                if (magic != b"glTF" or version != 2 or total != len(raw)
                        or kind != 0x4E4F534A or size % 4 or size > len(raw) - 20):
                    raise ValueError("Invalid GLB")
                document = json.loads(raw[20:20 + size])
            else:
                document = json.loads(raw)
            if not isinstance(document, dict) or document.get("asset", {}).get("version") != "2.0":
                raise ValueError("Invalid glTF")
            pending = [document]
            while pending:
                value = pending.pop()
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key.casefold() == "uri" and (
                            not isinstance(child, str) or not child.startswith("data:")
                            or "," not in child
                        ):
                            raise RiggingError(
                                "El modelo usa recursos externos. Exporta un GLB/GLTF "
                                "con buffers y texturas incrustados.", 415,
                            )
                        pending.append(child)
                elif isinstance(value, list):
                    pending.extend(value)
        elif extension == ".obj":
            text = raw.decode("utf-8-sig")
            for line in text.replace("\\\r\n", "").replace("\\\n", "").splitlines():
                words = line.split()
                if words and words[0].casefold() in {
                    "mtllib", "maplib", "call", "shadow_obj", "trace_obj",
                }:
                    raise RiggingError(
                        "El OBJ depende de archivos laterales. "
                        "Exporta un GLB con recursos incrustados.",
                        415,
                    )
        elif extension == ".ply":
            # PLY geometry is self-contained, except the conventional
            # TextureFile comment understood by mesh loaders.
            header, separator, _ = raw.partition(b"end_header")
            if not separator or not header.startswith(b"ply"):
                raise ValueError("Invalid PLY")
            lowered = header.lower().replace(b"_", b"")
            if b"texturefile" in lowered:
                raise RiggingError(
                    "El PLY referencia una textura externa. "
                    "Exporta un GLB con recursos incrustados.",
                    415,
                )
    except (ValueError, TypeError, AttributeError, RecursionError) as exc:
        raise RiggingError("El contenedor del modelo no es válido.") from exc


def _new_result_name() -> str:
    return f"rigged_{uuid.uuid4().hex}"


@dataclass
class RiggingJob:
    avatars: Path
    jobs: Path
    directory: Path
    source: Path
    output: Path
    destination: Path

    def cleanup(self) -> None:
        # Verify the exact absolute target before recursive deletion. rmtree
        # removes descendant links/junctions without following their targets.
        if not self.directory.exists():
            return
        if (
            self.directory.parent != self.jobs or self.jobs.resolve() != self.jobs
            or self.directory.is_symlink() or self.directory.resolve() != self.directory
            or not self.directory.name.startswith("job-")
        ):
            logger.error("Refusing cleanup of a redirected rigging job")
            return
        shutil.rmtree(self.directory)

    def publish(self, result: dict) -> dict:
        if result.get("output_path") != str(self.output):
            raise RiggingError("El conversor devolvió una salida no autorizada.", 500)
        model = _read_file(self.output, self.directory)
        validate_embedded_resources(model, ".glb")
        bundle = self.directory / "publish"
        bundle.mkdir(mode=0o700)
        (bundle / "model.glb").write_bytes(model)
        has_metadata = bool(result.get("metadata_path"))
        if has_metadata:
            expected = self.output.with_suffix(".bones.json")
            if result["metadata_path"] != str(expected):
                raise RiggingError("El conversor devolvió metadatos fuera del trabajo.", 500)
            metadata = _read_file(expected, self.directory, 4 * 1024 * 1024)
            try:
                if not isinstance(json.loads(metadata), dict):
                    raise ValueError("Invalid metadata")
            except (ValueError, RecursionError) as exc:
                raise RiggingError("Los metadatos del conversor no son válidos.", 500) from exc
            (bundle / "model.bones.json").write_bytes(metadata)
        # Only verified outputs enter the public bundle, not the input/script
        # or any extra files a converter may have left in its private workdir.
        lock = self.jobs / "publication.lock"
        if os.path.lexists(lock):
            _regular_file(lock, self.jobs)
        with interprocess_file_lock(lock):
            _directory(self.avatars)
            if self.destination.parent != self.avatars or os.path.lexists(self.destination):
                raise RiggingError("El destino ya existe; no se sobrescribió ningún archivo.", 409)
            # Both directories share the profile filesystem. All outputs
            # become visible together; cooperating writers use the same lock.
            os.rename(bundle, self.destination)
        response = {key: result[key] for key in (
            "method", "bones_found", "bone_count", "bones", "bone_names", "bone_mapping",
        ) if key in result}
        response.update(status="success", output_path=str(self.destination / "model.glb"))
        if has_metadata:
            response["metadata_path"] = str(self.destination / "model.bones.json")
        return response


def _prepare_job(avatars: Path, filename: str, method: str) -> RiggingJob:
    extension = validate_rig_request(filename, method)
    avatars = _directory(avatars)
    raw = _read_file(avatars / filename, avatars)
    validate_embedded_resources(raw, extension)
    destination = avatars / _new_result_name()
    if destination.parent != avatars or os.path.lexists(destination):
        raise RiggingError("El destino ya existe o no es válido.", 409)
    jobs = avatars.parent / ".rig-jobs"
    jobs.mkdir(mode=0o700, exist_ok=True)
    jobs = _directory(jobs)
    directory = Path(tempfile.mkdtemp(prefix="job-", dir=jobs))
    job = RiggingJob(avatars, jobs, directory, directory / f"input{extension}",
                     directory / "output.glb", destination)
    try:
        with job.source.open("xb") as handle:
            handle.write(raw)
        return job
    except BaseException:
        job.cleanup()
        raise


async def rig_managed_model(avatars: Path, filename: str, method: str) -> dict:
    """Wait for owned workers before cleaning on error or cancellation."""
    cancelled = threading.Event()

    async def run():
        job = None
        try:
            job = await asyncio.to_thread(_prepare_job, avatars, filename, method)
            if cancelled.is_set():
                raise asyncio.CancelledError
            from core.rigger import AutoRigger

            rigger = AutoRigger()
            await rigger.initialize()
            result = await rigger.auto_rig(str(job.source), str(job.output), method)
            if cancelled.is_set():
                raise asyncio.CancelledError
            if not isinstance(result, dict) or result.get("status") != "success":
                message = result.get("message") if isinstance(result, dict) else None
                raise RiggingError(message or "El conversor no completó el rigging.", 500)
            return await asyncio.to_thread(job.publish, result)
        except OSError as exc:
            logger.warning("Managed rigging I/O failed: %s", exc)
            raise RiggingError("No se pudo completar el trabajo de rigging.", 503) from exc
        finally:
            if job is not None:
                try:
                    await asyncio.to_thread(job.cleanup)
                except OSError:
                    logger.exception("Private rigging job cleanup deferred")

    task = asyncio.create_task(run())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancelled.set()
        # The executor/subprocess cannot be cancelled by cancelling its await.
        # Let it stop using its files before cleanup; never remove other jobs.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise
