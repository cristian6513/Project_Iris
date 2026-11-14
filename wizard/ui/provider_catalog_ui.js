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
        if (generation === this.generation && this.state === 'ready') return this.result;
    }
}
globalThis.IrisProviderModels = IrisProviderModels;
let _providerModelAccess;
let _providerModelPickerOpen = false;
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
    const recommendation = ready && access.result.recommended_model;
    const showRecommendation = Boolean(recommendation && !_providerModelPickerOpen && select?.value === recommendation);
    const recommended = document.getElementById('llm-recommended-model');
    if (recommended) recommended.hidden = !showRecommendation;
    const name = document.getElementById('llm-recommended-model-name');
    if (name) name.textContent = showRecommendation ? recommendation : '';
    if (section) section.style.display = ready && !showRecommendation ? 'block' : 'none';
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
                    ? `${access.result.models.length} modelos del catálogo de Hermes. El proveedor confirma el acceso al responder.`
                : `${access.result.models.length} modelos para esta cuenta. La cuota depende de tu plan.`
        : matches && access.state === 'error' ? access.result?.message || 'No se pudo cargar el catálogo. Reintenta.'
            : _providerModelsDraft().use_oauth ? 'Primero conecta tu cuenta. Después Iris cargará sus modelos.'
                : 'Pega tu API key y pulsa «Cargar modelos». No se guarda ni se genera una respuesta al consultar la lista.';
}
function invalidateProviderModels() {
    _providerModelPickerOpen = false;
    _providerModelsController().reset();
    if (typeof markModelStepDone === 'function') markModelStepDone();
}
async function loadProviderModels() {
    if (!_providerModelsRequired()) return;
    const draft = _providerModelsDraft();
    const context = JSON.stringify(draft);
    const controller = _providerModelsController();
    const select = document.getElementById('llm-model');
    const previous = controller.context === context ? select?.value : '';
    const previousCustom = previous === '__custom__' ? document.getElementById('llm-custom-model')?.value : '';
    const saved = llmSetup.primary;
    const savedModel = saved?.provider === draft.provider && (saved.auth === 'oauth') === draft.use_oauth
        ? saved.model : '';
    const result = await controller.load(context, draft);
    if (!result || _providerModelsFor(draft.provider, draft.use_oauth) !== result) return;
    const filter = document.getElementById('llm-model-filter');
    if (filter) filter.value = '';
    const modelSelect = _setProviderModelOptions(draft.provider, draft.use_oauth, false);
    const preferred = previous || savedModel || (draft.use_oauth && result.recommended_model);
    if (modelSelect && [...modelSelect.options].some(option => option.value === preferred && !option.disabled)) {
        modelSelect.value = preferred;
        const input = document.getElementById('llm-custom-model');
        if (input && previousCustom) input.value = previousCustom;
    } else if (preferred && (previous || savedModel) && draft.provider !== 'nous' && [...(modelSelect?.options || [])]
        .some(option => option.value === '__custom__')) {
        modelSelect.value = '__custom__';
        const input = document.getElementById('llm-custom-model');
        if (input) input.value = preferred;
    }
    const custom = document.getElementById('custom-model-section');
    if (custom && modelSelect?.value === '__custom__') custom.style.display = 'block';
    _renderProviderModelsAccess();
    markModelStepDone();
}

function showProviderModelPicker() {
    _providerModelPickerOpen = true;
    _renderProviderModelsAccess();
    document.getElementById('llm-model')?.focus();
}
