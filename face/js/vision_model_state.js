/**
 * F02 "Visión" (Modelos app) — pure view model of `GET /api/vision-model`.
 *
 * The server document says what the profile has configured, the route Hermes'
 * resolver plans for it and, per authenticated model, whether Hermes says it
 * reads images (true), does not (false) or cannot tell (null). The view never
 * claims more than that: an unknown capability stays "sin confirmar".
 */

function text(value) {
    return String(value ?? '').trim();
}

export function routeLabel(route = {}) {
    const model = text(route.model);
    const provider = text(route.provider);
    if (!model && !provider) return '';
    return model && provider ? `${model} (${provider})` : model || provider;
}

export function normalizeVisionState(raw = {}) {
    const configured = raw.configured && typeof raw.configured === 'object' ? raw.configured : {};
    const planned = raw.planned && typeof raw.planned === 'object' ? raw.planned : {};
    const models = Array.isArray(raw.models) ? raw.models : [];
    return {
        profile: text(raw.profile) || 'default',
        mode: raw.mode === 'model' ? 'model' : 'auto',
        route: raw.route === 'vision_model' ? 'vision_model' : 'chat',
        configured: {
            provider: text(configured.provider) || 'auto',
            model: text(configured.model),
            customEndpoint: configured.custom_endpoint === true,
            imageInputMode: text(configured.image_input_mode) || 'auto',
        },
        planned: {
            provider: text(planned.provider),
            model: text(planned.model),
            available: planned.available === true,
            known: Object.prototype.hasOwnProperty.call(planned, 'available'),
        },
        chosenVision: raw.chosen_vision === true ? true : raw.chosen_vision === false ? false : null,
        models: models
            .map((row) => ({
                provider: text(row?.provider),
                providerLabel: text(row?.provider_label) || text(row?.provider),
                model: text(row?.model),
                vision: row?.vision === true ? true : row?.vision === false ? false : null,
            }))
            .filter((row) => row.provider && row.model),
        catalogError: text(raw.catalog_error),
        error: text(raw.error),
        writable: raw.writable === true,
        reason: text(raw.reason),
        last: raw.last && typeof raw.last === 'object' ? {
            ok: raw.last.ok === true,
            provider: text(raw.last.provider),
            model: text(raw.last.model),
            code: text(raw.last.code),
            at: Number(raw.last.at) || 0,
        } : null,
    };
}

export function modelKey(provider, model) {
    return `${provider}\u0000${model}`;
}

export function draftFromState(state) {
    if (!state || state.mode !== 'model' || state.configured.customEndpoint) return { mode: 'auto', key: '' };
    return { mode: 'model', key: modelKey(state.configured.provider, state.configured.model) };
}

export function sameDraft(a, b) {
    return Boolean(a && b && a.mode === b.mode && (a.mode === 'auto' || a.key === b.key));
}

/** The choices: models that read images, then the unconfirmed ones; never one Hermes says cannot. */
export function choiceGroups(state, draft) {
    const groups = [
        { id: 'vision', label: 'Leen imágenes', models: [] },
        { id: 'unknown', label: 'Sin confirmar', models: [] },
    ];
    let excluded = 0;
    for (const row of state?.models || []) {
        if (row.vision === false) {
            excluded += 1;
            continue;
        }
        groups[row.vision ? 0 : 1].models.push({ ...row, key: modelKey(row.provider, row.model) });
    }
    // The saved choice stays listed even if the catalog no longer offers it.
    if (draft?.mode === 'model' && draft.key && !groups.some((g) => g.models.some((m) => m.key === draft.key))) {
        const [provider, model] = draft.key.split('\u0000');
        groups[1].models.unshift({ provider, providerLabel: provider, model, vision: null, key: draft.key, missing: true });
    }
    return { groups: groups.filter((group) => group.models.length), excluded };
}

export function payloadFor(state, draft) {
    if (!draft || draft.mode !== 'model' || !draft.key) return { profile: state?.profile || 'default', mode: 'auto' };
    const [provider, model] = draft.key.split('\u0000');
    return { profile: state?.profile || 'default', mode: 'model', provider, model };
}

/** What happens to an image with the saved setting, in the user's words. */
export function routeSummary(state) {
    if (!state) return { tone: 'muted', lines: ['Consultando a Hermes…'] };
    const lines = [];
    let tone = 'ok';
    const planned = routeLabel(state.planned);
    if (state.configured.customEndpoint) {
        lines.push('Este perfil usa un endpoint de visión configurado fuera de Iris. Se cambia desde Hermes.');
        tone = 'muted';
    }
    if (state.route === 'vision_model') {
        const chosen = state.mode === 'model' ? routeLabel(state.configured) : planned;
        lines.push(`Imágenes adjuntas y fotos: las describe ${chosen || 'el modelo de visión'} y el chat recibe esa descripción.`);
        if (state.planned.known && !state.planned.available) {
            lines.push('Hermes no puede usar ese modelo ahora: Iris no enviará imágenes hasta que esté disponible.');
            tone = 'warn';
        } else if (planned && state.mode === 'model' && planned !== routeLabel(state.configured)) {
            lines.push(`Hermes resuelve otra ruta (${planned}); revisa el proveedor antes de enviar imágenes.`);
            tone = 'warn';
        }
        if (state.mode === 'model' && state.chosenVision === null && !state.configured.customEndpoint) {
            lines.push('Hermes no puede confirmar que este modelo lea imágenes.');
            if (tone === 'ok') tone = 'warn';
        }
    } else {
        lines.push('Imágenes adjuntas: viajan con tu mensaje. Si el modelo del chat lee imágenes, las ve él; si no, Hermes usa su ruta de visión automática.');
        lines.push(state.planned.available
            ? `Fotos que le pidas a Iris: las analiza ${planned}.`
            : 'Fotos que le pidas a Iris: Hermes no tiene ahora un modelo de visión disponible.');
        if (!state.planned.available && state.planned.known) tone = 'warn';
    }
    if (state.configured.imageInputMode !== 'auto') {
        lines.push(`Hermes tiene fijado agent.image_input_mode: ${state.configured.imageInputMode}.`);
    }
    return { tone, lines };
}

export function lastSummary(state, now = Date.now()) {
    const last = state?.last;
    if (!last) return '';
    const minutes = last.at ? Math.max(0, Math.round((now / 1000 - last.at) / 60)) : null;
    const when = minutes === null ? '' : minutes < 1 ? ' hace un momento' : ` hace ${minutes} min`;
    const who = routeLabel(last);
    return last.ok
        ? `Última imagen: la analizó ${who || 'el modelo de visión'}${when}.`
        : `Última imagen: no se pudo analizar${who ? ` con ${who}` : ''}${when}.`;
}
