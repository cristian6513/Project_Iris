import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import * as THREE from '../../companion/node_modules/three/build/three.module.js';
import {
    AvatarSwapCoordinator, commitAvatarReplacement, disposeAvatarTree, retainAvatarResources,
} from '../../face/js/avatar_lifecycle.js';

const deferred = () => {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
};
const tick = () => new Promise(setImmediate);
const source = name => readFileSync(new URL('../../face/js/' + name, import.meta.url), 'utf8');
const managerClass = source('avatar.js').split('export class AvatarManager')[1].split('// ═')[0];

function tracked(resource) {
    let count = 0;
    resource.addEventListener('dispose', () => count++);
    return () => count;
}

test('shared geometry, textures and nested shader uniforms have one final owner', () => {
    const geometry = new THREE.BoxGeometry();
    const texture = new THREE.Texture();
    let bitmapCloses = 0;
    texture.image = { close() { bitmapCloses++; } };
    const material = new THREE.ShaderMaterial({ uniforms: { maps: { value: [texture, { other: texture }] } } });
    const countGeometry = tracked(geometry), countTexture = tracked(texture), countMaterial = tracked(material);
    const a = new THREE.Group(), b = new THREE.Group();
    a.add(new THREE.Mesh(geometry, material), new THREE.Mesh(geometry, material));
    b.add(new THREE.Mesh(geometry, material));
    const leaseA = retainAvatarResources(a), leaseB = retainAvatarResources(b);
    disposeAvatarTree(a, { lease: leaseA });
    assert.deepEqual([countGeometry(), countTexture(), countMaterial(), bitmapCloses], [0, 0, 0, 0]);
    disposeAvatarTree(b, { lease: leaseB });
    disposeAvatarTree(b, { lease: leaseB });
    assert.deepEqual([countGeometry(), countTexture(), countMaterial(), bitmapCloses], [1, 1, 1, 1]);
});

test('room resources remain usable when borrowed by a retired avatar', () => {
    const scene = new THREE.Scene(), group = new THREE.Group();
    const material = new THREE.MeshBasicMaterial({ map: new THREE.Texture() });
    const room = new THREE.Mesh(new THREE.BoxGeometry(), material);
    scene.add(room, group);
    group.add(new THREE.Mesh(new THREE.BoxGeometry(), material));
    const materialCount = tracked(material), textureCount = tracked(material.map);
    disposeAvatarTree(group, { preserveRoots: [scene] });
    assert.equal(materialCount(), 0);
    assert.equal(textureCount(), 0);
    assert.deepEqual(scene.children, [room]);
    disposeAvatarTree(scene);
});

function swapHarness() {
    const initial = { id: 'A', avatar: { group: {} } };
    const app = { visible: initial, created: [], released: [], urls: [] };
    const gates = new Map();
    const coordinator = new AvatarSwapCoordinator({
        create(id) {
            const candidate = { id, avatar: { group: {}, vrm: id === 'not-a-vrm' ? null : {} },
                init: () => gates.get(id)?.promise || Promise.resolve() };
            app.created.push(candidate);
            return candidate;
        },
        commit(candidate) { const old = app.visible; app.visible = candidate; return old; },
        release(candidate) { if (candidate) app.released.push(candidate.id); },
    });
    return { app, coordinator, gates, initial };
}

for (const failLate of [false, true]) {
    test(`A → slow B → C never resurrects B (${failLate ? 'failed' : 'loaded'} late)`, async () => {
        const { app, coordinator, gates } = swapHarness();
        const gate = deferred(); gates.set('B', gate);
        const pending = coordinator.replace('B', { releaseUrl: () => app.urls.push('B') });
        assert.equal(app.visible.id, 'A');
        assert.equal(await coordinator.replace('C'), true);
        failLate ? gate.reject(new Error('late failure')) : gate.resolve();
        assert.equal(await pending, false);
        assert.equal(app.visible.id, 'C');
        assert.deepEqual(app.released, ['A', 'B']);
        assert.deepEqual(app.urls, ['B']);
    });
}

test('failed current candidate preserves visible avatar and releases only its owned URL', async () => {
    const { app, coordinator, gates, initial } = swapHarness();
    const gate = deferred(); gates.set('B', gate);
    const pending = coordinator.replace('B', { releaseUrl: () => app.urls.push('owned') });
    gate.reject(new Error('invalid model'));
    await assert.rejects(pending, /invalid model/);
    assert.equal(app.visible, initial);
    assert.deepEqual(app.released, ['B']);
    assert.deepEqual(app.urls, ['owned']);
});

test('surface disposal invalidates loads already in flight and future selections', async () => {
    const { app, coordinator, gates } = swapHarness();
    const gate = deferred(); gates.set('B', gate);
    const pending = coordinator.replace('B');
    coordinator.dispose(); gate.resolve();
    assert.equal(await pending, false);
    assert.equal(await coordinator.replace('/face/animations/iris.vrm'), false);
    assert.equal(app.visible.id, 'A');
    assert.deepEqual(app.released, ['B']);
});

test('a file that loads without a VRM inside is refused and leaves the visible avatar', async () => {
    const { app, coordinator } = swapHarness();
    await assert.rejects(coordinator.replace('not-a-vrm'), /VRM válido/);
    assert.equal(app.visible.id, 'A');
    assert.deepEqual(app.released, ['not-a-vrm']);
});

for (const resource of ['/avatars/iris.vrm', '../avatars/iris.vrm', 'C:\\Models\\iris.vrm', '\\\\server\\iris.vrm']) {
    test(`lifecycle treats resource as an opaque loader input: ${resource}`, async () => {
        const { app, coordinator, gates } = swapHarness();
        const gate = deferred(); gates.set(resource, gate);
        const pending = coordinator.replace(resource);
        await coordinator.replace('next'); gate.resolve();
        assert.equal(await pending, false);
        assert.equal(app.created[0].id, resource);
        assert.equal(app.visible.id, 'next');
        assert.deepEqual(app.released, ['A', resource]);
    });
}

function managerHarness() {
    const created = [], gates = new Map(), limited = [];
    class FakeAvatar {
        constructor(scene) {
            this.scene = scene; this.group = new THREE.Group(); this.disposals = 0;
            this.group.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial()));
            created.push(this);
        }
        async init(url) {
            this.url = url;
            this.scene.add(this.group);
            if (gates.has(url)) await gates.get(url).promise;
            this.vrm = url ? {} : null;
        }
        dispose() { this.disposals++; }
    }
    const limitTextures = async (root, max) => { limited.push([root, max]); };
    const AvatarManager = new Function('THREE', 'VRMAvatar', 'disposeAvatarTree', 'retainAvatarResources', 'limitTextures',
        'return class AvatarManager' + managerClass)(THREE, FakeAvatar, disposeAvatarTree, retainAvatarResources, limitTextures);
    return { AvatarManager, created, gates, limited };
}

test('each loaded model gets the texture limit of the moment, before it is shown', async () => {
    const { AvatarManager, created, limited } = managerHarness();
    let quality = 0;
    const manager = new AvatarManager(new THREE.Scene(), 'first', 'iris', { textureLimit: () => quality });
    await manager.init();
    quality = 1024;
    await manager._replace('second', false);
    assert.deepEqual(limited.map(([root, max]) => [root, max]), [[created[0].group, 0], [created[1].group, 1024]]);
    const plain = managerHarness();
    await new plain.AvatarManager(new THREE.Scene(), 'first').init();
    assert.equal(plain.limited[0][1], 0, 'no option keeps textures as they come');
});

test('AvatarManager stages failed and superseded VRM candidates and disposes every owned tree', async () => {
    const { AvatarManager, created, gates } = managerHarness();
    const scene = new THREE.Scene(), manager = new AvatarManager(scene, 'first', 'iris');
    await manager.init();
    const original = manager.avatar;
    const bad = deferred(); gates.set('bad', bad);
    const failed = manager._replace('bad', false);
    assert.equal(manager.avatar, original); assert.equal(scene.children.length, 1);
    bad.reject(new Error('bad')); await assert.rejects(failed, /bad/);
    assert.equal(manager.avatar, original); assert.equal(created[1].disposals, 1);
    const slow = deferred(); gates.set('slow', slow);
    const pending = manager._replace('slow', false);
    await manager._replace('other', false); slow.resolve();
    assert.equal(await pending, false);
    assert.equal(scene.children.length, 1);
    assert.equal(original.disposals, 1); assert.equal(created[2].disposals, 1);
    manager.dispose(); manager.dispose();
    assert.equal(created[3].disposals, 1); assert.equal(scene.children.length, 0);
});

test("a model of the person's own that cannot be loaded gives way to IRIS; IRIS has no stand-in", async () => {
    const { AvatarManager, created, gates } = managerHarness();
    const broken = deferred(); gates.set('/avatars/mine.vrm', broken); broken.reject(new Error('corrupt'));
    const scene = new THREE.Scene(), manager = new AvatarManager(scene, '/avatars/mine.vrm', '/face/animations/iris.vrm');
    assert.equal(await manager.init(), true);
    assert.equal(manager.vrmUrl, '/face/animations/iris.vrm', 'who shows is visible to the caller');
    assert.equal(manager.avatar.url, '/face/animations/iris.vrm');
    assert.equal(created[0].disposals, 1); assert.equal(scene.children.length, 1);

    const strict = new AvatarManager(new THREE.Scene(), '/avatars/mine.vrm', '/face/animations/iris.vrm');
    await assert.rejects(strict.init({ fallback: false }), /corrupt/, 'a replacement never falls back silently');

    const irisGone = deferred(); gates.set('/face/animations/iris.vrm', irisGone); irisGone.reject(new Error('missing'));
    const empty = new THREE.Scene(), lonely = new AvatarManager(empty, '/face/animations/iris.vrm');
    await assert.rejects(lonely.init(), /missing/);
    assert.equal(lonely.avatar, null); assert.equal(empty.children.length, 0);
});

test('commit preserves audio, speech envelope, listening and room position across different rest yaw', () => {
    const scene = new THREE.Scene();
    const manager = yaw => ({ scene, avatar: { group: new THREE.Group(), restYaw: yaw } });
    const old = manager(0.2), candidate = manager(Math.PI);
    old.avatar.group.position.set(2, 0.75, -3);
    old.avatar.group.rotation.set(0.1, 0.6, -0.1);
    old.avatar.group.userData.stageVisualLiftApplied = true;
    scene.add(old.avatar.group);
    const oldIdle = { paused: true, _activityState: 'listening', params: { energy: 0.8 }, dispose() { this.disposed = true; } };
    const audio = { playing: true }, context = { close() { throw new Error('shared audio closed'); } };
    const lipSync = { avatar: old, audio, audioContext: context };
    const speech = { avatarManager: old, _talking: true, _envelope: 0.8, _syntheticMouthEnabled: false, _bones: old };
    // The old group is mid-breath: 0.75 is its rest height plus this offset.
    const micro = { avatarManager: old, _listening: true, _lastBreathOffset: 0.125,
        resetForRigSwap() { this._listening = false; this.resetAt = candidate.avatar.group.position.y; }, setListening(v) { this._listening = v; } };
    const room = {}, renderer = {};
    const actions = { avatar: old, room, _heldYaw: 0.6, hotspotTransition: { fromYaw: 0.6, toYaw: 1 }, setIdleSystem(idle) { this.idleSystem = idle; } };
    const app = { scene, room, renderer, avatarManager: old, idle: oldIdle, lipSync, speechAnimator: speech, microLife: micro, sceneActions: actions };
    const previous = commitAvatarReplacement(app, candidate, { createIdle: avatar => ({ avatar, params: {}, setActivity(state) { this._activityState = state; } }) });
    assert.equal(previous, old);
    assert.equal(app.lipSync, lipSync); assert.equal(lipSync.audio, audio); assert.equal(lipSync.audioContext, context);
    assert.equal(app.speechAnimator, speech); assert.equal(speech._talking, true); assert.equal(speech._envelope, 0.8);
    assert.equal(speech._syntheticMouthEnabled, false); assert.equal(speech._bones, null);
    assert.equal(app.idle.paused, true); assert.equal(app.idle._activityState, 'listening');
    assert.equal(micro._listening, true); assert.equal(micro.avatarManager, candidate);
    assert.equal(candidate.avatar.microLifeOwnsBlink, true); assert.equal(candidate.avatar.animationOwnsBlink, undefined);
    // Rest height without the breath, already in place when microLife re-baselines.
    assert.deepEqual(candidate.avatar.group.position.toArray(), [2, 0.625, -3]);
    assert.equal(micro.resetAt, 0.625);
    assert.ok(Math.abs(candidate.avatar.group.rotation.y - (0.4 + Math.PI)) < 1e-8);
    assert.equal(actions.idleSystem, app.idle); assert.equal(actions.room, room); assert.equal(app.renderer, renderer);
    assert.ok(Math.abs(actions._heldYaw - candidate.avatar.group.rotation.y) < 1e-8);
    assert.equal(oldIdle.disposed, true);
});

function appMethod(name, deps) {
    const block = source('app.js').match(new RegExp('^    (?:async )?' + name + '\\([^]*?^    \\}', 'm'))?.[0];
    assert.ok(block);
    return new Function(...Object.keys(deps), 'return ({' + block + '})')(...Object.values(deps))[name];
}

const IRIS = '/face/animations/iris.vrm';
const identityHandler = () => appMethod('_handleIdentityChange', {
    avatarUrl: (config, irisUrl, override) => String(override || config.avatar_vrm_path || '').trim() || irisUrl,
    IRIS_AVATAR_URL: IRIS,
});

test('the real identity handler cancels a pending change when the visible model is chosen again', async () => {
    const { coordinator, gates, app: h } = swapHarness();
    const gate = deferred(); gates.set('slow', gate);
    const app = { _avatarSwap: coordinator, avatarManager: { vrmUrl: IRIS }, config: {}, subtitles: { show() {} } };
    app._replaceAvatar = appMethod('_replaceAvatar', {});
    const pending = app._replaceAvatar('slow');
    assert.equal(coordinator.pending, true);
    await identityHandler().call(app, { detail: { vrmUrl: '' } }); // «Volver a IRIS» while IRIS shows
    assert.equal(coordinator.pending, false);
    gate.resolve(); assert.equal(await pending, false);
    assert.equal(h.visible.id, IRIS);
});

test('the identity handler shows the chosen model, and IRIS for an empty one', async () => {
    const { coordinator, app: h } = swapHarness();
    const app = { _avatarSwap: coordinator, avatarManager: { vrmUrl: '/avatars/mine.vrm' }, config: {}, subtitles: { show() {} } };
    app._replaceAvatar = appMethod('_replaceAvatar', {});
    const handler = identityHandler();
    await handler.call(app, { detail: { name: 'Iris' } });
    assert.equal(h.created.length, 0, 'a name change leaves the model alone');
    await handler.call(app, { detail: { vrmUrl: '/avatars/mine.vrm' } });
    assert.equal(h.created.length, 0, 'the visible model is not reloaded');
    await handler.call(app, { detail: { vrmUrl: '' } });
    assert.equal(h.visible.id, IRIS);
    app.avatarManager = { vrmUrl: IRIS };
    await handler.call(app, { detail: { vrmUrl: '/avatars/other.vrm' } });
    assert.equal(h.visible.id, '/avatars/other.vrm');
});

test('the real drop handler reserves generation before upload and loses to a newer panel selection', async () => {
    const { app: h, coordinator } = swapHarness();
    const handlers = {}, upload = deferred();
    const app = { _avatarSwap: coordinator, config: {}, subtitles: { show() {} }, panel: { addMessage() {} } };
    app._replaceAvatar = appMethod('_replaceAvatar', {});
    const document = { body: { classList: { add() {}, remove() {} }, addEventListener(name, handler) { handlers[name] = handler; } } };
    appMethod('_setupDragAndDrop', { document, fetch: () => upload.promise, FormData: class { append() {} } }).call(app);
    const pending = handlers.drop({ preventDefault() {}, dataTransfer: { files: [{ name: 'old.vrm' }] } });
    await app._replaceAvatar('/avatars/new.vrm');
    upload.resolve({ ok: true, json: async () => ({ status: 'saved', avatar_url: '/avatars/old.vrm' }) });
    await pending;
    assert.equal(h.visible.id, '/avatars/new.vrm');
    assert.equal(h.created.length, 1);
});

test('the real drop handler revokes temporary glTF URLs after load failure', async () => {
    const { coordinator, gates } = swapHarness(), handlers = {}, revoked = [];
    const gate = deferred(); gates.set('blob:preview', gate);
    const app = { _avatarSwap: coordinator, config: {}, subtitles: { show() {} }, panel: { addMessage() {} } };
    app._replaceAvatar = appMethod('_replaceAvatar', {});
    const document = { body: { classList: { add() {}, remove() {} }, addEventListener(name, handler) { handlers[name] = handler; } } };
    const URL = { createObjectURL: () => 'blob:preview', revokeObjectURL: value => revoked.push(value) };
    appMethod('_setupDragAndDrop', { document, URL }).call(app);
    const pending = handlers.drop({ preventDefault() {}, dataTransfer: { files: [{ name: 'preview.gltf' }] } });
    await tick(); gate.reject(new Error('bad preview')); await pending;
    assert.deepEqual(revoked, ['blob:preview']);
});
