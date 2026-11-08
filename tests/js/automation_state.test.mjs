/**
 * F07: the Automatizaciones view model and the general inbox envelope, with fixtures only.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    cancelText,
    createPayload,
    draftFromRule,
    emptyDraft,
    enableConfirmText,
    errorText,
    modelChoices,
    normalizeAutomations,
    permissionView,
    ruleView,
    sameDraft,
    sourceView,
    stateView,
    updatePayload,
    validateDraft,
} from '../../face/js/automation_state.js';
import { isPending, normalizeInbox } from '../../face/js/inbox_state.js';
import { normalizeSelectorState, selectionValue } from '../../face/js/model_selector_state.js';

const RULE = {
    id: 'a'.repeat(32), revision: 2, enabled: true, title: 'Investigar al irme',
    trigger: { source: 'iris_idle', threshold_s: 300, cooldown_s: 0 },
    execution: { prompt: 'Resume lo que te encargué.', profile: 'default', provider: '', model: '', timeout_s: 900,
        max_runs_per_day: 4, tool_allowlist: [], token_budget: 0 },
    delivery: { voice: false }, problem: '', updated_at: 10,
};
const occurrence = (state, extra = {}) => ({
    id: extra.id || 'b'.repeat(32), rule_id: RULE.id, rule_revision: 2, state, error: '', run_id: '',
    created_at: 100, updated_at: extra.updated_at || 100, ...extra,
});
const payload = (extra = {}) => ({
    rules: [RULE], occurrences: [], permission: 'allow', ready_sources: ['iris_idle'],
    sources: { iris_idle: { status: 'known', condition: 'idle', confirmed_s: 125 } }, ...extra,
});

test('normalization keeps only well-formed rows and never invents a state', () => {
    const state = normalizeAutomations(payload({
        rules: [RULE, RULE, { ...RULE, id: 'no' }],
        occurrences: [occurrence('completed', { result: { status: 'completed', text: '<b>hecho</b>', finished_at: 5 } }),
            occurrence('teleported', { id: 'c'.repeat(32), updated_at: 200 }), { id: 'x' }],
        permission: 'yes', ready_sources: ['iris_idle', 'gmail'],
    }));
    assert.equal(state.rules.length, 1);
    assert.equal(state.occurrences.length, 2);
    assert.equal(state.occurrences[0].state, 'teleported'); // most recent first
    assert.equal(stateView('teleported').label, 'Estado desconocido');
    assert.equal(state.occurrences[1].result.text, '<b>hecho</b>'); // plain text: the app uses textContent
    assert.equal(state.permission, 'unknown');
    assert.deepEqual([...state.readySources], ['iris_idle']);
});

test('stopping is never stopped and an unknown run is never a success', () => {
    assert.equal(stateView('stopping').label, 'Deteniéndose');
    assert.match(stateView('stopping').help, /No está detenida hasta que Hermes lo confirme/);
    assert.equal(stateView('ambiguous').label, 'Sin confirmar');
    assert.notEqual(stateView('ambiguous').tone, 'ok');
    assert.match(cancelText('stop_requested'), /Aparecerá detenida cuando Hermes lo confirme/);
    assert.match(cancelText('not_resent'), /no sabe si Hermes llegó a recibirla/);
    assert.match(cancelText('stop_not_accepted'), /sigue en curso/);
    assert.match(cancelText('completed'), /Ya había terminado \(Completada\)/);
});

test('a rule is active only as Core says, and without «Permitir» it says so', () => {
    const allowed = normalizeAutomations(payload());
    assert.equal(ruleView(allowed.rules[0], allowed).status.label, 'Activa');
    assert.equal(ruleView(allowed.rules[0], allowed).condition, 'Tras 5 min sin usar Iris');
    const asked = normalizeAutomations(payload({ permission: 'ask' }));
    const view = ruleView(asked.rules[0], asked);
    assert.equal(view.status.label, 'Activa · sin permiso');
    assert.match(view.status.help, /Permitir/);
    const paused = normalizeAutomations(payload({ rules: [{ ...RULE, enabled: false }] }));
    assert.equal(ruleView(paused.rules[0], paused).status.label, 'En pausa');
    const broken = normalizeAutomations(payload({ rules: [{ ...RULE, problem: 'Hermes 0.21.6 no permite limitar…' }] }));
    assert.equal(ruleView(broken.rules[0], broken).status.label, 'No puede ejecutarse');
    assert.match(permissionView('ask').text, /no puede preguntarte/);
});

test('an older or nonconsenting Core enables only Iris inactivity; the other sources explain why not', () => {
    const state = normalizeAutomations(payload());
    assert.deepEqual(sourceView('iris_idle', state), {
        id: 'iris_idle', label: 'Iris sin usar', help: sourceView('iris_idle', state).help, ready: true,
        now: 'Ahora: 2 min sin usar Iris.',
    });
    assert.match(sourceView('iris_idle', state).help, /No teclear puede ser leer/);
    assert.equal(sourceView('desktop_idle', state).ready, false);
    assert.match(sourceView('observed_absence', state).help, /F03/);
    const closed = normalizeAutomations(payload({ sources: { iris_idle: { status: 'unknown', reason: 'iris_closed' } } }));
    assert.match(sourceView('iris_idle', closed).now, /sin observar/);
});

test('the form validates in Spanish and builds what Core expects', () => {
    const draft = { ...emptyDraft(), title: '  Resumen  ', prompt: ' Resume el día ', thresholdMinutes: '10',
        timeoutMinutes: '20', maxRunsPerDay: '3' };
    assert.deepEqual(validateDraft(draft), {});
    assert.deepEqual(createPayload(draft), {
        title: 'Resumen', trigger: { source: 'iris_idle', threshold_s: 600 },
        execution: { prompt: 'Resume el día', timeout_s: 1200, max_runs_per_day: 3 },
    });
    const errors = validateDraft({ ...emptyDraft(), thresholdMinutes: '0', timeoutMinutes: '500', maxRunsPerDay: '1.5' });
    assert.deepEqual(Object.keys(errors).sort(), ['maxRunsPerDay', 'prompt', 'thresholdMinutes', 'timeoutMinutes', 'title']);
    assert.match(errors.thresholdMinutes, /Entre 1 y 1440/);
});

test('editing sends the revision that was read; a chosen model travels only with the rule', () => {
    const rule = normalizeAutomations(payload()).rules[0];
    const draft = { ...draftFromRule(rule), model: selectionValue('openai-codex', 'gpt-5.5') };
    assert.ok(sameDraft(draftFromRule(rule), draftFromRule(rule)));
    assert.ok(!sameDraft(draft, draftFromRule(rule)));
    const body = updatePayload(rule, draft);
    assert.equal(body.expected_revision, 2);
    assert.deepEqual(body.execution, { prompt: rule.prompt, timeout_s: 900, max_runs_per_day: 4,
        provider: 'openai-codex', model: 'gpt-5.5' });
    assert.ok(!('enabled' in body)); // saving never enables
});

test('models come from the F01 inventory, only from providers with a session', () => {
    const selector = normalizeSelectorState({
        supported: true,
        inventory: { providers: [
            { id: 'openai-codex', label: 'OpenAI Codex', authenticated: true, models: ['gpt-5.5'] },
            { id: 'sin-sesion', label: 'Sin sesión', authenticated: false, models: ['m1'] },
        ] },
        conversation: { mode: 'default' }, default: { provider: 'openai-codex', model: 'gpt-5.5' },
    });
    assert.deepEqual(modelChoices(selector), [{ label: 'OpenAI Codex',
        models: [{ value: selectionValue('openai-codex', 'gpt-5.5'), label: 'gpt-5.5' }] }]);
    assert.deepEqual(modelChoices(null), []);
});

test('answers become honest Spanish', () => {
    assert.match(errorText({ detail: { code: 'revision_conflict', message: 'x' } }, 'f'), /cambió en otro lugar/);
    assert.equal(errorText({ detail: { code: 'rule_not_ready', message: 'La entrega por voz todavía no existe' } }, 'f'),
        'La entrega por voz todavía no existe');
    assert.equal(errorText({}, 'No se pudo guardar.'), 'No se pudo guardar.');
    const state = normalizeAutomations(payload({ permission: 'ask' }));
    const confirmText = enableConfirmText(state.rules[0], state);
    assert.match(confirmText, /como máximo 4 al día/);
    assert.match(confirmText, /Permitir/);
});

// ── The general inbox envelope ────────────────────────────────────────────

const envelope = (origin, extra = {}) => ({
    schema: 'iris.inbox-item/v1', origin, profile: 'default', source_id: 'd'.repeat(32),
    key: `${origin}:default:${'d'.repeat(32)}`, title: 'T', text: 'x', outcome: origin === 'vision' ? 'notice' : 'completed',
    delivery: { status: 'pending' }, created_at: 1, updated_at: 1, ...extra,
});

test('the inbox keeps composite identities apart and drops unknown or reserved origins', () => {
    const inbox = normalizeInbox({ items: [envelope('automation'), envelope('vision'), envelope('automation'),
        envelope('gmail'), envelope('automation', { key: 'forged' }), envelope('automation', { outcome: 'ok' })] });
    assert.deepEqual(inbox.items.map((item) => item.key), [
        `automation:default:${'d'.repeat(32)}`, `vision:default:${'d'.repeat(32)}`]);
    assert.equal(inbox.unread, 2);
});

test('presented is not read, and snoozed comes back only when due', () => {
    const presented = normalizeInbox({ items: [envelope('automation', { delivery: { status: 'presented', presented_at: 5 } })] });
    assert.equal(presented.unread, 1);
    assert.ok(isPending(presented.items[0]));
    const snoozed = normalizeInbox({ items: [envelope('automation', { delivery: { status: 'snoozed', snoozed_until: 100 } })] });
    assert.equal(isPending(snoozed.items[0], 50), false);
    assert.equal(isPending(snoozed.items[0], 150), true);
});
