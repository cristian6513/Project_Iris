// Provider step (2026-10-13): the main provider folds into a summary once saved and ready,
// «Agregar otro proveedor» only stores another credential, messaging is optional and the
// Gateway comes after both, waiting for a ready provider. Pure logic from llm_setup.js and
// the real startGateway() in a VM; every request is intercepted.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

// wizard.js icon helpers (setIconText, uiIcon, iconLabelHtml): text-only stand-ins for the VM.
const iconStubs = {
    setIconText: (el, label) => { if (el) el.textContent = String(label ?? ''); },
    uiIcon: (token) => `<wz-icon>${token}</wz-icon>`,
    iconLabelHtml: (label) => String(label ?? ''),
};

const setupSource = readFileSync(new URL('../../wizard/ui/llm_setup.js', import.meta.url), 'utf8');
const wizardSource = readFileSync(new URL('../../wizard/ui/wizard.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../../wizard/ui/index.html', import.meta.url), 'utf8');
const context = vm.createContext({ ...iconStubs,});
vm.runInContext(setupSource, context);
const S = context.IrisLlmSetup;

const PRIMARY = { provider: 'anthropic', name: 'Anthropic (Claude)', model: 'claude-x', auth: 'api', ready: true };
const state = (primary, extra = {}) => S.normalize({ primary, additional: [], addable: ['openai', 'google'], ...extra });

test('a malformed answer reads as unknown and the Gateway decides, as before', () => {
    for (const payload of [null, 'x', 7]) {
        const unknown = S.normalize(payload);
        assert.equal(unknown.known, false);
        assert.deepEqual({ ...S.gatewayGate(unknown) }, { allowed: true, reason: '' });  // objects of the VM realm
        assert.equal(S.formOwnsProvider(unknown), true);
    }
    const partial = S.normalize({ primary: { provider: '' }, additional: [{}, { provider: 'openai', auth: 'weird' }] });
    assert.equal(partial.primary, null);
    assert.deepEqual(JSON.parse(JSON.stringify(partial.additional)), [{ provider: 'openai', name: 'openai', auth: 'api' }]);
});

test('the section folds only for a saved provider that passes the pre-flight', () => {
    assert.equal(S.view(state(null)), 'change');
    assert.equal(S.view(state(PRIMARY)), 'summary');
    assert.equal(S.view(state({ ...PRIMARY, ready: false })), 'change');
    assert.equal(S.view(state({ ...PRIMARY, ready: null })), 'change');
    assert.equal(S.view(state(PRIMARY), 'change'), 'change');
    assert.equal(S.view(state(PRIMARY), 'add'), 'add');
    assert.equal(S.view(state(null), 'add'), 'change');  // nothing to add next to
});

test('only the form in «Cambiar proveedor» may replace the main provider', () => {
    assert.equal(S.formOwnsProvider(state(null)), true);         // first run: the form is the choice
    assert.equal(S.formOwnsProvider(state(PRIMARY)), false);     // folded: keep the saved one
    assert.equal(S.formOwnsProvider(state(PRIMARY), 'add'), false);  // the form holds another provider
    assert.equal(S.formOwnsProvider(state(PRIMARY), 'change'), true);
});

test('the Gateway waits for a ready provider and for the engine', () => {
    assert.equal(S.gatewayGate(state(null)).allowed, false);
    assert.match(S.gatewayGate(state(null)).reason, /proveedor de IA/);
    const notReady = S.gatewayGate(state({ ...PRIMARY, ready: false, message: 'Falta la API key.' }));
    assert.deepEqual({ ...notReady }, { allowed: false, reason: 'Falta la API key.' });
    assert.equal(S.gatewayGate(state(PRIMARY)).allowed, true);
    assert.equal(S.gatewayGate(state({ ...PRIMARY, ready: null })).allowed, true);  // cannot tell
    assert.equal(S.gatewayGate(state(PRIMARY), 'missing').allowed, false);
    assert.equal(S.gatewayGate(state(PRIMARY), 'damaged').allowed, false);
});

test('adding lists only the providers that can sit next to the main one', () => {
    const ids = ['anthropic', 'openai', 'google', 'custom'];
    assert.deepEqual([...S.listedProviders(ids, state(PRIMARY), 'add')], ['openai', 'google']);
    assert.deepEqual([...S.listedProviders(ids, state(PRIMARY), 'change')], ids);
    assert.deepEqual([...S.listedProviders(ids, state(PRIMARY))], ids);
});

test('the step order: provider, optional messaging, then the Gateway, then the check', () => {
    const provider = html.indexOf('id="llm-section"');
    const messaging = html.indexOf('id="messaging-panel"');
    const gateway = html.indexOf('id="gateway-section"');
    const health = html.indexOf('id="btn-run-healthcheck"');
    assert.ok(provider > 0 && provider < messaging && messaging < gateway && gateway < health);
    const button = html.indexOf('id="btn-start-gateway"');
    assert.ok(button > gateway && button < health, 'the start button lives in the Gateway section');
    assert.match(html, /Canales de mensajería <span class="optional-pill">Opcional<\/span>/);
    assert.match(html, /onclick="changeLlmProvider\(\)">Cambiar proveedor</);
    assert.match(html, /onclick="addLlmProvider\(\)">＋ Agregar otro proveedor</);
    assert.ok(html.indexOf('llm_setup.js') < html.indexOf('/ui/wizard.js'), 'llm_setup.js loads first');
});

// ── The real startGateway() ────────────────────────────────────────────────

function between(start, end) {
    const a = wizardSource.indexOf(start);
    const b = wizardSource.indexOf(end, a + start.length);
    assert.ok(a >= 0 && b > a, `Missing function boundary: ${start}`);
    return wizardSource.slice(a, b);
}

function gateway({ primary, engine = 'stopped' }) {
    const requests = [];
    const prompts = [];
    const persisted = [];
    const nodes = new Map();
    const node = (id) => {
        if (!nodes.has(id)) nodes.set(id, { style: {}, dataset: {}, innerHTML: '', textContent: '', disabled: false,
            appendChild() {}, scrollIntoView() {}, classList: { add() {}, remove() {} } });
        return nodes.get(id);
    };
    const ctx = vm.createContext({ ...iconStubs,
        IrisLlmSetup: S, wizardState: { hermes_mode: 'full' }, console,
        document: { getElementById: node, querySelector: () => null, querySelectorAll: () => [] },
        escapeHtml: String, setTimeout: () => 0,
        fetch: async (url, options = {}) => {
            requests.push(`${options.method || 'GET'} ${url}`);
            return { ok: true, json: async () => ({ status: 'ok', message: 'listo' }) };
        },
        _ensureEngineConsent: async () => { prompts.push(1); return true; },
        _engineConsentRefused() {}, _setSetupStep() {}, markModelStepDone() {}, checkHermesOld() {},
        _persistLLMSelectionBeforeGateway: async () => { persisted.push(1); return {}; },
        _hydrateSavedConfigIntoDOM: async () => {}, renderProviderList() {},
        _setLlmStatus() {}, _attentionTo() {},
    });
    vm.runInContext(between('let llmSetup = IrisLlmSetup.EMPTY;', 'function _setLlmStatus('), ctx);
    vm.runInContext(between('// The Gateway section: blocked until', 'async function _persistLLMSelectionBeforeGateway()'), ctx);
    vm.runInContext(between('async function startGateway()', '// ── Step 3: Health Check end-to-end'), ctx);
    vm.runInContext(`llmSetup = IrisLlmSetup.normalize(${JSON.stringify({ primary, additional: [], addable: [] })});
        gatewayEngine = ${JSON.stringify(engine)};`, ctx);
    ctx.loadLlmSetup = async () => {};
    return { ctx, requests, prompts, persisted, node };
}

test('without a ready provider the button asks nothing and starts nothing', async () => {
    for (const primary of [null, { ...PRIMARY, ready: false, message: 'Falta la API key.' }]) {
        const env = gateway({ primary });
        await env.ctx.startGateway();
        assert.deepEqual(env.requests, []);
        assert.equal(env.prompts.length, 0);
        assert.equal(env.node('btn-start-gateway').disabled, true);
        assert.equal(env.node('gateway-section').dataset.state, 'blocked');
    }
});

test('with a ready provider it starts once, then restarts, and never rewrites the provider', async () => {
    const env = gateway({ primary: PRIMARY });
    await env.ctx.startGateway();
    assert.deepEqual(env.requests, ['POST /api/start-gateway']);
    assert.equal(vm.runInContext('gatewayEngine', env.ctx), 'running');
    assert.equal(env.node('btn-start-gateway').textContent, '🔁 Reiniciar IRIS');
    await env.ctx.startGateway();
    assert.deepEqual(env.requests, ['POST /api/start-gateway', 'POST /api/restart-gateway']);
    assert.equal(env.persisted.length, 0);
    assert.equal(env.prompts.length, 2);  // the engine notice guards both
});

test('a Gateway that was already running counts as started', async () => {
    const env = gateway({ primary: PRIMARY });
    env.ctx.fetch = async (url) => {
        env.requests.push(url);
        return { ok: true, json: async () => ({ status: 'running', message: 'ya estaba activo' }) };
    };
    await env.ctx.startGateway();
    assert.equal(vm.runInContext('gatewayEngine', env.ctx), 'running');
});

// ── Codex's review of these changes (2026-10-13, artifacts/qa11-wizard-oct13-review) ──

function nodes() {
    const map = new Map();
    const node = (id) => {
        if (!map.has(id)) map.set(id, { id, style: {}, dataset: {}, innerHTML: '', textContent: '', disabled: false,
            title: '', checked: false, value: '', appendChild() {}, scrollIntoView() {},
            classList: { add() {}, remove() {}, toggle() {} } });
        return map.get(id);
    };
    return node;
}

// R01: «Verificar conexión» saves the form only when the form owns the main provider.
function healthCheck({ mode, view, primary }) {
    const requests = [];
    const sources = [];
    const node = nodes();
    const ctx = vm.createContext({ ...iconStubs,
        IrisLlmSetup: S, console, escapeHtml: String, HEALTHCHECK_ROWS: [], _healthcheckStream: null,
        wizardState: { hermes_mode: mode, llm_provider: 'openrouter', llm_model: 'saved/model' },
        document: { getElementById: node, querySelector: () => null, querySelectorAll: () => [] },
        fetch: async (url, options = {}) => {
            requests.push(`${options.method || 'GET'} ${url}`);
            return { ok: true, json: async () => ({ status: 'saved', provider: 'deepseek', model: 'deepseek-chat' }) };
        },
        EventSource: class { constructor(url) { sources.push(url); } close() {} },
        selectedLLMProvider: () => 'deepseek', selectedLLMModel: () => 'deepseek-chat',
        maybeImportHermesConfig: async () => { requests.push('import-hermes-config'); },
        _renderHealthcheckSkeleton() {}, _updateHealthcheckRow() {},
    });
    vm.runInContext(between('let llmSetup = IrisLlmSetup.EMPTY;', 'function _setLlmStatus('), ctx);
    vm.runInContext(between('async function _persistLLMSelectionBeforeGateway()', '/**\n * Start the Gateway, or restart'), ctx);
    vm.runInContext(between('async function runHealthCheck()', '// ── Step 3: TTS Testing'), ctx);
    vm.runInContext(`llmSetup = IrisLlmSetup.normalize(${JSON.stringify({ primary, additional: [], addable: ['deepseek'] })});
        llmView = ${JSON.stringify(view === 'add' || view === 'change' ? view : '')};`, ctx);
    ctx.loadLlmSetup = async () => {};
    return { ctx, requests, sources };
}

for (const mode of ['full', 'quick', 'repair']) {
    for (const view of ['add', 'summary', 'change', 'first-run']) {
        test(`verifying in ${mode} mode with the provider section in ${view} view`, async () => {
            const primary = view === 'first-run' ? null : { provider: 'openrouter', name: 'OpenRouter', model: 'saved/model', auth: 'api', ready: true };
            const env = healthCheck({ mode, view, primary });
            await env.ctx.runHealthCheck();
            const saves = env.requests.filter((r) => r === 'POST /api/save-llm-selection').length;
            const owns = mode === 'full' && (view === 'change' || view === 'first-run');
            assert.equal(saves, owns ? 1 : 0);
            assert.equal(env.ctx.wizardState.llm_provider, owns ? 'deepseek' : 'openrouter');  // the main provider stays
            assert.deepEqual(env.sources, ['/api/health-check-stream']);  // the check itself always runs
        });
    }
}

// R02: the messaging section's restart obeys the Gateway gate before asking anything.
function messagingPanel(blocked) {
    const requests = [];
    const prompts = [];
    const status = { textContent: '', classList: { toggle() {} } };
    const ctx = vm.createContext({ ...iconStubs,
        console, AbortSignal: { timeout: () => undefined },
        document: { createElement: () => ({ style: {}, dataset: {}, append() {} }) },
        fetch: async (url, options = {}) => {
            requests.push(`${options.method || 'GET'} ${url}`);
            return { ok: true, json: async () => ({ restart_started: true }) };
        },
    });
    ctx.globalThis = ctx;
    vm.runInContext(readFileSync(new URL('../../wizard/ui/messaging.js', import.meta.url), 'utf8'), ctx);
    ctx.irisGatewayBlockedReason = () => blocked;
    ctx.irisEnsureEngineConsent = async () => { prompts.push(1); return true; };
    const panel = new ctx.IrisMessagingPanel({ querySelector: () => status });
    panel.restartButton = { disabled: false, title: '' };
    return { panel, requests, prompts, status };
}

test('the messaging restart is refused before a ready provider, without asking or sending', async () => {
    const env = messagingPanel('Primero elige y guarda tu proveedor de IA.');
    env.panel.syncRestartGate();
    assert.equal(env.panel.restartButton.disabled, true);
    assert.equal(env.panel.restartButton.title, 'Primero elige y guarda tu proveedor de IA.');
    assert.equal(await env.panel.restart(), false);
    assert.deepEqual(env.requests, []);
    assert.equal(env.prompts.length, 0);
    assert.equal(env.status.textContent, 'Primero elige y guarda tu proveedor de IA.');
});

test('with a ready provider the messaging restart asks the notice and restarts once', async () => {
    const env = messagingPanel('');
    env.panel.syncRestartGate();
    assert.equal(env.panel.restartButton.disabled, false);
    assert.equal(await env.panel.restart(), true);
    assert.equal(env.prompts.length, 1);
    assert.deepEqual(env.requests, ['POST /api/messaging/restart']);
});

// R05: a late provider-step read never re-enables the button while Quick/Repair start.
test('the saved-configuration start keeps the button busy until its request settles', async () => {
    const requests = [];
    let release;
    const node = nodes();
    const ctx = vm.createContext({ ...iconStubs,
        IrisLlmSetup: S, console, escapeHtml: String, wizardState: { hermes_mode: 'quick' },
        wizardRuntime: { known: true, packaged: true },
        document: { getElementById: node, querySelector: () => null, querySelectorAll: () => [], createElement: () => ({}) },
        fetch: async (url, options = {}) => {
            requests.push(`${options.method || 'GET'} ${url}`);
            if (url === '/api/current-config') return { ok: true, json: async () => ({}) };
            await new Promise((resolve) => { release = resolve; });
            return { ok: true, json: async () => ({ status: 'ok', message: 'listo' }) };
        },
        setTimeout: () => 0,
        _ensureEngineConsent: async () => true, _engineConsentRefused() {}, _engineConsentBlockedReason: () => '',
        _setSetupStep() {}, markModelStepDone() {}, checkHermesOld() {},
    });
    vm.runInContext(between('let llmSetup = IrisLlmSetup.EMPTY;', 'function _setLlmStatus('), ctx);
    vm.runInContext(between('// The Gateway section: blocked until', 'async function _persistLLMSelectionBeforeGateway()'), ctx);
    vm.runInContext(between('async function _startGatewayUsingSavedConfig(', 'async function _originalCheckHermes('), ctx);
    vm.runInContext(`gatewayEngine = 'stopped';`, ctx);
    const pending = ctx._startGatewayUsingSavedConfig({ restart: false });
    while (!release) await new Promise((resolve) => setImmediate(resolve));
    // The provider-step read started on entry now finishes: a ready main provider.
    vm.runInContext(`llmSetup = IrisLlmSetup.normalize(${JSON.stringify({ primary: PRIMARY })}); _renderGatewayGate();`, ctx);
    const button = node('btn-start-gateway');
    assert.equal(button.disabled, true, 'still busy: a second click cannot send another start');
    assert.equal(button.dataset.busy, '1');
    release();
    await pending;
    assert.equal(button.dataset.busy, undefined);
    assert.equal(button.disabled, false);
    assert.equal(button.textContent, '🔁 Reiniciar IRIS');
    assert.deepEqual(requests.filter((r) => r.startsWith('POST')), ['POST /api/start-gateway']);
});

// Codex (2026-10-13): the mode card kept the stale accessible name «Modo Completo BETA» after the
// packaged texts and the mode plan rewrote its title. Cards are named by their live titles.
test('mode cards and their radios are named by the live visible titles', () => {
    for (const key of ['full', 'skip', 'repair']) {
        assert.match(html, new RegExp(`value="${key}" aria-labelledby="mode-${key}-title" aria-describedby="mode-${key}-desc"`));
        assert.match(html, new RegExp(`class="mode-card-title" id="mode-${key}-title"`));
    }
    assert.match(wizardSource, /card\.setAttribute\('aria-labelledby', cardTitle\.id\)/);
    assert.doesNotMatch(wizardSource, /card\.setAttribute\('aria-label', cardTitle/);
});
