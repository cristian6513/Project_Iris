import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';

const threeUrl = new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href;
const THREE = await import(threeUrl);
// The module imports three by name, like the browser's import map.
const text = (await readFile(new URL('../../face/js/arm_reach.js', import.meta.url), 'utf8')).replace("from 'three'", `from '${threeUrl}'`);
const { ArmReach, REFERENCE, isReferenceBody } = await import(`data:text/javascript;base64,${Buffer.from(text).toString('base64')}`);

const NAMES = ['hips', 'spine', 'chest', 'leftShoulder', 'leftUpperArm', 'leftLowerArm', 'leftHand', 'rightShoulder', 'rightUpperArm', 'rightLowerArm', 'rightHand'];

/** A normalized rig. `long` scales the arm, `flip` looks the other way, as a VRM 0 does. */
function rig({ long = 1, scale = REFERENCE.scale, flip = false, hipsY = REFERENCE.hips[1], yaw = 0 } = {}) {
    const f = flip ? -1 : 1;
    const at = (values, sign = 1) => [values[0] * sign * f, values[1], values[2] * f];
    const parts = {
        hips: [null, [0, hipsY, 0]],
        spine: ['hips', REFERENCE.spine],
        chest: ['spine', REFERENCE.chest],
        leftShoulder: ['chest', at(REFERENCE.shoulder)],
        leftUpperArm: ['leftShoulder', at(REFERENCE.upperArm)],
        leftLowerArm: ['leftUpperArm', at(REFERENCE.lowerArm).map((v) => v * long)],
        leftHand: ['leftLowerArm', at(REFERENCE.hand).map((v) => v * long)],
        rightShoulder: ['chest', at(REFERENCE.shoulder, -1)],
        rightUpperArm: ['rightShoulder', at(REFERENCE.upperArm, -1)],
        rightLowerArm: ['rightUpperArm', at(REFERENCE.lowerArm, -1).map((v) => v * long)],
        rightHand: ['rightLowerArm', at(REFERENCE.hand, -1).map((v) => v * long)],
    };
    const group = new THREE.Group();
    group.rotation.y = yaw;
    group.position.set(1, 0.07, -2);
    const scene = new THREE.Group();
    scene.scale.setScalar(scale);
    scene.position.fromArray(REFERENCE.scene);
    group.add(scene);
    const root = new THREE.Group();
    scene.add(root);
    const bones = {};
    for (const name of NAMES) {
        const [parent, position] = parts[name];
        const bone = new THREE.Group();
        bone.position.fromArray(position);
        (parent ? bones[parent] : root).add(bone);
        bones[name] = bone;
    }
    const vrm = {
        scene,
        meta: { metaVersion: flip ? '0' : '1' },
        humanoid: {
            normalizedRestPose: { hips: { position: [0, hipsY, 0] } },
            getNormalizedBoneNode: (name) => bones[name] ?? null,
        },
    };
    return { vrm, group, bones };
}

/** The same pose on any rig: arms bent forward, spine leaning. VRM 0 gets it mirrored, as clips do. */
function pose({ bones, vrm }) {
    const flip = String(vrm.meta.metaVersion) === '0';
    const q = (x, y, z) => {
        const value = new THREE.Quaternion().setFromEuler(new THREE.Euler(x, y, z));
        return flip ? new THREE.Quaternion(-value.x, value.y, -value.z, value.w) : value;
    };
    bones.spine.quaternion.copy(q(0.1, 0, 0));
    bones.chest.quaternion.copy(q(0.08, 0.05, 0));
    bones.leftUpperArm.quaternion.copy(q(0.2, 0.5, -1.1));
    bones.leftLowerArm.quaternion.copy(q(0, 1.2, 0));
    bones.leftHand.quaternion.copy(q(0.1, 0, 0.2));
    bones.rightUpperArm.quaternion.copy(q(0.2, -0.5, 1.1));
    bones.rightLowerArm.quaternion.copy(q(0, -1.2, 0));
    bones.rightHand.quaternion.copy(q(0.1, 0, -0.2));
    const hips = new THREE.Vector3(0.01, 0.6, -0.1).multiplyScalar(vrm.humanoid.normalizedRestPose.hips.position[1] / REFERENCE.hips[1]);
    if (flip) hips.set(-hips.x, hips.y, -hips.z);
    bones.hips.position.copy(hips);
    bones.hips.quaternion.copy(q(0.05, 0, 0));
    vrm.scene.parent.updateWorldMatrix(true, true);
}

const world = (bone) => bone.getWorldPosition(new THREE.Vector3());
const worldTurn = (bone) => bone.getWorldQuaternion(new THREE.Quaternion());
const angle = (a, b) => 2 * Math.acos(Math.min(1, Math.abs(a.dot(b))));

test('the reference is where a body with IRIS\' proportions really has its hands', () => {
    const iris = rig();
    pose(iris);
    const reach = new ArmReach(iris.vrm, iris.group);
    const position = new THREE.Vector3(), rotation = new THREE.Quaternion();
    for (const side of [
        { sign: 1, shoulder: 'leftShoulder', upper: 'leftUpperArm', lower: 'leftLowerArm', hand: 'leftHand' },
        { sign: -1, shoulder: 'rightShoulder', upper: 'rightUpperArm', lower: 'rightLowerArm', hand: 'rightHand' },
    ]) {
        reach.reference(side, position, rotation);
        assert.ok(position.distanceTo(world(iris.bones[side.hand])) < 1e-5);
        assert.ok(angle(rotation, worldTurn(iris.bones[side.hand])) < 1e-4);
    }
});

test('IRIS herself is left alone', () => {
    const iris = rig();
    pose(iris);
    assert.equal(isReferenceBody(iris.vrm), true);
    const before = iris.bones.leftUpperArm.quaternion.clone();
    const reach = new ArmReach(iris.vrm, iris.group);
    reach.weight = 1;
    reach.apply();
    assert.ok(iris.bones.leftUpperArm.quaternion.equals(before));
});

for (const [name, options] of [
    ['shorter arms', { long: 0.85, scale: 1.55, hipsY: 0.95 }],
    ['longer arms', { long: 1.1, scale: 1.4, hipsY: 0.82 }],
    ['a VRM 0, turned round', { long: 0.85, scale: 1.55, hipsY: 0.95, flip: true, yaw: Math.PI }],
]) {
    test(`with ${name} the hands land where IRIS has them, and turned the same way`, () => {
        const iris = rig({ yaw: options.yaw ? 0 : 0 });
        const other = rig(options);
        for (const r of [iris, other]) r.group.rotation.y = 0.4 + (r === other && options.flip ? Math.PI : 0);
        pose(iris);
        pose(other);
        const away = world(iris.bones.leftHand).distanceTo(world(other.bones.leftHand));
        assert.ok(away > 0.03, `the clip alone leaves it ${away} away`);
        const reach = new ArmReach(other.vrm, other.group);
        reach.weight = 1;
        reach.apply();
        other.group.updateWorldMatrix(true, true);
        for (const hand of ['leftHand', 'rightHand']) {
            assert.ok(world(iris.bones[hand]).distanceTo(world(other.bones[hand])) < 1e-3, hand);
            // A VRM 0's rest is itself half a turn away, so that half turn is part of 'the same way'.
            const same = worldTurn(iris.bones[hand]);
            if (options.flip) same.multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI));
            assert.ok(angle(same, worldTurn(other.bones[hand])) < 1e-2, hand);
        }
    });
}

test('the arm keeps its length and the elbow stays on the side the clip gave it', () => {
    const other = rig({ long: 0.85, scale: 1.55, hipsY: 0.95 });
    pose(other);
    const { leftUpperArm, leftLowerArm, leftHand } = other.bones;
    const lengths = () => [world(leftUpperArm).distanceTo(world(leftLowerArm)), world(leftLowerArm).distanceTo(world(leftHand))];
    const before = lengths();
    const below = world(leftLowerArm).y - world(leftUpperArm).y;
    const reach = new ArmReach(other.vrm, other.group);
    reach.weight = 1;
    reach.apply();
    other.group.updateWorldMatrix(true, true);
    lengths().forEach((value, i) => assert.ok(Math.abs(value - before[i]) < 1e-5));
    assert.equal(Math.sign(world(leftLowerArm).y - world(leftUpperArm).y), Math.sign(below));
});

test('what cannot be reached is reached for: the arm goes as far as it can and no further', () => {
    const iris = rig();
    const other = rig({ long: 0.3, scale: 1.5, hipsY: 0.95 });
    pose(iris);
    pose(other);
    const reach = new ArmReach(other.vrm, other.group);
    reach.weight = 1;
    reach.apply();
    other.group.updateWorldMatrix(true, true);
    const { leftUpperArm, leftLowerArm, leftHand } = other.bones;
    const arm = world(leftUpperArm).distanceTo(world(leftLowerArm)) + world(leftLowerArm).distanceTo(world(leftHand));
    assert.ok(world(leftUpperArm).distanceTo(world(leftHand)) <= arm);
    for (const value of leftHand.quaternion.toArray()) assert.ok(Number.isFinite(value));
});

test('the weight fades between the clip and the reach, and zero touches nothing', () => {
    const iris = rig();
    const other = rig({ long: 0.85, scale: 1.55, hipsY: 0.95 });
    pose(iris);
    pose(other);
    const start = world(other.bones.leftHand);
    const goal = world(iris.bones.leftHand);
    const reach = new ArmReach(other.vrm, other.group);
    reach.apply();
    assert.ok(world(other.bones.leftHand).distanceTo(start) < 1e-9);
    reach.weight = 0.5;
    reach.apply();
    other.group.updateWorldMatrix(true, true);
    const halfway = world(other.bones.leftHand);
    assert.ok(halfway.distanceTo(goal) < start.distanceTo(goal) * 0.6);
    assert.ok(halfway.distanceTo(start) < start.distanceTo(goal) * 0.6);
});
