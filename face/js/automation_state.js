/**
 * F07 "Automatizaciones": the view model of Core's `/api/automations`.
 *
 * Everything shown is what Core answered: a rule is active only if Core says
 * so, a sent task is "en curso" only while Hermes runs it, and a cancellation
 * becomes "detenida" only once Hermes confirmed it. "stopping" is never shown
 * as stopped and an unknown run is never a success. Texts from Hermes or the
 * user are kept as plain strings (the app sets them with textContent).
 */

import { optionGroups, parseSelectionValue, selectionValue } from './model_selector_state.js?v=1';

const text = (value, limit) => String(value ?? '').slice(0, limit);
const number = (value, fallback = 0) => (Number.isFinite(Number(value)) ? Number(value) : fallback);
const ID = /^[0-9a-f]{32}$/;

// Execution state of an occurrence: what Iris and Hermes did, never whether it was seen.
export const OCCURRENCE_STATES = Object.freeze({
    candidate: { label: 'Preparando', tone: 'pending', help: 'La condición se cumplió; Iris la está preparando.' },
    claimed: { label: 'Reservada', tone: 'pending', help: 'Iris la reservó y va a comprobar todo antes de enviarla.' },
    submitted: { label: 'Enviada', tone: 'running', help: 'Se envió a Hermes; falta su respuesta.' },
    running: { label: 'En curso', tone: 'running', help: 'Hermes la está ejecutando.' },
    ambiguous: { label: 'Sin confirmar', tone: 'warning',
        help: 'No llegó la respuesta de Hermes. Iris solo la repite si sigue siendo válida, y nunca crea otra.' },
    stopping: { label: 'Deteniéndose', tone: 'warning',
        help: 'Iris ya no la enviará. No está detenida hasta que Hermes lo confirme o venza su plazo.' },
    completed: { label: 'Completada', tone: 'ok', help: 'Hermes terminó y su resultado está guardado.' },
    failed: { label: 'Fallida', tone: 'error', help: 'No terminó bien; el resultado explica por qué.' },
    cancelled: { label: 'Cancelada', tone: 'muted', help: 'No se envió a Hermes.' },
});
const UNKNOWN_STATE = { label: 'Estado desconocido', tone: 'warning', help: 'Core devolvió un estado que esta versión no conoce.' };
export const ACTIVE_STATES = new Set(['candidate', 'claimed', 'submitted', 'running', 'ambiguous', 'stopping']);
export const CANCELLABLE_STATES = new Set(['candidate', 'claimed', 'submitted', 'running', 'ambiguous']);

// Where "no estás" comes from. Only Iris' own inactivity can be enabled today.
export const SOURCES = Object.freeze({
    iris_idle: {
        label: 'Iris sin usar',
        phrase: 'sin usar Iris',
        help: 'Cuenta el tiempo sin interacción en Iris mientras Iris está abierto. No teclear puede ser leer: '
            + 'no mide si estás frente al PC ni la actividad de Windows.',
    },
    desktop_idle: { label: 'Windows sin usar', phrase: 'sin usar Windows', help: 'Cuenta solo intervalos observados sin teclado ni ratón en tu sesión de Windows. Funciona minimizado si Core sigue abierto. No demuestra ausencia física.' },
    observed_absence: { label: 'Ausencia ante la cámara', phrase: 'de ausencia ante la cámara', help: 'Todavía no disponible: se habilita cuando se valide la cámara (F03).' },
});

export const LIMITS = Object.freeze({
    titleMax: 120, promptMax: 4000,
    thresholdMinutes: [1, 1440], timeoutMinutes: [1, 120], maxRunsPerDay: [1, 48],
});

function normalizeRule(row) {
    if (!row || !ID.test(row.id || '')) return null;
    const trigger = row.trigger || {};
    const execution = row.execution || {};
    return {
        id: row.id,
        revision: Math.max(1, Math.trunc(number(row.revision, 1))),
        enabled: row.enabled === true,
        title: text(row.title, LIMITS.titleMax),
        source: text(trigger.source, 40),
        thresholdS: Math.max(0, number(trigger.threshold_s)),
        cooldownS: Math.max(0, number(trigger.cooldown_s)),
        prompt: text(execution.prompt, LIMITS.promptMax),
        profile: text(execution.profile, 64) || 'default',
        provider: text(execution.provider, 120),
        model: text(execution.model, 200),
        timeoutS: Math.max(0, number(execution.timeout_s)),
        maxRunsPerDay: Math.max(0, Math.trunc(number(execution.max_runs_per_day))),
        toolLimits: (Array.isArray(execution.tool_allowlist) && execution.tool_allowlist.length > 0)
            || number(execution.token_budget) > 0,
        voice: row.delivery?.voice === true,
        delivery: row.delivery ? { ...row.delivery } : { voice: false },
        problem: text(row.problem, 600),
        updatedAt: number(row.updated_at),
    };
}

function normalizeOccurrence(row) {
    if (!row || !ID.test(row.id || '') || !ID.test(row.rule_id || '')) return null;
    const result = row.result && typeof row.result === 'object' ? row.result : null;
    return {
        id: row.id,
        ruleId: row.rule_id,
        ruleRevision: Math.max(1, Math.trunc(number(row.rule_revision, 1))),
        state: text(row.state, 24),
        error: text(row.error, 500),
        runKnown: Boolean(text(row.run_id, 128)),
        createdAt: number(row.created_at),
        updatedAt: number(row.updated_at),
        result: result && {
            status: result.status === 'completed' ? 'completed' : 'failed',
            text: text(result.text, 20000),
            finishedAt: number(result.finished_at),
        },
    };
}

const countPresenceRevision = (value) => Number.isInteger(value) && value >= 0 ? value : 0;

function normalizeSource(row = {}) {
    return {
        known: row.status === 'known',
        condition: text(row.condition, 24),
        confirmedS: Math.max(0, number(row.confirmed_s)),
        reason: text(row.reason, 64),
    };
}

/** What `GET /api/automations` answered, normalized; unknown rows are left out. */
export function normalizeAutomations(payload = {}) {
    payload = payload && typeof payload === 'object' ? payload : {};
    const seen = new Set();
    const rules = (Array.isArray(payload.rules) ? payload.rules : []).map(normalizeRule)
        .filter((rule) => rule && !seen.has(rule.id) && seen.add(rule.id));
    const occurrences = (Array.isArray(payload.occurrences) ? payload.occurrences : []).map(normalizeOccurrence)
        .filter(Boolean).sort((a, b) => b.updatedAt - a.updatedAt);
    const sources = payload.sources && typeof payload.sources === 'object' ? payload.sources : {};
    return {
        rules,
        occurrences,
        sources: Object.fromEntries(Object.keys(SOURCES).map((id) => [id, normalizeSource(sources[id])])),
        permission: ['allow', 'ask', 'deny'].includes(payload.permission) ? payload.permission : 'unknown',
        readySources: new Set((Array.isArray(payload.ready_sources) ? payload.ready_sources : []).filter((id) => id in SOURCES)),
        presence: payload.presence && typeof payload.presence === 'object' ? {
            revision: countPresenceRevision(payload.presence.revision),
            supported: payload.presence.supported === true,
            desktopIdle: payload.presence.desktop_idle === true,
            returnVoice: payload.presence.return_voice === true,
            voiceSupported: payload.presence.voice_supported === true,
            focus: payload.presence.focus === true,
        } : null,
        storeProblem: text(payload.store_problem, 600),
    };
}

export function stateView(state) {
    return OCCURRENCE_STATES[state] || UNKNOWN_STATE;
}

const minutes = (seconds) => Math.max(1, Math.round(seconds / 60));
export function minutesText(seconds) {
    const total = minutes(seconds);
    if (total < 60) return `${total} min`;
    const hours = Math.floor(total / 60);
    return total % 60 ? `${hours} h ${total % 60} min` : `${hours} h`;
}

/** The rule in one line, and what it is doing now. */
export function ruleView(rule, state) {
    const source = SOURCES[rule.source];
    const condition = source ? `Tras ${minutesText(rule.thresholdS)} ${source.phrase}` : 'Condición desconocida';
    const last = state.occurrences.find((occurrence) => occurrence.ruleId === rule.id) || null;
    let status;
    if (rule.problem && rule.enabled) status = { label: 'No puede ejecutarse', tone: 'error', help: rule.problem };
    else if (rule.enabled) {
        status = state.permission === 'allow'
            ? { label: 'Activa', tone: 'ok', help: 'Se ejecutará cuando se cumpla la condición.' }
            : { label: 'Activa · sin permiso', tone: 'warning', help: permissionView(state.permission).text };
    } else status = { label: 'En pausa', tone: 'muted', help: rule.problem || 'No se ejecutará hasta que la actives.' };
    return { condition, status, last, model: rule.provider ? `${rule.model} (${rule.provider})` : 'Modelo del perfil' };
}

export function permissionView(permission) {
    return {
        allow: { tone: 'ok', label: 'Permitir',
            text: 'Las reglas activas se ejecutan en Hermes sin preguntarte, con los límites de cada regla.' },
        ask: { tone: 'warning', label: 'Preguntar',
            text: 'Nada se ejecuta solo: una automatización ocurre cuando no estás y no puede preguntarte. Para que corran, '
                + 'pon «Automatizaciones» en «Permitir» en Capability Center.' },
        deny: { tone: 'error', label: 'Denegar', text: 'Las automatizaciones están bloqueadas en Capability Center.' },
    }[permission] || { tone: 'warning', label: 'Desconocido', text: 'No se pudo leer el permiso de Capability Center.' };
}

export function sourceView(id, state) {
    const source = SOURCES[id];
    const ready = state.readySources.has(id);
    const signal = state.sources[id];
    let now;
    if (!ready) now = 'Todavía no disponible.';
    else if (!signal?.known) now = 'Ahora: sin observar de forma continua; el intervalo no cuenta.';
    else if (signal.condition === 'idle') now = `Ahora: ${minutesText(signal.confirmedS)} ${source.phrase}.`;
    else now = id === 'desktop_idle' ? 'Ahora: hay entrada reciente en Windows.' : 'Ahora: estás usando Iris.';
    return { id, label: source.label, help: source.help, ready, now };
}

// ── The rule form ────────────────────────────────────────────────────────

export function emptyDraft() {
    return { title: '', prompt: '', source: 'iris_idle', thresholdMinutes: '5', timeoutMinutes: '15',
        maxRunsPerDay: '4', model: '', voice: false };
}

export function draftFromRule(rule) {
    return {
        title: rule.title,
        prompt: rule.prompt,
        source: rule.source,
        thresholdMinutes: String(minutes(rule.thresholdS)),
        timeoutMinutes: String(minutes(rule.timeoutS)),
        maxRunsPerDay: String(rule.maxRunsPerDay),
        model: rule.provider ? selectionValue(rule.provider, rule.model) : '',
        voice: rule.voice === true,
    };
}

function whole(value, [min, max]) {
    const n = Number(String(value ?? '').trim());
    return Number.isInteger(n) && n >= min && n <= max ? n : null;
}

/** Field errors in Spanish; an empty object means the draft can be saved. */
export function validateDraft(draft) {
    const errors = {};
    if (!draft.title.trim()) errors.title = 'Ponle un nombre.';
    else if (draft.title.trim().length > LIMITS.titleMax) errors.title = `Hasta ${LIMITS.titleMax} caracteres.`;
    if (!draft.prompt.trim()) errors.prompt = 'Escribe qué debe hacer Hermes.';
    else if (draft.prompt.trim().length > LIMITS.promptMax) errors.prompt = `Hasta ${LIMITS.promptMax} caracteres.`;
    if (!(draft.source in SOURCES)) errors.source = 'Elige una condición.';
    if (whole(draft.thresholdMinutes, LIMITS.thresholdMinutes) === null) errors.thresholdMinutes = 'Entre 1 y 1440 minutos.';
    if (whole(draft.timeoutMinutes, LIMITS.timeoutMinutes) === null) errors.timeoutMinutes = 'Entre 1 y 120 minutos.';
    if (whole(draft.maxRunsPerDay, LIMITS.maxRunsPerDay) === null) errors.maxRunsPerDay = 'Entre 1 y 48 al día.';
    if (draft.model && !parseSelectionValue(draft.model)) errors.model = 'Elige un modelo de la lista.';
    return errors;
}

function sections(draft) {
    const chosen = draft.model ? parseSelectionValue(draft.model) : null;
    return {
        title: draft.title.trim(),
        trigger: { source: draft.source, threshold_s: whole(draft.thresholdMinutes, LIMITS.thresholdMinutes) * 60 },
        execution: {
            prompt: draft.prompt.trim(),
            timeout_s: Math.max(30, whole(draft.timeoutMinutes, LIMITS.timeoutMinutes) * 60),
            max_runs_per_day: whole(draft.maxRunsPerDay, LIMITS.maxRunsPerDay),
            ...(chosen ? { provider: chosen.provider, model: chosen.model } : {}),
        },
    };
}

/** `POST /api/automations/rules`: always created paused; Core enforces it too. */
export function createPayload(draft) {
    return { ...sections(draft), ...(draft.voice ? { delivery: { voice: true } } : {}) };
}

/** `PUT /api/automations/rules/{id}`: the edited sections, against the revision that was read. */
export function updatePayload(rule, draft) {
    const next = sections(draft);
    return { expected_revision: rule.revision, ...next, delivery: { ...(rule.delivery || {}), voice: draft.voice === true } };
}

export function sameDraft(a, b) {
    return Boolean(a && b) && Object.keys(emptyDraft()).every((key) => String(a[key]) === String(b[key]));
}

/** The models a rule may name: the F01 inventory, never changing the conversation's model. */
export function modelChoices(selectorState) {
    if (!selectorState?.supported) return [];
    return optionGroups(selectorState).groups
        .filter((group) => group.selectable && group.models.length)
        .map((group) => ({
            label: group.label,
            models: group.models.map((option) => ({ value: option.value, label: option.model })),
        }));
}

// ── Answers ──────────────────────────────────────────────────────────────

export function errorText(body, fallback) {
    const detail = body?.detail;
    if (detail && typeof detail === 'object') {
        if (detail.code === 'revision_conflict') return 'La regla cambió en otro lugar. Recargué su última versión; revisa y vuelve a guardar.';
        if (detail.code === 'invalid_rule') return 'Core rechazó la regla. Revisa los campos.';
        if (typeof detail.message === 'string' && detail.message) return detail.message;
    }
    if (typeof detail === 'string' && detail) return detail;
    return fallback;
}

/** What a cancellation really did (`POST …/cancel`). */
export function cancelText(result) {
    return {
        cancelled: 'Cancelada: no se envió a Hermes.',
        stop_requested: 'Se pidió a Hermes detenerla. Aparecerá detenida cuando Hermes lo confirme.',
        stop_not_accepted: 'Hermes no aceptó detenerla; sigue en curso y se seguirá hasta su plazo.',
        stop_not_possible: 'Cambió el Hermes o la credencial con que se envió: Iris no puede detenerla desde aquí.',
        not_resent: 'No se volverá a enviar. Iris no sabe si Hermes llegó a recibirla; lo dirá al vencer su plazo.',
        unknown: 'Esa ejecución ya no existe.',
    }[result] || `Ya había terminado (${stateView(result).label}).`;
}

export function enableConfirmText(rule, state) {
    const lines = [
        `«${rule.title}» se ejecutará en Hermes ${ruleView(rule, state).condition.replace(/^Tras/, 'tras')}, una vez por episodio y `
            + `como máximo ${rule.maxRunsPerDay} al día.`,
        'Hermes usará sus herramientas y sus aprobaciones habituales; la tarea corre sin que estés.',
    ];
    if (state.permission !== 'allow') lines.push(permissionView(state.permission).text);
    lines.push('¿Activarla?');
    return lines.join('\n\n');
}

export { selectionValue };
