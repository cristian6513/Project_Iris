"""Bounded, non-rendering validation for user supplied avatars and clips.

This module is stdlib-only: the animation catalogue also runs in resource-only
installations. Validation never fetches dependencies or rewrites animation data.
It is an ingestion policy, not a complete glTF/VRM conformance certificate.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import math
import os
import re
import stat
import struct
import tempfile
import zipfile
import zlib
from pathlib import Path
from typing import BinaryIO

AVATAR_MAX_BYTES = 100 * 1024 * 1024
PMX_ARCHIVE_MAX_BYTES = 64 * 1024 * 1024
ANIMATION_MAX_BYTES = 64 * 1024 * 1024
JSON_MAX_BYTES = 16 * 1024 * 1024
MAX_VALUES = 500_000
MAX_DEPTH = 64
MAX_DECODED_BYTES = 128 * 1024 * 1024


class AssetUploadError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def upload_filename(value: str) -> str:
    """A filename, never a path, on either Windows or POSIX."""
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10)
    }
    if (
        not value
        or len(value) > 200
        or value.startswith(".")
        or value.endswith((".", " "))
        or value.split(".", 1)[0].upper() in reserved
        or any(ord(c) < 32 or c in '/\\:<>"|?*' for c in value)
    ):
        raise AssetUploadError("Nombre de archivo inválido; no se permiten rutas.")
    return value


def read_bounded(source: BinaryIO, maximum: int) -> bytes:
    # CPython BytesIO.getvalue shares its immutable buffer, avoiding a second
    # full-size bytearray -> bytes copy for a 100 MiB avatar.
    data = io.BytesIO()
    while True:
        chunk = source.read(min(1024 * 1024, maximum + 1 - data.tell()))
        if not chunk:
            break
        data.write(chunk)
        if data.tell() > maximum:
            raise AssetUploadError("El archivo supera el límite permitido.", 413)
    if not data.tell():
        raise AssetUploadError("Archivo vacío.")
    return data.getvalue()


def atomic_avatar_write(root: Path, filename: str, content: bytes) -> Path:
    """Never follow an existing avatar, directory alias or hard link."""
    root = root.absolute()
    target = root / upload_filename(filename)
    if root.resolve() != root or target.resolve() != target or target.is_symlink():
        raise AssetUploadError("El destino del avatar no puede ser un enlace.", 409)
    if target.exists():
        info = target.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise AssetUploadError("Destino de avatar no permitido.", 409)
    root.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=root, prefix=".avatar-upload-", suffix=".tmp", delete=False
        ) as handle:
            temp = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        return target
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _require(condition: bool, message: str = "Estructura de archivo inválida.") -> None:
    if not condition:
        raise AssetUploadError(message)


def _integer(value, minimum=0, maximum=MAX_DECODED_BYTES):
    _require(type(value) is int and minimum <= value <= maximum)
    return value


def _entry(items, index):
    return items[_integer(index, 0, len(items) - 1)]


def _objects(document, key):
    items = document.get(key, [])
    _require(isinstance(items, list) and all(isinstance(item, dict) for item in items))
    return items


def _json(raw: bytes) -> dict:
    _require(len(raw) <= JSON_MAX_BYTES, "El JSON del modelo supera el presupuesto de validación.")
    # Bound depth and container entries BEFORE json.loads can allocate them.
    depth = entries = 0
    quoted = escaped = False
    for c in raw:
        if quoted:
            if escaped:
                escaped = False
            elif c == 92:
                escaped = True
            elif c == 34:
                quoted = False
        elif c == 34:
            quoted = True
        elif c in (91, 123):
            depth += 1
            entries += 1
        elif c in (93, 125):
            depth -= 1
        elif c in (44, 58):
            entries += 1
        _require(
            depth <= MAX_DEPTH and entries <= MAX_VALUES,
            "El JSON supera el presupuesto de complejidad.",
        )

    def pairs(values):
        result = {}
        for key, value in values:
            _require(key not in result, "El JSON contiene claves duplicadas.")
            result[key] = value
        return result

    doc = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs)
    _require(isinstance(doc, dict))
    pending = [doc]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, float):
            _require(math.isfinite(value))
    return doc


def _gltf_container(content: bytes, extension: str):
    binary = None
    if extension == "gltf":
        return _json(content), binary
    _require(len(content) >= 20)
    magic, version, length = struct.unpack_from("<4sII", content)
    _require(magic == b"glTF" and version == 2 and length == len(content))
    offset, chunks = 12, []
    while offset < length:
        _require(offset + 8 <= length and len(chunks) < 2)
        size, kind = struct.unpack_from("<II", content, offset)
        offset += 8
        _require(size % 4 == 0 and size > 0 and offset + size <= length)
        chunks.append((kind, memoryview(content)[offset : offset + size]))
        offset += size
    _require(chunks[0][0] == 0x4E4F534A)
    if len(chunks) == 2:
        _require(chunks[1][0] == 0x004E4942)
        binary = chunks[1][1]
    return _json(bytes(chunks[0][1])), binary


def _data_uri(uri: str) -> bytes:
    _require(
        isinstance(uri, str) and uri.startswith("data:"),
        "El archivo depende de recursos externos. Exporta un GLB/VRMA autocontenido.",
    )
    header, separator, payload = uri.partition(",")
    _require(
        separator == "," and len(header) <= 128 and header.endswith(";base64"),
        "Solo se admiten recursos incrustados en base64.",
    )
    return base64.b64decode(payload, validate=True)


def _validate_gltf(content: bytes, extension: str, avatar: bool) -> dict:
    doc, binary = _gltf_container(content, extension)
    _require(isinstance(doc.get("asset"), dict) and doc["asset"].get("version") == "2.0")
    for key in ("extensionsUsed", "extensionsRequired"):
        names = doc.get(key, [])
        _require(isinstance(names, list) and all(isinstance(name, str) for name in names))
    # These codecs may expand attacker supplied lengths outside our accessor
    # budget. Existing playback stays available; this upload policy fails closed.
    unsupported = {"KHR_draco_mesh_compression", "EXT_meshopt_compression", "KHR_texture_basisu"}
    pending = [doc]
    embedded_bytes = 0
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            extensions = value.get("extensions", {})
            _require(isinstance(extensions, dict))
            if unsupported.intersection(extensions):
                raise AssetUploadError(
                    "Subida con compresión no soportada; exporta sin Draco, meshopt o BasisU.", 415
                )
            # Includes extension resource URIs, not only core buffers/images.
            if "uri" in value:
                embedded_bytes += len(_data_uri(value["uri"]))
                _require(embedded_bytes <= MAX_DECODED_BYTES)
            pending.extend(v for k, v in value.items() if k != "extras")
        elif isinstance(value, list):
            pending.extend(value)
    buffers = []
    for index, item in enumerate(_objects(doc, "buffers")):
        size = _integer(item.get("byteLength"), 1)
        if "uri" in item:
            data = memoryview(_data_uri(item["uri"]))
            _require(len(data) == size)
        else:
            _require(index == 0 and binary is not None and size <= len(binary) <= size + 3)
            data = binary[:size]
        buffers.append(data)
    if binary is not None:
        _require(bool(buffers))
    views = _objects(doc, "bufferViews")
    for view in views:
        data = _entry(buffers, view.get("buffer"))
        start, size = _integer(view.get("byteOffset", 0)), _integer(view.get("byteLength"), 1)
        _require(start + size <= len(data))
        if "byteStride" in view:
            stride = _integer(view["byteStride"], 4, 252)
            _require(stride % 4 == 0)

    def region(view_index, offset, count, element_size, component_size, *, stride_allowed=True):
        view = _entry(views, view_index)
        stride = view.get("byteStride", element_size)
        _require(stride_allowed or "byteStride" not in view)
        start = _integer(offset)
        _require(
            stride >= element_size
            and stride % component_size == 0
            and start % component_size == 0
            and (view.get("byteOffset", 0) + start) % component_size == 0
            and start + (count - 1) * stride + element_size <= view["byteLength"]
        )
        data = buffers[view["buffer"]]
        return data, view.get("byteOffset", 0) + start, stride

    accessors = _objects(doc, "accessors")
    layouts = []
    allocation = 0
    components = {
        5120: (1, "b"),
        5121: (1, "B"),
        5122: (2, "h"),
        5123: (2, "H"),
        5125: (4, "I"),
        5126: (4, "f"),
    }
    dimensions = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
    for accessor in accessors:
        count = _integer(accessor.get("count"), 1)
        kind = accessor.get("type")
        component = accessor.get("componentType")
        _require(kind in dimensions and component in components)
        width, code = components[component]
        size = width * dimensions[kind]
        if kind in ("MAT2", "MAT3"):
            columns = int(kind[-1])
            size = ((columns * width + 3) // 4 * 4) * columns
        allocation += count * max(size, dimensions[kind] * 4)
        _require(
            allocation <= MAX_DECODED_BYTES, "Los accessors superan el presupuesto de memoria."
        )
        layout = None
        if "bufferView" in accessor:
            layout = region(
                accessor["bufferView"], accessor.get("byteOffset", 0), count, size, width
            )
        else:
            _require(accessor.get("byteOffset", 0) == 0)
        if "sparse" in accessor:
            sparse = accessor["sparse"]
            _require(isinstance(sparse, dict))
            n = _integer(sparse.get("count"), 1, count)
            indices, values = sparse.get("indices"), sparse.get("values")
            _require(isinstance(indices, dict) and isinstance(values, dict))
            index_type = indices.get("componentType")
            _require(index_type in (5121, 5123, 5125))
            iw, ic = components[index_type]
            raw, start, stride = region(
                indices.get("bufferView"),
                indices.get("byteOffset", 0),
                n,
                iw,
                iw,
                stride_allowed=False,
            )
            previous = -1
            for i in range(n):
                value = struct.unpack_from("<" + ic, raw, start + i * stride)[0]
                _require(previous < value < count)
                previous = value
            region(
                values.get("bufferView"),
                values.get("byteOffset", 0),
                n,
                size,
                width,
                stride_allowed=False,
            )
        layouts.append(layout)

    nodes = _objects(doc, "nodes")
    parents = set()
    for node in nodes:
        children = node.get("children", [])
        _require(isinstance(children, list))
        for child in children:
            _entry(nodes, child)
            _require(child not in parents)
            parents.add(child)
    visited = set()
    stack = [(i, 0) for i in range(len(nodes)) if i not in parents]
    while stack:
        index, depth = stack.pop()
        _require(
            depth <= MAX_DEPTH and index not in visited,
            "Jerarquía de nodos inválida o demasiado profunda.",
        )
        visited.add(index)
        stack.extend((child, depth + 1) for child in nodes[index].get("children", []))
    _require(len(visited) == len(nodes), "La jerarquía contiene ciclos.")
    for scene in _objects(doc, "scenes"):
        _require(isinstance(scene.get("nodes", []), list))
        for index in scene.get("nodes", []):
            _entry(nodes, index)
    if "scene" in doc:
        _entry(doc.get("scenes", []), doc["scene"])
    for image in _objects(doc, "images"):
        _require(("uri" in image) != ("bufferView" in image))
        if "bufferView" in image:
            _entry(views, image["bufferView"])

    animations = _objects(doc, "animations")
    checked_tracks = set()
    for animation in animations:
        samplers, channels = _objects(animation, "samplers"), _objects(animation, "channels")
        _require(bool(samplers) and bool(channels))
        for sampler in samplers:
            times = _entry(accessors, sampler.get("input"))
            _entry(accessors, sampler.get("output"))
            _require(
                times["type"] == "SCALAR"
                and times["componentType"] == 5126
                and sampler.get("interpolation", "LINEAR") in {"LINEAR", "STEP", "CUBICSPLINE"}
            )
            # Sparse animation tracks need expansion to check ordering/values.
            # Fail explicitly rather than accepting a partially checked track.
            output = accessors[sampler["output"]]
            if "sparse" in times or "sparse" in output:
                raise AssetUploadError(
                    "Subida de pistas sparse no soportada; exporta pistas densas.", 415
                )
            previous = -1.0
            for index in (sampler["input"], sampler["output"]):
                check_key = (index, index == sampler["input"])
                if check_key in checked_tracks:
                    continue
                accessor, layout = accessors[index], layouts[index]
                _require(layout is not None and accessor["componentType"] == 5126)
                raw, start, stride = layout
                fmt = "<" + "f" * dimensions[accessor["type"]]
                for frame in range(accessor["count"]):
                    values = struct.unpack_from(fmt, raw, start + frame * stride)
                    _require(all(math.isfinite(v) for v in values))
                    if index == sampler["input"]:
                        _require(
                            values[0] >= 0 and values[0] > previous,
                            "Tiempos de animación inválidos.",
                        )
                        previous = values[0]
                checked_tracks.add(check_key)
        for channel in channels:
            sampler = _entry(samplers, channel.get("sampler"))
            target = channel.get("target")
            _require(isinstance(target, dict))
            _entry(nodes, target.get("node"))
            path = target.get("path")
            _require(path in {"translation", "rotation", "scale", "weights"})
            source, output = accessors[sampler["input"]], accessors[sampler["output"]]
            count = source["count"] * (3 if sampler.get("interpolation") == "CUBICSPLINE" else 1)
            _require(
                output["type"]
                == ("VEC4" if path == "rotation" else "SCALAR" if path == "weights" else "VEC3")
            )
            _require(
                output["count"] == count if path != "weights" else output["count"] % count == 0
            )
    extensions = doc.get("extensions", {})
    if avatar:
        valid = False
        for name in ("VRM", "VRMC_vrm"):
            ext = extensions.get(name, {})
            bones = ext.get("humanoid", {}).get("humanBones", {})
            hips = (
                next((b for b in bones if isinstance(b, dict) and b.get("bone") == "hips"), {})
                if isinstance(bones, list)
                else bones.get("hips", {})
            )
            if (
                isinstance(hips, dict)
                and type(hips.get("node")) is int
                and 0 <= hips["node"] < len(nodes)
            ):
                valid = True
        _require(valid, "El archivo no contiene un avatar VRM 0/1 con humanoide válido.")
    else:
        _require(bool(animations), "El archivo no contiene animaciones.")
        if extension == "vrma":
            ext = extensions.get("VRMC_vrm_animation")
            _require(isinstance(ext, dict), "Falta la extensión VRMC_vrm_animation.")
            bones = ext.get("humanoid", {}).get("humanBones", {})
            _require(isinstance(bones, dict))
            for bone in bones.values():
                _require(isinstance(bone, dict))
                _entry(nodes, bone.get("node"))
    return doc


def _validate_bvh(content: bytes) -> None:
    # Stream tokens: never split the entire motion section into millions of
    # Python strings. The number of numeric values is independently bounded.
    text = content.decode("utf-8-sig")
    tokens = iter(re.finditer(r"[^\s{}]+|[{}]", text))
    position = 0

    def take(expected=None):
        nonlocal position
        match = next(tokens, None)
        _require(match is not None, "BVH truncado.")
        value = match.group()
        position = match.end()
        _require(expected is None or value == expected, "Estructura BVH inválida.")
        return value

    def numbers(count):
        for _ in range(count):
            _require(math.isfinite(float(take())))

    take("HIERARCHY")
    take("ROOT")
    channels = nodes = 0

    def node(depth, end=False):
        nonlocal channels, nodes
        nodes += 1
        _require(depth <= MAX_DEPTH and nodes <= 2048)
        if not end:
            take()  # bone name
        take("{")
        take("OFFSET")
        numbers(3)
        if not end:
            take("CHANNELS")
            n = _integer(int(take()), 0, 6)
            names = [take() for _ in range(n)]
            _require(
                len(set(names)) == n
                and all(
                    v in {a + b for a in "XYZ" for b in ("position", "rotation")} for v in names
                )
            )
            channels += n
        while True:
            kind = take()
            if kind == "}":
                return
            _require(not end)
            if kind == "JOINT":
                node(depth + 1)
            else:
                _require(kind == "End")
                take("Site")
                node(depth + 1, True)

    node(0)
    take("MOTION")
    take("Frames:")
    frames = _integer(int(take()), 1, MAX_VALUES)
    take("Frame")
    take("Time:")
    interval = float(take())
    _require(
        math.isfinite(interval)
        and interval > 0
        and channels > 0
        and frames * channels <= MAX_VALUES
    )
    # BVHLoader reads one line per frame; matching only the total number of
    # values would accept broken row boundaries and silently shift motion.
    motion = io.StringIO(text[position:])
    _require(not motion.readline().strip(), "Datos BVH en la línea de Frame Time.")
    rows = 0
    for line in motion:
        if not line.strip():
            continue
        rows += 1
        _require(rows <= frames)
        columns = 0
        for match in re.finditer(r"\S+", line):
            columns += 1
            _require(columns <= channels and math.isfinite(float(match.group())))
        _require(columns == channels, "Los canales BVH no coinciden con los valores del fotograma.")
    _require(rows == frames, "El número de filas BVH no coincide con los fotogramas.")


def _validate_pmx_archive(content: bytes) -> None:
    """Check the uploaded ZIP without extracting or rewriting it."""
    if len(content) > PMX_ARCHIVE_MAX_BYTES:
        raise AssetUploadError("El ZIP MMD supera el límite de 64 MiB.", 413)
    model = None
    names: set[str] = set()
    expanded = 0
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            _require(len(entries) <= 256, "El ZIP MMD tiene demasiados archivos.")
            for entry in entries:
                name = entry.orig_filename.replace("\\", "/").rstrip("/")
                parts = name.split("/")
                _require(
                    all(part not in {"", ".", ".."} for part in parts)
                    and not any(ord(char) < 32 or char == ":" for char in name),
                    "El ZIP MMD contiene una ruta inválida.",
                )
                _require(name.lower() not in names, "El ZIP MMD contiene rutas duplicadas.")
                names.add(name.lower())
                _require(
                    not stat.S_ISLNK(entry.external_attr >> 16), "El ZIP MMD contiene un enlace."
                )
                _require(
                    not entry.flag_bits & 1
                    and entry.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
                    "El ZIP MMD está cifrado o usa una compresión no admitida.",
                )
                if entry.is_dir():
                    continue
                expanded += entry.file_size
                if expanded > MAX_DECODED_BYTES or entry.file_size > 32 * 1024 * 1024:
                    raise AssetUploadError(
                        "El contenido del ZIP MMD supera el límite permitido.", 413
                    )
                if Path(name).suffix.lower() == ".pmx":
                    _require(model is None, "El ZIP debe contener un solo modelo PMX.")
                    model = entry
            _require(model is not None, "El ZIP debe contener un modelo PMX con sus texturas.")
            with archive.open(model) as source:
                header = source.read(17)
                _require(
                    len(header) == 17 and header[:4] == b"PMX ",
                    "El archivo PMX del ZIP no es válido.",
                )
                _require(
                    header[4:9] in (struct.pack("<fB", 2.0, 8), struct.pack("<fB", 2.1, 8)),
                    "El ZIP contiene una versión de PMX no admitida.",
                )
            _require(archive.testzip() is None, "El ZIP MMD contiene un archivo dañado.")
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, zlib.error) as exc:
        raise AssetUploadError("No se pudo leer el ZIP MMD.") from exc


def validate_asset(content: bytes, extension: str, *, avatar=False) -> dict | None:
    """Validate within explicit budgets, returning original glTF metadata."""
    try:
        _require(bool(content))
        if avatar and extension == "zip":
            _validate_pmx_archive(content)
            return None
        if extension in {"vrm", "vrma", "glb", "gltf"}:
            return _validate_gltf(content, extension, avatar)
        if not avatar and extension == "bvh":
            _validate_bvh(content)
            return None
        if not avatar and extension == "fbx":
            raise AssetUploadError(
                "La subida FBX (binario/ASCII) aún no tiene un validador seguro. "
                "Exporta la animación como GLB, VRMA o BVH. "
                "Las animaciones FBX existentes siguen disponibles.",
                415,
            )
        raise AssetUploadError("Formato de subida no soportado.", 415)
    except AssetUploadError:
        raise
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        UnicodeError,
        struct.error,
        binascii.Error,
        RecursionError,
        OverflowError,
    ) as exc:
        raise AssetUploadError("Archivo inválido o estructura no soportada.") from exc
