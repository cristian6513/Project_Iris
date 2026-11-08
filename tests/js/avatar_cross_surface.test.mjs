// Codex qa10 closure, C2: Quick Configuration and the page's drag and drop are
// two surfaces choosing the avatar. A late answer replaced a model dropped after
// it, because each surface kept its own order. They now share Face's reservation:
// these tests run the real panel wiring, Face's handlers, its drop handler and
// AvatarSwapCoordinator, with controlled requests and a fake renderer. Since
// 2026-10-28 the panel's own action is «Volver a IRIS» (always a VRM).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

import { createAvatarActions } from '../../face/js/avatar_intent.js';
import { AvatarSwapCoordinator } from '../../face/js/avatar_lifecycle.js';
import { avatarUrl, IRIS_AVATAR_URL } from '../../face/js/avatar_state.js';

const IRIS = IRIS_AVATAR_URL;
const appSource = readFileSync(new URL('../../face/js/app.js', import.meta.url), 'utf8');
const panelSource = readFileSync(new URL('../../face/js/panel.js', import.meta.url), 'utf8');

function appMethod(name) {
    const block = appSource.match(new RegExp(`^    (?:async )?${name}\\([^]*?^    \\}`, 'm'))?.[0];
    assert.ok(block, `app.js no longer has ${name}`);
    return block;
}

function panelWiring() {
    const start = panelSource.indexOf('const avatarActions = createAvatarActions({');
    const end = panelSource.indexOf('\n        });\n', start);
    assert.ok(start > 0 && end > start, 'panel.js no longer creates its avatar actions');
    return panelSource.slice(start, end + '\n        });'.length);
}

const flush = async () => { for (let i = 0; i < 8; i += 1) await new Promise((done) => setImmediate(done)); };
const reply = (body, ok = true) => ({ ok, json: async () => body });

function surfaces(visible = '/avatars/mio.vrm') {
    const calls = [];
    const fetch = (url, init = {}) => {
        let resolve, reject;
        const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
        calls.push({ url, init, resolve, reject });
        return promise;
    };
    const listeners = {};
    const window = {
        addEventListener(type, callback) { (listeners[type] ||= []).push(callback); },
        dispatchEvent(event) { for (const callback of listeners[event.type] || []) callback(event); return true; },
    };
    const bodyHandlers = {};
    const shown = [];
    const commits = [];
    const context = vm.createContext({
        window, fetch, createAvatarActions, avatarUrl, IRIS_AVATAR_URL,
        console: { log() {}, warn() {}, error() {} },
        FormData: class { append() {} },
        URL: { createObjectURL: () => 'blob:vista', revokeObjectURL() {} },
        CustomEvent: class { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
        document: { body: { classList: { add() {}, remove() {} }, addEventListener(type, callback) { bodyHandlers[type] = callback; } } },
    });
    const face = {
        config: { avatar_vrm_path: visible === IRIS ? '' : visible },
        avatarManager: { vrmUrl: visible },
        subtitles: { show: (text) => shown.push(text) },
        panel: { addMessage() {} },
    };
    face._avatarSwap = new AvatarSwapCoordinator({
        create: (resource) => ({ resource, avatar: { group: {}, vrm: {} }, init: async () => {} }),
        commit: (candidate) => {
            commits.push(candidate.resource);
            face.avatarManager = { vrmUrl: candidate.resource };
            face.config.avatar_vrm_path = candidate.resource === IRIS ? '' : candidate.resource;
            return null;
        },
        release() {},
    });
    for (const name of ['_reserveAvatarSelection', '_handleIdentityChange', '_replaceAvatar', '_setupDragAndDrop']) {
        face[name] = vm.runInContext(`({${appMethod(name)}}).${name}`, context);
    }
    // The same two listeners Face registers at start-up.
    assert.match(appSource, /addEventListener\('IRIS:avatar-reserve', e => this\._reserveAvatarSelection\(e\)\)/);
    assert.match(appSource, /addEventListener\('IRIS:identity-change', e => this\._handleIdentityChange\(e\)\)/);
    window.addEventListener('IRIS:avatar-reserve', (e) => face._reserveAvatarSelection(e));
    window.addEventListener('IRIS:identity-change', (e) => face._handleIdentityChange(e));
    face._setupDragAndDrop();
    vm.runInContext(`${panelWiring()}\nthis.avatarActions = avatarActions;`, context);
    const drop = (name) => bodyHandlers.drop({ preventDefault() {}, target: null, dataTransfer: { files: [{ name }] } });
    return { calls, face, commits, shown, drop, actions: context.avatarActions };
}

test('a model dropped on the page wins over an earlier «Volver a IRIS» still waiting', async () => {
    const s = surfaces();
    const returning = s.actions.useIris();
    await flush();
    const clearing = s.calls[0];                            // POST /api/update-identity, delayed
    assert.equal(clearing.url, '/api/update-identity');

    const dropping = s.drop('nuevo.vrm');
    await flush();
    s.calls[1].resolve(reply({ status: 'saved', avatar_url: '/avatars/nuevo.vrm' }));
    await dropping;
    assert.deepEqual(s.commits, ['/avatars/nuevo.vrm']);

    clearing.resolve(reply({ status: 'saved' }));
    assert.deepEqual(await returning, { ok: true, superseded: true });
    await flush();
    assert.deepEqual(s.commits, ['/avatars/nuevo.vrm']);
    assert.equal(s.face.config.avatar_vrm_path, '/avatars/nuevo.vrm');
    assert.equal(s.face._avatarSwap.pending, false);
});

test("a «Volver a IRIS» started after a drop wins over the drop's late upload", async () => {
    const s = surfaces();
    const dropping = s.drop('soltado.vrm');
    await flush();
    const upload = s.calls[0];
    assert.equal(upload.url, '/api/avatar/upload');

    const returning = s.actions.useIris();
    await flush();
    s.calls[1].resolve(reply({ status: 'saved' }));
    assert.deepEqual(await returning, { ok: true, superseded: false });
    await flush();
    assert.deepEqual(s.commits, [IRIS]);

    upload.resolve(reply({ status: 'saved', avatar_url: '/avatars/soltado.vrm' }));
    await dropping;
    await flush();
    assert.deepEqual(s.commits, [IRIS]);
    assert.equal(s.face.config.avatar_vrm_path, '');
    assert.equal(s.face._avatarSwap.pending, false);
});

test('a panel upload still saving yields to a later «Volver a IRIS»', async () => {
    const s = surfaces();
    const uploading = s.actions.upload(new File(['vrm'], 'pendiente.vrm'));
    await flush();
    const upload = s.calls[0];

    const returning = s.actions.useIris();
    await flush();
    s.calls[1].resolve(reply({ status: 'saved' }));
    assert.deepEqual(await returning, { ok: true, superseded: false });
    await flush();
    assert.deepEqual(s.commits, [IRIS]);

    upload.resolve(reply({ status: 'saved', avatar_url: '/avatars/pendiente.vrm' }));
    assert.deepEqual(await uploading, { url: '/avatars/pendiente.vrm', superseded: true });
    await flush();
    assert.deepEqual(s.commits, [IRIS]);
    assert.equal(s.face._avatarSwap.pending, false);
});

test('late failures never replace the current selection nor report over it', async () => {
    const s = surfaces();
    const returning = s.actions.useIris();
    await flush();
    const clearing = s.calls[0];
    const dropping = s.drop('vigente.vrm');
    await flush();
    s.calls[1].resolve(reply({ status: 'saved', avatar_url: '/avatars/vigente.vrm' }));
    await dropping;
    clearing.reject(new Error('sin conexión'));
    assert.deepEqual(await returning, { ok: false, superseded: true });

    const lateDrop = s.drop('fallido.vrm');
    await flush();
    const failingUpload = s.calls[2];
    const reuploading = s.actions.upload(new File(['vrm'], 'vigente.vrm'));
    await flush();
    s.calls[3].resolve(reply({ status: 'saved', avatar_url: '/avatars/vigente.vrm' }));
    await reuploading;
    failingUpload.resolve(reply({ detail: 'Archivo dañado' }, false));
    await lateDrop;
    await flush();
    assert.equal(s.face.config.avatar_vrm_path, '/avatars/vigente.vrm');
    assert.deepEqual(s.commits, ['/avatars/vigente.vrm'], 'the visible model is not reloaded');
    assert.ok(!s.shown.includes('Archivo dañado'), 'a superseded drop does not report its failure');
    assert.equal(s.face._avatarSwap.pending, false);
});

test('«Volver a IRIS» while IRIS is on screen ends a pending drop without reloading her', async () => {
    const s = surfaces(IRIS);
    const dropping = s.drop('soltado.vrm');
    await flush();
    const upload = s.calls[0];
    const returning = s.actions.useIris();                  // what is already visible
    await flush();
    s.calls[1].resolve(reply({ status: 'saved' }));
    assert.deepEqual(await returning, { ok: true, superseded: false });
    upload.resolve(reply({ status: 'saved', avatar_url: '/avatars/soltado.vrm' }));
    await dropping;
    await flush();
    assert.deepEqual(s.commits, []);
    assert.equal(s.face.config.avatar_vrm_path, '');
    assert.equal(s.face._avatarSwap.pending, false);
});

test('without a newer action «Volver a IRIS» shows IRIS once', async () => {
    const s = surfaces();
    const returning = s.actions.useIris();
    await flush();
    s.calls[0].resolve(reply({ status: 'saved' }));
    assert.deepEqual(await returning, { ok: true, superseded: false });
    await flush();
    assert.deepEqual(s.commits, [IRIS]);
    assert.equal(s.face._avatarSwap.pending, false);
});
