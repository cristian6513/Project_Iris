// A PMX model's grant bones (付与): bones that copy part of another bone's turn, as MMD does every
// frame (2026-11-25). IRIS left them all still: Lily's shoulder pads, which follow half her arm,
// stretched the skin of her shoulder (26 triangles over 4x standing, 16 with grants), and a game
// dancer's waist ring is bent by an invisible physics skirt through them. Grants that hold the
// humanoid are left out: MMD's waist cancel undoes the hips' turn under the legs.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const threeUrl = new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href;
const THREE = await import(threeUrl);
const source = readFileSync(new URL('../../face/js/pmx_avatar.js', import.meta.url), 'utf8');
const block = source.slice(source.indexOf('const _grantTurn'), source.indexOf('export async function loadPmxAvatar'));
assert.ok(block.includes('export function grantedBones') && block.includes('export function applyGrants'));
const { grantedBones, applyGrants } = await import('data:text/javascript;base64,' + Buffer.from(
    `import * as THREE from '${threeUrl}';\n${block}`).toString('base64'));

function model() {
    const bone = (name, parent) => { const b = new THREE.Bone(); b.name = name; parent?.add(b); return b; };
    const root = bone('センター');
    const hips = bone('腰', root), cancel = bone('腰キャンセル左', hips), thigh = bone('左足', cancel), twin = bone('左足D', hips);
    const spine = bone('上半身', hips), arm = bone('左腕', spine), pad = bone('Shoulder_L', arm);
    const neck = bone('首', spine), ring = bone('脖環主', neck);
    const skirt = bone('Dress_2_0', spine), hoop = bone('M_Belt_02', spine), swung = bone('Hair', neck);
    const bones = [root, hips, cancel, thigh, twin, spine, arm, pad, neck, ring, skirt, hoop, swung];
    const at = (b) => bones.indexOf(b);
    const grant = (target, from, ratio, more = {}) => ({ index: at(target), parentIndex: at(from), ratio, affectRotation: true, affectPosition: false, isLocal: false, ...more });
    const grants = [
        grant(cancel, hips, -1),           // holds the left leg: out
        grant(twin, thigh, 1),             // a leg twin: followTwins
        grant(pad, arm, 0.5),              // in
        grant(ring, neck, 0.8),            // in
        grant(hoop, skirt, 1),             // in: from a physics bone
        grant(swung, neck, 1),             // a bone the physics moves: out
        grant(hoop, hoop, 1),              // itself: out
        grant(pad, arm, 1, { isLocal: true }),
        grant(ring, neck, 1, { affectRotation: false, affectPosition: true }),
    ];
    const rigidBodies = [{ boneIndex: at(skirt), type: 1 }, { boneIndex: at(swung), type: 1 }, { boneIndex: at(spine), type: 0 }];
    const mesh = { skeleton: { bones }, geometry: { userData: { MMD: { grants, rigidBodies } } } };
    const humanoid = { humanBones: { hips: { node: hips }, spine: { node: spine }, leftUpperArm: { node: arm }, neck: { node: neck }, leftUpperLeg: { node: thigh } } };
    return { mesh, humanoid, twins: [[thigh, twin]], bones: { hips, arm, pad, neck, ring, skirt, hoop } };
}

const turn = (axis, degrees) => new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(...axis).normalize(), degrees * Math.PI / 180);
const degreesOf = (q) => 2 * Math.acos(Math.min(1, Math.abs(q.w))) * 180 / Math.PI;

test('the grants IRIS follows leave out what holds the humanoid, the leg twins, physics bones, itself, local and position grants', () => {
    const { mesh, humanoid, twins } = model();
    const granted = grantedBones(mesh, humanoid, twins);
    assert.deepEqual(granted.map((g) => `${g.target.name}<${g.source.name}`), ['Shoulder_L<左腕', '脖環主<首', 'M_Belt_02<Dress_2_0']);
    assert.deepEqual(grantedBones({ skeleton: { bones: [] }, geometry: { userData: {} } }, { humanBones: {} }), [], 'a model without grants');
});

test('a granted bone turns by its share of the source, about the same axis, from its rest, frame after frame', () => {
    const { mesh, humanoid, twins, bones } = model();
    bones.pad.quaternion.copy(turn([0, 1, 0], 10));            // its rest, as modelled
    const granted = grantedBones(mesh, humanoid, twins);
    bones.arm.quaternion.copy(turn([0, 0, 1], 60));
    bones.neck.quaternion.copy(turn([1, 0, 0], -30));
    bones.skirt.quaternion.copy(turn([0, 0, 1], 40));
    for (let frame = 0; frame < 3; frame++) applyGrants(granted);   // no build-up
    const pad = turn([0, 1, 0], 10).multiply(turn([0, 0, 1], 30));
    assert.ok(Math.abs(bones.pad.quaternion.angleTo(pad)) < 1e-6, 'rest, then half the arm');
    assert.ok(Math.abs(degreesOf(bones.ring.quaternion) - 24) < 1e-6, 'the necklace: 0.8 of the neck');
    assert.ok(Math.abs(bones.hoop.quaternion.angleTo(turn([0, 0, 1], 40))) < 1e-6, 'the ring: all of the skirt');
    // A source turned the long way round (w < 0) gives the same short turn.
    bones.neck.quaternion.copy(turn([1, 0, 0], -30)).set(-bones.neck.quaternion.x, -bones.neck.quaternion.y, -bones.neck.quaternion.z, -bones.neck.quaternion.w);
    applyGrants(granted);
    assert.ok(Math.abs(degreesOf(bones.ring.quaternion) - 24) < 1e-6);
    // Back at rest, back to the rest turn exactly.
    for (const b of [bones.arm, bones.neck, bones.skirt]) b.quaternion.identity();
    applyGrants(granted);
    assert.ok(bones.pad.quaternion.angleTo(turn([0, 1, 0], 10)) < 1e-9);
    assert.deepEqual(bones.ring.quaternion.toArray(), [0, 0, 0, 1]);
});

test('a negative share turns the other way', () => {
    const { bones } = model();
    const granted = [{ target: bones.pad, source: bones.arm, ratio: -0.7, rest: new THREE.Quaternion() }];
    bones.arm.quaternion.copy(turn([0, 0, 1], 50));
    applyGrants(granted);
    assert.ok(bones.pad.quaternion.angleTo(turn([0, 0, 1], -35)) < 1e-6);
});

test('the avatar applies the grants every frame, after the humanoid and the leg twins, before the physics', () => {
    assert.match(source, /const granted = grantedBones\(mesh, humanoid, twins\);/);
    const update = source.slice(source.indexOf('            update(delta) {'));
    const order = ['humanoid.update();', 'followTwins(twins);', 'applyGrants(granted);', 'physics?.update(delta);'].map((step) => update.indexOf(step));
    assert.ok(order.every((at, i) => at > 0 && (i === 0 || at > order[i - 1])), `order ${order}`);
});
