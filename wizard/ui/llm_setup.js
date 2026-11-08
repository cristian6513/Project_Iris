/* The AI provider step and the Gateway gate (pure logic, testable without a browser).
 *
 * The main provider is the one Hermes runs by default. Once it is saved and passes the
 * Gateway's pre-flight, its section folds into a summary with «Cambiar proveedor» and
 * «Agregar otro proveedor». Another provider is only its credential in Hermes: Iris
 * offers its models per conversation, and the main provider does not change. The
 * Gateway starts once the main provider is ready; messaging is optional.
 */
(function () {
    const AUTH_LABELS = Object.freeze({
        api: 'API key', 'api+oauth': 'API key', 'api-optional': 'API key opcional', oauth: 'OAuth',
        none: 'Local, sin clave', aws: 'Credenciales de AWS', external: 'CLI externa', entra: 'Microsoft Entra',
    });

    const EMPTY = Object.freeze({ known: false, primary: null, additional: Object.freeze([]), addable: Object.freeze([]) });

    const text = (value, limit = 160) => (typeof value === 'string' ? value.trim().slice(0, limit) : '');

    // /api/llm-setup → a frozen state; anything malformed reads as "unknown".
    function normalize(payload) {
        if (!payload || typeof payload !== 'object') return EMPTY;
        const raw = payload.primary;
        const primary = raw && typeof raw === 'object' && text(raw.provider)
            ? Object.freeze({
                provider: text(raw.provider, 80),
                name: text(raw.name, 120) || text(raw.provider, 80),
                model: text(raw.model, 200),
                auth: text(raw.auth, 20) || 'api',
                ready: raw.ready === true ? true : raw.ready === false ? false : null,
                code: text(raw.code, 60),
                message: text(raw.message, 400),
            })
            : null;
        const additional = (Array.isArray(payload.additional) ? payload.additional : [])
            .filter(row => row && typeof row === 'object' && text(row.provider))
            .map(row => Object.freeze({
                provider: text(row.provider, 80),
                name: text(row.name, 120) || text(row.provider, 80),
                auth: row.auth === 'oauth' ? 'oauth' : 'api',
            }));
        const addable = (Array.isArray(payload.addable) ? payload.addable : [])
            .filter(id => typeof id === 'string' && id);
        return Object.freeze({ known: true, primary, additional: Object.freeze(additional), addable: Object.freeze(addable) });
    }

    // What the section shows. `requested` is the user's last choice: '' (follow the state),
    // 'change' (edit the main provider) or 'add' (one more provider).
    function view(state, requested = '') {
        if (requested === 'add' && state.primary) return 'add';
        if (requested === 'change') return 'change';
        return state.primary && state.primary.ready === true ? 'summary' : 'change';
    }

    // Only the form in 'change' view may become the main provider (the final save
    // included): the summary keeps the saved one and 'add' never replaces it.
    function formOwnsProvider(state, requested = '') {
        return !state.known || view(state, requested) === 'change';
    }

    // The Gateway refuses to start without a ready main provider: say so before trying.
    function gatewayGate(state, engine = 'ok') {
        if (engine === 'missing') return { allowed: false, reason: 'Primero prepara el motor de IA (arriba).' };
        if (engine === 'damaged') return { allowed: false, reason: 'El motor necesita reparación antes de iniciarse.' };
        if (!state.known) return { allowed: true, reason: '' };  // cannot tell: the Gateway decides, as before
        if (!state.primary) return { allowed: false, reason: 'Primero elige y guarda tu proveedor de IA.' };
        if (state.primary.ready === false) {
            return { allowed: false, reason: state.primary.message || 'Tu proveedor de IA todavía no está listo: complétalo arriba.' };
        }
        return { allowed: true, reason: '' };
    }

    function authLabel(auth) {
        return AUTH_LABELS[auth] || AUTH_LABELS.api;
    }

    // The provider cards the editor lists: every one to change the main provider,
    // only the addable ones (never the main provider) to add another.
    function listedProviders(ids, state, requested = '') {
        if (view(state, requested) !== 'add') return ids.slice();
        const addable = new Set(state.addable);
        return ids.filter(id => addable.has(id));
    }

    globalThis.IrisLlmSetup = Object.freeze({
        EMPTY, normalize, view, formOwnsProvider, gatewayGate, authLabel, listedProviders,
    });
})();
