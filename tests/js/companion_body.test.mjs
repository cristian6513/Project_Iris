// The Companion's routines: the room's own logic, with what she can do standing where she is.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { RoomRoutines } from '../../face/js/room_routines.js';

const threeUrl = new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href;
const gltfUrl = new URL('../../companion/node_modules/three/examples/jsm/loaders/GLTFLoader.js', import.meta.url).href;
const THREE = await import(threeUrl);
const faceUrl = (name) => new URL(`../../face/js/${name}`, import.meta.url);
const moduleUrl = (text) => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const three = (text) => text.replace("from 'three'", `from '${threeUrl}'`);
const handProp = moduleUrl(three(await readFile(faceUrl('hand_prop.js'), 'utf8')));
const conjured = moduleUrl(three(await readFile(faceUrl('conjured_props.js'), 'utf8')));
const { CompanionBody, CompanionProps, STAND_INS } = await import(moduleUrl(three(await readFile(faceUrl('companion_body.js'), 'utf8'))
    .replace("from 'three/addons/loaders/GLTFLoader.js'", `from '${gltfUrl}'`)
    .replace("from './conjured_props.js'", `from '${conjured}'`)
    .replace("from './hand_prop.js'", `from '${handProp}'`)));

function idleFixture({ missing = [] } = {}) {
    const calls = [];
    const idle = {
        pose: null,
        rising: false,
        canHold: (name) => !missing.includes(name),
        isSeated(name = this.pose) { return Boolean(this.pose) && this.pose === name; },
        isGettingUp() { return this.rising; },
        trigger(name) { calls.push(`trigger:${name}`); if (name.startsWith('stand_')) this.pose = name; return true; },
        standUp() { calls.push('standUp'); this.pose = null; this.rising = true; return true; },
        putAway() { calls.push('putAway'); return false; },
    };
    return { idle, calls };
}

test('every room order has a stand-in, and none of them walks', () => {
    for (const order of ['read_center', 'tablet_center', 'sit_sofa', 'read_sofa', 'tablet_sofa', 'sit_bed', 'read_bed', 'tablet_bed', 'sit_desk', 'use_pc', 'look_window', 'lie_bed']) {
        assert.ok(STAND_INS[order], order);
    }
    assert.ok(Object.values(STAND_INS).every((pose) => pose.startsWith('stand_') || ['stretch', 'idle_lazy'].includes(pose)));
    assert.equal(STAND_INS.read_sofa, 'stand_reading');
    assert.equal(STAND_INS.use_pc, 'stand_tablet', 'a long task: the tablet, as there is no PC here');
    assert.equal(new CompanionBody({ getIdle: () => null }).isWalking(), false);
});

test('without the object or its clip she takes a plain rest, and with nothing at all she does nothing', () => {
    const { idle, calls } = idleFixture();
    const body = new CompanionBody({ getIdle: () => idle, hasProp: () => false });
    assert.equal(body.pose('read_sofa'), 'idle_lazy', 'no book');
    body.execute('read_sofa');
    assert.deepEqual(calls, ['trigger:idle_lazy']);
    const clips = idleFixture({ missing: ['stand_reading'] });
    assert.equal(new CompanionBody({ getIdle: () => clips.idle, hasProp: () => true }).pose('read_sofa'), 'idle_lazy', 'no clip');
    const none = idleFixture({ missing: ['stand_reading', 'idle_lazy'] });
    const empty = new CompanionBody({ getIdle: () => none.idle, hasProp: () => true });
    assert.equal(empty.canRun('read_sofa'), false);
    assert.equal(empty.execute('read_sofa'), false);
    assert.equal(empty.canRun('nonsense'), false);
});

test('she puts one thing away before taking the other', () => {
    const { idle, calls } = idleFixture();
    const body = new CompanionBody({ getIdle: () => idle, hasProp: () => true });
    body.execute('read_sofa');
    assert.equal(idle.pose, 'stand_reading');
    body.execute('read_bed');
    assert.deepEqual(calls, ['trigger:stand_reading'], 'already reading');
    body.execute('use_pc');
    assert.equal(calls.at(-1), 'standUp');
    assert.equal(body.isBusy(), true);
    body.update();
    assert.equal(calls.at(-1), 'standUp', 'still putting it away');
    idle.rising = false;
    body.update();
    assert.equal(calls.at(-1), 'trigger:stand_tablet');
    assert.equal(body.isBusy(), false);
});

test('leaving a step puts the thing away and forgets what was waiting', () => {
    const { idle, calls } = idleFixture();
    const body = new CompanionBody({ getIdle: () => idle, hasProp: () => true });
    assert.equal(body.execute('return_center'), true, 'nothing to leave');
    assert.deepEqual(calls, []);
    body.execute('read_sofa');
    body.execute('use_pc');
    idle.pose = 'stand_reading';
    body.execute('return_center');
    assert.equal(body.waiting, null);
    assert.equal(calls.at(-1), 'standUp');
});

test('the room\'s routines run on this body: near, then the stand-ins of a visit, and speech stops them', () => {
    const { idle, calls } = idleFixture();
    const body = new CompanionBody({ getIdle: () => idle, hasProp: () => true });
    const clock = { t: 0 };
    const context = { life: 'calm', reducedMotion: false, visible: true, exploring: false, activity: 'idle' };
    const routines = new RoomRoutines({ scenes: body, context: () => context, now: () => clock.t, random: () => 0.9 });
    clock.t = 61;
    routines.update();
    assert.match(calls.at(-1), /^trigger:(stand_reading|stand_tablet)$/);
    clock.t = 62;
    context.activity = 'thinking';
    routines.update();
    assert.ok(calls.includes('putAway'), 'she frees her hands to answer');
    context.activity = 'idle';
    assert.equal(body.heldByOrder(), false);
});

test('a tool that runs long has her take the tablet, and she lets go of it to answer', () => {
    const { idle, calls } = idleFixture();
    const body = new CompanionBody({ getIdle: () => idle, hasProp: () => true });
    const clock = { t: 0 };
    const context = { life: 'calm', reducedMotion: false, visible: true, exploring: false, activity: 'idle' };
    const routines = new RoomRoutines({ scenes: body, context: () => context, now: () => clock.t });
    clock.t = 1;
    routines.turnStarted(1);
    routines.toolRunning(1);
    clock.t = 22;
    routines.update();
    assert.equal(calls.at(-1), 'trigger:stand_tablet');
    context.activity = 'speaking';
    clock.t = 23;
    routines.update();
    assert.equal(calls.at(-1), 'putAway');
});

test('spoken to while she swaps one thing for the other, she takes up nothing', () => {
    const { idle, calls } = idleFixture();
    const body = new CompanionBody({ getIdle: () => idle, hasProp: () => true });
    const clock = { t: 0 };
    const context = { life: 'calm', reducedMotion: false, visible: true, exploring: false, activity: 'idle' };
    const routines = new RoomRoutines({ scenes: body, context: () => context, now: () => clock.t, random: () => 0.9 });
    clock.t = 61;
    routines.update();
    const first = idle.pose;
    body.execute(first === 'stand_reading' ? 'tablet_center' : 'read_center');
    assert.equal(calls.at(-1), 'standUp');
    routines.turnStarted(1);
    routines.update();
    assert.equal(body.waiting, null);
    idle.rising = false;
    routines.update();
    body.update();
    assert.ok(!calls.slice(1).some((call) => call.startsWith('trigger:')), `nothing comes out: ${calls}`);
});

function gltfLoader(models) {
    return {
        async loadAsync(url) {
            const name = url.split('/').at(-1);
            if (!models[name]) throw Error('404');
            return { scene: models[name]() };
        },
    };
}
const book = () => {
    const scene = new THREE.Group();
    scene.add(new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshStandardMaterial()));
    return scene;
};
const tablet = () => {
    const scene = new THREE.Group();
    for (const name of ['tablet_body', 'tablet_screen', 'tablet_base']) {
        const mesh = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshStandardMaterial());
        mesh.name = name;
        scene.add(mesh);
    }
    return scene;
};

test('the props load from the room\'s models and live in the scene, hidden', async () => {
    const scene = new THREE.Scene();
    const props = new CompanionProps({ scene, modelBase: '/face/public/assets/room' });
    assert.equal(props.has('conjured'), false, 'nothing yet');
    await props.load(gltfLoader({ 'book-v1.glb': book, 'tablet-v2.glb': tablet }));
    assert.equal(props.has('conjured'), true);
    assert.equal(props.has('tablet'), true);
    const held = scene.getObjectByName('conjured_tablet');
    assert.equal(held.visible, false);
    assert.equal(scene.getObjectByName('tablet_base'), undefined, 'the stand is not hers to carry');
    const glows = [];
    held.traverse((child) => { if (child.isMesh) glows.push(child.material.userData.glow); });
    assert.ok(Math.max(...glows) < 0.1, 'no room lights here: a brighter glow is a flat coloured card');
    assert.ok(Math.min(...glows) > 0, 'dimmed, not lost against dark clothes');
});

test('a model that fails to load costs only that object', async () => {
    const props = new CompanionProps({ scene: new THREE.Scene(), modelBase: '' });
    await props.load(gltfLoader({ 'tablet-v2.glb': tablet }));
    assert.equal(props.has('conjured'), false);
    assert.equal(props.has('tablet'), true);
    props.update(0.1, null, null);
    props.drop();
});
