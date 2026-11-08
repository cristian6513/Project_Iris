import numpy as np

def pack_skinning_data(weights_matrix, bone_indices_map):
    """
    Packs a (N_verts, N_bones) weight matrix into GLTF-ready binary chunks.
    Returns (joints_bytes, weights_bytes).
    """
    n_verts = weights_matrix.shape[0]

    # We need 4 joints and 4 weights per vertex
    packed_joints = np.zeros((n_verts, 4), dtype=np.uint16)
    packed_weights = np.zeros((n_verts, 4), dtype=np.float32)
    
    for i in range(n_verts):
        row = weights_matrix[i]
        # Get indices of top 4 weights
        top4_idx = np.argsort(row)[-4:][::-1]
        top4_val = row[top4_idx]
        
        # Normalize to sum to 1.0
        s = top4_val.sum()
        if s > 0:
            top4_val = top4_val / s
        else:
            # Fallback for unconnected vertices (all to hips)
            top4_idx = np.array([0, 0, 0, 0])
            top4_val = np.array([1.0, 0, 0, 0])
            
        packed_joints[i] = top4_idx
        packed_weights[i] = top4_val
        
    return packed_joints.tobytes(), packed_weights.tobytes()
