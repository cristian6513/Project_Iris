import numpy as np

try:
    import pygltflib
except ImportError:  # Optional ``rigger`` extra; the rest of Core stays usable.
    pygltflib = None

from core.skin_packer import pack_skinning_data

def inject_vrm_bones(glb_path, bones_dict, weights_matrix=None, bone_names=None):
    """
    Advanced Injector: Adds Bones AND Skinning Weights to a static GLB.
    """
    if pygltflib is None:
        print("Auto-rigging requires the optional IRIS rigger dependencies.")
        return False
    try:
        gltf = pygltflib.GLTF2().load(glb_path)
    except Exception as e:
        print(f"Error loading {glb_path}: {e}")
        return False

    # --- 1. Nodes & Hierarchy ---
    node_offset = len(gltf.nodes)
    bone_indices = {}
    new_bone_names = list(bones_dict.keys())

    bone_hierarchy = {
        "hips":          ["spine", "leftUpperLeg", "rightUpperLeg"],
        "spine":         ["chest"],
        "chest":         ["upperChest", "breast_physics"],
        "upperChest":    ["neck", "leftShoulder", "rightShoulder"],
        "neck":          ["head"],
        "head":          ["hair_root"],
        "hair_root":     ["hair_mid"],
        "hair_mid":      ["hair_tip"],
        "leftShoulder":  ["leftUpperArm"],
        "rightShoulder": ["rightUpperArm"],
        "leftUpperArm":  ["leftLowerArm"],
        "leftLowerArm":  ["leftHand"],
        "rightUpperArm": ["rightLowerArm"],
        "rightLowerArm": ["rightHand"],
        "leftUpperLeg":  ["leftLowerLeg"],
        "leftLowerLeg":  ["leftFoot"],
        "rightUpperLeg": ["rightLowerLeg"],
        "rightLowerLeg": ["rightFoot"],
    }

    # Build inverse lookup (child -> parent)
    parent_map = {}
    for parent, children in bone_hierarchy.items():
        for child in children:
            parent_map[child] = parent

    # Add hair chain bones if not already in bones_dict
    _inject_hair_chain(bones_dict, new_bone_names)

    for i, name in enumerate(new_bone_names):
        if name not in bones_dict:
            continue
        world_head = np.array(bones_dict[name]["head"])

        parent_name = parent_map.get(name)
        if parent_name and parent_name in bones_dict:
            parent_world_head = np.array(bones_dict[parent_name]["head"])
            local_pos = (world_head - parent_world_head).tolist()
        else:
            local_pos = world_head.tolist()

        node = pygltflib.Node(name=name, translation=local_pos)
        gltf.nodes.append(node)
        bone_indices[name] = node_offset + len(bone_indices)

    for parent, children in bone_hierarchy.items():
        if parent in bone_indices:
            idxs = [bone_indices[c] for c in children if c in bone_indices]
            if idxs:
                gltf.nodes[bone_indices[parent]].children = idxs

    # Scene
    if "hips" in bone_indices:
        if not gltf.scenes:
            gltf.scenes = [pygltflib.Scene(nodes=[])]
        scene = gltf.scenes[gltf.scene if gltf.scene is not None else 0]
        if bone_indices["hips"] not in scene.nodes:
            scene.nodes.append(bone_indices["hips"])

    # --- 2. Skinning & Binary Injection ---
    if weights_matrix is not None and bone_names is not None:
        # Remap weights to include only bones actually in bone_indices
        active_bone_names = [n for n in bone_names if n in bone_indices]
        active_col_indices = [bone_names.index(n) for n in active_bone_names]
        weights_active = weights_matrix[:, active_col_indices]

        joints_bytes, weights_bytes = pack_skinning_data(weights_active, active_bone_names)

        # --- Robust binary blob access (works across all pygltflib versions) ---
        blob = _get_blob(gltf)
        if blob is None:
            blob = b""

        # Align to 4 bytes
        blob, start_offset = _align4(blob)
        blob += joints_bytes
        joints_len = len(joints_bytes)

        blob, weights_offset = _align4(blob)
        blob += weights_bytes
        weights_len = len(weights_bytes)

        # Inverse Bind Matrices
        ibm_data = []
        for name in active_bone_names:
            pos = bones_dict[name]["head"]
            matrix = np.eye(4, dtype=np.float32)
            matrix[0:3, 3] = -np.array(pos, dtype=np.float32)
            ibm_data.append(matrix.flatten(order='F'))

        ibm_bytes = np.array(ibm_data, dtype=np.float32).tobytes()
        blob, ibm_offset = _align4(blob)
        blob += ibm_bytes
        ibm_len = len(ibm_bytes)

        # Commit blob before building accessors
        _set_blob(gltf, blob)

        # BufferViews
        bv_joints = pygltflib.BufferView(buffer=0, byteOffset=start_offset, byteLength=joints_len, target=34962)
        bv_weights = pygltflib.BufferView(buffer=0, byteOffset=weights_offset, byteLength=weights_len, target=34962)
        bv_ibm    = pygltflib.BufferView(buffer=0, byteOffset=ibm_offset,    byteLength=ibm_len)
        gltf.bufferViews += [bv_joints, bv_weights, bv_ibm]
        bv_joints_idx  = len(gltf.bufferViews) - 3
        bv_weights_idx = len(gltf.bufferViews) - 2
        bv_ibm_idx     = len(gltf.bufferViews) - 1

        n_verts = weights_matrix.shape[0]
        acc_joints = pygltflib.Accessor(bufferView=bv_joints_idx,  componentType=5123, count=n_verts, type="VEC4")
        acc_weights= pygltflib.Accessor(bufferView=bv_weights_idx, componentType=5126, count=n_verts, type="VEC4")
        acc_ibm    = pygltflib.Accessor(bufferView=bv_ibm_idx,     componentType=5126, count=len(active_bone_names), type="MAT4")
        gltf.accessors += [acc_joints, acc_weights, acc_ibm]
        acc_joints_idx  = len(gltf.accessors) - 3
        acc_weights_idx = len(gltf.accessors) - 2
        acc_ibm_idx     = len(gltf.accessors) - 1

        joint_node_indices = [bone_indices[n] for n in active_bone_names]
        skin = pygltflib.Skin(
            joints=joint_node_indices,
            skeleton=bone_indices.get("hips"),
            inverseBindMatrices=acc_ibm_idx,
        )
        gltf.skins.append(skin)
        skin_index = len(gltf.skins) - 1

        if gltf.meshes:
            for prim in gltf.meshes[0].primitives:
                if prim.attributes is None:
                    prim.attributes = pygltflib.Attributes()
                prim.attributes.JOINTS_0  = acc_joints_idx
                prim.attributes.WEIGHTS_0 = acc_weights_idx

            for node in gltf.nodes:
                if node.mesh == 0:
                    node.skin = skin_index

    # --- 3. VRM 1.0 Metadata ---
    if gltf.extensionsUsed is None:
        gltf.extensionsUsed = []
    for ext in ["VRM", "VRMC_vrm", "VRMC_springBone"]:
        if ext in gltf.extensionsUsed:
            gltf.extensionsUsed.remove(ext)
    gltf.extensionsUsed += ["VRMC_vrm", "VRMC_springBone"]

    if gltf.extensions is None:
        gltf.extensions = {}

    # Humanoid bones (exclude physics/helper bones)
    _physics_bones = {"hair_root", "hair_mid", "hair_tip", "breast_physics"}
    human_bones = {
        name: {"node": bone_indices[name]}
        for name in new_bone_names
        if name in bone_indices and name not in _physics_bones
    }

    gltf.extensions["VRMC_vrm"] = {
        "specVersion": "1.0",
        "meta": {
            "name": "Project IRIS Avatar",
            "authors": ["IRIS Auto-Rigger"],
            "licenseUrl": "https://vrm.dev/licenses/1.0/",
            "avatarPermission": "everyone",
            "commercialUsage": "personalProfit",
        },
        "humanoid": {"humanBones": human_bones},
        "expressions": {
            "preset": {
                name: {"morphTargetBinds": [], "overrideBlink": "none",
                       "overrideLookAt": "none", "overrideMouth": "none"}
                for name in [
                    "happy", "angry", "sad", "relaxed", "surprised",
                    "blink", "blinkLeft", "blinkRight",
                    "lookUp", "lookDown", "lookLeft", "lookRight",
                    "aa", "ih", "ou", "ee", "oh",
                ]
            },
            "custom": {},
        },
        "lookAt": {
            "offsetFromHeadBone": [0.0, 0.06, 0.0],
            "type": "bone",
            "rangeMapHorizontalInner": {"inputMaxValue": 90.0, "outputScale": 10.0},
            "rangeMapHorizontalOuter": {"inputMaxValue": 90.0, "outputScale": 10.0},
            "rangeMapVerticalDown":    {"inputMaxValue": 90.0, "outputScale": 10.0},
            "rangeMapVerticalUp":      {"inputMaxValue": 90.0, "outputScale": 10.0},
        },
        "firstPerson": {"meshAnnotations": []},
    }

    # Spring bone chains
    springs = []
    if all(k in bone_indices for k in ("hair_root", "hair_mid", "hair_tip")):
        springs.append({
            "name": "HairChain",
            "joints": [
                {"node": bone_indices["hair_root"], "stiffness": 1.0, "dragForce": 0.3, "gravityPower": 0.08, "gravityDir": [0, -1, 0], "hitRadius": 0.03},
                {"node": bone_indices["hair_mid"],  "stiffness": 0.6, "dragForce": 0.3, "gravityPower": 0.10, "gravityDir": [0, -1, 0], "hitRadius": 0.02},
                {"node": bone_indices["hair_tip"],  "stiffness": 0.3, "dragForce": 0.2, "gravityPower": 0.12, "gravityDir": [0, -1, 0], "hitRadius": 0.01},
            ],
        })
    if "breast_physics" in bone_indices:
        springs.append({
            "name": "BreastPhysics",
            "joints": [
                {"node": bone_indices["breast_physics"], "stiffness": 0.4, "dragForce": 0.6, "gravityPower": 0.03, "gravityDir": [0, -1, 0], "hitRadius": 0.04},
            ],
        })

    if springs:
        gltf.extensions["VRMC_springBone"] = {
            "specVersion": "1.0",
            "springs": springs,
        }

    # Update buffer length
    final_blob = _get_blob(gltf) or b""
    if gltf.buffers:
        gltf.buffers[0].byteLength = len(final_blob)
    else:
        gltf.buffers.append(pygltflib.Buffer(byteLength=len(final_blob)))

    gltf.save_binary(glb_path)
    return True


# ── Helpers ──────────────────────────────────────────────────

def _get_blob(gltf) -> bytes | None:
    """Retrieve binary blob across pygltflib versions."""
    if hasattr(gltf, '_glb_data'):
        return gltf._glb_data
    try:
        return gltf.binary_blob()
    except Exception:
        return None


def _set_blob(gltf, data: bytes) -> None:
    """Write binary blob across pygltflib versions."""
    if hasattr(gltf, 'set_binary_blob'):
        gltf.set_binary_blob(data)
    elif hasattr(gltf, '_glb_data'):
        gltf._glb_data = data
    else:
        # Last resort: replace callable attribute
        gltf.binary_blob = data


def _align4(blob: bytes) -> tuple[bytes, int]:
    """Pad blob to 4-byte alignment, return (new_blob, offset_of_next_write)."""
    pad = (4 - (len(blob) % 4)) % 4
    blob += b"\x00" * pad
    return blob, len(blob)


def _inject_hair_chain(bones_dict: dict, bone_names: list) -> None:
    """
    Derive hair_root / hair_mid / hair_tip from hair_physics if present,
    creating a 3-joint chain that produces visible spring bone oscillation.
    """
    if "hair_physics" not in bones_dict:
        return
    if "hair_root" in bones_dict:
        return  # already injected

    hp = bones_dict.pop("hair_physics")
    if "hair_physics" in bone_names:
        bone_names.remove("hair_physics")

    head_pos = np.array(hp["head"])
    tail_pos  = np.array(hp["tail"])
    direction = tail_pos - head_pos
    step = direction / 3.0

    bones_dict["hair_root"] = {"head": head_pos.tolist(),              "tail": (head_pos + step).tolist()}
    bones_dict["hair_mid"]  = {"head": (head_pos + step).tolist(),     "tail": (head_pos + step * 2).tolist()}
    bones_dict["hair_tip"]  = {"head": (head_pos + step * 2).tolist(), "tail": (head_pos + step * 3).tolist()}

    for name in ["hair_root", "hair_mid", "hair_tip"]:
        if name not in bone_names:
            bone_names.append(name)
