"""Small, structurally complete fixtures with an actual single-frame track."""

import base64
import json
import struct


def container(document, binary=None):
    raw = json.dumps(document, separators=(",", ":")).encode()
    raw += b" " * (-len(raw) % 4)
    chunks = struct.pack("<I4s", len(raw), b"JSON") + raw
    if binary is not None:
        binary += b"\0" * (-len(binary) % 4)
        chunks += struct.pack("<I4s", len(binary), b"BIN\0") + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


def animation_document(tag="new"):
    binary = struct.pack("<5f", 0, 0, 0, 0, 1)
    return {
        "asset": {"version": "2.0"},
        "extras": {"tag": tag},
        "nodes": [{"name": "hips"}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
        "extensionsUsed": ["VRMC_vrm_animation"],
        "extensions": {
            "VRMC_vrm_animation": {
                "specVersion": "1.0",
                "humanoid": {"humanBones": {"hips": {"node": 0}}},
            }
        },
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteLength": 4},
            {"buffer": 0, "byteOffset": 4, "byteLength": 16},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 1,
                "type": "SCALAR",
                "min": [0],
                "max": [0],
            },
            {"bufferView": 1, "componentType": 5126, "count": 1, "type": "VEC4"},
        ],
        "animations": [
            {
                "samplers": [{"input": 0, "output": 1}],
                "channels": [{"sampler": 0, "target": {"node": 0, "path": "rotation"}}],
            }
        ],
    }, binary


def animation(tag="new", *, embedded=False):
    doc, binary = animation_document(tag)
    if embedded:
        doc["buffers"][0]["uri"] = (
            "data:application/octet-stream;base64," + base64.b64encode(binary).decode()
        )
        return json.dumps(doc).encode()
    return container(doc, binary)


def avatar(version=1):
    return container(
        {
            "asset": {"version": "2.0"},
            "nodes": [{}],
            "extensions": {
                "VRMC_vrm" if version else "VRM": {
                    "humanoid": {
                        "humanBones": {"hips": {"node": 0}}
                        if version
                        else [{"bone": "hips", "node": 0}]
                    },
                }
            },
        }
    )


BVH = b"""HIERARCHY
ROOT Hips
{
 OFFSET 0 0 0
 CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
 JOINT Head
 {
  OFFSET 0 1 0
  CHANNELS 3 Zrotation Xrotation Yrotation
  End Site
  {
   OFFSET 0 1 0
  }
 }
}
MOTION
Frames: 1
Frame Time: 0.0333333
0 0 0 0 0 0 0 0 0
"""
