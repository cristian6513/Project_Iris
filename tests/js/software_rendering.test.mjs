// A PC drawing without a GPU (the user's VirtualBox VM, 2026-11-17) is offered «Ahorro» at 30 FPS
// once, and nothing changes unless the user accepts.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import {
    SOFTWARE_RENDERING_CHOICE,
    SOFTWARE_RENDERING_KEY,
    isSoftwareRenderer,
    rendererName,
    savingSuggestion,
    suggestSavingOnSoftwareRendering,
} from '../../face/js/software_rendering.js?v=2';

const SWIFTSHADER = 'ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)';
const WARP = 'ANGLE (Microsoft, Microsoft Basic Render Driver (0x0000008C) Direct3D11 vs_5_0 ps_5_0, D3D11)';
const RADEON = 'ANGLE (AMD, AMD Radeon RX 9060 XT (0x00007590) Direct3D11 vs_5_0 ps_5_0, D3D11)';
const UHD = 'ANGLE (Intel, Intel(R) UHD Graphics 630 (0x00003E92) Direct3D11 vs_5_0 ps_5_0, D3D11)';

function fakeGl(name, { unmasked = true } = {}) {
    const info = { UNMASKED_RENDERER_WEBGL: 0x9246 };
    return {
        RENDERER: 0x1f01,
        getExtension: (ext) => (unmasked && ext === 'WEBGL_debug_renderer_info' ? info : null),
        getParameter: (key) => (key === 0x9246 ? name : 'WebKit WebGL'),
    };
}

function memoryStorage(initial = {}) {
    const data = new Map(Object.entries(initial));
    return { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, String(value)), data };
}

/** Core's render settings: GET answers `saved` (or fails), PUT answers `putOk`. */
function fakeCore({ saved = { render_quality: 'balanced', render_fps_limit: 60 }, readFails = false, putOk = true } = {}) {
    const calls = [];
    const fetchImpl = async (url, options = {}) => {
        calls.push({ url, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null });
        if ((options.method || 'GET') === 'GET') {
            if (readFails) throw new Error('Core is not answering yet');
            return { ok: true, json: async () => saved };
        }
        return { ok: putOk, json: async () => SOFTWARE_RENDERING_CHOICE };
    };
    return { fetchImpl, calls, puts: () => calls.filter((call) => call.method === 'PUT') };
}

test('software renderers are recognised and real GPUs are not', () => {
    for (const name of [SWIFTSHADER, WARP, 'llvmpipe (LLVM 15.0.7, 256 bits)', 'Google SwiftShader']) {
        assert.equal(isSoftwareRenderer(name), true, name);
    }
    for (const name of [RADEON, UHD, 'WebKit WebGL', '', undefined]) assert.equal(isSoftwareRenderer(name), false, String(name));
});

test('the unmasked GPU name is read when the browser offers it', () => {
    assert.equal(rendererName(fakeGl(SWIFTSHADER)), SWIFTSHADER);
    assert.equal(rendererName(fakeGl(SWIFTSHADER, { unmasked: false })), 'WebKit WebGL');
    assert.equal(rendererName({ getExtension() { throw new Error('lost context'); } }), '');
});

test('the offer names what the user has now and both ways out', () => {
    const dialog = savingSuggestion({ render_quality: 'high', render_fps_limit: 0 });
    assert.match(dialog.message, /Alta calidad sin límite de FPS/);
    assert.match(dialog.message, /Ajustes → Rendimiento/);
    assert.equal(dialog.confirmLabel, 'Probar Ahorro a 30 FPS');
    assert.equal(dialog.cancelLabel, 'Mantener mi configuración');
    assert.match(savingSuggestion({ render_quality: 'balanced', render_fps_limit: 60 }).message, /Equilibrado a 60 FPS/);
});

test('accepting applies «Ahorro» at 30 FPS and saves it for Companion and the next start', async () => {
    const core = fakeCore(), storage = memoryStorage(), applied = [], asked = [];
    const outcome = await suggestSavingOnSoftwareRendering({
        gl: fakeGl(WARP), storage, fetchImpl: core.fetchImpl,
        ask: async (dialog) => { asked.push(dialog); return true; },
        apply: (choice) => applied.push(choice),
    });
    assert.equal(outcome, 'saved');
    assert.equal(asked.length, 1);
    assert.deepEqual(applied, [{ ...SOFTWARE_RENDERING_CHOICE }]);
    assert.deepEqual(core.puts().map((call) => [call.url, call.body]),
        [['/api/render-settings', { render_quality: 'saving', render_fps_limit: 30 }]]);
    assert.equal(storage.data.get(SOFTWARE_RENDERING_KEY), '1');
});

test('keeping the settings changes and saves nothing, and the offer does not come back', async () => {
    const core = fakeCore({ saved: { render_quality: 'high', render_fps_limit: 0 } }), storage = memoryStorage();
    let applied = false;
    const outcome = await suggestSavingOnSoftwareRendering({
        gl: fakeGl(SWIFTSHADER), storage, fetchImpl: core.fetchImpl, ask: async () => false, apply: () => { applied = true; },
    });
    assert.equal(outcome, 'kept');
    assert.equal(applied, false);
    assert.deepEqual(core.puts(), []);
    assert.equal(storage.data.get(SOFTWARE_RENDERING_KEY), '1');
});

test('unreadable saved settings mean no offer and no change (Codex, 2026-11-17)', async () => {
    // A failed read used to look like «Equilibrado, 60 FPS» and overwrite «Alta, sin límite».
    for (const core of [fakeCore({ readFails: true }), fakeCore({ saved: { render_quality: 'ultra' } }), fakeCore({ saved: null })]) {
        let asked = false, applied = false;
        const outcome = await suggestSavingOnSoftwareRendering({
            gl: fakeGl(SWIFTSHADER), storage: memoryStorage(), fetchImpl: core.fetchImpl,
            ask: async () => { asked = true; return true; }, apply: () => { applied = true; },
        });
        assert.equal(outcome, null);
        assert.equal(asked, false);
        assert.equal(applied, false);
        assert.deepEqual(core.puts(), []);
    }
});

test('a real GPU, an earlier answer or settings already in «Ahorro» at 30 FPS ask nothing', async () => {
    const cases = [
        { gl: fakeGl(RADEON), storage: memoryStorage(), core: fakeCore() },
        { gl: fakeGl(SWIFTSHADER), storage: memoryStorage({ [SOFTWARE_RENDERING_KEY]: '1' }), core: fakeCore() },
        { gl: fakeGl(SWIFTSHADER), storage: memoryStorage(), core: fakeCore({ saved: { ...SOFTWARE_RENDERING_CHOICE } }) },
    ];
    for (const { gl, storage, core } of cases) {
        let asked = false;
        const outcome = await suggestSavingOnSoftwareRendering({
            gl, storage, fetchImpl: core.fetchImpl, ask: async () => { asked = true; return true; }, apply() {},
        });
        assert.equal(outcome, null);
        assert.equal(asked, false);
        assert.deepEqual(core.puts(), []);
    }
});

test('an accepted change that Core cannot save stays for this session and is offered again', async () => {
    const core = fakeCore({ putOk: false }), storage = memoryStorage(), applied = [];
    const outcome = await suggestSavingOnSoftwareRendering({
        gl: fakeGl(SWIFTSHADER), storage, fetchImpl: core.fetchImpl, ask: async () => true, apply: (choice) => applied.push(choice),
    });
    assert.equal(outcome, 'unsaved');
    assert.equal(applied.length, 1);
    assert.equal(storage.data.has(SOFTWARE_RENDERING_KEY), false);
});

test('storage that throws (private window, blocked site data) does not break the start', async () => {
    const storage = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
    const outcome = await suggestSavingOnSoftwareRendering({
        gl: fakeGl(SWIFTSHADER), storage, fetchImpl: fakeCore().fetchImpl, ask: async () => false, apply() {},
    });
    assert.equal(outcome, 'kept');
});

test('Face offers it once the room shows, with its own dialog, never during setup', async () => {
    const app = await readFile(new URL('../../face/js/app.js', import.meta.url), 'utf8');
    assert.match(app, /this\._firstFrameShown = new Promise/);
    const firstFrame = app.slice(app.indexOf('if (this._awaitingFirstFrame) {'));
    assert.ok(firstFrame.indexOf('this._resolveFirstFrame?.()') > 0, 'the first frame settles the promise');
    const init = app.slice(app.indexOf('async init()'), app.indexOf('_setupRenderer() {'));
    const offer = init.indexOf('this._firstFrameShown.then(() => suggestSavingOnSoftwareRendering(');
    assert.ok(offer > init.indexOf('this.panel = new SidePanel();'), 'offered after the panel exists');
    assert.match(init.slice(offer, offer + 400), /ask: \(dialog\) => irisConfirm\(/);
    assert.doesNotMatch(app, /adoptSavingOnSoftwareRendering/);
});
