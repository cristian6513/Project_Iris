// A08.6: every Wizard path that starts or restarts the bundled engine asks for
// Hermes' security notice first. Real wizard.js/messaging.js functions run in a
// VM with in-memory doubles; every request is intercepted (no Gateway, network
// or profile). Based on the closure-check reproduction.
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

const source = readFileSync(new URL('../../wizard/ui/wizard.js', import.meta.url), 'utf8');
const runtimeSource = readFileSync(new URL('../../wizard/ui/wizard_runtime.js', import.meta.url), 'utf8');
const messagingSource = readFileSync(new URL('../../wizard/ui/messaging.js', import.meta.url), 'utf8');

function between(start, end) {
    const a = source.indexOf(start);
    const b = source.indexOf(end, a + start.length);
    assert.ok(a >= 0 && b > a, `Missing function boundary: ${start}`);
    return source.slice(a, b);
}

const functions = [
    between('function _runtimeText(', 'function _installerTool()'),
    between('async function loadWizardRuntime()', '// Inside the Desktop shell'),
    between("const _OC_ACK_KEY = 'IRIS.oc.security_acked';", '// ── OS / dependency precheck'),
    between('function applyHermesModeOnEntry()', '/**\n * Run the same /api/hermes-status'),
    between('function confirmHermesMode()', '/**\n * Fire the appropriate side-effect'),
    between('async function _triggerHermesModeAction(', '/**\n * Reads /api/current-config'),
    between('async function _startGatewayUsingSavedConfig(', 'async function _originalCheckHermes('),
    between('async function startGateway()', '// ── Step 3: Health Check end-to-end'),
    between('async function launchIris()', 'async function stopIris()'),
].join('\n');

const ENGINE = ['/api/start-gateway', '/api/restart-gateway', '/api/iris/launch', '/api/messaging/restart'];

const PACKAGED_INFO = { mode: 'packaged', capabilities: { external_hermes_install: false, external_autostart: false } };
const DEVELOPMENT_INFO = { mode: 'development', capabilities: { external_hermes_install: true, external_autostart: true } };

// What /api/runtime-info answers on each call: a failure kind or a payload.
function runtimeReply(reply) {
    if (reply === 'network') throw new TypeError('Failed to fetch');
    if (reply === 'http') return { ok: false, status: 503, json: async () => ({}) };
    if (reply === 'invalid') return { ok: true, json: async () => { throw new SyntaxError('Unexpected token'); } };
    if (reply === 'shape') return { ok: true, json: async () => ({ capabilities: {} }) };
    return { ok: true, json: async () => reply };
}

function setup({ packaged = true, runtime = null, runtimeReplies = [], profileConsent = false, decision = null, saveFails = false, mode = 'quick' } = {}) {
    const nodes = new Map();
    const node = id => {
        if (!nodes.has(id)) nodes.set(id, {
            style: {}, innerHTML: '', textContent: '', disabled: false, checked: false, dataset: {},
            classList: { add() {}, remove() {}, toggle() {} }, focus() {}, appendChild() {},
        });
        return nodes.get(id);
    };
    const requests = [];
    const timers = [];
    const prompts = [];
    const counters = { persisted: 0, warnings: [] };
    const win = { location: { href: '' } };
    win.parent = win;
    const ctx = vm.createContext({ ...iconStubs,
        wizardState: { hermes_install_status: 'installed', hermes_mode: mode },
        selectedHermesMode: mode, currentStep: 2, _lastConfigSaveOk: true, _lastConfigSaveError: "", _lastAiReadiness: null,
        _configSaveGeneration: 0,  // launchIris reads it (wizard.js declares it next to _lastConfigSaveOk)
        localStorage: { getItem: () => null, setItem() {} },
        document: { getElementById: node, querySelector: () => null, querySelectorAll: () => [], body: { dataset: {} }, title: 'IRIS', referrer: '' },
        window: win, location: { search: '', origin: 'http://127.0.0.1:8765', href: 'http://127.0.0.1:8765/ui/' },
        URL, URLSearchParams, globalThis: null,
        escapeHtml: value => String(value), console,
        maybeImportHermesConfig() {},
        _hydrateSavedConfigIntoDOM: async () => {},
        _persistLLMSelectionBeforeGateway: async () => {
            counters.persisted++;
            return { provider: 'openrouter', model: 'fixture/model', auth: 'api' };
        },
        _appendInstallLog() {},
        _setSetupStep() {}, markModelStepDone() {}, checkHermesOld() {},
        // The provider step (llm_setup.js) saved a ready provider: the Gateway may start.
        llmSetup: { known: true, primary: { name: 'OpenRouter', model: 'fixture/model', auth: 'api', ready: true } },
        IrisLlmSetup: { authLabel: () => 'API key' }, gatewayEngine: 'stopped', llmView: '',
        _gatewayBlockedReason: () => '', _gatewayLog: () => null, _setGatewayEngine() {}, _renderGatewayGate() {},
        loadLlmSetup: async () => {}, _setLlmStatus() {}, _attentionTo() {},
        _showConfigWarnings: warnings => counters.warnings.push(...warnings),
        commitInstallerTheme: async () => true,
        waitForIrisPort: async () => true, refreshIrisStatus: async () => {}, alert() {},
        irisTell: async () => {}, irisAsk: async () => true,  // IRIS OS dialogs (wizard.js helpers)
        fetch: async (url, options = {}) => {
            requests.push({ url, method: options.method || 'GET' });
            if (url === '/api/runtime-info') return runtimeReply(runtimeReplies.shift() ?? 'network');
            if (url === '/api/hermes-security-ack') {
                return { ok: !saveFails, json: async () => ({ status: saveFails ? 'error' : 'saved' }) };
            }
            return { ok: true, json: async () => url === '/api/current-config'
                ? { llm_provider: 'openrouter', llm_model: 'fixture/model', has_llm_api_key: true }
                : { status: 'error', code: 'intercepted', message: 'No real engine request', port: 8765 } };
        },
        setTimeout: (callback, delay) => { timers.push({ callback, delay }); return timers.length; },
        AbortSignal: { timeout: () => undefined },
    });
    ctx.globalThis = ctx;
    vm.runInContext(runtimeSource, ctx);
    // `runtime: 'unknown'` is the state left by a failed first /api/runtime-info.
    const initial = runtime === 'unknown' ? null : runtime || (packaged ? PACKAGED_INFO : DEVELOPMENT_INFO);
    vm.runInContext(`let wizardRuntime = IrisWizardRuntime.profile(${JSON.stringify(initial)});`, ctx);
    vm.runInContext(functions, ctx);
    vm.runInContext(messagingSource, ctx);
    ctx._engineConsent = profileConsent;
    vm.runInContext(`_engineConsent = ${profileConsent};`, ctx);
    ctx._promptEngineConsent = async () => { prompts.push(1); return decision === true; };
    vm.runInContext('_promptEngineConsent = globalThis._promptEngineConsent;', ctx);
    ctx.updateUI = () => ctx.applyHermesModeOnEntry();
    return { ctx, requests, prompts, counters, timers };
}

async function run(env, action) {
    if (action === 'quick' || action === 'repair') {
        env.ctx.confirmHermesMode();
        const timer = env.timers.find(item => item.delay === 100);
        assert.ok(timer, 'confirmHermesMode must enqueue the normal mode action');
        await timer.callback();
        await new Promise(resolve => setImmediate(resolve));
        await new Promise(resolve => setImmediate(resolve));
    } else if (action === 'messaging') {
        const panel = new env.ctx.IrisMessagingPanel({ querySelector: () => ({ textContent: '', classList: { toggle() {} } }) });
        await panel.restart();
    } else {
        await env.ctx[action]();
    }
    return {
        engine: env.requests.filter(r => ENGINE.includes(r.url)),
        consentSaves: env.requests.filter(r => r.url === '/api/hermes-security-ack').length,
    };
}

const ENTRIES = {
    // The provider is saved by its own section: starting the Gateway never rewrites it.
    manual: { action: 'startGateway', mode: 'full', url: '/api/start-gateway', persists: 0 },
    quick: { action: 'quick', mode: 'quick', url: '/api/start-gateway', persists: 0 },
    repair: { action: 'repair', mode: 'repair', url: '/api/restart-gateway', persists: 0 },
    launch: { action: 'launchIris', mode: 'full', url: '/api/iris/launch', persists: 0 },
    messaging: { action: 'messaging', mode: 'full', url: '/api/messaging/restart', persists: 0 },
};

for (const [name, entry] of Object.entries(ENTRIES)) {
    test(`packaged ${name}: declining the notice sends and persists nothing`, async () => {
        const env = setup({ mode: entry.mode, decision: false });
        const result = await run(env, entry.action);
        assert.equal(env.prompts.length, 1);
        assert.deepEqual(result.engine, []);
        assert.equal(result.consentSaves, 0);
        assert.equal(env.counters.persisted, 0);
    });

    test(`packaged ${name}: accepting saves consent once and makes exactly one engine request`, async () => {
        const env = setup({ mode: entry.mode, decision: true });
        const result = await run(env, entry.action);
        assert.equal(result.consentSaves, 1);
        assert.deepEqual(result.engine.map(r => r.url), [entry.url]);
        // Saved-configuration flows keep the stored selection and credentials.
        assert.equal(env.counters.persisted, entry.persists);
    });

    test(`packaged ${name}: consent already in the profile asks nothing`, async () => {
        const env = setup({ mode: entry.mode, profileConsent: true });
        const result = await run(env, entry.action);
        assert.equal(env.prompts.length, 0);
        assert.equal(result.consentSaves, 0);
        assert.deepEqual(result.engine.map(r => r.url), [entry.url]);
    });

    test(`development ${name}: unchanged, no consent prompt`, async () => {
        const env = setup({ packaged: false, mode: entry.mode, decision: false });
        const result = await run(env, entry.action);
        assert.equal(env.prompts.length, 0);
        assert.equal(result.consentSaves, 0);
        assert.deepEqual(result.engine.map(r => r.url), [entry.url]);
    });
}

// The mode is unknown when the first /api/runtime-info failed. It is asked once
// more; while it stays unknown nothing is prompted, saved, persisted or started.
const RUNTIME_FAILURES = { 'HTTP error': 'http', 'network failure': 'network', 'invalid JSON': 'invalid', 'answer without a mode': 'shape' };

for (const [name, entry] of Object.entries(ENTRIES)) {
    for (const [label, failure] of Object.entries(RUNTIME_FAILURES)) {
        test(`unknown runtime (${label}) ${name}: nothing starts, nothing is asked or saved`, async () => {
            const env = setup({ runtime: 'unknown', runtimeReplies: [failure], mode: entry.mode, decision: true });
            const result = await run(env, entry.action);
            assert.equal(env.requests.filter(r => r.url === '/api/runtime-info').length, 1);
            assert.equal(env.prompts.length, 0);
            assert.deepEqual(result.engine, []);
            assert.equal(result.consentSaves, 0);
            assert.equal(env.counters.persisted, 0);
        });
    }

    test(`unknown runtime that answers packaged on retry ${name}: asks the notice first`, async () => {
        const env = setup({ runtime: 'unknown', runtimeReplies: [PACKAGED_INFO], mode: entry.mode, decision: false });
        const result = await run(env, entry.action);
        assert.equal(env.prompts.length, 1);
        assert.deepEqual(result.engine, []);
        assert.equal(env.counters.persisted, 0);
    });

    test(`unknown runtime that answers development on retry ${name}: unchanged`, async () => {
        const env = setup({ runtime: 'unknown', runtimeReplies: [DEVELOPMENT_INFO], mode: entry.mode, decision: false });
        const result = await run(env, entry.action);
        assert.equal(env.prompts.length, 0);
        assert.deepEqual(result.engine.map(r => r.url), [entry.url]);
    });
}

test('an unknown runtime explains why the engine did not start', async () => {
    const env = setup({ runtime: 'unknown', runtimeReplies: ['http'], mode: 'full' });
    await run(env, 'launchIris');
    assert.match(env.counters.warnings.join(' '), /No se pudo comprobar el tipo de instalación/);
});

test('only a confirmed development runtime skips the notice', async () => {
    const env = setup({ runtime: { mode: 'portable', capabilities: {} }, mode: 'quick', decision: false });
    const result = await run(env, 'quick');
    assert.equal(env.prompts.length, 1);
    assert.deepEqual(result.engine, []);
    assert.equal(env.requests.filter(r => r.url === '/api/runtime-info').length, 0);
});

test('a consent that cannot be saved does not start the engine', async () => {
    const env = setup({ mode: 'quick', decision: true, saveFails: true });
    const result = await run(env, 'quick');
    assert.equal(result.consentSaves, 1);
    assert.deepEqual(result.engine, []);
});

// A fake <dialog> that, like the embedded webview where this was found, never
// delivers `close`; `cancel` (Escape) and `close` can still be fired by hand.
function fakeDialog() {
    const listeners = new Map();
    const on = (map, type, handler) => { if (!map.has(type)) map.set(type, new Set()); map.get(type).add(handler); };
    const button = value => {
        const own = new Map();
        return {
            value,
            addEventListener: (type, handler) => on(own, type, handler),
            removeEventListener: (type, handler) => own.get(type)?.delete(handler),
            click() { for (const h of [...(own.get('click') || [])]) h({ preventDefault() {}, currentTarget: this }); },
        };
    };
    const buttons = [button('cancel'), button('accept')];
    return {
        open: false, returnValue: '', buttons,
        addEventListener: (type, handler) => on(listeners, type, handler),
        removeEventListener: (type, handler) => listeners.get(type)?.delete(handler),
        fire(type) { for (const h of [...(listeners.get(type) || [])]) h(); },
        showModal() { this.open = true; },
        close(value) { this.open = false; this.returnValue = value; },  // no event, on purpose
        querySelectorAll: () => buttons,
    };
}

function realPrompt(env, dialog) {
    env.ctx.document.getElementById = id => (id === 'engine-consent-dialog' ? dialog
        : id === 'engine-consent-body' ? { replaceChildren() {} } : { style: {}, classList: { add() {}, remove() {} } });
    vm.runInContext(between('function _promptEngineConsent()', '// Shared guard'), env.ctx);
}

test('the buttons settle the answer even if the dialog never fires close', async () => {
    for (const [index, expected] of [[1, true], [0, false]]) {
        const env = setup({ mode: 'quick' });
        const dialog = fakeDialog();
        realPrompt(env, dialog);
        const answer = env.ctx._promptEngineConsent();
        assert.equal(dialog.open, true);
        dialog.buttons[index].click();
        assert.equal(await answer, expected);
        assert.equal(dialog.open, false);
        assert.equal(vm.runInContext('_engineConsentPrompt', env.ctx), null);
    }
});

test('Escape and an external close count as declined unless the value is accept', async () => {
    for (const [event, value, expected] of [['cancel', '', false], ['close', 'cancel', false], ['close', 'accept', true]]) {
        const env = setup({ mode: 'quick' });
        const dialog = fakeDialog();
        realPrompt(env, dialog);
        const answer = env.ctx._promptEngineConsent();
        dialog.returnValue = value;
        dialog.open = false;
        dialog.fire(event);
        assert.equal(await answer, expected);
    }
});

test('a prompt left without an answer never blocks the next one', async () => {
    const env = setup({ mode: 'quick' });
    const dialog = fakeDialog();
    realPrompt(env, dialog);
    const first = env.ctx._promptEngineConsent();
    dialog.open = false;  // closed by a path that reported nothing
    const second = env.ctx._promptEngineConsent();
    assert.notEqual(first, second);
    assert.equal(await first, false);
    assert.equal(dialog.open, true);
    dialog.buttons[1].click();
    assert.equal(await second, true);
});

test('while the dialog is open, concurrent requests share one answer', async () => {
    const env = setup({ mode: 'quick' });
    const dialog = fakeDialog();
    realPrompt(env, dialog);
    const first = env.ctx._promptEngineConsent();
    assert.equal(env.ctx._promptEngineConsent(), first);
    dialog.buttons[0].click();
    assert.equal(await first, false);
});
