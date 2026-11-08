/**
 * IRIS' skirt while she sits.
 *
 * The skirt hangs from the hips on spring bones. Sitting raises the thighs through its front
 * panels, and the leg colliders push those panels to the nearest side: between her legs. Here the
 * panels' rest pose turns forward as she sits, so the thighs carry them on top, and the spring
 * still moves them. Other avatars have no bones with these names, and nothing changes for them.
 */

import * as THREE from 'three';

// Degrees about the hips' X axis at full weight. Negative lifts a panel towards the knees.
const PANELS = Object.freeze({
    skirt_frontL: -52,
    skirt_front01L: -14,
    skirt_frontR: -52,
    skirt_front01R: -14,
});

export class SeatDrape {
    constructor(vrm) {
        this.weight = 0;
        const joints = [...(vrm?.springBoneManager?.joints || [])];
        const hips = vrm?.humanoid?.getRawBoneNode?.('hips');
        const restOf = (bone) => joints.find((joint) => joint.bone === bone)?._initialLocalRotation;
        this.panels = joints.filter((joint) => joint.bone.name in PANELS).map((joint) => {
            // The turn is about the hips' axis, so bring it into the frame of this joint's parent.
            const parentInHips = new THREE.Quaternion();
            for (let node = joint.bone.parent; node && node !== hips; node = node.parent) {
                const rest = restOf(node);
                if (rest) parentInHips.premultiply(rest);
            }
            const angle = THREE.MathUtils.degToRad(PANELS[joint.bone.name]);
            const turn = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), angle);
            const local = parentInHips.clone().invert().multiply(turn).multiply(parentInHips);
            const rest = joint._initialLocalRotation.clone();
            return { joint, rest, draped: local.multiply(rest) };
        });
    }

    /** 0 is the rest pose of the file, 1 the panels over her thighs. */
    set(weight) {
        if (weight === this.weight) return;
        this.weight = weight;
        for (const { joint, rest, draped } of this.panels) {
            const rotation = joint._initialLocalRotation.slerpQuaternions(rest, draped, weight);
            joint._initialLocalMatrix.compose(joint.bone.position, rotation, joint.bone.scale);
        }
    }
}
