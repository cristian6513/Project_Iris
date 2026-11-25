// Hair colliders at the avatar's real size (2026-11-24): IRIS's fringe sank into her forehead
// because three-vrm reads collider radii as world lengths while avatar.js scales the model ×1.46.
// These tests run the real three-vrm springbone module.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from '../../companion/node_modules/three/build/three.module.js';
import {
    VRMSpringBoneCollider, VRMSpringBoneColliderShapeCapsule, VRMSpringBoneColliderShapeSphere,
    VRMSpringBoneJoint, VRMSpringBoneManager,
} from '../../companion/node_modules/@pixiv/three-vrm-springbone/lib/three-vrm-springbone.module.js';
import { scaleHairColliders } from '../../face/js/spring_collider_scale.js';

/** A tiny avatar: hips → head, a hair chain on the head and a skirt chain on the hips. */
function avatar(scale = 1.5) {
    const scene = new THREE.Group();
    scene.scale.setScalar(scale);
    const bone = (name, parent, y, z = 0) => { const b = Object.assign(new THREE.Bone(), { name }); b.position.set(0, y, z); parent.add(b); return b; };
    const hips = bone('hips', scene, 1);
    const head = bone('head', hips, 0.6);
    // A fringe: from above her forehead, hanging down in front of it.
    const hair = bone('hair', head, 0.1, 0.1), hairTip = bone('hair_tip', hair, -0.12);
    const skirt = bone('skirt', hips, 0), skirtTip = bone('skirt_tip', skirt, -0.2);
    scene.updateMatrixWorld(true);
    const collider = (shape, parent) => { const c = new VRMSpringBoneCollider(shape); parent.add(c); return c; };
    const headSphere = collider(new VRMSpringBoneColliderShapeSphere({ radius: 0.08 }), head);
    const neck = collider(new VRMSpringBoneColliderShapeCapsule({ radius: 0.04, tail: new THREE.Vector3(0, -0.1, 0) }), head);
    const leg = collider(new VRMSpringBoneColliderShapeCapsule({ radius: 0.07, tail: new THREE.Vector3(0, -0.4, 0) }), hips);
    const manager = new VRMSpringBoneManager();
    const hairJoint = new VRMSpringBoneJoint(hair, hairTip, { hitRadius: 0.01 },
        [{ name: 'head', colliders: [headSphere, neck] }, { name: 'shared', colliders: [leg] }]);
    const skirtJoint = new VRMSpringBoneJoint(skirt, skirtTip, { hitRadius: 0.03 }, [{ name: 'legs', colliders: [leg] }]);
    manager.addJoint(hairJoint);
    manager.addJoint(skirtJoint);
    const humanoid = { getRawBoneNode: (name) => ({ hips, head })[name] ?? null };
    return { vrm: { scene, springBoneManager: manager, humanoid }, headSphere, neck, leg, hairJoint, skirtJoint };
}

test('three-vrm reads a collider radius as a world length (why the scale is needed)', () => {
    const { headSphere } = avatar(1.5);
    headSphere.updateWorldMatrix(true, false);
    const centre = new THREE.Vector3().setFromMatrixPosition(headSphere.colliderMatrix);
    // 0.10 in the world from the centre: inside a 0.12 sphere (0.08 × 1.5), outside a 0.08 one.
    const distance = headSphere.shape.calculateCollision(headSphere.colliderMatrix,
        centre.clone().add(new THREE.Vector3(0, 0, 0.1)), 0, new THREE.Vector3());
    assert.ok(distance > 0, `the library already scales radii (distance ${distance}); scaleHairColliders would double it`);
});

test('the hair joints and the colliders only they use get the scale', () => {
    const { vrm, headSphere, neck, leg, hairJoint, skirtJoint } = avatar(1.5);
    const result = scaleHairColliders(vrm);
    assert.equal(result.joints, 1);
    assert.equal(result.colliders, 2);
    assert.equal(result.shared, 1);
    assert.ok(Math.abs(result.scale - 1.5) < 1e-9);
    assert.ok(Math.abs(headSphere.shape.radius - 0.12) < 1e-9);
    assert.ok(Math.abs(neck.shape.radius - 0.06) < 1e-9);
    assert.ok(Math.abs(hairJoint.settings.hitRadius - 0.015) < 1e-9);
    // The clothing keeps its tuned sizes, and a collider it shares with the hair too.
    assert.equal(leg.shape.radius, 0.07);
    assert.equal(skirtJoint.settings.hitRadius, 0.03);
});

test('once per avatar, and nothing at the file size or without a head', () => {
    const { vrm, headSphere } = avatar(1.5);
    scaleHairColliders(vrm);
    scaleHairColliders(vrm);
    assert.ok(Math.abs(headSphere.shape.radius - 0.12) < 1e-9);

    const plain = avatar(1);
    assert.equal(scaleHairColliders(plain.vrm).joints, 0);
    assert.equal(plain.headSphere.shape.radius, 0.08);

    const headless = avatar(1.5);
    headless.vrm.humanoid = { getRawBoneNode: () => null };
    assert.equal(scaleHairColliders(headless.vrm).joints, 0);
    assert.equal(scaleHairColliders({}).joints, 0);
    assert.equal(scaleHairColliders(null).joints, 0);
});

test('the scaled head sphere keeps a hair tip out that used to get through', () => {
    const run = (fix) => {
        const { vrm, hairJoint } = avatar(1.5);
        if (fix) scaleHairColliders(vrm);
        vrm.springBoneManager.setInitState();
        // Push the hair back into the head, the way inertia does when she sits up quickly.
        hairJoint.settings.gravityDir = new THREE.Vector3(0, 0, -1);
        hairJoint.settings.gravityPower = 4;
        for (let i = 0; i < 120; i++) vrm.springBoneManager.update(1 / 60);
        const head = vrm.humanoid.getRawBoneNode('head').getWorldPosition(new THREE.Vector3());
        return hairJoint.child.getWorldPosition(new THREE.Vector3()).distanceTo(head);
    };
    const before = run(false), after = run(true);
    assert.ok(after > before + 0.01, `tip ${before.toFixed(3)} → ${after.toFixed(3)} from the head`);
    assert.ok(after >= 0.12 + 0.015 - 1e-3, `the tip stays outside the scaled sphere (${after.toFixed(3)})`);
});
