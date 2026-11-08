"""IRIS' VRM from PBR to MToon, editing only the glTF JSON (2026-10-28).

IRIS' exported materials are PBR. Face's room is lit for MToon avatars (VRoid style):
three.js divides PBR diffuse light by pi and MToon does not, so under the same dim rig
IRIS looked grey while Carlotta (MToon) looked right. This adds `VRMC_materials_mtoon`
to every material and drops the PBR-only extensions (clearcoat, transmission,
iridescence...). Geometry, bones, expressions, springs, textures and the binary chunk
stay byte-identical, so earlier optimisations (sparse morphs) survive. Run it again
after any new export from Blender:

    python -I scripts/iris_vrm_mtoon.py IN.vrm OUT.vrm [--report report.json]

Values follow Carlotta's MToon as three-vrm reads it: shade = 0.45 of the texture,
toony 0.95, GI equalisation 0.9, no outline. Materials are matched by their stable
exported names; anything new falls back to the default recipe.
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

GLB_MAGIC, JSON_CHUNK, BIN_CHUNK = 0x46546C67, 0x4E4F534A, 0x004E4942
MTOON = "VRMC_materials_mtoon"
PBR_ONLY = {
    "KHR_materials_clearcoat", "KHR_materials_transmission", "KHR_materials_specular",
    "KHR_materials_anisotropy", "KHR_materials_ior", "KHR_materials_iridescence",
}

# (name prefix, recipe). First match wins; the order puts specific names first.
RECIPES = [
    # Skin (body and face share it): warm shade, mostly lit so the face has no hard shadow.
    ("Material.001", {"shade": [0.72, 0.52, 0.52], "toony": 0.85, "shift": 0.25}),
    # Eyes, sclera and mouth read as flat painted parts.
    ("iris_texture.001", {"shade": [0.95, 0.95, 0.95], "toony": 0.9, "shift": 0.0}),
    ("esclera", {"shade": [0.95, 0.95, 0.95], "toony": 0.9, "shift": 0.0}),
    ("IRIS_Boca_Original", {"shade": [0.85, 0.8, 0.8], "toony": 0.9, "shift": 0.1}),
    ("IRIS_Labio_Interior_Rosado", {"shade": [0.85, 0.8, 0.8], "toony": 0.9, "shift": 0.1}),
    ("cabello_base", {"shade": [0.42, 0.38, 0.52], "rim": [0.06, 0.04, 0.12]}),
    # Transmission and iridescence have no MToon equivalent: their glow stays (emission)
    # and a coloured rim stands in for the iridescent edge.
    ("MAT_Cristal_IRIS", {"shade": [0.6, 0.6, 0.65], "rim": [0.22, 0.1, 0.38], "fresnel": 2.5}),
    ("MAT_Linea_Holografica", {"shade": [0.6, 0.6, 0.65], "rim": [0.14, 0.07, 0.28], "fresnel": 3.0}),
    ("detalles_neon", {"shade": [0.6, 0.6, 0.65]}),
    ("logo iris", {"shade": [0.6, 0.6, 0.65]}),
    # Eyeliner and lashes stay pure dark lines.
    ("pestañas", {"dark_line": True, "rim": None}),
    ("IRIS_Maquillaje_Delineado", {"dark_line": True, "rim": None}),
]
DEFAULT = {"shade": [0.45, 0.45, 0.5], "toony": 0.95, "shift": -0.05,
           # The outfit is black by design: a soft edge and stronger neon keep it readable
           # in the dark themes, where its 0.3 emission barely showed.
           "rim": [0.05, 0.04, 0.09], "emission": 2.0}
# Skin, eyes and mouth never glow and need no edge.
for _prefix, _recipe in RECIPES[:5]:
    _recipe.setdefault("rim", None)
    _recipe.setdefault("emission", 1.0)
# Black leather (no texture, base close to black) only had shape through PBR gloss:
# a soft cool rim keeps its volume readable in the dark room.
DARK_RIM = [0.1, 0.08, 0.16]


def read_glb(data: bytes) -> tuple[dict, bytes]:
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC or version != 2 or length != len(data):
        raise ValueError("No es un GLB/VRM 2.0 válido.")
    json_length, json_type = struct.unpack_from("<II", data, 12)
    if json_type != JSON_CHUNK:
        raise ValueError("El primer bloque del VRM no es JSON.")
    document = json.loads(data[20:20 + json_length])
    rest = data[20 + json_length:]
    return document, rest  # rest = the BIN chunk header and bytes, kept as they are


def write_glb(document: dict, rest: bytes) -> bytes:
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    header = struct.pack("<III", GLB_MAGIC, 2, 12 + 8 + len(payload) + len(rest))
    return header + struct.pack("<II", len(payload), JSON_CHUNK) + payload + rest


def recipe_for(name: str) -> dict:
    for prefix, recipe in RECIPES:
        if name.startswith(prefix):
            return {**DEFAULT, **recipe}
    return dict(DEFAULT)


def mtoon_for(material: dict) -> tuple[dict, str]:
    name = material.get("name", "")
    recipe = recipe_for(name)
    pbr = material.get("pbrMetallicRoughness", {})
    base = pbr.get("baseColorFactor", [1, 1, 1, 1])[:3]
    texture = pbr.get("baseColorTexture")
    dark = texture is None and max(base) < 0.05
    if recipe.get("dark_line") or dark:
        shade = list(base)  # black stays black in light and shadow
    elif texture is None:
        shade = [b * s for b, s in zip(base, recipe["shade"])]
    else:
        shade = list(recipe["shade"])
    mtoon = {
        "specVersion": "1.0",
        "transparentWithZWrite": False,
        "renderQueueOffsetNumber": 0,
        "shadeColorFactor": [round(v, 4) for v in shade],
        "shadingToonyFactor": recipe["toony"],
        "shadingShiftFactor": recipe["shift"],
        "giEqualizationFactor": 0.9,
        "matcapFactor": [0, 0, 0],
        "parametricRimColorFactor": [0, 0, 0],
        "rimLightingMixFactor": 1.0,
        "parametricRimFresnelPowerFactor": recipe.get("fresnel", 4.0),
        "parametricRimLiftFactor": 0.0,
        "outlineWidthMode": "none",
        "outlineWidthFactor": 0.0,
        "outlineColorFactor": [0, 0, 0],
        "outlineLightingMixFactor": 1.0,
    }
    if texture is not None:
        mtoon["shadeMultiplyTexture"] = {"index": texture["index"], "texCoord": texture.get("texCoord", 0)}
    rim = DARK_RIM if dark and not recipe.get("dark_line") else recipe.get("rim")
    if rim:
        # Mostly independent of the room's light, so the edge reads in a dark theme.
        mtoon["parametricRimColorFactor"] = rim
        mtoon["rimLightingMixFactor"] = 0.3
    kind = "dark-rim" if dark and rim else ("line" if recipe.get("dark_line") else "textured" if texture else "color")
    return mtoon, kind


def convert(document: dict) -> dict:
    report = {"materials": [], "removed_extensions": sorted(PBR_ONLY & set(document.get("extensionsUsed", [])))}
    for material in document.get("materials", []):
        extensions = {k: v for k, v in material.get("extensions", {}).items() if k not in PBR_ONLY}
        mtoon, kind = mtoon_for(material)
        extensions[MTOON] = mtoon
        material["extensions"] = extensions
        boost = recipe_for(material.get("name", "")).get("emission", 1.0)
        if boost != 1.0 and any(material.get("emissiveFactor", [0, 0, 0])):
            material["emissiveFactor"] = [round(min(1.0, v * boost), 4) for v in material["emissiveFactor"]]
        report["materials"].append({"name": material.get("name"), "kind": kind,
                                    "shade": mtoon["shadeColorFactor"],
                                    "rim": mtoon["parametricRimColorFactor"],
                                    "emissive": material.get("emissiveFactor", [0, 0, 0])})
    used = [name for name in document.get("extensionsUsed", []) if name not in PBR_ONLY]
    document["extensionsUsed"] = used + ([MTOON] if MTOON not in used else [])
    if "extensionsRequired" in document:
        document["extensionsRequired"] = [n for n in document["extensionsRequired"] if n not in PBR_ONLY]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    data = args.source.read_bytes()
    document, rest = read_glb(data)
    if "VRMC_vrm" not in document.get("extensions", {}):
        raise SystemExit("Solo se convierten VRM 1.0 (VRMC_vrm).")
    report = convert(document)
    output = write_glb(document, rest)
    if args.output.exists():
        raise SystemExit(f"No sobrescribo {args.output}; elige otro nombre.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    report.update(source=str(args.source), output=str(args.output),
                  source_bytes=len(data), output_bytes=len(output))
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("output", "source_bytes", "output_bytes", "removed_extensions")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
