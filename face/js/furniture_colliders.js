import * as THREE from 'three';

/**
 * The room's furniture as solid boxes for hair and clothes.
 *
 * Without them, hair, ribbons, skirts and capes fell through the bed, the desk and the sofa
 * (measured with every test avatar, 2026-11-10). Each kind of avatar keeps its own physics, as
 * with the PMX lights and physics: a VRM's spring bones get these boxes as colliders here; a
 * PMX's MMD physics gets PMX_FURNITURE_BOXES (a thinner sofa seat) as kinematic bodies with no
 * speed in its worker.
 *
 * Boxes are axis-aligned, in room (world) units, measured on room.js' models with rays from
 * above and from the side. A box only stops what is inside it: hair hanging past the bed's edge
 * still falls. They are bounded on purpose (an endless plane at the mattress would hold hair up in
 * the air beside the bed). Move them with the furniture.
 */
export const FURNITURE_BOXES = Object.freeze([
    // The bed: mattress and blanket (top -0.55), headboard, pillows.
    Object.freeze({ name: 'bed', min: [2.64, -1.2, 0.1], max: [4.72, -0.56, 3.6] }),
    Object.freeze({ name: 'headboard', min: [2.64, -1.2, 0.1], max: [4.72, 0.5, 0.4] }),
    Object.freeze({ name: 'pillows', min: [2.9, -0.56, 0.4], max: [4.45, -0.33, 0.72] }),
    // The desk's top (a slab: the chair and her knees go under it) and its raised back.
    Object.freeze({ name: 'desk', min: [1.5, -0.3, -3.23], max: [4.0, -0.16, -2.07] }),
    Object.freeze({ name: 'desk_back', min: [1.85, -0.16, -3.23], max: [3.4, 0.0, -2.9] }),
    // The sofa: seat, back and arms.
    Object.freeze({ name: 'sofa_seat', min: [-4.97, -1.2, 1.0], max: [-1.59, -0.63, 1.95] }),
    Object.freeze({ name: 'sofa_back', min: [-4.97, -1.2, 0.39], max: [-1.59, -0.14, 1.0] }),
    Object.freeze({ name: 'sofa_arm_left', min: [-4.97, -1.2, 0.39], max: [-4.55, -0.52, 2.0] }),
    Object.freeze({ name: 'sofa_arm_right', min: [-1.95, -1.2, 0.39], max: [-1.59, -0.52, 2.0] }),
    Object.freeze({ name: 'coffee_table', min: [-4.3, -1.2, 2.28], max: [-2.3, -0.76, 3.04] }),
    // The floor, under all of it.
    Object.freeze({ name: 'floor', min: [-8, -3, -8], max: [8, -1.2, 8] }),
]);

// A PMX ribbon can be anchored below the cushion's top and kept in contact by a full-depth
// box. Keep the supporting surface and leave room for ribbons below it.
export const PMX_FURNITURE_BOXES = Object.freeze(FURNITURE_BOXES.map(box => box.name === 'sofa_seat'
    ? Object.freeze({ ...box, min: [box.min[0], box.max[1] - 0.08, box.min[2]] })
    : box));

/**
 * A box as a spring-bone collider shape. The joints test their tails in world space, so the box
 * is too, whatever the collider's own matrix. Inside it (grown by the joint's radius), a tail goes
 * out the nearest face.
 */
export class FurnitureBoxShape {
    constructor(box) {
        this.name = box.name;
        this.min = new THREE.Vector3(...box.min);
        this.max = new THREE.Vector3(...box.max);
        this.offset = null;
    }

    get type() {
        return 'furniture-box';
    }

    calculateCollision(_matrix, point, radius, target) {
        const r = radius || 0;
        const depths = [
            point.x - (this.min.x - r), (this.max.x + r) - point.x,
            point.y - (this.min.y - r), (this.max.y + r) - point.y,
            point.z - (this.min.z - r), (this.max.z + r) - point.z,
        ];
        let face = 0;
        for (let i = 0; i < 6; i++) {
            if (depths[i] <= 0) return 1;            // outside: no contact
            if (depths[i] < depths[face]) face = i;
        }
        const axis = face >> 1, sign = face & 1 ? 1 : -1;
        target.set(0, 0, 0).setComponent(axis, sign);
        return -depths[face];
    }
}

const GROUP_NAME = 'iris-furniture';

/**
 * Give a VRM's spring bones the furniture. The colliders hang from her hips bone, so the order in
 * which three-vrm updates her joints keeps to her own skeleton. Once per avatar; true when added.
 */
export function addFurnitureColliders(vrm, boxes = FURNITURE_BOXES) {
    const manager = vrm?.springBoneManager;
    const joints = manager?.joints;
    const anchor = vrm?.humanoid?.getRawBoneNode?.('hips');
    if (!joints?.size || !anchor) return false;
    const first = joints.values().next().value;
    if (first.colliderGroups.some(group => group.name === GROUP_NAME)) return false;
    const colliders = boxes.map((box) => {
        const collider = new THREE.Object3D();
        collider.name = `furniture_${box.name}`;
        collider.shape = new FurnitureBoxShape(box);
        collider.colliderMatrix = new THREE.Matrix4();
        anchor.add(collider);
        return collider;
    });
    const group = { name: GROUP_NAME, colliders };
    for (const joint of joints) {
        joint.colliderGroups = [...joint.colliderGroups, group];
        manager.addJoint(joint);                      // marks the joint order to be worked out again
    }
    return true;
}
