"""IRIS' VRM converted from PBR to MToon (scripts/iris_vrm_mtoon.py, 2026-10-28)."""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from scripts.iris_vrm_mtoon import MTOON, PBR_ONLY, convert, read_glb, write_glb

ROOT = Path(__file__).resolve().parents[1]


def material(name, *, texture=None, base=(1, 1, 1, 1), emissive=None, extensions=()):
    pbr = {"baseColorFactor": list(base)}
    if texture is not None:
        pbr["baseColorTexture"] = {"index": texture}
    item = {"name": name, "pbrMetallicRoughness": pbr, "extensions": {e: {} for e in extensions}}
    if emissive is not None:
        item["emissiveFactor"] = list(emissive)
        item["emissiveTexture"] = {"index": 0}
    return item


def fixture():
    return {
        "asset": {"version": "2.0"},
        "extensionsUsed": ["KHR_materials_clearcoat", "KHR_materials_transmission", "VRMC_vrm", "VRMC_springBone"],
        "extensions": {"VRMC_vrm": {"specVersion": "1.0"}},
        "materials": [
            material("Material.001__IRIS_00_0", texture=1, extensions=["KHR_materials_clearcoat"]),
            material("correa1__IRIS_04_0", base=(0.0, 0.0, 0.01, 1), extensions=["KHR_materials_clearcoat"]),
            material("pestañas__IRIS_00_2", base=(0.0, 0.0, 0.01, 1)),
            material("chaqueta__IRIS_39_0", texture=2, emissive=(0.3, 0.3, 0.3)),
            material("MAT_Cristal_IRIS__IRIS_05_1", texture=3, emissive=(0.6, 0.6, 0.6),
                     extensions=["KHR_materials_transmission", "KHR_materials_clearcoat"]),
            material("remaches__IRIS_44_0", base=(1, 0.07, 0.34, 1)),
        ],
    }


def test_every_material_becomes_mtoon_and_the_pbr_only_extensions_go():
    document = fixture()
    report = convert(document)
    assert [m["kind"] for m in report["materials"]] == ["textured", "dark-rim", "line", "textured", "textured", "color"]
    for item in document["materials"]:
        assert set(item["extensions"]) == {MTOON}
        assert item["extensions"][MTOON]["specVersion"] == "1.0"
        assert item["extensions"][MTOON]["outlineWidthMode"] == "none"
    assert document["extensionsUsed"] == ["VRMC_vrm", "VRMC_springBone", MTOON]
    assert not PBR_ONLY & set(document["extensionsUsed"])


def test_each_part_keeps_its_look():
    document = fixture()
    convert(document)
    skin, leather, lash, coat, crystal, rivet = (m["extensions"][MTOON] for m in document["materials"])
    # Skin: warm shade over its own texture, mostly lit, no glowing edge.
    assert skin["shadeMultiplyTexture"] == {"index": 1, "texCoord": 0}
    assert skin["shadeColorFactor"][0] > skin["shadeColorFactor"][2] and skin["shadingShiftFactor"] > 0
    assert skin["parametricRimColorFactor"] == [0, 0, 0]
    # Black leather keeps its shape through a rim; lashes stay plain dark lines.
    assert leather["shadeColorFactor"] == [0.0, 0.0, 0.01] and any(leather["parametricRimColorFactor"])
    assert lash["parametricRimColorFactor"] == [0, 0, 0]
    # The outfit's neon glows twice as strong, capped at 1.
    assert document["materials"][3]["emissiveFactor"] == [0.6, 0.6, 0.6]
    assert document["materials"][4]["emissiveFactor"] == [1.0, 1.0, 1.0]
    assert any(coat["parametricRimColorFactor"]) and any(crystal["parametricRimColorFactor"])
    # An untextured coloured part shades towards a darker tone of its own colour.
    assert rivet["shadeColorFactor"] == [0.45, 0.0315, 0.17]


def test_the_glb_round_trip_keeps_the_binary_chunk_byte_for_byte():
    document = fixture()
    payload = json.dumps(document).encode()
    payload += b" " * (-len(payload) % 4)
    binary = b"\x01\x02\x03\x04" * 8
    data = (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(payload) + 8 + len(binary))
            + struct.pack("<II", len(payload), 0x4E4F534A) + payload
            + struct.pack("<II", len(binary), 0x004E4942) + binary)
    parsed, rest = read_glb(data)
    convert(parsed)
    output = write_glb(parsed, rest)
    assert output.endswith(struct.pack("<II", len(binary), 0x004E4942) + binary)
    assert struct.unpack_from("<I", output, 8)[0] == len(output)
    assert (len(output) - len(rest)) % 4 == 0
    again, _ = read_glb(output)
    assert again["materials"][0]["extensions"][MTOON]["specVersion"] == "1.0"
    with pytest.raises(ValueError):
        read_glb(b"not a glb" * 4)


def test_the_shipped_iris_is_the_mtoon_conversion():
    document, _ = read_glb((ROOT / "face" / "animations" / "iris.vrm").read_bytes())
    assert "VRMC_vrm" in document["extensions"]
    assert len(document["materials"]) == 56
    assert all(set(m.get("extensions", {})) == {MTOON} for m in document["materials"])
    assert MTOON in document["extensionsUsed"] and not PBR_ONLY & set(document["extensionsUsed"])
