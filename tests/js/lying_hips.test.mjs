import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const threeUrl = new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href;
const THREE = await import(threeUrl);
const { IRIS_TRUNK_LOW, LyingHips, liftAngle, lyingFacing } = await import('data:text/javascript;base64,'
    + Buffer.from(await readFile(new URL('../../face/js/lying_hips.js', import.meta.url), 'utf8')).toString('base64'));

// An avatar fitted to the room: hips `rest` high in its own units, the scene scaled by `scale`,
// and the normalized rig under its root as three-vrm builds it.
function avatar(rest, scale) {
    const scene = new THREE.Group();
    scene.scale.setScalar(scale);
    const root = new THREE.Object3D();
    const hips = new THREE.Object3D();
    root.add(hips);
    scene.add(root);
    const humanoid = { normalizedHumanBonesRoot: root, normalizedRestPose: { hips: { position: [0, rest, 0] } } };
    return { vrm: { scene, humanoid }, hips, root };
}
const worldOf = node => node.getWorldPosition(new THREE.Vector3());

test('an avatar with higher hips lies where IRIS lies, and the clip keeps its own values', () => {
    const { vrm, hips, root } = avatar(0.923, 1.607); // Lily: hips 15 % higher than IRIS'
    const lying = new LyingHips(vrm);
    // The mixer wrote the clip scaled to her hips (three-vrm). IRIS' (0.883 high, room scale
    // 1.46) would have been 0.55 * 0.883 / 0.923 of her own units.
    const iris = new THREE.Vector3(0.1, 0.55, -0.4).multiplyScalar(0.883 / 0.923 * 1.46);
    hips.position.set(0.1, 0.55, -0.4);
    lying.set(1);
    assert.ok(worldOf(hips).distanceTo(iris) < 0.005, `hips at ${worldOf(hips).toArray()}, IRIS' at ${iris.toArray()}`);
    assert.deepEqual(hips.position.toArray(), [0.1, 0.55, -0.4], 'what the mixer and fades read is untouched');
    lying.set(1);
    assert.ok(worldOf(hips).distanceTo(iris) < 0.005, 'never twice');
    lying.set(0.5);
    const half = worldOf(hips).y;
    assert.ok(half < 0.55 * 1.607 && half > iris.y);
    lying.set(0);
    assert.equal(root.scale.x, 1);
    lying.set(Number.NaN);
    assert.equal(root.scale.x, 1);
});

test('IRIS and avatars of her proportions are left as the clip has them', () => {
    for (const [rest, scale] of [[0.883, 1.46], [0.9, 1.45]]) {
        const { vrm, root } = avatar(rest, scale);
        new LyingHips(vrm).set(1);
        assert.equal(root.scale.x, 1);
    }
    assert.doesNotThrow(() => new LyingHips({ scene: new THREE.Group(), humanoid: {} }).set(1));
});

test('her hips face up on her back, down on her front, and neither upright or on her side', () => {
    const v = (x, y, z) => new THREE.Vector3(x, y, z);
    assert.deepEqual(lyingFacing(v(0, 0, -1)), { back: 1, front: 0 }, 'down is behind her: on her back');
    assert.deepEqual(lyingFacing(v(0, 0, 1)), { back: 0, front: 1 });
    assert.deepEqual(lyingFacing(v(0, -1, 0)), { back: 0, front: 0 }, 'upright: sitting on the desk, standing');
    assert.deepEqual(lyingFacing(v(1, 0, 0)), { back: 0, front: 0 }, 'on her side, halfway through a roll');
    assert.deepEqual(lyingFacing(v(0, 0, -1), true), { back: 0, front: 1 }, 'a VRM 0 model looks the other way');
    const half = lyingFacing(v(0, -Math.SQRT1_2, -Math.SQRT1_2));
    assert.ok(half.back > 0 && half.back < 1, 'tipping back, in between');
});

test('a leg turns at the hip by the angle that lifts its heel by the asked height', () => {
    const angle = liftAngle({ x: 0, y: 0, z: 0 }, { x: 0, y: -0.05, z: 0.9 }, 0.1);
    const length = Math.hypot(0.05, 0.9);
    const before = Math.asin(-0.05 / length);
    assert.ok(Math.abs(length * (Math.sin(before + angle) - Math.sin(before)) - 0.1) < 1e-9);
    assert.equal(liftAngle({ x: 0, y: 0, z: 0 }, { x: 0, y: 0, z: 1 }, 0), 0);
});

// The normalized rig lying on her back or front: hips turned, a leg from the hip to the heel.
function lyingAvatar({ rest = 0.883, scale = 1.4594, way = 'back', meta = '1' } = {}) {
    const { vrm, hips, root } = avatar(rest, scale);
    const group = new THREE.Group();
    group.add(vrm.scene);
    vrm.meta = { metaVersion: meta };
    // On her back: her front (+Z) faces up. On her front: it faces down.
    hips.quaternion.setFromAxisAngle(new THREE.Vector3(1, 0, 0), way === 'back' ? -Math.PI / 2 : Math.PI / 2);
    hips.position.set(0, 0.35, 0);
    const bones = {};
    for (const side of ['left', 'right']) {
        const leg = new THREE.Object3D(), foot = new THREE.Object3D();
        leg.position.set(side === 'left' ? 0.08 : -0.08, -0.05, 0);
        foot.position.set(0, -0.8, 0);
        hips.add(leg); leg.add(foot);
        bones[side + 'UpperLeg'] = leg; bones[side + 'Foot'] = foot;
    }
    bones.hips = hips;
    vrm.humanoid.getNormalizedBoneNode = name => bones[name] ?? null;
    group.updateMatrixWorld(true);
    return { vrm, root, bones, group };
}

test('once measured, her trunk rests where IRIS\' does, as much as she lies that way', () => {
    const { vrm, root } = lyingAvatar({ way: 'back' });
    const lying = new LyingHips(vrm);
    lying.fit.back = { rise: 0.03, legs: 0 };
    lying.fit.front = { rise: -0.02 };
    lying.set(1, 0.1);
    assert.ok(Math.abs(root.position.y - 0.03 * 0.2 / 1.4594) < 1e-9, 'eased in, not at once');
    for (let i = 0; i < 10; i++) lying.set(1, 0.1);
    assert.ok(Math.abs(root.position.y * 1.4594 - 0.03) < 1e-9, 'on her back: the back fit');
    lying.set(0.5, 0.1);
    assert.ok(Math.abs(root.position.y * 1.4594 - 0.015) < 1e-9, 'half lying, half of it');
    lying.set(0, 0.1);
    assert.equal(root.position.y, 0, 'up: nothing');

    const front = lyingAvatar({ way: 'front' });
    const prone = new LyingHips(front.vrm);
    prone.fit.front = { rise: -0.02 };
    prone.gain.front = 1;
    prone.set(1);
    assert.ok(Math.abs(front.root.position.y * 1.4594 + 0.02) < 1e-9, 'on her front: the front fit');
});

test('on her back a heel that sank is lifted by turning the leg at the hip; on her front it is not', () => {
    const { vrm, bones } = lyingAvatar({ way: 'back' });
    const lying = new LyingHips(vrm);
    const heel = () => bones.leftFoot.getWorldPosition(new THREE.Vector3()).y;
    const before = heel();
    const leg = bones.leftUpperLeg;
    leg.name = 'leftUpperLeg';
    const rotation = leg.quaternion.toArray();
    const track = new THREE.QuaternionKeyframeTrack('leftUpperLeg.quaternion', [0, 1], [...rotation, ...rotation]);
    const mixer = new THREE.AnimationMixer(vrm.scene);
    mixer.clipAction(new THREE.AnimationClip('held', 1, [track])).play();
    lying.fit.back = { rise: 0, legs: 0.06 };
    lying.gain.back = 1;
    for (let frame = 0; frame < 60; frame++) {
        mixer.update(1 / 60);
        lying.set(1);
        assert.ok(Math.abs(heel() - before - 0.06) < 1e-6, `heel rose ${heel() - before}`);
        // In the app the corrected pose is copied to the raw bones before this restore.
        lying.restoreLegs();
        assert.deepEqual(leg.quaternion.toArray(), rotation);
    }

    lying.set(1);
    lying.set(1);
    assert.ok(Math.abs(heel() - before - 0.06) < 1e-6, 'the second update replaces the lift');
    lying.set(0);
    assert.ok(Math.abs(heel() - before) < 1e-6, 'clearing the lift also works before drawing');

    const front = lyingAvatar({ way: 'front' });
    const prone = new LyingHips(front.vrm);
    const kept = front.bones.leftUpperLeg.quaternion.clone();
    prone.fit.back = { rise: 0, legs: 0.06 };
    prone.gain.back = 1;
    prone.set(1);
    assert.ok(front.bones.leftUpperLeg.quaternion.equals(kept), 'her feet are up in the air');
});

test('measure reads where her trunk and heels rest, once, and IRIS herself needs nothing', () => {
    // A skinned body in the room: trunk vertices on the hips bone, heel vertices on the foot's.
    function skinned(trunkY, heelY) {
        const { vrm, group } = lyingAvatar({ way: 'back' });
        const hipsBone = new THREE.Bone(), footBone = new THREE.Bone();
        hipsBone.add(footBone);
        vrm.scene.add(hipsBone);
        const count = 200, positions = [], index = [], weight = [];
        for (let i = 0; i < count; i++) {
            const trunk = i < 150;
            // Heights above the group's floor, in room units, before the scene's scale.
            const y = (trunk ? trunkY : heelY) + (i % 50) * 0.002;
            positions.push(0, y / 1.4594, 0);
            index.push(trunk ? 0 : 1, 0, 0, 0);
            weight.push(1, 0, 0, 0);
        }
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
        geometry.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(index, 4));
        geometry.setAttribute('skinWeight', new THREE.Float32BufferAttribute(weight, 4));
        const mesh = new THREE.SkinnedMesh(geometry, new THREE.MeshBasicMaterial());
        vrm.scene.add(mesh);
        group.updateMatrixWorld(true);
        mesh.bind(new THREE.Skeleton([hipsBone, footBone]));
        const raw = { hips: hipsBone, leftFoot: footBone };
        vrm.humanoid.getRawBoneNode = name => raw[name] ?? null;
        return vrm;
    }
    const iris = new LyingHips(skinned(IRIS_TRUNK_LOW.back, -0.55));
    assert.equal(iris.measure('back'), null, 'trunk where IRIS\' is, heels above it');

    const deep = new LyingHips(skinned(IRIS_TRUNK_LOW.back - 0.05, -0.72));
    const fit = deep.measure('back');
    assert.ok(Math.abs(fit.rise - 0.05) < 2e-3, `trunk 5 cm (room) too deep: rise ${fit.rise}`);
    assert.ok(Math.abs(fit.legs - (IRIS_TRUNK_LOW.back - (-0.72 + fit.rise))) < 2e-3, `heels lifted ${fit.legs}`);
    deep.fit.back.rise = 123;
    assert.equal(deep.measure('back').rise, 123, 'measured once');
});
