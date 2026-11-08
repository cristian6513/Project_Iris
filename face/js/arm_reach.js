/**
 * Hands that touch things, on any body. Clips store rotations, so on an avatar with other
 * proportions the same rotations leave the hands somewhere else: short of the keyboard, or off
 * the book. While a clip touches something this works out where IRIS' hand is on that frame,
 * from the clip's own rotations and her proportions, and bends the arm to put the hand there.
 *
 * Runs on the normalized rig, after the clip has written the pose. Only the upper arm, forearm and
 * hand change, by the smallest turn that does it, so the twist the clip gave them stays.
 */
import * as THREE from 'three';

// IRIS' rig in metres, in the normalized pose: the proportions the clips were made on.
export const REFERENCE = Object.freeze({
    scale: 1.4594,
    scene: [0.0018, -1.2245, 0],
    hips: [0, 0.8833, 0],
    spine: [0, 0.0718, 0],
    chest: [0, 0.223, 0],
    shoulder: [0.0244, 0.2061, 0],
    upperArm: [0.0817, -0.0198, -0.0412],
    lowerArm: [0.2316, 0, 0],
    hand: [0.3012, 0, 0],
});
const REACH_LIMIT = 0.999;       // of the arm's length: a dead straight arm gives no elbow

const SIDES = Object.freeze([
    Object.freeze({ sign: 1, shoulder: 'leftShoulder', upper: 'leftUpperArm', lower: 'leftLowerArm', hand: 'leftHand' }),
    Object.freeze({ sign: -1, shoulder: 'rightShoulder', upper: 'rightUpperArm', lower: 'rightLowerArm', hand: 'rightHand' }),
]);

const v1 = new THREE.Vector3(), v2 = new THREE.Vector3(), v3 = new THREE.Vector3();
const q1 = new THREE.Quaternion(), q2 = new THREE.Quaternion();

/** A clip was made for this body: nothing to correct. */
export function isReferenceBody(vrm) {
    const hips = vrm?.humanoid?.normalizedRestPose?.hips?.position;
    return Math.abs((vrm?.scene?.scale?.x ?? 0) - REFERENCE.scale) < 1e-3
        && Math.abs((hips?.[1] ?? 0) - REFERENCE.hips[1]) < 1e-3;
}

export class ArmReach {
    constructor(vrm, group) {
        this.vrm = vrm;
        this.group = group;
        this.weight = 0;
        this.active = !isReferenceBody(vrm);
        const hips = vrm.humanoid?.normalizedRestPose?.hips?.position?.[1];
        this.hipsRatio = hips ? hips / REFERENCE.hips[1] : 1;
        // A VRM 0 model looks the other way in its own space: so do the clips written to it.
        this.flip = String(vrm.meta?.metaVersion) === '0';
        this.bone = (name) => vrm.humanoid.getNormalizedBoneNode(name);
    }

    _offset(values, sign = 1) {
        const [x, y, z] = values;
        return this.flip ? v3.set(-x * sign, y, -z) : v3.set(x * sign, y, z);
    }

    /** Where IRIS has this hand now, in world space, and how it is turned. */
    reference(side, position, rotation) {
        const hips = this.bone('hips');
        position.copy(hips.position).divideScalar(this.hipsRatio);
        rotation.copy(hips.quaternion);
        const walk = (node, offset, sign) => {
            position.add(this._offset(offset, sign).applyQuaternion(rotation));
            if (node) rotation.multiply(node.quaternion);
        };
        walk(this.bone('spine'), REFERENCE.spine, 1);
        walk(this.bone('chest'), REFERENCE.chest, 1);
        walk(this.bone(side.shoulder), REFERENCE.shoulder, side.sign);
        walk(this.bone(side.upper), REFERENCE.upperArm, side.sign);
        walk(this.bone(side.lower), REFERENCE.lowerArm, side.sign);
        walk(this.bone(side.hand), REFERENCE.hand, side.sign);
        const group = this.group.getWorldQuaternion(q1);
        position.multiplyScalar(REFERENCE.scale);
        position.add(this._offset(REFERENCE.scene));
        position.applyQuaternion(group).add(this.group.getWorldPosition(v1));
        rotation.premultiply(group);
    }

    apply() {
        if (!this.active || this.weight <= 0) return;
        const root = this.bone('hips')?.parent;
        if (!root) return;
        root.updateWorldMatrix(true, true);
        for (const side of SIDES) this._reach(side);
    }

    _reach(side) {
        const upper = this.bone(side.upper), lower = this.bone(side.lower), hand = this.bone(side.hand);
        if (!upper || !lower || !hand) return;
        const target = new THREE.Vector3(), turned = new THREE.Quaternion();
        this.reference(side, target, turned);
        const shoulder = upper.getWorldPosition(new THREE.Vector3());
        const elbow = lower.getWorldPosition(new THREE.Vector3());
        const wrist = hand.getWorldPosition(new THREE.Vector3());
        const first = elbow.distanceTo(shoulder), second = wrist.distanceTo(elbow);
        target.lerp(wrist, 1 - this.weight);

        const line = target.clone().sub(shoulder);
        const room = (first + second) * REACH_LIMIT;
        const distance = THREE.MathUtils.clamp(line.length(), Math.abs(first - second) + 1e-4, room);
        const along = line.normalize();
        // The elbow keeps to the side the clip gave it.
        const side90 = elbow.clone().sub(shoulder);
        side90.addScaledVector(along, -side90.dot(along));
        if (side90.lengthSq() < 1e-8) side90.set(0, -1, 0).addScaledVector(along, along.y);
        side90.normalize();
        const a = (first * first - second * second + distance * distance) / (2 * distance);
        const height = Math.sqrt(Math.max(0, first * first - a * a));
        const newElbow = shoulder.clone().addScaledVector(along, a).addScaledVector(side90, height);
        const newWrist = shoulder.clone().addScaledVector(along, distance);

        const upperTurn = new THREE.Quaternion().setFromUnitVectors(
            v1.subVectors(elbow, shoulder).normalize(), v2.subVectors(newElbow, shoulder).normalize());
        const upperWorld = upper.getWorldQuaternion(new THREE.Quaternion()).premultiply(upperTurn);
        const lowerWorld = lower.getWorldQuaternion(new THREE.Quaternion()).premultiply(upperTurn);
        const handWorld = hand.getWorldQuaternion(new THREE.Quaternion());
        const forearm = v1.subVectors(wrist, elbow).normalize().applyQuaternion(upperTurn);
        const lowerTurn = new THREE.Quaternion().setFromUnitVectors(forearm, v2.subVectors(newWrist, newElbow).normalize());
        lowerWorld.premultiply(lowerTurn);
        handWorld.slerp(turned, this.weight);

        upper.quaternion.copy(q1.copy(upper.parent.getWorldQuaternion(q2)).invert().multiply(upperWorld));
        lower.quaternion.copy(q1.copy(upperWorld).invert().multiply(lowerWorld));
        hand.quaternion.copy(q1.copy(lowerWorld).invert().multiply(handWorld));
    }
}
