/**
 * "Modelos del selector" — pure view model.
 *
 * One login can publish hundreds of models (qa9: an OpenAI session already
 * filled the chat selector). This app chooses which ones the selector lists.
 * The draft is `null` (list every model) or a Set of selection values; it only
 * filters the selector, it never adds a model Hermes did not publish.
 * No DOM here (tests/js/model_catalog_state.test.mjs).
 */

import { parseSelectionValue, selectionValue } from './model_selector_state.js?v=3';

/** Every selection value of the inventory, in display order. */
export function allValues(state) {
    return (state?.providers || []).flatMap((row) => row.models.map((model) => selectionValue(row.id, model)));
}

function isListed(draft, value) {
    return draft === null || draft.has(value);
}

/** Providers with a session first; each with its models, counts and the query applied. */
export function catalogView(state, draft, query = '') {
    const needle = String(query || '').trim().toLowerCase();
    const conv = state?.conversation || {};
    const current = conv.mode === 'custom' ? selectionValue(conv.provider, conv.model) : '';
    const rows = [...(state?.providers || [])].sort((a, b) => Number(b.authenticated) - Number(a.authenticated));
    let listed = 0;
    let total = 0;
    const groups = [];
    for (const row of rows) {
        const providerMatch = !needle || row.label.toLowerCase().includes(needle) || row.id.toLowerCase().includes(needle);
        const models = [];
        let rowListed = 0;
        for (const model of row.models) {
            const value = selectionValue(row.id, model);
            const checked = isListed(draft, value);
            total += 1;
            if (checked) {
                listed += 1;
                rowListed += 1;
            }
            if (needle && !providerMatch && !model.toLowerCase().includes(needle)) continue;
            models.push({
                value,
                model,
                checked,
                isDefault: row.id === state.default?.provider && model === state.default?.model,
                current: value === current,
            });
        }
        if (!models.length && needle) continue;
        groups.push({
            id: row.id,
            label: row.label,
            authenticated: row.authenticated,
            warning: row.authenticated ? '' : 'Sin sesión ni clave guardada: sus modelos todavía no se pueden usar.',
            listed: rowListed,
            total: row.models.length,
            models,
        });
    }
    return { groups, listed, total, showsAll: draft === null };
}

/** The draft after (un)listing one model. Starting from "all", the others stay listed. */
export function toggleModel(draft, state, value, checked) {
    const next = new Set(draft === null ? allValues(state) : draft);
    if (checked) next.add(value);
    else next.delete(value);
    return next;
}

/** The draft after (un)listing every model of one provider. */
export function toggleProvider(draft, state, providerId, checked) {
    const row = (state?.providers || []).find((item) => item.id === providerId);
    if (!row) return draft;
    const next = new Set(draft === null ? allValues(state) : draft);
    for (const model of row.models) {
        const value = selectionValue(row.id, model);
        if (checked) next.add(value);
        else next.delete(value);
    }
    return next;
}

/** What PUT /api/models/visible receives: null lists every model. */
export function draftPayload(draft) {
    if (draft === null) return null;
    return [...draft].map(parseSelectionValue).filter((item) => item.provider && item.model);
}

/** The draft the saved choice describes (`state.visible` is null or a Set). */
export function draftFromState(state) {
    return state?.visible ? new Set(state.visible) : null;
}

export function sameDraft(a, b) {
    if (a === null || b === null) return a === b;
    if (a.size !== b.size) return false;
    for (const value of a) if (!b.has(value)) return false;
    return true;
}
