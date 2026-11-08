/**
 * F01 model selector — pure view model.
 *
 * Turns Core's `/api/models/selector` document into what the composer shows:
 * the pill, the state line, the radio options and the default-model action.
 * No DOM here, so every state is testable (tests/js/model_selector_state.test.mjs).
 */

const safeText = (value, max = 200) => String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, max);

/** Hermes' error text as the end of a sentence: ": text." (or "." when empty). */
const reasonTail = (text) => (text ? `: ${/[.!?…]$/.test(text) ? text : `${text}.`}` : '.');

export const SELECTION_SEPARATOR = '\u0000';

export function selectionValue(provider, model) {
    return `${provider}${SELECTION_SEPARATOR}${model}`;
}

export function parseSelectionValue(value) {
    const [provider = '', model = ''] = String(value || '').split(SELECTION_SEPARATOR);
    return { provider, model };
}

const REASONS = {
    hermes_offline: 'El servicio de IA está detenido. Pulsa «Iniciar IRIS» para elegir modelo.',
    hermes_without_session_models: 'Esta versión del motor de IA no permite elegir el modelo por conversación.',
};

export function normalizeSelectorState(payload = {}) {
    const conversation = payload?.conversation || {};
    const last = conversation.last && typeof conversation.last === 'object' ? conversation.last : null;
    const providers = Array.isArray(payload?.inventory?.providers) ? payload.inventory.providers : [];
    return {
        supported: payload?.supported === true,
        reason: safeText(payload?.reason, 64),
        busy: payload?.busy === true,
        warning: safeText(payload?.warning, 400),
        // Only a Gateway accredited as this machine's can have its default changed here.
        defaultWritable: payload?.default_writable === true,
        defaultReason: safeText(payload?.default_reason, 300),
        conversation: {
            sessionId: safeText(conversation.session_id, 192),
            // Names the conversation a write is for; Core refuses it once switched.
            context: safeText(conversation.context, 120),
            mode: conversation.mode === 'custom' ? 'custom' : 'default',
            provider: safeText(conversation.provider, 80),
            model: safeText(conversation.model, 200),
            state: safeText(conversation.state, 24) || 'pending',
            last: last && {
                status: safeText(last.status, 24),
                confirmed: last.confirmed === true,
                error: safeText(last.error, 400),
                effectiveModel: safeText(last.effective?.model, 200),
                model: safeText(last.model, 200),
            },
        },
        default: {
            provider: safeText(payload?.default?.provider, 80),
            model: safeText(payload?.default?.model, 200),
        },
        inventoryError: safeText(payload?.inventory?.error, 300),
        // The models the user chose to list ("Modelos del selector"); null lists all.
        visible: Array.isArray(payload?.visible)
            ? new Set(payload.visible
                .filter((item) => item && safeText(item.provider, 80) && safeText(item.model, 200))
                .map((item) => selectionValue(safeText(item.provider, 80), safeText(item.model, 200))))
            : null,
        providers: providers
            .filter((row) => row && safeText(row.id, 80))
            .map((row) => ({
                id: safeText(row.id, 80),
                label: safeText(row.label, 80) || safeText(row.id, 80),
                current: row.current === true,
                authenticated: row.authenticated === true,
                warning: safeText(row.warning, 300),
                models: (Array.isArray(row.models) ? row.models : []).map((m) => safeText(m, 200)).filter(Boolean),
            })),
    };
}

function providerLabel(state, id) {
    return state.providers.find((row) => row.id === id)?.label || id;
}

/** Short text for the composer pill. */
export function pillView(state, { loading = false, requestError = '' } = {}) {
    if (!state) {
        return { tone: 'loading', label: 'Modelo', detail: 'Cargando…', title: 'Cargando el modelo de esta conversación.' };
    }
    const conv = state.conversation;
    const custom = conv.mode === 'custom';
    const label = custom ? (conv.model || 'Modelo') : 'Predeterminado';
    const detail = custom ? providerLabel(state, conv.provider) : (conv.model || state.default.model || 'según el motor de IA');
    if (!state.supported) {
        return { tone: 'disabled', label: 'Modelo', detail: 'no disponible', title: REASONS[state.reason] || REASONS.hermes_offline };
    }
    const status = statusView(state, { loading, requestError });
    return { tone: status.tone, label, detail, title: status.text };
}

/** The state line inside the dialog (and the pill's tooltip). */
export function statusView(state, { loading = false, requestError = '' } = {}) {
    if (requestError) return { tone: 'failed', text: requestError };
    if (loading) return { tone: 'loading', text: 'Actualizando…' };
    if (!state?.supported) return { tone: 'disabled', text: REASONS[state?.reason] || REASONS.hermes_offline };
    const conv = state.conversation;
    const last = conv.last;
    switch (conv.state) {
        case 'confirmed':
            return { tone: 'confirmed', text: `Última respuesta confirmada con ${last?.effectiveModel || conv.model}.` };
        case 'failed':
            return {
                tone: 'failed',
                text: `La última respuesta falló con este modelo${reasonTail(last?.error)} Elige otro o vuelve al predeterminado.`,
            };
        case 'cancelled':
            return { tone: 'unconfirmed', text: 'La última respuesta se detuvo antes de terminar.' };
        case 'unconfirmed':
            return { tone: 'unconfirmed', text: 'El motor de IA no confirmó qué modelo dio la última respuesta.' };
        default:
            return {
                tone: 'pending',
                text: state.busy
                    ? 'IRIS está respondiendo con el modelo anterior; el cambio se aplica en tu próximo mensaje.'
                    : 'Se aplicará en tu próximo mensaje.',
            };
    }
}

/**
 * Radio options: "Predeterminado" first, then each provider with its models.
 * A provider without credentials is shown (so the user learns why) but its
 * models cannot be chosen. When the user chose which models to list, the
 * others are hidden (`hidden` counts them), except the conversation's own
 * model and the default, so the current choice is always visible.
 */
export function optionGroups(state, query = '') {
    if (!state) return { defaultOption: null, groups: [], total: 0, shown: 0, hidden: 0 };
    const needle = safeText(query, 80).toLowerCase();
    const conv = state.conversation;
    const checked = conv.mode === 'custom' ? selectionValue(conv.provider, conv.model) : 'default';
    const defaultLabel = state.default.model
        ? `${state.default.model} · ${providerLabel(state, state.default.provider)}`
        : 'El modelo predeterminado del motor de IA';
    let total = 0;
    let shown = 0;
    let hidden = 0;
    const groups = [];
    for (const row of state.providers) {
        const providerMatch = !needle || row.label.toLowerCase().includes(needle) || row.id.toLowerCase().includes(needle);
        const models = [];
        let rowHidden = 0;
        for (const model of row.models) {
            total += 1;
            const value = selectionValue(row.id, model);
            const isDefault = row.id === state.default.provider && model === state.default.model;
            if (state.visible && !state.visible.has(value) && value !== checked && !isDefault) {
                rowHidden += 1;
                continue;
            }
            if (needle && !providerMatch && !model.toLowerCase().includes(needle)) continue;
            shown += 1;
            models.push({
                value,
                model,
                checked: value === checked,
                isDefault,
                disabled: !state.supported || !row.authenticated,
            });
        }
        hidden += rowHidden;
        // A provider whose models were all hidden by the user's choice leaves the list.
        if (!models.length && (needle || row.authenticated || rowHidden)) continue;
        groups.push({
            id: row.id,
            label: row.label,
            current: row.current,
            selectable: row.authenticated,
            warning: row.authenticated ? '' : (row.warning || 'Sin sesión ni clave guardada. Configura este proveedor en el asistente.'),
            models,
        });
    }
    return {
        defaultOption: { value: 'default', label: 'Predeterminado', detail: defaultLabel, checked: checked === 'default', disabled: !state.supported },
        groups,
        total,
        shown,
        hidden,
    };
}

/** "Usar como predeterminado": only for this conversation's own model when it differs from the default. */
export function defaultAction(state) {
    if (!state?.supported || !state.defaultWritable) return null;
    const conv = state.conversation;
    if (conv.mode !== 'custom' || !conv.provider || !conv.model) return null;
    if (conv.provider === state.default.provider && conv.model === state.default.model) return null;
    return {
        provider: conv.provider,
        model: conv.model,
        label: `Usar ${conv.model} como predeterminado`,
        confirm:
            `El motor de IA usará ${conv.model} (${providerLabel(state, conv.provider)}) en todo lo que no tenga un modelo propio: ` +
            'otras conversaciones, canales de mensajería y rutinas.',
    };
}

/** Chat notice for a turn that did not answer with its model (null when nothing to say). */
export function turnNotice(outcome = {}) {
    const status = safeText(outcome?.status, 24);
    if (status !== 'failed') return null;
    const model = safeText(outcome?.model, 200);
    const error = safeText(outcome?.error, 300);
    return `${model ? `El modelo ${model}` : 'El modelo'} no respondió${reasonTail(error)} ` +
        'Elige otro en el selector de modelo o vuelve al predeterminado.';
}

/** A Core error body (`detail` string or `{code, message}`) as one sentence. */
export function requestErrorText(body, fallback = 'No pude cambiar el modelo.') {
    const detail = body?.detail;
    if (typeof detail === 'string') return safeText(detail, 300) || fallback;
    if (detail && typeof detail === 'object') return safeText(detail.message, 300) || fallback;
    return fallback;
}
