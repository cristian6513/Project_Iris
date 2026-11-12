import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { companionAvatarSelection, CompanionAvatarSync } from '../../face/js/companion_avatar_sync.js';
import { isPmxArchive } from '../../face/js/avatar_state.js';
import { SpeechPresentation } from '../../face/js/speech_presentation.js';
import { GraphicsQuality, graphicsQuality } from '../../face/js/graphics_quality.js';

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}
const tick = () => new Promise(setImmediate);

test("IRIS comes from the Companion's bundle, local VRMs are versioned and remote URLs stay intact", () => {
    const iris = '/face/animations/iris.vrm';
    assert.deepEqual(companionAvatarSelection({}, iris), { resource: iris, own: false, key: '["iris",""]' });
    assert.equal(companionAvatarSelection({ avatar_type: 'particle', particle_style: 'cortana' }, iris).resource, iris,
        'a config from before the geometric and particle avatars shows IRIS');
    const local = companionAvatarSelection({ avatar_vrm_path: 'C:\\Models\\Mi avatar.vrm', avatar_revision: 'v2' }, iris);
    assert.deepEqual([local.resource, local.own], ['/avatars/Mi%20avatar.vrm?v=v2', true]);
    const url = 'https://example.com/avatar.vrm?signature=abc';
    assert.equal(companionAvatarSelection({ avatar_vrm_path: url, avatar_revision: 'x' }, iris).resource, url);
    const cfg = { avatar_vrm_path: '/api/avatar/current' };
    assert.notEqual(companionAvatarSelection({ ...cfg, avatar_revision: 'a' }).key, companionAvatarSelection({ ...cfg, avatar_revision: 'b' }).key);
});

test('an event during an old config fetch discards that snapshot', async () => {
    const old = deferred(), applied = [];
    let reads = 0;
    const sync = new CompanionAvatarSync({ readConfig: () => ++reads === 1 ? old.promise : Promise.resolve('new'), applyConfig: async cfg => applied.push(cfg) });
    const pending = sync.request(); sync.request(true);
    old.resolve('old'); await pending;
    assert.deepEqual(applied, ['new']); assert.equal(reads, 2);
});

test('rapid avatar changes supersede a pending load without overlapping loaders', async () => {
    const load = deferred(), committed = [];
    let config = 'A', active = 0, peak = 0;
    const sync = new CompanionAvatarSync({ readConfig: async () => config, applyConfig: async (cfg, current) => {
        peak = Math.max(peak, ++active);
        if (cfg === 'A') await load.promise;
        if (current()) committed.push(cfg);
        active--;
    } });
    const pending = sync.request(); await tick();
    config = 'B'; sync.request(true); config = 'C'; sync.request(true);
    load.resolve(); await pending;
    assert.deepEqual(committed, ['C']); assert.equal(peak, 1);
});

test('periodic checks do not invalidate a slow but current model load', async () => {
    const load = deferred(); let committed = false, reads = 0;
    const sync = new CompanionAvatarSync({ readConfig: async () => ++reads, applyConfig: async (_, current) => { await load.promise; committed = current(); } });
    const pending = sync.request(); await tick();
    assert.equal(sync.request(), pending); assert.equal(sync.request(), pending);
    load.resolve(); await pending;
    assert.equal(committed, true); assert.equal(reads, 1);
});

test('failed loads can retry and disposed surfaces cannot commit late completions', async () => {
    let fail = true, applied = 0, errors = 0;
    const load = deferred();
    const sync = new CompanionAvatarSync({ readConfig: async () => { if (fail) throw Error('offline'); return {}; }, applyConfig: async (_, current) => { await load.promise; if (current()) applied++; }, onError: () => errors++ });
    await sync.request(); assert.equal(errors, 1);
    fail = false; const pending = sync.request(); await tick(); sync.dispose(); load.resolve(); await pending;
    assert.equal(applied, 0);
});

const source = readFileSync(new URL('../../face/companion/companion.js', import.meta.url), 'utf8');
function method(name, deps = {}) {
    const block = source.match(new RegExp('^    (?:async )?' + name + '\\([^]*?^    \\}', 'm'))?.[0];
    assert.ok(block, name);
    return new Function(...Object.keys(deps), 'return ({' + block.trim() + '})')(...Object.values(deps))[name];
}
function appHarness({ gate, broken = false, visible = true } = {}) {
    const disposed = [], loaded = [], released = [], errors = [];
    class Scene { add(group) { this.last = group; } }
    class AvatarManager {
        constructor(scene, url, irisUrl, options) {
            Object.assign(this, { scene, url, irisUrl, options, vrmUrl: url }); this.avatar = { group: {}, vrm: {} }; loaded.push(this);
        }
        async init({ fallback }) {
            if (gate) await gate.promise;
            if (!broken) return;
            if (!fallback) throw new Error('El archivo no es un VRM.');
            this.vrmUrl = this.irisUrl; // the real manager shows IRIS instead
        }
        setEmotion() {}
    }
    class IdleSystem { constructor(avatar) { this.avatar = avatar; } pause() { this.paused = true; } setActivity(state) { this._activityState = state; } }
    class SpeechAnimator { constructor(avatar) { this.avatarManager = avatar; } setSyntheticMouthEnabled(value) { this.synthetic = value; } start() { this._talking = true; } }
    const old = { avatar: { group: {} } };
    const app = { config: {}, scene: new Scene(), avatarManager: visible ? old : null, _appliedAvatarKey: 'old',
        bridge: { native: true, coreAssetUrl: async () => 'blob:new', releaseCoreAssetUrl: url => released.push(url) },
        _applyIdentityAndTheme() {}, _cancelWindowWalk() {}, _showError: message => errors.push(message), lipSync: { avatar: old, currentAudio: 'keep playing' },
        idle: { avatar: old, _activityState: 'listening', disposals: 0, dispose() { this.disposals++; } }, speechAnimator: { _talking: true, _syntheticMouthEnabled: false } };
    app.speechPresentation = new SpeechPresentation({
        getIdle: () => app.idle, getSpeechAnimator: () => app.speechAnimator,
        applyExpression: emotion => app.avatarManager?.setEmotion?.(emotion),
    });
    app.speechPresentation.start({ emotion: 'feliz' });
    app.apply = method('_applyAvatarConfig', { THREE: { Scene }, AvatarManager, IdleSystem, SpeechAnimator, AnimationLibrarySync: class { start() {} async refresh() {} }, LipSyncEngine: class {},
        companionAvatarSelection, isPmxArchive, graphicsQuality, localAnimationBase: () => '/animations', IRIS_AVATAR_FILE: 'iris.vrm', IRIS_AVATAR_REVISION: '12',
        disposeCompanionAvatar: manager => { if (manager) disposed.push(manager); } });
    return { app, old, disposed, loaded, released, errors };
}
const vrm = { avatar_vrm_path: '/avatars/new.vrm', avatar_revision: '2' };

test('Companion keeps the PMX format when Core serves the archive as a blob', async () => {
    const h = appHarness();
    await h.app.apply({ ...vrm, avatar_vrm_path: '/avatars/new.ZIP' }, () => true);
    assert.equal(h.loaded[0].url, 'blob:new');
    assert.equal(h.loaded[0].options.pmxBlob, true);
    assert.deepEqual(h.released, ['blob:new']);
});

test('Companion applies a recovered quality preference to the PMX that is already visible', async () => {
    const h = appHarness();
    const rates = [];
    const physics = { heavy: true, setRate: hz => rates.push(hz) };
    h.app.config = { avatar_vrm_path: '/avatars/heavy.zip', render_quality: 'balanced' };
    h.app._appliedAvatarKey = companionAvatarSelection(h.app.config).key;
    h.app.graphicsQuality = new GraphicsQuality({
        getRenderer: () => null, getComposer: () => null, getAvatarPhysics: () => physics,
    });
    await h.app.apply({ render_quality: 'saving' }, () => true);
    await h.app.apply({ render_quality: 'saving' }, () => true);
    assert.equal(h.app.graphicsQuality.quality, 'saving');
    assert.deepEqual(rates, [32.5]);
    assert.equal(h.app.avatarManager, h.old);
    assert.equal(h.loaded.length, 0);
});

test('Companion swaps only after loading and rewires animation while keeping live lip sync', async () => {
    const gate = deferred(), h = appHarness({ gate });
    const oldIdle = h.app.idle, lipSync = h.app.lipSync;
    const pending = h.app.apply(vrm, () => true); await tick();
    assert.equal(h.app.avatarManager, h.old); assert.equal(h.disposed.length, 0);
    gate.resolve(); await pending;
    assert.notEqual(h.app.idle, oldIdle);
    assert.equal(oldIdle.disposals, 1);
    assert.equal(h.app.idle._activityState, 'listening');
    assert.equal(h.app.idle.avatar, h.app.avatarManager);
    assert.equal(h.app.speechAnimator.avatarManager, h.app.avatarManager);
    assert.equal(h.app.speechAnimator._talking, true);
    assert.equal(h.app.speechAnimator.synthetic, false);
    assert.equal(h.app.lipSync, lipSync); assert.equal(lipSync.avatar, h.app.avatarManager);
    assert.deepEqual(h.disposed, [h.old]); assert.deepEqual(h.released, ['blob:new']);
    await h.app.apply(vrm, () => true); assert.equal(h.loaded.length, 1);
});

test('failed or superseded replacements leave the visible avatar and animations intact', async () => {
    const failed = appHarness({ broken: true });
    await assert.rejects(failed.app.apply(vrm, () => true), /VRM/);
    assert.equal(failed.app.avatarManager, failed.old); assert.equal(failed.app._appliedAvatarKey, 'old');
    assert.equal(failed.app.idle.disposals, 0);
    assert.deepEqual(failed.disposed, [failed.loaded[0]]);
    const gate = deferred(), stale = appHarness({ gate }); let current = true;
    const pending = stale.app.apply(vrm, () => current); await tick(); current = false; gate.resolve(); await pending;
    assert.equal(stale.app.avatarManager, stale.old);
    assert.equal(stale.app.idle.disposals, 0);
    assert.deepEqual(stale.disposed, [stale.loaded[0]]);
    assert.deepEqual(stale.released, ['blob:new']);
});

test("IRIS loads from the Companion's own bundle, without asking Core for the file", async () => {
    const h = appHarness({ visible: false });
    await h.app.apply({ avatar_vrm_path: '' }, () => true);
    assert.equal(h.loaded[0].url, '/animations/iris.vrm?v=12');
    assert.deepEqual(h.released, [], 'no Core asset URL was made');
    assert.equal(h.app._appliedAvatarKey, '["iris",""]');
});

test('a model of their own that cannot load at start shows IRIS and says so, then retries later', async () => {
    const h = appHarness({ broken: true, visible: false });
    await h.app.apply(vrm, () => true);
    assert.equal(h.app.avatarManager, h.loaded[0]);
    assert.equal(h.loaded[0].vrmUrl, '/animations/iris.vrm?v=12');
    assert.deepEqual(h.errors, ['No pude cargar tu VRM. Mostrando a IRIS.']);
    assert.equal(h.app._appliedAvatarKey, '', 'the saved model is tried again on the next change');
});

test('the real Companion message handler invalidates avatar configuration', () => {
    let invalidated = false;
    method('_handleMessage').call({ _avatarSync: { request: force => { invalidated = force; } } }, 'avatar_config_changed', {});
    assert.equal(invalidated, true);
});

test('Vida propia comes from the native bootstrap while Core is away, and from Core once it answers', () => {
    const apply = (config) => {
        let applied;
        const app = { config, bridge: { preferences: { life: 'off' } }, speechKicker: {},
            _applyCompanionPreferences: value => { applied = value; } };
        const document = { documentElement: { style: { setProperty() {} } }, getElementById: () => ({}) };
        method('_applyIdentityAndTheme', { document, cleanAssistantName: value => value, readTheme: () => ({}) }).call(app);
        return applied.life;
    };
    assert.equal(apply({}), 'off');
    assert.equal(apply({ companion_life: 'active' }), 'active');
});

test('refresh uses saved settings instead of stale native bootstrap preferences', () => {
    let applied;
    const app = { config: { assistant_name: 'New', companion_scale: 1.2, companion_quiet_mode: true, companion_show_controls: false },
        bridge: { preferences: { scale: 0.8, quietMode: false, showControls: true } }, speechKicker: {},
        _applyCompanionPreferences: value => { applied = value; } };
    const document = { documentElement: { style: { setProperty() {} } }, getElementById: () => ({}) };
    method('_applyIdentityAndTheme', { document, cleanAssistantName: value => value, readTheme: () => ({}) }).call(app);
    assert.equal(applied.scale, 1.2);
    assert.equal(applied.quietMode, true);
    assert.equal(applied.showControls, false);
});
