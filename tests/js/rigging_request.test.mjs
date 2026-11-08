import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';

const source = fs.readFileSync(new URL('../../wizard/ui/wizard.js', import.meta.url), 'utf8');
const rigSource = source.slice(source.indexOf('async function rigModel('), source.indexOf('// ── Step 5:', source.indexOf('async function rigModel(')));

async function exercise(response) {
    const result = { style: {}, innerHTML: '' };
    const state = { uploadedModel: 'muñeca & amiga #1.glb', avatar_vrm_path: 'previous.vrm' };
    let sent;
    const context = vm.createContext({
        document: { getElementById: () => result },
        wizardState: state,
        escapeHtml: value => String(value).replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
        fetch: async (url, options) => { sent = { url, options }; return response; },
    });
    vm.runInContext(rigSource, context);
    await context.rigModel();
    return { result, state, sent };
}

test('rigging preserves Unicode and reserved query characters in basenames', async () => {
    const { state, sent } = await exercise({
        ok: true, json: async () => ({ status: 'success', method: 'auto', output_path: 'generated/model.glb' }),
    });
    const url = new URL(sent.url, 'http://localhost');
    assert.equal(url.searchParams.get('filename'), 'muñeca & amiga #1.glb');
    assert.equal(url.searchParams.get('method'), 'auto');
    assert.equal(url.hash, '');
    assert.equal(sent.options.method, 'POST');
    assert.equal(state.avatar_vrm_path, 'generated/model.glb');
});

test('rejected rigging displays the reason without changing the selected avatar', async () => {
    const { state, result } = await exercise({
        ok: false, status: 415, json: async () => ({ message: 'Exporta <GLB> con recursos incrustados.' }),
    });
    assert.equal(state.avatar_vrm_path, 'previous.vrm');
    assert.match(result.innerHTML, /Exporta &lt;GLB&gt; con recursos incrustados/);
});

test('FastAPI enum validation produces a useful message instead of undefined', async () => {
    const { state, result } = await exercise({
        ok: false, status: 422, json: async () => ({ detail: [{ type: 'literal_error' }] }),
    });
    assert.equal(state.avatar_vrm_path, 'previous.vrm');
    assert.match(result.innerHTML, /HTTP 422/);
    assert.doesNotMatch(result.innerHTML, /undefined|\[object Object\]/);
});
