import test from 'node:test';
import assert from 'node:assert/strict';

import { normalizeSelectorState, optionGroups, selectionValue } from '../../face/js/model_selector_state.js?v=3';
import {
    allValues,
    catalogView,
    draftFromState,
    draftPayload,
    sameDraft,
    toggleModel,
    toggleProvider,
} from '../../face/js/model_catalog_state.js';

// qa9: one OpenAI login already filled the chat selector with every model it
// publishes. "Modelos del selector" chooses which ones the selector lists.

function state(visible = null, conversation = {}) {
    return normalizeSelectorState({
        supported: true,
        conversation: { session_id: 's', context: 'c', mode: 'default', state: 'pending', ...conversation },
        default: { provider: 'openai-codex', model: 'gpt-6.1-sol' },
        inventory: {
            error: '',
            providers: [
                { id: 'opencode', label: 'OpenCode Zen', authenticated: false, models: ['kimi-k3'] },
                { id: 'openai-codex', label: 'OpenAI Codex', authenticated: true,
                  models: ['gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-5.4', 'gpt-5.4-mini'] },
            ],
        },
        visible,
    });
}

const value = (provider, model) => selectionValue(provider, model);

test('without a saved choice every model is listed, providers with a session first', () => {
    const current = state();
    assert.equal(current.visible, null);
    const view = catalogView(current, draftFromState(current));
    assert.equal(view.showsAll, true);
    assert.deepEqual(view.groups.map((group) => group.id), ['openai-codex', 'opencode']);
    assert.equal(view.listed, 5);
    assert.equal(view.total, 5);
    assert.ok(view.groups[0].models.every((model) => model.checked));
    assert.match(view.groups[1].warning, /Sin sesión/);
});

test('unlisting one model from "all" keeps the others listed', () => {
    const current = state();
    const draft = toggleModel(null, current, value('openai-codex', 'gpt-5.4-mini'), false);
    assert.equal(draft.size, allValues(current).length - 1);
    assert.equal(catalogView(current, draft).listed, 4);
    assert.deepEqual(draftPayload(draft).find((item) => item.model === 'gpt-5.4-mini'), undefined);
});

test('a provider can be listed or hidden at once', () => {
    const current = state();
    const hidden = toggleProvider(null, current, 'opencode', false);
    assert.equal(catalogView(current, hidden).groups.find((group) => group.id === 'opencode').listed, 0);
    const back = toggleProvider(hidden, current, 'opencode', true);
    assert.ok(back.has(value('opencode', 'kimi-k3')));
    assert.equal(toggleProvider(null, current, 'missing', false), null);
});

test('the payload is null for "all" and provider/model pairs otherwise', () => {
    assert.equal(draftPayload(null), null);
    assert.deepEqual(draftPayload(new Set([value('openai-codex', 'gpt-5.4')])), [{ provider: 'openai-codex', model: 'gpt-5.4' }]);
});

test('saved and edited drafts compare by content', () => {
    assert.ok(sameDraft(null, null));
    assert.ok(!sameDraft(null, new Set()));
    assert.ok(sameDraft(new Set(['a', 'b']), new Set(['b', 'a'])));
    assert.ok(!sameDraft(new Set(['a']), new Set(['b'])));
});

test('the selector lists only the chosen models, plus the conversation model and the default', () => {
    const chosen = [{ provider: 'openai-codex', model: 'gpt-5.4' }];
    const current = state(chosen, { mode: 'custom', provider: 'openai-codex', model: 'gpt-6.1-terra' });
    const view = optionGroups(current);
    const listed = view.groups.flatMap((group) => group.models.map((model) => model.model));
    // gpt-5.4 chosen; gpt-6.1-terra in use; gpt-6.1-sol is the default.
    assert.deepEqual(listed.sort(), ['gpt-5.4', 'gpt-6.1-sol', 'gpt-6.1-terra']);
    assert.equal(view.hidden, 2); // gpt-5.4-mini and the unauthenticated kimi-k3
    assert.ok(!view.groups.some((group) => group.id === 'opencode'));
});

test('the catalog marks the model in use and the default', () => {
    const current = state(null, { mode: 'custom', provider: 'openai-codex', model: 'gpt-5.4' });
    const models = catalogView(current, null).groups[0].models;
    assert.ok(models.find((model) => model.model === 'gpt-5.4').current);
    assert.ok(models.find((model) => model.model === 'gpt-6.1-sol').isDefault);
});

test('a search keeps matching providers and models only', () => {
    const view = catalogView(state(), null, 'mini');
    assert.deepEqual(view.groups.map((group) => group.models.map((model) => model.model)), [['gpt-5.4-mini']]);
    assert.equal(catalogView(state(), null, 'zen').groups[0].id, 'opencode');
});
