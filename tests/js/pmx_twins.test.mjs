// MMD legs come twice: the bones the leg is moved by (足, ひざ, 足首) and those the skin follows (足D...).
// Iris moves the skin's; the twin is turned with it, so rigid bodies on either (the thighs a skirt
// rests on) sit where the visible leg is.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const threeUrl = new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href;
const THREE = await import(threeUrl);
const loader = readFileSync(new URL('../../face/js/pmx_avatar.js', import.meta.url), 'utf8');
const body = loader.slice(loader.indexOf('const _twinTurn'), loader.indexOf('\n}\n', loader.indexOf('export function followTwins')) + 3);
assert.ok(body.includes('export function followTwins'), 'followTwins found in pmx_avatar.js');
const { followTwins } = await import('data:text/javascript;base64,' + Buffer.from(`import * as THREE from '${threeUrl}';\n${body}`).toString('base64'));

// A lower body with both thighs on it, each with its knee, as MMD models have them.
function legs() {
    const lowerBody = new THREE.Bone();
    lowerBody.position.set(0, 10, 0);
    lowerBody.quaternion.setFromAxisAngle(new THREE.Vector3(0, 1, 0), 0.4);
    const thigh = new THREE.Bone(), thighD = new THREE.Bone(), knee = new THREE.Bone(), kneeD = new THREE.Bone();
    thigh.position.set(1, -1, 0);
    thighD.position.set(1, -1, 0);
    knee.position.set(0, -4, 0);
    kneeD.position.set(0, -4, 0);
    lowerBody.add(thigh, thighD);
    thigh.add(knee);
    thighD.add(kneeD);
    lowerBody.updateMatrixWorld(true);
    return { lowerBody, thigh, thighD, knee, kneeD };
}

const turnOf = bone => bone.getWorldQuaternion(new THREE.Quaternion());
const placeOf = bone => bone.getWorldPosition(new THREE.Vector3());

test('sitting: the twin thigh and knee turn with the bones the skin follows', () => {
    const { lowerBody, thigh, thighD, knee, kneeD } = legs();
    thighD.quaternion.setFromAxisAngle(new THREE.Vector3(1, 0, 0), -1.4);   // thigh up, as on a seat
    kneeD.quaternion.setFromAxisAngle(new THREE.Vector3(1, 0, 0), 1.5);     // shin down
    lowerBody.updateMatrixWorld(true);
    followTwins([[thighD, thigh], [kneeD, knee]]);
    assert.ok(Math.abs(Math.abs(turnOf(thigh).dot(turnOf(thighD))) - 1) < 1e-6, 'thigh');
    assert.ok(Math.abs(Math.abs(turnOf(knee).dot(turnOf(kneeD))) - 1) < 1e-6, 'knee, after its thigh');
    // A rigid body hung on the twin knee is where the visible knee is.
    assert.ok(placeOf(knee).distanceTo(placeOf(kneeD)) < 1e-6);
});

test('a twin without a parent is left alone', () => {
    const moved = new THREE.Bone(), twin = new THREE.Bone();
    moved.quaternion.setFromAxisAngle(new THREE.Vector3(1, 0, 0), 1);
    followTwins([[moved, twin]]);
    assert.equal(twin.quaternion.w, 1);
});

test('the PMX avatar collects the twin legs and turns them before the physics step', () => {
    assert.match(loader, /if \(control && deform\) twins\.push\(/);
    assert.match(loader, /return \{ humanoid, twins \};/);
    const update = loader.slice(loader.indexOf('update(delta) {'));
    const order = ['humanoid.update()', 'followTwins(twins)', 'physics?.update(delta)'].map(step => update.indexOf(step));
    assert.ok(order.every(index => index >= 0) && order[0] < order[1] && order[1] < order[2], order.join(','));
});
