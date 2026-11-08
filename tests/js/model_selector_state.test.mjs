import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    defaultAction,
    normalizeSelectorState,
    optionGroups,
    parseSelectionValue,
    pillView,
    requestErrorText,
    selectionValue,
    statusView,
    turnNotice,
} from '../../face/js/model_selector_state.js';

function payload(overrides = {}) {
    return {
        supported: true,
        reason: '',
        busy: false,
        default_writable: true,
        default_reason: '',
        conversation: {
            session_id: 'IRIS-iris', profile: 'default', context: 'default:0', mode: 'default',
            provider: 'fake-a', model: 'alpha-1', state: 'pending', last: null,
            ...(overrides.conversation || {}),
        },
        default: { provider: 'fake-a', model: 'alpha-1', ...(overrides.default || {}) },
        inventory: {
            error: '',
            providers: [
                { id: 'fake-a', label: 'Fake A', current: true, authenticated: true, models: ['alpha-1', 'alpha-2'], warning: '' },
                { id: 'fake-b', label: 'Fake B', current: false, authenticated: true, models: ['beta-1'], warning: '' },
                { id: 'codex', label: 'OpenAI Codex', current: false, authenticated: false, models: ['gpt-5'],
                  warning: 'Configured provider is not authenticated' },
            ],
            ...(overrides.inventory || {}),
        },
        ...Object.fromEntries(Object.entries(overrides).filter(([key]) => !['conversation', 'default', 'inventory'].includes(key))),
    };
}

const state = (overrides) => normalizeSelectorState(payload(overrides));

test('selection values round-trip provider and model', () => {
    assert.deepEqual(parseSelectionValue(selectionValue('fake-b', 'org/beta:1')), { provider: 'fake-b', model: 'org/beta:1' });
});

test('a default conversation shows "Predeterminado" with the default model, pending until a turn', () => {
    const pill = pillView(state());
    assert.equal(pill.label, 'Predeterminado');
    assert.equal(pill.detail, 'alpha-1');
    assert.equal(pill.tone, 'pending');
    assert.match(pill.title, /próximo mensaje/);
});

test('a conversation with its own model shows the model and its provider', () => {
    const pill = pillView(state({ conversation: { mode: 'custom', provider: 'fake-b', model: 'beta-1' } }));
    assert.equal(pill.label, 'beta-1');
    assert.equal(pill.detail, 'Fake B');
});

test('confirmed, failed, cancelled and unconfirmed states read from the last turn', () => {
    const confirmed = statusView(state({ conversation: { state: 'confirmed', last: {
        status: 'completed', confirmed: true, model: 'alpha-1', effective: { model: 'alpha-1' } } } }));
    assert.deepEqual(confirmed, { tone: 'confirmed', text: 'Última respuesta confirmada con alpha-1.' });

    const failed = statusView(state({ conversation: { state: 'failed', last: {
        status: 'failed', error: "Model 'alpha-9' isn't available" } } }));
    assert.equal(failed.tone, 'failed');
    assert.match(failed.text, /alpha-9/);
    assert.match(failed.text, /vuelve al predeterminado/);

    assert.equal(statusView(state({ conversation: { state: 'cancelled' } })).tone, 'unconfirmed');
    assert.equal(statusView(state({ conversation: { state: 'unconfirmed' } })).tone, 'unconfirmed');
});

test('while IRIS answers, a new choice says the running answer keeps its model', () => {
    assert.match(statusView(state({ busy: true })).text, /modelo anterior.*próximo mensaje/);
});

test('loading and request errors take precedence', () => {
    assert.equal(statusView(state(), { loading: true }).tone, 'loading');
    assert.deepEqual(statusView(state(), { requestError: 'Hermes devolvió 500.' }), { tone: 'failed', text: 'Hermes devolvió 500.' });
    assert.equal(pillView(null).tone, 'loading');
});

test('an older or offline Hermes disables the selector with the reason', () => {
    const old = state({ supported: false, reason: 'hermes_without_session_models' });
    assert.equal(pillView(old).tone, 'disabled');
    assert.match(pillView(old).title, /no permite elegir el modelo por conversación/);
    const offline = state({ supported: false, reason: 'hermes_offline' });
    assert.match(statusView(offline).text, /servicio de IA está detenido/);
    const groups = optionGroups(old);
    assert.equal(groups.defaultOption.disabled, true);
    assert.ok(groups.groups.flatMap((group) => group.models).every((model) => model.disabled));
});

test('options: default first, providers with models, the provider without credentials is visible but not selectable', () => {
    const view = optionGroups(state());
    assert.deepEqual(view.defaultOption, { value: 'default', label: 'Predeterminado', detail: 'alpha-1 · Fake A', checked: true, disabled: false });
    assert.deepEqual(view.groups.map((group) => group.id), ['fake-a', 'fake-b', 'codex']);
    const codex = view.groups.find((group) => group.id === 'codex');
    assert.equal(codex.selectable, false);
    assert.match(codex.warning, /not authenticated/);
    assert.ok(codex.models.every((model) => model.disabled));
    const alpha1 = view.groups[0].models[0];
    assert.equal(alpha1.isDefault, true);
    assert.equal(alpha1.checked, false);
    assert.equal(view.total, 4);
});

test('the conversation model is the checked option', () => {
    const view = optionGroups(state({ conversation: { mode: 'custom', provider: 'fake-b', model: 'beta-1' } }));
    assert.equal(view.defaultOption.checked, false);
    assert.equal(view.groups[1].models[0].checked, true);
});

test('the filter matches models and providers', () => {
    assert.deepEqual(optionGroups(state(), 'beta').groups.map((group) => group.id), ['fake-b']);
    const byProvider = optionGroups(state(), 'fake a');
    assert.deepEqual(byProvider.groups.map((group) => group.id), ['fake-a']);
    assert.equal(byProvider.shown, 2);
    assert.deepEqual(optionGroups(state(), 'zzz').groups, []);
});

test('"usar como predeterminado" only for a conversation model that differs from the default', () => {
    assert.equal(defaultAction(state()), null);
    assert.equal(defaultAction(state({ conversation: { mode: 'custom', provider: 'fake-a', model: 'alpha-1' } })), null);
    const action = defaultAction(state({ conversation: { mode: 'custom', provider: 'fake-b', model: 'beta-1' } }));
    assert.equal(action.provider, 'fake-b');
    assert.equal(action.model, 'beta-1');
    assert.match(action.confirm, /canales de mensajería y rutinas/);
    assert.equal(defaultAction(state({ supported: false, conversation: { mode: 'custom', provider: 'fake-b', model: 'beta-1' } })), null);
});

test('a failed turn produces a chat notice; other turns do not', () => {
    assert.equal(turnNotice({ status: 'completed', model: 'alpha-1' }), null);
    assert.equal(turnNotice({ status: 'cancelled' }), null);
    const notice = turnNotice({ status: 'failed', model: 'alpha-9', error: 'HTTP 404' });
    assert.match(notice, /^El modelo alpha-9 no respondió: HTTP 404\. Elige otro/);
});

test('Core errors become one sentence', () => {
    assert.equal(requestErrorText({ detail: { code: 'unknown_model', message: 'Ese modelo no aparece.' } }), 'Ese modelo no aparece.');
    assert.equal(requestErrorText({ detail: 'La conversación activa cambió.' }), 'La conversación activa cambió.');
    assert.equal(requestErrorText({}), 'No pude cambiar el modelo.');
});

test('normalization tolerates malformed payloads', () => {
    const empty = normalizeSelectorState({ inventory: { providers: [null, { id: '' }, { id: 'x', models: 'no' }] } });
    assert.equal(empty.supported, false);
    assert.deepEqual(empty.providers.map((row) => [row.id, row.models]), [['x', []]]);
    assert.equal(normalizeSelectorState(undefined).conversation.mode, 'default');
});

test('the component never writes Hermes text as HTML', () => {
    const source = readFileSync(new URL('../../face/js/model_selector.js', import.meta.url), 'utf8');
    assert.doesNotMatch(source, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
});

test('a Gateway that is not this machine\'s cannot have its default changed from here', () => {
    const remote = state({ default_writable: false, default_reason: 'El Gateway de Hermes no es el de este equipo.',
        conversation: { mode: 'custom', provider: 'fake-b', model: 'beta-1' } });
    assert.equal(remote.defaultWritable, false);
    assert.match(remote.defaultReason, /no es el de este equipo/);
    assert.equal(defaultAction(remote), null);
});

test('the conversation context travels with the state', () => {
    assert.equal(state().conversation.context, 'default:0');
});
