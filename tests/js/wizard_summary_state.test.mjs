// A08.9: the final summary says what the save actually left behind. Before, its title was a
// static "¡IRIS está lista!" with no AI provider, and a failed OAuth save was blamed on the
// voice settings (QA on a clean Windows 11 + Codex qa8 review). Real wizard.js functions run
// in a VM with in-memory doubles; every request is intercepted.
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
const html = readFileSync(new URL('../../wizard/ui/index.html', import.meta.url), 'utf8');

function between(start, end) {
    const a = source.indexOf(start);
    const b = source.indexOf(end, a + start.length);
    assert.ok(a >= 0 && b > a, `Missing function boundary: ${start}`);
    return source.slice(a, b);
}

const functions = [
    between('function _renderSummaryState(', 'async function loadWizardCompanionLifecycle()'),
    between('async function launchIris()', 'async function stopIris()'),
    between('function edgeVoiceLabel(', 'let _voicePreviewGeneration'),
].join('\n');

// A request that only ends when its attempt is aborted (by the deadline or a newer attempt).
function pendingUntilAborted(signal) {
    return new Promise((_, reject) => {
        const abort = () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
        if (signal?.aborted) abort();
        else signal?.addEventListener('abort', abort);
    });
}

async function finalStep(saveReply, { attempts = 1, channels = null, formOwnsProvider = true, formProvider = '',
                                     skipSave = false, configured = false, savedConfig = {}, formOAuth = false,
                                     formKey = '', edgeVoice = '', ttsProvider = 'edge-tts', kokoroOption = '' } = {}) {
    const nodes = new Map();
    const node = (id) => {
        if (!nodes.has(id)) {
            nodes.set(id, { value: '', checked: false, innerHTML: '', disabled: false, hidden: true, textContent: '',
                style: {}, dataset: {} });
        }
        return nodes.get(id);
    };
    const button = node('launch');
    if (channels) node('messaging-panel')._messaging = { platforms: channels };
    node('use-oauth').checked = formOAuth;
    node('llm-api-key').value = formKey;
    if (kokoroOption) node('kokoro-voice').selectedOptions = [{ textContent: kokoroOption }];
    node('summary-status').classList = { toggle: (name, on) => { if (name === 'dim') node('summary-status').dim = on; } };
    const calls = [];
    const saved = [];
    const timers = [];
    const warnings = [];
    const context = vm.createContext({ ...iconStubs,
        wizardState: { hermes_mode: 'full' }, wizardRuntime: { packaged: true },
        _configSaveGeneration: 0, _configSaveController: null, _configSaveSession: 'test-session',
        _lastConfigSaveOk: false, _lastConfigSaveError: '', _lastAiReadiness: null,
        AbortController, navigator: { platform: 'Win32' },
        selectedIdentityTraits: () => [], selectedLLMProvider: () => formProvider, selectedLLMModel: () => (formProvider ? 'form-model' : ''),
        selectedVoice: edgeVoice, LLM_PROVIDERS: {}, _llmFormOwnsProvider: () => formOwnsProvider, VISUAL_THEMES: { iris: { name: 'Iris' } },
        escapeHtml: String, loadWizardCompanionLifecycle: () => {}, renderWizardCompanionLifecycle: () => {},
        document: { getElementById: node, querySelectorAll: () => [], querySelector: (query) => (query.includes('.btn-gold') ? button : query === 'input[name="tts"]:checked' ? { value: ttsProvider } : null),
                    body: { dataset: { configured: configured ? 'true' : 'false' } } },
        _showConfigWarnings: (items) => warnings.push(...items),
        _ensureEngineConsent: async () => true, commitInstallerTheme: async () => {},
        _engineConsentBlockedReason: () => 'blocked',
        window: {}, location: { search: '', origin: 'http://127.0.0.1:8790', href: 'http://127.0.0.1:8790/ui/' },
        URL, URLSearchParams, console, alert: () => {},
        irisTell: async () => {}, irisAsk: async () => true,  // IRIS OS dialogs (wizard.js helpers)
        // A controlled clock: the test decides when the save's deadline fires.
        setTimeout: (fn) => { timers.push(fn); return timers.length; },
        clearTimeout: (id) => { timers[id - 1] = null; },
        fetch: async (url, options = {}) => {
            calls.push(url);
            // Each attempt starts with its read of the saved settings.
            const attempt = calls.filter((item) => item === '/api/current-config').length;
            const reply = attempt > 1 && saveReply.retry ? saveReply.retry : saveReply;
            if (url === '/api/current-config') {
                if (reply.getHang) return pendingUntilAborted(options.signal);
                return { ok: true, json: async () => savedConfig };
            }
            if (url === '/api/save-config') {
                saved.push(JSON.parse(options.body || '{}'));
                if (reply.hang) return pendingUntilAborted(options.signal);
                return { ok: reply.ok, status: reply.status, json: async () => reply.body };
            }
            if (url === '/api/iris/launch') {
                return { ok: false, status: 409, json: async () => ({ status: 'error', message: 'rechazado por el test' }) };
            }
            throw new Error(`Unexpected request ${url} (${options.method || 'GET'})`);
        },
    });
    context.window.parent = context.window;
    vm.runInContext(functions, context);
    let pending = skipSave ? Promise.resolve() : context.showSummaryAndSave();
    if (saveReply.hang || saveReply.getHang) {
        await new Promise((resolve) => setImmediate(resolve));
        if (saveReply.retryBeforeDeadline) {
            // The user retries while the first attempt is still waiting.
            const retry = context.showSummaryAndSave();
            await new Promise((resolve) => setImmediate(resolve));
            timers.forEach((fire) => fire?.());  // the first attempt's deadline now passes
            await Promise.all([pending, retry]);
        } else {
            timers.forEach((fire) => fire?.());
            await pending;
        }
    } else {
        await pending;
    }
    for (let extra = 1; !skipSave && extra < attempts; extra += 1) await context.showSummaryAndSave();
    const afterSave = {
        title: node('summary-title').textContent,
        status: node('summary-status').hidden ? '' : node('summary-status').textContent,
        launchEnabled: !button.disabled,
        retryShown: !node('summary-retry').hidden,
        statusTone: node('summary-status').dataset.tone || '',
        statusDim: node('summary-status').dim,
        configureAiShown: !node('summary-configure-ai').hidden,
        summary: node('config-summary').innerHTML,
        saved,
    };
    warnings.length = 0;
    await context.launchIris();
    return { ...afterSave, launchWarnings: [...warnings], launchSent: calls.includes('/api/iris/launch') };
}

test('the summary is not "ready" before anything was saved', () => {
    assert.match(html, /<h2 id="summary-title">Guardando tu configuración…<\/h2>/);
    assert.doesNotMatch(html, /<h2>¡IRIS está lista!<\/h2>/);
});

test('summary saves the chosen voice and preserves wake configuration in new and existing profiles', async () => {
    for (const savedConfig of [{}, { hermes_wake_mode: 'custom', hermes_wake_phrase: 'Hey Luna' }, { hermes_wake_mode: 'off' }]) {
        const step = await finalStep({ ok: true, status: 200, body: { status: 'saved', ai: { ready: true } } },
            { edgeVoice: 'es-MX-JorgeNeural', savedConfig });
        assert.match(step.summary, /Edge-TTS · Jorge \(México\)/);  // the name the voice step showed, not its id
        assert.doesNotMatch(step.summary, /Dalia/);
        assert.equal(step.saved[0].edge_tts_voice, 'es-MX-JorgeNeural');
        assert.equal(step.saved[0].hermes_wake_mode, 'keep');
        assert.equal(step.saved[0].hermes_wake_phrase, '');
    }
});

test('a saved TTS key counts as configured without exposing or resending it', async () => {
    for (const provider of ['elevenlabs', 'minimax']) {
        const step = await finalStep({ok:true,status:200,body:{status:'saved',ai:{ready:true}}},
            {ttsProvider:provider,savedConfig:{[`has_${provider}_api_key`]:true}});
        assert.match(step.summary, /Configurada/);
        assert.equal(step.saved[0][`${provider}_api_key`], '');
        const row=step.summary.split(`<wz-icon>🔑</wz-icon> ${provider==='minimax'?'MiniMax':'ElevenLabs'}`)[1];
        assert.match(row,/value ok/);
        assert.doesNotMatch(row,/No configurada/);
    }
});

test('a failed save shows its own reason, not a voice error, and launches nothing', async () => {
    const step = await finalStep({ ok: false, status: 409, body: { status: 'error', code: 'missing_oauth', message: 'OAuth pendiente' } });
    assert.equal(step.title, 'No se pudo guardar la configuración');
    assert.equal(step.status, 'OAuth pendiente');
    assert.equal(step.launchEnabled, false);
    assert.equal(step.launchSent, false);
    assert.ok(step.launchWarnings.some((w) => w.includes('OAuth pendiente')), step.launchWarnings);
    assert.ok(!step.launchWarnings.some((w) => /voz/i.test(w)), step.launchWarnings);
});

test('saved preferences without an AI provider are not called ready', async () => {
    const ai = { ready: false, code: 'missing_provider', message: 'Falta elegir y conectar tu proveedor de IA.' };
    const step = await finalStep({ ok: true, status: 200, body: { status: 'saved', ai } });
    assert.equal(step.title, 'Tus preferencias están guardadas');
    assert.equal(step.status, ai.message);
    assert.equal(step.launchEnabled, false);
    assert.equal(step.launchSent, false);
    assert.deepEqual(step.launchWarnings, [ai.message]);
    // 2026-10-19 review: the missing provider was a grey footnote under a green «LLM: No necesaria».
    assert.equal(step.statusTone, 'warn');
    assert.equal(step.statusDim, false);  // `.dim` would force it back to grey 11px mono
    assert.equal(step.configureAiShown, true);
    assert.match(step.summary, /Proveedor de IA<\/span><span class="value warn"><wz-icon>⚠<\/wz-icon> Sin configurar/);
    assert.match(step.summary, /Acceso a la IA<\/span><span class="value warn"><wz-icon>⚠<\/wz-icon> Falta elegir un proveedor/);
    assert.doesNotMatch(step.summary, /No necesaria|> LLM</);
});

test('only a missing provider offers «Configurar IA ahora» and the warning tone', async () => {
    const ready = await finalStep({ ok: true, status: 200, body: { status: 'saved', ai: { ready: true } } },
        { formProvider: 'openai', formKey: 'sk-test' });
    assert.equal(ready.configureAiShown, false);
    assert.equal(ready.statusTone, '');
    assert.equal(ready.statusDim, true);
    assert.doesNotMatch(ready.summary, /Falta elegir un proveedor|Sin configurar/);
    const failed = await finalStep({ ok: false, status: 500, body: { message: 'x' } });
    assert.equal(failed.configureAiShown, false);
    assert.equal(failed.statusTone, 'error');
});

test('the summary names a Kokoro voice the way its list did', async () => {
    const step = await finalStep({ ok: true, status: 200, body: { status: 'saved', ai: { ready: true } } },
        { ttsProvider: 'kokoro', kokoroOption: 'Dora ♀ (español · cálida)' });
    assert.match(step.summary, /Kokoro · Dora \(español · cálida\)/);
    assert.doesNotMatch(step.summary, /ef_dora|Kokoro-82M/);
});

test('a saved Fish Audio account keeps its voice/model without exposing or resending the key', async () => {
    const step = await finalStep({ok:true,status:200,body:{status:'saved',ai:{ready:true}}}, {
        ttsProvider:'fish-audio',savedConfig:{has_fish_audio_api_key:true,fish_audio_voice_id:'saved-voice',fish_audio_model:'s2.1-pro'},
    });
    assert.match(step.summary,/Fish Audio · voz seleccionada · modelo de pago/);
    assert.match(step.summary,/<wz-icon>🔑<\/wz-icon> Fish Audio<\/span><span class="value ok">/);
    assert.equal(step.saved[0].fish_audio_voice_id,'saved-voice');
    assert.equal(step.saved[0].fish_audio_model,'s2.1-pro');
    assert.equal(step.saved[0].fish_audio_api_key,'');
});

test('a saved setup with a ready provider is ready and launches', async () => {
    const step = await finalStep({ ok: true, status: 200, body: { status: 'saved', ai: { ready: true, code: '', message: '' } } });
    assert.equal(step.title, '¡IRIS está lista!');
    assert.equal(step.status, '');
    assert.equal(step.launchEnabled, true);
    assert.equal(step.launchSent, true);
});

test('when the backend cannot tell, the summary stays neutral and the launch decides', async () => {
    const step = await finalStep({ ok: true, status: 200, body: { status: 'saved' } });
    assert.equal(step.title, 'Configuración guardada');
    assert.equal(step.launchEnabled, true);
    assert.equal(step.launchSent, true);
});

// qa9 (Windows 11 VM): the save waited on a cold Hermes and the summary stayed on
// "Guardando tu configuración…" for good, with "Lanzar IRIS" silently doing nothing.
test('a save that never answers ends with a reason and a retry, and launches nothing', async () => {
    const step = await finalStep({ hang: true });
    assert.equal(step.title, 'No se pudo guardar la configuración');
    assert.match(step.status, /tardó demasiado/);
    assert.equal(step.retryShown, true);
    assert.equal(step.launchEnabled, false);
    assert.equal(step.launchSent, false);
});

test('only a failed save offers the retry', async () => {
    assert.equal((await finalStep({ ok: false, status: 500, body: { message: 'x' } })).retryShown, true);
    assert.equal((await finalStep({ ok: true, status: 200, body: { status: 'saved' } })).retryShown, false);
    assert.match(html, /id="summary-retry"[^>]*hidden[^>]*onclick="showSummaryAndSave\(\)"/);
});

// Codex qa10 review, R2: the deadline also covers the first read of the saved settings.
test('a first read that never answers also ends with a reason and a retry', async () => {
    const step = await finalStep({ getHang: true });
    assert.equal(step.title, 'No se pudo guardar la configuración');
    assert.match(step.status, /tardó demasiado/);
    assert.equal(step.retryShown, true);
    assert.equal(step.launchEnabled, false);
    assert.equal(step.launchSent, false);
});

test('a valid retry after a timeout reaches the ready state', async () => {
    const ready = { ok: true, status: 200, body: { status: 'saved', ai: { ready: true, code: '', message: '' } } };
    const step = await finalStep({ hang: true, retry: ready }, { attempts: 2 });
    assert.equal(step.title, '¡IRIS está lista!');
    assert.equal(step.retryShown, false);
    assert.equal(step.launchEnabled, true);
});

test('a replaced attempt cannot overwrite the newer result when its deadline passes', async () => {
    const ready = { ok: true, status: 200, body: { status: 'saved', ai: { ready: true, code: '', message: '' } } };
    const step = await finalStep({ getHang: true, retryBeforeDeadline: true, retry: ready });
    assert.equal(step.title, '¡IRIS está lista!');
    assert.equal(step.retryShown, false);
    assert.equal(step.launchEnabled, true);
});

// qa11: `selectedChannels` was removed in A08.6 but still read by the summary, which threw
// before saving and stayed on "Guardando tu configuración…". The test context no longer
// provides that global: the summary reads the enabled channels from the messaging panel.
test('the summary names the enabled channels and still saves', async () => {
    const channels = [
        { id: 'telegram', name: 'Telegram', enabled: true },
        { id: 'discord', name: 'Discord', enabled: false },
        { id: 'api_server', name: 'API server', enabled: true, managed: true },  // Iris' own link to Hermes
    ];
    const step = await finalStep({ ok: true, status: 200, body: { status: 'saved', ai: { ready: true, code: '', message: '' } } }, { channels });
    assert.equal(step.title, '¡IRIS está lista!');
    assert.match(step.summary, /Canales<\/span><span class="value">Telegram<\/span>/);
    assert.doesNotMatch(step.summary, /Discord|API server/);
});

test('without the messaging panel the summary saves and lists no channels', async () => {
    const step = await finalStep({ ok: true, status: 200, body: { status: 'saved', ai: { ready: true, code: '', message: '' } } });
    assert.equal(step.title, '¡IRIS está lista!');
    assert.doesNotMatch(step.summary, /Canales/);
});

// 2026-10-13: once the provider step saved its main provider, the form may hold another
// provider being added: the final save must keep the saved one, not send the form's.
test('the final save keeps the saved provider unless its form is in «Cambiar proveedor»', async () => {
    const ready = { ok: true, status: 200, body: { status: 'saved', ai: { ready: true, code: '', message: '' } } };
    const kept = await finalStep(ready, { formOwnsProvider: false, formProvider: 'deepseek' });
    assert.equal(kept.saved.length, 1);
    assert.equal(kept.saved[0].llm_provider, '');
    assert.equal(kept.saved[0].llm_model, '');
    const changed = await finalStep(ready, { formOwnsProvider: true, formProvider: 'deepseek' });
    assert.equal(changed.saved[0].llm_provider, 'deepseek');
});

// qa11: «Abrir IRIS OS» on the first screen shared the summary's guard and never opened a
// configured Iris unless its summary had been saved in the same session.
test('the first screen opens an already configured Iris without a save', async () => {
    const opened = await finalStep({}, { skipSave: true, configured: true });
    assert.equal(opened.launchSent, true);
    assert.deepEqual(opened.launchWarnings.filter((w) => /Espera a que termine/.test(w)), []);
    const firstRun = await finalStep({}, { skipSave: true, configured: false });
    assert.equal(firstRun.launchSent, false);
});

test('once a save started, a configured Iris still waits for it to succeed', async () => {
    const failed = await finalStep({ ok: false, status: 409, body: { status: 'error', message: 'OAuth pendiente' } }, { configured: true });
    assert.equal(failed.launchSent, false);
});

// R04 (Codex, 2026-10-13): a form that does not own the main provider says nothing about its
// credential. Before, an OAuth form being added made a saved API key read «No configurada».
const READY = { ok: true, status: 200, body: { status: 'saved', ai: { ready: true, code: '', message: '' } } };
const SAVED_API = { llm_provider: 'openrouter', llm_model: 'saved/model', llm_auth_mode: 'api', has_llm_api_key: true };
const SAVED_OAUTH = { llm_provider: 'openai', llm_model: 'saved/model', llm_auth_mode: 'api+oauth', llm_use_oauth: true,
    has_llm_oauth: true };

test('a saved API main provider stays configured while an OAuth provider is being added', async () => {
    const step = await finalStep(READY, { formOwnsProvider: false, formProvider: 'nous', formOAuth: true, savedConfig: SAVED_API });
    assert.ok(step.summary.includes('<wz-icon>✅</wz-icon> Configurada'));
    assert.doesNotMatch(step.summary, /faltante|No configurada|Sin OAuth/);
    assert.equal(step.saved[0].llm_provider, '');
    assert.equal(step.saved[0].llm_api_key, '');
});

test('a saved OAuth main provider stays signed in while an API provider is being added', async () => {
    const step = await finalStep(READY, { formOwnsProvider: false, formProvider: 'deepseek', formKey: 'sk-other',
        savedConfig: SAVED_OAUTH });
    assert.match(step.summary, /Sesión OAuth guardada/);
    assert.doesNotMatch(step.summary, /faltante|No configurada/);
    assert.equal(step.saved[0].llm_provider, '');
    assert.equal(step.saved[0].llm_api_key, '');
});

test('in «Cambiar proveedor» the summary shows and saves the form', async () => {
    const step = await finalStep(READY, { formOwnsProvider: true, formProvider: 'deepseek', formKey: 'sk-new',
        savedConfig: SAVED_API });
    assert.ok(step.summary.includes('<wz-icon>✅</wz-icon> Configurada'));
    assert.equal(step.saved[0].llm_provider, 'deepseek');
    assert.equal(step.saved[0].llm_api_key, 'sk-new');
});
