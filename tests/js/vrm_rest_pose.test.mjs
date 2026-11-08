import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import * as THREE from '../../companion/node_modules/three/build/three.module.js';

const threeUrl = new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href;
const gltfUrl = new URL('../../companion/node_modules/three/examples/jsm/loaders/GLTFLoader.js', import.meta.url).href;
const source = (await readFile(new URL('../../face/js/vrm_animation_loader.js', import.meta.url), 'utf8'))
    .replace("from './avatar_lifecycle.js'", `from '${new URL('../../face/js/avatar_lifecycle.js', import.meta.url).href}'`)
    .replace("from 'three'", `from '${threeUrl}'`)
    .replace("from 'three/addons/loaders/GLTFLoader.js'", `from '${gltfUrl}'`);
const { VRMAnimationPlayer } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

for (const direction of [-1, 1]) test(`authored rest remains after loader disposal under arm direction ${direction}`, () => {
    const root = new THREE.Group(); root.rotation.y = 1.1;
    const upper = new THREE.Bone(); upper.name = 'arm';
    const lower = new THREE.Bone(); lower.position.x = direction * 0.3;
    const hand = new THREE.Bone(); hand.rotation.x = 0.24;
    root.add(upper); upper.add(lower); lower.add(hand);
    upper.rotation.set(0.7, -0.4, 0.9);
    const untouchedHand = hand.quaternion.clone();
    const expected = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), -direction * 1.1);
    const vrm = { scene: root, humanoid: {} };
    const player = new VRMAnimationPlayer(vrm);
    const rest = new THREE.AnimationClip('rest', 1 / 24, [
        new THREE.QuaternionKeyframeTrack('arm.quaternion', [0], expected.toArray()),
    ]);
    player.clips.set('rest', rest);
    player.actions.set('rest', player.mixer.clipAction(rest));
    assert.equal(player.applyPose('rest'), true);
    assert.equal(player.applyPose('rest'), true);
    const mixer = player.mixer;
    player.dispose(); player.dispose();
    assert.ok(upper.quaternion.angleTo(expected) < 1e-6);
    assert.ok(hand.quaternion.angleTo(untouchedHand) < 1e-6);
    assert.equal(mixer.stats.actions.total, 0);
    assert.equal(mixer.stats.bindings.total, 0);
    root.updateMatrixWorld(true);
    assert.ok(lower.getWorldPosition(new THREE.Vector3()).y < -0.25);
    assert.equal(player.applyPose('rest'), false);
    assert.ok(upper.quaternion.angleTo(expected) < 1e-6);
    assert.equal(player.applyPose('missing'), false);

    // The next mixer captures the authored rest, not a procedural arm angle.
    const next = new THREE.AnimationMixer(root);
    const gesture = new THREE.AnimationClip('gesture', 1, [
        new THREE.QuaternionKeyframeTrack('arm.quaternion', [0, 1], [0, 0, 0, 1, 0, 0, 0, 1]),
    ]);
    next.clipAction(gesture).play(); next.update(0.5); next.stopAllAction();
    assert.ok(upper.quaternion.angleTo(expected) < 1e-6);
});
