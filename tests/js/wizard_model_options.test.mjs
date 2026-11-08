// QA12 Q03/Q04: the model select lists every model, filters a long list without losing the
// chosen one, and shows models the account's plan cannot use as disabled, saying why.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../../wizard/ui/wizard.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../../wizard/ui/index.html', import.meta.url), 'utf8');
const between = (a, b) => source.slice(source.indexOf(a), source.indexOf(b, source.indexOf(a) + a.length));

function page(models, meta = {}, provider = 'opencode') {
    const select = {
        innerHTML: '', value: '',
        get options() { return [...this.innerHTML.matchAll(/<option value="([^"]+)"/g)].map(m => ({ value: m[1] })); },
    };
    const filter = { hidden: true, value: '', placeholder: '', dataset: {} };
    const nodes = { 'llm-model': select, 'llm-model-filter': filter, 'llm-custom-model': { value: '' }, 'custom-model-section': { style: {} } };
    const context = vm.createContext({
        document: { getElementById: id => nodes[id] || null },
        LLM_MODELS: { [provider]: models }, LLM_OAUTH_MODELS: {}, LLM_OAUTH_CATALOG: {},
        MODEL_META: Object.fromEntries(Object.entries(meta).map(([id, value]) => [`${provider}::${id}`, value])),
        wizardState: {}, selectedLLMProvider: () => provider,
        escapeHtml: value => String(value),
    });
    vm.runInContext(between('const MODEL_FILTER_MIN', 'function filterModelOptions()'), context);
    vm.runInContext(between('function _restoreSavedLLMModelIntoDOM()', 'async function _hydrateSavedConfigIntoDOM()'), context);
    // The real <select> keeps a value only when an option has it.
    const render = (preserve = true) => {
        context._setProviderModelOptions(provider, false, preserve);
        const values = [...select.innerHTML.matchAll(/<option value="([^"]*)"/g)].map(m => m[1]);
        if (!values.includes(select.value)) select.value = values[0] || '';
        return values;
    };
    return { select, filter, render, restore(saved) { context.wizardState.llm_model = saved; context._restoreSavedLLMModelIntoDOM(); } };
}

const many = Array.from({ length: 30 }, (_, i) => ({ value: `model-${i}`, label: `Model ${i}` }));

test('a short list has no filter; a long one offers it with its size', () => {
    assert.equal(page(many.slice(0, 12)).render().length, 13);
    const long = page(many);
    assert.equal(long.render().length, 31, 'all thirty, plus «Otro modelo»');
    assert.equal(long.filter.hidden, false);
    assert.equal(long.filter.placeholder, 'Buscar entre 30 modelos…');
});

test('filtering narrows the list but never drops the chosen model', () => {
    const { select, filter, render } = page(many);
    render();
    select.value = 'model-7';
    filter.value = 'model 2';
    assert.deepEqual(render(), ['model-2', 'model-7', 'model-20', 'model-21', 'model-22', 'model-23', 'model-24',
        'model-25', 'model-26', 'model-27', 'model-28', 'model-29', '__custom__']);
    assert.equal(select.value, 'model-7');
});

test('a model outside the plan is listed disabled with the reason', () => {
    const { select, render } = page([{ value: 'free', label: 'Free' }, { value: 'paid', label: 'Paid' }, { value: 'unknown', label: 'Unknown' }],
        { paid: { locked: true, lockReason: 'plan' }, unknown: { locked: true, lockReason: 'pending' } });
    render();
    assert.match(select.innerHTML, /<option value="free">Free<\/option>/);
    assert.match(select.innerHTML, /<option value="paid" disabled>Paid · no incluido en tu plan<\/option>/);
    assert.match(select.innerHTML, /<option value="unknown" disabled>Unknown · comprobando tu plan<\/option>/);
});

test('Nous cannot pick a paid first entry and lists usable models before locked ones', () => {
    const { select, render } = page([{ value: 'paid', label: 'Paid' }, { value: 'free', label: 'Free' }],
        { paid: { locked: true, lockReason: 'plan' } }, 'nous');
    select.value = 'paid';
    assert.deepEqual(render(), ['', 'free', 'paid']);
    assert.equal(select.value, '');
    assert.equal(select.disabled, false);
    assert.ok(!select.innerHTML.includes('__custom__'));
});

test('Nous model choice stays locked before login or while the plan is unknown', () => {
    for (const lockReason of ['auth', 'pending']) {
        const { select, render } = page([{ value: 'one', label: 'One' }], { one: { locked: true, lockReason } }, 'nous');
        render(); assert.equal(select.disabled, true); assert.equal(select.value, '');
    }
    const old = page([{ value: 'one', label: 'One' }], { one: { available: false } }, 'nous');
    old.render(); assert.equal(old.select.disabled, true);
});

test('restoring Nous settings cannot select a saved model that the account can no longer use', () => {
    for (const metadata of [{ locked: true, lockReason: 'plan' }, { available: false }]) {
        const p = page([{ value: 'previous', label: 'Anterior' }, { value: 'free', label: 'Gratis' }], { previous: metadata }, 'nous');
        p.render(); p.restore('previous'); assert.equal(p.select.value, '');
    }
});

test('the filter field sits above the select and its metadata carries the lock', () => {
    assert.match(html, /<input type="search" id="llm-model-filter" class="model-filter" hidden[^>]*oninput="filterModelOptions\(\)">\s*<select id="llm-model"/);
    assert.match(source, /locked:\s+!!m\.locked,\s+lockReason: m\.lock_reason \|\| '',/);
});
