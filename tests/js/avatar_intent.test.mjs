// Codex qa10 review, R5: "Volver a IRIS" waits for Core; an upload finished
// meanwhile must not be replaced by that late answer.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { createAvatarActions, createIntentGate } from '../../face/js/avatar_intent.js';

function deferred() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
}

const reply = (body, ok = true) => ({ ok, json: async () => body });

function controlledFetch() {
    const calls = [];
    const fetchImpl = (url, init = {}) => {
        const call = { url, init, ...deferred() };
        calls.push(call);
        return call.promise;
    };
    return { calls, fetchImpl };
}

class FakeForm {
    constructor() { this.parts = []; }
    append(...part) { this.parts.push(part); }
}

async function settle() {
    for (let i = 0; i < 20; i += 1) await Promise.resolve();
}

test('a late "Volver a IRIS" never replaces a model uploaded after it', async () => {
    const fetch = controlledFetch();
    const scene = [];
    const actions = createAvatarActions({ fetchImpl: fetch.fetchImpl, dispatch: (detail) => scene.push(detail),
        FormDataImpl: FakeForm });

    const returning = actions.useIris();
    await settle();
    const clearing = fetch.calls[0];                            // POST /api/update-identity, still pending
    assert.equal(clearing.url, '/api/update-identity');
    assert.deepEqual(JSON.parse(clearing.init.body), { avatar_vrm_path: '' }, "IRIS' own VRM is an empty path");

    const uploading = actions.upload({ name: 'nuevo.vrm' });
    await settle();
    fetch.calls[1].resolve(reply({ status: 'saved', avatar_url: '/avatars/nuevo.vrm' }));
    assert.deepEqual(await uploading, { url: '/avatars/nuevo.vrm', superseded: false });

    clearing.resolve(reply({ status: 'saved' }));
    assert.deepEqual(await returning, { ok: true, superseded: true });
    assert.deepEqual(scene, [{ vrmUrl: '/avatars/nuevo.vrm' }]);
});

test('without a newer action, "Volver a IRIS" shows IRIS once Core forgot the model', async () => {
    const fetch = controlledFetch();
    const scene = [];
    const actions = createAvatarActions({ fetchImpl: fetch.fetchImpl, dispatch: (detail) => scene.push(detail) });
    const returning = actions.useIris();
    await settle();
    fetch.calls[0].resolve(reply({ status: 'saved' }));
    assert.deepEqual(await returning, { ok: true, superseded: false });
    assert.deepEqual(scene, [{ vrmUrl: '' }]);
});

test('a refused "Volver a IRIS" changes nothing on stage', async () => {
    const fetch = controlledFetch();
    const scene = [];
    const actions = createAvatarActions({ fetchImpl: fetch.fetchImpl, dispatch: (detail) => scene.push(detail) });
    const returning = actions.useIris();
    await settle();
    fetch.calls[0].resolve(reply({ status: 'error' }, false));
    assert.deepEqual(await returning, { ok: false, superseded: false });
    assert.deepEqual(scene, []);
});

test('an upload transport error explains how to retry and preserves the current avatar', async () => {
    const scene = [];
    const actions = createAvatarActions({
        fetchImpl: async () => { throw new TypeError('Failed to fetch'); },
        dispatch: (detail) => scene.push(detail),
        FormDataImpl: FakeForm,
    });
    await assert.rejects(actions.upload({ name: 'avatar.vrm' }), /No pude conectar con IRIS.*vuelve a intentarlo/);
    assert.deepEqual(scene, []);
});

test('the current upload reports validation errors and an older upload yields silently', async () => {
    const fetch = controlledFetch();
    const scene = [];
    const actions = createAvatarActions({
        fetchImpl: fetch.fetchImpl, dispatch: (detail) => scene.push(detail), FormDataImpl: FakeForm,
    });
    const first = actions.upload({ name: 'viejo.vrm' });
    const second = actions.upload({ name: 'nuevo.vrm' });
    fetch.calls[0].resolve(reply({ detail: 'Modelo anterior inválido.' }, false));
    fetch.calls[1].resolve(reply({ detail: 'El VRM no incluye huesos humanoides.' }, false));
    assert.deepEqual(await first, { url: '', superseded: true });
    await assert.rejects(second, /no incluye huesos humanoides/);
    assert.deepEqual(scene, []);
});

test('the gate counts every action and Quick Configuration uses it', () => {
    const gate = createIntentGate();
    const first = gate.begin();
    assert.ok(gate.isCurrent(first));
    gate.invalidate();
    assert.ok(!gate.isCurrent(first));
    const panel = readFileSync(new URL('../../face/js/panel.js', import.meta.url), 'utf8');
    assert.match(panel, /await avatarActions\.useIris\(\)/);
    assert.match(panel, /await avatarActions\.upload\(file\)/);
    assert.doesNotMatch(panel, /fetch\('\/api\/avatar\/upload'/);
    // Always a VRM: no avatar kinds to pick (2026-10-28).
    assert.doesNotMatch(panel, /quick-avatar"|procedural|Particulas|Geometrico/);
});
