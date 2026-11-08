import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as THREE from '../../companion/node_modules/three/build/three.module.js';
import { disposeAvatarTree } from '../../face/js/avatar_lifecycle.js';

const source = name => readFileSync(new URL('../../face/js/' + name, import.meta.url), 'utf8');
const deferred = () => {
    let resolve;
    const promise = new Promise(yes => { resolve = yes; });
    return { promise, resolve };
};
const tick = () => new Promise(setImmediate);
function playerHarness() {
    const callbacks = [], retarget = deferred();
    let conversions = 0;
    class Loader { register() {} load(url, done) { callbacks.push(done); } }
    let code = source('vrm_animation_loader.js').replace(/^import .*;\r?$/gm, '').replace('export class', 'class')
        .replace('let _vrmAnimPlugin = null;', 'let _vrmAnimPlugin = class {};')
        .replace('let _createClip = null;', 'let _createClip = makeClip;');
    for (const type of ['BVH', 'FBX', 'GLB']) {
        code = code.replace(new RegExp("import\\('\\./" + type.toLowerCase() + "_retargeter\\.js[^']*'\\)"),
            `Promise.resolve({ loadAndRetarget${type}: () => retarget.promise })`);
    }
    const Player = new Function('THREE', 'GLTFLoader', 'disposeAvatarTree', 'makeClip', 'retarget', code + '\nreturn VRMAnimationPlayer;')(
        THREE, Loader, disposeAvatarTree, () => {
            conversions++;
            return new THREE.AnimationClip('test', 1, [new THREE.VectorKeyframeTrack('.position', [0, 1], [0, 0, 0, 1, 0, 0])]);
        }, retarget);
    const vrm = { scene: new THREE.Group(), humanoid: {} };
    return { player: new Player(vrm), callbacks, retarget, conversions: () => conversions, vrm };
}

test('a VRMA callback after disposal frees its source without binding the retired rig', async () => {
    const h = playerHarness(), pending = h.player.load('late', '/late.vrma');
    await tick();
    const mixer = h.player.mixer;
    h.player.dispose(); h.player.dispose();
    const geometry = new THREE.BoxGeometry(), material = new THREE.MeshBasicMaterial();
    let freed = 0;
    geometry.addEventListener('dispose', () => freed++);
    const scene = new THREE.Group(); scene.add(new THREE.Mesh(geometry, material));
    h.callbacks[0]({ scene, scenes: [scene, scene], userData: { vrmAnimations: [{}] } });
    assert.equal(await pending, false); assert.equal(h.conversions(), 0); assert.equal(freed, 1);
    assert.equal(mixer.stats.actions.total, 0); assert.equal(mixer.stats.bindings.total, 0);
    assert.equal(h.player.actions.size, 0); assert.equal(h.player.vrm, null);
    assert.equal(await h.player.load('future', '/future.vrma'), false);
});

test('disposing a playing mixer releases actions, bindings, transition and owned listener exactly once', async () => {
    const h = playerHarness(), pending = h.player.load('one', '/one.vrma');
    await tick(); h.callbacks[0]({ scene: new THREE.Group(), userData: { vrmAnimations: [{}] } });
    assert.equal(await pending, true); h.player.play('one', { loop: true }); h.player.update(0.25);
    h.player.stop(0.3);
    const mixer = h.player.mixer, listener = h.player._onLoop;
    assert.ok(mixer.stats.actions.total > 0);
    h.player.dispose(); h.player.dispose();
    assert.equal(mixer.stats.actions.total, 0); assert.equal(mixer.stats.bindings.total, 0);
    assert.equal(mixer.hasEventListener('loop', listener), false);
    assert.equal(h.player.current, null); assert.equal(h.player._transitionAction, null);
    assert.equal(h.player.play('one'), false); h.player.update(1); h.player.stop();
});

for (const extension of ['bvh', 'fbx', 'glb', 'gltf']) {
    test(`late ${extension} retarget completion cannot repopulate a disposed player`, async () => {
        const h = playerHarness(), pending = h.player.load('late', '/late.' + extension);
        await tick(); h.player.dispose();
        h.retarget.resolve({ clip: new THREE.AnimationClip('late', 1, []) });
        assert.equal(await pending, false); assert.equal(h.player.actions.size, 0); assert.equal(h.player.clips.size, 0);
    });
}

function idleHarness() {
    const response = deferred(), json = deferred(), clips = deferred(), players = [], notifications = [];
    let signal, fetches = 0;
    class Player {
        constructor() { players.push(this); this.mixer = new THREE.EventDispatcher(); this.clips = new Map(); this._revisions = new Map(); this._formats = new Map(); }
        load() { return clips.promise.then(() => true); }
        dispose() { this.disposals = (this.disposals || 0) + 1; }
    }
    const code = source('idle_system.js').replace(/^import .*;\r?$/gm, '').replace('export class', 'class');
    const Idle = new Function('THREE', 'VRMAnimationPlayer', 'fetch', 'window', 'CustomEvent', code + '\nreturn IdleSystem;')(
        THREE, Player, (_, options) => { signal = options.signal; fetches++; return response.promise; },
        { dispatchEvent: event => notifications.push(event) }, class { constructor(type) { this.type = type; } });
    const avatar = { avatar: { group: new THREE.Group(), vrm: { scene: new THREE.Group(), humanoid: {} } } };
    const idle = new Idle(avatar);
    return { idle, response, json, clips, players, notifications, signal: () => signal, fetches: () => fetches,
        deliverResponse: () => response.resolve({ ok: true, json: () => json.promise }) };
}

for (const stage of ['response', 'json', 'clips']) {
    test(`retired IdleSystem cannot publish or restart animation after pending ${stage}`, async () => {
        const h = idleHarness(), pending = h.idle._initVRMAPlayer();
        assert.equal(h.idle._initVRMAPlayer(), pending);
        const reload = h.idle.reloadManifest();
        if (stage !== 'response') { h.deliverResponse(); await tick(); }
        if (stage === 'clips') { h.json.resolve({ clips: { idle: 'idle.vrma' } }); await tick(); }
        let returned = 0; h.idle._returnToIdleClipAfterGesture = () => returned++;
        const beforeNotifications = h.notifications.length;
        h.idle.dispose(); h.idle.dispose();
        assert.equal(h.signal().aborted, true);
        h.deliverResponse(); h.json.resolve({ clips: { idle: 'idle.vrma' } }); h.clips.resolve(new Set(['idle']));
        await Promise.all([pending, reload]);
        assert.equal(h.fetches(), 1); assert.equal(h.notifications.length, beforeNotifications); assert.equal(returned, 0);
        assert.equal(h.players.length, stage === 'clips' ? 1 : 0);
        if (h.players.length) assert.equal(h.players[0].disposals, 1);
        assert.equal(h.idle.avatar, null); assert.equal(h.idle._vrmaPlayer, null);
        await h.idle.reloadManifest(); assert.equal(h.fetches(), 1);
    });
}

test('retired IdleSystem detaches its finished callback before disposing the player', async () => {
    const h = idleHarness(), pending = h.idle._initVRMAPlayer();
    h.deliverResponse(); h.json.resolve({ clips: { idle: 'idle.vrma' } }); await tick();
    const player = h.players[0], callback = () => { throw Error('retired callback fired'); };
    player.mixer.addEventListener('finished', callback); h.idle._vrmaFinishedHandler = callback;
    h.idle._pendingVrmaFinish = { action: {} };
    h.idle.dispose(); player.mixer.dispatchEvent({ type: 'finished' });
    assert.equal(player.mixer.hasEventListener('finished', callback), false);
    assert.equal(h.idle._pendingVrmaFinish, null);
    h.clips.resolve(new Set()); await pending;
});

for (const format of ['FBX', 'GLB']) {
    for (const outcome of ['empty', 'success', 'error']) {
        test(`${format} source resources are released after ${outcome}, including retarget errors`, async () => {
            const sourceScene = new THREE.Group(), geometry = new THREE.BoxGeometry(), material = new THREE.MeshBasicMaterial();
            sourceScene.add(new THREE.Mesh(geometry, material));
            let freed = 0; geometry.addEventListener('dispose', () => freed++);
            const animations = outcome === 'empty' ? [] : [{ tracks: [{}] }];
            sourceScene.animations = animations;
            class Loader {
                load(url, done) { done(format === 'FBX' ? sourceScene : { scene: sourceScene, scenes: [sourceScene], animations }); }
            }
            const text = source(format.toLowerCase() + '_retargeter.js');
            const code = text.slice(text.indexOf('export async function loadAndRetarget')).replace('export async', 'async');
            const load = new Function(format === 'FBX' ? 'FBXLoader' : 'GLTFLoader', 'disposeAvatarTree', 'retargetClipToVRMHumanoid',
                code + `\nreturn loadAndRetarget${format};`)(Loader, disposeAvatarTree, () => {
                if (outcome === 'error') throw Error('invalid tracks');
                return { clip: {} };
            });
            const promise = load('/source', { humanoid: {}, scene: new THREE.Group() }, 'test');
            if (outcome === 'error') await assert.rejects(promise, /invalid tracks/);
            else assert.equal(Boolean(await promise), outcome === 'success');
            assert.equal(freed, 1); assert.equal(sourceScene.children.length, 0);
        });
    }
}
