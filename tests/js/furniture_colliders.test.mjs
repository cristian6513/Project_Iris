// Hair and clothes rest on the room's furniture: boxes for a VRM's spring bones, and the same boxes
// in a PMX mesh's space for its MMD physics worker.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const threeUrl = new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href;
const THREE = await import(threeUrl);
const source = readFileSync(new URL('../../face/js/furniture_colliders.js', import.meta.url), 'utf8')
    .replace("from 'three'", `from '${threeUrl}'`);
const { FURNITURE_BOXES, PMX_FURNITURE_BOXES, FurnitureBoxShape, addFurnitureColliders } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const { furnitureInMeshSpace } = await import(new URL('../../face/js/pmx_physics.js', import.meta.url).href);

const bed = FURNITURE_BOXES.find(box => box.name === 'bed');

test('a hair tail inside the mattress goes out through its top; beside the bed it is left alone', () => {
    const shape = new FurnitureBoxShape(bed);
    const push = new THREE.Vector3();
    const depth = shape.calculateCollision(null, new THREE.Vector3(3.6, -0.6, 2.0), 0.02, push);
    assert.ok(depth < 0, 'inside');
    assert.deepEqual(push.toArray(), [0, 1, 0], 'up, the nearest face');
    assert.ok(Math.abs(-depth - (-0.56 + 0.02 - -0.6)) < 1e-9, 'just out of it, radius included');
    // Hanging past the near edge: outside the box, it falls as it would.
    assert.ok(shape.calculateCollision(null, new THREE.Vector3(2.5, -0.9, 2.0), 0.02, push) > 0);
    // Deep in by the near side: out through the side, not up through the mattress.
    shape.calculateCollision(null, new THREE.Vector3(2.66, -1.0, 2.0), 0.0, push);
    assert.deepEqual(push.toArray(), [-1, 0, 0]);
});

test('the furniture boxes are where the room has its furniture', () => {
    const names = FURNITURE_BOXES.map(box => box.name);
    for (const name of ['bed', 'headboard', 'pillows', 'desk', 'sofa_seat', 'sofa_back', 'coffee_table', 'floor']) assert.ok(names.includes(name), name);
    assert.equal(bed.max[1], -0.56, 'just under the blanket, measured at -0.55');
    const desk = FURNITURE_BOXES.find(box => box.name === 'desk');
    assert.ok(desk.min[1] > -0.5, 'a slab: the chair and her knees go under the desk');
    for (const box of FURNITURE_BOXES) for (let k = 0; k < 3; k++) assert.ok(box.min[k] < box.max[k], box.name);
});

test('the PMX sofa supports cloth at the cushion without blocking ribbons underneath', () => {
    assert.equal(PMX_FURNITURE_BOXES.length, FURNITURE_BOXES.length);
    for (const [i, original] of FURNITURE_BOXES.entries()) {
        const box = PMX_FURNITURE_BOXES[i];
        if (original.name !== 'sofa_seat') {
            assert.equal(box, original);
            continue;
        }
        assert.deepEqual(box.max, original.max, 'same supporting surface');
        assert.deepEqual(box.min, [original.min[0], original.max[1] - 0.08, original.min[2]]);
        assert.ok(box.min[1] > original.min[1]);
    }
});

// A VRM's spring bones as three-vrm keeps them: joints with collider groups, and a manager that
// works out their order again when a joint is added.
function springVrm(jointCount = 3) {
    const hips = new THREE.Object3D();
    const joints = new Set(Array.from({ length: jointCount }, () => ({ colliderGroups: [{ name: 'body', colliders: [] }] })));
    const manager = { joints, added: 0, addJoint(joint) { this.added++; this.joints.add(joint); } };
    return { vrm: { springBoneManager: manager, humanoid: { getRawBoneNode: name => (name === 'hips' ? hips : null) } }, hips, joints, manager };
}

test('every spring joint gets the furniture once, hung from her hips so her own skeleton orders them', () => {
    const { vrm, hips, joints, manager } = springVrm();
    assert.equal(addFurnitureColliders(vrm), true);
    assert.equal(hips.children.length, FURNITURE_BOXES.length);
    for (const joint of joints) {
        assert.equal(joint.colliderGroups.length, 2, 'its own colliders stay');
        assert.equal(joint.colliderGroups[1].colliders.length, FURNITURE_BOXES.length);
        assert.ok(joint.colliderGroups[1].colliders.every(collider => collider.shape instanceof FurnitureBoxShape && collider.colliderMatrix));
    }
    assert.equal(manager.added, joints.size, 'the joints are ordered again');
    assert.equal(addFurnitureColliders(vrm), false, 'never twice');
    assert.equal(hips.children.length, FURNITURE_BOXES.length);
    assert.equal(addFurnitureColliders({}), false, 'a PMX has no spring bones: its worker takes the boxes');
    assert.equal(addFurnitureColliders(springVrm(0).vrm), false);
});

test('in a PMX mesh\'s space the boxes turn and scale with her: she walks and turns, the furniture stays', () => {
    const turn = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), 0.7);
    const scale = 0.08 * 1.42;
    const matrix = new THREE.Matrix4().compose(new THREE.Vector3(2.5, -1.25, 1.7), turn, new THREE.Vector3(scale, scale, scale));
    const packed = furnitureInMeshSpace([bed], matrix);
    assert.equal(packed.length, 10);
    const centre = new THREE.Vector3((bed.min[0] + bed.max[0]) / 2, (bed.min[1] + bed.max[1]) / 2, (bed.min[2] + bed.max[2]) / 2)
        .applyMatrix4(matrix.clone().invert());
    assert.ok(new THREE.Vector3(packed[0], packed[1], packed[2]).distanceTo(centre) < 1e-3);
    const inverse = turn.clone().invert();
    const q = new THREE.Quaternion(packed[3], packed[4], packed[5], packed[6]);
    assert.ok(Math.abs(Math.abs(q.dot(inverse)) - 1) < 1e-5, 'turned back by her own turn');
    assert.ok(Math.abs(packed[7] - (bed.max[0] - bed.min[0]) / 2 / scale) < 1e-3, 'in her own units');
    assert.ok(Math.abs(packed[8] - (bed.max[1] - bed.min[1]) / 2 / scale) < 1e-3);
});

test('the physics worker takes the furniture before a reset, in every group', () => {
    const worker = readFileSync(new URL('../../face/js/pmx_physics_worker.js', import.meta.url), 'utf8');
    // Ammo takes groups and masks as 16-bit shorts: a 17th bit would be lost and nothing would collide.
    assert.match(worker, /const EVERY_GROUP = -1;/);
    assert.ok(worker.indexOf('placeFurniture(data.furniture') < worker.indexOf('if (data.reset)'));
    const driver = readFileSync(new URL('../../face/js/pmx_physics.js', import.meta.url), 'utf8');
    assert.match(driver, /furniture: placed/);
});

// The worker's furniture code, run on a stand-in for Ammo that records what it is asked to do.
const workerSource = readFileSync(new URL('../../face/js/pmx_physics_worker.js', import.meta.url), 'utf8');
const furnitureCode = workerSource.slice(workerSource.indexOf('let furniture = [];'), workerSource.indexOf('function setPose'));
const furnitureModule = await import('data:text/javascript;base64,' + Buffer.from(
    `const FURNITURE_SIZE = 10; const EVERY_GROUP = -1; let physics;\n${furnitureCode}\n`
    + 'export { placeFurniture }; export function usePhysics(p) { physics = p; furniture = []; placed = null; place = null; }',
).toString('base64'));

function standInAmmo() {
    const form = (t) => ({ origin: [...t.origin], turn: [...t.turn] });
    class Vector { constructor(x = 0, y = 0, z = 0) { this.v = [x, y, z]; } setValue(x, y, z) { this.v = [x, y, z]; } }
    class Turn { constructor(x, y, z, w) { this.q = [x, y, z, w]; } setValue(x, y, z, w) { this.q = [x, y, z, w]; } }
    class Transform {
        setIdentity() { this.origin = [0, 0, 0]; this.turn = [0, 0, 0, 1]; }
        setOrigin(v) { this.origin = [...v.v]; }
        setRotation(q) { this.turn = [...q.q]; }
    }
    class MotionState { constructor(t) { this.at = form(t); } setWorldTransform(t) { this.at = form(t); } }
    class Info { constructor(mass, state) { this.mass = mass; this.state = state; } set_m_friction() {} set_m_restitution() {} }
    class Body {
        constructor(info) { this.mass = info.mass; this.state = info.state; this.flags = 0; this.placed = []; }
        getCollisionFlags() { return this.flags; }
        setCollisionFlags(flags) { this.flags = flags; }
        setActivationState(state) { this.activation = state; }
        getMotionState() { return this.state; }
        setCenterOfMassTransform(t) { this.placed.push(form(t)); }
    }
    const Ammo = {
        btVector3: Vector, btQuaternion: Turn, btTransform: Transform, btDefaultMotionState: MotionState,
        btBoxShape: class { constructor(size) { this.half = [...size.v]; } }, btRigidBodyConstructionInfo: Info, btRigidBody: Body,
        destroy() {},
    };
    const world = {
        bodies: new Set(),
        addRigidBody(body, group, mask) { Object.assign(body, { group, mask }); this.bodies.add(body); },
        removeRigidBody(body) { this.bodies.delete(body); },
    };
    return { Ammo, world };
}

// Two boxes packed as pmx_physics.js sends them: centre, turn, half extents.
const twoBoxes = (shift = 0) => Float32Array.from([shift, 1, 2, 0, 0, 0, 1, 3, 1, 2, 4 + shift, 0, 0, 0, 0, 0, 1, 1, 1, 1]);

test('the worker uses split impulse on room resets and restores the Ammo default without furniture', async () => {
    const source = workerSource.replace(/^import .+;\r?\n/gm, '')
        .replace('import(/* @vite-ignore */ moduleUrl)', 'loadAmmo(moduleUrl)');
    const runWorker = new Function('Bone', 'Object3D', 'MMDPhysics', 'self', 'globalThis', 'loadAmmo', 'fetch', source);

    for (const defaultSplit of [false, true]) {
        const { Ammo, world } = standInAmmo();
        let split = defaultSplit;
        const observed = [], replies = [];
        const solver = {
            get_m_splitImpulse: () => split,
            set_m_splitImpulse: value => { split = value; },
        };
        world.getSolverInfo = () => solver;
        class MMDPhysics {
            constructor() { this.world = world; this.bodies = []; }
            reset() {}
            update() { observed.push(split); }
        }
        const scope = { postMessage: reply => replies.push(reply) };
        runWorker(THREE.Bone, THREE.Object3D, MMDPhysics, scope, scope,
            async () => ({ default: async () => Ammo }),
            async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(0) }));
        const transforms = new Float32Array();
        const step = 1 / 65;
        await scope.onmessage({ data: { kind: 'init', assets: 'https://iris.invalid/ammo/',
            parents: [], bodies: [], constraints: [], transforms, step } });
        const runStep = (furniture, reset = false) => scope.onmessage({ data: {
            kind: 'step', transforms, furniture, reset, step, delta: step,
        } });
        await runStep(null, true);
        await runStep(twoBoxes());
        await runStep(twoBoxes(2), true);
        await runStep(twoBoxes(2));
        await runStep(null);

        assert.deepEqual(replies[0], { ready: true });
        assert.ok(replies.slice(1).every(reply => reply.transforms === transforms));
        assert.deepEqual(observed, [defaultSplit, false, true, false, defaultSplit]);
    }
});

test('the worker keeps the furniture as kinematic boxes with no speed: when she turns or jumps they never throw her clothes', () => {
    const { Ammo, world } = standInAmmo();
    globalThis.Ammo = Ammo;
    furnitureModule.usePhysics({ world });
    // Companion has no room: no furniture, step after step, and nothing breaks.
    furnitureModule.placeFurniture(null);
    furnitureModule.placeFurniture(null);
    assert.equal(world.bodies.size, 0);

    furnitureModule.placeFurniture(twoBoxes());
    const bodies = [...world.bodies];
    assert.equal(bodies.length, 2);
    for (const body of bodies) {
        assert.equal(body.mass, 0);
        assert.equal(body.flags & 2, 2, 'kinematic');
        assert.equal(body.group, -1);
        assert.equal(body.mask, -1);
    }

    furnitureModule.placeFurniture(twoBoxes());
    assert.ok(bodies.every((body) => body.placed.length === 0), 'she stood still: nothing is moved');

    // She walked or turned: in her space the furniture moved. Placed twice, so its last place is
    // its place: Bullet then gives it no speed.
    furnitureModule.placeFurniture(twoBoxes(0.5));
    assert.equal(world.bodies.size, 2, 'moved, not made again');
    assert.deepEqual(bodies[0].placed.map((t) => t.origin), [[0.5, 1, 2], [0.5, 1, 2]]);
    assert.deepEqual(bodies[0].state.at.origin, [0.5, 1, 2]);
    assert.deepEqual(bodies[1].placed.map((t) => t.origin), [[4.5, 0, 0], [4.5, 0, 0]]);

    furnitureModule.placeFurniture(null);
    furnitureModule.placeFurniture(null);
    assert.equal(world.bodies.size, 0, 'taken away');
    delete globalThis.Ammo;
});
