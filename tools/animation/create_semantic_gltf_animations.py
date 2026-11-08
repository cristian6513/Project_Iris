"""Generate retarget-safe GLTF animation clips for IRIS.

Blender armature GLBs carry bone local axes/roll information. IRIS's generic
GLB retargeter copies source quaternions onto VRM normalized bones, so those
armature-space quaternions can bend Carlotta's legs in impossible directions.

This script writes tiny GLTF animation-only files whose nodes are named like
humanoid bones (Hips, Spine, LeftUpLeg...). The keyframes store semantic
rotations directly, matching the procedural pose values used by idle_system.js.
"""

from __future__ import annotations

import base64
import json
import math
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "face" / "animations"

BONES = [
    "Hips", "Spine", "Chest", "Neck", "Head",
    "LeftArm", "LeftForeArm", "LeftHand",
    "RightArm", "RightForeArm", "RightHand",
    "LeftUpLeg", "LeftLeg", "LeftFoot",
    "RightUpLeg", "RightLeg", "RightFoot",
]


def quat_from_euler_xyz(rx: float, ry: float, rz: float) -> tuple[float, float, float, float]:
    """Return quaternion x, y, z, w for intrinsic XYZ Euler radians."""
    cx = math.cos(rx / 2)
    sx = math.sin(rx / 2)
    cy = math.cos(ry / 2)
    sy = math.sin(ry / 2)
    cz = math.cos(rz / 2)
    sz = math.sin(rz / 2)
    return (
        sx * cy * cz + cx * sy * sz,
        cx * sy * cz - sx * cy * sz,
        cx * cy * sz + sx * sy * cz,
        cx * cy * cz - sx * sy * sz,
    )


def deg(x: float) -> float:
    return math.radians(x)


def qdeg(x: float = 0, y: float = 0, z: float = 0) -> tuple[float, float, float, float]:
    return quat_from_euler_xyz(deg(x), deg(y), deg(z))


def build_gltf(name: str, duration: float, frames: list[tuple[float, dict[str, tuple[float, float, float, float]]]]) -> dict:
    nodes = [{"name": bone} for bone in BONES]
    node_index = {bone: i for i, bone in enumerate(BONES)}

    blob = bytearray()
    buffer_views = []
    accessors = []
    samplers = []
    channels = []

    def align4() -> None:
        while len(blob) % 4:
            blob.append(0)

    def add_accessor(values: list[float], component_type: int, type_name: str, count: int, minmax: bool = False) -> int:
        align4()
        offset = len(blob)
        for value in values:
            blob.extend(struct.pack("<f", float(value)))
        byte_length = len(blob) - offset
        buffer_views.append({"buffer": 0, "byteOffset": offset, "byteLength": byte_length})
        accessor = {
            "bufferView": len(buffer_views) - 1,
            "componentType": component_type,
            "count": count,
            "type": type_name,
        }
        if minmax:
            accessor["min"] = [min(values)]
            accessor["max"] = [max(values)]
        accessors.append(accessor)
        return len(accessors) - 1

    times = [t for t, _ in frames]
    time_accessor = add_accessor(times, 5126, "SCALAR", len(times), minmax=True)

    for bone in BONES:
        rotations = []
        for _, pose in frames:
            rotations.extend(pose.get(bone, qdeg()))
        rotation_accessor = add_accessor(rotations, 5126, "VEC4", len(times))
        samplers.append({
            "input": time_accessor,
            "output": rotation_accessor,
            "interpolation": "LINEAR",
        })
        channels.append({
            "sampler": len(samplers) - 1,
            "target": {"node": node_index[bone], "path": "rotation"},
        })

    uri = "data:application/octet-stream;base64," + base64.b64encode(bytes(blob)).decode("ascii")
    return {
        "asset": {"version": "2.0", "generator": "IRIS semantic animation generator"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(BONES)))}],
        "nodes": nodes,
        "buffers": [{"uri": uri, "byteLength": len(blob)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
        "animations": [{
            "name": name,
            "samplers": samplers,
            "channels": channels,
        }],
        "extras": {"duration": duration, "retargeting": "semantic_vrm_humanoid"},
    }


def seated_pose(breath: float = 0, look: float = 0, hands: float = 0) -> dict[str, tuple[float, float, float, float]]:
    return {
        "Hips": qdeg(-5),
        "Spine": qdeg(-3.4 + breath, look * 0.4, 0),
        "Chest": qdeg(-2.6 + breath, look * 0.55, 0),
        "Neck": qdeg(-2.0, 3.0 + look, 0),
        "Head": qdeg(-3.2, 4.0 + look, -0.4),
        "LeftUpLeg": qdeg(33, 0, 1.4),
        "RightUpLeg": qdeg(33, 0, -1.4),
        "LeftLeg": qdeg(-24),
        "RightLeg": qdeg(-24),
        "LeftFoot": qdeg(5),
        "RightFoot": qdeg(5),
        "LeftArm": qdeg(-18, 0, -2),
        "RightArm": qdeg(18, 0, 2),
        "LeftForeArm": qdeg(-31 + hands),
        "RightForeArm": qdeg(31 - hands),
        "LeftHand": qdeg(hands * 0.7),
        "RightHand": qdeg(-hands * 0.7),
    }


def seated_pose_v2(breath: float = 0, look: float = 0, hands: float = 0) -> dict[str, tuple[float, float, float, float]]:
    """Desk seated draft used by Use PC 2.

    Keep this clip in semantic humanoid space. The Blender-exported draft
    carried Carlotta's raw bone axes and retargeted with crossed hands / feet.
    """
    return {
        "Hips": qdeg(-3),
        "Spine": qdeg(-1.4 + breath, look * 0.22, 0),
        "Chest": qdeg(-1.0 + breath, look * 0.28, 0),
        "Neck": qdeg(-1.0, 1.2 + look * 0.4, 0),
        "Head": qdeg(-2.2, 1.5 + look * 0.45, 0),
        "LeftUpLeg": qdeg(47, 0, 1.5),
        "RightUpLeg": qdeg(47, 0, -1.5),
        "LeftLeg": qdeg(-64),
        "RightLeg": qdeg(-64),
        "LeftFoot": qdeg(-8),
        "RightFoot": qdeg(-8),
        "LeftArm": qdeg(-31, -1.5, -7),
        "RightArm": qdeg(31, 1.5, 7),
        "LeftForeArm": qdeg(-43 + hands * 0.45, 1.5, -3),
        "RightForeArm": qdeg(43 - hands * 0.45, -1.5, 3),
        "LeftHand": qdeg(-3 + hands * 0.25, 0, -2),
        "RightHand": qdeg(3 - hands * 0.25, 0, 2),
    }


def focus_pose(look: float = 0) -> dict[str, tuple[float, float, float, float]]:
    return {
        "Spine": qdeg(-1.6, look * 0.25, 0),
        "Chest": qdeg(-1.2, look * 0.35, 0),
        "Neck": qdeg(-1.4, 2.0 + look, 0),
        "Head": qdeg(-2.8, 4.0 + look, -0.3),
        "LeftArm": qdeg(-4, 0, -1),
        "RightArm": qdeg(4, 0, 1),
        "LeftForeArm": qdeg(-5),
        "RightForeArm": qdeg(5),
    }


def write_clip(filename: str, name: str, duration: float, frames: list[tuple[float, dict[str, tuple[float, float, float, float]]]]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / filename
    gltf = build_gltf(name, duration, frames)
    path.write_text(json.dumps(gltf, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {path}")


def main() -> None:
    neutral: dict[str, tuple[float, float, float, float]] = {}
    sit_a = seated_pose(breath=0.0, look=0.0, hands=0.0)
    sit_b = seated_pose(breath=0.55, look=-1.2, hands=2.2)
    sit_c = seated_pose(breath=-0.2, look=0.9, hands=-1.8)
    pc2_a = seated_pose_v2(breath=0.0, look=0.0, hands=0.0)
    pc2_b = seated_pose_v2(breath=0.18, look=-0.35, hands=1.2)
    pc2_c = seated_pose_v2(breath=-0.08, look=0.28, hands=-0.9)

    write_clip(
        "pc_focus_idle.gltf",
        "pc_focus_idle",
        4.0,
        [
            (0.0, focus_pose(0.0)),
            (1.6, focus_pose(-1.0)),
            (3.2, focus_pose(0.8)),
            (4.0, focus_pose(0.0)),
        ],
    )
    write_clip(
        "pc_sit_down.gltf",
        "pc_sit_down",
        1.65,
        [
            (0.0, neutral),
            (0.45, {
                "Hips": qdeg(-2), "Spine": qdeg(-1.5), "Chest": qdeg(-1),
                "LeftUpLeg": qdeg(12, 0, 0.5), "RightUpLeg": qdeg(12, 0, -0.5),
                "LeftLeg": qdeg(-8), "RightLeg": qdeg(-8),
                "LeftArm": qdeg(-7, 0, -1), "RightArm": qdeg(7, 0, 1),
                "Head": qdeg(-1.4, 2.0, 0),
            }),
            (1.15, seated_pose(breath=-0.25, look=0.4, hands=0.0)),
            (1.65, sit_a),
        ],
    )
    write_clip(
        "pc_seated_idle.gltf",
        "pc_seated_idle",
        4.0,
        [
            (0.0, sit_a),
            (1.25, sit_b),
            (2.65, sit_c),
            (4.0, sit_a),
        ],
    )
    write_clip(
        "_draft_pc_sit_down_v2.gltf",
        "pc_sit_down_v2",
        1.65,
        [
            (0.0, neutral),
            (0.45, {
                "Hips": qdeg(-1),
                "Spine": qdeg(-0.8),
                "Chest": qdeg(-0.5),
                "LeftUpLeg": qdeg(18, 0, 0.5),
                "RightUpLeg": qdeg(18, 0, -0.5),
                "LeftLeg": qdeg(-16),
                "RightLeg": qdeg(-16),
                "LeftFoot": qdeg(-3),
                "RightFoot": qdeg(-3),
                "LeftArm": qdeg(-12, 0, -2),
                "RightArm": qdeg(12, 0, 2),
                "Head": qdeg(-1, 1.2, 0),
            }),
            (1.15, seated_pose_v2(breath=-0.05, look=0.15, hands=0.0)),
            (1.65, pc2_a),
        ],
    )
    write_clip(
        "_draft_pc_seated_idle_v2.gltf",
        "pc_seated_idle_v2",
        4.0,
        [
            (0.0, pc2_a),
            (1.25, pc2_b),
            (2.65, pc2_c),
            (4.0, pc2_a),
        ],
    )


if __name__ == "__main__":
    main()
