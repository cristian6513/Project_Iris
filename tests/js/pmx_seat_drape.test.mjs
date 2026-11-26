// Seated, a PMX avatar's skirt leans forward over her thighs (2026-11-25), as IRIS' own skirt does
// (seat_drape.js). Her level thighs lifted the skirt's chains further than their joints turn: they
// went out sideways and up and the cloth between them stretched into long triangles, on every PMX
// tested and in Blender's MMD physics too. Letting the skirt hang through her thighs was tried
// first; the user chose the leaning skirt on Lily, Denia and Feixue.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { thighBodies } from '../../face/js/pmx_physics.js';

const THREE = await import(new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href);
const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const bone = (name, parent = null) => ({ name, parent });

test('the thighs are the bodies that follow an upper leg, its twin chain or its twist bones, down to the knee', () => {
    const hips = bone('下半身');
    const thighD = bone('左足D', hips), kneeD = bone('左ひざD', thighD), shinChild = bone('calf', kneeD);
    const thigh = bone('左足', hips), knee = bone('左ひざ', thigh);
    const twist = bone('Bip001LThighTwist', thighD);
    const rightThigh = bone('右足', hips), rightKnee = bone('右ひざ', rightThigh);
    const skirt = bone('スカート', hips);
    const humanoid = { humanBones: {
        leftUpperLeg: { node: thighD }, leftLowerLeg: { node: kneeD },
        rightUpperLeg: { node: rightThigh }, rightLowerLeg: { node: rightKnee },
    } };
    const bones = [hips, thighD, kneeD, shinChild, thigh, knee, twist, rightThigh, rightKnee, skirt];
    const twins = [[thighD, thigh], [kneeD, knee]];
    const at = (name, type = 0) => ({ boneIndex: bones.findIndex((b) => b.name === name), type });
    const bodies = [
        at('左足D'), at('左足'), at('Bip001LThighTwist'), at('右足'),        // 0-3: thighs
        at('左ひざD'), at('左ひざ'), at('calf'), at('右ひざ'),               // 4-7: knees and below
        at('下半身'), at('スカート', 1), at('左足D', 1),                    // 8-10: hips, skirt, a physics body
    ];
    assert.deepEqual(thighBodies(bodies, bones, humanoid, twins), [0, 1, 2, 3]);
    assert.deepEqual(thighBodies(bodies, bones, humanoid), [0, 2, 3], 'without the twins the control chain is not hers');
});

test('the avatar hands its thighs and the lean to the worker, and each step how far she sits', () => {
    assert.match(read('../../face/js/pmx_avatar.js'), /createPmxPhysics\(mesh, humanoid, modelled, twins\)/);
    const physics = read('../../face/js/pmx_physics.js');
    assert.match(physics, /const DRAPE_ANGLE = 50 \* Math\.PI \/ 180;/);
    assert.match(physics, /transforms, bodies, constraints, step, head, thighs, drapeAngle: DRAPE_ANGLE,/);
    assert.match(physics, /furniture: placed, hold, drape,/);
    // The same weight as IRIS' own skirt, from the seat's drape curve.
    const avatar = read('../../face/js/avatar.js');
    const seatDrape = avatar.slice(avatar.indexOf('    setSeatDrape(weight) {'));
    assert.ok(seatDrape.indexOf('this.vrm.pmxPhysics.setDrape?.(weight);') < seatDrape.indexOf('new SeatDrape('));
    assert.match(read('../../face/js/idle_system.js'), /this\.avatar\?\.avatar\?\.setSeatDrape\?\.\(this\._seatDrapeWeight\(\)\);/);
});

// The worker's own code, run on a stand-in for Ammo and MMDPhysics.
class Turn {
    constructor(x = 0, y = 0, z = 0, w = 1) { this.q = [x, y, z, w]; }
    setValue(x, y, z, w) { this.q = [x, y, z, w]; }
    x() { return this.q[0]; } y() { return this.q[1]; } z() { return this.q[2]; } w() { return this.q[3]; }
}
class Body {
    constructor(at, turn) { Object.assign(this, { at, turn }); }
    getCenterOfMassTransform() {
        const { at, turn } = this;
        return { getOrigin: () => ({ x: () => at[0], y: () => at[1], z: () => at[2] }), getRotation: () => new Turn(...turn) };
    }
}
class Frame {
    constructor(q) { this.q = q; }
    getRotation() { return new Turn(...this.q); }
    setRotation(turn) { this.q = [...turn.q]; }
}

async function seatedWorker() {
    const source = read('../../face/js/pmx_physics_worker.js').replace(/^import .+;\r?\n/gm, '')
        .replace('import(/* @vite-ignore */ moduleUrl)', 'loadAmmo(moduleUrl)');
    const runWorker = new Function('Bone', 'Object3D', 'MMDPhysics', 'self', 'globalThis', 'loadAmmo', 'fetch', source);
    const world = { getSolverInfo: () => ({ get_m_splitImpulse: () => false, set_m_splitImpulse() {} }), removeRigidBody() {} };
    const Ammo = { btQuaternion: Turn, btVector3: class { constructor() {} }, destroy() {} };
    let made;
    class MMDPhysics {
        constructor(mesh, bodies, constraints) {
            this.world = world;
            this.bodies = bodies.map((params) => ({ params, body: new Body(params.at, params.turn ?? [0, 0, 0, 1]) }));
            this.constraints = constraints.map((c) => ({
                bodyA: this.bodies[c.rigidBodyIndex1], bodyB: this.bodies[c.rigidBodyIndex2],
                constraint: { frame: new Frame([0, 0, 0, 1]), getFrameOffsetA() { return this.frame; } },
            }));
            made = this;
        }
        reset() {}
        update() {}
    }
    const scope = { postMessage() {} };
    runWorker(THREE.Bone, THREE.Object3D, MMDPhysics, scope, scope,
        async () => ({ default: async () => Ammo }),
        async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(0) }));
    // Bones: 0 hips, 1 head, 2 a lock of hair under it, 3 the rest. She faces +z, in MMD units.
    const parents = [-1, 0, 1, 0];
    const body = (type, at, more = {}) => ({ type, at, boneIndex: type ? 3 : 0, groupIndex: type ? 1 : 0, groupTarget: 0xffff,
        weight: 1, width: 0.2, height: 0, depth: 0, shapeType: 0, ...more });
    const bodies = [
        body(0, [0, 10, 0]),                                  // 0 hips
        body(0, [-1, 7, 0], { groupTarget: 0xffdf }),         // 1 left thigh: meets group 1, not group 5
        body(0, [1, 7, 0], { groupTarget: 0xffdf }),          // 2 right thigh
        body(1, [0, 9, 1.5]),                                 // 3 skirt, in front
        body(1, [2, 9, 0]),                                   // 4 skirt, at her side
        body(1, [0, 9, -1.5]),                                // 5 skirt, behind
        body(1, [0, 14, 1]),                                  // 6 a ribbon on her chest
        body(1, [0, 9, 1.2], { boneIndex: 2 }),               // 7 hair hanging in front
        body(1, [1.5, 9, 1.5]),                               // 8 skirt, half way round
        body(1, [0, 9, 1.5], { groupIndex: 5 }),              // 9 a group her thighs don't meet
        body(0, [0, 15, 0], { boneIndex: 1 }),                // 10 head
        body(1, [-0.5, 9, 1.5]),                              // 11 skirt held as the joint's first body
    ];
    const joint = (a, b) => ({ rigidBodyIndex1: a, rigidBodyIndex2: b });
    const constraints = [joint(0, 3), joint(0, 4), joint(0, 5), joint(0, 6), joint(10, 7), joint(0, 8), joint(0, 9), joint(11, 0)];
    const pose = () => new Float32Array(parents.length * 7).map((_, i) => (i % 7 === 6 ? 1 : 0));
    const step = 1 / 65;
    await scope.onmessage({ data: { kind: 'init', assets: 'https://iris.invalid/ammo/', parents, transforms: pose(),
        bodies, constraints, step, head: 1, thighs: [1, 2], drapeAngle: 50 * Math.PI / 180 } });
    const sit = (drape) => scope.onmessage({ data: { kind: 'step', transforms: pose(), furniture: null, hold: false, drape, step, delta: step } });
    const frames = () => made.constraints.map((c) => new THREE.Quaternion(...c.constraint.frame.q));
    return { sit, frames };
}

const angleOf = (q) => 2 * Math.acos(Math.min(1, Math.abs(q.w))) * 180 / Math.PI;

test('seated, the joints that hang her skirt in front lean forward; the sides, the back, her chest, her hair and what her thighs miss stay', async () => {
    const { sit, frames } = await seatedWorker();
    await sit(1);
    const [front, side, back, chest, hair, half, missed, heldFirst] = frames();
    // In front: 50 degrees, and the hanging chain swings forward (+z) and up.
    assert.ok(Math.abs(angleOf(front) - 50) < 1e-3, `front ${angleOf(front)}`);
    const swung = new THREE.Vector3(0, -1, 0).applyQuaternion(front);
    assert.ok(swung.z > 0.7 && swung.y < -0.6, `swung ${swung.toArray()}`);
    // Half way round: by the cosine, about its own axis.
    assert.ok(Math.abs(angleOf(half) - 50 * Math.SQRT1_2) < 1e-2, `half way ${angleOf(half)}`);
    const out = new THREE.Vector3(0, -1, 0).applyQuaternion(half);
    assert.ok(out.x > 0.3 && out.z > 0.3, 'swings out where it hangs');
    for (const [name, q] of Object.entries({ side, back, chest, hair, missed })) assert.ok(angleOf(q) < 1e-6, name);
    // Held as the joint's first body, its frame turns the other way, so the chain still swings forward.
    const cos = Math.cos(Math.atan2(-0.5, 1.5));
    assert.ok(Math.abs(angleOf(heldFirst) - 50 * cos) < 1e-2, `held first ${angleOf(heldFirst)}`);
    assert.ok(new THREE.Vector3(0, -1, 0).applyQuaternion(heldFirst.clone().invert()).z > 0.5);
});

test('the lean follows the seat as she sits down and stands up, and standing gives back the modelled joints', async () => {
    const { sit, frames } = await seatedWorker();
    await sit(0.5);
    assert.ok(Math.abs(angleOf(frames()[0]) - 25) < 1e-3, 'half seated, half the lean');
    await sit(1);
    await sit(0);
    for (const q of frames()) assert.deepEqual(q.toArray(), [0, 0, 0, 1]);
    await sit(1);
    await sit(Number.NaN);
    assert.deepEqual(frames()[0].toArray(), [0, 0, 0, 1], 'a weight that is not a number is standing');
});

test('the worker leans the skirt each step before the physics runs', () => {
    const worker = read('../../face/js/pmx_physics_worker.js');
    assert.match(worker, /findDrape\(data\.thighs, data\.drapeAngle\);/);
    assert.ok(worker.indexOf('if (drapeJoints.length) drape(data.drape);') < worker.indexOf('physics.update(data.delta);'));
    assert.match(worker, /joint\.constraint\.getFrameOffsetA\(\)\.setRotation\(drapeTurn\);/);
});
