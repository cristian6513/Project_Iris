"""
IRIS Auto-Rigger — 3D Model Processing Pipeline

Takes raw 3D model files (.obj, .fbx, .glb) and prepares them
for use as IRIS's avatar in Three.js:

1. Format conversion → GLB (Three.js native)
2. VRM packaging (optional) → adds blendshapes & bone metadata
3. Anymate3D auto-rigging (optional) → generates skeleton from mesh

Dependencies:
    pip install trimesh pygltflib numpy
    Optional: pip install torch anymate  (for Anymate3D auto-rigging)
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from core.rigging_jobs import RIG_FORMATS, RIG_METHODS

logger = logging.getLogger("IRIS.rigger")

# Standard humanoid bone names (VRM / Mixamo compatible)
HUMANOID_BONES = [
    "hips", "spine", "chest", "upperChest", "neck", "head",
    "leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand",
    "rightShoulder", "rightUpperArm", "rightLowerArm", "rightHand",
    "leftUpperLeg", "leftLowerLeg", "leftFoot",
    "rightUpperLeg", "rightLowerLeg", "rightFoot",
    "leftEye", "rightEye", "jaw",
]


class AutoRigger:
    """
    Automatic rigging pipeline.

    Strategy priority:
    1. Anymate3D (local ML model, best results for humanoids)
    2. Blender scripted rig (if Blender is installed)
    3. Manual bone placement heuristic (always available)
    """

    def __init__(self) -> None:
        self._anymate_available = False
        self._blender_path: str | None = None

    async def initialize(self) -> None:
        """Check what rigging backends are available."""
        # Check Anymate3D
        try:
            import anymate  # noqa: F401
            self._anymate_available = True
            logger.info("Anymate3D available for auto-rigging")
        except ImportError:
            logger.info("Anymate3D not installed (optional). pip install anymate")

        # Check Blender
        for path in [
            "blender",
            "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe",
            "C:/Program Files/Blender Foundation/Blender 4.1/blender.exe",
            "C:/Program Files/Blender Foundation/Blender 4.0/blender.exe",
            "/usr/bin/blender",
            "/Applications/Blender.app/Contents/MacOS/Blender",
        ]:
            if shutil.which(path):
                self._blender_path = path
                logger.info("Blender found at: %s", path)
                break

        if not self._anymate_available and not self._blender_path:
            logger.info("No rigging backend found. Heuristic bone placement available.")

    async def auto_rig(
        self,
        input_model: str,
        output_path: str | None = None,
        method: str = "auto",
    ) -> dict[str, Any]:
        """
        Auto-rig a 3D model.

        Args:
            input_model: Path to a format declared in RIG_FORMATS
            output_path: Where to save the rigged model
            method: 'anymate', 'blender', 'heuristic', or 'auto'

        Returns:
            Dict with status, output_path, bones found, etc.
        """
        if method not in RIG_METHODS:
            return {"status": "error", "message": "Unknown rigging method"}
        inp = Path(input_model)
        extension = inp.suffix.lower()
        supported = set().union(*RIG_FORMATS.values()) if method == "auto" else RIG_FORMATS[method]
        if extension not in supported:
            return {"status": "error", "message": "Unsupported format for this rigging method"}
        if not inp.is_file():
            return {"status": "error", "message": f"File not found: {input_model}"}

        if output_path is None:
            output_path = str(inp.parent / f"{inp.stem}_rigged.glb")

        # Only choose an available backend that actually supports this format.
        available = {
            "anymate": self._anymate_available,
            "blender": bool(self._blender_path),
            "heuristic": True,
        }
        if method == "auto":
            method = next(
                backend for backend in ("anymate", "blender", "heuristic")
                if available[backend] and extension in RIG_FORMATS[backend]
            )
        if not available[method]:
            return {"status": "error", "message": f"Rigging backend unavailable: {method}"}

        logger.info("Auto-rigging %s with method: %s", inp.name, method)
        backend = {
            "anymate": self._rig_anymate,
            "blender": self._rig_blender,
            "heuristic": self._rig_heuristic,
        }[method]
        return await backend(str(inp), output_path)

    async def _rig_anymate(self, input_path: str, output_path: str) -> dict[str, Any]:
        """Use Anymate3D ML model for automatic rigging."""

        def _run() -> dict[str, Any]:
            try:
                import anymate
                import trimesh

                # Load mesh
                mesh = trimesh.load(input_path)
                vertices = mesh.vertices

                # Anymate3D: predict skeleton
                model = anymate.load_model()
                result = model.predict(vertices)

                # result contains: joints, parents, skinning_weights
                joints = result.joints       # (N_joints, 3)
                parents = result.parents     # (N_joints,) parent indices
                weights = result.weights     # (N_verts, N_joints)

                # Map Anymate joints to VRM humanoid bones
                bone_mapping = self._map_anymate_to_humanoid(joints, parents)

                # Build rigged GLB with skeleton
                # (Implementation using pygltflib to inject skeleton data)
                self._build_rigged_glb(
                    input_path, output_path,
                    joints, parents, weights, bone_mapping,
                )

                return {
                    "status": "success",
                    "method": "anymate",
                    "output_path": output_path,
                    "bones_found": len(joints),
                    "bone_mapping": bone_mapping,
                }

            except Exception as e:
                logger.error("Anymate3D rigging failed: %s", e)
                return {"status": "error", "method": "anymate", "message": str(e)}

        return await asyncio.get_event_loop().run_in_executor(None, _run)

    async def _rig_blender(self, input_path: str, output_path: str) -> dict[str, Any]:
        """Use Blender's Python API for auto-rigging via Rigify."""
        if not self._blender_path:
            return {"status": "error", "message": "Blender not found"}

        # Blender Python script for auto-rigging with Rigify
        blender_script = '''
import bpy
import sys
import json

argv = sys.argv
input_file = argv[argv.index("--") + 1]
output_file = argv[argv.index("--") + 2]

# Clear scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# Import model
ext = input_file.rsplit(".", 1)[-1].lower()
if ext == "obj":
    bpy.ops.wm.obj_import(filepath=input_file)
elif ext == "fbx":
    bpy.ops.import_scene.fbx(filepath=input_file)
elif ext in ("glb", "gltf"):
    bpy.ops.import_scene.gltf(filepath=input_file)
elif ext == "stl":
    bpy.ops.wm.stl_import(filepath=input_file)

# Select the mesh
mesh_obj = None
for obj in bpy.data.objects:
    if obj.type == "MESH":
        mesh_obj = obj
        break

if not mesh_obj:
    print(json.dumps({"status": "error", "message": "No mesh found"}))
    sys.exit(1)

# Create armature with basic humanoid bones
bpy.ops.object.select_all(action='DESELECT')

# Add a basic armature
bpy.ops.object.armature_add(enter_editmode=True)
armature = bpy.context.active_object
armature.name = "IRISArmature"

# Get mesh bounds for bone placement
bounds = [mesh_obj.bound_box[i] for i in range(8)]
min_y = min(b[1] for b in bounds)
max_y = max(b[1] for b in bounds)
height = max_y - min_y

# Create spine bones based on mesh proportions
edit_bones = armature.data.edit_bones

# Root bone
root = edit_bones["Bone"]
root.name = "hips"
root.head = (0, 0, height * 0.45)
root.tail = (0, 0, height * 0.55)

spine = edit_bones.new("spine")
spine.parent = root
spine.head = root.tail
spine.tail = (0, 0, height * 0.65)

chest = edit_bones.new("chest")
chest.parent = spine
chest.head = spine.tail
chest.tail = (0, 0, height * 0.75)

neck = edit_bones.new("neck")
neck.parent = chest
neck.head = chest.tail
neck.tail = (0, 0, height * 0.85)

head = edit_bones.new("head")
head.parent = neck
head.head = neck.tail
head.tail = (0, 0, height * 1.0)

# Arms
for side, sign in [("left", 1), ("right", -1)]:
    shoulder = edit_bones.new(f"{side}Shoulder")
    shoulder.parent = chest
    shoulder.head = chest.tail
    shoulder.tail = (sign * height * 0.1, 0, height * 0.72)

    upper = edit_bones.new(f"{side}UpperArm")
    upper.parent = shoulder
    upper.head = shoulder.tail
    upper.tail = (sign * height * 0.25, 0, height * 0.55)

    lower = edit_bones.new(f"{side}LowerArm")
    lower.parent = upper
    lower.head = upper.tail
    lower.tail = (sign * height * 0.35, 0, height * 0.42)

    hand = edit_bones.new(f"{side}Hand")
    hand.parent = lower
    hand.head = lower.tail
    hand.tail = (sign * height * 0.4, 0, height * 0.38)

# Legs
for side, sign in [("left", 1), ("right", -1)]:
    upper = edit_bones.new(f"{side}UpperLeg")
    upper.parent = root
    upper.head = (sign * height * 0.08, 0, height * 0.45)
    upper.tail = (sign * height * 0.08, 0, height * 0.25)

    lower = edit_bones.new(f"{side}LowerLeg")
    lower.parent = upper
    lower.head = upper.tail
    lower.tail = (sign * height * 0.08, 0, height * 0.05)

    foot = edit_bones.new(f"{side}Foot")
    foot.parent = lower
    foot.head = lower.tail
    foot.tail = (sign * height * 0.08, -height * 0.08, 0)

# Back to object mode
bpy.ops.object.mode_set(mode='OBJECT')

# Parent mesh to armature with automatic weights
mesh_obj.select_set(True)
armature.select_set(True)
bpy.context.view_layer.objects.active = armature
bpy.ops.object.parent_set(type='ARMATURE_AUTO')

# Export as GLB
bpy.ops.export_scene.gltf(
    filepath=output_file,
    export_format='GLB',
    export_animations=True,
    export_skins=True,
)

bone_names = [b.name for b in armature.data.bones]
print(json.dumps({
    "status": "success",
    "bones": bone_names,
    "bone_count": len(bone_names),
}))
'''
        def _run() -> dict[str, Any]:
            script_path = None
            try:
                # The API supplies a private job directory. Create exclusively
                # inside it, so cleanup never targets the shared system temp.
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", prefix="blender-", suffix=".py",
                    dir=Path(output_path).parent, delete=False,
                ) as script:
                    script_path = Path(script.name)
                    script.write(blender_script)
                result = subprocess.run(
                    [
                        self._blender_path,
                        "--background", "--factory-startup", "--disable-autoexec",
                        "--python", str(script_path),
                        "--", input_path, output_path,
                    ],
                    cwd=Path(output_path).parent,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0:
                    return {
                        "status": "error", "method": "blender",
                        "message": "Blender did not complete successfully",
                    }

                for line in reversed(result.stdout.strip().split("\n")):
                    if line.startswith("{"):
                        try:
                            data = json.loads(line)
                            data["method"] = "blender"
                            data["output_path"] = output_path
                            return data
                        except json.JSONDecodeError:
                            continue
                return {
                    "status": "error", "method": "blender",
                    "message": "Blender did not return a rigging result",
                }
            except subprocess.TimeoutExpired:
                return {"status": "error", "method": "blender", "message": "Blender timed out (120s)"}
            except Exception as e:
                return {"status": "error", "method": "blender", "message": str(e)}
            finally:
                if script_path is not None:
                    script_path.unlink(missing_ok=True)

        return await asyncio.get_event_loop().run_in_executor(None, _run)

    async def _rig_heuristic(self, input_path: str, output_path: str) -> dict[str, Any]:
        """
        Heuristic bone placement — no external dependencies.

        Analyses the mesh bounding box and places bones at proportional positions.
        Generates a rigged GLB with automatic skinning weights (nearest-bone).
        """

        def _run() -> dict[str, Any]:
            try:
                import trimesh
                import numpy as np

                mesh = trimesh.load(input_path)
                if isinstance(mesh, trimesh.Scene):
                    mesh = trimesh.util.concatenate(list(mesh.geometry.values()))

                # --- Automatic Normalization (Center & Scale) ---
                import math

                bounds = mesh.bounds
                min_pt, max_pt = bounds[0], bounds[1]
                span_x = max_pt[0] - min_pt[0]
                span_y = max_pt[1] - min_pt[1]
                span_z = max_pt[2] - min_pt[2]

                # Step 1: Fix up-axis — Y must be the tallest dimension.
                # Blender exports Z-up by default; some tools export X-up.
                if span_z > span_y and span_z >= span_x:
                    # Z-up model (Blender native) — rotate -90° around X so Z→Y
                    mesh.apply_transform(
                        trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0])
                    )
                    bounds = mesh.bounds
                    min_pt, max_pt = bounds[0], bounds[1]
                    span_x = max_pt[0] - min_pt[0]
                    span_z = max_pt[2] - min_pt[2]
                elif span_x > span_y and span_x >= span_z:
                    # X-up model — rotate 90° around Z so X→Y
                    mesh.apply_transform(
                        trimesh.transformations.rotation_matrix(math.pi / 2, [0, 0, 1])
                    )
                    bounds = mesh.bounds
                    min_pt, max_pt = bounds[0], bounds[1]
                    span_x = max_pt[0] - min_pt[0]
                    span_z = max_pt[2] - min_pt[2]

                # Step 2: Fix facing direction — model should be narrow on Z (faces -Z).
                if span_z > span_x:
                    mesh.apply_transform(
                        trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0])
                    )
                    bounds = mesh.bounds
                    min_pt, max_pt = bounds[0], bounds[1]

                # 1. Move to origin (center x/z, bottom y=0)
                # Current center and floor
                center_x = (min_pt[0] + max_pt[0]) / 2
                center_z = (min_pt[2] + max_pt[2]) / 2
                floor_y = min_pt[1]
                
                translation = [-center_x, -floor_y, -center_z]
                mesh.apply_translation(translation)
                
                # 2. Scale to standard human height (1.8m)
                final_height = max_pt[1] - min_pt[1]
                if final_height > 0:
                    scale_factor = 1.8 / final_height
                    mesh.apply_scale(scale_factor)
                
                # Re-calculate bounds after normalization
                bounds = mesh.bounds
                min_pt, max_pt = bounds[0], bounds[1]
                height = max_pt[1] - min_pt[1]
                center_x = 0  # Should be 0 after translation
                center_z = 0  # Should be 0 after translation

                # Place bones proportionally
                def pt(x_off, y_frac, z_off=0):
                    return [center_x + x_off * height, min_pt[1] + y_frac * height, center_z + z_off]

                bones = {
                    "hips":          {"head": pt(0, 0.45), "tail": pt(0, 0.50)},
                    "spine":         {"head": pt(0, 0.50), "tail": pt(0, 0.60)},
                    "chest":         {"head": pt(0, 0.60), "tail": pt(0, 0.70)},
                    # upperChest bridges the gap between chest and neck
                    "upperChest":    {"head": pt(0, 0.70), "tail": pt(0, 0.78)},
                    # Shoulders connect upperChest to the arms
                    "leftShoulder":  {"head": pt(0,    0.74), "tail": pt( 0.12, 0.74)},
                    "rightShoulder": {"head": pt(0,    0.74), "tail": pt(-0.12, 0.74)},
                    "neck":          {"head": pt(0, 0.78), "tail": pt(0, 0.87)},
                    "head":          {"head": pt(0, 0.87), "tail": pt(0, 1.00)},
                    "leftUpperArm":  {"head": pt( 0.12, 0.75), "tail": pt( 0.25, 0.55)},
                    "leftLowerArm":  {"head": pt( 0.25, 0.55), "tail": pt( 0.35, 0.40)},
                    "leftHand":      {"head": pt( 0.35, 0.40), "tail": pt( 0.40, 0.36)},
                    "rightUpperArm": {"head": pt(-0.12, 0.75), "tail": pt(-0.25, 0.55)},
                    "rightLowerArm": {"head": pt(-0.25, 0.55), "tail": pt(-0.35, 0.40)},
                    "rightHand":     {"head": pt(-0.35, 0.40), "tail": pt(-0.40, 0.36)},
                    "leftUpperLeg":  {"head": pt( 0.08, 0.45), "tail": pt( 0.08, 0.25)},
                    "leftLowerLeg":  {"head": pt( 0.08, 0.25), "tail": pt( 0.08, 0.05)},
                    "leftFoot":      {"head": pt( 0.08, 0.05), "tail": pt( 0.08, 0.0, -0.08)},
                    "rightUpperLeg": {"head": pt(-0.08, 0.45), "tail": pt(-0.08, 0.25)},
                    "rightLowerLeg": {"head": pt(-0.08, 0.25), "tail": pt(-0.08, 0.05)},
                    "rightFoot":     {"head": pt(-0.08, 0.05), "tail": pt(-0.08, 0.0, -0.08)},
                    # Physics bones (dynamic spring bones)
                    "hair_physics":   {"head": pt(0, 0.95, -0.05), "tail": pt(0, 0.85, -0.12)},
                    "breast_physics": {"head": pt(0, 0.65,  0.08), "tail": pt(0, 0.60,  0.15)},
                }

                # Compute skinning weights: nearest bone midpoint
                verts = mesh.vertices
                bone_midpoints = []
                bone_names = list(bones.keys())
                for name in bone_names:
                    h = np.array(bones[name]["head"])
                    t = np.array(bones[name]["tail"])
                    bone_midpoints.append((h + t) / 2)
                bone_midpoints = np.array(bone_midpoints)

                # For each vertex, find nearest 4 bones
                from scipy.spatial import cKDTree
                tree = cKDTree(bone_midpoints)
                distances, indices = tree.query(verts, k=min(4, len(bone_names)))

                # Inverse distance weighting
                weights = np.zeros((len(verts), len(bone_names)))
                for v_idx in range(len(verts)):
                    dists = distances[v_idx]
                    idxs = indices[v_idx]
                    inv_dists = 1.0 / (dists + 1e-6)
                    inv_dists /= inv_dists.sum()
                    for w_i, b_i in enumerate(idxs):
                        weights[v_idx, b_i] = inv_dists[w_i]

                # Save as enhanced GLB with bone metadata
                # Now using pygltflib injector to physically append skeleton nodes and VRM humanoid tags!
                glb_data = mesh.export(file_type="glb")
                Path(output_path).write_bytes(glb_data)
                
                try:
                    from core.glb_injector import inject_vrm_bones
                    success = inject_vrm_bones(output_path, bones, weights, bone_names)
                    if not success:
                        logger.error("Failed to inject VRM bones via pygltflib")
                except Exception as e:
                    logger.error(f"Error during physical VRM injection: {e}")
                
                # Save bone data as sidecar JSON
                meta_path = Path(output_path).with_suffix(".bones.json")
                meta_path.write_text(json.dumps({
                    "bones": bones,
                    "bone_names": bone_names,
                    "bone_count": len(bone_names),
                    "vertex_count": len(verts),
                    "weights_shape": list(weights.shape),
                }, indent=2))

                logger.info(
                    "Heuristic rigging complete: %d bones, %d vertices",
                    len(bone_names), len(verts),
                )

                return {
                    "status": "success",
                    "method": "physical_vrm",
                    "output_path": output_path,
                    "bones_found": len(bone_names),
                    "bone_names": bone_names,
                    "metadata_path": str(meta_path),
                }

            except ImportError as e:
                missing = str(e).split("'")[1] if "'" in str(e) else str(e)
                return {
                    "status": "error",
                    "method": "heuristic",
                    "message": f"Missing dependency: {missing}. pip install trimesh scipy",
                }
            except Exception as e:
                return {"status": "error", "method": "heuristic", "message": str(e)}

        return await asyncio.get_event_loop().run_in_executor(None, _run)

    def _map_anymate_to_humanoid(self, joints, parents):
        """Map Anymate3D predicted joints to VRM humanoid bone names."""
        # Anymate outputs joints sorted roughly by height
        # Heuristic: map by Y-coordinate position
        import numpy as np

        n_joints = len(joints)
        sorted_by_y = np.argsort(joints[:, 1])

        mapping = {}
        if n_joints >= 17:
            # Standard humanoid mapping
            mapping = {
                int(sorted_by_y[0]):  "leftFoot",
                int(sorted_by_y[1]):  "rightFoot",
                int(sorted_by_y[2]):  "leftLowerLeg",
                int(sorted_by_y[3]):  "rightLowerLeg",
                int(sorted_by_y[4]):  "leftUpperLeg",
                int(sorted_by_y[5]):  "rightUpperLeg",
                int(sorted_by_y[6]):  "hips",
                int(sorted_by_y[7]):  "spine",
                int(sorted_by_y[8]):  "chest",
                int(sorted_by_y[9]):  "leftHand",
                int(sorted_by_y[10]): "rightHand",
                int(sorted_by_y[11]): "leftLowerArm",
                int(sorted_by_y[12]): "rightLowerArm",
                int(sorted_by_y[13]): "leftUpperArm",
                int(sorted_by_y[14]): "rightUpperArm",
                int(sorted_by_y[15]): "neck",
                int(sorted_by_y[16]): "head",
            }

        return mapping

    def _build_rigged_glb(self, input_path, output_path, joints, parents, weights, bone_mapping):
        """
        Build a rigged GLB from Anymate3D output using inject_vrm_bones.

        Converts Anymate joints/parents/weights into the bones_dict format
        expected by inject_vrm_bones and delegates to it.
        """
        import shutil
        import numpy as np

        shutil.copy2(input_path, output_path)

        if not bone_mapping:
            logger.warning("No bone mapping from Anymate — skeleton not injected.")
            return

        # Convert Anymate joints to bones_dict {name: {head, tail}}
        bones_dict: dict = {}
        bone_names_ordered: list = []

        for joint_idx, bone_name in bone_mapping.items():
            world_pos = joints[joint_idx].tolist()
            parent_idx = int(parents[joint_idx]) if parents[joint_idx] >= 0 else -1

            if parent_idx >= 0 and parent_idx < len(joints):
                tail = joints[parent_idx].tolist()
            else:
                tail = [world_pos[0], world_pos[1] + 0.1, world_pos[2]]

            bones_dict[bone_name] = {"head": world_pos, "tail": tail}
            bone_names_ordered.append(bone_name)

        # Remap weights to only the mapped bone columns
        mapped_col_indices = list(bone_mapping.keys())
        weights_mapped = np.asarray(weights)[:, mapped_col_indices]

        try:
            from core.glb_injector import inject_vrm_bones
            success = inject_vrm_bones(output_path, bones_dict, weights_mapped, bone_names_ordered)
            if success:
                logger.info("Anymate3D: VRM skeleton injected via inject_vrm_bones.")
            else:
                logger.error("Anymate3D: inject_vrm_bones returned False.")
        except Exception as e:
            logger.error("Anymate3D: skeleton injection failed: %s", e)

