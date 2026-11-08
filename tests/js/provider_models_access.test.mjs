import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
const read = path => readFileSync(new URL(`../../wizard/ui/${path}`, import.meta.url), 'utf8');
const source = read('provider_catalog_ui.js');
const wizard = read('wizard.js');
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
function page() {
    const nodes = Object.fromEntries(['llm-api-key', 'llm-base-url', 'llm-model', 'llm-model-access', 'llm-model-section',
        'custom-model-section', 'btn-load-provider-models', 'llm-model-access-status', 'use-oauth', 'llm-model-filter']
        .map(id => [id, { style: {}, dataset: {}, value: '', hidden: false, checked: false }]));
    Object.defineProperty(nodes['llm-model'], 'options', { get() {
        return [...(this.innerHTML || '').matchAll(/<option value="([^"]*)"/g)].map(m => ({ value: m[1] }));
    } });
    let provider = 'opencode-zen';
    const reads = [];
    const ctx = vm.createContext({ AbortController, document: { getElementById: id => nodes[id] || null },
        selectedLLMProvider: () => provider, LLM_PROVIDERS: { 'opencode-zen': { auth: 'api' }, openai: { auth: 'api+oauth' },
            nous: { auth: 'oauth' }, local: { auth: 'none' } }, llmSetup: {}, llmView: 'change',
        IrisLlmSetup: { view: (_setup, view) => view }, markModelStepDone() {},
        LLM_MODELS: { 'opencode-zen': [{ value: 'obsolete', label: 'Old' }] }, LLM_OAUTH_MODELS: {}, LLM_OAUTH_CATALOG: {},
        MODEL_META: {}, escapeHtml: value => String(value), fetch: (url, options) => {
            const task = deferred(); reads.push({ url, options, task }); return task.promise;
        },
    });
    vm.runInContext(source, ctx);
    vm.runInContext(wizard.slice(wizard.indexOf('const MODEL_FILTER_MIN'), wizard.indexOf('function filterModelOptions()')), ctx);
    return { ctx, nodes, reads, selectProvider(value) { provider = value; ctx._renderProviderModelsAccess(); } };
}
function complete(read, models = [{ value: 'free', label: 'Free' }], extra = {}) {
    const body = JSON.parse(read.options.body);
    read.task.resolve({ ok: true, json: async () => ({ ...body, api_key: undefined, status: 'ok', source: 'public', models, ...extra }) });
}

test('cloud model choice is hidden before access; local and add-provider flows stay independent', () => {
    const p = page();
    p.ctx._renderProviderModelsAccess();
    assert.equal(p.nodes['llm-model-section'].style.display, 'none');
    assert.equal(p.nodes['llm-model'].disabled, true);
    assert.equal(p.reads.length, 0, 'selecting a provider never sends a draft key');
    p.selectProvider('local');
    assert.equal(p.nodes['llm-model-access'].hidden, true);
    p.selectProvider('openai'); p.ctx.llmView = 'add'; p.ctx._renderProviderModelsAccess();
    assert.equal(p.nodes['llm-model-access'].hidden, true);
});

test('explicit read lists every model including free, without picking the first paid model or saving', async () => {
    const p = page();
    p.nodes['llm-api-key'].value = 'synthetic';
    const load = p.ctx.loadProviderModels();
    const models = Array.from({ length: 130 }, (_, i) => ({ value: `m-${i}`, label: `Model ${i}` }));
    complete(p.reads[0], models); await load;
    assert.equal(p.nodes['llm-model-section'].style.display, 'block');
    assert.equal(p.nodes['llm-model'].options.length, 132, '130 models, placeholder and manual entry');
    assert.equal(p.nodes['llm-model'].value, '');
    assert.equal(p.nodes['llm-model-filter'].hidden, false);
    assert.match(p.nodes['llm-model-access-status'].textContent, /no verifica la clave/);
    assert.deepEqual(p.reads.map(r => r.url), ['/api/provider-models']);
});

test('editing a key aborts its read and a late response cannot restore the list', async () => {
    const p = page(); p.nodes['llm-api-key'].value = 'old';
    const load = p.ctx.loadProviderModels();
    p.nodes['llm-api-key'].value = 'new'; p.ctx.invalidateProviderModels();
    assert.equal(p.reads[0].options.signal.aborted, true);
    complete(p.reads[0]); await load;
    assert.equal(p.nodes['llm-model-section'].style.display, 'none');
    assert.equal(p.ctx._providerModelsFor('opencode-zen', false), null);
});

test('provider, OAuth method and endpoint changes all invalidate the catalog', async () => {
    for (const change of [p => p.selectProvider('openai'), p => { p.nodes['use-oauth'].checked = true; },
        p => { p.nodes['llm-base-url'].value = 'https://relay.example/v1'; }]) {
        const p = page(); const load = p.ctx.loadProviderModels();
        change(p); p.ctx._renderProviderModelsAccess(); complete(p.reads[0]); await load;
        assert.equal(p.reads[0].options.signal.aborted, true);
        assert.equal(p.nodes['llm-model-section'].style.display, 'none');
    }
});

test('an older response cannot replace a newer account list, even if cancellation is ignored', async () => {
    const p = page(); const first = p.ctx.loadProviderModels(); const second = p.ctx.loadProviderModels();
    complete(p.reads[1], [{ value: 'new-account', label: 'New' }]); await second;
    complete(p.reads[0], [{ value: 'old-account', label: 'Old' }]); await first;
    assert.match(p.nodes['llm-model'].innerHTML, /new-account/);
    assert.doesNotMatch(p.nodes['llm-model'].innerHTML, /old-account/);
});

test('a failed credential check keeps choice hidden and permits an explicit retry', async () => {
    const p = page(); const first = p.ctx.loadProviderModels();
    complete(p.reads[0], [], { status: 'error', message: 'Credencial rechazada' }); await first;
    assert.equal(p.nodes['llm-model-section'].style.display, 'none');
    assert.match(p.nodes['llm-model-access-status'].textContent, /Credencial rechazada/);
    assert.equal(p.nodes['btn-load-provider-models'].disabled, false);
    const second = p.ctx.loadProviderModels(); complete(p.reads[1]); await second;
    assert.equal(p.nodes['llm-model-section'].style.display, 'block');
});
