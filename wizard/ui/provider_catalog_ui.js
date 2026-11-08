/* Lists belong to a provider, access method, key and endpoint. No persistent cache. */
class IrisProviderModels {
    constructor(read, changed = () => {}) {
        this.read = read; this.changed = changed; this.generation = 0;
        this.context = ''; this.state = 'idle'; this.result = null;
    }
    reset(context = '') {
        this.controller?.abort(); this.controller = null; this.generation++;
        this.context = context; this.state = 'idle'; this.result = null; this.changed();
    }
    async load(context, body) {
        this.reset(context);
        const generation = this.generation;
        const controller = this.controller = new AbortController();
        this.state = 'loading'; this.changed();
        try {
            const result = await this.read(body, controller.signal);
            if (generation !== this.generation) return;
            this.result = result; this.state = result.status === 'ok' ? 'ready' : 'error';
        } catch {
            if (generation !== this.generation) return;
            this.result = { message: 'No se pudo cargar el catálogo. Revisa la conexión y reintenta.' };
            this.state = 'error';
        } finally {
            if (generation === this.generation) { this.controller = null; this.changed(); }
        }
    }
}
globalThis.IrisProviderModels = IrisProviderModels;
let _providerModelAccess;
function _providerModelsController() {
    return _providerModelAccess ||= new IrisProviderModels(async (body, signal) => {
        const response = await fetch('/api/provider-models', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body), signal, cache: 'no-store',
        });
        const result = await response.json();
        return response.ok ? result : { ...result, status: 'error' };
    }, _renderProviderModelsAccess);
}
function _providerModelsDraft() {
    const provider = selectedLLMProvider();
    const use_oauth = Boolean(document.getElementById('use-oauth')?.checked);
    return { provider, use_oauth,
        api_key: use_oauth ? '' : (document.getElementById('llm-api-key')?.value.trim() || ''),
        base_url: use_oauth ? '' : (document.getElementById('llm-base-url')?.value.trim() || ''),
    };
}
function _providerModelsRequired() {
    return IrisLlmSetup.view(llmSetup, llmView) !== 'add'
        && ['api', 'api+oauth', 'oauth'].includes(LLM_PROVIDERS[selectedLLMProvider()]?.auth);
}
function _providerModelsFor(provider, oauth) {
    const access = _providerModelAccess;
    return access?.state === 'ready' && access.context === JSON.stringify(_providerModelsDraft())
        && access.result.provider === provider && access.result.use_oauth === oauth ? access.result : null;
}
function _renderProviderModelsAccess() {
    const required = _providerModelsRequired();
    const access = _providerModelAccess;
    const matches = access?.context === JSON.stringify(_providerModelsDraft());
    if (access?.context && !matches) { access.reset(); return; }
    const ready = matches && access.state === 'ready';
    const panel = document.getElementById('llm-model-access');
    if (!panel) return;
    panel.hidden = !required;
    if (!required) return;
    const loading = matches && access.state === 'loading';
    const select = document.getElementById('llm-model');
    const section = document.getElementById('llm-model-section');
    if (section) section.style.display = ready ? 'block' : 'none';
    if (!ready) {
        if (select) { select.value = ''; select.disabled = true; }
        const custom = document.getElementById('custom-model-section');
        if (custom) custom.style.display = 'none';
    }
    const button = document.getElementById('btn-load-provider-models');
    if (button) {
        button.disabled = loading;
        button.textContent = loading ? 'Cargando modelos…' : ready ? 'Actualizar modelos' : 'Cargar modelos';
    }
    const status = document.getElementById('llm-model-access-status');
    const openCode = ['opencode-zen', 'opencode-go'].includes(selectedLLMProvider());
    if (status) status.textContent = loading ? 'Consultando los modelos de este acceso…'
        : ready ? access.result.source === 'public'
            ? openCode
                ? `${access.result.models.length} modelos del catálogo público. Los gratuitos son solo para la aplicación OpenCode. En Iris elige un modelo de pago de Zen o incluido en Go. La lista no verifica la clave ni la cuota.`
                : `${access.result.models.length} modelos del catálogo público. La lista no verifica la clave ni garantiza acceso a las promociones gratuitas.`
            : access.result.source === 'reference'
                ? 'El proveedor no devolvió una lista en vivo. Modelos de referencia de Hermes; acceso y cuota sin verificar.'
                : access.result.source === 'hermes'
                    ? `${access.result.models.length} modelos del catálogo de Hermes para este método de acceso. Su disponibilidad depende del proveedor.`
                : `${access.result.models.length} modelos para este acceso. Elige uno; la cuota y disponibilidad dependen del proveedor.`
        : matches && access.state === 'error' ? access.result?.message || 'No se pudo cargar el catálogo. Reintenta.'
            : _providerModelsDraft().use_oauth ? 'Primero conecta tu cuenta. Después Iris cargará sus modelos.'
                : 'Pega tu API key y pulsa «Cargar modelos». No se guarda ni se genera una respuesta al consultar la lista.';
}
function invalidateProviderModels() {
    _providerModelsController().reset();
    if (typeof markModelStepDone === 'function') markModelStepDone();
}
async function loadProviderModels() {
    const draft = _providerModelsDraft();
    await _providerModelsController().load(JSON.stringify(draft), draft);
    if (!_providerModelsFor(draft.provider, draft.use_oauth)) return;
    _setProviderModelOptions(draft.provider, draft.use_oauth, true);
    _renderProviderModelsAccess();
    markModelStepDone();
}
