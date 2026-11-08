import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import * as THREE from '../../companion/node_modules/three/build/three.module.js';
import { VRMSpringBoneJoint, VRMSpringBoneManager } from '../../companion/node_modules/@pixiv/three-vrm-springbone/lib/three-vrm-springbone.module.js';

// Resolve the browser import map to the same locally installed dependencies.
const url = relative => new URL(relative, import.meta.url).href;
const source = (await readFile(new URL('../../face/js/auto_springbones.js', import.meta.url), 'utf8'))
    .replaceAll("'three'", JSON.stringify(url('../../companion/node_modules/three/build/three.module.js')))
    .replaceAll("'@pixiv/three-vrm-springbone'", JSON.stringify(url('../../companion/node_modules/@pixiv/three-vrm-springbone/lib/three-vrm-springbone.module.js')));
const { autoRegisterSpringBones } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

function fixture(authored = true, missing = false) {
    const scene = new THREE.Scene();
    const head = new THREE.Bone(); head.name = 'head'; scene.add(head);
    const root = new THREE.Bone(); root.name = 'hair_front'; head.add(root);
    const tip = new THREE.Bone(); tip.name = 'hair_front_tip'; tip.position.y = -0.1; root.add(tip);
    if (missing) {
        const end = new THREE.Bone(); end.name = 'hair_front_end'; end.position.y = -0.1; tip.add(end);
    }
    scene.updateMatrixWorld(true);
    const manager = new VRMSpringBoneManager();
    const joint = new VRMSpringBoneJoint(root, tip, {
        stiffness: 15, gravityPower: 0, gravityDir: new THREE.Vector3(1, 0, 0),
        dragForce: 0.1, hitRadius: 0.012,
    }, []);
    if (authored) manager.addJoint(joint);
    manager.setInitState();
    return { vrm: { scene, springBoneManager: manager }, joint, manager };
}
const snapshot = joint => ({ ...joint.settings, gravityDir: joint.settings.gravityDir.toArray() });

test('authored physics, including zero gravity and stiff bangs, survives registration and playback', async () => {
    const { vrm, joint, manager } = fixture();
    const before = snapshot(joint), update = manager.update, groups = joint.colliderGroups;
    assert.equal((await autoRegisterSpringBones(vrm)).added, 0);
    for (let i = 0; i < 120; i++) manager.update(1 / 60);
    assert.deepEqual(snapshot(joint), before);
    assert.equal(joint.colliderGroups, groups);
    assert.equal(manager.update, update, 'registration must not install a per-frame parameter override');
});

test('missing joints get independent defaults without altering an authored joint', async () => {
    const { vrm, joint, manager } = fixture(true, true);
    const before = snapshot(joint);
    assert.equal((await autoRegisterSpringBones(vrm)).added, 1);
    assert.deepEqual(snapshot(joint), before);
    const generated = [...manager.joints].find(j => j !== joint);
    assert.ok(generated.settings.gravityPower > 0);
    assert.notEqual(generated.settings.gravityDir, joint.settings.gravityDir);
    assert.equal(manager.joints.size, 2);
    let resetCount = 0;
    manager.setInitState = () => { resetCount++; };
    assert.equal((await autoRegisterSpringBones(vrm)).added, 0);
    assert.equal(resetCount, 0, 'repeated registration must not reset active physics');
});

test('an unconfigured hair chain still receives working spring physics', async () => {
    const { vrm, manager } = fixture(false);
    assert.equal((await autoRegisterSpringBones(vrm)).added, 1);
    for (let i = 0; i < 60; i++) manager.update(1 / 60);
    for (const joint of manager.joints) assert.ok(joint.bone.quaternion.toArray().every(Number.isFinite));
});
