/**
 * IRIS Setup Wizard — Full Step Logic
 *
 * Welcome (unnumbered) → Assistant → AI → Voice → Avatar → Summary.
 * Stable section IDs keep the existing provider, voice and desktop contracts.
 */

let currentStep = 0;
let wizardFlow = 'first-run'; // first-run | basic | advanced | repair
let editingSection = 1;
let summaryReturn = false;
let displayedStep = null;
let hardwareProbe = null;
let wizardState = {
    personality_template: 'iris',
    assistant_name: 'IRIS',
    personality_instructions: '',
    personality_traits: [],
    custom_backstory: '',
    include_iris_backstory: true,
    tts_provider: 'edge-tts',
    edge_tts_voice: 'es-MX-DaliaNeural',
    avatar_choice: 'iris',  // 'iris' (included) or 'vrm' (the person's own model)
    avatar_vrm_path: '',
    uploadedModel: null,
    hermes_wake_mode: 'keep',
    hermes_wake_phrase: '',
    visual_theme: 'iris',
    visual_theme_selected: false,
    recommendations: {},
    llm_base_url: '',
};
let selectedHermesMode = null;  // 'full' | 'skip' | 'repair' | 'quick'
let hermesDetectionDone = false;
let visualThemeTouched = false;
// Runtime contract (A08.6). Until /api/runtime-info answers nothing that the
// backend could refuse is offered; development keeps the external installer.
let wizardRuntime = IrisWizardRuntime.profile(null);
const _desktopFrame = window.parent !== window
    && new URLSearchParams(location.search).get('desktop') === '1';

// IRIS OS icons. wizard_icons.js draws each <wz-icon> as the desktop's line icon; without
// it the emoji inside still reads. Only the wizard's own labels carry icons: names,
// models, provider data and errors are always plain text (Codex, wizard-icons-oct13-review).
const LEADING_ICON = /^([\p{Extended_Pictographic}✓✕])\uFE0F?\s*/u;

function uiIcon(token) {
    return `<wz-icon>${token}</wz-icon>`;
}

/** One of the wizard's labels («⏳ Instalando…»): its leading emoji becomes the icon and
 * the rest, which may quote data, stays plain text. */
function setIconText(element, label) {
    if (!element) return;
    const text = String(label ?? '');
    const match = text.match(LEADING_ICON);
    if (!match) {
        element.textContent = text;
        return;
    }
    const icon = document.createElement('wz-icon');
    icon.textContent = match[1];
    const rest = text.slice(match[0].length);
    element.replaceChildren(icon, ...(rest ? [` ${rest}`] : []));
}

/** The same for a label going into HTML: icon markup, then the escaped rest. */
function iconLabelHtml(label) {
    const text = String(label ?? '');
    const match = text.match(LEADING_ICON);
    return match ? `${uiIcon(match[1])} ${escapeHtml(text.slice(match[0].length))}` : escapeHtml(text);
}

// IRIS OS dialogs (/shared/iris_dialog.js, the same module Iris uses). It loads as a module, so it
// is read at call time; without it the browser's own dialog keeps the question answerable.
function irisAsk(options) {
    if (globalThis.IrisDialog) return globalThis.IrisDialog.confirm(options);
    return Promise.resolve(window.confirm([options.message, options.question].filter(Boolean).join(' ')));
}

function irisTell(options) {
    if (globalThis.IrisDialog) return globalThis.IrisDialog.alert(options);
    window.alert(options.message || '');
    return Promise.resolve();
}

function _runtimeText(key, fallback) {
    return IrisWizardRuntime.text(key, wizardRuntime, fallback);
}

function _installerTool() {
    return wizardRuntime.packaged ? 'OS SETUP' : 'OS INSTALLER';
}

async function loadWizardRuntime() {
    let info = null;
    try {
        const response = await fetch('/api/runtime-info', { cache: 'no-store' });
        if (response.ok) info = await response.json();
    } catch { /* stays unknown: install controls remain hidden */ }
    wizardRuntime = IrisWizardRuntime.profile(info);
    const body = document.body;
    body.dataset.runtimeMode = wizardRuntime.mode;
    body.dataset.hermesInstall = wizardRuntime.canInstallHermes ? 'on' : 'off';
    body.dataset.hermesAutostart = wizardRuntime.canAutostart ? 'on' : 'off';
    IrisWizardRuntime.applyTexts(document, wizardRuntime);
    if (wizardRuntime.packaged) document.title = _runtimeText('document-title', document.title);
    HEALTHCHECK_ROWS = IrisWizardRuntime.healthRows(wizardRuntime);
    return wizardRuntime;
}

// Inside the Desktop shell the Manual and the licenses open in the same frame:
// the embedded webview has no second window to show them in.
function _prepareDocumentationLinks() {
    if (!_desktopFrame) return;
    for (const id of ['manual-link', 'licenses-link']) {
        const link = document.getElementById(id);
        if (!link) continue;
        const url = new URL(link.getAttribute('href'), location.href);
        url.searchParams.set('desktop', '1');
        link.href = url.pathname.split('/').pop() + url.search + url.hash;
        link.target = '_self';
    }
}

const FLOW_LABELS = {
    'first-run': 'Primera configuración',
    basic: 'Personalización',
    advanced: 'Configuración de IA',
    repair: 'Diagnóstico',
    edit: 'Cambiar configuración',
    'theme-first': 'Primer arranque',
};

// The appearance (2) opens the setup: the installer takes the chosen edition's colours at once.
// QA12: a profile that never chose one opens on it, before the welcome ('theme-first').
const FLOW_STEPS = {
    'first-run': [0, 2, 1, 3, 4, 5, 8],
    advanced: [0, 3, 8],
    basic: [0, 2, 1, 4, 5, 8],
    repair: [0, 3, 8],
    'theme-first': [2, 0],
};

const VISUAL_THEMES = Object.freeze({
    iris: {
        name: 'Cyber', build: 'CYBER EDITION',
        copy: 'Cian eléctrico, magenta y profundidad de cristal.',
    },
    rose: {
        name: 'Sakura', build: 'SAKURA EDITION',
        copy: 'Rosa orbital, violeta suave y una atmósfera serena.',
    },
    verdant: {
        name: 'Hacker', build: 'HACKER EDITION',
        copy: 'Verde terminal, alto contraste y energía de laboratorio.',
    },
    sunrise: {
        name: 'Classic', build: 'CLASSIC EDITION',
        copy: 'Azul profundo, vidrio sobrio y concentración profesional.',
    },
});
let visualThemeSaveQueue = Promise.resolve(true);

function applyInstallerTheme(theme = 'iris', { selected = false, persisted = false } = {}) {
    const migratedTheme = theme === 'alice' ? 'iris' : theme;
    const safeTheme = VISUAL_THEMES[migratedTheme] ? migratedTheme : 'iris';
    const meta = VISUAL_THEMES[safeTheme];
    wizardState.visual_theme = safeTheme;
    if (selected) wizardState.visual_theme_selected = true;
    document.body.dataset.installerTheme = safeTheme;
    if (window.parent !== window && new URLSearchParams(location.search).get("desktop") === "1") {
        window.parent.postMessage({ type: "iris.desktop.preset", preset: safeTheme }, location.origin);
    }

    document.querySelectorAll('.edition-card').forEach(card => {
        const active = card.dataset.edition === safeTheme;
        card.classList.toggle('selected', active);
        const input = card.querySelector('input[name="visual_theme"]');
        if (input) input.checked = active;
    });

    const preview = document.getElementById('edition-live-preview');
    if (preview) preview.dataset.editionPreview = safeTheme;
    const build = document.getElementById('edition-preview-build');
    const name = document.getElementById('edition-preview-name');
    const copy = document.getElementById('edition-preview-copy');
    if (build) build.textContent = meta.build;
    if (name) name.textContent = meta.name.toUpperCase();
    if (copy) copy.textContent = meta.copy;
    if (persisted) {
        const state = document.getElementById('edition-save-state');
        if (state) {
            state.className = 'edition-save-state saved';
            state.textContent = 'EDICIÓN GUARDADA';
        }
    }
}

function commitInstallerTheme() {
    const theme = VISUAL_THEMES[wizardState.visual_theme]
        ? wizardState.visual_theme
        : 'iris';
    wizardState.visual_theme_selected = true;
    const state = document.getElementById('edition-save-state');
    if (state) {
        state.className = 'edition-save-state saving';
        state.textContent = 'GUARDANDO PERFIL…';
    }

    // Serialize rapid clicks so an older request can never finish last and
    // overwrite the edition the user actually left selected.
    const saveSelectedTheme = async () => {
        try {
            const response = await fetch('/api/save-visual-theme', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ theme }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.message || `HTTP ${response.status}`);
            if (state && wizardState.visual_theme === theme) {
                state.className = 'edition-save-state saved';
                state.textContent = 'EDICIÓN GUARDADA';
            }
            return true;
        } catch (error) {
            console.warn('[Wizard] No se pudo guardar el tema todavía:', error);
            if (state && wizardState.visual_theme === theme) {
                state.className = 'edition-save-state error';
                state.textContent = 'SE GUARDARÁ AL FINAL';
            }
            return false;
        }
    };
    visualThemeSaveQueue = visualThemeSaveQueue
        .catch(() => false)
        .then(saveSelectedTheme);
    return visualThemeSaveQueue;
}

document.querySelectorAll('input[name="visual_theme"]').forEach(input => {
    input.addEventListener('change', () => {
        if (!input.checked) return;
        const theme = input.value || 'iris';
        visualThemeTouched = true;
        applyInstallerTheme(theme, { selected: true });
        void commitInstallerTheme();
    });
});

function activeFlowSteps() {
    if (wizardFlow === 'edit') return [0, editingSection, 8];
    const steps = FLOW_STEPS[wizardFlow] || FLOW_STEPS['first-run'];
    // The setup after the welcome doesn't ask again for the appearance chosen before it.
    return wizardState.theme_chosen_first && ['first-run', 'basic'].includes(wizardFlow)
        ? steps.filter(step => step !== 2) : steps;
}

function setInstallerMode(mode = wizardFlow) {
    wizardFlow = mode;
    const modeLabel = document.getElementById('installer-mode-label');
    if (modeLabel) modeLabel.textContent = FLOW_LABELS[mode] || mode.toUpperCase();
}

function setInstallerModeLabel(label = '') {
    const modeLabel = document.getElementById('installer-mode-label');
    if (modeLabel) modeLabel.textContent = label;
}

function setInstallerStatus(status = 'IDLE', tool = 'OS INSTALLER') {
    const statusLabel = document.getElementById('installer-status-label');
    const toolLabel = document.getElementById('installer-tool-label');
    const statuses = { IDLE: 'En espera', READY: 'Disponible', EXECUTING: 'Configurando', ERROR: 'Revisar', SAVING: 'Guardando', RUNNING: 'En marcha' };
    const tools = { 'OS INSTALLER': 'Configuración', 'OS SETUP': 'Configuración', 'CONFIG CENTER': 'Ajustes', 'FULL SETUP': 'Configuración inicial', 'BASIC CONFIG': 'Personalización', 'ADVANCED CONFIG': 'Inteligencia artificial', 'HERMES REPAIR': 'Diagnóstico de IA' };
    if (statusLabel) statusLabel.textContent = statuses[status] || status;
    if (toolLabel) toolLabel.textContent = tools[tool] || tool;
}

function renderInstallerConfigReadout(config = {}) {
    const node = document.getElementById('installer-config-readout');
    if (!node) return;
    const hasHermes =
        Boolean(config.llm_provider || config.llm_provider_label || config.has_llm_api_key || config.has_llm_oauth);
    const customVoice =
        Boolean(config.tts_provider && config.tts_provider !== 'edge-tts')
        || Boolean(config.edge_tts_voice && config.edge_tts_voice !== 'es-MX-DaliaNeural');
    const customAvatar = Boolean(config.avatar_vrm_path);
    const configured = hasHermes || customVoice || customAvatar || config.hermes_wake_configured === true;

    if (configured) {
        node.textContent = 'Configuración existente detectada';
        setInstallerStatus('READY', 'CONFIG CENTER');
        setInstallerModeLabel('Configuración existente');
        document.body.dataset.configured = 'true';
    } else {
        node.textContent = wizardRuntime.packaged
            ? 'Listo para la primera configuración'
            : 'Listo para primera instalacion';
        setInstallerStatus('IDLE', _installerTool());
        setInstallerModeLabel('Primera configuración');
        document.body.dataset.configured = 'false';
    }
    const note = document.querySelector('[data-runtime-text="readout-note"]');
    if (note && configured) note.textContent = 'Puedes cambiar una sección o abrir IRIS OS con tus preferencias guardadas.';
}

// ── Step Navigation ─────────────────────────────────────

function updateUI() {
    if (currentStep !== 4 && typeof stopCloneRecording === 'function') {
        stopCloneRecording({ discard: true });
    }
    if (currentStep !== 4 && typeof _stopFishAudioSample === 'function') {
        _stopFishAudioSample();
    }
    document.querySelectorAll('.wizard-step').forEach((el) => {
        el.classList.toggle('active', Number(el.dataset.step) === currentStep);
    });
    document.body.dataset.wizardStep = String(currentStep);
    document.body.dataset.wizardFlow = wizardFlow;
    // Nothing comes before the appearance on a first start.
    const appearanceBack = document.querySelector('[data-step="2"] .step-buttons .btn-back');
    if (appearanceBack) appearanceBack.hidden = wizardFlow === 'theme-first';

    const steps = activeFlowSteps();
    const flowIndex = Math.max(0, steps.indexOf(currentStep));
    const pct = steps.length > 1 ? (flowIndex / (steps.length - 1)) * 100 : 0;
    document.getElementById('progress-fill').style.width = `${pct}%`;
    if (currentStep === 0) {
        document.getElementById('step-indicator').textContent = 'Bienvenida';
        const setupHint = document.querySelector('.setup-welcome-actions span');
        if (setupHint && wizardState.theme_chosen_first) {
            setupHint.textContent = 'Asistente, IA, voz y avatar; la apariencia ya está elegida. Puedes cambiarlo después.';
        }
        const configured = document.body.dataset.configured === 'true';
        setInstallerStatus(configured ? 'READY' : 'IDLE', configured ? 'CONFIG CENTER' : _installerTool());
        setInstallerModeLabel(configured ? 'Configuración existente' : 'Primera configuración');
    } else {
        const labels = { 1: 'Tu asistente', 2: 'Apariencia', 3: 'Inteligencia artificial', 4: 'Voz', 5: 'Avatar', 8: 'Resumen y abrir' };
        const prefix = summaryReturn ? 'Revisar · ' : wizardFlow === 'edit' ? ''
            : wizardFlow === 'theme-first' ? 'Antes de empezar · ' : `Paso ${flowIndex} de ${steps.length - 1} · `;
        document.getElementById('step-indicator').textContent = `${prefix}${labels[currentStep]}`;
        setInstallerMode(wizardFlow);
    }

    // A change of section, unlike a status refresh, moves the reader to its
    // heading. This works both standalone and inside the Desktop iframe.
    if (displayedStep !== currentStep) {
        displayedStep = currentStep;
        const heading = document.querySelector('.wizard-step.active h2');
        if (heading) {
            heading.tabIndex = -1;
            heading.focus({ preventScroll: true });
            window.scrollTo({ top: 0, behavior: 'instant' });
        }
    }

    // Trigger step-specific logic
    if (currentStep === 3) {
        applyHermesModeOnEntry();
        // Passive detection is shared with voice recommendations; simply
        // entering this section never starts, repairs or installs Hermes.
        if (!hardwareProbe) hardwareProbe = detectHardware().then(() => checkHermesOld());
        void _enterProviderCatalogSection();
        void loadLlmSetup();
    } else {
        _leaveProviderCatalogSection();
    }
    if (currentStep === 4) {
        updateVoiceSelectorVisibility();
        // Probe optional voice runtimes only while this configuration screen
        // is open. The desktop/chat path never pays for these subprocesses.
        void refreshExtrasStatus();
    }
    if (currentStep === 5) { // Avatar step
        checkModelPendingOnStepChange(5);
        refreshAnimationSlots();
    }
    if (currentStep === 8) showSummaryAndSave();
}

function nextStep() {
    const stepIdx = parseInt(document.querySelector(".wizard-step.active")?.dataset.step || 0);
    if (stepIdx === 1 && !validateIdentityStep()) return;
    if (currentStep === 2 && wizardFlow === 'theme-first') {
        // Keeping the preselected edition is a choice too.
        wizardState.theme_chosen_first = true;
        void commitInstallerTheme();
    }
    checkModelPendingOnStepChange(stepIdx + 1);
    // Leaving the Hermes step (3) → mark "model" sub-step done if the
    // user actually picked a provider + model (any non-empty selection
    // counts; we don't validate the API key here because Ollama and
    // local providers don't need one).
    if (stepIdx === 3 && typeof markModelStepDone === 'function') {
        const prov = selectedLLMProvider();
        const model = selectedLLMModel(prov);
        if (prov && model) markModelStepDone();
    }
    if (summaryReturn) {
        summaryReturn = false;
        currentStep = 8;
        updateUI();
        return;
    }
    const steps = activeFlowSteps();
    const idx = steps.indexOf(currentStep);
    if (idx >= 0 && idx < steps.length - 1) {
        currentStep = steps[idx + 1];
        updateUI();
    }
}

function prevStep() {
    if (currentStep === 8 && _summarySaving) return;
    if (summaryReturn) {
        summaryReturn = false;
        currentStep = 8;
        updateUI();
        return;
    }
    const steps = activeFlowSteps();
    const idx = steps.indexOf(currentStep);
    if (idx > 0) {
        currentStep = steps[idx - 1];
        updateUI();
    }
}

// QA12: a profile that never chose an edition opens on the appearance; «Siguiente» leads to the welcome.
function startThemeFirst() {
    setInstallerMode('theme-first');
    currentStep = 2;
    updateUI();
}

function startFirstInstall() {
    void commitInstallerTheme();
    setInstallerMode('first-run');
    setInstallerStatus('EXECUTING', 'FULL SETUP');
    summaryReturn = false;
    selectedHermesMode = 'full';
    wizardState.hermes_mode = 'full';
    currentStep = activeFlowSteps()[1];
    updateUI();
}

function startBasicSetup() {
    void commitInstallerTheme();
    setInstallerMode('basic');
    setInstallerStatus('EXECUTING', 'BASIC CONFIG');
    selectedHermesMode = 'skip';
    wizardState.hermes_mode = 'skip';
    currentStep = activeFlowSteps()[1];
    updateUI();
}

function startAdvancedSetup() {
    openWizardSection(3);
}

function openWizardSection(section) {
    if (![1, 2, 3, 4, 5].includes(section)) return;
    void commitInstallerTheme();
    editingSection = section;
    summaryReturn = false;
    setInstallerMode('edit');
    wizardState.hermes_mode = section === 3 ? 'full' : 'skip';
    selectedHermesMode = wizardState.hermes_mode;
    currentStep = section;
    updateUI();
}

function reviewWizardSection(section) {
    if (![1, 2, 3, 4, 5].includes(section)) return;
    if (_summarySaving) return;
    summaryReturn = true;
    // The provider form keeps ownership of an intentional edit (R01–R04).
    if (section === 3) wizardState.hermes_mode = 'full';
    currentStep = section;
    updateUI();
}

function startRepairFlow() {
    void commitInstallerTheme();
    setInstallerMode('repair');
    setInstallerStatus('EXECUTING', 'HERMES REPAIR');
    selectedHermesMode = 'repair';
    wizardState.hermes_mode = 'repair';
    currentStep = 3;
    updateUI();
    const diagnostics = document.getElementById('setup-diagnostics');
    if (diagnostics) diagnostics.open = true;
}

// ── Radio Card Selection ────────────────────────────────

document.querySelectorAll('.radio-card').forEach((card, index) => {
    const cardInput = card.querySelector('input[type="radio"]');
    const cardTitle = card.querySelector('.radio-title, .mode-card-title, strong');
    card.setAttribute('role', 'radio');
    card.tabIndex = 0;
    card.setAttribute('aria-checked', cardInput?.checked ? 'true' : 'false');
    // Named by its visible title, not a copy of it: packaged texts and the mode plan
    // rewrite titles later (a copy kept the stale «Modo Completo BETA»).
    if (cardTitle) {
        if (!cardTitle.id) cardTitle.id = `radio-card-title-${index}`;
        card.setAttribute('aria-labelledby', cardTitle.id);
    }
    card.addEventListener('keydown', (event) => {
        if (event.target !== card) return;
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        card.click();
    });
    card.addEventListener('click', (e) => {
        // Don't toggle if clicking a button inside the card
        if (e.target.closest('.btn-test')) return;

        const inputEl = card.querySelector('input');
        const name = inputEl ? inputEl.name : null;

        // Voice choices live in several disclosure groups. They still form
        // one native radio set; visual and accessible state follow that set.
        document.querySelectorAll('.radio-card').forEach(c => {
            const radio = c.querySelector('input[type="radio"]');
            if (!radio || radio.name !== name) return;
            const checked = c === card;
            radio.checked = checked;
            c.classList.toggle('selected', checked);
            c.setAttribute('aria-checked', checked ? 'true' : 'false');
        });
        card.classList.add('selected');
        card.setAttribute('aria-checked', 'true');
        if (inputEl) inputEl.checked = true;

        // Handle personality toggle
        if (name === 'personality') {
            const val = inputEl.value;
            wizardState.personality_template = val;
            syncIdentityModeUI(val);
            if (val === 'custom') {
                const customization = document.getElementById('identity-customization');
                if (customization) customization.open = true;
            }
            // The card handler checks the radio programmatically, which does
            // not reliably emit the radio's `change` event. Refresh here so
            // IRIS/custom never displays the previous mode's prompt.
            scheduleIdentityPreview();
        }
        // Handle avatar type toggle
        if (name === 'avatar') {
            const val = inputEl.value;
            wizardState.avatar_choice = val;
            const uploadSection = document.getElementById('model-upload-section');
            if (uploadSection) uploadSection.style.display = val === 'vrm' ? 'block' : 'none';
        }
        if (name === 'tts') {
            stopCloneRecording({ discard: true });
            wizardState.tts_provider = inputEl.value;
            updateVoiceSelectorVisibility();
        }
    });
});

const PERSONALITY_GUIDE = 'Perceptiva y cálida. Escucha antes de responder, es directa sin ser fría, mantiene curiosidad genuina y combina criterio técnico con explicaciones claras y breves.';
let identityTraitCatalog = [];
let identityTraitLimit = 6;
let identityPreviewTimer = null;
let identityPreviewRequestId = 0;

function syncIdentityModeUI(mode = 'iris') {
    const irisSection = document.getElementById('iris-personality-section');
    const customSection = document.getElementById('custom-personality-section');
    if (irisSection) irisSection.style.display = mode === 'custom' ? 'none' : 'grid';
    if (customSection) customSection.style.display = mode === 'custom' ? 'grid' : 'none';
    const validation = document.getElementById('identity-validation');
    if (validation) validation.textContent = '';
}

function fillPersonalityExample() {
    const field = document.getElementById('custom-personality-instructions');
    if (!field) return;
    field.value = PERSONALITY_GUIDE;
    field.focus();
    document.getElementById('identity-validation').textContent = '';
    scheduleIdentityPreview();
}

function selectedIdentityTraits() {
    return Array.isArray(wizardState.personality_traits)
        ? [...new Set(wizardState.personality_traits)].slice(0, identityTraitLimit)
        : [];
}

function renderIdentityTraits() {
    const grid = document.getElementById('identity-trait-grid');
    if (!grid) return;
    if (!identityTraitCatalog.length) {
        grid.innerHTML = '<div class="identity-traits-loading">No se pudo cargar el catálogo de rasgos.</div>';
        return;
    }
    grid.innerHTML = identityTraitCatalog.map(trait => `
        <button type="button" class="identity-trait-card" data-trait-id="${escapeHtml(trait.id)}"
                title="${escapeHtml(trait.prompt)}" aria-pressed="false">
            <span class="identity-trait-glyph">${escapeHtml(trait.glyph || '•')}</span>
            <span><strong>${escapeHtml(trait.label)}</strong><small>${escapeHtml(trait.description)}</small></span>
        </button>
    `).join('');
    grid.querySelectorAll('.identity-trait-card').forEach(button => {
        button.addEventListener('click', () => toggleIdentityTrait(button.dataset.traitId));
    });
    updateIdentityTraitSelection();
}

function toggleIdentityTrait(traitId) {
    const selected = selectedIdentityTraits();
    const index = selected.indexOf(traitId);
    if (index >= 0) selected.splice(index, 1);
    else if (selected.length < identityTraitLimit) selected.push(traitId);
    else {
        const validation = document.getElementById('identity-validation');
        if (validation) validation.textContent = `Puedes combinar hasta ${identityTraitLimit} rasgos.`;
        return;
    }
    wizardState.personality_traits = selected;
    const validation = document.getElementById('identity-validation');
    if (validation) validation.textContent = '';
    updateIdentityTraitSelection();
    scheduleIdentityPreview();
}

function updateIdentityTraitSelection() {
    const selected = selectedIdentityTraits();
    document.querySelectorAll('.identity-trait-card').forEach(button => {
        const isSelected = selected.includes(button.dataset.traitId);
        button.classList.toggle('selected', isSelected);
        button.setAttribute('aria-pressed', String(isSelected));
        button.disabled = !isSelected && selected.length >= identityTraitLimit;
    });
    const count = document.getElementById('identity-trait-count');
    if (count) count.textContent = `${selected.length} / ${identityTraitLimit}`;
    const limit = document.getElementById('identity-trait-limit');
    if (limit) limit.textContent = String(identityTraitLimit);
}

function buildIdentityPreviewPayload() {
    const personality = document.querySelector('input[name="personality"]:checked')?.value || 'iris';
    const irisInstructions = document.getElementById('iris-personality-instructions')?.value?.trim() || '';
    const customInstructions = document.getElementById('custom-personality-instructions')?.value?.trim() || '';
    return {
        assistant_name: document.getElementById('assistant-name')?.value?.trim() || 'IRIS',
        personality_template: personality,
        personality_instructions: personality === 'custom' ? customInstructions : irisInstructions,
        personality_traits: selectedIdentityTraits(),
        custom_backstory: personality === 'custom'
            ? (document.getElementById('custom-backstory')?.value?.trim() || '')
            : '',
        include_iris_backstory: Boolean(document.getElementById('include-iris-backstory')?.checked),
    };
}

function scheduleIdentityPreview() {
    clearTimeout(identityPreviewTimer);
    identityPreviewTimer = setTimeout(() => void refreshIdentityPreview(), 260);
}

async function refreshIdentityPreview() {
    const requestId = ++identityPreviewRequestId;
    const payload = buildIdentityPreviewPayload();
    const prompt = document.getElementById('identity-prompt-preview');
    const warnings = document.getElementById('identity-preview-warnings');
    if (!prompt) return null;
    if (payload.personality_template === 'custom'
        && !payload.personality_instructions
        && payload.personality_traits.length === 0) {
        prompt.textContent = 'Elige al menos un rasgo o escribe cómo debe comportarse tu personaje.';
        if (warnings) warnings.innerHTML = '';
        return null;
    }
    try {
        const response = await fetch('/api/identity-preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.message || `HTTP ${response.status}`);
        if (requestId !== identityPreviewRequestId) return null;
        prompt.textContent = data.soul || 'Sin contenido de personalidad.';
        const traitMetric = document.getElementById('identity-preview-traits');
        const charMetric = document.getElementById('identity-preview-chars');
        const tokenMetric = document.getElementById('identity-preview-tokens');
        if (traitMetric) traitMetric.textContent = String(data.selected_traits?.length || 0);
        if (charMetric) charMetric.textContent = Number(data.characters || 0).toLocaleString('es');
        if (tokenMetric) tokenMetric.textContent = Number(data.estimated_tokens || 0).toLocaleString('es');
        if (warnings) warnings.innerHTML = (data.warnings || [])
            .map(item => `<p><wz-icon>⚠</wz-icon> ${escapeHtml(item)}</p>`).join('');
        return data;
    } catch (error) {
        if (requestId !== identityPreviewRequestId) return null;
        prompt.textContent = `No se pudo construir la vista previa: ${error.message || error}`;
        return null;
    }
}

async function testIdentityPreviewWithHermes() {
    const button = document.getElementById('identity-sample-btn');
    const output = document.getElementById('identity-sample-output');
    if (!button || !output) return;
    const preview = await refreshIdentityPreview();
    if (!preview) return;
    button.disabled = true;
    button.textContent = 'Consultando…';
    output.hidden = false;
    output.textContent = 'IRIS está interpretando el perfil…';
    try {
        const response = await fetch('/api/identity-preview/sample', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(buildIdentityPreviewPayload()),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.message || data.detail || `HTTP ${response.status}`);
        output.textContent = data.sample || 'El modelo respondió sin texto.';
    } catch (error) {
        output.textContent = `No se pudo generar la muestra: ${error.message || error}`;
    } finally {
        button.disabled = false;
        button.textContent = 'Generar muestra';
    }
}

async function exportIdentityProfile() {
    const validation = document.getElementById('identity-validation');
    try {
        const response = await fetch('/api/identity-profile/export', {
            method: 'POST',
            cache: 'no-store',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(buildIdentityPreviewPayload()),
        });
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(data.message || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const disposition = response.headers.get('Content-Disposition') || '';
        const filename = disposition.match(/filename="([^"]+)"/)?.[1] || 'IRIS-identity.json';
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        if (validation) validation.textContent = 'Perfil exportado sin credenciales ni memoria.';
    } catch (error) {
        if (validation) validation.textContent = `No se pudo exportar el perfil: ${error.message || error}`;
    }
}

function triggerIdentityProfileImport() {
    document.getElementById('identity-profile-file')?.click();
}

async function importIdentityProfile(event) {
    const input = event?.target;
    const file = input?.files?.[0];
    const validation = document.getElementById('identity-validation');
    if (!file) return;
    try {
        if (file.size > 512 * 1024) throw new Error('El perfil supera el límite de 512 KB.');
        const documentPayload = JSON.parse(await file.text());
        const response = await fetch('/api/identity-profile/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ document: documentPayload }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.message || `HTTP ${response.status}`);
        const warning = (data.warnings || []).join(' ');
        if (validation) validation.textContent = `Perfil importado. ${warning} Recargando configuración…`.trim();
        setTimeout(() => window.location.reload(), 650);
    } catch (error) {
        if (validation) validation.textContent = `No se pudo importar el perfil: ${error.message || error}`;
    } finally {
        if (input) input.value = '';
    }
}

async function loadIdentityHistory() {
    const list = document.getElementById('identity-history-list');
    if (!list) return;
    try {
        const response = await fetch('/api/identity-history', { cache: 'no-store' });
        const data = await response.json();
        const history = Array.isArray(data.history) ? data.history : [];
        if (!history.length) {
            list.innerHTML = '<p>Aún no hay revisiones de identidad.</p>';
            return;
        }
        list.innerHTML = history.map(entry => {
            const summary = entry.summary || {};
            const traits = Array.isArray(summary.traits) && summary.traits.length
                ? `${summary.traits.length} rasgos`
                : 'sin rasgos';
            const saved = entry.saved_at ? new Date(entry.saved_at).toLocaleString('es') : 'fecha desconocida';
            return `<article class="identity-history-item">
                <div><strong>${escapeHtml(summary.assistant_name || 'IRIS')}</strong><small>${escapeHtml(saved)} · ${escapeHtml(traits)} · ${escapeHtml(entry.source || 'identity')}</small></div>
                <button type="button" data-history-id="${escapeHtml(entry.id)}">Restaurar</button>
            </article>`;
        }).join('');
        list.querySelectorAll('[data-history-id]').forEach(button => {
            button.addEventListener('click', () => restoreIdentityHistory(button.dataset.historyId, button));
        });
    } catch (error) {
        list.innerHTML = `<p>No se pudo leer el historial: ${escapeHtml(error.message || error)}</p>`;
    }
}

async function restoreIdentityHistory(entryId, button) {
    if (!entryId || !await irisAsk({
        title: 'Restaurar identidad', icon: 'update',
        message: 'Se restaurará esta versión. La identidad actual quedará guardada en el historial.',
        question: '¿Deseas continuar?', confirmLabel: 'Restaurar', confirmIcon: 'update',
    })) return;
    const validation = document.getElementById('identity-validation');
    button.disabled = true;
    try {
        const response = await fetch(`/api/identity-history/${encodeURIComponent(entryId)}/restore`, { method: 'POST' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.message || `HTTP ${response.status}`);
        if (validation) validation.textContent = 'Versión restaurada. Recargando configuración…';
        setTimeout(() => window.location.reload(), 500);
    } catch (error) {
        button.disabled = false;
        if (validation) validation.textContent = `No se pudo restaurar: ${error.message || error}`;
    }
}

async function importLegacyPersonality() {
    const validation = document.getElementById('identity-validation');
    try {
        const response = await fetch('/api/identity-legacy/import', { method: 'POST' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.message || `HTTP ${response.status}`);
        if (validation) validation.textContent = 'Personalidad antigua importada al perfil visible. Recargando…';
        setTimeout(() => window.location.reload(), 500);
    } catch (error) {
        if (validation) validation.textContent = `No se pudo importar personality.json: ${error.message || error}`;
    }
}

async function initIdentityStudio() {
    try {
        const response = await fetch('/api/identity-options', { cache: 'no-store' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || `HTTP ${response.status}`);
        identityTraitCatalog = Array.isArray(data.traits) ? data.traits : [];
        identityTraitLimit = Number(data.max_traits) || 6;
        const knownTraitIds = new Set(identityTraitCatalog.map(trait => trait.id));
        wizardState.personality_traits = selectedIdentityTraits()
            .filter(traitId => knownTraitIds.has(traitId));
        const backstory = document.getElementById('iris-backstory-preview');
        const currentName = document.getElementById('assistant-name')?.value?.trim() || 'IRIS';
        if (backstory) backstory.textContent = String(data.iris_backstory || '').replaceAll('{name}', currentName);
        const legacyNotice = document.getElementById('identity-legacy-notice');
        if (legacyNotice) legacyNotice.hidden = !data.legacy_file?.detected;
    } catch (error) {
        console.warn('[Identity] options unavailable:', error);
    }
    renderIdentityTraits();
    const watched = [
        'assistant-name', 'iris-personality-instructions',
        'custom-personality-instructions', 'custom-backstory',
        'include-iris-backstory',
    ];
    watched.forEach(id => {
        const element = document.getElementById(id);
        if (!element || element.dataset.identityPreviewBound) return;
        element.addEventListener(element.type === 'checkbox' ? 'change' : 'input', scheduleIdentityPreview);
        element.dataset.identityPreviewBound = 'true';
    });
    document.querySelectorAll('input[name="personality"]').forEach(radio => {
        if (radio.dataset.identityPreviewBound) return;
        radio.addEventListener('change', scheduleIdentityPreview);
        radio.dataset.identityPreviewBound = 'true';
    });
    updateIdentityTraitSelection();
    await Promise.all([refreshIdentityPreview(), loadIdentityHistory()]);
}

function validateIdentityStep() {
    const validation = document.getElementById('identity-validation');
    const name = document.getElementById('assistant-name')?.value?.trim() || '';
    const mode = document.querySelector('input[name="personality"]:checked')?.value || 'iris';
    const customInstructions = document.getElementById('custom-personality-instructions')?.value?.trim() || '';
    const selectedTraits = selectedIdentityTraits();
    let message = '';
    if (!name) message = 'Escribe un nombre para tu asistente antes de continuar.';
    else if (name.length > 48) message = 'El nombre puede tener como máximo 48 caracteres.';
    else if (mode === 'custom' && !customInstructions && selectedTraits.length === 0) {
        message = 'Elige al menos un rasgo, describe la personalidad o usa el ejemplo como guía.';
    }
    if (validation) validation.textContent = message;
    if (message) {
        (message.startsWith('Escribe')
            ? document.getElementById('assistant-name')
            : document.getElementById('custom-personality-instructions'))?.focus();
        return false;
    }
    wizardState.assistant_name = name;
    wizardState.personality_template = mode;
    wizardState.personality_traits = selectedTraits;
    return true;
}

// ── Step 1: Hardware Detection ──────────────────────────

async function detectHardware() {
    const panel = document.getElementById('hardware-info');
    panel.innerHTML = '<div class="loading-spinner"></div><p>Detectando hardware...</p>';

    let hardwareOk = false;
    try {
        const res = await fetch('/api/detect-hardware');
        const d = await res.json();

        const esc = value => escapeHtml(String(value ?? ''));
        const ram = Number.isFinite(d.ram_gb) ? `${d.ram_gb} GB` : 'No verificable';
        const cpu = d.cpu || {};
        const gpu = d.gpu || {};
        const support = d.platform_support || {};
        const hermesCompat = d.hermes_compatibility || {};
        const rec = d.recommendations || {};
        wizardState.recommendations = rec;
        const inference = rec.inference || {};
        const voice = rec.voice || {};
        const ollama = d.local_model_servers?.ollama || {};
        const lmstudio = d.local_model_servers?.lmstudio || {};
        wizardState.local_model_servers = d.local_model_servers || {};
        const gpuAccelerated = gpu.acceleration === 'cuda' || gpu.acceleration === 'metal';
        const gpuText = gpu.detected
            ? `${gpu.name || 'GPU detectada'} (${gpu.acceleration || 'genérica'})`
            : 'Sin aceleración confirmada (CPU)';
        const localText = [
            `Ollama: ${ollama.running ? `activo, ${(ollama.models || []).length} modelo(s)` : 'servidor no detectado'}`,
            `LM Studio: ${lmstudio.running ? `activo, ${(lmstudio.models || []).length} modelo(s)` : 'servidor no detectado'}`,
        ].join(' · ');

        panel.innerHTML = `
            <div class="info-row"><span class="label">Sistema</span><span class="value">${esc(d.os)} ${esc(d.machine)}</span></div>
            <div class="info-row"><span class="label">Compatibilidad</span><span class="${support.status === 'supported' ? 'value ok' : 'value warn'}">${esc(support.label || 'No verificada')}</span></div>
            <div class="info-row"><span class="label">CPU</span><span class="value">${esc(cpu.name || 'No identificada')} · ${esc(cpu.logical_cores || '?')} hilos</span></div>
            <div class="info-row"><span class="label">RAM</span><span class="value">${esc(ram)}</span></div>
            <div class="info-row"><span class="label">GPU</span><span class="${gpuAccelerated ? 'value ok' : 'value'}">${gpu.detected && gpuAccelerated ? `${uiIcon('✅')} ` : ''}${esc(gpuText)}</span></div>
            <div class="info-row"><span class="label">Hermes</span><span class="${d.hermes_runnable && hermesCompat.status === 'ready' ? 'value ok' : d.hermes_installed ? 'value warn' : 'value'}">${esc(d.hermes_version || d.hermes_state || 'No instalado')}${hermesCompat.label ? ` · ${esc(hermesCompat.label)}` : ''}</span></div>
            <div class="info-row"><span class="label">IA local</span><span class="value dim">${esc(localText)}</span></div>
            <div class="info-row"><span class="label">IA recomendada</span><span class="value highlight">${esc(inference.label || 'Proveedor remoto')}</span></div>
            <p class="dim" style="margin:4px 0 8px">${esc(inference.reason || '')}</p>
            <div class="info-row"><span class="label">Voz recomendada</span><span class="value highlight">${esc(voice.label || 'Edge TTS')}</span></div>
            <p class="dim" style="margin:4px 0 8px">${esc(voice.reason || '')}</p>
            <div class="info-row"><span class="label">Entradas / salidas de audio</span><span class="value">${(d.audio_input || []).length} / ${(d.audio_output || []).length}</span></div>
            ${d.hermes_path ? `<div class="info-row"><span class="label">Hermes CLI</span><span class="value dim" style="font-size:10px">${esc(d.hermes_path)}</span></div>` : ''}
        `;
        hardwareOk = true;
    } catch (e) {
        panel.innerHTML = `<p class="err">Error: ${escapeHtml(e.message)}</p><p class="dim">Asegúrate de que el wizard backend esté corriendo.</p>`;
    }

    // After hardware info, also run the Hermes mode picker. The mode panel
    // is below #hardware-info in the same step (see index.html step 2).
    // If hardware probe failed we still try — Hermes detection is
    // independent. If both fail, we expose the fallback "Siguiente sin
    // elegir modo →" button so the user is never stuck.
    try {
        await runHermesModePicker();
    } catch (err) {
        console.warn('hermes mode picker failed:', err);
        // Surface the fallback button so the user can still advance.
        const fb = document.getElementById('hw-next-fallback');
        if (fb) fb.style.display = 'inline-block';
    }
}

/**
 * On step 3 entry, the user has ALREADY picked a mode in step 2. This
 * function just renders the appropriate panel and auto-triggers the
 * matching action (install / start / restart) — no second mode picker.
 *
 * Defensive: if the user somehow lands here without a mode (deep link,
 * dev-tools manipulation, old cached JS), render the legacy install
 * panel via checkHermesOld() so they're not stuck on a blank panel.
 */
/**
 * Sync IRIS's config.json with Hermes' on-disk config (config.yaml + .env).
 * Only fires for quick/repair modes (the user is reusing existing Hermes
 * state) and only once per wizard session — guarded by
 * wizardState.hermes_config_imported.
 */
async function maybeImportHermesConfig() {
    if (wizardState.hermes_config_imported) return;
    const mode = wizardState.hermes_mode;
    if (mode !== 'quick' && mode !== 'repair') return;
    try {
        const res = await fetch('/api/import-hermes-config', { method: 'POST' });
        const d = await res.json().catch(() => ({}));
        // Mark done regardless of status — we only want to attempt once.
        // 'warning' (no_hermes_config) is a legit terminal state, not a
        // transient failure worth retrying.
        wizardState.hermes_config_imported = true;
        if (d && d.status === 'imported') {
            // Best-effort: nothing else to do here. The pre-flight on
            // /api/start-gateway will now read the freshly-synced config.
        }
    } catch (e) {
        // Network error → leave the flag false so a retry can happen if
        // the user navigates back and forward. No UI surface yet because
        // the user can still proceed with whatever IRIS had cached.
    }
}

function applyHermesModeOnEntry() {
    const mode = wizardState.hermes_mode;
    // Entry is passive. Importing existing Hermes settings happens only when
    // the user applies quick/repair, alongside that explicit action.
    if (!mode) {
        // Compatibility fallback for an old cached page (probe
        // failed) or jumped here via deep link. Send them back to step 2
        // so they can pick. The mode picker DOM lives there now; running
        // the legacy in-place detector would mutate step 2's DOM while
        // step 3 is active, which looks broken to the user.
        const infoPanel = document.getElementById('hermes-info');
        if (infoPanel) {
            infoPanel.innerHTML = `
                <div class="info-row warn"><wz-icon>⚠</wz-icon> Elige cómo continuar con el motor</div>
                <p class="dim" style="margin-top:6px">${wizardRuntime.packaged
                    ? 'Las opciones del motor incluido están en este apartado de diagnóstico.'
                    : 'En este apartado puedes configurar, iniciar o reparar el motor de IA.'}</p>
            `;
        }
        // Render the legacy install panel anyway so power users have
        // controls available (provider radios, Install button, etc.).
        if (typeof checkHermesOld === 'function') checkHermesOld();
        return;
    }
    // The user picked a mode in step 2 → confirmHermesMode() already set
    // wizardState.hermes_mode and triggered the right action. Just make
    // sure the info panel reflects the choice so they don't see a stale
    // loading spinner.
    const infoPanel = document.getElementById('hermes-info');
    if (infoPanel && infoPanel.innerHTML.includes('loading-spinner')) {
        const labels = wizardRuntime.packaged ? {
            full:   '<wz-icon>🧠</wz-icon> Configura el proveedor de IA del motor incluido…',
            quick:  '<wz-icon>⚡</wz-icon> Activando el motor incluido con tu configuración guardada…',
            repair: '<wz-icon>🔧</wz-icon> Reiniciando el motor incluido…',
            skip:   '<wz-icon>✅</wz-icon> El motor incluido ya estaba listo — esta pestaña queda informativa.',
        } : {
            full:   '<wz-icon>🧠</wz-icon> Modo Completo — instalando Hermes desde cero…',
            quick:  '<wz-icon>⚡</wz-icon> Iniciando IRIS con la configuración guardada…',
            repair: '<wz-icon>🔧</wz-icon> Reiniciando IRIS…',
            skip:   '<wz-icon>✅</wz-icon> Hermes ya estaba listo — esta pestaña queda informativa.',
        };
        infoPanel.innerHTML = `<div class="info-row">${labels[mode] || ('Modo: ' + mode)}</div>`;
    }
}

/**
 * Run the same /api/hermes-status probe that step 3 used to do, but in
 * step 2. Wires up the mode-cards visibility based on the detected state
 * (full/skip/quick/repair). Continue button stays hidden until the user
 * clicks a card.
 */
async function runHermesModePicker() {
    const modePanel = document.getElementById('hermes-mode-panel');
    if (!modePanel) return;  // markup not present (legacy HTML)

    const messageEl  = document.getElementById('hermes-mode-message');
    const betaWarn   = document.getElementById('beta-warning');
    const continueBtn = document.getElementById('modo-continue-btn');
    const cards = document.querySelectorAll('.mode-card');

    cards.forEach((card) => {
        const input = card.querySelector('input');
        const label = card.querySelector('.mode-label, .mode-card-title');
        const desc = card.querySelector('.mode-desc, .mode-card-desc');
        if (input && !input.dataset.canonicalValue) input.dataset.canonicalValue = input.value;
        if (label && !label.dataset.canonicalText) label.dataset.canonicalText = label.textContent;
        if (desc && !desc.dataset.canonicalText) desc.dataset.canonicalText = desc.textContent;
        if (!card.dataset.canonicalOnclick) card.dataset.canonicalOnclick = card.getAttribute('onclick') || ' ';
        if (input) {
            input.value = input.dataset.canonicalValue;
            input.checked = false;
            delete input.dataset.origValue;
        }
        if (label) label.textContent = label.dataset.canonicalText;
        if (desc) desc.textContent = desc.dataset.canonicalText;
        const originalClick = card.dataset.canonicalOnclick.trim();
        if (originalClick) card.setAttribute('onclick', originalClick);
        else card.removeAttribute('onclick');
        card.style.display = '';
        card.style.pointerEvents = '';
        card.style.opacity = '';
        card.classList.remove('selected', 'disabled');
        delete card.dataset.origOnclick;
    });

    modePanel.style.display = 'block';
    if (messageEl) messageEl.innerHTML = '<span class="dim">Comprobando el motor de IA…</span>';
    if (continueBtn) continueBtn.style.display = 'none';

    let d;
    try {
        const res = await fetch('/api/hermes-status');
        d = await res.json();
    } catch (e) {
        if (messageEl) messageEl.innerHTML = '<strong class="warn"><wz-icon>⚠</wz-icon> No se pudo verificar el motor de IA</strong> — Selecciona el modo manualmente.';
        // We can't trust /api/hermes-status, so disable "Modo Rápido"
        // (its semantics depend on whether Hermes is running). The user
        // can still pick Completo or Reparar manually.
        cards.forEach(c => {
            c.style.display = '';
            c.classList.remove('selected');
            const val = c.querySelector('input')?.value;
            if (val === 'skip' || val === 'quick') {
                // Disable the Rápido card and surface why.
                c.classList.add('disabled');
                c.style.pointerEvents = 'none';
                c.style.opacity = '0.5';
                const desc = c.querySelector('.mode-card-desc');
                if (desc && !desc.dataset.origDisabledDesc) {
                    desc.dataset.origDisabledDesc = desc.textContent;
                    desc.textContent = '(requiere Hermes detectado)';
                }
            }
        });
        return;
    }

    hermesDetectionDone = true;
    wizardState.hermes_install_status = d.status || 'unknown';
    wizardState.hermes_install_diagnostic = d.diagnostic || '';
    wizardState.hermes_residual_launcher = Boolean(d.residual_launcher);
    wizardState.hermes_install_message = d.message || '';

    if (betaWarn) betaWarn.style.display = d.beta_warning ? 'block' : 'none';

    if (wizardRuntime.packaged) {
        _applyPackagedModePlan(d, cards, messageEl, continueBtn);
        return;
    }

    const autoSelect = d.mode; // 'full' | 'skip' | 'quick' | 'repair'

    if (d.status === 'connected') {
        if (messageEl) messageEl.innerHTML = `<strong><wz-icon>✅</wz-icon> ${escapeHtml(d.message)}</strong> — Puedes saltar la instalación de Hermes e ir directo a configurar voz y avatar.`;
        cards.forEach(c => {
            c.style.display = '';
            c.classList.remove('selected');
            const val = c.querySelector('input')?.value;
            if (val === autoSelect) {
                c.classList.add('selected');
                c.querySelector('input').checked = true;
            }
        });
    } else if (d.status === 'damaged') {
        if (messageEl) {
            const detail = d.diagnostic ? `<br><span class="dim">${escapeHtml(d.diagnostic)}</span>` : '';
            messageEl.innerHTML = `<strong class="warn"><wz-icon>🔧</wz-icon> Instalación incompleta o dañada</strong> — El lanzador existe pero no puede ejecutarse.${detail}`;
        }
        cards.forEach(c => {
            const val = c.querySelector('input')?.value;
            c.style.display = val === 'repair' ? '' : 'none';
            c.classList.toggle('selected', val === 'repair');
            const input = c.querySelector('input');
            if (input) input.checked = val === 'repair';
        });
    } else if (d.status === 'needs_setup') {
        if (messageEl) messageEl.innerHTML = '<strong><wz-icon>🧠</wz-icon> El motor de IA responde</strong> — Falta elegir proveedor y modelo; no hace falta reinstalarlo.';
        cards.forEach(c => {
            const val = c.querySelector('input')?.value;
            c.style.display = val === 'full' ? '' : 'none';
            c.classList.toggle('selected', val === 'full');
            const input = c.querySelector('input');
            if (input) input.checked = val === 'full';
            if (val === 'full') {
                const label = c.querySelector('.mode-card-title');
                const desc = c.querySelector('.mode-card-desc');
                if (label) label.textContent = 'Completar configuración';
                if (desc) desc.textContent = 'Conservar Hermes y configurar un proveedor de IA y un modelo viables.';
            }
        });
    } else if (d.status === 'installed') {
        if (messageEl) {
            const compat = d.compatibility?.status === 'update_recommended'
                ? `<br><span class="warn"><wz-icon>⚠</wz-icon> ${escapeHtml(d.compatibility.label)}</span>`
                : '';
            messageEl.innerHTML = `<strong><wz-icon>⚡</wz-icon> Servicio de IA detenido — motor preparado</strong><br><span class="dim">Puedes iniciar IRIS con la configuración guardada. Si algo está roto, intenta reparar.</span>${compat}`;
        }
        cards.forEach(c => {
            const val = c.querySelector('input')?.value;
            const label = c.querySelector('.mode-label, .mode-card-title');
            const desc = c.querySelector('.mode-desc, .mode-card-desc');
            if (val === 'skip') {
                if (label && !label.dataset.origLabel) {
                    label.dataset.origLabel = label.textContent;
                    label.textContent = 'Iniciar IRIS';
                }
                if (desc && !desc.dataset.origDesc) {
                    desc.dataset.origDesc = desc.textContent;
                    desc.textContent = 'El motor ya está preparado. Pulsa «Iniciar IRIS» para conectar tu asistente.';
                }
                // Re-clicking this renamed card must keep the 'quick' semantics.
                c.dataset.origOnclick = c.getAttribute('onclick') || '';
                c.setAttribute('onclick', "selectHermesMode('quick')");
                const input = c.querySelector('input');
                if (input) {
                    if (!input.dataset.origValue) input.dataset.origValue = input.value;
                    input.value = 'quick';
                }
                c.style.display = '';
                c.classList.add('selected');
                c.querySelector('input').checked = true;
            } else if (val === 'repair') {
                c.style.display = '';  // visible as a fallback
            } else if (val === 'full') {
                // An existing functional install must still allow changing
                // provider/model. "Full" here means full configuration, not
                // reinstall: checkHermesOld() will detect the launcher and
                // keep the install button hidden.
                c.style.display = '';
                if (label) label.textContent = 'Cambiar proveedor / modelo';
                if (desc) desc.textContent = 'Conserva Hermes instalado y abre la configuración completa de proveedores.';
            } else {
                c.style.display = 'none';
            }
        });
    } else {
        // missing / unknown → only "Modo Completo" applies. A global
        // launcher left by an old pip install is not a managed Hermes runtime,
        // so it follows the same install path with a more precise explanation.
        if (messageEl) {
            const detail = d.diagnostic
                ? `<br><span class="dim">${escapeHtml(d.diagnostic)}</span>`
                : '';
            messageEl.innerHTML = d.residual_launcher
                ? `<strong class="warn"><wz-icon>🧹</wz-icon> Launcher antiguo detectado</strong> — La instalación administrada de Hermes ya no existe. Puedes instalarla desde cero; el perfil se conservará.${detail}`
                : `<strong><wz-icon>🧠</wz-icon> Hermes no detectado</strong> — Se instalará desde cero (Modo Completo).`;
        }
        cards.forEach(c => {
            const val = c.querySelector('input')?.value;
            if (val === 'skip' || val === 'repair') {
                c.style.display = 'none';
            } else {
                c.style.display = '';
                c.classList.remove('selected');
                if (val === 'full') {
                    c.classList.add('selected');
                    c.querySelector('input').checked = true;
                    if (d.residual_launcher) {
                        const label = c.querySelector('.mode-card-title');
                        const desc = c.querySelector('.mode-card-desc');
                        if (label) label.textContent = 'Instalar Hermes';
                        if (desc) desc.textContent = 'Crea el runtime oficial administrado e ignora el launcher global residual.';
                    }
                }
            }
        });
    }

    selectedHermesMode = autoSelect;
    const cardToSelect = document.querySelector(`.mode-card input[value="${autoSelect}"]`)?.closest('.mode-card');
    if (cardToSelect) {
        cardToSelect.classList.add('selected');
        cardToSelect.querySelector('input').checked = true;
    }
    if (continueBtn) continueBtn.style.display = 'inline-block';
}

// The bundled engine never offers installation: the plan only shows the
// configuration, activation or repair that the package really supports.
function _applyPackagedModePlan(status, cards, messageEl, continueBtn) {
    const plan = IrisWizardRuntime.packagedModePlan(status);
    if (messageEl) {
        messageEl.replaceChildren();
        const title = document.createElement('strong');
        title.className = plan.tone === 'warn' ? 'warn' : '';
        title.textContent = plan.title;
        const detail = document.createElement('span');
        detail.className = 'dim';
        detail.textContent = ` — ${plan.detail}`;
        messageEl.hidden = !plan.title;
        if (plan.title) messageEl.append(title, detail);
        if (status.diagnostic && plan.tone === 'warn') {
            const diagnostic = document.createElement('div');
            diagnostic.className = 'dim';
            diagnostic.textContent = status.diagnostic;
            messageEl.append(diagnostic);
        }
    }
    const container = document.getElementById('mode-cards');
    const hasOptions = Object.values(plan.cards).some(entry => entry.visible);
    container?.classList.toggle('is-empty', !hasOptions);
    const panel = document.getElementById('hermes-mode-panel');
    if (panel) {
        panel.hidden = !plan.title && !hasOptions;
        panel.style.display = panel.hidden ? 'none' : 'block';
    }
    cards.forEach((card) => {
        const input = card.querySelector('input');
        const key = input?.dataset.canonicalValue || input?.value;
        const entry = plan.cards[key] || { visible: false };
        card.style.display = entry.visible ? '' : 'none';
        const label = card.querySelector('.mode-card-title');
        const desc = card.querySelector('.mode-card-desc');
        if (entry.title && label) label.textContent = entry.title;
        if (entry.desc && desc) desc.textContent = entry.desc;
        if (entry.mode && input) {
            input.value = entry.mode;
            card.setAttribute('onclick', `selectHermesMode('${entry.mode}')`);
        }
        const selected = entry.visible && (input?.value === plan.select);
        card.classList.toggle('selected', selected);
        if (input) input.checked = selected;
    });
    selectedHermesMode = plan.select;
    // «Aplicar opción» appears once a card is picked (selectHermesMode).
    if (continueBtn) continueBtn.style.display = plan.select ? 'inline-block' : 'none';
}

// ── Step 2: Hermes Full Setup ─────────────────────────

// All providers Hermes supports, grouped by category.
//
// These are seeded with a hardcoded fallback so the wizard still renders if
// the backend is unreachable; on load we try to replace them with the list
// served by `GET /api/providers` (source of truth in wizard/providers.py).
// `let` instead of `const` specifically so the backend load can overwrite.
let LLM_PROVIDERS = {
    // ── Popular / Recommended ──
    anthropic:  { name: 'Anthropic (Claude)', category: 'popular', auth: 'api+oauth', url: 'https://platform.claude.com/settings/keys', hint: 'platform.claude.com' },
    openai:     { name: 'OpenAI (GPT)', category: 'popular', auth: 'api+oauth', url: 'https://platform.openai.com/api-keys', hint: 'platform.openai.com' },
    google:     { name: 'Google (Gemini)', category: 'popular', auth: 'api', url: 'https://aistudio.google.com/apikey', hint: 'aistudio.google.com (gratis)' },
    deepseek:   { name: 'DeepSeek', category: 'popular', auth: 'api', url: 'https://platform.deepseek.com/api-keys', hint: 'platform.deepseek.com' },
    // ── Cloud Providers ──
    xai:        { name: 'xAI (Grok)', category: 'cloud', auth: 'api+oauth', url: 'https://console.x.ai/', hint: 'console.x.ai' },
    nvidia:     { name: 'NVIDIA NIM', category: 'cloud', auth: 'api', url: 'https://build.nvidia.com/', hint: 'build.nvidia.com' },
    bedrock:    { name: 'Amazon Bedrock', category: 'cloud', auth: 'aws', url: 'https://console.aws.amazon.com/bedrock/', hint: 'Credenciales mediante la cadena AWS SDK' },
    'azure-foundry': { name: 'Azure Foundry', category: 'cloud', auth: 'api', url: 'https://ai.azure.com/', hint: 'API key y endpoint del deployment', base_url_required: true },
    'copilot-acp': { name: 'GitHub Copilot (ACP)', category: 'cloud', auth: 'external', url: 'https://github.com/github/copilot-cli', hint: 'Requiere Copilot CLI autenticada' },
    // ── Chinese AI ──
    minimax:    { name: 'MiniMax', category: 'chinese', auth: 'api', url: 'https://platform.minimax.io/', hint: 'platform.minimax.io' },
    alibaba:    { name: 'Alibaba (Qwen Cloud)', category: 'chinese', auth: 'api', url: 'https://dashscope.console.aliyun.com/', hint: 'Aliyun DashScope' },
    moonshot:   { name: 'Moonshot AI (Kimi)', category: 'chinese', auth: 'api', url: 'https://platform.moonshot.cn/', hint: 'platform.moonshot.cn' },
    zhipu:      { name: 'Z.AI (GLM)', category: 'chinese', auth: 'api', url: 'https://open.bigmodel.cn/', hint: 'open.bigmodel.cn' },
    stepfun:    { name: 'StepFun', category: 'chinese', auth: 'api', url: 'https://platform.stepfun.com/', hint: 'platform.stepfun.com' },
    // ── Aggregators (access 100+ models with one key) ──
    openrouter: { name: 'OpenRouter', category: 'aggregator', auth: 'api+oauth', url: 'https://openrouter.ai/keys', hint: 'openrouter.ai — Accede a 100+ modelos con una key' },
    // ── Local / Self-hosted ──
    'ollama-local': { name: 'Ollama (Local)', category: 'local', auth: 'none', url: 'https://ollama.com/download', hint: 'Requiere Ollama activo y un modelo descargado por el usuario' },
    vllm:       { name: 'vLLM', category: 'local', auth: 'none', url: 'https://vllm.ai/', hint: 'Servidor de inferencia local' },
    custom:     { name: 'Endpoint OpenAI-compatible', category: 'local', auth: 'api-optional', url: '', hint: 'Indica la URL completa del servidor', base_url_required: true },
    huggingface:{ name: 'Hugging Face', category: 'cloud', auth: 'api', url: 'https://huggingface.co/settings/tokens', hint: 'huggingface.co/settings/tokens' },
};

// Model identifiers always come from the backend snapshot built from the
// installed Hermes inventory. If it is unavailable, the selector offers only
// the explicit manual-model path instead of presenting a stale build-time ID.
let LLM_MODELS = {};
// Category labels
let PROVIDER_CATEGORIES = {
    popular: '⭐ Populares',
    cloud: '☁️ Cloud',
    chinese: '🌐 IA China',
    aggregator: '🌐 Agregadores (100+ modelos)',
    local: '🏠 Local / Self-hosted',
};

/**
 * Replace the in-memory provider catalog with whatever the wizard backend
 * serves from `/api/providers`. If the fetch fails, provider cards remain
 * available but the model field degrades honestly to manual entry.
 *
 * Backend shape (v2 — now merged with Hermes's live catalog):
 *   { categories: {popular: '...', ...},
 *     providers:  [{id, name, icon, category, auth, url, hint,
 *                   hermes_id, env_key,
 *                   discovered: bool,          // true ⇒ Hermes-only, synthesized
 *                   hermes_detected: bool,   // true ⇒ curated + confirmed by Hermes
 *                   models: [{value, label, tier,
 *                             context?, input?, available?, discovered?}, ...]
 *                  }, ...],
 *     source:     'merged' | 'static',
 *     hermes:   {version, providers_detected, models_detected}
 *   }
 *
 * The UI keeps LLM_PROVIDERS keyed by id and LLM_MODELS keyed by id for
 * backwards compat. The richer fields (discovered, available, …) live on
 * `PROVIDER_META[id]` so the render layer can add 🆕 badges without
 * changing the existing call sites.
 */
let PROVIDER_META = {};           // id → {discovered, hermes_detected}
let MODEL_META    = {};           // `${provId}::${modelId}` → {discovered, available, context}
let LLM_OAUTH_MODELS = {};        // provider id → models valid for its OAuth-specific Hermes adapter
let LLM_OAUTH_CATALOG = {};       // provider id → separate OAuth adapter was authoritatively inventoried
let PROVIDER_SOURCE = 'static';   // 'merged' | 'static' — shown in footer

let _providerCatalogLoadPromise = null;
let _latestProviderCatalogPayload = null;

function loadProvidersFromBackend() {
    if (_providerCatalogLoadPromise) return _providerCatalogLoadPromise;
    const load = _loadProvidersFromBackend();
    _providerCatalogLoadPromise = load;
    const release = () => {
        if (_providerCatalogLoadPromise === load) _providerCatalogLoadPromise = null;
    };
    // Attach both handlers explicitly: ignoring the Promise returned by
    // `.finally()` would create an unhandled rejection if the loader ever
    // throws before its defensive catch.
    load.then(release, release);
    return load;
}

async function _loadProvidersFromBackend() {
    try {
        const res = await fetch('/api/providers', { cache: 'no-store' });
        if (!res.ok) return false;
        const payload = await res.json();
        if (!payload || !Array.isArray(payload.providers)) return false;
        _latestProviderCatalogPayload = payload;

        const nextProviders = {};
        const nextModels = {};
        const nextOAuthModels = {};
        const nextOAuthCatalog = {};
        const nextMeta = {};
        const nextModelMeta = {};
        for (const p of payload.providers) {
            if (!p || !p.id) continue;
            nextProviders[p.id] = {
                name:     p.name,
                icon:     p.icon,
                category: p.category,
                auth:     p.auth,
                env_key:  p.env_key || '',
                url:      p.url,
                hint:     p.hint,
                base_url: p.base_url || '',
                base_url_required: !!p.base_url_required,
                base_url_env_key: p.base_url_env_key || '',
            };
            nextModels[p.id] = Array.isArray(p.models) ? p.models : [];
            nextOAuthModels[p.id] = Array.isArray(p.oauth_models) ? p.oauth_models : [];
            nextOAuthCatalog[p.id] = !!p.oauth_catalog_authoritative;
            nextMeta[p.id] = {
                discovered:        !!p.discovered,
                hermes_detected: !!p.hermes_detected,
                warning:           p.warning || null,
            };
            for (const m of [...nextModels[p.id], ...nextOAuthModels[p.id]]) {
                if (!m || !m.value) continue;
                nextModelMeta[`${p.id}::${m.value}`] = {
                    discovered: !!m.discovered,
                    available:  m.available,
                    context:    m.context,
                    input:      m.input,
                    locked:     !!m.locked,
                    lockReason: m.lock_reason || '',
                };
            }
        }
        LLM_PROVIDERS = nextProviders;
        LLM_MODELS = nextModels;
        LLM_OAUTH_MODELS = nextOAuthModels;
        LLM_OAUTH_CATALOG = nextOAuthCatalog;
        PROVIDER_META = nextMeta;
        MODEL_META = nextModelMeta;
        PROVIDER_SOURCE = payload.source || 'static';
        if (payload.categories && typeof payload.categories === 'object') {
            PROVIDER_CATEGORIES = payload.categories;
        }
        // Update the live-sync indicator if it's mounted.
        _renderProviderSyncPill(payload);
        return true;
    } catch {
        return false;
    }
}

/**
 * Render the catalog-sync pill + refresh button based on the snapshot
 * metadata returned by /api/providers.
 *
 * The pill now shows:
 *   • which file the catalog was read from (runtime vs bundled vs
 *     missing)
 *   • the date that snapshot was captured, in DD/MM/AAAA Spanish
 *     format so the user recognizes freshness at a glance
 *   • the provider + model counts (from the merged payload, which
 *     includes curated + discovered)
 *
 * The refresh button is always shown when we have a usable catalog;
 * it's hidden only on the rare "no catalog at all" case (where
 * showing it would be a no-op — the user can't see anything to
 * refresh FROM).
 */
function _renderProviderSyncPill(payload) {
    const pill = document.getElementById('provider-sync-pill');
    const refreshBtn = document.getElementById('provider-refresh-btn');
    if (!pill) return;

    const snap = payload.snapshot || {};
    const source = snap.source || 'missing';
    const dateIso = snap.snapshot_date || '';
    const dateLabel = _formatSnapshotDate(dateIso);
    const providers = (payload.providers && payload.providers.length) || 0;
    const models = payload.selectable_model_count
        ?? snap.model_count
        ?? (payload.hermes && payload.hermes.models_detected)
        ?? 0;
    const officialInventory = snap.catalog_source === 'installed_inventory';

    if (payload.degraded) {
        setIconText(pill, '⚠️ Catálogo no disponible · escribe el modelo manualmente');
        pill.className = 'sync-pill warn';
        pill.title = 'No pude validar modelos con Hermes. Las tarjetas siguen disponibles, pero IRIS no presenta IDs antiguos como actuales.';
    } else if (source === 'runtime') {
        // User has already run at least one manual refresh. Green pill.
        setIconText(pill, `🔄 ${providers} proveedores · ${models} modelos · Sincronizado ${dateLabel}`);
        pill.className = 'sync-pill ok';
        pill.title = officialInventory
            ? `Inventario oficial de modelos de Hermes, sincronizado el ${dateLabel}.`
            : `Catálogo de compatibilidad de Hermes, sincronizado el ${dateLabel}.`;
    } else if (source === 'bundled') {
        // Shipped snapshot. Amber to nudge the user toward a refresh
        // if the date is old, neutral if recent.
        setIconText(pill, `📦 ${providers} proveedores · ${models} modelos · Catálogo bundled del ${dateLabel}`);
        pill.className = 'sync-pill warn';
        pill.title = `Estás viendo el catálogo bundled con IRIS (fecha ${dateLabel}). Haz click en "Actualizar catálogo" para consultar Hermes y traer la lista más reciente.`;
    } else {
        // No snapshot at all — first run without Hermes installed,
        // or both files missing/corrupt. Show the static list warning.
        setIconText(pill, '📋 Lista estática (sin catálogo Hermes)');
        pill.className = 'sync-pill off';
        pill.title = 'No se encontró ningún snapshot del catálogo. Instala Hermes y haz click en "Actualizar catálogo" para descargarlo.';
    }

    // Show the refresh button whenever Hermes COULD be installed.
    // Even on "missing" we show it — the user may have just installed
    // Hermes and wants to populate the catalog for the first time.
    if (refreshBtn) {
        refreshBtn.hidden = false;
    }
}

/** Convert "YYYY-MM-DD" ISO date to "DD/MM/AAAA" Spanish format.
 *  Returns "fecha desconocida" on empty / malformed input. */
function _formatSnapshotDate(iso) {
    if (!iso || typeof iso !== 'string') return 'fecha desconocida';
    const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return iso;
    return `${m[3]}/${m[2]}/${m[1]}`;
}

let _catalogAutoRefreshAttempted = false;
let _catalogAutoRefreshTimer = null;

function _catalogSnapshotNeedsRefresh(snapshot) {
    const snap = snapshot || {};
    if (snap.source !== 'runtime') return true;
    // A same-day public fallback is not equivalent to the installed Hermes
    // inventory. Retry once this session so a just-installed/repaired Hermes
    // replaces the partial fallback immediately instead of waiting a day.
    if (snap.catalog_source !== 'installed_inventory') return true;
    const match = String(snap.snapshot_date || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) return true;
    const snapshotDay = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    const now = new Date();
    const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
    return !Number.isFinite(snapshotDay) || snapshotDay < today;
}

function _isProviderCatalogSectionActive() {
    const section = document.getElementById('llm-section');
    const step = section?.closest?.('.wizard-step');
    return currentStep === 3 && !!step?.classList.contains('active');
}

async function _enterProviderCatalogSection() {
    await loadProvidersFromBackend();
    if (!_isProviderCatalogSectionActive()) return;
    // IA no longer follows a mode-confirmation screen. Populate and hydrate
    // its form here on the first visit; later visits keep unsaved edits.
    const section = document.getElementById('llm-section');
    if (section && !section.dataset.initialized) {
        section.dataset.initialized = 'loading';
        try {
            await _originalCheckHermes();
            section.dataset.initialized = 'ready';
        } catch (error) {
            delete section.dataset.initialized;
            console.warn('No se pudo preparar el formulario de IA:', error);
        }
    }
    if (!_isProviderCatalogSectionActive()) return;
    _maybeAutoRefreshProviderCatalog(_latestProviderCatalogPayload);
}

function _leaveProviderCatalogSection() {
    if (_catalogAutoRefreshTimer) {
        clearTimeout(_catalogAutoRefreshTimer);
        _catalogAutoRefreshTimer = null;
    }
    const refresh = window._catalogRefreshState;
    if (refresh?.inFlight && refresh.automatic) {
        cancelCatalogRefresh({ resetAutomaticAttempt: true });
    }
}

function _maybeAutoRefreshProviderCatalog(payload) {
    if (
        _catalogAutoRefreshAttempted
        || _catalogAutoRefreshTimer
        || !_isProviderCatalogSectionActive()
        || !_catalogSnapshotNeedsRefresh(payload?.snapshot)
    ) return;
    // Paint the immediately available list first. Refreshing is deliberately
    // deferred and non-blocking so offline users can continue configuring
    // Hermes while the last known-good snapshot stays in place.
    _catalogAutoRefreshTimer = setTimeout(() => {
        _catalogAutoRefreshTimer = null;
        if (_isProviderCatalogSectionActive() && !window._catalogRefreshState?.inFlight) {
            _catalogAutoRefreshAttempted = true;
            startCatalogRefresh({ automatic: true });
        }
    }, 250);
}

// ── Explicit catalog refresh ──────────────────────────────────────
//
// Wired to the "🔄 Actualizar catálogo" button. Opens an SSE stream
// against POST /api/providers/refresh, shows a progress panel with a
// wall-clock timer + progress bar proportional to the 45 s worst case,
// and on success re-fetches /api/providers so the provider list is
// re-rendered with fresh data AND a fresh "Actualizado DD/MM/AAAA"
// stamp.
//
// State is kept on `window` so the panel can be cancelled from other
// handlers (e.g. if the user navigates away the beforeunload hook
// could tear it down — not implemented but the hook is available).
window._catalogRefreshState = null;
let _catalogRefreshGeneration = 0;

async function startCatalogRefresh(options = {}) {
    const automatic = !!options.automatic;
    const panel = document.getElementById('provider-refresh-panel');
    const pill  = document.getElementById('provider-sync-pill');
    const btn   = document.getElementById('provider-refresh-btn');
    const bar   = document.getElementById('refresh-progress-bar');
    const elap  = document.getElementById('refresh-elapsed');
    if (!panel || !btn) return;

    // Don't double-start. If a refresh is already in flight, clicking
    // the button is a no-op.
    if (window._catalogRefreshState && window._catalogRefreshState.inFlight) return;

    // Reset panel to pristine in-flight state (clear any error/info
    // styling and restore the meta row from a previous failed attempt).
    panel.hidden = false;
    panel.classList.remove('error');
    panel.classList.remove('info');
    panel.classList.add('in-flight');
    btn.disabled = true;
    bar.style.width = '0%';
    elap.textContent = '0:00';
    const metaEl = panel.querySelector('.refresh-panel-meta');
    if (metaEl) metaEl.style.display = '';
    const titleEl = panel.querySelector('.refresh-panel-title');
    if (titleEl) titleEl.textContent = automatic
        ? 'Actualizando lista automáticamente…'
        : 'Actualizando catálogo de Hermes…';
    const iconEl = panel.querySelector('.refresh-panel-icon');
    if (iconEl) setIconText(iconEl, '🔄');
    const hintEl = panel.querySelector('.refresh-panel-hint');
    if (hintEl) hintEl.textContent = _runtimeText('refresh-hint', 'Consultando el inventario oficial de proveedores y modelos de tu Hermes instalado. Si la versión es antigua, se usará el catálogo público compatible.');

    // AbortController so "Cancelar" can tear down the fetch and kill
    // the server-side subprocess via asyncio.CancelledError.
    const ac = new AbortController();
    const MAX_SECONDS = 45;
    const t0 = Date.now();

    // Local timer: ticks every 500 ms to animate elapsed counter and
    // progress bar. Independent of server heartbeats so the UI feels
    // alive even if the SSE events are chunky.
    const timer = setInterval(() => {
        const elapsed = (Date.now() - t0) / 1000;
        const pct = Math.min(100, (elapsed / MAX_SECONDS) * 100);
        bar.style.width = pct.toFixed(1) + '%';
        const mm = Math.floor(elapsed / 60);
        const ss = Math.floor(elapsed % 60).toString().padStart(2, '0');
        elap.textContent = `${mm}:${ss}`;
    }, 500);

    const run = {
        inFlight: true,
        automatic,
        abort: ac,
        timer,
        generation: ++_catalogRefreshGeneration,
        successHideTimer: null,
    };
    window._catalogRefreshState = run;

    try {
        const res = await fetch('/api/providers/refresh', {
            method: 'POST',
            signal: ac.signal,
            headers: { 'Accept': 'text/event-stream' },
        });
        if (!res.ok || !res.body) {
            throw new Error(`HTTP ${res.status}`);
        }

        // Parse SSE manually. Lines look like "data: {json}\n\n". We
        // accumulate bytes in a buffer, split on "\n\n", and treat
        // each chunk as one event.
        const reader = res.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        let terminated = false;

        while (!terminated) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            let idx;
            while ((idx = buffer.indexOf('\n\n')) >= 0) {
                const rawEvent = buffer.slice(0, idx);
                buffer = buffer.slice(idx + 2);
                const dataLine = rawEvent.split('\n').find(l => l.startsWith('data: '));
                if (!dataLine) continue;
                let evt;
                try {
                    evt = JSON.parse(dataLine.slice(6));
                } catch { continue; }

                if (evt.type === 'done') {
                    terminated = true;
                    await _onCatalogRefreshDone(evt, run);
                } else if (evt.type === 'error') {
                    terminated = true;
                    _onCatalogRefreshError(evt, { automatic });
                } else if (evt.type === 'progress') {
                    // Server heartbeat — our local timer already
                    // updates the UI every 500 ms, so we mostly just
                    // trust the local clock. Could adjust here if
                    // the server reports it's farther along than we
                    // think.
                } else if (evt.type === 'start') {
                    // Start event — nothing to do, timer is running.
                }
            }
        }
        if (!terminated) {
            throw new Error('El stream terminó sin confirmar si el catálogo fue actualizado.');
        }
    } catch (err) {
        // AbortError = user clicked Cancel. Any other error = network
        // / parse / server failure.
        if (err.name === 'AbortError') {
            // Silent — the cancel handler already tore down the panel.
            return;
        }
        _onCatalogRefreshError({
            type: 'error',
            code: 'network',
            message: `Error de red: ${err.message || err}`,
        }, { automatic });
    } finally {
        clearInterval(timer);
        if (window._catalogRefreshState === run) {
            panel.classList.remove('in-flight');
            btn.disabled = false;
            window._catalogRefreshState = null;
        }
    }
}

function cancelCatalogRefresh(options = {}) {
    const panel = document.getElementById('provider-refresh-panel');
    const s = window._catalogRefreshState;
    if (s && s.abort) {
        try { s.abort.abort(); } catch {}
    }
    if (s && s.timer) clearInterval(s.timer);
    if (s && s.successHideTimer) clearTimeout(s.successHideTimer);
    _catalogRefreshGeneration += 1;
    if (panel) {
        panel.hidden = true;
        panel.classList.remove('in-flight', 'error');
    }
    window._catalogRefreshState = null;
    if (options.resetAutomaticAttempt) _catalogAutoRefreshAttempted = false;
    const btn = document.getElementById('provider-refresh-btn');
    if (btn) btn.disabled = false;
}

async function _onCatalogRefreshDone(evt, run) {
    const panel = document.getElementById('provider-refresh-panel');
    const snap  = evt.snapshot || {};
    const dateLabel = _formatSnapshotDate(snap.snapshot_date || '');
    const dur = evt.duration_s || snap.duration_s || '?';

    // Flash a brief success state in the panel, then fade out and
    // re-fetch /api/providers so the full list repaints with fresh
    // data + fresh date stamp.
    if (panel) {
        const titleEl = panel.querySelector('.refresh-panel-title');
        if (titleEl) titleEl.textContent =
            `Fuente actualizada: ${evt.providers_count} catálogos de modelos · ${evt.models_count} modelos`;
        const iconEl = panel.querySelector('.refresh-panel-icon');
        if (iconEl) setIconText(iconEl, '✅');
        const hintEl = panel.querySelector('.refresh-panel-hint');
        if (hintEl) {
            const localCount = (snap.local_discovered || 0);
            const localTail = localCount > 0
                ? ` · ${localCount} detectados desde tu Hermes local`
                : '';
            hintEl.textContent =
                `Terminado en ${dur}s${localTail}. Catálogo sellado con fecha ${dateLabel}.`;
        }
        // Operation finished — hide the elapsed/max progress meta row
        // so it doesn't keep showing the frozen "0:02 / hasta 0:45".
        const metaEl = panel.querySelector('.refresh-panel-meta');
        if (metaEl) metaEl.style.display = 'none';
        run.successHideTimer = setTimeout(() => {
            if (_catalogRefreshGeneration !== run.generation) return;
            panel.hidden = true;
            panel.classList.remove('in-flight');
            // Restore meta visibility for the next refresh attempt.
            if (metaEl) metaEl.style.display = '';
        }, 2500);
    }

    // Re-pull /api/providers so the list repaints with the brand-new
    // snapshot. Reuse the same backend loader used on initial render —
    // it updates `LLM_PROVIDERS`, re-renders the pill, and fires any
    // filter restyling.
    try {
        if (typeof loadProvidersFromBackend === 'function') {
            await loadProvidersFromBackend();
        }
        if (typeof renderProviderList === 'function') {
            renderProviderList();
        }
        const activeProvider = selectedLLMProvider();
        if (activeProvider) {
            // Reconcile both model options and the auth matrix. The provider
            // handler preserves valid same-provider OAuth state, but turns it
            // off if the refreshed Hermes capabilities no longer support it.
            updateLLMProviderUI(activeProvider);
        }
    } catch (err) {
        console.warn('[refresh] repaint failed:', err);
    }
}

function _onCatalogRefreshError(evt, options = {}) {
    const panel = document.getElementById('provider-refresh-panel');
    if (!panel) return;
    if (options.automatic) {
        panel.hidden = true;
        panel.classList.remove('in-flight', 'error', 'info');
        const pill = document.getElementById('provider-sync-pill');
        if (pill) {
            pill.classList.add('warn');
            pill.title = `No se pudo actualizar automáticamente: ${evt.message || 'sin conexión'}. Se conserva la última lista válida.`;
        }
        return;
    }
    panel.classList.add('error');
    panel.classList.remove('in-flight');

    // Map server error codes to user-friendly strings. The UI stays
    // open so the user can read the message and hit "Cancelar" to
    // dismiss (or click the refresh button again to retry).
    const messages = {
        hermes_not_found: wizardRuntime.packaged
            ? 'El motor incluido no está disponible. Repara Iris.'
            : 'Hermes no está instalado o no está en el PATH.',
        timeout:            'El inventario de Hermes no respondió en 45 segundos. Tu lista anterior sigue activa.',
        crashed:            'Hermes devolvió un error o datos inválidos.',
        write_failed:       'No se pudo escribir el snapshot en ~/.iris/.',
        network:            'Fallo de red hablando con el wizard.',
        unsupported:        'El catálogo dinámico no está disponible en Hermes.',
    };
    const msg = messages[evt.code] || 'Error desconocido.';

    // Older Hermes builds may lack the inventory contract. Present that as
    // compatibility information while keeping the bundled fallback active.
    const isInfo = evt.code === 'unsupported';
    if (isInfo) {
        panel.classList.remove('error');
        panel.classList.add('info');
    } else {
        panel.classList.add('error');
        panel.classList.remove('info');
    }

    const titleEl = panel.querySelector('.refresh-panel-title');
    if (titleEl) titleEl.textContent = msg;
    const iconEl = panel.querySelector('.refresh-panel-icon');
    if (iconEl) setIconText(iconEl, isInfo ? 'ℹ️' : '❌');
    const hintEl = panel.querySelector('.refresh-panel-hint');
    if (hintEl) {
        hintEl.textContent = isInfo
            ? _runtimeText('catalog-fallback', 'Sigue usando el catálogo incluido con esta versión de IRIS. Actualiza Hermes y vuelve a intentarlo para obtener su inventario oficial.')
            : (evt.message || '');
    }
    // Operation finished — hide the "elapsed / hasta 0:45" row so it
    // doesn't look like the request is still timing out.
    const metaEl = panel.querySelector('.refresh-panel-meta');
    if (metaEl) metaEl.style.display = 'none';
    const bar = document.getElementById('refresh-progress-bar');
    if (bar) bar.style.width = '100%';
}

async function checkHermesOld() {
    const panel = document.getElementById('hermes-info');
    const actions = document.getElementById('hermes-actions');
    panel.hidden = false;
    panel.innerHTML = '<div class="loading-spinner"></div><p>Comprobando la conexión de IRIS…</p>';
    let hermesTestStatus = 'unknown';

    try {
        const res = await fetch('/api/test-hermes');
        const d = await res.json();
        hermesTestStatus = d.status || 'unknown';

        if (d.status === 'connected') {
            panel.innerHTML = `
                <div class="info-row ok"><wz-icon>✅</wz-icon> El servicio de IA está conectado</div>
                <div class="info-row"><span class="label">Versión</span><span class="value">${d.version || 'Detectado'}</span></div>
                <div class="info-row"><span class="label">Modelos</span><span class="value">${(d.models || []).join(', ') || 'Configurar abajo'}</span></div>
            `;
            actions.style.display = 'flex';
            document.getElementById('btn-install-hermes').style.display = 'none';
            _setGatewayEngine('running');
            _seedSetupChecklist('connected');
        } else if (d.status === 'installed') {
            panel.innerHTML = wizardRuntime.packaged ? `
                <div class="info-row warn"><wz-icon>⚠</wz-icon> Motor incluido detenido</div>
                <p class="dim" style="margin-top:4px">Tiene proveedor y modelo. Inícialo con «Iniciar IRIS», más arriba.</p>
            ` : `
                <div class="info-row warn"><wz-icon>⚠</wz-icon> El motor de IA está preparado, pero detenido</div>
                <div class="info-row"><span class="label">Ubicación</span><span class="value dim" style="font-size:10px">${escapeHtml(d.path || '')}</span></div>
            `;
            actions.style.display = 'flex';
            document.getElementById('btn-install-hermes').style.display = 'none';
            _setGatewayEngine('stopped');
            _seedSetupChecklist('installed');
        } else if (d.status === 'needs_setup') {
            panel.innerHTML = wizardRuntime.packaged ? `
                <div class="info-row ok"><wz-icon>✅</wz-icon> Motor incluido disponible</div>
            ` : `
                <div class="info-row ok"><wz-icon>✅</wz-icon> Motor de IA disponible</div>
            `;
            actions.style.display = 'flex';
            document.getElementById('btn-install-hermes').style.display = 'none';
            _setGatewayEngine('stopped');
            _seedSetupChecklist('installed');
        } else if (!wizardRuntime.canInstallHermes) {
            // Packaged (or an unknown runtime): nothing here can install Hermes.
            panel.innerHTML = `
                <div class="info-row warn"><wz-icon>🔧</wz-icon> ${wizardRuntime.packaged ? 'El motor incluido no está disponible' : 'El motor de IA no está disponible'}</div>
                <p class="dim" style="margin-top:4px">${wizardRuntime.packaged
                    ? 'Repara o reinstala Iris desde su instalador; el motor se repara con el paquete de Iris.'
                    : 'Este runtime no permite instalar Hermes desde el asistente.'}</p>
                ${d.diagnostic ? `<p class="dim" style="margin-top:4px">${escapeHtml(d.diagnostic)}</p>` : ''}
            `;
            actions.style.display = 'flex';
            _setGatewayEngine('damaged');
            _seedSetupChecklist('damaged');
        } else if (d.status === 'damaged') {
            panel.innerHTML = `
                <div class="info-row warn"><wz-icon>🔧</wz-icon> Hermes está incompleto o dañado</div>
                <p class="dim" style="margin-top:4px">${escapeHtml(d.diagnostic || 'El lanzador no puede ejecutarse.')}</p>
            `;
            actions.style.display = 'flex';
            const installBtn = document.getElementById('btn-install-hermes');
            setIconText(installBtn, '🔧 Reparar Hermes');
            installBtn.style.display = 'inline-block';
            _setGatewayEngine('damaged');
            _seedSetupChecklist('damaged');
        } else {
            panel.innerHTML = `
                <div class="info-row warn"><wz-icon>⚠</wz-icon> Hermes no está instalado</div>
                <p class="dim" style="margin-top:4px">Se instalará automáticamente con un click.</p>
            `;
            actions.style.display = 'flex';
            const installBtn = document.getElementById('btn-install-hermes');
            setIconText(installBtn, '📦 Instalar Hermes');
            installBtn.style.display = 'inline-block';
            _setGatewayEngine('missing');
            _seedSetupChecklist('missing');
        }
    } catch {
        panel.innerHTML = `
            <div class="info-row warn"><wz-icon>⚠</wz-icon> No se pudo verificar Hermes</div>
            <p class="dim">El wizard backend no está corriendo. Configura el proveedor de IA abajo.</p>
        `;
        actions.style.display = 'flex';
        _setGatewayEngine('unknown');
    }
    // The provider card already owns configuration and engine state. Diagnostics only
    // repeats the engine panel when something needs attention, never a stale setup prompt.
    panel.hidden = ['connected', 'installed', 'needs_setup'].includes(hermesTestStatus);
    void loadLlmSetup();

    // Setup LLM provider radio interactivity.
    // setupLLMProviderHandlers() was referenced but never defined — the
    // throw aborted the rest of this function (channel handlers, autostart,
    // OS precheck, security-ack, existing-config probe, extras). Guarded
    // with typeof so any future reintroduction works without re-breaking it.
    if (typeof setupLLMProviderHandlers === 'function') {
        try { setupLLMProviderHandlers(); }
        catch (e) { console.warn('setupLLMProviderHandlers threw:', e); }
    }
    if (typeof setupChannelHandlers === 'function') {
        try { setupChannelHandlers(); }
        catch (e) { console.warn('setupChannelHandlers threw:', e); }
    }
    // Reflect current autostart state (no-op if the toggle isn't mounted)
    if (typeof refreshAutostartStatus === 'function') {
        try { refreshAutostartStatus(); }
        catch (e) { console.warn('refreshAutostartStatus threw:', e); }
    }
    // Show the security acknowledgement gate (mirrors `hermes onboard`)
    // and detect any pre-existing config so we can offer reuse-or-overwrite
    // instead of silently steamrolling. Both are no-ops if their DOM isn't
    // mounted (defensive — keeps older HTML compatible).
    try {
        const ocStatus = wizardRuntime.packaged
            ? (hermesTestStatus === 'connected' ? 'connected' : 'installed')
            : document.getElementById('hermes-info')?.querySelector('.info-row.ok')
                ? 'connected'
                : (['installed', 'needs_setup'].includes(hermesTestStatus) ? 'installed' : 'missing');
        // OS / dependency precheck FIRST — its result may hard-disable
        // the install button for a real installer prerequisite, and we want that signal
        // visible before the security-ack gate kicks in.
        await runOsPrecheck(ocStatus);
        _showSecurityAckIfNeeded(ocStatus);
        checkExistingHermesConfig().catch(() => {});
    } catch {}
}

// ── Hermes Mode Selection ─────────────────────────────────

function selectHermesMode(mode) {
    selectedHermesMode = mode;
    document.querySelectorAll('.mode-card').forEach(c => {
        const input = c.querySelector('input');
        const val = input?.value;
        c.classList.toggle('selected', val === mode);
        if (input) input.checked = val === mode;
        // The Skip card may have been hijacked to 'quick' (installed-but-stopped).
        // If the user picks ANY other mode while looking at the renamed card,
        // restore its original label/desc/onclick/value so nothing stays in a
        // half-rewritten state for the next pass.
        const wasHijacked = (val === 'quick' || val === 'skip') && (input?.dataset.origValue || c.dataset.origOnclick);
        if (wasHijacked && mode !== 'quick' && mode !== 'skip') {
            const label = c.querySelector('.mode-label, .mode-card-title');
            const desc = c.querySelector('.mode-desc, .mode-card-desc');
            if (label?.dataset.origLabel) {
                label.textContent = label.dataset.origLabel;
                delete label.dataset.origLabel;
            }
            if (desc?.dataset.origDesc) {
                desc.textContent = desc.dataset.origDesc;
                delete desc.dataset.origDesc;
            }
            if (input?.dataset.origValue) {
                input.value = input.dataset.origValue;
                delete input.dataset.origValue;
            }
            if (c.dataset.origOnclick !== undefined) {
                if (c.dataset.origOnclick) {
                    c.setAttribute('onclick', c.dataset.origOnclick);
                } else {
                    c.removeAttribute('onclick');
                }
                delete c.dataset.origOnclick;
            }
        }
    });
    document.getElementById('modo-continue-btn').style.display = 'inline-block';

    const betaWarning = document.getElementById('beta-warning');
    betaWarning.style.display = (
        mode === 'full'
        && !['installed', 'needs_setup'].includes(wizardState.hermes_install_status)
    ) ? 'block' : 'none';
}

function confirmHermesMode() {
    if (!selectedHermesMode) return;

    // Optional action within IA. Entering IA itself never starts the engine.
    wizardState.hermes_mode = selectedHermesMode;

    // The provider form remains the same instance after applying an option.
    const modePanel = document.getElementById('hermes-mode-panel');
    const betaWarn = document.getElementById('beta-warning');
    if (modePanel) modePanel.style.display = 'none';
    if (betaWarn) betaWarn.style.display = 'none';

    if (selectedHermesMode === 'skip') {
        nextStep();
        return;
    }

    // Full / quick / repair operate in the visible IA section.
    currentStep = 3;
    updateUI();

    // Trigger the mode-specific action AFTER step 3 is shown so its DOM
    // (hermes-info, hermes-actions, etc.) is the active panel.
    setTimeout(() => _triggerHermesModeAction(selectedHermesMode), 100);
}

/**
 * Fire the appropriate side-effect for the chosen Hermes mode, after
 * step 3 is the active panel. Separated from confirmHermesMode() because
 * it needs the step 3 DOM to be visible (otherwise selectors return null
 * or the user can't see what's happening).
 */
async function _triggerHermesModeAction(mode) {
    const infoPanel = document.getElementById('hermes-info');
    const actions   = document.getElementById('hermes-actions');

    if (mode === 'full') {
        if (infoPanel && wizardRuntime.packaged) {
            infoPanel.innerHTML = `<div class="info-row ok"><wz-icon>🧠</wz-icon> Motor de IA incluido</div>
                <p class="dim" style="margin-top:6px">Elige proveedor y modelo. Se conserva tu configuración y no se instala nada.</p>`;
        } else if (infoPanel) {
            infoPanel.innerHTML = wizardState.hermes_install_status === 'needs_setup'
                ? `<div class="info-row ok"><wz-icon>🧠</wz-icon> Motor de IA listo para configurar</div>
                   <p class="dim" style="margin-top:6px">El lanzador funciona. Elige proveedor y modelo; se conservará la instalación.</p>`
                : wizardState.hermes_install_status === 'installed'
                    ? `<div class="info-row ok"><wz-icon>🧠</wz-icon> Configuración avanzada de IA</div>
                       <p class="dim" style="margin-top:6px">Se conservará la instalación actual. Cambia proveedor o modelo y pulsa «Reiniciar IRIS» cuando estés listo.</p>`
                : wizardState.hermes_residual_launcher
                    ? `<div class="info-row warn"><wz-icon>🧹</wz-icon> Instalación oficial no encontrada</div>
                       <p class="dim" style="margin-top:6px">Solo queda un launcher global antiguo. El instalador oficial creará un runtime nuevo y conservará los datos del perfil.</p>
                       ${wizardState.hermes_install_diagnostic ? `<p class="dim" style="margin-top:6px">${escapeHtml(wizardState.hermes_install_diagnostic)}</p>` : ''}`
                : `<div class="info-row warn"><wz-icon>🧠</wz-icon> Modo Completo (Beta) activado</div>
                   <p class="dim" style="margin-top:6px">Hermes no está instalado. Puedes iniciar la instalación oficial; el proceso puede tardar varios minutos.</p>`;
        }
        // Render the normal Hermes install panel (provider list, autostart,
        // OS precheck, etc.) so the user can hit "Instalar Hermes". Only in
        // Modo Completo does the user pick a new provider from scratch.
        if (typeof _originalCheckHermes === 'function') {
            await _originalCheckHermes();
        } else {
            if (typeof loadProvidersFromBackend === 'function') await loadProvidersFromBackend();
            if (typeof renderProviderList === 'function') renderProviderList();
        }
        if (typeof checkHermesOld === 'function') checkHermesOld();
        return;
    }

    await maybeImportHermesConfig();
    // quick / repair → hydrate the provider list AND the saved config so
    // the radios reflect what's on disk instead of stale DOM state. This
    // matters because the
    // user picked these modes expecting "use my existing setup" — we
    // must NOT overwrite that with whatever the DOM happens to default
    // to. _hydrateSavedConfigIntoDOM() reads /api/current-config and
    // checks the matching radio + populates the model dropdown + fills
    // the API-key field. After it, if the user gets a missing_credentials
    // error, the failing fields they need to fix are visible.
    try {
        if (typeof loadProvidersFromBackend === 'function') await loadProvidersFromBackend();
        if (typeof renderProviderList === 'function') renderProviderList();
        await _hydrateSavedConfigIntoDOM();
    } catch (e) { console.warn(`provider hydrate (${mode}) failed:`, e); }

    if (mode === 'repair') {
        if (wizardState.hermes_install_status === 'damaged') {
            if (infoPanel) {
                const detail = wizardState.hermes_install_diagnostic
                    ? `<p class="dim" style="margin-top:6px">${escapeHtml(wizardState.hermes_install_diagnostic)}</p>`
                    : '';
                infoPanel.innerHTML = `
                    <div class="info-row warn"><wz-icon>🔧</wz-icon> Hermes necesita reparación</div>
                    <p class="dim" style="margin-top:6px">El lanzador encontrado no funciona. El instalador oficial conservará los datos de usuario y reconstruirá el runtime.</p>
                    ${detail}
                `;
            }
            if (actions) actions.style.display = 'flex';
            const installBtn = document.getElementById('btn-install-hermes');
            if (installBtn) {
                setIconText(installBtn, '🔧 Reparar Hermes');
                installBtn.style.display = 'inline-block';
            }
            _setGatewayEngine('damaged');
            _seedSetupChecklist('damaged');
            runOsPrecheck('damaged');
            return;
        }
        if (infoPanel) {
            infoPanel.innerHTML = `
                <div class="info-row warn"><wz-icon>🔧</wz-icon> Reiniciando IRIS…</div>
                <p class="dim" style="margin-top:6px">Aplicando la configuración guardada al servicio de IA, sin cerrar tus ventanas.</p>
            `;
        }
        if (actions) actions.style.display = 'flex';
        const installBtn = document.getElementById('btn-install-hermes');
        if (installBtn) installBtn.style.display = 'none';
        // CRITICAL: never call _persistLLMSelectionBeforeGateway() here — it
        // reads from the DOM and OVERWRITES the saved config with whatever
        // radio is currently checked.
        // For repair, we want to USE the existing config, not replace it.
        _startGatewayUsingSavedConfig({ restart: true });
        return;
    }

    if (mode === 'quick') {
        if (infoPanel) {
            infoPanel.innerHTML = `
                <div class="info-row ok"><wz-icon>⚡</wz-icon> Iniciando IRIS…</div>
                <p class="dim" style="margin-top:6px">El motor está preparado. Iniciando IRIS con la configuración guardada.</p>
            `;
        }
        if (actions) actions.style.display = 'flex';
        const installBtn = document.getElementById('btn-install-hermes');
        if (installBtn) installBtn.style.display = 'none';
        // Same rule as repair: DO NOT call startGateway() here — it would
        // persist transient DOM state and could clobber the user's saved
        // provider+model+key on disk.
        _startGatewayUsingSavedConfig({ restart: false });
        return;
    }
}

/**
 * Reads /api/current-config and reflects it in the DOM:
 *   - checks the radio for wizardState.llm_provider
 *   - sets the model dropdown
 *   - fills the API-key input (if any)
 *   - toggles the OAuth checkbox
 *
 * This is the "tell me what's saved" half of _persistLLMSelectionBeforeGateway()
 * (which is the "tell disk what's in DOM" half). For quick/repair flows we
 * only want the read direction, never the write — the user picked those
 * modes because they want their existing config preserved.
 *
 * Idempotent and safe to call when no config exists (no-op).
 */
function _restoreSavedLLMModelIntoDOM() {
    const savedModel = (wizardState.llm_model || '').trim();
    const modelSelect = document.getElementById('llm-model');
    if (!savedModel || !modelSelect) return;

    if (selectedLLMProvider() === 'nous' && (MODEL_META[`nous::${savedModel}`]?.locked
        || MODEL_META[`nous::${savedModel}`]?.available === false
        || ![...modelSelect.options].some(option => option.value === savedModel))) {
        modelSelect.value = '';
        return;
    }

    if ([...modelSelect.options].some(option => option.value === savedModel)) {
        modelSelect.value = savedModel;
        return;
    }

    // A valid model can be absent from the bundled/live catalogue (custom
    // OpenRouter aliases are common). Falling back to the first option used
    // to display Claude Sonnet and could later persist it as if the user had
    // selected it. Keep the exact saved value through the existing custom
    // model control instead.
    if ([...modelSelect.options].some(option => option.value === '__custom__')) {
        modelSelect.value = '__custom__';
        const customInput = document.getElementById('llm-custom-model');
        const customSection = document.getElementById('custom-model-section');
        if (customInput) customInput.value = savedModel;
        if (customSection) customSection.style.display = 'block';
    }
}

/** A saved key never comes back to the page: without this its field looks as if it were lost. */
function _showSavedVoiceKeys(cfg) {
    for (const [id, saved] of [['key-fish-audio', cfg.has_fish_audio_api_key],
        ['key-elevenlabs', cfg.has_elevenlabs_api_key], ['key-minimax', cfg.has_minimax_api_key]]) {
        const input = document.getElementById(id);
        if (input && saved) input.placeholder = 'Clave guardada. Déjala vacía para conservarla.';
    }
}

async function _hydrateSavedConfigIntoDOM() {
    let cfg;
    try {
        const r = await fetch('/api/current-config');
        if (!r.ok) return;
        cfg = await r.json();
    } catch { return; }

    _showSavedVoiceKeys(cfg);
    if (cfg.llm_provider) wizardState.llm_provider = cfg.llm_provider;
    if (cfg.llm_model) wizardState.llm_model = cfg.llm_model;
    wizardState.llm_base_url = cfg.llm_base_url || '';
    // Availability is not selection: an old OAuth profile may coexist with
    // an explicit API-key config. Restore only the method Hermes persisted.
    wizardState.llm_use_oauth = Boolean(cfg.llm_use_oauth);

    // Force the radio to the saved provider. Clear every rendered default
    // even when the saved provider is an older/discovered slug that is not
    // currently visible; otherwise the checked DOM radio could impersonate a
    // real user choice and later overwrite Hermes with Anthropic.
    const persistedProvider = wizardState.llm_provider;
    if (persistedProvider) {
        document.querySelectorAll('input[name="llm_provider"]').forEach(r => { r.checked = false; });
        document.querySelectorAll('.provider-grid .radio-card').forEach(c => c.classList.remove('selected'));
        const radio = document.querySelector(`input[name="llm_provider"][value="${persistedProvider}"]`);
        if (radio) {
            radio.checked = true;
            radio.closest('.radio-card')?.classList.add('selected');
            // Trigger any per-provider UI updates (OAuth toggle visibility,
            // model dropdown rebuild). Mirrors what _originalCheckHermes does
            // after restoring the radio.
            if (typeof updateLLMProviderUI === 'function') {
                try { updateLLMProviderUI(persistedProvider); } catch {}
            }
        }
    }

    // Restore the model AFTER updateLLMProviderUI rebuilt the <select>.
    // Unknown/custom model ids are kept through "Otro modelo".
    _restoreSavedLLMModelIntoDOM();

    // OAuth toggle
    const oauthToggle = document.getElementById('use-oauth');
    if (oauthToggle && wizardState.llm_use_oauth) {
        oauthToggle.checked = true;
        oauthToggle.dispatchEvent(new Event('change'));
    }
    const baseUrlInput = document.getElementById('llm-base-url');
    if (baseUrlInput && wizardState.llm_base_url) {
        baseUrlInput.value = wizardState.llm_base_url;
    }
}

/**
 * Start (or restart) the gateway WITHOUT touching the saved provider
 * config. Mirrors startGateway()'s success/error handling but skips the
 * _persistLLMSelectionBeforeGateway() step.
 *
 * Used by quick / repair modes where the user's intent is "boot Hermes
 * with the config that's already on disk" — overwriting that config
 * with transient form state is data loss.
 */
async function _startGatewayUsingSavedConfig({ restart = false } = {}) {
    const log = _gatewayLog();
    const info = document.getElementById('hermes-info');
    const btn = document.getElementById('btn-start-gateway');

    if (!await _ensureEngineConsent()) {
        _engineConsentRefused(log);
        if (info) {
            // Retry the same saved-configuration action: never the form-persisting start.
            info.innerHTML = `
                <div class="info-row warn"><wz-icon>⚠</wz-icon> El motor sigue detenido</div>
                <p class="dim" style="margin-top:6px">${escapeHtml(_engineConsentBlockedReason(`${restart ? 'reiniciarlo' : 'activarlo'} con tu configuración guardada`))}</p>
                <button class="btn-primary btn-sm" type="button" style="margin-top:8px" onclick="_startGatewayUsingSavedConfig({ restart: ${restart ? 'true' : 'false'} })">${wizardRuntime.known ? 'Revisar aviso y continuar' : 'Volver a intentarlo'}</button>
            `;
        }
        return;
    }

    if (log) log.style.display = 'block';
    // Busy until the request settles: a late provider-step read must not enable it again (R05).
    if (btn) { btn.disabled = true; btn.dataset.busy = '1'; setIconText(btn, restart ? '⏳ Reiniciando IRIS…' : '⏳ Iniciando IRIS…'); }

    // Show the user what Hermes has on disk before we start so they
    // see WHICH provider/model is about to boot — and that we're NOT
    // overriding it from the wizard form. With IRISConfig.load() now
    // auto-deriving the llm_* slice from Hermes' on-disk config, this
    // is purely informational; the gateway pre-flight will read the
    // same values automatically.
    try {
        const cfgRes = await fetch('/api/current-config');
        if (cfgRes.ok) {
            const cfg = await cfgRes.json();
            const prov  = cfg.llm_provider || '(sin proveedor)';
            const model = cfg.llm_model || '(sin modelo)';
            const auth  = cfg.llm_use_oauth
                ? '🔐 OAuth'
                : (cfg.has_llm_api_key ? '🔑 API key' : '⚠️ sin credenciales');
            if (log) log.innerHTML += `<div class="log-line log-info"><wz-icon>📋</wz-icon> IRIS va a usar: <b>${escapeHtml(prov)}</b> / ${escapeHtml(model)} <span class="dim">(${iconLabelHtml(auth)})</span></div>`;
        }
    } catch (e) {
        if (log) log.innerHTML += `<div class="log-line dim">No pude leer la config actual (${escapeHtml(e.message || e)}).</div>`;
    }

    if (log) {
        const verb = restart ? 'Reiniciando' : 'Iniciando';
        log.innerHTML += `<div class="log-line log-info"><wz-icon>🚀</wz-icon> ${verb} IRIS con la configuración guardada (no se modifica nada).</div>`;
    }
    if (typeof _setSetupStep === 'function') _setSetupStep('gateway', 'active');

    const endpoint = restart ? '/api/restart-gateway' : '/api/start-gateway';

    const _dumpTail = (tail) => {
        if (!log || !tail) return;
        const pre = document.createElement('pre');
        pre.className = 'log-tail';
        pre.textContent = tail;
        log.appendChild(pre);
    };

    try {
        const res = await fetch(endpoint, { method: 'POST' });
        const d = await res.json();

        if (d.status === 'ok' || d.status === 'success' || d.status === 'running') {
            if (log) log.innerHTML += `<div class="log-line log-ok"><wz-icon>✅</wz-icon> ${escapeHtml(d.message || 'Servicio de IA listo')}</div>`;
            _setGatewayEngine('running');
            if (info) info.innerHTML = '<div class="info-row ok"><wz-icon>🟢</wz-icon> El servicio de IA está activo</div>';
            if (typeof _setSetupStep === 'function') _setSetupStep('gateway', 'done');
            if (typeof markModelStepDone === 'function') markModelStepDone();
            setTimeout(() => { if (typeof checkHermesOld === 'function') checkHermesOld(); }, 2000);
            return;
        }

        // Error path — reuse the same friendly messages as startGateway()
        // so the user can fix credentials in-place. We do NOT silently
        // fall back to startGateway() (would re-persist DOM defaults).
        const codeHints = {
            missing_api_key:     { icon: '🔑',    label: 'Falta la API key' },
            missing_oauth:       { icon: '🔐',    label: 'Falta autenticación OAuth' },
            missing_base_url:    { icon: '🌐',    label: 'Falta el endpoint del proveedor' },
            invalid_base_url:    { icon: '🌐',    label: 'El endpoint no es válido' },
            external_cli_missing:{ icon: '🧩',    label: 'Falta la CLI externa' },
            missing_credentials: { icon: '🔑',    label: 'Falta configuración' },
            gateway_crashed:     { icon: '💥',    label: 'El servicio de IA se cerró al iniciar' },
            gateway_not_listening:{icon: '⚠️',    label: 'La conexión de IA todavía no responde' },
        };
        const hint = codeHints[d.code] || { icon: '❌', label: 'No se pudo iniciar IRIS' };
        if (log) log.innerHTML += `<div class="log-line log-err">${uiIcon(hint.icon)} ${escapeHtml(d.message || hint.label)}</div>`;
        if (info) {
            info.innerHTML = `
                <div class="info-row err"><wz-icon>⚠</wz-icon> ${hint.label}</div>
                <p class="dim" style="margin-top:6px">${escapeHtml(d.message || '')}</p>
                <p class="dim" style="margin-top:6px">Tu configuración guardada quedó intacta. Si necesitas cambiar de proveedor, vuelve atrás y elige "${wizardRuntime.packaged ? 'Cambiar proveedor / modelo' : 'Modo Completo'}".</p>
            `;
        }
        _dumpTail(d.log_tail);
        _setGatewayEngine('failed', hint.label);
        if (typeof _setSetupStep === 'function') _setSetupStep('gateway', 'err');

        // For credential errors, highlight the field that needs fixing
        // (same UX as startGateway()).
        if (d.code === 'missing_api_key' || d.code === 'missing_credentials') {
            setTimeout(() => {
                const apiInput = document.getElementById('llm-api-key');
                if (apiInput) {
                    apiInput.classList.add('field-attention');
                    apiInput.focus();
                    setTimeout(() => apiInput.classList.remove('field-attention'), 4000);
                }
            }, 150);
        } else if (d.code === 'missing_base_url' || d.code === 'invalid_base_url') {
            setTimeout(() => {
                const endpointInput = document.getElementById('llm-base-url');
                if (endpointInput) {
                    endpointInput.classList.add('field-attention');
                    endpointInput.focus();
                    setTimeout(() => endpointInput.classList.remove('field-attention'), 4000);
                }
            }, 150);
        } else if (d.code === 'missing_oauth') {
            setTimeout(() => {
                const oauthBtn = document.getElementById('btn-auth-oauth');
                if (oauthBtn) {
                    oauthBtn.classList.add('field-attention');
                    oauthBtn.focus();
                    setTimeout(() => oauthBtn.classList.remove('field-attention'), 4000);
                }
            }, 150);
        }
    } catch (e) {
        if (log) log.innerHTML += `<div class="log-line log-err"><wz-icon>❌</wz-icon> Error de conexión: ${escapeHtml(e.message || e)}</div>`;
        _setGatewayEngine('failed', 'No se pudo contactar con el asistente');
        if (typeof _setSetupStep === 'function') _setSetupStep('gateway', 'err');
    } finally {
        if (btn) delete btn.dataset.busy;
        _renderGatewayGate();
    }
}

async function _originalCheckHermes() {
    // Pull the authoritative provider list from the backend. If the fetch
    // fails (wizard backend down, offline) we silently keep the hardcoded
    // fallback so the UI is still usable.
    await loadProvidersFromBackend();

    // Hydrate the user's previously-persisted LLM choice so the radios
    // reflect what's actually saved. Without this, re-entering the step
    // could expose stale form state and pair the wrong key/model/provider.
    try {
        const r = await fetch('/api/current-config');
        if (r.ok) {
            const cfg = await r.json();
            if (cfg.llm_provider) wizardState.llm_provider = cfg.llm_provider;
            if (cfg.llm_model)    wizardState.llm_model    = cfg.llm_model;
            wizardState.llm_base_url = cfg.llm_base_url || '';
            wizardState.llm_use_oauth = Boolean(cfg.llm_use_oauth);
        }
    } catch (e) { console.warn('current-config fetch for LLM hydrate failed:', e); }

    renderProviderList();

    // After render, force-select the persisted provider's radio (if it
    // exists in the rendered list) and pre-fill the model dropdown.
    const persistedProvider = wizardState.llm_provider || '';
    const radio = persistedProvider
        ? document.querySelector(`input[name="llm_provider"][value="${persistedProvider}"]`)
        : null;
    if (radio) {
        // Clear any other stale "selected" / checked state.
        document.querySelectorAll('input[name="llm_provider"]').forEach(r => { r.checked = false; });
        document.querySelectorAll('.provider-grid .radio-card').forEach(c => c.classList.remove('selected'));
        radio.checked = true;
        radio.closest('.radio-card')?.classList.add('selected');
    }

    const initial = selectedLLMProvider();
    if (initial && (!persistedProvider || radio)) {
        updateLLMProviderUI(initial);
    } else if (persistedProvider && !radio) {
        // Preserve an existing provider that this Hermes/UI version does not
        // currently advertise. Most importantly, do not reinterpret it as
        // Anthropic merely because no matching radio could be rendered.
        console.warn(`Proveedor guardado no visible en la lista: ${persistedProvider}`);
    }

    // Restore the saved model after the provider rebuilt the dropdown. If
    // the catalogue does not contain it, preserve it as a custom model.
    _restoreSavedLLMModelIntoDOM();

    // Restore the OAuth toggle state if it applies to this provider.
    const oauthToggle = document.getElementById('use-oauth');
    if (oauthToggle && wizardState.llm_use_oauth) {
        oauthToggle.checked = true;
        // Some providers re-bind a change handler — fire it so the UI
        // section visibility updates accordingly.
        oauthToggle.dispatchEvent(new Event('change'));
    }
}

/**
 * Build the auth-format badge HTML for a provider card.
 *
 * Renders one or two pills that describe how the user will authenticate
 * with this provider. Four variants:
 *
 *   • "api"        → 🔑 API Key (with the env_var name as a tooltip,
 *                    so power users know which variable Hermes will
 *                    populate — e.g. ANTHROPIC_API_KEY).
 *   • "oauth"      → 🔐 OAuth (login via browser, no API key field).
 *   • "api+oauth"  → both pills side-by-side ("o" between them) — the
 *                    user picks via the OAuth toggle in updateLLMProviderUI.
 *   • "aws"        → credentials are resolved by the AWS SDK chain.
 *   • "external"   → an installed external CLI owns authentication.
 *   • "none"       → 🏠 Local sin credencial (Ollama / vLLM / llama.cpp).
 *
 * Each pill carries a `title=` attribute so hovering reveals the
 * detailed explanation without bloating the card.
 */
function _renderAuthBadges(p) {
    const auth = p.auth || 'api';
    const envKey = p.env_key || '';
    const envHint = envKey ? ` (${escapeHtml(envKey)})` : '';

    if (auth === 'none') {
        return `<span class="auth-badge auth-badge-local" title="Servidor local — no requiere autenticación."><wz-icon>🏠</wz-icon> Local</span>`;
    }
    if (auth === 'aws') {
        return `<span class="auth-badge auth-badge-key" title="Hermes usa la cadena de credenciales de AWS SDK."><wz-icon>☁</wz-icon> Credenciales AWS</span>`;
    }
    if (auth === 'external') {
        return `<span class="auth-badge auth-badge-local" title="La autenticación pertenece a una CLI externa instalada."><wz-icon>🧩</wz-icon> CLI externa</span>`;
    }
    if (auth === 'api-optional') {
        return `<span class="auth-badge auth-badge-key" title="El endpoint puede aceptar una API key, pero también puede funcionar sin ella."><wz-icon>🔑</wz-icon> API key opcional</span>`;
    }
    if (auth === 'oauth') {
        return `<span class="auth-badge auth-badge-oauth" title="Inicio de sesión con navegador. Hermes abrirá el flujo OAuth automáticamente."><wz-icon>🔐</wz-icon> OAuth</span>`;
    }
    if (auth === 'api+oauth') {
        const apiBadge = `<span class="auth-badge auth-badge-key" title="API Key${envHint}. Pegas la clave directamente."><wz-icon>🔑</wz-icon> API Key</span>`;
        const oauthBadge = `<span class="auth-badge auth-badge-oauth" title="Inicio de sesión con navegador (alternativa a la API Key)."><wz-icon>🔐</wz-icon> OAuth</span>`;
        return `${apiBadge}<span class="auth-badge-sep">o</span>${oauthBadge}`;
    }
    // Default: "api"
    return `<span class="auth-badge auth-badge-key" title="API Key${envHint}. Pegas la clave en el siguiente paso."><wz-icon>🔑</wz-icon> API Key</span>`;
}

function renderProviderList(filter = '') {
    const container = document.getElementById('provider-list');
    if (!container) return;

    const q = filter.toLowerCase();
    // Existing or user-selected providers always win, even while a search
    // filter hides their card. A first run deliberately has no checked radio:
    // the recommendation badge is informational, not permission to configure
    // Hermes with Anthropic without an explicit click.
    // Adding another provider lists only the ones that can sit next to the main
    // provider, and nothing is checked until the user picks one.
    const adding = IrisLlmSetup.view(llmSetup, llmView) === 'add';
    const selectedProvider = adding
        ? (document.querySelector('input[name="llm_provider"]:checked')?.value || '')
        : (wizardState.llm_provider
            || document.querySelector('input[name="llm_provider"]:checked')?.value
            || '');
    const listed = new Set(IrisLlmSetup.listedProviders(Object.keys(LLM_PROVIDERS), llmSetup, llmView));
    let html = '';
    let lastCategory = '';

    for (const [key, p] of Object.entries(LLM_PROVIDERS)) {
        if (!listed.has(key)) continue;
        // Filter check
        if (q && !String(p.name || '').toLowerCase().includes(q) && !key.includes(q) && !String(p.category || '').includes(q)) continue;

        // Category header
        if (p.category !== lastCategory) {
            lastCategory = p.category;
            html += `<div class="provider-category">${iconLabelHtml(PROVIDER_CATEGORIES[p.category] || p.category)}</div>`;
        }

        const recommendedProvider = wizardState.recommendations?.inference?.provider || '';
        const isRecommended = Boolean(recommendedProvider && recommendedProvider === key);
        const isSelected = key === selectedProvider;
        const meta = PROVIDER_META[key] || {};
        // Badges combine in a predictable order: "Recomendado" first,
        // "🆕 Nuevo" for discovered providers, and "✓ Live" when the
        // curated entry was confirmed by Hermes this session.
        const badges = [];
        if (isRecommended) badges.push('<span class="badge">Recomendado</span>');
        if (meta.discovered) {
            badges.push('<span class="badge badge-new" title="Detectado automáticamente en tu Hermes">🆕 Nuevo</span>');
        } else if (meta.hermes_detected) {
            badges.push('<span class="badge badge-live" title="Confirmado por tu Hermes instalado"><wz-icon>✓</wz-icon> Live</span>');
        }
        const badge = badges.join(' ');
        const authBadges = _renderAuthBadges(p);

        html += `
            <label class="radio-card ${isSelected ? 'selected' : ''}" data-value="${escapeHtml(key)}">
                <input type="radio" name="llm_provider" value="${escapeHtml(key)}" ${isSelected ? 'checked' : ''}>
                <div class="radio-content">
                    <div class="radio-header">
                        <strong><span class="provider-monogram" aria-hidden="true">${escapeHtml(String(p.name || key).slice(0, 2).toUpperCase())}</span>${escapeHtml(p.name)}</strong>
                        ${badge}
                    </div>
                    <div class="auth-badges">${authBadges}</div>
                    <span>${escapeHtml(p.hint)}</span>
                </div>
            </label>
        `;
    }

    container.innerHTML = html;

    // The label already gives us native radio-card click behaviour. Binding
    // an additional click handler here used to call updateLLMProviderUI twice
    // (once manually and once through the radio's change event).
    container.querySelectorAll('input[name="llm_provider"]').forEach(radio => {
        radio.addEventListener('change', () => {
            if (!radio.checked) return;
            container.querySelectorAll('.radio-card').forEach(c => c.classList.remove('selected'));
            radio.closest('.radio-card')?.classList.add('selected');
            updateLLMProviderUI(radio.value);
        });
    });
}

function filterProviders() {
    const q = document.getElementById('provider-search')?.value || '';
    renderProviderList(q);
}

function _renderProviderWarning(provider) {
    // Mirror the per-provider cautions `hermes onboard` displays right
    // before the user confirms a Model/auth provider (Gemini CLI account
    // suspension warning, etc.). Pulled from PROVIDER_META.warning which
    // the backend fills from PROVIDER_WARNINGS in providers.py.
    const box = document.getElementById('provider-warning');
    if (!box) return;
    const warn = (PROVIDER_META[provider] || {}).warning;
    if (!warn) {
        box.style.display = 'none';
        box.innerHTML = '';
        return;
    }
    const level = (warn.level || 'info').replace(/[^a-z]/g, '');
    box.className = `provider-warning level-${level}`;
    box.innerHTML = `
        <div class="pw-title">${level === 'danger' ? '<wz-icon>🛑</wz-icon>' : level === 'warn' ? '<wz-icon>⚠</wz-icon>' : 'ℹ️'} ${escapeHtml(warn.title || 'Aviso')}</div>
        <div class="pw-body">${escapeHtml(warn.body || '').replace(/\n/g, '<br>')}</div>
    `;
    box.style.display = 'block';
}

// Q03: providers can list hundreds of models; a long list gets a filter instead of a cut.
const MODEL_FILTER_MIN = 12;

function _setProviderModelOptions(provider, useOAuth = false, preserveSelection = true) {
    const modelSelect = document.getElementById('llm-model');
    if (!modelSelect) return null;
    const previous = modelSelect.value;
    const oauthModels = LLM_OAUTH_MODELS[provider] || [];
    const hasSeparateOAuthCatalog = !!LLM_OAUTH_CATALOG[provider];
    const access = typeof _providerModelsFor === 'function' ? _providerModelsFor(provider, useOAuth) : null;
    const models = access ? access.models : useOAuth && hasSeparateOAuthCatalog
        ? oauthModels
        : (LLM_MODELS[provider] || []);
    const nous = provider === 'nous';
    const locked = m => { if (access) return false; const meta = MODEL_META[`${provider}::${m.value}`] || {}; return !!meta.locked || (nous && meta.available === false); };
    const selectable = models.filter(m => !locked(m));
    const availability = document.getElementById('llm-model-availability');
    if (availability) {
        availability.hidden = !nous || !!access;
        availability.textContent = !models.length ? 'Conecta tu cuenta y actualiza el catálogo para consultar sus modelos.'
            : selectable.length ? `${selectable.length} modelos disponibles para tu cuenta · ${models.length - selectable.length} fuera de tu plan. Los bloqueados se muestran al final.`
                : 'Conecta tu cuenta o actualiza el catálogo para comprobar los modelos incluidos en tu plan.';
    }
    modelSelect.disabled = nous && !selectable.length;
    const filter = document.getElementById('llm-model-filter');
    if (filter && filter.dataset.provider !== provider) {
        filter.value = '';
        filter.dataset.provider = provider;
    }
    if (filter) {
        filter.hidden = models.length <= MODEL_FILTER_MIN;
        filter.placeholder = nous ? `Buscar modelos · ${selectable.length} disponibles de ${models.length}` : `Buscar entre ${models.length} modelos…`;
    }
    const query = filter && !filter.hidden ? filter.value.trim().toLowerCase() : '';
    // The chosen model always stays listed, whatever the filter says.
    let shown = query
        ? models.filter(m => m.value === previous || `${m.label} ${m.value}`.toLowerCase().includes(query))
        : models;
    if (nous) shown = [...shown.filter(m => !locked(m)), ...shown.filter(locked)];
    modelSelect.innerHTML = (nous || access ? '<option value="" disabled>Elige un modelo disponible para tu cuenta</option>' : '') + shown.map(m => {
        const meta = MODEL_META[`${provider}::${m.value}`] || {};
        const bits = [];
        if (meta.context && meta.context >= 1000) {
            bits.push(`${Math.round(meta.context / 1000)}K`);
        }
        if (meta.input && meta.input !== 'text') {
            bits.push(meta.input.replace('text+', '+'));
        }
        const suffix = bits.length ? ` — ${bits.join(', ')}` : '';
        // Q04: a model the account's plan cannot use is shown, but cannot be picked.
        const lock = !locked(m) ? '' : meta.lockReason === 'auth' || (!meta.locked && meta.available === false) ? ' · conecta tu cuenta' : meta.lockReason === 'pending' ? ' · comprobando tu plan' : ' · no incluido en tu plan';
        const label = ['opencode-zen', 'opencode-go'].includes(provider) && m.free
            ? `${m.label.replace(/\s*·\s*Gratis$/i, '')} · Gratis solo en OpenCode`
            : m.label;
        return `<option value="${escapeHtml(m.value)}"${locked(m) ? ' disabled' : ''}>${escapeHtml(label)}${escapeHtml(suffix)}${lock}</option>`;
    }).join('') + (nous ? '' : '<option value="__custom__">Otro modelo (escribir manualmente)</option>');
    if (nous || access) {
        modelSelect.value = preserveSelection && selectable.some(m => m.value === previous) ? previous : '';
    } else if (preserveSelection && [...modelSelect.options].some(option => option.value === previous)) {
        modelSelect.value = previous;
    } else if (preserveSelection && previous && previous !== '__custom__') {
        // A refresh may legitimately remove the configured model. Never jump
        // silently to the first new option: preserve the exact saved/user
        // value through the existing manual-model path until they choose.
        modelSelect.value = '__custom__';
        const customInput = document.getElementById('llm-custom-model');
        const customSection = document.getElementById('custom-model-section');
        if (customInput) customInput.value = previous;
        if (customSection) customSection.style.display = 'block';
    }
    return modelSelect;
}

function filterModelOptions() {
    _setProviderModelOptions(selectedLLMProvider(), Boolean(document.getElementById('use-oauth')?.checked), true);
}

function updateLLMProviderUI(provider) {
    const providerInfo = LLM_PROVIDERS[provider] || {};
    _renderProviderWarning(provider);

    // OAuth-specific adapters (notably openai-codex) can expose a
    // different set of models from the provider's API-key adapter.
    // Adding another provider never touches the main one's state.
    const adding = IrisLlmSetup.view(llmSetup, llmView) === 'add';
    const sameProvider = !adding && wizardState.llm_provider === provider;
    const auth = providerInfo.auth || 'api';
    const isLocal = auth === 'none';
    const isManagedAuth = auth === 'aws' || auth === 'external';
    const isOAuthOnly = auth === 'oauth';
    const isDualAuth = auth === 'api+oauth';
    const keepOAuth = isOAuthOnly || (
        isDualAuth
        && sameProvider
        && Boolean(document.getElementById('use-oauth')?.checked)
    );
    const modelSelect = _setProviderModelOptions(provider, keepOAuth, sameProvider);
    if (!modelSelect) return;

    if (!sameProvider) {
        // OAuth belongs to the provider that started it. Cancelling the session
        // stops its private Hermes worker,
        // so a late OpenAI result cannot mark Anthropic (or vice versa) as
        // authenticated after the user switches cards.
        _closeOAuthStream();
        wizardState.oauth_authenticated = false;
        wizardState.oauth_authenticated_provider = '';
        const oauthPanel = document.getElementById('oauth-flow-panel');
        const oauthLog = document.getElementById('oauth-flow-log');
        const oauthUrl = document.getElementById('oauth-url-box');
        if (oauthPanel) oauthPanel.style.display = 'none';
        if (oauthLog) oauthLog.innerHTML = '';
        if (oauthUrl) { oauthUrl.style.display = 'none'; oauthUrl.innerHTML = ''; }
        const providerKey = document.getElementById('llm-api-key');
        if (providerKey) providerKey.value = '';
    }

    // Show/hide API key section
    const keySection = document.getElementById('llm-key-section');
    const keyInput = document.getElementById('llm-api-key');
    const modelSection = document.getElementById('llm-model-section');
    const ollamaSection = document.getElementById('ollama-section');
    const oauthSection = document.getElementById('llm-oauth-section');
    const baseUrlSection = document.getElementById('llm-base-url-section');
    const baseUrlInput = document.getElementById('llm-base-url');
    const baseUrlHint = document.getElementById('llm-base-url-hint');
    const customModelSection = document.getElementById('custom-model-section');

    // Visibility matrix for each auth mode:
    //   "none"       → local server with no auth (Ollama, vLLM, llama.cpp).
    //   "api"        → API Key only (Anthropic, OpenAI, etc.).
    //   "oauth"      → OAuth only (Codex login, Google Antigravity, Gemini CLI).
    //                   We hide the key section completely AND auto-flip the
    //                   OAuth toggle ON so the action button is reachable
    //                   without an extra click.
    //   "api+oauth"  → user picks via the toggle (GitHub Copilot).
    keySection.style.display = (
        isLocal || isOAuthOnly || isManagedAuth || (isDualAuth && keepOAuth)
    ) ? 'none' : 'block';
    if (keyInput) {
        keyInput.required = auth === 'api' || (auth === 'api+oauth' && !keepOAuth);
    }
    const showBaseUrl = Boolean(providerInfo.base_url_required || providerInfo.base_url);
    if (baseUrlSection) baseUrlSection.style.display = showBaseUrl ? 'block' : 'none';
    if (baseUrlInput) {
        const savedEndpoint = sameProvider ? (wizardState.llm_base_url || '') : '';
        baseUrlInput.value = savedEndpoint || providerInfo.base_url || '';
        baseUrlInput.required = Boolean(providerInfo.base_url_required);
    }
    if (baseUrlHint) {
        baseUrlHint.textContent = providerInfo.base_url_required
            ? 'Obligatorio para este proveedor'
            : 'Puedes cambiar el endpoint detectado';
    }
    const detectedOllamaModels = provider === 'ollama-local'
        ? (wizardState.local_model_servers?.ollama?.models || [])
        : [];
    const useDetectedOllama = detectedOllamaModels.length > 0;
    modelSection.style.display = useDetectedOllama ? 'none' : 'block';
    ollamaSection.style.display = useDetectedOllama ? 'block' : 'none';
    if (provider === 'ollama-local') {
        const ollamaSelect = document.getElementById('ollama-model');
        if (ollamaSelect && useDetectedOllama) {
            ollamaSelect.replaceChildren(...detectedOllamaModels.map(model => {
                const option = document.createElement('option');
                option.value = model;
                option.textContent = `${model} (detectado)`;
                return option;
            }));
        }
    }
    oauthSection.style.display = (isDualAuth || isOAuthOnly) ? 'block' : 'none';
    if (customModelSection) {
        customModelSection.style.display = (
            !useDetectedOllama && modelSelect.value === '__custom__'
        ) ? 'block' : 'none';
    }
    if (!sameProvider) {
        const customModelInput = document.getElementById('llm-custom-model');
        if (customModelInput) customModelInput.value = '';
    }

    // For OAuth-only providers, lock the toggle ON and reveal the
    // auth button immediately. For dual auth, the toggle defaults
    // OFF (API Key path) and the user can switch. For API-only or
    // local, reset the toggle so it's not stuck in a stale state.
    const oauthToggle = document.getElementById('use-oauth');
    const oauthAction = document.getElementById('llm-oauth-action-section');
    if (oauthToggle) {
        if (isOAuthOnly) {
            oauthToggle.checked = true;
            oauthToggle.disabled = true;
            if (oauthAction) oauthAction.style.display = 'block';
            wizardState.use_oauth = true;
        } else if (isDualAuth) {
            oauthToggle.disabled = false;
            oauthToggle.checked = keepOAuth;
            if (oauthAction) oauthAction.style.display = keepOAuth ? 'block' : 'none';
            wizardState.use_oauth = keepOAuth;
        } else {
            oauthToggle.disabled = false;
            oauthToggle.checked = false;
            if (oauthAction) oauthAction.style.display = 'none';
            wizardState.use_oauth = false;
        }
    }
    _syncOAuthChoice();

    // Update API-key help on every switch. Leaving this conditional on a URL
    // leaked the previous card's text/link into custom endpoints (for example
    // "Spawns copilot --acp" appeared above the custom API-key field).
    const keyLink = document.getElementById('llm-key-link');
    const keyHint = document.getElementById('llm-key-hint');
    if (keyHint) {
        keyHint.textContent = auth === 'api-optional'
            ? 'Opcional: déjala vacía si tu endpoint no usa autenticación'
            : (providerInfo.hint || 'Pega la API key del proveedor seleccionado');
    }
    if (keyLink) {
        if (providerInfo.url && !isManagedAuth) {
            keyLink.href = providerInfo.url;
            keyLink.style.display = 'inline-flex';
        } else {
            keyLink.removeAttribute('href');
            keyLink.style.display = 'none';
        }
    }

    // Update radio card selection visuals
    document.querySelectorAll('.provider-grid .radio-card').forEach(c => c.classList.remove('selected'));
    const selectedCard = document.querySelector(`.provider-grid .radio-card[data-value="${provider}"]`);
    if (selectedCard) selectedCard.classList.add('selected');

    if (!adding) wizardState.llm_provider = provider;
    _resumeProviderLogin(provider);
    _applyLlmViewToForm();

    // Handle the custom-model field and checklist in one handler. The old
    // implementation assigned `onchange` twice, silently replacing the
    // checklist update with the custom-field toggle.
    modelSelect.onchange = () => {
        if (modelSelect.value === '__custom__') {
            if (customModelSection) customModelSection.style.display = 'block';
        } else {
            if (customModelSection) customModelSection.style.display = 'none';
        }
        if (typeof markModelStepDone === 'function') markModelStepDone();
    };
    const customModelInput = document.getElementById('llm-custom-model');
    if (customModelInput) {
        customModelInput.oninput = () => {
            if (typeof markModelStepDone === 'function') markModelStepDone();
        };
    }
    if (baseUrlInput) {
        baseUrlInput.oninput = () => {
            invalidateProviderModels();
            wizardState.llm_base_url = baseUrlInput.value.trim();
            if (typeof markModelStepDone === 'function') markModelStepDone();
        };
    }
    if (typeof _renderProviderModelsAccess === 'function') _renderProviderModelsAccess();
}

function toggleOAuthMode() {
    if (typeof invalidateProviderModels === 'function') invalidateProviderModels();
    const useOAuth = document.getElementById('use-oauth').checked;
    const keySection = document.getElementById('llm-key-section');
    const authSection = document.getElementById('llm-oauth-action-section');
    const keyInput = document.getElementById('llm-api-key');
    if (useOAuth) {
        keySection.style.display = 'none';
        if (authSection) authSection.style.display = 'block';
    } else {
        _closeOAuthStream();
        wizardState.oauth_authenticated = false;
        wizardState.oauth_authenticated_provider = '';
        keySection.style.display = 'block';
        if (authSection) authSection.style.display = 'none';
    }
    // Adding another provider never changes how the main one authenticates.
    if (IrisLlmSetup.view(llmSetup, llmView) !== 'add') {
        wizardState.use_oauth = useOAuth;
        wizardState.llm_use_oauth = useOAuth;
    }
    const provider = document.querySelector('input[name="llm_provider"]:checked')?.value || wizardState.llm_provider || '';
    const authMode = (LLM_PROVIDERS[provider] || {}).auth || 'api';
    if (keyInput) keyInput.required = !useOAuth && ['api', 'api+oauth'].includes(authMode);
    // This is an explicit adapter change, not a background refresh. An API
    // model absent from the OAuth adapter must not survive as a manual value.
    _setProviderModelOptions(provider, useOAuth, false);
    const customModelSection = document.getElementById('custom-model-section');
    if (customModelSection) {
        customModelSection.style.display = document.getElementById('llm-model')?.value === '__custom__' ? 'block' : 'none';
    }
    if (typeof markModelStepDone === 'function') markModelStepDone();
    _syncOAuthChoice();
    _applyLlmViewToForm();
}

// QA12: the sign-in is a visible «Conectar cuenta», not a checkbox to find first. A dual
// provider offers it next to its API key and, once chosen, «Usar API key» to go back;
// an OAuth-only provider shows just its login.
function _syncOAuthChoice() {
    const dual = (LLM_PROVIDERS[selectedLLMProvider()] || {}).auth === 'api+oauth';
    const useOAuth = Boolean(document.getElementById('use-oauth')?.checked);
    const offer = document.getElementById('oauth-offer');
    if (offer) offer.hidden = !dual || useOAuth;
    const useKey = document.getElementById('btn-oauth-use-key');
    if (useKey) useKey.hidden = !dual || !useOAuth;
}

// Provider login UI lives in provider_login_ui.js.

// Dynamic channel configuration lives in messaging.js.

// ── Security acknowledgement gate (mirrors `onboard`'s first prompt) ──
//
// `hermes onboard` forces the user through a security disclaimer
// before doing anything. We do the same — the install button stays
// disabled until #oc-security-check is ticked. State persists in
// localStorage so the user doesn't have to re-ack every time they
// reopen the wizard.

const _OC_ACK_KEY = 'IRIS.oc.security_acked';
// Packaged: the answer lives in the profile (`hermes_security_acknowledged`),
// because Iris Desktop serves this page from a new origin on every launch.
// Development keeps the browser flag that gates its external installer.
let _engineConsent = false;
let _engineConsentPrompt = null;
let _engineConsentFinish = null;

function _isSecurityAcked() {
    if (wizardRuntime.packaged) return _engineConsent;
    try { return localStorage.getItem(_OC_ACK_KEY) === '1'; }
    catch { return false; }
}

async function _saveEngineConsent(accepted) {
    const response = await fetch('/api/hermes-security-ack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ accepted }),
    });
    if (!response.ok) throw new Error('No se pudo guardar la respuesta al aviso de seguridad.');
    _engineConsent = accepted;
}

function onSecurityAckToggle(ev) {
    const checked = !!ev.target.checked;
    try { localStorage.setItem(_OC_ACK_KEY, checked ? '1' : '0'); } catch {}
    _applySecurityAckState(checked);
    if (!wizardRuntime.packaged) return;
    _saveEngineConsent(checked).catch(() => {
        ev.target.checked = _engineConsent;
        _applySecurityAckState(_engineConsent);
    });
}

// Ask with the same notice text shown in the Hermes step. Resolves true only
// after an explicit "accept"; "Ahora no", Escape or any other close resolves
// false. The buttons settle the answer themselves: an embedded webview may
// never deliver the dialog's `close` event, which is only a fallback here.
function _promptEngineConsent() {
    const dialog = document.getElementById('engine-consent-dialog');
    if (!dialog || typeof dialog.showModal !== 'function') return Promise.resolve(false);
    if (_engineConsentPrompt && dialog.open) return _engineConsentPrompt;
    // A previous prompt whose dialog is gone counts as declined, never as pending.
    if (_engineConsentFinish) _engineConsentFinish(false);
    const notice = document.querySelector('#oc-security-ack .security-ack-body');
    const body = document.getElementById('engine-consent-body');
    if (notice && body) body.replaceChildren(...[...notice.childNodes].map(node => node.cloneNode(true)));
    const buttons = [...dialog.querySelectorAll('button[value]')];
    _engineConsentPrompt = new Promise((resolve) => {
        const onClick = (event) => {
            event.preventDefault();
            finish(event.currentTarget.value === 'accept');
        };
        const onCancel = () => finish(false);
        const onClose = () => finish(dialog.returnValue === 'accept');
        function finish(accepted) {
            if (_engineConsentFinish !== finish) return;
            _engineConsentFinish = null;
            _engineConsentPrompt = null;
            buttons.forEach(button => button.removeEventListener('click', onClick));
            dialog.removeEventListener('cancel', onCancel);
            dialog.removeEventListener('close', onClose);
            if (dialog.open) dialog.close(accepted ? 'accept' : 'cancel');
            resolve(accepted);
        }
        _engineConsentFinish = finish;
        buttons.forEach(button => button.addEventListener('click', onClick));
        dialog.addEventListener('cancel', onCancel);
        dialog.addEventListener('close', onClose);
        dialog.returnValue = '';
        dialog.showModal();
    });
    return _engineConsentPrompt;
}

// Shared guard for every Wizard path that starts or restarts the bundled
// engine. Runs before anything is persisted or requested. Only a confirmed
// development runtime skips the notice: if /api/runtime-info could not be
// read, it is asked once more and, while the mode stays unknown, nothing starts.
async function _ensureEngineConsent() {
    if (!wizardRuntime.known) await loadWizardRuntime();
    if (!wizardRuntime.known) return false;
    if (wizardRuntime.mode === 'development' || _engineConsent) return true;
    if (!await _promptEngineConsent()) return false;
    try {
        await _saveEngineConsent(true);
    } catch {
        return false;
    }
    const check = document.getElementById('oc-security-check');
    if (check) check.checked = true;
    _applySecurityAckState(true);
    return true;
}
globalThis.irisEnsureEngineConsent = _ensureEngineConsent;

// Why a guarded start did not happen, for the caller's own message.
function _engineConsentBlockedReason(action = 'continuar') {
    return wizardRuntime.known
        ? `Acepta el aviso de seguridad de IRIS para ${action}.`
        : 'No se pudo comprobar el tipo de instalación, así que el motor no se inició. Vuelve a intentarlo en unos segundos.';
}
globalThis.irisEngineConsentBlockedReason = _engineConsentBlockedReason;

function _engineConsentRefused(log) {
    const ack = document.getElementById('oc-security-ack');
    if (ack && wizardRuntime.known) ack.style.display = '';
    if (log) {
        log.style.display = 'block';
        _appendInstallLog(log, `⚠️ ${_engineConsentBlockedReason()}`, 'log-warn', { label: true });
    }
}

function _applySecurityAckState(acked) {
    const box = document.getElementById('oc-security-ack');
    const installBtn = document.getElementById('btn-install-hermes');
    if (box) box.classList.toggle('acked', acked);
    if (installBtn) {
        // OS precheck wins: if it hard-blocked the button (a required
        // platform tool is missing), don't re-enable just because the user ticked the
        // ack checkbox — they still can't install.
        const osBlocked = installBtn.dataset.blockedByOs === '1';
        installBtn.disabled = osBlocked || !acked;
        installBtn.title = osBlocked
            ? (installBtn.title || 'Falta una dependencia obligatoria.')
            : (acked ? '' : 'Marca la casilla de seguridad primero');
        installBtn.style.opacity = (osBlocked || !acked) ? '0.5' : '';
    }
}

// ── OS / dependency precheck (mirrors `hermes onboard`'s "Windows
// detected — runs great on WSL2!" hint) ──
//
// Hits /api/detect-dependencies and renders the result inside
// #oc-os-precheck. Soft-blocks the install button only when a prerequisite
// the official installer cannot provision is missing.
//
// Status values from the backend:
//   ready            — green   — install away
//   blocked          — red     — a genuine prerequisite is missing

async function runOsPrecheck(installState) {
    const box        = document.getElementById('oc-os-precheck');
    const titleEl    = document.getElementById('oc-os-title');
    const badgeEl    = document.getElementById('oc-os-badge');
    const hintEl     = document.getElementById('oc-os-hint');
    const iconEl     = document.getElementById('oc-os-icon');
    const depList    = document.getElementById('oc-os-deps');
    if (!box) return null;
    if (!wizardRuntime.canInstallHermes) {
        box.style.display = 'none';
        return null;
    }

    // If Hermes is already running, the precheck is informational at
    // best — collapse the panel so we don't yell at users who are clearly
    // past this step.
    if (installState === 'connected') {
        box.style.display = 'none';
        return null;
    }
    box.style.display = '';

    try {
        const res  = await fetch('/api/detect-dependencies');
        const data = await res.json();

        // The backend wraps tool results in `data.deps.<name>` and
        // returns a top-level `all_required_ok` + `platform`. Earlier
        // versions read a flat shape (`data.node`, `data.hermes`)
        // which silently always evaluated to undefined → every tool
        // showed "no encontrado" even when present.
        const deps     = data.deps || {};
        const platform = (data.platform || '').toLowerCase();
        const isWindows = platform === 'windows';
        const allOk    = !!data.all_required_ok;
        const support  = data.platform_support || {};

        // Classify the whole panel from the per-tool results.
        let cls, badge, icon;
        if (allOk) {
            const limited = support.status === 'best_effort';
            cls = limited ? 'status-warn' : 'status-ready';
            badge = support.tier || (limited ? 'Soporte limitado' : 'OK');
            icon = limited ? '⚠️' : '✅';
        } else {
            cls = 'status-error'; icon = '⛔';
            const missing = Object.entries(deps)
                .filter(([_, v]) => v && v.required && v.status !== 'ok')
                .map(([k]) => k);
            badge = missing.length
                ? `Falta ${missing[0]}${missing.length > 1 ? ` (+${missing.length - 1})` : ''}`
                : 'Faltan herramientas';
        }
        box.classList.remove('status-ready', 'status-warn', 'status-error');
        if (cls) box.classList.add(cls);

        if (iconEl)  setIconText(iconEl, icon);
        if (badgeEl) badgeEl.textContent = badge;
        if (titleEl) {
            const sys = isWindows ? 'Windows' : (data.platform || 'Sistema');
            titleEl.textContent = `Entorno detectado: ${sys}`;
        }
        if (hintEl) {
            hintEl.textContent = data.hint || (allOk
                ? 'Entorno listo para el instalador oficial.'
                : 'Faltan dependencias para instalar Hermes. Mira la lista de abajo.');
        }

        const dependencyLabel = (dep) => {
            const ok = dep && dep.status === 'ok';
            const managed = dep && (dep.status === 'managed' || dep.managed_by_installer);
            return ok
                ? (dep.version || dep.path || 'instalado')
                : managed
                    ? 'lo gestiona Hermes'
                    : dep?.status === 'pending'
                        ? 'pendiente'
                        : '— no encontrado';
        };
        if (depList) {
            depList.replaceChildren();
            Object.entries(deps).forEach(([key, dep]) => {
                if (!dep) return;
                const ok = dep.status === 'ok';
                const managed = dep.status === 'managed' || dep.managed_by_installer;
                const item = document.createElement('li');
                item.dataset.dependency = key;
                if (dep.detail) item.title = dep.detail;

                const name = document.createElement('span');
                name.className = 'dep-name';
                name.textContent = dep.name || key;

                const value = document.createElement('span');
                value.className = 'dep-val';
                value.textContent = dependencyLabel(dep);
                value.classList.toggle('dep-missing', !ok && !managed && dep.status !== 'pending');
                value.classList.toggle('dep-ok', !!ok);
                value.classList.toggle('dep-managed', !!managed);

                item.append(name, value);
                depList.appendChild(item);
            });
        }

        // ── install button gating ────────────────────────────────
        // The official installer provisions Python, Node and uv. Only
        // platform/shell/Git/curl/xz blockers disable the button here.
        // Security acknowledgement is a separate gate handled below.
        const installBtn = document.getElementById('btn-install-hermes');
        if (installBtn) {
            const hardBlock = !data.all_required_ok;
            if (hardBlock) {
                installBtn.disabled = true;
                installBtn.title = data.hint || 'Falta una dependencia obligatoria.';
                installBtn.style.opacity = '0.5';
                installBtn.dataset.blockedByOs = '1';
            } else {
                delete installBtn.dataset.blockedByOs;
                _applySecurityAckState(_isSecurityAcked());
            }
        }
        return data;
    } catch (err) {
        // Backend down or endpoint missing — fail soft. Show a neutral
        // message and don't block install.
        box.classList.remove('status-ready', 'status-warn', 'status-error');
        if (titleEl) titleEl.textContent = 'No se pudo comprobar el entorno';
        if (badgeEl) badgeEl.textContent = '?';
        if (hintEl)  hintEl.textContent  = 'El backend del wizard no respondió. Continúa bajo tu propio criterio.';
        return null;
    }
}

function _showSecurityAckIfNeeded(installState) {
    // Only show on systems where Hermes isn't running yet — if the
    // gateway is already connected the user has clearly past this point.
    const box = document.getElementById('oc-security-ack');
    const cb  = document.getElementById('oc-security-check');
    if (!box || !cb) return;
    if (installState === 'connected') {
        box.style.display = 'none';
        return;
    }
    box.style.display = '';
    const acked = _isSecurityAcked();
    cb.checked = acked;
    _applySecurityAckState(acked);
}

// ── Existing config detect (mirrors `onboard`'s "config handling") ──
//
// If `~/.hermes/hermes.json` already has a model configured, ask
// whether to reuse those values or reconfigure from scratch. Skip
// silently if there's nothing to reuse.

// Hermes auto-creates its own config on install — no "existing config
// detected" banner needed. Kept as a no-op so existing call sites
// don't crash.
async function checkExistingHermesConfig() {
    const box = document.getElementById('oc-existing-config');
    if (box) box.style.display = 'none';
}

function reuseExistingConfig() {
    const box = document.getElementById('oc-existing-config');
    if (!box) return;
    const primary = box.dataset.primary || '';
    if (primary && primary.includes('/')) {
        // Pre-select provider+model in the UI so the user just clicks Next.
        const [providerSlug, ...modelParts] = primary.split('/');
        const modelId = modelParts.join('/');
        // Translate hermes_id back to wizard id when they differ
        // (e.g. amazon-bedrock → bedrock). PROVIDER_META is keyed by
        // wizard id; we look up by either field.
        let wizardId = providerSlug;
        for (const [wid, meta] of Object.entries(PROVIDER_META || {})) {
            if (wid === providerSlug) { wizardId = wid; break; }
        }
        const radio = document.querySelector(`input[name="llm_provider"][value="${wizardId}"]`);
        if (radio) {
            radio.checked = true;
            updateLLMProviderUI(wizardId);
        }
        const modelSelect = document.getElementById('llm-model');
        if (modelSelect) {
            // Try exact match first; fall back to leaving the dropdown alone.
            const opt = Array.from(modelSelect.options).find(o => o.value === modelId);
            if (opt) modelSelect.value = modelId;
        }
    }
    box.style.display = 'none';
    // Treat existing config as enough to mark step 3 done; user can still
    // change before clicking Next.
    if (typeof markModelStepDone === 'function') markModelStepDone();
}

function overwriteExistingConfig() {
    const box = document.getElementById('oc-existing-config');
    if (box) box.style.display = 'none';
    // Nothing else to do — the next save will overwrite the file.
}

// ── Hermes setup pipeline (replaces interactive `hermes onboard`) ──
//
// The 5-step checklist in index.html (#hermes-checklist) reflects the
// non-interactive equivalent of `hermes onboard --install-daemon`:
//
//   1. install   — official Hermes installer       (installHermes)
//   2. setup     — prepare local workspace          (runHermesSetup)
//   3. model     — provider+model+API key saved      (markModelStepDone)
//   4. daemon    — hermes daemon install (opt.)    (autostart toggle)
//   5. gateway   — hermes gateway started          (startGateway)
//
// Each step's pill flips through pending → active → done/err so the user
// always sees what's happening and where it stopped if anything fails.

function _setSetupStep(stepKey, status) {
    // status: 'pending' | 'active' | 'done' | 'err' | 'skipped'
    const el = document.getElementById(`oc-step-${stepKey}`);
    if (!el) return;
    el.classList.remove('pending', 'active', 'done', 'err', 'skipped');
    el.classList.add(status);
    const statusEl = el.querySelector('.step-status');
    if (statusEl) {
        setIconText(statusEl, {
            pending: '⏳',
            active:  '⚙️',
            done:    '✅',
            err:     '❌',
            skipped: '⏭️',
        }[status] || '⏳');
    }
}

function _showSetupChecklist() {
    const list = document.getElementById('hermes-checklist');
    if (list) list.style.display = '';
    const installText = document.querySelector('#oc-step-install .step-text');
    if (installText) installText.textContent = _runtimeText('setup-install', 'Instalar Hermes con el instalador oficial');
    const setupText = document.querySelector('#oc-step-setup .step-text');
    if (setupText) setupText.textContent = _runtimeText('setup-setup', 'Preparar workspace de Hermes');
}

function selectedLLMProvider() {
    return document.querySelector('input[name="llm_provider"]:checked')?.value
        || wizardState.llm_provider
        || '';
}

function selectedLLMModel(provider = '') {
    if (typeof _providerModelsRequired === 'function' && _providerModelsRequired()
        && !_providerModelsFor(provider || selectedLLMProvider(), Boolean(document.getElementById('use-oauth')?.checked))) return '';
    const selectedProvider = provider
        || selectedLLMProvider();
    if (selectedProvider === 'ollama-local') {
        const detected = document.getElementById('ollama-model')?.value || '';
        if (detected) return detected;
    }
    const selected = document.getElementById('llm-model')?.value || '';
    if (selected === '__custom__') {
        return document.getElementById('llm-custom-model')?.value?.trim() || '';
    }
    return selected;
}

// Called from checkHermes() after we know the install state, so the
// checklist starts in a sensible state instead of showing every step
// as pending even when Hermes is already running.
function _seedSetupChecklist(state) {
    _showSetupChecklist();
    if (state === 'connected') {
        // Already running → all infrastructure steps are done; user
        // just needs to confirm provider/model.
        _setSetupStep('install', 'done');
        _setSetupStep('setup',   'done');
        _setSetupStep('gateway', 'done');
    } else if (state === 'installed') {
        _setSetupStep('install', 'done');
        // A runnable launcher proves the official workspace stage completed;
        // provider selection and gateway startup remain visible below.
        _setSetupStep('setup',   'done');
    } else if (state === 'damaged' && wizardRuntime.packaged) {
        _setSetupStep('install', 'err');
    }
    // 'missing' → leave everything pending; install button does the rest.
}

// Called from any path that proves the user has a complete LLM
// selection (provider + model picked, and creds satisfied via API key,
// OAuth, or "none" for local). Re-checks the DOM so callers can fire
// it speculatively without worrying about premature state.
function markModelStepDone() {
    if (llmSetup.known) {
        // The provider step knows: only a saved provider that passes the pre-flight is done.
        if (llmSetup.primary && llmSetup.primary.ready === true) _setSetupStep('model', 'done');
        return;
    }
    const prov = selectedLLMProvider();
    if (!prov) return;
    const model = selectedLLMModel(prov);
    if (!model) return;  // model dropdown still empty — not actually done yet
    _setSetupStep('model', 'done');
}

// Hermes' installer already does setup as part of install (creates
// ~/.hermes/.env, config.yaml, SOUL.md, syncs 89 bundled skills), so
// nothing extra to run. We keep the function as a thin OK-resolver so
// the existing post-install handler doesn't need to be rewritten.
function runHermesSetup() {
    const log = document.getElementById('hermes-install-log');
    _setSetupStep('setup', 'active');
    if (log) {
        log.innerHTML += '<div class="log-line log-ok"><wz-icon>✅</wz-icon> Workspace inicializado por el instalador (~/.hermes/)</div>';
    }
    _setSetupStep('setup', 'done');
    return Promise.resolve(true);
}

// ── Step 3: Install Hermes with live SSE progress ───────────────
//
// A POST response carries SSE frames over fetch(). Using POST prevents browser
// prefetchers and ordinary cross-site links from starting a local installation.
//
// The backend tags meaningful installer phases and emits heartbeats during
// quiet download/build stages, so the header always explains current work.

const _INSTALL_PHASE_LABELS = {
    fetch:       '⬇️  Descargando instalador oficial',
    provision:   '⚙️  Preparando dependencias administradas',
    environment: '🧪 Creando entorno de Hermes',
    postinstall: '🧰 Preparando herramientas opcionales',
    verify:      '🔎 Verificando el lanzador instalado',
    finalize:    '✅ Finalizando instalación',
};

function _appendInstallLog(log, text, className = '', { label = false } = {}) {
    if (!log) return;
    const line = document.createElement('div');
    line.className = `log-line${className ? ` ${className}` : ''}`;
    if (label) setIconText(line, text);
    else line.textContent = text;
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
}

async function _consumeSseResponse(response, onMessage) {
    if (!response.body) throw new Error('El navegador no expuso el stream de instalación.');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    const dispatchFrame = (frame) => {
        const payload = frame
            .split(/\r?\n/)
            .filter(line => line.startsWith('data:'))
            .map(line => line.slice(5).trimStart())
            .join('\n');
        if (!payload) return;
        try { onMessage(JSON.parse(payload)); }
        catch { /* Ignore malformed/non-JSON keepalives, not the stream itself. */ }
    };

    while (true) {
        const { value, done } = await reader.read();
        if (done) {
            buffer += decoder.decode();
            break;
        }
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop() || '';
        frames.forEach(dispatchFrame);
    }
    if (buffer.trim()) dispatchFrame(buffer);
}

async function _runtimeOperationAllowed(capability) {
    try {
        const response = await fetch('/api/runtime-info', { cache: 'no-store' });
        const runtime = await response.json();
        return response.ok && runtime.capabilities?.[capability] === true;
    } catch {
        return false;
    }
}

async function installHermes() {
    const log  = document.getElementById('hermes-install-log');
    const btn  = document.getElementById('btn-install-hermes');
    const info = document.getElementById('hermes-info');
    if (!await _runtimeOperationAllowed('external_hermes_install')) {
        if (btn) btn.disabled = true;
        if (info) info.textContent = 'La instalación externa no está habilitada en este runtime de Iris.';
        return;
    }

    if (log) {
        log.style.display = 'block';
        log.replaceChildren();
        _appendInstallLog(
            log,
            '📦 Instalando Hermes desde el origen oficial. IRIS validará el script y el lanzador antes de darlo por listo.',
            'log-info',
            { label: true },
        );
    }
    if (btn) {
        btn.disabled = true;
        setIconText(btn, '⏳ Instalando...');
    }
    // Flip checklist into "step 1 running" so the user sees movement
    // even before the installer produces its first line of output.
    _showSetupChecklist();
    _setSetupStep('install', 'active');

    // Header inside the log box that we rewrite as the phase changes
    const headerEl = document.createElement('div');
    headerEl.className = 'log-line log-info';
    headerEl.style.fontWeight = '600';
    headerEl.textContent = '⬇️  Preparando instalación oficial...';
    if (log) log.appendChild(headerEl);

    let terminal = false;
    let installed = false;
    const finishError = (message) => {
        if (terminal) return;
        terminal = true;
        _appendInstallLog(log, `❌ ${String(message || 'Error desconocido.')}`, 'log-err', { label: true });
        if (btn) {
            setIconText(btn, '🔄 Reintentar');
            _applySecurityAckState(_isSecurityAcked());
        }
        _setSetupStep('install', 'err');
    };

    try {
        const response = await fetch('/api/install-hermes-stream', {
            method: 'POST',
            cache: 'no-store',
            credentials: 'same-origin',
            headers: {
                Accept: 'text/event-stream',
                'X-IRIS-Intent': 'install-hermes',
            },
        });
        if (!response.ok) {
            let detail = `El backend rechazó la instalación (HTTP ${response.status}).`;
            try {
                const body = await response.json();
                if (body?.detail) detail = body.detail;
            } catch {}
            throw new Error(detail);
        }

        await _consumeSseResponse(response, (msg) => {
            if (terminal || !msg) return;
            if (msg.type === 'phase') {
                if (msg.phase && _INSTALL_PHASE_LABELS[msg.phase]) {
                    setIconText(headerEl, _INSTALL_PHASE_LABELS[msg.phase]);
                }
                if (msg.text) _appendInstallLog(log, msg.text, 'log-info');
            } else if (msg.type === 'artifact') {
                const digest = String(msg.artifact?.sha256 || '');
                const bytes = Number(msg.artifact?.bytes || 0);
                if (digest) {
                    _appendInstallLog(
                        log,
                        `🔐 Script validado · SHA-256 ${digest.slice(0, 16)}… · ${Math.ceil(bytes / 1024)} KiB`,
                        'log-info',
                        { label: true },
                    );
                }
            } else if (msg.type === 'waiting') {
                setIconText(headerEl, '⏳ El instalador continúa trabajando...');
                if (msg.text) _appendInstallLog(log, msg.text, 'log-info');
            } else if (msg.type === 'log') {
                if (msg.phase && _INSTALL_PHASE_LABELS[msg.phase]) {
                    setIconText(headerEl, _INSTALL_PHASE_LABELS[msg.phase]);
                }
                _appendInstallLog(log, String(msg.text || ''));
            } else if (msg.type === 'done') {
                if (!msg.ok) {
                    finishError(
                        msg.verification?.message
                        || `El instalador terminó con código ${msg.returncode}. Revisa los logs de arriba.`,
                    );
                    return;
                }
                terminal = true;
                installed = true;
                setIconText(headerEl, '✅ Hermes instalado correctamente');
                const verified = msg.verification?.message || 'Lanzador verificado. Ya puedes pulsar «Iniciar IRIS».';
                _appendInstallLog(log, `✅ ${verified}`, 'log-ok', { label: true });
                if (btn) btn.style.display = 'none';
                _setGatewayEngine('stopped');
                if (info) info.innerHTML = '<div class="info-row ok"><wz-icon>✅</wz-icon> Hermes instalado</div>';
                if (typeof loadProvidersFromBackend === 'function') {
                    loadProvidersFromBackend().catch(() => {});
                }
                _setSetupStep('install', 'done');
                runHermesSetup().catch(() => {});
            } else if (msg.type === 'error') {
                finishError(msg.message);
            }
        });
        if (!terminal) finishError('El stream terminó sin confirmar el resultado de la instalación.');
    } catch (error) {
        finishError(error?.message || 'Se perdió la conexión con el wizard backend.');
    }
    return installed;
}

// ── Optional extras (A08.5): approved, isolated, profile-owned ──────
//
// Local engines (Kokoro, Coqui, NeuTTS, HF Qwen3-TTS) need large Python
// packages that are not part of the Iris runtime. Each button installs the
// exact versions approved in Iris' extras manifest into an isolated
// environment inside the user's profile; Iris then runs the engine in a
// separate process that only sees that environment. Nothing is installed
// into the bundled runtime and pip is never used.
//
// Each extra is mapped to:
//   • a main button (install / repair / update depending on its state)
//   • a status line with the real state reported by `/api/check-extras`
//   • secondary actions created here: Cancelar, Reparar, Quitar
//   • a collapsible log panel fed by the SSE stream

const _EXTRAS_UI = {
    'kokoro': {
        btn:    'btn-install-kokoro',
        status: 'kokoro-install-status',
        log:    'kokoro-install-log',
        label:  'Kokoro (TTS local)',
    },
    'coqui': {
        btn:    'btn-install-coqui',
        status: 'coqui-install-status',
        log:    'coqui-install-log',
        label:  'Coqui XTTS',
    },
    'neutts': {
        btn:    'btn-install-neutts',
        status: 'neutts-install-status',
        log:    'neutts-install-log',
        label:  'NeuTTS',
    },
    'hf-s2s': {
        btn:    'btn-install-hf-s2s',
        status: 'hf-s2s-install-status',
        log:    'hf-s2s-install-log',
        label:  'HF Qwen3-TTS',
    },
};

const _EXTRA_PHASE_LABELS = {
    resolve: '🔎 Preparando instalación',
    fetch:   '⬇️  Descargando paquetes aprobados',
    install: '📦 Extrayendo en el entorno aislado',
    verify:  '🔐 Verificando',
    done:    '✅ Listo',
};

const _extraState = {};
const _extraRunning = new Set();

function _extraSize(bytes) {
    return bytes > 0 ? formatBytes(bytes, 0) : 'tamaño desconocido';
}

function _extraActions(key) {
    const ui = _EXTRAS_UI[key];
    const status = ui && document.getElementById(ui.status);
    if (!status) return null;
    let actions = document.getElementById(`${key}-extra-actions`);
    if (!actions) {
        actions = document.createElement('span');
        actions.id = `${key}-extra-actions`;
        actions.className = 'extra-actions';
        actions.style.cssText = 'display:inline-flex; gap:6px; margin-left:8px; flex-wrap:wrap;';
        const make = (action, text, handler) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn-test btn-sm';
            button.dataset.extraAction = action;
            setIconText(button, text);
            button.style.display = 'none';
            button.addEventListener('click', handler);
            actions.appendChild(button);
        };
        make('cancel', '⏹ Cancelar', () => cancelExtra(key));
        make('repair', '🛠 Reparar', () => installExtra(key, 'repair'));
        make('remove', '🗑 Quitar', () => removeExtra(key));
        status.insertAdjacentElement('afterend', actions);
    }
    return actions;
}

function _showExtraActions(key, visible) {
    const actions = _extraActions(key);
    if (!actions) return;
    actions.querySelectorAll('button').forEach((button) => {
        button.style.display = visible.includes(button.dataset.extraAction) ? 'inline-block' : 'none';
        button.disabled = false;
    });
}

function _renderExtra(key, entry) {
    const ui = _EXTRAS_UI[key];
    const btn = ui && document.getElementById(ui.btn);
    const status = ui && document.getElementById(ui.status);
    if (!btn || !status) return;
    _extraState[key] = entry;
    // Each card already explains its engine; the status adds only the state
    // and the model download policy from the approved manifest.
    const models = entry.models ? ` ${entry.models}` : '';
    btn.style.display = 'inline-block';
    btn.disabled = false;
    status.style.color = '';
    if (_extraRunning.has(key) || entry.operation_running) {
        btn.disabled = true;
        setIconText(btn, '⏳ En curso…');
        status.textContent = 'Operación en curso.';
        _showExtraActions(key, _extraRunning.has(key) ? ['cancel'] : []);
        return;
    }
    switch (entry.state) {
    case 'installed':
        btn.style.display = 'none';
        setIconText(status, `✅ ${ui.label} instalado en su entorno aislado.`);
        status.style.color = '#9cffa0';
        _showExtraActions(key, ['repair', 'remove']);
        break;
    case 'broken':
        btn.dataset.extraAction = 'repair';
        setIconText(btn, `🛠 Reparar ${ui.label}`);
        status.textContent = entry.detail || 'El entorno está incompleto.';
        status.style.color = '#ffd36a';
        _showExtraActions(key, ['remove']);
        break;
    case 'outdated':
        btn.dataset.extraAction = 'repair';
        setIconText(btn, `⬆️ Actualizar ${ui.label} (${_extraSize(entry.download_bytes)})`);
        status.textContent = entry.detail || 'Hay una versión aprobada nueva.';
        status.style.color = '#ffd36a';
        _showExtraActions(key, ['remove']);
        break;
    case 'incompatible':
        btn.disabled = true;
        setIconText(btn, '🚫 No compatible');
        status.textContent = entry.detail || 'No compatible con este runtime.';
        status.style.color = '#ff9ab0';
        _showExtraActions(key, []);
        break;
    default:
        btn.dataset.extraAction = 'install';
        setIconText(btn, `📥 Instalar ${ui.label} (${_extraSize(entry.download_bytes)})`);
        status.textContent = `No instalado.${models}`;
        _showExtraActions(key, entry.models_present ? ['remove'] : []);
    }
    if (entry.hardware?.verdict === 'blocked') {
        // The server refuses an install, repair or update too, and Core will not
        // load it: say why before anyone tries. «Quitar» stays available.
        const reasons = (entry.hardware.reasons || []).join(' ');
        if (entry.state === 'installed') {
            // A profile from another PC, or weights still to download on a full disk.
            status.textContent = `${ui.label} está instalado, pero tu equipo no puede usarlo. ${reasons}`;
            status.style.color = '#ff9ab0';
        } else if (entry.state !== 'incompatible') {
            btn.disabled = true;
            setIconText(btn, '🚫 Tu equipo no puede usarlo');
            status.textContent = reasons;
            status.style.color = '#ff9ab0';
        }
        const repair = _extraActions(key)?.querySelector('[data-extra-action="repair"]');
        if (repair) repair.style.display = 'none';
    }
    _renderVoiceHardware(key, entry.hardware);
}

// Whether this PC can run a local voice engine (core/voice_requirements.py):
// warnings (tight RAM, few CPU threads) and notes (the GPU is not used, measured
// speed) never block; a blocked model is disabled in its selector.
function _renderVoiceHardware(key, hardware) {
    const ui = _EXTRAS_UI[key];
    const status = ui && document.getElementById(ui.status);
    if (!status) return;
    let note = document.getElementById(`${key}-hw-note`);
    if (!hardware) {
        if (note) note.remove();
        return;
    }
    if (!note) {
        note = document.createElement('p');
        note.id = `${key}-hw-note`;
        note.className = 'dim';
        note.style.cssText = 'font-size:11px; margin:6px 0 0;';
        const actions = document.getElementById(`${key}-extra-actions`);
        (actions || status).insertAdjacentElement('afterend', note);
    }
    if (key === 'hf-s2s') {
        _applyQwenModelAvailability(hardware);
        return;
    }
    const model = Object.values(hardware.models || {})[0] || {};
    note.textContent = [...(model.warnings || []), ...(model.notes || [])].join(' ');
}

function _applyQwenModelAvailability(hardware = _extraState['hf-s2s']?.hardware) {
    const select = document.getElementById('hf-s2s-model');
    const note = document.getElementById('hf-s2s-hw-note');
    if (!select || !hardware) return;
    for (const option of select.options) {
        if (!option.dataset.label) option.dataset.label = option.textContent;
        // One verdict per model id: Base and CustomVoice are separate downloads.
        const verdict = (hardware.models || {})[option.value];
        const blocked = verdict?.verdict === 'blocked';
        option.disabled = blocked;
        option.textContent = blocked ? `${option.dataset.label} · no disponible en tu equipo` : option.dataset.label;
    }
    if (select.selectedOptions[0]?.disabled) {
        // Same family, smaller size: 1.7B → 0.6B, when this PC can run it.
        const smaller = [...select.options].find(option => option.value === select.value.replace('1.7B', '0.6B'));
        if (smaller && !smaller.disabled) select.value = smaller.value;
    }
    const selected = (hardware.models || {})[select.value] || {};
    // A wholly blocked engine already explains itself next to its install button.
    const lines = hardware.verdict === 'blocked' ? [] : selected.verdict === 'blocked'
        ? selected.reasons || []
        : [...(selected.warnings || []), ...(selected.notes || [])];
    if (note) {
        note.textContent = lines.join(' ');
        note.style.color = selected.verdict === 'blocked' ? '#ff9ab0' : (selected.verdict === 'warn' ? '#ffd36a' : '');
    }
}

async function refreshExtrasStatus() {
    // Single request returns the real state of every admitted extra.
    let data;
    try {
        const res = await fetch('/api/check-extras', { cache: 'no-store' });
        data = await res.json();
        if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
    } catch (error) {
        for (const ui of Object.values(_EXTRAS_UI)) {
            const status = document.getElementById(ui.status);
            if (status) status.textContent = `No se pudo consultar el estado: ${error?.message || 'sin conexión'}.`;
        }
        return;
    }
    for (const key of Object.keys(_EXTRAS_UI)) {
        const entry = (data.extras || {})[key];
        if (entry) {
            _renderExtra(key, entry);
            continue;
        }
        const ui = _EXTRAS_UI[key];
        const btn = document.getElementById(ui.btn);
        const status = document.getElementById(ui.status);
        if (!btn || !status) continue;
        btn.disabled = true;
        setIconText(btn, '🚫 No disponible');
        status.textContent = (data.unsupported || {})[key] || 'Este extra no está aprobado en esta versión de Iris.';
        status.style.color = '#ff9ab0';
        _showExtraActions(key, []);
    }
}

async function installExtra(extraKey, requestedAction) {
    const ui = _EXTRAS_UI[extraKey];
    if (!ui) return;
    if (!await _runtimeOperationAllowed('profile_extras')) {
        const status = document.getElementById(ui.status);
        if (status) status.textContent = 'Los extras opcionales no están habilitados en este runtime.';
        return;
    }
    const btn    = document.getElementById(ui.btn);
    const log    = document.getElementById(ui.log);
    const status = document.getElementById(ui.status);
    if (!btn || !log || !status) return;
    const action = requestedAction || (btn.dataset.extraAction === 'repair' ? 'repair' : 'install');

    log.style.display = 'block';
    log.innerHTML = '';
    _extraRunning.add(extraKey);
    btn.disabled = true;
    setIconText(btn, action === 'repair' ? '⏳ Reparando…' : '⏳ Instalando…');
    status.textContent = 'Preparando…';
    status.style.color = '';
    _showExtraActions(extraKey, ['cancel']);

    const headerEl = document.createElement('div');
    headerEl.className = 'log-line log-info';
    headerEl.style.fontWeight = '600';
    setIconText(headerEl, `${action === 'repair' ? '🛠 Reparando' : '⬇️  Instalando'} ${ui.label}…`);
    log.appendChild(headerEl);

    let terminal = false;
    const finish = async (ok, message) => {
        if (terminal) return;
        terminal = true;
        _extraRunning.delete(extraKey);
        _appendInstallLog(log, message, ok ? 'log-ok' : 'log-err', { label: true });
        await refreshExtrasStatus();
        if (!ok) {
            setIconText(status, message);
            status.style.color = '#ff9ab0';
        }
    };

    const handleMessage = (msg) => {
        if (terminal || !msg) return;
        if (msg.type === 'log') {
            if (msg.phase && _EXTRA_PHASE_LABELS[msg.phase]) setIconText(headerEl, _EXTRA_PHASE_LABELS[msg.phase]);
            _appendInstallLog(log, msg.text);
        } else if (msg.type === 'progress') {
            const total = msg.total > 0 ? ` / ${formatBytes(msg.total, 0)}` : '';
            status.textContent = `${msg.file}: ${formatBytes(msg.done, 0)}${total}`;
        } else if (msg.type === 'done') {
            const text = msg.changed
                ? `✅ ${ui.label} listo en su entorno aislado.`
                : `✅ ${ui.label} ya estaba instalado${msg.verified ? ' y verificado' : ''}.`;
            void finish(true, text);
        } else if (msg.type === 'error') {
            const text = msg.code === 'cancelled'
                ? '⏹ Cancelado. El entorno anterior sigue igual.'
                : `❌ ${String(msg.message || 'Error desconocido.')}`;
            void finish(false, text);
        }
    };

    try {
        const query = new URLSearchParams({ extra: extraKey, action });
        const response = await fetch(`/api/install-extra-stream?${query}`, {
            method: 'POST',
            cache: 'no-store',
            credentials: 'same-origin',
            headers: {
                Accept: 'text/event-stream',
                'X-IRIS-Intent': 'install-extra',
            },
        });
        if (!response.ok) {
            let detail = `El backend rechazó la operación (HTTP ${response.status}).`;
            try {
                const body = await response.json();
                if (body?.detail) detail = body.detail;
            } catch {}
            throw new Error(detail);
        }
        await _consumeSseResponse(response, handleMessage);
        if (!terminal) await finish(false, '❌ La operación terminó sin confirmación final.');
    } catch (error) {
        await finish(false, `❌ ${error?.message || 'Se perdió la conexión con el wizard backend.'}`);
    }
}

async function cancelExtra(extraKey) {
    const actions = _extraActions(extraKey);
    const button = actions?.querySelector('[data-extra-action="cancel"]');
    if (button) button.disabled = true;
    try {
        await fetch(`/api/extras/cancel?${new URLSearchParams({ extra: extraKey })}`, {
            method: 'POST',
            cache: 'no-store',
            credentials: 'same-origin',
            headers: { 'X-IRIS-Intent': 'cancel-extra' },
        });
    } catch {
        if (button) button.disabled = false;
    }
}

async function removeExtra(extraKey) {
    const ui = _EXTRAS_UI[extraKey];
    const status = ui && document.getElementById(ui.status);
    if (!ui || !status) return;
    if (!await irisAsk({
        title: `Quitar ${ui.label}`, icon: 'trash', tone: 'danger',
        message: 'Se borra su entorno aislado; tus voces y referencias no se tocan.',
        question: '¿Deseas continuar?', confirmLabel: 'Quitar', confirmIcon: 'trash',
    })) return;
    const entry = _extraState[extraKey] || {};
    const purge = entry.models_present && await irisAsk({
        title: 'Modelos descargados', icon: 'trash',
        message: 'Puedes borrar también los modelos que descargó este extra para liberar espacio, o conservarlos para una reinstalación.',
        question: '¿Borrarlos?', confirmLabel: 'Borrar modelos', cancelLabel: 'Conservarlos',
    });
    _showExtraActions(extraKey, []);
    status.textContent = 'Quitando…';
    try {
        const response = await fetch('/api/extras/remove', {
            method: 'POST',
            cache: 'no-store',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-IRIS-Intent': 'remove-extra' },
            body: JSON.stringify({ extra: extraKey, purge_models: Boolean(purge) }),
        });
        const body = await response.json().catch(() => ({}));
        await refreshExtrasStatus();
        if (!response.ok || !body.ok) {
            status.textContent = body.error || body.detail || `No se pudo quitar (HTTP ${response.status}).`;
            status.style.color = '#ff9ab0';
        } else if (body.models_kept) {
            status.textContent += ' Los modelos descargados se conservaron.';
        }
    } catch (error) {
        status.textContent = `No se pudo quitar: ${error?.message || 'sin conexión'}.`;
        status.style.color = '#ff9ab0';
    }
}

// ── Autostart toggle (Windows: Startup folder, Linux/macOS: systemd/launchd)

async function refreshAutostartStatus() {
    const pill = document.getElementById('autostart-state');
    const cb   = document.getElementById('autostart-toggle');
    if (!pill && !cb) return;
    try {
        const res = await fetch('/api/autostart-status');
        const d = await res.json();
        if (cb) cb.checked = !!d.enabled;
        if (pill) {
            pill.textContent = d.enabled
                ? `Activo · ${d.method || ''}`
                : 'Desactivado';
            pill.className = d.enabled ? 'autostart-pill on' : 'autostart-pill off';
        }
    } catch {
        if (pill) {
            pill.textContent = 'No se pudo consultar';
            pill.className = 'autostart-pill off';
        }
    }
}

async function toggleAutostart(ev) {
    if (!await _runtimeOperationAllowed('external_autostart')) {
        ev.target.disabled = true;
        const detail = document.getElementById('autostart-detail');
        if (detail) detail.textContent = 'El arranque del runtime incluido se administra desde Iris.';
        return;
    }
    const cb     = ev.target;
    const action = cb.checked ? 'enable' : 'disable';
    const detail = document.getElementById('autostart-detail');
    cb.disabled = true;
    if (detail) detail.textContent = 'Aplicando cambios...';
    try {
        const res = await fetch('/api/autostart', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action }),
        });
        const d = await res.json();
        if (detail) detail.textContent = d.detail || (d.ok ? 'Listo.' : 'Falló.');
        await refreshAutostartStatus();
        // Reflect autostart state on the checklist: enabled & ok → done,
        // disabled or never touched → skipped (this step is optional).
        if (typeof _setSetupStep === 'function') {
            if (cb.checked && d.ok) _setSetupStep('daemon', 'done');
            else if (!cb.checked)   _setSetupStep('daemon', 'skipped');
            else                    _setSetupStep('daemon', 'err');
        }
    } catch (e) {
        if (detail) detail.textContent = `Error: ${e.message}`;
        // Roll the checkbox back so it reflects reality
        cb.checked = !cb.checked;
        if (typeof _setSetupStep === 'function') _setSetupStep('daemon', 'err');
    } finally {
        cb.disabled = false;
    }
}

/**
 * Persist the current LLM selection (provider + model + api key + OAuth flag)
 * to ~/.iris/config.json *before* asking the gateway to start.
 *
 * Why this is mandatory:
 *   `/api/start-gateway` reads `cfg.llm_provider` from disk to decide which
 *   provider to launch. Until the user reaches the final summary step the
 *   wizard never POSTs `/api/save-config`, so the on-disk value can still be
 *   the previous provider even after the user picked and OAuth-authenticated,
 *   say, Codex. Without this call, the gateway
 *   pre-flight rejects the launch citing the wrong provider.
 *
 *   Returns the backend's resolved {provider, model, auth} so the caller can
 *   show what was actually persisted.
 */
// ── AI provider step: the main provider, more providers and the Gateway gate ──
//
// The main provider is saved on its own («Guardar proveedor» → /api/save-llm-selection,
// the Gateway's pre-flight), then the section folds into a summary with «Cambiar
// proveedor» and «Agregar otro proveedor». Another provider is only its credential in
// Hermes (/api/add-llm-provider): Iris offers its models per conversation. The Gateway
// section, below messaging, starts once the main provider is ready (llm_setup.js).

let llmSetup = IrisLlmSetup.EMPTY;
let llmView = '';              // '' follows the saved state | 'change' | 'add'
let _llmSetupGeneration = 0;
let gatewayEngine = 'unknown'; // unknown | missing | damaged | stopped | running | failed
let gatewayFailure = '';
let gatewayNeedsRestart = false;

function _gatewayLog() {
    return document.getElementById('gateway-log');
}

function _llmFormOwnsProvider() {
    return IrisLlmSetup.formOwnsProvider(llmSetup, llmView);
}

function _gatewayBlockedReason() {
    const gate = IrisLlmSetup.gatewayGate(llmSetup, gatewayEngine);
    return gate.allowed ? '' : gate.reason;
}
// messaging.js restarts Hermes too: it asks the same gate (R02).
globalThis.irisGatewayBlockedReason = _gatewayBlockedReason;

async function loadLlmSetup() {
    const generation = ++_llmSetupGeneration;
    let next = IrisLlmSetup.EMPTY;
    try {
        const response = await fetch('/api/llm-setup', { cache: 'no-store' });
        if (response.ok) next = IrisLlmSetup.normalize(await response.json());
    } catch { /* unknown: the Gateway decides, as before */ }
    if (generation !== _llmSetupGeneration) return llmSetup;  // a newer read won
    llmSetup = next;
    renderLlmSetup();
    return llmSetup;
}

function _setLlmStatus(id, message = '', tone = '') {
    const status = document.getElementById(id);
    if (!status) return;
    status.textContent = message;
    status.dataset.tone = tone;
}

function renderLlmSetup() {
    const section = document.getElementById('llm-section');
    if (!section) return;
    const view = IrisLlmSetup.view(llmSetup, llmView);
    section.dataset.view = view;
    const summary = document.getElementById('llm-configured');
    const editor = document.getElementById('llm-editor');
    if (summary) summary.hidden = view !== 'summary';
    if (editor) editor.hidden = view === 'summary';

    const primary = llmSetup.primary;
    if (primary) {
        const name = document.getElementById('llm-configured-name');
        const detail = document.getElementById('llm-configured-detail');
        if (name) name.textContent = primary.name;
        if (detail) {
            detail.textContent = [primary.model, IrisLlmSetup.authLabel(primary.auth)].filter(Boolean).join(' · ');
        }
    }
    const others = document.getElementById('llm-configured-others');
    if (others) {
        others.replaceChildren();
        others.hidden = !llmSetup.additional.length;
        if (llmSetup.additional.length) {
            const label = document.createElement('span');
            label.className = 'llm-configured-eyebrow';
            label.textContent = 'También conectados';
            const list = document.createElement('div');
            list.className = 'llm-configured-chips';
            for (const row of llmSetup.additional) {
                const chip = document.createElement('span');
                chip.className = 'llm-chip';
                chip.textContent = `${row.name} · ${IrisLlmSetup.authLabel(row.auth)}`;
                list.append(chip);
            }
            const note = document.createElement('p');
            note.className = 'llm-configured-note';
            note.textContent = 'Elige sus modelos en el selector de cada conversación de Iris.';
            others.append(label, list, note);
        }
    }

    const intro = document.getElementById('llm-editor-intro');
    if (intro) {
        intro.hidden = view === 'change' && !primary;
        intro.textContent = view === 'add'
            ? `Conecta otro proveedor con su API key o su cuenta. ${primary ? primary.name : 'Tu proveedor principal'} sigue siendo el principal; los modelos del nuevo aparecerán en el selector de cada conversación.`
            : primary
                ? `Elige el proveedor y el modelo que responderán por defecto. Ahora: ${primary.name}${primary.model ? ` · ${primary.model}` : ''}.`
                : '';
    }
    const save = document.getElementById('btn-save-llm');
    if (save && !save.dataset.busy) save.textContent = view === 'add' ? 'Agregar proveedor' : 'Guardar proveedor';
    const cancel = document.getElementById('btn-cancel-llm');
    if (cancel) cancel.hidden = !(view !== 'summary' && primary && primary.ready === true);
    if (view === 'change' && primary && primary.ready === false && !llmView) {
        _setLlmStatus('llm-save-status', primary.message || 'Tu proveedor guardado todavía no está listo.', 'error');
    }
    _applyLlmViewToForm(view);
    if (typeof _setSetupStep === 'function' && wizardState.hermes_mode === 'full') {
        _setSetupStep('model', primary && primary.ready === true ? 'done' : 'pending');
    }
    _renderGatewayGate();
}

// The form follows the view: adding lists only the providers that can sit next to the
// main one and never asks for a model (it is chosen per conversation).
function _applyLlmViewToForm(view = IrisLlmSetup.view(llmSetup, llmView)) {
    const section = document.getElementById('llm-section');
    const listed = section?.dataset.listed || '';
    if (listed !== view && (listed === 'add' || view === 'add')) {
        renderProviderList(document.getElementById('provider-search')?.value || '');
    }
    if (section) section.dataset.listed = view;
    if (typeof _renderProviderModelsAccess === 'function') _renderProviderModelsAccess();
    if (view !== 'add') return;
    for (const id of ['llm-model-section', 'custom-model-section', 'ollama-section', 'llm-base-url-section']) {
        const element = document.getElementById(id);
        if (element) element.style.display = 'none';
    }
}

function _restoreLlmFormToPrimary() {
    document.querySelectorAll('input[name="llm_provider"]').forEach(radio => { radio.checked = false; });
    document.querySelectorAll('.provider-grid .radio-card').forEach(card => card.classList.remove('selected'));
    const key = document.getElementById('llm-api-key');
    if (key) key.value = '';
    return _hydrateSavedConfigIntoDOM();
}

function changeLlmProvider() {
    llmView = 'change';
    _setLlmStatus('llm-save-status');
    _setLlmStatus('llm-configured-status');
    renderLlmSetup();
    document.getElementById('llm-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function addLlmProvider() {
    llmView = 'add';
    _setLlmStatus('llm-save-status');
    _setLlmStatus('llm-configured-status');
    document.querySelectorAll('input[name="llm_provider"]').forEach(radio => { radio.checked = false; });
    document.querySelectorAll('.provider-grid .radio-card').forEach(card => card.classList.remove('selected'));
    for (const id of ['llm-key-section', 'llm-oauth-section', 'provider-warning']) {
        const element = document.getElementById(id);
        if (element) element.style.display = 'none';
    }
    const key = document.getElementById('llm-api-key');
    if (key) key.value = '';
    renderLlmSetup();
    document.getElementById('llm-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function cancelLlmEdit() {
    llmView = '';
    _setLlmStatus('llm-save-status');
    await _restoreLlmFormToPrimary();
    renderLlmSetup();
}

function _attentionTo(code) {
    const target = ['missing_base_url', 'invalid_base_url'].includes(code)
        ? document.getElementById('llm-base-url')
        : code === 'missing_oauth'
            ? document.getElementById('btn-auth-oauth')
            : ['missing_api_key', 'invalid_api_key', 'missing_credentials'].includes(code)
                ? document.getElementById('llm-api-key')
                : null;
    if (!target) return;
    target.classList.add('field-attention');
    target.focus();
    setTimeout(() => target.classList.remove('field-attention'), 4000);
}

async function saveLlmProvider() {
    const button = document.getElementById('btn-save-llm');
    const adding = IrisLlmSetup.view(llmSetup, llmView) === 'add';
    const provider = adding
        ? (document.querySelector('input[name="llm_provider"]:checked')?.value || '')
        : selectedLLMProvider();
    if (!provider) {
        _setLlmStatus('llm-save-status', 'Elige un proveedor de la lista.', 'error');
        return;
    }
    if (button) { button.disabled = true; button.dataset.busy = '1'; button.textContent = adding ? 'Agregando…' : 'Guardando…'; }
    _setLlmStatus('llm-save-status', adding ? 'Comprobando la credencial…' : 'Comprobando el proveedor…');
    try {
        let result;
        if (adding) {
            const useOAuth = Boolean(document.getElementById('use-oauth')?.checked);
            const response = await fetch('/api/add-llm-provider', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    provider,
                    use_oauth: useOAuth,
                    api_key: useOAuth ? '' : (document.getElementById('llm-api-key')?.value?.trim() || ''),
                }),
            });
            result = await response.json().catch(() => ({}));
            if (!response.ok) result = { ...result, status: 'error' };
        } else {
            result = await _persistLLMSelectionBeforeGateway();
        }
        if (result?.status === 'error' || !result?.provider) {
            if (result?.code === 'became_main') await loadLlmSetup();  // another save changed the main provider
            _setLlmStatus('llm-save-status', result?.message || 'No se pudo guardar el proveedor.', 'error');
            _attentionTo(result?.code);
            return;
        }
        llmView = '';
        _setLlmStatus('llm-save-status');
        await _restoreLlmFormToPrimary();
        await loadLlmSetup();
        if (gatewayEngine === 'running') gatewayNeedsRestart = true;
        const name = result.name || (LLM_PROVIDERS[result.provider] || {}).name || result.provider;
        const applied = gatewayEngine === 'running' ? ' Pulsa «Reiniciar IRIS» para aplicarlo.' : '';
        const message = adding
            ? `${name} agregado. Elige sus modelos en el selector de cada conversación.${applied}`
            : `${name} guardado como proveedor principal.${applied}`;
        _setLlmStatus(llmSetup.primary?.ready === true ? 'llm-configured-status' : 'llm-save-status', message, 'ok');
        _renderGatewayGate();
    } finally {
        if (button) { button.disabled = false; delete button.dataset.busy; }
        renderLlmSetup();
    }
}

// The Gateway section: blocked until the main provider is ready, then start, or restart
// with the saved configuration once it runs.
function _setGatewayEngine(state, failure = '') {
    gatewayEngine = state;
    gatewayFailure = state === 'failed' ? failure : '';
    if (state === 'running') {
        gatewayNeedsRestart = false;
        if (typeof _setSetupStep === 'function') _setSetupStep('gateway', 'done');
    }
    _renderGatewayGate();
}

function _renderGatewayGate() {
    const section = document.getElementById('gateway-section');
    const button = document.getElementById('btn-start-gateway');
    const hint = document.getElementById('gateway-hint');
    const state = document.getElementById('gateway-state');
    document.getElementById('messaging-panel')?._messaging?.syncRestartGate?.();
    if (!section || !button) return;
    const gate = IrisLlmSetup.gatewayGate(llmSetup, gatewayEngine);
    const running = gatewayEngine === 'running';
    section.dataset.state = !gate.allowed ? 'blocked' : running ? (gatewayNeedsRestart ? 'stale' : 'running')
        : gatewayEngine === 'failed' ? 'failed' : 'ready';
    if (button.dataset.busy) return;
    button.disabled = !gate.allowed;
    setIconText(button, running ? '🔁 Reiniciar IRIS' : gatewayEngine === 'failed' ? '🔄 Reintentar' : '🚀 Iniciar IRIS');
    if (state) {
        state.textContent = !gate.allowed ? 'Esperando proveedor'
            : running ? (gatewayNeedsRestart ? 'Activo · cambios sin aplicar' : 'Activo')
                : gatewayEngine === 'failed' ? 'No se pudo iniciar' : 'Listo para iniciar';
    }
    if (hint) {
        hint.textContent = !gate.allowed ? gate.reason
            : running
                ? (gatewayNeedsRestart
                    ? 'Guardaste un cambio de proveedor: pulsa «Reiniciar IRIS» para aplicarlo. Reinicia el asistente sin cerrar tus ventanas.'
                    : 'IRIS ya puede conversar. Reiniciar el asistente aplica cambios sin cerrar tus ventanas.')
                : gatewayEngine === 'failed'
                    ? (gatewayFailure ? `${gatewayFailure}. Revisa el detalle de abajo y vuelve a intentarlo.` : 'Revisa el detalle de abajo y vuelve a intentarlo.')
                    : 'Proveedor listo. La mensajería es opcional: puedes iniciar ya.';
    }
}

async function _persistLLMSelectionBeforeGateway() {
    const provider = selectedLLMProvider();
    const useOAuth = document.getElementById('use-oauth')?.checked || false;
    const apiKey   = useOAuth ? '' : (document.getElementById('llm-api-key')?.value?.trim() || '');
    const model    = selectedLLMModel(provider);
    const baseUrl  = document.getElementById('llm-base-url')?.value?.trim() || '';

    if (!provider || !model) {
        return { status: 'error', message: !provider ? 'Selecciona un proveedor.' : 'Selecciona o escribe un modelo válido.' };
    }

    try {
        const r = await fetch('/api/save-llm-selection', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                provider:  provider,
                model:     model,
                api_key:   apiKey,
                use_oauth: useOAuth,
                base_url:  baseUrl,
            }),
        });
        const d = await r.json();
        if (!r.ok) return { ...d, status: 'error', message: d.message || 'No se pudo guardar el proveedor.' };
        // Mirror into wizardState so other steps see the latest pick
        // without having to re-read the radio.
        wizardState.llm_provider = d.provider || provider;
        wizardState.llm_model    = d.model    || model;
        wizardState.llm_base_url = d.base_url || baseUrl;
        return d;  // {provider, model, auth, hermes}
    } catch (e) {
        console.warn('save-llm-selection failed:', e);
        return { status: 'error', message: `No se pudo guardar la selección: ${e.message || e}` };
    }
}

/**
 * Start the Gateway, or restart it once it runs, with the SAVED provider.
 *
 * The provider is saved by its own section («Guardar proveedor») before this button
 * enables, so nothing here reads the form: the provider list may be showing another
 * provider being added. Starting before a provider was saved used to end in "falta el
 * proveedor"; the gate (llm_setup.js) now says so without trying.
 */
async function startGateway() {
    const log = _gatewayLog();
    const btn = document.getElementById('btn-start-gateway');
    const info = document.getElementById('hermes-info');
    const blocked = _gatewayBlockedReason();
    if (blocked) {
        _renderGatewayGate();
        document.getElementById('llm-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
    }
    if (!await _ensureEngineConsent()) {
        _engineConsentRefused(log);
        return;
    }

    const restart = gatewayEngine === 'running';
    if (log) log.style.display = 'block';
    if (btn) {
        btn.disabled = true;
        btn.dataset.busy = '1';
        setIconText(btn, restart ? '⏳ Reiniciando IRIS…' : '⏳ Iniciando IRIS…');
    }
    if (log) {
        const primary = llmSetup.primary;
        if (primary) {
            log.innerHTML += `<div class="log-line log-info"><wz-icon>📋</wz-icon> IRIS va a usar: <b>${escapeHtml(primary.name)}</b>${primary.model ? ' / ' + escapeHtml(primary.model) : ''} <span class="dim">(${escapeHtml(IrisLlmSetup.authLabel(primary.auth))})</span></div>`;
        }
        log.innerHTML += `<div class="log-line log-info"><wz-icon>🚀</wz-icon> ${restart ? 'Reiniciando' : 'Iniciando'} IRIS… esto puede tardar unos segundos.</div>`;
    }
    if (typeof _setSetupStep === 'function') _setSetupStep('gateway', 'active');

    // Helper — dump the log tail into the panel so the user sees the
    // REAL reason a crashed/stuck gateway failed, not just our message.
    const _dumpTail = (tail) => {
        if (!log || !tail) return;
        const pre = document.createElement('pre');
        pre.className = 'log-tail';
        pre.textContent = tail;
        log.appendChild(pre);
    };
    const finish = (state, failure = '') => {
        if (btn) delete btn.dataset.busy;
        _setGatewayEngine(state, failure);
    };

    try {
        const res = await fetch(restart ? '/api/restart-gateway' : '/api/start-gateway', { method: 'POST' });
        const d = await res.json();
        if (d.stop && d.stop.detail && log) {
            log.innerHTML += `<div class="log-line log-info"><wz-icon>🛑</wz-icon> ${escapeHtml(d.stop.detail)}</div>`;
        }

        if (d.status === 'ok' || d.status === 'success' || d.status === 'running') {
            if (log) log.innerHTML += `<div class="log-line log-ok"><wz-icon>✅</wz-icon> ${escapeHtml(d.message || 'Servicio de IA listo.')}</div>`;
            if (info) info.innerHTML = '<div class="info-row ok"><wz-icon>🟢</wz-icon> El servicio de IA está activo</div>';
            finish('running');
            // Gateway only reaches "ok" when the pre-flight accepted the
            // provider's credentials, so the model sub-step is done too.
            if (typeof markModelStepDone === 'function') markModelStepDone();
            setTimeout(checkHermesOld, 2000);
        } else if (['missing_api_key', 'missing_oauth', 'missing_credentials', 'missing_base_url', 'invalid_base_url'].includes(d.code)) {
            // The saved provider lost its credential since it was saved: back to its section.
            if (log) log.innerHTML += `<div class="log-line log-err"><wz-icon>🔑</wz-icon> ${escapeHtml(d.message)}</div>`;
            finish('failed', 'Tu proveedor de IA necesita atención');
            if (typeof _setSetupStep === 'function') _setSetupStep('gateway', 'err');
            llmView = 'change';
            await loadLlmSetup();
            _setLlmStatus('llm-save-status', d.message || 'Revisa la credencial de tu proveedor.', 'error');
            setTimeout(() => _attentionTo(d.code), 150);
        } else if (d.code === 'gateway_crashed') {
            // The process died within 8s. Show exit code + log tail so the
            // user has a fighting chance to debug (bad model id, port in
            // use, corrupt config, etc).
            if (log) log.innerHTML +=
                `<div class="log-line log-err"><wz-icon>💥</wz-icon> El servicio de IA se cerró al iniciar (código ${escapeHtml(d.returncode)}).</div>` +
                `<div class="log-line dim">Log: ${escapeHtml(d.log_path || '')}</div>`;
            _dumpTail(d.log_tail);
            finish('failed', 'El servicio de IA se cerró al iniciar');
            if (typeof _setSetupStep === 'function') _setSetupStep('gateway', 'err');
        } else if (d.code === 'gateway_not_listening' || d.status === 'warning') {
            // Process is alive but port hasn't opened. Could still work —
            // mark as active (not done) and re-poll /api/test-hermes.
            if (log) log.innerHTML += `<div class="log-line log-warn"><wz-icon>⚠</wz-icon> ${escapeHtml(d.message)}</div>`;
            _dumpTail(d.log_tail);
            finish('stopped');
            if (typeof _setSetupStep === 'function') _setSetupStep('gateway', 'active');
            setTimeout(checkHermesOld, 4000);
        } else {
            if (log) log.innerHTML += `<div class="log-line log-err"><wz-icon>❌</wz-icon> ${escapeHtml(d.message || 'No se pudo iniciar IRIS.')}</div>`;
            _dumpTail(d.log_tail);
            finish('failed', 'No se pudo iniciar');
            if (typeof _setSetupStep === 'function') _setSetupStep('gateway', 'err');
        }
    } catch (e) {
        if (log) log.innerHTML += `<div class="log-line log-err"><wz-icon>❌</wz-icon> Error de conexión: ${escapeHtml(e.message)}</div>`;
        finish('failed', 'No se pudo contactar con el asistente');
        if (typeof _setSetupStep === 'function') _setSetupStep('gateway', 'err');
    }
}

// ── Step 3: Health Check end-to-end ────────────────────

// The seven rows we expect from /api/health-check-stream. Declared up-front so
// we can render the skeleton immediately on click and then fill each row
// in-place as its SSE event arrives — no flicker, no re-layout.
let HEALTHCHECK_ROWS = IrisWizardRuntime.healthRows(null);

function _healthcheckRowHtml(id, label, state, detail = '', hint = '', elapsedMs = 0) {
    const icon = {
        pending: '⚪',
        running: '⏳',
        ok:      '✅',
        fail:    '❌',
        inactive:'🟡',
        skip:    '⏭️',
    }[state] || '⚪';
    const iconClass = state === 'running' ? 'hc-icon spin' : 'hc-icon';
    const elapsed = elapsedMs > 0 ? `<span class="hc-elapsed">${elapsedMs} ms</span>` : '<span></span>';
    const detailHtml = detail ? `<div class="hc-detail">${escapeHtml(detail)}</div>` : '';
    const hintHtml   = hint   ? `<div class="hc-hint"><wz-icon>💡</wz-icon> ${escapeHtml(hint)}</div>` : '';
    return `
        <div class="hc-row state-${state}" data-hc="${id}">
            <span class="${iconClass}">${uiIcon(icon)}</span>
            <div class="hc-body">
                <div class="hc-label">${escapeHtml(label)}</div>
                ${detailHtml}
                ${hintHtml}
            </div>
            ${elapsed}
        </div>
    `;
}

function escapeHtml(s) {
    return String(s || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function _renderHealthcheckSkeleton() {
    const grid = document.getElementById('healthcheck-grid');
    if (!grid) return;
    grid.innerHTML = HEALTHCHECK_ROWS
        .map(r => _healthcheckRowHtml(r.id, r.label, 'pending'))
        .join('');
    // Mark the first one as running
    const first = grid.querySelector('[data-hc="cli_in_path"]');
    if (first) {
        first.outerHTML = _healthcheckRowHtml('cli_in_path', HEALTHCHECK_ROWS[0].label, 'running');
    }
}

function _updateHealthcheckRow(result) {
    const grid = document.getElementById('healthcheck-grid');
    if (!grid) return;
    const row = grid.querySelector(`[data-hc="${result.id}"]`);
    if (!row) return;
    const meta = HEALTHCHECK_ROWS.find(r => r.id === result.id);
    const label = meta ? meta.label : (result.label || result.id);
    const state = result.status === 'ok'   ? 'ok'
                : result.status === 'skip' ? 'skip'
                : result.status === 'inactive' ? 'inactive'
                : result.ok                 ? 'ok'
                :                             'fail';
    row.outerHTML = _healthcheckRowHtml(
        result.id, label, state,
        result.detail || '', result.hint || '', result.elapsed_ms || 0,
    );

    // Mark the next pending row as "running" so the user sees progress
    const next = grid.querySelector('.state-pending');
    if (next) {
        const id = next.getAttribute('data-hc');
        const m = HEALTHCHECK_ROWS.find(r => r.id === id);
        next.outerHTML = _healthcheckRowHtml(id, m ? m.label : id, 'running');
    }
}

// Keep a handle so a second click re-triggers the run cleanly
let _healthcheckStream = null;

async function runHealthCheck() {
    const btn     = document.getElementById('btn-run-healthcheck');
    const summary = document.getElementById('healthcheck-summary');

    if (_healthcheckStream) {
        try { _healthcheckStream.close(); } catch {}
        _healthcheckStream = null;
    }

    if (btn) {
        btn.disabled = true;
        setIconText(btn, '⏳ Verificando...');
    }
    if (summary) summary.textContent = '';

    // Verify the selection visible on screen, never the provider left by a
    // previous wizard run. This was the source of contradictory output such
    // as "OAuth de OpenAI" in the summary but "oc-auto necesita API key" in
    // the diagnostic below it. Only a form that owns the main provider
    // («Cambiar proveedor», or a first run) is saved first: folded, or adding
    // another provider, the saved main provider is verified as it is (R01).
    if (wizardState.hermes_mode === 'full' && _llmFormOwnsProvider()) {
        const persisted = await _persistLLMSelectionBeforeGateway();
        if (!persisted?.provider) {
            if (btn) {
                btn.disabled = false;
                setIconText(btn, '🔄 Reintentar');
            }
            if (summary) {
                setIconText(summary, `❌ No se pudo verificar la selección actual: ${persisted?.message || 'proveedor o modelo incompleto.'}`);
                summary.style.color = 'var(--red)';
            }
            return;
        }
        void loadLlmSetup();  // the saved main provider changed: the section and the Gateway follow it
    } else {
        await maybeImportHermesConfig();
    }

    _renderHealthcheckSkeleton();

    const results = [];
    const es = new EventSource('/api/health-check-stream');
    _healthcheckStream = es;

    es.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }

        if (msg.type === 'check') {
            results.push(msg);
            _updateHealthcheckRow(msg);
        } else if (msg.type === 'done') {
            es.close();
            _healthcheckStream = null;
            if (btn) {
                btn.disabled = false;
                setIconText(btn, '🔄 Re-verificar');
            }
            const ok = results.filter(r => r.status === 'ok').length;
            const total = results.length || HEALTHCHECK_ROWS.length;
            const inactive = results.filter(r => r.status === 'inactive').length;
            const failures = results.filter(r => r.status === 'fail').length;
            if (summary) {
                setIconText(summary, msg.ok
                    ? `✅ ${ok}/${total} comprobaciones listas — conexión completa.`
                    : failures > 0
                        ? `❌ ${failures} problema(s) de configuración · ${ok}/${total} comprobaciones listas.`
                        : `🟡 Configuración válida · ${inactive} servicio(s) local(es) todavía apagado(s).`);
                summary.style.color = msg.ok
                    ? 'var(--green)'
                    : failures > 0 ? 'var(--red)' : 'var(--amber)';
            }
        } else if (msg.type === 'error') {
            es.close();
            _healthcheckStream = null;
            if (btn) {
                btn.disabled = false;
                setIconText(btn, '🔄 Reintentar');
            }
            if (summary) {
                setIconText(summary, `❌ Error: ${msg.message}`);
                summary.style.color = 'var(--red)';
            }
        }
    };

    es.onerror = () => {
        // Fires on clean .close() too — only treat as error if we haven't finished
        if (_healthcheckStream !== es) return;
        es.close();
        _healthcheckStream = null;
        if (btn) {
            btn.disabled = false;
            setIconText(btn, '🔄 Reintentar');
        }
        if (summary) {
            setIconText(summary, '❌ Se perdió la conexión con el wizard backend.');
            summary.style.color = 'var(--red)';
        }
    };
}

// ── Step 3: TTS Testing ─────────────────────────────────

/**
 * Generate + play a TTS preview for `provider` and surface playback
 * controls in the wizard's TTS step.
 *
 * Flow:
 *   1. Read the user's editable phrase from `#tts-test-text` (default
 *      "Hola mundo, soy IRIS.").
 *   2. Pull the currently-selected voice from whichever sub-control
 *      applies to this provider — the request hits `/api/test-tts-audio`
 *      which spins up the matching TTS engine and returns audio bytes.
 *   3. Wrap the bytes in a Blob URL and feed an `<audio>` element so
 *      the browser plays through whatever output device the user is
 *      actually listening on (works correctly even when the wizard is
 *      hit remotely via LAN access).
 *   4. Auto-play on first generation; subsequent clicks replay the
 *      same audio without re-generating until provider/voice/phrase
 *      change. Saves time + tokens for repeat clicks.
 *
 * Errors come back as JSON (`{status:"error", message:"…"}`) — we
 * detect that vs. real audio by sniffing the Content-Type, and
 * surface the message verbatim so users know if they need to install
 * extras, paste an API key, etc.
 */
let _ttsPreviewBlobUrl = null;
let _ttsPreviewKey = '';   // "provider|voice|text" — replays without regen when unchanged
let _ttsPreviewGeneration = 0;
let _ttsPreviewController = null;

function resetTTSPreview() {
    document.getElementById('tts-test-result')?.querySelector('audio')?.pause();
    _ttsPreviewKey = '';
    _ttsPreviewGeneration += 1;
    _ttsPreviewController?.abort();
    if (_ttsPreviewBlobUrl) URL.revokeObjectURL(_ttsPreviewBlobUrl);
    _ttsPreviewBlobUrl = null;
    const result = document.getElementById('tts-test-result');
    if (result) { result.replaceChildren(); result.style.display = 'none'; }
}

let _fishAudioSearchGeneration = 0;
let _fishAudioPage = 0;
let _fishAudioSearchIdentity = '';
let _fishAudioSampleGeneration = 0;
let _fishAudioSampleUrl = null;
let _fishAudioSampleController = null;
let _fishAudioSampleAudio = null;
// Voices listed so far: id → name, languages, popularity, whether Fish published a sample.
const _fishAudioVoices = new Map();

function _fishAudioKey() {
    return document.getElementById('key-fish-audio')?.value?.trim() || '';
}

function _fishAudioVoiceLabel(voice) {
    const parts = [voice.name];
    if (voice.languages?.length) parts.push(voice.languages.slice(0, 3).join('/'));
    if (voice.likes) parts.push(`${new Intl.NumberFormat('es', { notation: 'compact' }).format(voice.likes)} ♥`);
    return parts.join(' · ');
}

// The chosen voice's name rides on its id field and is saved beside the id,
// so a revisit shows more than the id. A pasted id has no known name.
function _setFishAudioVoiceName(name, voice) {
    const input = document.getElementById('key-fish-audio-voice');
    if (input) input.dataset.voiceName = name;
    const label = document.getElementById('fish-audio-voice-name');
    if (!label) return;
    label.textContent = name ? `Voz elegida: ${name}${voice?.has_sample === false ? ' · sin muestra publicada' : ''}` : '';
    label.style.display = name ? 'block' : 'none';
}

function _stopFishAudioSample() {
    _fishAudioSampleGeneration += 1;
    _fishAudioSampleController?.abort();
    _fishAudioSampleController = null;
    if (_fishAudioSampleAudio) {
        _fishAudioSampleAudio.pause();
        _fishAudioSampleAudio.removeAttribute('src');
        _fishAudioSampleAudio.load();
        _fishAudioSampleAudio = null;
    }
    if (_fishAudioSampleUrl) URL.revokeObjectURL(_fishAudioSampleUrl);
    _fishAudioSampleUrl = null;
    const player = document.getElementById('fish-audio-sample-player');
    if (player) { player.replaceChildren(); player.style.display = 'none'; }
    const voiceId = document.getElementById('key-fish-audio-voice')?.value?.trim() || '';
    const button = document.getElementById('fish-audio-sample');
    if (button) button.disabled = !voiceId || _fishAudioVoices.get(voiceId)?.has_sample === false;
}

function _clearFishAudioResults() {
    _fishAudioVoices.clear();
    _fishAudioPage = 0;
    _fishAudioSearchIdentity = '';
    const select = document.getElementById('fish-audio-voices');
    if (select) { select.replaceChildren(); select.disabled = true; }
    const more = document.getElementById('fish-audio-more');
    if (more) more.style.display = 'none';
}

function resetFishAudioAccount() {
    resetTTSPreview();
    _stopFishAudioSample();
    _fishAudioSearchGeneration += 1;
    _clearFishAudioResults();
    const button = document.getElementById('fish-audio-search');
    if (button) button.disabled = false;
    const status = document.getElementById('fish-audio-search-status');
    if (status) status.textContent = 'La clave cambió. Pulsa Buscar voces para consultar esa cuenta.';
}

function _fishAudioSearchFilters() {
    return {
        query: document.getElementById('fish-audio-query')?.value?.trim() || '',
        self_only: Boolean(document.getElementById('fish-audio-self')?.checked),
        language: document.getElementById('fish-audio-language')?.value || '',
    };
}

async function searchFishAudioVoices({ more = false } = {}) {
    const generation = ++_fishAudioSearchGeneration;
    const status = document.getElementById('fish-audio-search-status');
    const select = document.getElementById('fish-audio-voices');
    const button = document.getElementById('fish-audio-search');
    const moreButton = document.getElementById('fish-audio-more');
    const key = _fishAudioKey();
    if (!status || !select) return;
    const filters = _fishAudioSearchFilters();
    const identity = JSON.stringify(filters);
    // Continue only the search that produced the current list.
    const append = more && _fishAudioPage > 0 && identity === _fishAudioSearchIdentity;
    const page = append ? _fishAudioPage + 1 : 1;
    status.textContent = 'Consultando las voces de Fish Audio…';
    if (button) button.disabled = true;
    if (moreButton) moreButton.disabled = true;
    try {
        const headers = { 'Content-Type': 'application/json', 'X-IRIS-Intent': 'preview-tts' };
        if (key) headers['X-IRIS-TTS-Key'] = key;
        const response = await fetch('/api/fish-audio/voices', { method: 'POST', credentials: 'same-origin', headers,
            body: JSON.stringify({ ...filters, page }) });
        const data = await response.json();
        if (generation !== _fishAudioSearchGeneration || key !== _fishAudioKey()
                || identity !== JSON.stringify(_fishAudioSearchFilters())) return;
        if (!response.ok) throw new Error(data.message || 'No se pudo consultar el catálogo.');
        if (!append) {
            _clearFishAudioResults();
            const placeholder = document.createElement('option');
            placeholder.value = ''; placeholder.textContent = 'Selecciona una voz'; select.appendChild(placeholder);
        }
        for (const voice of data.voices || []) {
            if (_fishAudioVoices.has(voice.id)) continue;   // pages overlap when the ranking moves
            _fishAudioVoices.set(voice.id, voice);
            const option = document.createElement('option');
            option.value = voice.id; option.textContent = _fishAudioVoiceLabel(voice); select.appendChild(option);
        }
        _fishAudioPage = page;
        _fishAudioSearchIdentity = identity;
        select.disabled = !_fishAudioVoices.size;
        if (moreButton) moreButton.style.display = data.has_more ? '' : 'none';
        status.textContent = _fishAudioVoices.size
            ? `${_fishAudioVoices.size} voces, las más populares primero. Elige una para escuchar su muestra gratis.${data.has_more ? ' «Ver más voces» trae la página siguiente.' : ''}`
            : 'No se encontraron voces. Prueba otro nombre u otro idioma, o clona la tuya.';
    } catch (error) {
        if (generation !== _fishAudioSearchGeneration) return;
        // A failed search leaves no results from the previous one under its error.
        if (!append) _clearFishAudioResults();
        status.textContent = error.message || 'No se pudo consultar Fish Audio.';
    } finally {
        if (generation === _fishAudioSearchGeneration) {
            if (button) button.disabled = false;
            if (moreButton) moreButton.disabled = false;
        }
    }
}

function _fishAudioVoiceChanged() {
    resetTTSPreview();
    _stopFishAudioSample();
    const voiceId = document.getElementById('key-fish-audio-voice')?.value?.trim() || '';
    const sample = document.getElementById('fish-audio-sample');
    if (sample) sample.disabled = !voiceId || _fishAudioVoices.get(voiceId)?.has_sample === false;
}

function chooseFishAudioVoice() {
    const selected = document.getElementById('fish-audio-voices')?.value;
    const input = document.getElementById('key-fish-audio-voice');
    if (!input || !selected) return;
    input.value = selected;
    const voice = _fishAudioVoices.get(selected);
    _setFishAudioVoiceName(voice?.name || '', voice);
    _fishAudioVoiceChanged();
    markVoiceDraftChanged();
}

function fishAudioVoiceTyped() {
    _setFishAudioVoiceName('');
    _fishAudioVoiceChanged();
}

// The sample Fish published with the voice: no synthesis, so it uses no credit.

// Save exactly the active voice card. Other wizard drafts are left untouched.
let _voiceDraftRevision = 0;
let _voiceSavedRevision = 0;
function markVoiceDraftChanged() {
    _voiceDraftRevision += 1;
    const status = document.getElementById('voice-save-status');
    if (status) status.textContent = 'Cambios de voz sin guardar.';
}
document.getElementById('save-voice-selection')?.closest?.('.wizard-step')?.addEventListener('input', (event) => {
    if (event.target.closest('.iris-audio-player')) return;
    markVoiceDraftChanged();
});

function updateVoiceSaveButton() {
    const input = document.querySelector('input[name="tts"]:checked');
    const button = document.getElementById('save-voice-selection');
    const label = input?.closest?.('.radio-card')?.querySelector('strong')?.textContent || input?.value || 'Elegida';
    if (button) button.textContent = 'Guardar voz · ' + label;
    const status = document.getElementById('voice-save-status');
    if (status) status.textContent = _voiceDraftRevision > _voiceSavedRevision ? 'Cambios de voz sin guardar.' : '';
}

async function saveVoiceSelection() {
    const revision = _voiceDraftRevision;
    const button = document.getElementById('save-voice-selection');
    const status = document.getElementById('voice-save-status');
    const provider = document.querySelector('input[name="tts"]:checked')?.value || 'edge-tts';
    const value = id => document.getElementById(id)?.value?.trim() || '';
    const fields = {
        'edge-tts': { edge_tts_voice: selectedVoice || wizardState.edge_tts_voice || 'es-MX-DaliaNeural' },
        kokoro: { kokoro_voice: value('kokoro-voice') || 'ef_dora' },
        elevenlabs: { elevenlabs_api_key: value('key-elevenlabs'), elevenlabs_voice_id: value('key-elevenlabs-voice') },
        minimax: { minimax_api_key: value('key-minimax'), minimax_voice_id: value('key-minimax-voice') || 'Spanish_Wiselady' },
        'fish-audio': { fish_audio_api_key: value('key-fish-audio'), fish_audio_voice_id: value('key-fish-audio-voice'),
            fish_audio_voice_name: document.getElementById('key-fish-audio-voice')?.dataset.voiceName || '', fish_audio_model: value('fish-audio-model') },
        'hf-s2s': { hf_s2s_model: value('hf-s2s-model'), hf_s2s_speaker: value('hf-s2s-speaker') || 'Aiden' },
        'fish-speech': { fish_speech_url: value('fish-speech-url'), fish_speech_ref_audio: value('fish-speech-ref-audio'),
            fish_speech_ref_text: value('fish-speech-ref-text'), fish_speech_reference_id: value('fish-speech-reference-id') },
    };
    if (button) button.disabled = true;
    if (status) status.textContent = 'Guardando esta voz…';
    try {
        const response = await fetch('/api/save-voice-selection', { method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-IRIS-Intent': 'save-voice' },
            body: JSON.stringify({ tts_provider: provider,
                tts_allow_remote_fallback: provider !== 'edge-tts' && Boolean(document.getElementById('tts-allow-remote-fallback')?.checked),
                ...(fields[provider] || {}) }) });
        const data = await response.json();
        if (!response.ok || data.status !== 'saved') throw new Error(data.message || 'No se pudo guardar la voz.');
        _voiceSavedRevision = revision;
        if (status) status.textContent = revision === _voiceDraftRevision
            ? data.message : 'Voz guardada. Hay cambios posteriores sin guardar.';
    } catch (error) {
        if (status) status.textContent = error.message || 'No se pudo guardar la voz.';
    } finally { if (button) button.disabled = false; }
}

function createVoiceSamplePlayer(audio) {
    const box = document.createElement('div');
    box.className = 'iris-audio-player';
    const toggle = document.createElement('button');
    toggle.type = 'button'; toggle.className = 'btn-test';
    const seek = document.createElement('input');
    seek.type = 'range'; seek.min = '0'; seek.max = '1000'; seek.value = '0'; seek.disabled = true;
    seek.setAttribute('aria-label', 'Posición de la muestra de voz');
    const elapsed = document.createElement('span'); elapsed.className = 'dim';
    const format = seconds => Number.isFinite(seconds) ? Math.floor(seconds / 60) + ':' + String(Math.floor(seconds % 60)).padStart(2, '0') : '0:00';
    const refresh = () => {
        toggle.textContent = audio.paused ? '▶ Reproducir' : 'Ⅱ Pausar';
        toggle.setAttribute('aria-label', audio.paused ? 'Reproducir muestra' : 'Pausar muestra');
        const ready = Number.isFinite(audio.duration) && audio.duration > 0;
        seek.disabled = !ready;
        seek.value = ready ? String(Math.round(1000 * audio.currentTime / audio.duration)) : '0';
        elapsed.textContent = format(audio.currentTime) + ' / ' + format(audio.duration);
        seek.setAttribute('aria-valuetext', elapsed.textContent);
    };
    toggle.addEventListener('click', () => {
        if (!audio.paused) audio.pause();
        else audio.play().catch(() => { elapsed.textContent = 'No se pudo reproducir la muestra.'; });
    });
    seek.addEventListener('input', () => { if (!seek.disabled) audio.currentTime = Number(seek.value) * audio.duration / 1000; });
    for (const event of ['play', 'pause', 'ended', 'loadedmetadata', 'durationchange', 'timeupdate']) audio.addEventListener(event, refresh);
    audio.addEventListener('error', () => { elapsed.textContent = 'No se pudo reproducir la muestra.'; });
    audio.hidden = true;
    box.append(toggle, seek, elapsed, audio); refresh();
    return box;
}

async function playFishAudioSample() {
    const voiceId = document.getElementById('key-fish-audio-voice')?.value?.trim() || '';
    const player = document.getElementById('fish-audio-sample-player');
    const button = document.getElementById('fish-audio-sample');
    if (!voiceId || !player) return;
    _stopFishAudioSample();
    const generation = _fishAudioSampleGeneration;
    const key = _fishAudioKey();
    const controller = new AbortController();
    _fishAudioSampleController = controller;
    player.style.display = 'block';
    player.textContent = 'Trayendo la muestra de Fish…';
    if (button) button.disabled = true;
    try {
        const headers = { 'Content-Type': 'application/json', 'X-IRIS-Intent': 'preview-tts' };
        if (key) headers['X-IRIS-TTS-Key'] = key;
        const response = await fetch('/api/fish-audio/sample', { method: 'POST', credentials: 'same-origin', headers,
            signal: controller.signal, body: JSON.stringify({ voice_id: voiceId }) });
        if (generation !== _fishAudioSampleGeneration) return;
        if (!(response.headers.get('content-type') || '').startsWith('audio/')) {
            const data = await response.json().catch(() => ({}));
            throw new Error(data.message || `No se pudo traer la muestra (HTTP ${response.status}).`);
        }
        const blob = await response.blob();
        if (generation !== _fishAudioSampleGeneration) return;
        _fishAudioSampleUrl = URL.createObjectURL(blob);
        const audio = document.createElement('audio');
        _fishAudioSampleAudio = audio;
        audio.src = _fishAudioSampleUrl;
        player.replaceChildren(createVoiceSamplePlayer(audio));
        audio.play?.()?.catch?.(() => {});
    } catch (error) {
        if (generation === _fishAudioSampleGeneration) player.textContent = error.message || 'No se pudo traer la muestra.';
    } finally {
        if (_fishAudioSampleController === controller) _fishAudioSampleController = null;
        if (generation === _fishAudioSampleGeneration && button) button.disabled = false;
    }
}

async function testTTS(provider, event) {
    if (event) event.stopPropagation();

    const generation = ++_ttsPreviewGeneration;
    _ttsPreviewController?.abort();
    _ttsPreviewController = new AbortController();

    const result = document.getElementById('tts-test-result');
    if (!result) return;

    // Pull the editable phrase. Default kicks in if the user cleared
    // the input — server also enforces a default, but doing it here
    // means the cache key matches what we send.
    const textInput = document.getElementById('tts-test-text');
    const text = (textInput?.value || '').trim() || 'Hola mundo, soy IRIS.';

    // Resolve provider-specific voice id. For most providers we leave
    // it empty and let the backend use the saved config; for edge-tts
    // we pull from the active voice selector (#voice-select dropdown
    // populated by /api/tts-voices).
    let voice = '';
    let model = '';
    if (provider === 'edge-tts') {
        voice = selectedVoice
             || document.querySelector('input[name="voice"]:checked')?.value
             || document.getElementById('voice-select')?.value
             || '';
    } else if (provider === 'kokoro') {
        voice = document.getElementById('kokoro-voice')?.value || '';
    } else if (provider === 'elevenlabs') {
        voice = document.getElementById('key-elevenlabs-voice')?.value?.trim()
             || (wizardState.cloned_voice_provider === provider ? wizardState.cloned_voice_id : '')
             || '';
    } else if (provider === 'fish-audio') {
        voice = document.getElementById('key-fish-audio-voice')?.value?.trim() || '';
        model = document.getElementById('fish-audio-model')?.value || 's2.1-pro-free';
    } else if (provider === 'minimax') {
        voice = document.getElementById('key-minimax-voice')?.value?.trim()
             || (wizardState.cloned_voice_provider === provider ? wizardState.cloned_voice_id : '')
             || 'Spanish_Wiselady';
    } else if (provider === 'hf-s2s') {
        voice = document.getElementById('hf-s2s-speaker')?.value || 'Aiden';
        model = document.getElementById('hf-s2s-model')?.value
             || 'Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice';
    }

    const fishUrl = provider === 'fish-speech'
        ? (document.getElementById('fish-speech-url')?.value?.trim() || '')
        : '';
    const cacheKey = `${provider}|${model}|${voice}|${fishUrl}|${text}`;
    if (_ttsPreviewBlobUrl && _ttsPreviewKey === cacheKey) {
        // Same request as last time — just replay the cached audio.
        result.style.display = 'block';
        const player = result.querySelector('audio');
        if (player) {
            player.currentTime = 0;
            player.play().catch(() => {});
            return;
        }
        // Audio element got removed somehow — fall through and regenerate.
    }

    result.querySelector('audio')?.pause();
    result.style.display = 'block';
    const localEngine = ['kokoro', 'hf-s2s', 'coqui', 'neutts'].includes(provider);
    result.innerHTML = `<div class="loading-spinner"></div><p>Generando audio con ${escapeHtml(provider)}…</p>${localEngine
        ? '<p class="dim">La primera prueba de un motor local descarga su modelo a tu perfil y puede tardar varios minutos. Puedes seguir configurando; la descarga continúa.</p>'
        : ''}`;

    try {
        const previewKey = ['elevenlabs', 'minimax', 'fish-audio'].includes(provider)
            ? (document.getElementById(`key-${provider}`)?.value?.trim() || '') : '';
        const previewHeaders = {
            'Content-Type': 'application/json',
            'X-IRIS-Intent': 'preview-tts',
        };
        if (previewKey) previewHeaders['X-IRIS-TTS-Key'] = previewKey;
        const res = await fetch('/api/test-tts-audio', {
            method: 'POST',
            credentials: 'same-origin',
            headers: previewHeaders,
            body: JSON.stringify({ provider, voice, model, text, fish_url: fishUrl }),
            signal: _ttsPreviewController.signal,
        });
        if (generation !== _ttsPreviewGeneration) return;
        const ct = res.headers.get('content-type') || '';

        if (!ct.startsWith('audio/')) {
            // Backend returned JSON error / info instead of audio.
            const d = await res.json().catch(() => ({ message: `HTTP ${res.status}` }));
            result.innerHTML = `<div class="err"><wz-icon>❌</wz-icon> ${escapeHtml(d.message || 'Error generando audio')}</div>`;
            return;
        }

        const blob = await res.blob();
        if (generation !== _ttsPreviewGeneration) return;
        // Free the previous blob so we don't leak object URLs across
        // many test clicks. Browsers cap at ~16 MB of un-revoked URLs
        // per origin which is generous, but the cleanup is cheap.
        if (_ttsPreviewBlobUrl) URL.revokeObjectURL(_ttsPreviewBlobUrl);
        _ttsPreviewBlobUrl = URL.createObjectURL(blob);
        _ttsPreviewKey = cacheKey;

        const sizeKB = (blob.size / 1024).toFixed(1);
        result.innerHTML = `
            <div class="ok"><wz-icon>✅</wz-icon> <strong>${provider}</strong> · ${sizeKB} KB · "${_escapeHtml(text)}"</div>
        `;
        const audio = new Audio(_ttsPreviewBlobUrl);
        result.appendChild(createVoiceSamplePlayer(audio));
        audio.play().catch(() => {}); // The visible themed button remains available.
    } catch (e) {
        if (e?.name === 'AbortError' || generation !== _ttsPreviewGeneration) return;
        result.innerHTML = `<div class="err"><wz-icon>❌</wz-icon> Error: ${escapeHtml(e.message || e)}</div>`;
    }
}

function _escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// ── Step 4: Model Upload ────────────────────────────────

const uploadZone = document.getElementById('upload-zone');
const modelFileInput = document.getElementById('model-file');

if (uploadZone) {
    uploadZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadZone.classList.add('drag-active');
    });
    uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-active'));
    uploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadZone.classList.remove('drag-active');
        const file = e.dataTransfer?.files?.[0];
        if (file) uploadModel(file);
    });
}

if (modelFileInput) {
    modelFileInput.addEventListener('change', () => {
        const file = modelFileInput.files?.[0];
        if (file) uploadModel(file);
    });
}

async function uploadModel(file) {
    const infoPanel = document.getElementById('model-info');
    infoPanel.style.display = 'block';
    infoPanel.innerHTML = '<div class="loading-spinner"></div><p>Subiendo modelo...</p>';

    const form = new FormData();
    form.append('file', file);

    try {
        const res = await fetch('/api/upload-model', { method: 'POST', body: form });
        const d = await res.json();
        if (!res.ok) throw new Error(d?.detail || d?.message || `HTTP ${res.status}`);

        wizardState.uploadedModel = d.filename;
        wizardState.avatar_vrm_path = d.path;

        const sizeKB = Math.round(d.size_bytes / 1024);
        infoPanel.innerHTML = `
            <div class="ok"><wz-icon>✅</wz-icon> Modelo subido: ${escapeHtml(d.filename)}</div>
            <div class="info-row"><span class="label">Formato</span><span class="value">.${d.format}</span></div>
            <div class="info-row"><span class="label">Tamaño</span><span class="value">${sizeKB} KB</span></div>
            ${d.vertices ? `<div class="info-row"><span class="label">Vértices</span><span class="value">${d.vertices.toLocaleString()}</span></div>` : ''}
            ${d.faces ? `<div class="info-row"><span class="label">Caras</span><span class="value">${d.faces.toLocaleString()}</span></div>` : ''}
        `;

        // Show rig option for non-VRM formats
        if (d.needs_conversion) {
            document.getElementById('rig-section').style.display = 'block';
        }

    } catch (e) {
        infoPanel.innerHTML = `<div class="err">Error subiendo modelo: ${escapeHtml(e.message)}</div>`;
    } finally {
        if (modelFileInput) modelFileInput.value = '';
    }
}

// ── Step 5: Animation library ──────────────────────────
//
// Each row = one "slot" the avatar knows about (idle, wave, dance…).
// Uploading .vrma or .bvh to a slot saves it under face/animations/ and
// rewrites manifest.json — the running IRIS frontend picks it up next
// revision revalidation while visible (including a separate Wizard process).

/** Friendly label per slot — keeps the grid readable. */
const _ANIM_SLOT_LABELS = {
    idle:         'Idle (principal)',
    idle_casual:  'Idle casual',
    idle_smile:   'Idle · sonrisa',
    idle_love:    'Idle · cariño',
    idle_noble:   'Idle · noble',
    idle_weave:   'Idle · vínculo',
    wave:         'Saludo',
    think:        'Pensar',
    nod:          'Asentir',
    shake:        'Negar',
    look_around:  'Mirar alrededor',
    stretch:      'Estirarse',
    hair_touch:   'Tocarse el pelo',
    shy:          'Tímida',
    sit:          'Sentarse',
    lie:          'Recostarse',
    dance:        'Bailar',
};

async function refreshAnimationSlots() {
    const root = document.getElementById('animation-slots');
    if (!root) return;

    let data;
    try {
        const res = await fetch('/api/animation-slots', { cache: 'no-store' });
        data = await res.json();
        if (!res.ok || data?.error) throw new Error(data?.error || `HTTP ${res.status}`);
    } catch (e) {
        root.innerHTML = `<div class="err" style="padding:12px">No se pudo cargar la lista de slots: ${escapeHtml(e.message)}</div>`;
        return;
    }

    const slots = data?.slots || [];
    const accepted = (data?.accepted_formats || ['vrma', 'bvh'])
        .map(x => `.${x}`).join(',');

    if (slots.length === 0) {
        root.innerHTML = '<div class="dim" style="padding:12px">Sin slots definidos.</div>';
        return;
    }

    root.innerHTML = slots.map(s => {
        const label = _ANIM_SLOT_LABELS[s.slot] || s.slot;
        const badgeClass = s.present ? 'anim-slot-badge present' : 'anim-slot-badge';
        const badgeText = s.present ? (s.format || 'ok') : 'vacío';
        const fileLine = s.filename
            ? `<div class="anim-slot-file" title="${escapeHtml(s.filename)}">${escapeHtml(s.filename)}</div>`
            : '<div class="anim-slot-file">— sin archivo —</div>';
        const slotClass = s.present ? 'anim-slot has-file' : 'anim-slot';
        return `
            <div class="${slotClass}" data-slot="${s.slot}">
                <div class="anim-slot-head">
                    <span class="anim-slot-name">${escapeHtml(label)}</span>
                    <span class="${badgeClass}">${escapeHtml(badgeText)}</span>
                </div>
                ${fileLine}
                <div class="anim-slot-actions">
                    <label class="btn-upload">
                        <wz-icon>⬆</wz-icon> Subir archivo
                        <input type="file" accept="${accepted}"
                               onchange="uploadAnimationFile(this, '${s.slot}')">
                    </label>
                </div>
                <div class="anim-slot-msg"></div>
            </div>
        `;
    }).join('');
}

// Animation extensions the upload UI accepts. MUST stay in sync with
// ``wizard/app.py`` ``ANIMATION_EXTENSIONS`` and ``face/js/vrm_animation_loader.js``
// dispatcher. The `<input accept>` attribute is built dynamically from the
// backend's response (see refreshAnimationSlots), but this client-side
// guard short-circuits the upload before the user waits on a multi-MB POST
// just to get a 400 back. Order matters cosmetically (error message uses .join).
const _ACCEPTED_ANIM_EXTS = ['vrma', 'bvh', 'fbx', 'glb', 'gltf'];

async function uploadAnimationFile(inputEl, slot) {
    const file = inputEl.files?.[0];
    if (!file) return;
    const row = inputEl.closest('.anim-slot');
    const msg = row?.querySelector('.anim-slot-msg');

    const ext = (file.name.split('.').pop() || '').toLowerCase();
    if (!_ACCEPTED_ANIM_EXTS.includes(ext)) {
        const expected = _ACCEPTED_ANIM_EXTS.map(e => '.' + e).join(', ');
        if (msg) { msg.textContent = `Formato .${ext} no aceptado (acepta: ${expected})`; msg.className = 'anim-slot-msg err'; }
        inputEl.value = '';
        return;
    }

    row?.classList.remove('error');
    row?.classList.add('uploading');
    if (msg) { msg.textContent = `Subiendo ${file.name}…`; msg.className = 'anim-slot-msg'; }

    const form = new FormData();
    form.append('file', file);
    form.append('slot', slot);

    try {
        const res = await fetch('/api/upload-animation', { method: 'POST', body: form });
        const d = await res.json();
        if (!res.ok) throw new Error(d?.detail || d?.message || `HTTP ${res.status}`);

        // Refresh the row before showing the result so warnings remain visible.
        await refreshAnimationSlots();
        const currentRow = document.querySelector(`.anim-slot[data-slot="${slot}"]`) || row;
        const currentMsg = currentRow?.querySelector('.anim-slot-msg') || msg;
        currentRow?.classList.remove('uploading');
        if (currentMsg) {
            const sizeKB = Math.round((d.size_bytes || 0) / 1024);
            const warnings = Array.isArray(d.warnings) ? d.warnings.filter(Boolean) : [];
            setIconText(currentMsg, `✓ Guardado como ${d.filename} (${sizeKB} KB)` +
                (warnings.length ? ` · ${warnings.join(' ')}` : ''));
            currentMsg.className = warnings.length ? 'anim-slot-msg warn' : 'anim-slot-msg ok';
        }
    } catch (e) {
        row?.classList.remove('uploading');
        row?.classList.add('error');
        if (msg) { msg.textContent = `Error: ${e.message}`; msg.className = 'anim-slot-msg err'; }
    } finally {
        inputEl.value = '';
    }
}

async function rigModel() {
    const result = document.getElementById('rig-result');
    result.style.display = 'block';
    result.innerHTML = '<div class="loading-spinner"></div><p>Auto-rigging en progreso... esto puede tardar hasta 2 minutos.</p>';

    try {
        const res = await fetch(`/api/rig-model?filename=${encodeURIComponent(wizardState.uploadedModel)}&method=auto`, {
            method: 'POST',
        });
        const d = await res.json();
        if (!res.ok) {
            throw new Error(d.message || (typeof d.detail === 'string' ? d.detail : `No se pudo preparar el rigging (HTTP ${res.status}).`));
        }

        if (d.status === 'success') {
            wizardState.avatar_vrm_path = d.output_path;

            if (d.method === 'heuristic') {
                result.innerHTML = `<div class="warn"><wz-icon>⚠</wz-icon> Rigging superficial. No tienes <b>Blender</b> instalado, por lo que IRIS mapeó tu modelo pero NO le inyectó esqueleto físico real. Si el modelo no se mueve, necesitarás instalar <a href="https://www.blender.org/download/" target="_blank">Blender 4.2+</a> y volver a subirlo.</div>
                <div class="info-row"><span class="label">Archivo</span><span class="value">${d.output_path.split(/[\\/]/).pop()}</span></div>`;
            } else if (d.method === 'physical_vrm' || d.method === 'blender') {
                result.innerHTML = `
                    <div class="ok"><wz-icon>✅</wz-icon> Rigging físico completado (${d.method})</div>
                    <div class="info-row"><span class="label">Huesos inyectados</span><span class="value">${d.bones_found}</span></div>
                    <div class="info-row"><span class="label">Archivo</span><span class="value">${d.output_path.split(/[\\/]/).pop()}</span></div>
                `;
            } else {
                result.innerHTML = `
                    <div class="ok"><wz-icon>✅</wz-icon> Auto-rigging completado (${d.method})</div>
                    <div class="info-row"><span class="label">Huesos</span><span class="value">${d.bones_found}</span></div>
                    <div class="info-row"><span class="label">Archivo</span><span class="value">${d.output_path.split(/[\\/]/).pop()}</span></div>
                `;
            }
        } else {
            result.innerHTML = `<div class="err"><wz-icon>❌</wz-icon> ${escapeHtml(d.message)}</div>`;
        }
    } catch (e) {
        result.innerHTML = `<div class="err">Error: ${escapeHtml(e.message)}</div>`;
    }
}

// ── Step 5: Audio Test ──────────────────────────────────

// ── Voice Browser ───────────────────────────────────────

let allVoices = [];
let selectedVoice = 'es-MX-DaliaNeural';
let _voiceListGeneration = 0;
let _voiceListController = null;

async function loadVoices() {
    const generation = ++_voiceListGeneration;
    _voiceListController?.abort();
    _voiceListController = new AbortController();
    const lang = document.getElementById('voice-lang-filter')?.value || 'es';
    const list = document.getElementById('voice-list');
    if (!list) return;

    list.innerHTML = '<div class="loading-spinner"></div><p class="dim">Cargando voces...</p>';

    try {
        const res = await fetch(`/api/voices?lang=${encodeURIComponent(lang)}`, {
            signal: _voiceListController.signal,
        });
        const data = await res.json();
        if (generation !== _voiceListGeneration) return;
        allVoices = data.voices || [];
        // The endpoint answers 200 with an `error` when Edge's catalog is unreachable:
        // that is not an empty filter (offline QA showed «No hay voces para este filtro»).
        if (!allVoices.length && data.error) {
            _renderVoiceListError(data.error === 'edge-tts not installed' ? 'missing' : 'offline');
            return;
        }
        renderVoices(allVoices);
    } catch (e) {
        if (e?.name === 'AbortError' || generation !== _voiceListGeneration) return;
        _renderVoiceListError('offline');
    }
}

function _renderVoiceListError(reason) {
    const list = document.getElementById('voice-list');
    if (!list) return;
    const message = reason === 'missing'
        ? 'El motor Edge-TTS no está instalado en este equipo.'
        : 'No pude cargar las voces de Edge-TTS. Necesita conexión a internet.';
    list.innerHTML = `<div class="voice-list-error" role="status"><p><wz-icon>⚠</wz-icon> ${message}</p>`
        + '<p class="dim">Puedes reintentar o elegir una voz en «Voces locales», que funcionan sin internet.</p>'
        + '<button type="button" class="btn-secondary" onclick="loadVoices()">Reintentar</button></div>';
}

function filterVoices() {
    loadVoices();
}

function filterVoicesLocal() {
    const gender = document.getElementById('voice-gender-filter')?.value || '';
    const filtered = gender
        ? allVoices.filter(v => v.gender === gender)
        : allVoices;
    renderVoices(filtered);
}

function renderVoices(voices) {
    const list = document.getElementById('voice-list');
    if (!list || !voices.length) {
        if (list) list.innerHTML = '<p class="dim">No hay voces para este filtro.</p>';
        return;
    }

    list.innerHTML = voices.map(v => {
        const voiceId = String(v.id || '');
        const voiceName = String(v.name || voiceId);
        const voiceLang = String(v.lang || '');
        const isSelected = voiceId === selectedVoice;
        const isFav = v.favorite;
        const genderIcon = v.gender === 'Female' ? '♀' : '♂';
        const langCode = voiceLang.split('-').slice(0, 2).join('-');
        const displayName = voiceId.split('-').pop().replace('Neural', '');

        return `
            <div class="voice-card ${isSelected ? 'selected' : ''} ${isFav ? 'favorite' : ''}"
                 data-voice-id="${escapeHtml(voiceId)}"
                 data-voice-name="${escapeHtml(voiceName)}">
                <button type="button" class="voice-select voice-info" aria-pressed="${isSelected}"
                    aria-label="Elegir ${escapeHtml(displayName)} · ${escapeHtml(voiceLang)}"
                    onclick="selectVoice(this.closest('.voice-card').dataset.voiceId, this.closest('.voice-card').dataset.voiceName)">
                    <strong>${escapeHtml(displayName)} ${genderIcon}</strong>
                    <span class="dim">${escapeHtml(langCode)}</span>
                    ${isFav ? '<span class="badge mini"><wz-icon>⭐</wz-icon></span>' : ''}
                </button>
                <button type="button" class="btn-preview" aria-label="Escuchar ${escapeHtml(displayName)} · ${escapeHtml(voiceLang)}" onclick="previewVoice(this.closest('.voice-card').dataset.voiceId, event)">▶</button>
            </div>
        `;
    }).join('');
}

function selectVoice(voiceId, voiceName) {
    selectedVoice = voiceId;
    wizardState.edge_tts_voice = voiceId;

    // Update visual selection
    document.querySelectorAll('.voice-card').forEach(c => {
        c.classList.remove('selected');
        c.querySelector('.voice-select')?.setAttribute('aria-pressed', 'false');
    });
    const card = Array.from(document.querySelectorAll('.voice-card'))
        .find(candidate => candidate.dataset.voiceId === voiceId);
    if (card) {
        card.classList.add('selected');
        card.querySelector('.voice-select')?.setAttribute('aria-pressed', 'true');
    }

    // Show selection confirmation
    const sel = document.getElementById('voice-selected');
    const name = document.getElementById('voice-selected-name');
    if (sel) sel.style.display = 'block';
    if (name) name.textContent = voiceId.split('-').pop().replace('Neural', '');
    const choice = document.getElementById('voice-current-choice');
    if (choice) choice.textContent = `Voz elegida: ${edgeVoiceLabel(voiceId)}`;
}

// A readable name for an Edge voice id: «es-MX-JorgeNeural» → «Jorge (México)».
function edgeVoiceLabel(voiceId = '') {
    const id = String(voiceId || '');
    const parts = id.split('-');
    if (parts.length < 3) return id;
    const name = parts.slice(2).join('-').replace(/Neural$/, '').replace(/Multilingual$/, ' multilingüe');
    let region = parts[1];
    try { region = new Intl.DisplayNames(['es'], { type: 'region' }).of(parts[1]) || region; } catch { /* keep the code */ }
    return `${name} (${region})`;
}

let _voicePreviewGeneration = 0;
let _voicePreviewController = null;

async function previewVoice(voiceId, event) {
    if (event) event.stopPropagation();
    const generation = ++_voicePreviewGeneration;
    _voicePreviewController?.abort();
    _voicePreviewController = new AbortController();
    const btn = event?.target;
    if (btn) { setIconText(btn, '⏳'); btn.disabled = true; }

    // Use the streaming endpoint that actually returns audio bytes the
    // browser can play. The previous `/api/test-tts` (without `-audio`
    // suffix) only generated an MP3 to a tmp file on the server and
    // measured its size — no audio ever reached the user's speakers,
    // even though the ▶ button visually completed with a ✓. Help text
    // tells the user "haz click para escuchar"; now it actually works.
    const audioEl = document.getElementById('voice-preview-audio')
        || (() => {
            const a = document.createElement('audio');
            a.id = 'voice-preview-audio';
            a.style.display = 'none';
            document.body.appendChild(a);
            return a;
        })();
    // Cancel whatever was playing — clicking a different ▶ rapidly
    // would otherwise overlap audio across detached <audio> elements.
    try { audioEl.pause(); audioEl.currentTime = 0; } catch {}

    try {
        const res = await fetch('/api/test-tts-audio', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-IRIS-Intent': 'preview-tts',
            },
            body: JSON.stringify({ provider: 'edge-tts', voice: voiceId }),
            signal: _voicePreviewController.signal,
        });
        if (generation !== _voicePreviewGeneration) return;
        if (!res.ok || !(res.headers.get('content-type') || '').startsWith('audio/')) {
            const body = await res.json().catch(() => ({}));
            throw new Error(body.message || `HTTP ${res.status}`);
        }
        const blobUrl = URL.createObjectURL(await res.blob());
        if (generation !== _voicePreviewGeneration) {
            URL.revokeObjectURL(blobUrl);
            return;
        }
        const previousUrl = audioEl.dataset.objectUrl || '';
        if (previousUrl) URL.revokeObjectURL(previousUrl);
        audioEl.dataset.objectUrl = blobUrl;
        audioEl.src = blobUrl;
        await audioEl.play();
        if (btn) setIconText(btn, '🔊');
    } catch (e) {
        if (e?.name === 'AbortError' || generation !== _voicePreviewGeneration) return;
        console.warn('[wizard] preview failed:', e);
        if (btn) setIconText(btn, '❌');
    }
    setTimeout(() => { if (btn) { btn.textContent = '▶'; btn.disabled = false; } }, 2000);
}

// Show/hide voice selector based on TTS provider
function updateVoiceSelectorVisibility() {
    _applyQwenModelAvailability();
    const tts = document.querySelector('input[name="tts"]:checked')?.value;
    document.querySelectorAll('input[name="tts"]').forEach(input => {
        input.closest('.radio-card')?.setAttribute('aria-checked', input.checked ? 'true' : 'false');
    });
    prepareApiKeysStep();
    updateVoiceSaveButton();
    const selectedCard = document.querySelector('input[name="tts"]:checked')?.closest('.radio-card');
    const group = selectedCard?.closest('details');
    if (group) group.open = true;
    const choice = document.getElementById('voice-current-choice');
    if (choice) choice.textContent = `Voz elegida: ${tts === 'edge-tts' ? edgeVoiceLabel(selectedVoice) : selectedCard?.querySelector('strong')?.textContent || tts || 'Sin elegir'}`;
    const sec = document.getElementById('voice-selector-section');
    if (sec) {
        sec.style.display = tts === 'edge-tts' ? 'block' : 'none';
        if (tts === 'edge-tts' && allVoices.length === 0) loadVoices();
    }

    // Kokoro voice dropdown (only shown when Kokoro is selected)
    const kokoroSec = document.getElementById('kokoro-voice-section');
    if (kokoroSec) kokoroSec.style.display = tts === 'kokoro' ? 'block' : 'none';

    const coquiInstall = document.getElementById('coqui-install-section');
    if (coquiInstall) coquiInstall.style.display = tts === 'coqui' ? 'block' : 'none';
    const neuttsInstall = document.getElementById('neutts-install-section');
    if (neuttsInstall) neuttsInstall.style.display = tts === 'neutts' ? 'block' : 'none';
    const hfS2SSection = document.getElementById('hf-s2s-section');
    if (hfS2SSection) hfS2SSection.style.display = tts === 'hf-s2s' ? 'block' : 'none';
    const hfModel = document.getElementById('hf-s2s-model')?.value || '';
    const hfSpeaker = document.getElementById('hf-s2s-speaker');
    const hfModeNote = document.getElementById('hf-s2s-mode-note');
    const hfUsesClone = hfModel.includes('-Base');
    if (hfSpeaker) hfSpeaker.disabled = hfUsesClone;
    if (hfModeNote) {
        hfModeNote.textContent = hfUsesClone
            ? 'Base usa la muestra WAV y su transcripción; la voz predefinida queda desactivada.'
            : 'CustomVoice puede hablar español, aunque sus timbres son nativos de otros idiomas. Al clonar, el wizard cambiará al modelo Base del mismo tamaño.';
    }

    // Fish-Speech config (URL + optional reference audio path).
    // Unlike Kokoro/NeuTTS, Fish-Speech runs as a SEPARATE local server
    // the user owns, so the wizard can't auto-install it — we just
    // expose the two fields the engine actually needs.
    const fishSec = document.getElementById('fish-speech-section');
    if (fishSec) fishSec.style.display = tts === 'fish-speech' ? 'block' : 'none';

    const fallbackSec = document.getElementById('tts-fallback-section');
    if (fallbackSec) fallbackSec.style.display = tts && tts !== 'edge-tts' ? 'block' : 'none';

    // Voice cloning section (show for providers that support it).
    // ``neutts`` is included even though the previous version excluded
    // it — the backend `/api/clone-voice` explicitly handles NeuTTS
    // (wizard/app.py /api/clone-voice neutts branch) and NeuTTS's
    // headline feature in the radio card is "3-second cloning". Hiding
    // the recorder for NeuTTS meant users had no way to configure the
    // reference, so the engine produced silence on first use.
    const cloneSec = document.getElementById('voice-clone-section');
    if (cloneSec) {
        const supportsClone = ['elevenlabs', 'minimax', 'fish-audio', 'coqui', 'neutts', 'fish-speech', 'hf-s2s'].includes(tts);
        cloneSec.style.display = supportsClone ? 'block' : 'none';
    }

    const requirements = document.getElementById('clone-provider-requirements');
    const requirementText = {
        elevenlabs: 'ElevenLabs: WAV claro de 3 s a 2 min. Se enviará al servicio cloud y requiere API key.',
        minimax: 'MiniMax: WAV de 10 s a 5 min. Se enviará al servicio cloud y requiere API key.',
        'fish-audio': 'Fish Audio: WAV de 10-60 s; si la voz sale robótica, prueba con 30-60 s. La muestra se enviará a Fish para crear una voz privada en tu cuenta. Requiere API key.',
        coqui: 'Coqui XTTS v2: WAV de 3 s a 2 min. Se guarda localmente y cambia el modelo a XTTS v2.',
        neutts: 'NeuTTS: WAV mono de 3-15 s y transcripción exacta. Se guarda localmente.',
        'fish-speech': 'Fish Speech S2: WAV mono de 10-30 s y transcripción exacta. Requiere un servidor compatible ya activo.',
        'hf-s2s': 'HF Qwen3-TTS Base: WAV mono de 3-15 s y transcripción exacta. Se guarda y procesa localmente.',
    };
    if (requirements) requirements.textContent = requirementText[tts] || 'Este motor usa voces predefinidas y no admite clonación.';

    const transcriptSec = document.getElementById('clone-transcript-section');
    if (transcriptSec) transcriptSec.style.display = ['neutts', 'fish-speech', 'fish-audio', 'hf-s2s'].includes(tts) ? 'block' : 'none';
    const transcriptOptional = document.getElementById('clone-transcript-optional');
    if (transcriptOptional) transcriptOptional.style.display = tts === 'fish-audio' ? 'block' : 'none';
}


// ── Voice Cloning ───────────────────────────────────────

let cloneMediaRecorder = null;
let cloneChunks = [];
let cloneBlob = null;
let cloneTimer = null;
let cloneSeconds = 0;
let clonePreviewUrl = '';
let cloneCaptureState = 'idle';
let cloneActiveStream = null;
let _cloneInFlight = false;

function _resampleMono(input, sourceRate, targetRate = 24000) {
    if (sourceRate === targetRate) return input;
    const outputLength = Math.max(1, Math.round(input.length * targetRate / sourceRate));
    const output = new Float32Array(outputLength);
    const ratio = sourceRate / targetRate;
    for (let i = 0; i < outputLength; i++) {
        const position = i * ratio;
        const left = Math.floor(position);
        const right = Math.min(left + 1, input.length - 1);
        const mix = position - left;
        output[i] = input[left] * (1 - mix) + input[right] * mix;
    }
    return output;
}

function _pcm16WavBlob(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const writeAscii = (offset, value) => {
        for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
    };
    writeAscii(0, 'RIFF');
    view.setUint32(4, 36 + samples.length * 2, true);
    writeAscii(8, 'WAVE');
    writeAscii(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeAscii(36, 'data');
    view.setUint32(40, samples.length * 2, true);
    let offset = 44;
    for (const sample of samples) {
        const clipped = Math.max(-1, Math.min(1, sample));
        view.setInt16(offset, clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff, true);
        offset += 2;
    }
    return new Blob([buffer], { type: 'audio/wav' });
}

async function _cloneSampleAsWav(blob) {
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextCtor) throw new Error('Este navegador no puede normalizar audio a WAV.');
    const context = new AudioContextCtor();
    try {
        const decoded = await context.decodeAudioData((await blob.arrayBuffer()).slice(0));
        const mono = new Float32Array(decoded.length);
        for (let channel = 0; channel < decoded.numberOfChannels; channel++) {
            const source = decoded.getChannelData(channel);
            for (let i = 0; i < source.length; i++) mono[i] += source[i] / decoded.numberOfChannels;
        }
        const normalized = _resampleMono(mono, decoded.sampleRate, 24000);
        return {
            blob: _pcm16WavBlob(normalized, 24000),
            duration: decoded.duration,
        };
    } finally {
        try { await context.close(); } catch {}
    }
}

async function toggleCloneRecording() {
    if (cloneCaptureState === 'requesting' || cloneCaptureState === 'stopping') return;
    if (cloneMediaRecorder && cloneMediaRecorder.state === 'recording') {
        stopCloneRecording();
        return;
    }

    if (!navigator.mediaDevices?.getUserMedia) {
        await irisTell({ title: 'Micrófono no disponible', icon: 'warning',
            message: 'Este navegador o contexto no permite acceder al micrófono. Puedes subir un archivo de audio autorizado.' });
        return;
    }
    if (!window.MediaRecorder) {
        await irisTell({ title: 'No se puede grabar', icon: 'warning',
            message: 'Este navegador no permite grabar audio. Puedes subir un archivo WAV, MP3 u OGG.' });
        return;
    }

    const provider = document.querySelector('input[name="tts"]:checked')?.value
        || wizardState.tts_provider
        || '';
    const maxRecordingSeconds = {
        elevenlabs: 120,
        minimax: 300,
        coqui: 120,
        neutts: 15,
        'fish-speech': 30,
        'fish-audio': 60,
        'hf-s2s': 15,
    }[provider] || 120;

    const button = document.getElementById('btn-record-clone');
    cloneCaptureState = 'requesting';
    if (button) button.disabled = true;
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (cloneCaptureState !== 'requesting' || currentStep !== 4) {
            stream.getTracks().forEach(track => track.stop());
            return;
        }
        cloneActiveStream = stream;
            cloneChunks = [];
            cloneSeconds = 0;
            const consent = document.getElementById('clone-consent-confirmed');
            if (consent) consent.checked = false;
            const options = (window.MediaRecorder?.isTypeSupported?.('audio/webm'))
                ? { mimeType: 'audio/webm' }
                : undefined;
            try {
                cloneMediaRecorder = new MediaRecorder(stream, options);
            } catch (error) {
                stream.getTracks().forEach(t => t.stop());
                throw error;
            }

            cloneMediaRecorder.ondataavailable = (e) => {
                if (e.data.size > 0) cloneChunks.push(e.data);
            };

            cloneMediaRecorder.onstop = () => {
                stream.getTracks().forEach(t => t.stop());
                if (cloneActiveStream === stream) cloneActiveStream = null;
                cloneCaptureState = 'idle';
                clearInterval(cloneTimer);
                cloneBlob = new Blob(cloneChunks, { type: cloneMediaRecorder.mimeType || 'audio/webm' });
                showClonePreview('grabación_micrófono', cloneBlob);
            };

            try {
                cloneMediaRecorder.start(500);
                cloneCaptureState = 'recording';
            } catch (error) {
                stream.getTracks().forEach(t => t.stop());
                cloneMediaRecorder = null;
                throw error;
            }

            // UI updates
            document.getElementById('btn-record-clone').style.display = 'none';
            const status = document.getElementById('recording-status');
            status.style.display = 'flex';

            cloneTimer = setInterval(() => {
                cloneSeconds++;
                const m = Math.floor(cloneSeconds / 60);
                const s = cloneSeconds % 60;
                document.getElementById('recording-time').textContent =
                    `${m}:${s.toString().padStart(2, '0')}`;

                // Match the selected provider's server-side duration limit so
                // a locally recorded sample is not guaranteed to be rejected.
                if (cloneSeconds >= maxRecordingSeconds) stopCloneRecording();
            }, 1000);
    } catch (err) {
        cloneCaptureState = 'idle';
        cloneActiveStream?.getTracks().forEach(track => track.stop());
        cloneActiveStream = null;
        await irisTell({ title: 'Micrófono no disponible', icon: 'warning',
            message: `No se pudo acceder al micrófono: ${err.message}` });
    } finally {
        if (button) button.disabled = false;
    }
}

function stopCloneRecording({ discard = false } = {}) {
    if (cloneCaptureState === 'requesting') cloneCaptureState = 'stopping';
    if (cloneMediaRecorder && cloneMediaRecorder.state === 'recording') {
        if (discard) {
            const recorder = cloneMediaRecorder;
            recorder.ondataavailable = null;
            recorder.onstop = () => {
                cloneActiveStream?.getTracks().forEach(track => track.stop());
                cloneActiveStream = null;
                cloneCaptureState = 'idle';
            };
        }
        cloneMediaRecorder.stop();
    }
    cloneActiveStream?.getTracks().forEach(track => track.stop());
    cloneActiveStream = null;
    clearInterval(cloneTimer);
    if (discard) cloneChunks = [];
    cloneCaptureState = 'idle';
    document.getElementById('btn-record-clone').style.display = 'block';
    document.getElementById('recording-status').style.display = 'none';
}

function handleCloneFile(input) {
    const file = input.files[0];
    if (!file) return;

    const consent = document.getElementById('clone-consent-confirmed');
    if (consent) consent.checked = false;
    cloneBlob = file;
    showClonePreview(file.name, file);
}

function showClonePreview(filename, blob) {
    document.getElementById('clone-result').style.display = 'block';
    document.getElementById('clone-filename').textContent = filename;

    if (clonePreviewUrl) URL.revokeObjectURL(clonePreviewUrl);
    clonePreviewUrl = URL.createObjectURL(blob);
    const player = document.getElementById('clone-audio-player');
    player.src = clonePreviewUrl;

    // Auto-suggest voice name based on personality
    const nameInput = document.getElementById('clone-voice-name');
    const personality = document.querySelector('input[name="personality"]:checked')?.value || 'IRIS';
            const presetNames = { iris: 'IRIS', custom: 'Personalizado' };
    if (!nameInput.value) {
        const assistantName = document.getElementById('assistant-name')?.value?.trim();
        nameInput.value = assistantName || presetNames[personality] || 'Mi asistente';
    }
}

function clearCloneSample() {
    cloneBlob = null;
    if (clonePreviewUrl) URL.revokeObjectURL(clonePreviewUrl);
    clonePreviewUrl = '';
    const player = document.getElementById('clone-audio-player');
    if (player) player.removeAttribute('src');
    document.getElementById('clone-result').style.display = 'none';
    document.getElementById('clone-audio-file').value = '';
    document.getElementById('clone-status').style.display = 'none';
    const consent = document.getElementById('clone-consent-confirmed');
    if (consent) consent.checked = false;
}

async function cloneVoice() {
    if (_cloneInFlight) return;
    if (!cloneBlob) {
        await irisTell({ title: 'Falta el audio', icon: 'info', message: 'Primero graba o sube un audio.' });
        return;
    }

    const voiceName = document.getElementById('clone-voice-name')?.value?.trim() || 'IRIS Clone';
    const provider = document.querySelector('input[name="tts"]:checked')?.value
        || wizardState.tts_provider
        || '';
    const consentConfirmed = document.getElementById('clone-consent-confirmed')?.checked || false;
    if (!consentConfirmed) {
        await irisTell({ title: 'Falta tu autorización', icon: 'shield',
            message: 'Confirma que tienes autorización para clonar y usar esta voz.' });
        return;
    }
    const status = document.getElementById('clone-status');
    const cloneButton = document.querySelector('[onclick="cloneVoice()"]');
    _cloneInFlight = true;
    if (cloneButton) cloneButton.disabled = true;
    status.style.display = 'block';
    status.className = 'clone-status loading';
    status.innerHTML = '<wz-icon>⏳</wz-icon> Clonando voz... Esto puede tomar 15-30 segundos.';

    try {
        status.innerHTML = '<wz-icon>⏳</wz-icon> Normalizando muestra a WAV mono…';
        const normalized = await _cloneSampleAsWav(cloneBlob);
        const formData = new FormData();
        formData.append('audio', normalized.blob, 'voice_sample.wav');
        formData.append('name', voiceName);
        formData.append('provider', provider);
        if (provider === 'hf-s2s') {
            formData.append(
                'model',
                document.getElementById('hf-s2s-model')?.value
                    || 'Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice'
            );
        }
        formData.append('transcript', document.getElementById('clone-transcript')?.value?.trim() || '');
        formData.append('api_key', ['elevenlabs', 'minimax', 'fish-audio'].includes(provider) ? (document.getElementById(`key-${provider}`)?.value?.trim() || '') : '');
        formData.append('consent_confirmed', 'true');

        status.innerHTML = '<wz-icon>⏳</wz-icon> Procesando clonación…';
        const cloneRequestId = globalThis.crypto?.randomUUID?.()
            || `clone-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const res = await fetch('/api/clone-voice', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'X-IRIS-Intent': 'clone-voice',
                'Idempotency-Key': cloneRequestId,
            },
            body: formData,
        });
        const data = await res.json();

        if (data.status === 'ok') {
            status.className = 'clone-status success';
            const detail = data.message || `Referencia de ${normalized.duration.toFixed(1)} s guardada.`;
            status.innerHTML = `<wz-icon>✅</wz-icon> Voz configurada: <strong>${escapeHtml(data.voice_id)}</strong><br><span class="dim">${escapeHtml(detail)}</span>`;

            // Auto-fill the voice ID in the API keys step
            if (data.provider === 'elevenlabs') {
                const voiceInput = document.getElementById('key-elevenlabs-voice');
                if (voiceInput) voiceInput.value = data.voice_id;
                const keyInput = document.getElementById('key-elevenlabs');
                const cloneKey = document.getElementById(`key-${provider}`)?.value?.trim() || '';
                if (keyInput && cloneKey) keyInput.value = cloneKey;
            } else if (data.provider === 'fish-audio') {
                const voiceInput = document.getElementById('key-fish-audio-voice');
                if (voiceInput) voiceInput.value = data.voice_id;
                wizardState.fish_audio_voice_id = data.voice_id;
                _setFishAudioVoiceName(data.name || '');
                _fishAudioVoiceChanged();
            } else if (data.provider === 'minimax') {
                const voiceInput = document.getElementById('key-minimax-voice');
                if (voiceInput) voiceInput.value = data.voice_id;
                const keyInput = document.getElementById('key-minimax');
                const cloneKey = document.getElementById(`key-${provider}`)?.value?.trim() || '';
                if (keyInput && cloneKey) keyInput.value = cloneKey;
            } else if (data.provider === 'coqui') {
                wizardState.coqui_ref_audio = data.voice_id;
            } else if (data.provider === 'neutts') {
                wizardState.neutts_ref_audio = data.voice_id;
                wizardState.neutts_ref_text = document.getElementById('clone-transcript')?.value?.trim() || '';
            } else if (data.provider === 'fish-speech') {
                wizardState.fish_speech_ref_audio = data.voice_id;
                wizardState.fish_speech_ref_text = document.getElementById('clone-transcript')?.value?.trim() || '';
                const fishPath = document.getElementById('fish-speech-ref-audio');
                const fishText = document.getElementById('fish-speech-ref-text');
                if (fishPath) fishPath.value = data.voice_id;
                if (fishText) fishText.value = wizardState.fish_speech_ref_text;
            } else if (data.provider === 'hf-s2s') {
                wizardState.hf_s2s_ref_audio = data.voice_id;
                wizardState.hf_s2s_ref_text = document.getElementById('clone-transcript')?.value?.trim() || '';
                wizardState.hf_s2s_model = data.model;
                const hfModel = document.getElementById('hf-s2s-model');
                if (hfModel && [...hfModel.options].some(option => option.value === data.model)) {
                    hfModel.value = data.model;
                }
                updateVoiceSelectorVisibility();
            }

            wizardState.cloned_voice_id = data.voice_id;
            wizardState.cloned_voice_provider = data.provider;
            _ttsPreviewKey = '';
            if (_ttsPreviewBlobUrl) URL.revokeObjectURL(_ttsPreviewBlobUrl);
            _ttsPreviewBlobUrl = null;
        } else {
            status.className = 'clone-status error';
            setIconText(status, `❌ Error: ${data.error || 'No se pudo clonar la voz'}`);
        }
    } catch (e) {
        status.className = 'clone-status error';
        setIconText(status, `❌ No se pudo procesar la muestra: ${e.message}`);
    } finally {
        _cloneInFlight = false;
        if (cloneButton) cloneButton.disabled = false;
    }
}

window.addEventListener('pagehide', () => {
    stopCloneRecording({ discard: true });
    _stopFishAudioSample();
});
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') stopCloneRecording({ discard: true });
});

// ── Voice credentials ────────────────────────────────────

function prepareApiKeysStep() {
    // Show/hide conditional key fields based on selections
    const tts = document.querySelector('input[name="tts"]:checked')?.value;
    if (tts !== 'fish-audio') _stopFishAudioSample();
    const el = document.getElementById('elevenlabs-key-section');
    const mm = document.getElementById('minimax-key-section');
    const fishAudio = document.getElementById('fish-audio-key-section');
    const wh = document.getElementById('whisper-key-section');
    if (el) el.style.display = tts === 'elevenlabs' ? 'block' : 'none';
    if (mm) mm.style.display = tts === 'minimax' ? 'block' : 'none';
    if (fishAudio) fishAudio.style.display = tts === 'fish-audio' ? 'flex' : 'none';
    if (wh) wh.style.display = 'none'; // whisper-api would show this
}

function toggleKeyVisibility(inputId, btn) {
    const input = document.getElementById(inputId);
    if (input.type === 'password') {
        input.type = 'text';
        setIconText(btn, '🔒');
    } else {
        input.type = 'password';
        setIconText(btn, '👁️');
    }
}

// ── Step 8: Summary & Save ──────────────────────────────

let _lastConfigSaveOk = false;
// What the final save left behind, for the summary title and the launch button: the save's
// own error, and whether the AI provider is ready (``ai`` from /api/save-config, the same
// check the launch makes). Saving preferences without a provider is allowed.
let _lastConfigSaveError = '';
let _lastAiReadiness = null;
let _summarySaving = false;
let _wizardCompanionLifecycle = null;
let _wizardCompanionBusy = '';
let _wizardCompanionError = '';
let _wizardCompanionNotice = '';
let _configSaveGeneration = 0;
let _configSaveController = null;
const _configSaveSession = globalThis.crypto?.randomUUID?.()
    || `iris-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const _wizardCompanionReadOnly = new URLSearchParams(window.location.search).get('preview') === 'companion';

// The summary title says what actually happened: never "ready" before the save succeeded
// and the AI provider can answer.
function _renderSummaryState(state, detail = '') {
    _summarySaving = state === 'saving';
    for (const button of document.querySelectorAll('[data-step="8"] .step-buttons .btn-back, .setup-summary-links button')) {
        button.disabled = _summarySaving;
    }
    const titles = {
        saving: 'Guardando tu configuración…',
        failed: 'No se pudo guardar la configuración',
        'ai-pending': 'Tus preferencias están guardadas',
        ready: '¡IRIS está lista!',
        saved: 'Configuración guardada',
    };
    const title = document.getElementById('summary-title');
    if (title) title.textContent = titles[state] || titles.saved;
    const status = document.getElementById('summary-status');
    if (status) {
        status.textContent = detail;
        status.hidden = !detail;
        // Without a provider IRIS cannot talk: that is a warning, not a footnote
        // (`.dim` forces grey 11px mono with !important, so the callout drops it).
        const tone = state === 'ai-pending' ? 'warn' : state === 'failed' ? 'error' : '';
        status.dataset.tone = tone;
        status.classList?.toggle('dim', !tone);
    }
    // A failed save offers its own retry; the launch stays blocked until it succeeds.
    const retry = document.getElementById('summary-retry');
    if (retry) retry.hidden = state !== 'failed';
    // …and a missing provider offers the way to fix it, as the main action.
    const configureAi = document.getElementById('summary-configure-ai');
    if (configureAi) configureAi.hidden = state !== 'ai-pending';
}

// The save waits for Hermes; a cold first start can take a while on a slow PC,
// but it must end with a result the user can act on, never wait forever (qa9).
const CONFIG_SAVE_TIMEOUT_MS = 180000;

async function showSummaryAndSave() {
    const saveGeneration = ++_configSaveGeneration;
    _configSaveController?.abort();
    _configSaveController = new AbortController();
    // One deadline for the whole save, from the first read: a pending read of the
    // saved settings must also end in a visible result (Codex qa10 review, R2).
    const saveController = _configSaveController;
    let saveTimedOut = false;
    const saveTimer = setTimeout(() => {
        if (saveGeneration !== _configSaveGeneration) return;  // a newer attempt owns the screen
        saveTimedOut = true;
        saveController.abort();
    }, CONFIG_SAVE_TIMEOUT_MS);
    _lastConfigSaveOk = false;
    _lastConfigSaveError = '';
    _lastAiReadiness = null;
    _showConfigWarnings([]);
    _renderSummaryState('saving', 'La primera vez puede tardar un minuto mientras IRIS se prepara.');
    const launchButton = document.querySelector('[data-step="8"] .btn-gold');
    if (launchButton) launchButton.disabled = true;
    loadWizardCompanionLifecycle();
    const tts = document.querySelector('input[name="tts"]:checked')?.value || 'edge-tts';
    const personality = document.querySelector('input[name="personality"]:checked')?.value || 'iris';
    const assistantName = document.getElementById('assistant-name')?.value?.trim() || '';
    const irisInstructions = document.getElementById('iris-personality-instructions')?.value?.trim() || '';
    const customInstructions = document.getElementById('custom-personality-instructions')?.value?.trim() || '';
    const personalityInstructions = personality === 'custom' ? customInstructions : irisInstructions;
    const customBackstory = document.getElementById('custom-backstory')?.value?.trim() || '';
    const includeIrisBackstory = Boolean(document.getElementById('include-iris-backstory')?.checked);
    const personalityTraits = selectedIdentityTraits();

    // Fetch the previously-saved config FIRST so we can use it as a fallback
    // for fields the user didn't explicitly change. Without this, the save
    // step silently overwrites the user's saved values with HTML defaults
    // (e.g. `assistant_name="IRIS"` from the preset, or `avatar="procedural"`
    // from the default-checked radio) every time they reach step 8.
    //
    // This used to be done lower in this function only for LLM fields, but
    // the same "default wins over saved" problem applied to name + avatar.
    // Pulling it up so EVERY field that defaults to a HTML radio value can
    // fall back to the saved version instead of clobbering it.
    let savedConfig = {};
    try {
        const r = await fetch('/api/current-config', { signal: saveController.signal });
        if (r.ok) savedConfig = await r.json();
    } catch (e) {
        if (saveTimedOut && saveGeneration === _configSaveGeneration) {
            _failConfigSaveByTimeout();
            return;
        }
        if (e?.name === 'AbortError' || saveGeneration !== _configSaveGeneration) {
            clearTimeout(saveTimer);
            return;
        }
        console.warn('current-config fetch failed:', e);
    }
    if (saveGeneration !== _configSaveGeneration) {
        clearTimeout(saveTimer);
        return;
    }

    // Avatar: prefer the radio if the user picked one, else the saved model.
    // IRIS' included VRM is an empty path; the person's own model keeps its path.
    const domAvatar = document.querySelector('input[name="avatar"]:checked')?.value;
    const avatar = domAvatar || (savedConfig.avatar_vrm_path ? 'vrm' : 'iris');
    // Channels live in Hermes (messaging.js saves them); the summary only names the
    // enabled ones. Since A08.6 this read was missing and the summary threw before saving.
    const selectedChannels = (document.getElementById('messaging-panel')?._messaging?.platforms || [])
        .filter(platform => platform && platform.enabled && !platform.managed)  // not Iris' own API server
        .map(platform => String(platform.name || platform.id || ''))
        .filter(Boolean);

    const savedName  = (savedConfig.assistant_name || '').trim();
    const resolvedAssistantName = assistantName || savedName || 'IRIS';

    // Collect API keys from the wizard inputs
    const elevenlabsKey = document.getElementById('key-elevenlabs')?.value?.trim() || '';
    const elevenlabsVoice = document.getElementById('key-elevenlabs-voice')?.value?.trim() || '';
    const minimaxKey = document.getElementById('key-minimax')?.value?.trim() || '';
    const minimaxVoice = document.getElementById('key-minimax-voice')?.value?.trim() || '';
    const fishAudioKey = document.getElementById('key-fish-audio')?.value?.trim() || '';
    const fishAudioVoice = document.getElementById('key-fish-audio-voice')?.value?.trim() || savedConfig.fish_audio_voice_id || '';
    const fishAudioVoiceName = document.getElementById('key-fish-audio-voice')?.dataset?.voiceName
        || (fishAudioVoice === savedConfig.fish_audio_voice_id ? savedConfig.fish_audio_voice_name || '' : '');
    const fishAudioModel = document.getElementById('fish-audio-model')?.value || savedConfig.fish_audio_model || 's2.1-pro-free';
    const hasFishAudioKey = Boolean(fishAudioKey || savedConfig.has_fish_audio_api_key);
    const hasElevenlabsKey = Boolean(elevenlabsKey || savedConfig.has_elevenlabs_api_key);
    const hasMinimaxKey = Boolean(minimaxKey || savedConfig.has_minimax_api_key);
    const whisperKey = document.getElementById('key-whisper')?.value?.trim() || '';
    const hermesToken = document.getElementById('key-hermes')?.value?.trim() || '';

    // LLM provider config — use the explicit DOM/state choice in full mode,
    // and the saved value in quick/repair. There is deliberately no provider
    // fallback here: an absent radio must never turn into an Anthropic write.
    // Once the provider step saved its main provider, only its form in
    // «Cambiar proveedor» may replace it: the summary and «Agregar otro
    // proveedor» keep the saved one (the form may hold the provider being added).
    const selectedProvider = selectedLLMProvider();
    const isFullHermesMode = wizardState.hermes_mode === 'full' && _llmFormOwnsProvider();
    const llmProvider = isFullHermesMode
        ? (selectedProvider || savedConfig.llm_provider || '')
        : (savedConfig.llm_provider || selectedProvider || '');

    // For model + key + OAuth, also prefer persisted but accept fresh DOM
    // values if the user just typed them and we haven't re-saved yet.
    // A form that does not own the main provider (folded, or adding another one)
    // says nothing about it: its key and OAuth toggle belong to the other provider (R04).
    const domLlmApiKey = isFullHermesMode ? (document.getElementById('llm-api-key')?.value?.trim() || '') : '';
    const domUseOAuth = isFullHermesMode && Boolean(document.getElementById('use-oauth')?.checked);
    const domLlmBaseUrl = isFullHermesMode ? (document.getElementById('llm-base-url')?.value?.trim() || '') : '';
    const llmApiKey    = domUseOAuth ? '' : domLlmApiKey;
    const savedProviderMatches = savedConfig.llm_provider === llmProvider;
    const hasLlmApiKey = !domUseOAuth && (
        Boolean(domLlmApiKey) || (savedProviderMatches && Boolean(savedConfig.has_llm_api_key))
    );

    const domLlmModel = selectedLLMModel(llmProvider);
    const llmModel = isFullHermesMode
        ? (domLlmModel || savedConfig.llm_model || '')
        : (savedConfig.llm_model || domLlmModel || '');

    // Credential availability and selected auth path are separate. A stale
    // OAuth profile must not flip an explicit API configuration back to OAuth.
    const liveOAuthMatches = wizardState.oauth_authenticated_provider === llmProvider
        && wizardState.oauth_authenticated === true;
    const hasOAuth = liveOAuthMatches || (savedProviderMatches && Boolean(savedConfig.has_llm_oauth));

    // Resolved auth mode for the chosen provider — drives the connection
    // label ("OAuth" / "API Key" / "Local") with reality, not guesses.
    const llmAuthMode = isFullHermesMode
        ? ((LLM_PROVIDERS[llmProvider] || {}).auth || savedConfig.llm_auth_mode || 'api')
        : (savedConfig.llm_auth_mode || (LLM_PROVIDERS[llmProvider] || {}).auth || 'api');
    const llmProviderLbl = (LLM_PROVIDERS[llmProvider] || {}).name || savedConfig.llm_provider_label || llmProvider;
    const useOAuth = llmAuthMode === 'oauth' || (
        isFullHermesMode ? domUseOAuth : Boolean(savedConfig.llm_use_oauth)
    );

    // Hardcoded labels for the curated providers; for discovered or
    // anything else, fall back to the backend-resolved label. This means
    // the summary always says "OpenAI Codex (CLI auth)" instead of
    // "oc-codex" for OAuth-only providers picked from the wizard.
    const providerLabels = { anthropic: 'Anthropic (Claude)', openai: 'OpenAI (GPT)', google: 'Google (Gemini)', minimax: 'MiniMax', 'ollama-local': 'Ollama (Local)', ollama: 'Ollama Cloud' };
    const llmDisplayName = providerLabels[llmProvider] || llmProviderLbl || llmProvider;

    // Kokoro voice selection (only meaningful when tts === 'kokoro').
    // The dropdown is hidden for other providers but we still read it
    // so re-running the wizard preserves the user's last choice.
    const kokoroVoice = document.getElementById('kokoro-voice')?.value || 'ef_dora';

    // Fish-Speech wizard fields (FIX W2). Only meaningful when
    // tts === 'fish-speech' but we always read them so re-running the
    // wizard preserves them; IRISConfig has matching defaults.
    const fishUrl = document.getElementById('fish-speech-url')?.value?.trim()
                 || 'http://127.0.0.1:8080';
    const fishRefAudio = document.getElementById('fish-speech-ref-audio')?.value?.trim() || '';
    const fishRefText = document.getElementById('fish-speech-ref-text')?.value?.trim() || '';
    const fishReferenceId = document.getElementById('fish-speech-reference-id')?.value?.trim() || '';
    const hfS2SModel = document.getElementById('hf-s2s-model')?.value
                    || savedConfig.hf_s2s_model
                    || 'Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice';
    const hfS2SSpeaker = document.getElementById('hf-s2s-speaker')?.value
                      || savedConfig.hf_s2s_speaker
                      || 'Aiden';
    const hfS2SRefAudio = wizardState.hf_s2s_ref_audio || savedConfig.hf_s2s_ref_audio || '';
    const hfS2SRefText = wizardState.hf_s2s_ref_text || savedConfig.hf_s2s_ref_text || '';

    const config = {
        assistant_name: resolvedAssistantName,
        personality_template: personality,
        personality_instructions: personalityInstructions,
        personality_traits: personalityTraits,
        custom_backstory: personality === 'custom'
            ? customBackstory
            : (savedConfig.custom_backstory || ''),
        include_iris_backstory: includeIrisBackstory,
        legacy_personality_file_enabled: false,
        custom_personality: '',
        visual_theme: wizardState.visual_theme || savedConfig.visual_theme || 'iris',
        visual_theme_selected: Boolean(
            wizardState.visual_theme_selected || savedConfig.visual_theme_selected
        ),
        tts_provider: tts,
        tts_allow_remote_fallback: Boolean(document.getElementById('tts-allow-remote-fallback')?.checked),
        edge_tts_voice: selectedVoice || 'es-MX-DaliaNeural',
        kokoro_voice: kokoroVoice,
        fish_speech_url: fishUrl,
        fish_speech_ref_audio: fishRefAudio,
        fish_speech_ref_text: fishRefText,
        fish_speech_reference_id: fishReferenceId,
        hf_s2s_model: hfS2SModel,
        hf_s2s_speaker: hfS2SSpeaker,
        hf_s2s_language: savedConfig.hf_s2s_language || 'spanish',
        hf_s2s_ref_audio: hfS2SRefAudio,
        hf_s2s_ref_text: hfS2SRefText,
        hf_s2s_device: savedConfig.hf_s2s_device || 'auto',
        elevenlabs_api_key: elevenlabsKey,
        elevenlabs_voice_id: elevenlabsVoice,
        minimax_api_key: minimaxKey,
        minimax_voice_id: minimaxVoice || 'Spanish_Wiselady',
        fish_audio_api_key: fishAudioKey,
        fish_audio_voice_id: fishAudioVoice,
        fish_audio_voice_name: fishAudioVoiceName.slice(0, 120),
        fish_audio_model: fishAudioModel,
        whisper_api_key: whisperKey,
        hermes_api_key: hermesToken,
        avatar_vrm_path: avatar === 'vrm' ? wizardState.avatar_vrm_path || '' : '',
        hermes_url: savedConfig.hermes_url || 'http://127.0.0.1:8642',
        // LLM config
        llm_provider: llmProvider,
        llm_api_key: llmApiKey,
        llm_model: llmModel,
        llm_use_oauth: useOAuth,
        llm_base_url: domLlmBaseUrl || savedConfig.llm_base_url || '',
    };

    // Wake configuration belongs to IRIS OS. Preserve the phrase AND the
    // listening preference; a new profile stays off by backend policy.
    config.hermes_wake_mode = 'keep';
    config.hermes_wake_phrase = '';

    // Propagate the selected engine mode, without changing provider ownership.
    // /api/save-config uses this to decide whether to rewrite Hermes'
    // credential files: only Modo Completo gets the write — Modo
    // Rápido / Reparar must NOT touch Hermes' .env / config.yaml.
    config.hermes_mode = wizardState.hermes_mode || '';

    // Drop the LLM credential fields unless we're in Modo Completo with the
    // provider form in charge. In quick / repair / skip, or once the provider
    // step saved it, the source of truth is Hermes' on-disk config — sending
    // these from the wizard form could only cause drift.
    if (config.hermes_mode !== 'full' || !isFullHermesMode) {
        config.llm_provider = '';
        config.llm_api_key = '';
        config.llm_model = '';
        config.llm_use_oauth = false;
        config.llm_base_url = '';
    }

    // Show summary. Voices by the name the voice step showed, not by their ids.
    const kokoroOption = document.getElementById('kokoro-voice')?.selectedOptions?.[0]?.textContent || '';
    const kokoroLabel = kokoroOption.replace(/[♀♂]\s*/g, '').trim() || kokoroVoice;
    const ttsLabels = {
        'edge-tts': `Edge-TTS · ${edgeVoiceLabel(config.edge_tts_voice)}`,
        coqui: 'Coqui TTS (Local)',
        elevenlabs: `ElevenLabs · ${elevenlabsVoice || 'voz guardada'}`,
        minimax: `MiniMax · ${minimaxVoice || 'voz guardada'}`,
        'fish-audio': `Fish Audio · ${fishAudioVoice ? (fishAudioVoiceName || 'voz seleccionada') : 'sin voz'} · ${fishAudioModel === 's2.1-pro-free' ? 'modelo de pruebas' : 'modelo de pago'}`,
        kokoro: `Kokoro · ${kokoroLabel}`,
        'fish-speech': 'Fish Speech S2 · servidor local',
        neutts: 'NeuTTS · clonación local',
        'hf-s2s': `HF Qwen3-TTS · ${hfS2SModel.includes('1.7B') ? '1.7B' : '0.6B'} · ${hfS2SModel.includes('-Base') ? 'clonada' : hfS2SSpeaker}`,
    };
    const mask = (k) => k ? '<wz-icon>✅</wz-icon> Configurada' : '<wz-icon>⚠</wz-icon> No configurada';

    // Build the LLM credential row based on the resolved auth mode +
    // the actual presence flags from the backend. Five outcomes:
    //   • none + no key       → "🏠 Local — sin credenciales"
    //   • oauth + has OAuth   → "🔐 Perfil OAuth presente"
    //   • oauth + no OAuth    → "⚠️ OAuth pendiente"
    //   • api/api+oauth, key  → "✅ API Key configurada"
    //   • api/api+oauth + OAuth (no key) → "🔐 OAuth (sin API key)"
    //   • else                → "⚠️ No configurada"
    let connectionLabel, credentialRow;
    if (!llmProvider) {
        // No provider means no access at all: never a green «No necesaria» under «Sin configurar».
        connectionLabel = 'Pendiente';
        credentialRow   = `<span class="value warn"><wz-icon>⚠</wz-icon> Falta elegir un proveedor</span>`;
    } else if (llmAuthMode === 'none') {
        connectionLabel = '<wz-icon>🏠</wz-icon> Local';
        credentialRow   = `<span class="value ok"><wz-icon>✅</wz-icon> Sin clave (servidor local)</span>`;
    } else if (llmAuthMode === 'api-optional') {
        connectionLabel = '<wz-icon>🔌</wz-icon> Endpoint personalizado';
        credentialRow   = hasLlmApiKey
            ? `<span class="value ok"><wz-icon>✅</wz-icon> API key opcional configurada</span>`
            : `<span class="value ok"><wz-icon>✅</wz-icon> Sin autenticación</span>`;
    } else if (llmAuthMode === 'aws') {
        connectionLabel = '<wz-icon>☁</wz-icon> AWS SDK';
        credentialRow   = `<span class="value ok"><wz-icon>☁</wz-icon> Cadena de credenciales AWS</span>`;
    } else if (llmAuthMode === 'external') {
        connectionLabel = '<wz-icon>🧩</wz-icon> CLI externa';
        credentialRow   = `<span class="value ok"><wz-icon>✅</wz-icon> Autenticación gestionada por la CLI</span>`;
    } else if (llmAuthMode === 'entra') {
        connectionLabel = '<wz-icon>🔐</wz-icon> Microsoft Entra';
        credentialRow   = `<span class="value ok"><wz-icon>✅</wz-icon> Credencial guardada</span>`;
    } else if (useOAuth && hasOAuth) {
        connectionLabel = '<wz-icon>🔐</wz-icon> OAuth';
        credentialRow   = `<span class="value ok"><wz-icon>🔐</wz-icon> Sesión OAuth guardada</span>`;
    } else if (useOAuth) {
        connectionLabel = '<wz-icon>🔐</wz-icon> OAuth (pendiente)';
        credentialRow   = `<span class="value warn"><wz-icon>⚠</wz-icon> Sin OAuth — autentica antes de iniciar</span>`;
    } else if (hasLlmApiKey) {
        connectionLabel = '<wz-icon>🔑</wz-icon> API Key';
        credentialRow   = `<span class="value ok"><wz-icon>✅</wz-icon> Configurada</span>`;
    } else {
        connectionLabel = '<wz-icon>🔑</wz-icon> API Key (faltante)';
        credentialRow   = `<span class="value warn"><wz-icon>⚠</wz-icon> No configurada</span>`;
    }

    document.getElementById('config-summary').innerHTML = `
        <div class="info-row"><span class="label">Identidad</span><span class="value highlight">${escapeHtml(resolvedAssistantName)}</span></div>
        <div class="info-row"><span class="label">Personalidad</span><span class="value">${personality === 'custom' ? 'Personalizada' : 'IRIS ajustable'}</span></div>
        ${personalityTraits.length ? `<div class="info-row"><span class="label">Rasgos</span><span class="value dim" style="font-size:11px;max-width:300px;text-align:right">${escapeHtml(identityTraitCatalog.filter(item => personalityTraits.includes(item.id)).map(item => item.label).join(', '))}</span></div>` : ''}
        ${personalityInstructions ? `<div class="info-row"><span class="label">Tono</span><span class="value dim" style="font-size:11px;max-width:300px;text-align:right">${escapeHtml(personalityInstructions.slice(0, 80))}${personalityInstructions.length > 80 ? '…' : ''}</span></div>` : ''}
        ${personality === 'custom' && customBackstory ? `<div class="info-row"><span class="label"><wz-icon>📝</wz-icon> Backstory</span><span class="value dim" style="font-size:11px;max-width:300px;text-align:right">${escapeHtml(customBackstory.slice(0, 80))}${customBackstory.length > 80 ? '…' : ''}</span></div>` : ''}
        ${personality === 'iris' ? `<div class="info-row"><span class="label">Backstory IRIS</span><span class="value">${includeIrisBackstory ? 'Incluido' : 'Desactivado'}</span></div>` : ''}
        <div class="info-row"><span class="label"><wz-icon>🧠</wz-icon> Proveedor de IA</span>${llmProvider ? `<span class="value highlight">${escapeHtml(llmDisplayName)}</span>` : '<span class="value warn"><wz-icon>⚠</wz-icon> Sin configurar</span>'}</div>
        <div class="info-row"><span class="label"><wz-icon>🎨</wz-icon> Edición visual</span><span class="value">${(VISUAL_THEMES[config.visual_theme] || VISUAL_THEMES.iris).name}</span></div>
        <div class="info-row"><span class="label"><wz-icon>🧪</wz-icon> Modelo</span><span class="value">${llmModel ? escapeHtml(llmModel) : '<em>sin elegir</em>'}</span></div>
        <div class="info-row"><span class="label"><wz-icon>🔗</wz-icon> Conexión</span><span class="value">${connectionLabel}</span></div>
        <div class="info-row"><span class="label"><wz-icon>🔊</wz-icon> Voz</span><span class="value">${escapeHtml(ttsLabels[tts] || tts)}</span></div>
        <div class="info-row"><span class="label"><wz-icon>🎭</wz-icon> Avatar</span><span class="value">${config.avatar_vrm_path ? escapeHtml(config.avatar_vrm_path.split(/[\\/]/).pop()) : 'IRIS (incluida)'}</span></div>


        ${selectedChannels.length > 0 ? `<div class="info-row"><span class="label"><wz-icon>📱</wz-icon> Canales</span><span class="value">${escapeHtml(selectedChannels.join(', '))}</span></div>` : ''}
        <div class="info-row"><span class="label"><wz-icon>🔑</wz-icon> Acceso a la IA</span>${credentialRow}</div>
        ${tts === 'elevenlabs' ? `<div class="info-row"><span class="label"><wz-icon>🔑</wz-icon> ElevenLabs</span><span class="value ${hasElevenlabsKey ? 'ok' : 'warn'}">${mask(hasElevenlabsKey)}</span></div>` : ''}
        ${tts === 'fish-audio' ? `<div class="info-row"><span class="label"><wz-icon>🔑</wz-icon> Fish Audio</span><span class="value ${hasFishAudioKey ? 'ok' : 'warn'}">${mask(hasFishAudioKey)}</span></div>` : ''}
        ${tts === 'minimax' ? `<div class="info-row"><span class="label"><wz-icon>🔑</wz-icon> MiniMax</span><span class="value ${hasMinimaxKey ? 'ok' : 'warn'}">${mask(hasMinimaxKey)}</span></div>` : ''}
    `;

    // Determine launch command based on OS
    const isWindows = navigator.platform.includes('Win');
    if (!wizardRuntime.packaged) {
        document.getElementById('launch-command').textContent = isWindows
            ? 'python -m core.agent'
            : './scripts/start.sh';
    }

    // Save config (keys go to ~/.iris/config.json, never committed).
    // The endpoint may return non-fatal `warnings` (e.g. "voice id
    // not in the live Edge-TTS catalog") that we MUST surface — the
    // whole point of the W3 validation was to catch typos / stale
    // voice ids early, and silently swallowing them defeats the fix.
    try {
        const res = await fetch('/api/save-config', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-IRIS-Save-Session': _configSaveSession,
                'X-IRIS-Save-Generation': String(saveGeneration),
            },
            body: JSON.stringify(config),
            signal: saveController.signal,
        });
        const data = await res.json().catch(() => ({}));
        if (saveGeneration !== _configSaveGeneration) return;
        if (!res.ok) {
            const message = data.message || data.error || `No se pudo guardar la configuración (HTTP ${res.status}).`;
            _lastConfigSaveError = `No se guardó la configuración: ${message}`;
            _showConfigWarnings([_lastConfigSaveError]);
            _renderSummaryState('failed', message);
            return;
        }
        const warnings = Array.isArray(data?.warnings) ? data.warnings : [];
        if (warnings.length > 0) {
            // Render as a yellow toast / banner so the user sees them
            // BEFORE clicking "Lanzar IRIS" — otherwise they'd only
            // discover the typo when synthesis fails at the first turn.
            _showConfigWarnings(warnings);
        }
        _lastConfigSaveOk = true;
        _lastAiReadiness = data?.ai || null;
        const aiReady = _lastAiReadiness?.ready;
        if (aiReady === false) {
            _renderSummaryState('ai-pending', _lastAiReadiness.message || 'Falta configurar tu proveedor de IA.');
        } else {
            _renderSummaryState(aiReady === true ? 'ready' : 'saved');
        }
        // Preferences are saved (the Companion can use them), but IRIS cannot talk yet.
        if (launchButton) launchButton.disabled = aiReady === false;
        renderWizardCompanionLifecycle();
    } catch (e) {
        if (saveGeneration !== _configSaveGeneration) return;
        if (saveTimedOut) {
            _failConfigSaveByTimeout();
            return;
        }
        if (e?.name === 'AbortError') return;
        console.error('Config save failed:', e);
        const message = e.message || String(e);
        _lastConfigSaveError = `No se pudo conectar con el backend para guardar: ${message}`;
        _showConfigWarnings([_lastConfigSaveError]);
        _renderSummaryState('failed', message);
    } finally {
        clearTimeout(saveTimer);
    }
}

function _failConfigSaveByTimeout() {
    const message = 'El motor de IA tardó demasiado en confirmar el guardado. Comprueba que el equipo no esté muy ocupado y reintenta.';
    _lastConfigSaveOk = false;
    _lastConfigSaveError = `No se guardó la configuración: ${message}`;
    _showConfigWarnings([_lastConfigSaveError]);
    _renderSummaryState('failed', message);
}

async function loadWizardCompanionLifecycle() {
    if (_wizardCompanionBusy) return;
    _wizardCompanionBusy = 'refresh';
    _wizardCompanionError = '';
    renderWizardCompanionLifecycle();
    try {
        const response = await fetch('/api/companion/lifecycle', { cache: 'no-store' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        _wizardCompanionLifecycle = data;
    } catch (error) {
        _wizardCompanionLifecycle = null;
        _wizardCompanionError = error?.message || 'No se pudo diagnosticar el Companion.';
    } finally {
        _wizardCompanionBusy = '';
        renderWizardCompanionLifecycle();
    }
}

async function runWizardCompanionAction(action, requestedEnabled = null) {
    if (_wizardCompanionBusy) return;
    if (_wizardCompanionReadOnly && action !== 'refresh') return;
    if (action === 'refresh') {
        _wizardCompanionNotice = '';
        await loadWizardCompanionLifecycle();
        return;
    }
    if (action === 'install' && _wizardCompanionLifecycle?.installation?.running) {
        action = 'stop';
    }
    if (action === 'install' && _wizardCompanionLifecycle?.action === 'reinstall') {
        if (!await irisAsk({
            title: 'Reinstalar el Companion', icon: 'update',
            message: 'Se reinstalará IRIS en ventana (Desktop Companion) con el paquete local validado.',
            question: '¿Deseas continuar?', confirmLabel: 'Reinstalar', confirmIcon: 'update',
        })) return;
    }
    const routes = {
        install: ['/api/companion/install', 'install-companion'],
        launch: ['/api/companion/launch', 'launch-companion'],
        stop: ['/api/companion/stop', 'stop-companion'],
        autostart: ['/api/companion/autostart', 'configure-companion-autostart'],
    };
    const route = routes[action];
    if (!route) return;
    const enabled = action === 'autostart'
        ? Boolean(requestedEnabled ?? document.getElementById('wizard-companion-autostart')?.checked)
        : false;
    _wizardCompanionBusy = action;
    _wizardCompanionError = '';
    _wizardCompanionNotice = '';
    renderWizardCompanionLifecycle();
    try {
        const response = await fetch(route[0], {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-IRIS-Intent': route[1],
            },
            body: action === 'autostart' ? JSON.stringify({ enabled }) : undefined,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        _wizardCompanionLifecycle = data.lifecycle || _wizardCompanionLifecycle;
        _wizardCompanionBusy = '';
        await loadWizardCompanionLifecycle();
        const warnings = Array.isArray(data.warnings) ? data.warnings.join(' ') : '';
        _wizardCompanionNotice = action === 'launch' && data.status === 'focus_requested'
            ? `Companion ya estaba abierto; pedí a Windows traerlo al frente.${warnings ? ` ${warnings}` : ''}`
            : action === 'stop'
                ? `Desktop Companion cerrado.${warnings ? ` ${warnings}` : ''}`
                : warnings;
    } catch (error) {
        _wizardCompanionError = error?.message || 'No se pudo completar la acción del Companion.';
    } finally {
        _wizardCompanionBusy = '';
        renderWizardCompanionLifecycle();
    }
}

function renderWizardCompanionLifecycle() {
    const card = document.getElementById('wizard-companion-card');
    if (!card) return;
    const report = _wizardCompanionLifecycle;
    const setChannel = (name, code, detail, state = '') => {
        const channel = card.querySelector(`[data-wizard-companion-channel="${name}"]`);
        if (!channel) return;
        channel.dataset.state = state || code.toLowerCase();
        channel.querySelector('strong').textContent = code;
        channel.querySelector('small').textContent = detail;
    };
    if (!report) {
        for (const name of ['package', 'app', 'webview', 'core']) {
            setChannel(name, _wizardCompanionBusy ? 'SCAN' : 'WAIT', _wizardCompanionBusy ? 'Diagnóstico local en curso…' : 'Esperando diagnóstico.');
        }
    } else {
        const pkg = report.package || {};
        const app = report.installation || {};
        const webview = report.webview2 || {};
        const core = report.core || {};
        setChannel('package', pkg.trusted ? 'VALID' : pkg.available ? 'BLOCK' : 'NONE', pkg.trusted ? `${pkg.version || 'sin versión'} · ${pkg.signed ? 'firmado' : 'sin firma'}` : (pkg.error || 'No hay paquete local empaquetado.'), pkg.trusted ? 'ready' : pkg.available ? 'blocked' : 'missing');
        const processUnknown = app.running_known === false && (app.installed || app.process_check_required);
        setChannel('app', app.running ? 'LIVE' : processUnknown ? 'CHECK' : app.installed ? 'READY' : app.state === 'damaged' ? 'REPAIR' : 'ABSENT', app.installed ? `${app.version || 'versión desconocida'} · ${app.running ? 'ejecutándose' : processUnknown ? 'estado de proceso no comprobable' : 'cerrado'}` : processUnknown ? 'Instalación incompleta · proceso no comprobable.' : app.state === 'damaged' ? 'Instalación incompleta.' : 'Todavía no está instalado.', app.installed && !processUnknown ? 'ready' : app.installed || app.state === 'damaged' ? 'blocked' : 'missing');
        setChannel(
            'webview',
            webview.available ? 'READY' : 'MISS',
            webview.available
                ? `WebView2 ${webview.version || 'detectado'}`
                : pkg.webview2_mode === 'downloadBootstrapper'
                    ? 'No detectado · instalar puede requerir Internet.'
                    : 'Runtime no detectado.',
            webview.available ? 'ready' : 'blocked',
        );
        setChannel('core', core.available ? 'LIVE' : 'IDLE', core.available ? `Loopback:${core.port} responde` : `Core no responde en :${core.port}`, core.available ? 'ready' : 'missing');
    }

    const unsupportedPlatform = Boolean(report && report?.platform?.supported === false);
    const actionLabels = {
        install: 'Instalar Companion', repair: 'Reparar Companion', update: 'Actualizar Companion',
        reinstall: 'Reinstalar', close_required: 'Cierra el Companion', diagnostic_required: 'Diagnóstico incompleto', manual_migration_required: 'Requiere desinstalar', newer_repair_package_required: 'Paquete de reparación anterior', unavailable: unsupportedPlatform ? 'Solo Windows x64' : 'Paquete no disponible', ready: 'Instalado',
    };
    const install = document.getElementById('wizard-companion-install');
    const launch = document.getElementById('wizard-companion-launch');
    const autostart = document.getElementById('wizard-companion-autostart');
    const refresh = document.getElementById('wizard-companion-refresh');
    const lifecycleBlocked = Boolean(_wizardCompanionBusy)
        || Boolean(_wizardCompanionError)
        || _wizardCompanionReadOnly
        || (!_lastConfigSaveOk && !_wizardCompanionReadOnly);
    if (refresh) refresh.disabled = Boolean(_wizardCompanionBusy);
    if (install) {
        install.textContent = _wizardCompanionBusy === 'stop'
            ? 'Cerrando…'
            : report?.installation?.running
                ? 'Cierra el Companion'
                : _wizardCompanionBusy === 'install'
                    ? 'Instalando…'
                    : (actionLabels[report?.action] || 'Instalar Companion');
        install.disabled = lifecycleBlocked || !(
            report?.installation?.running
            || ['install', 'repair', 'update', 'reinstall'].includes(report?.action)
        );
    }
    if (launch) {
        launch.textContent = _wizardCompanionBusy === 'launch'
            ? 'Abriendo…'
            : report?.installation?.running ? 'Mostrar ventana' : 'Abrir Companion';
        launch.disabled = lifecycleBlocked
            || !report?.platform?.supported
            || !report?.installation?.installed
            || report?.installation?.running_known === false;
    }
    if (autostart) {
        autostart.checked = Boolean(report?.autostart?.enabled || report?.autostart?.stale);
        const autostartActive = Boolean(report?.autostart?.enabled || report?.autostart?.stale);
        autostart.disabled = lifecycleBlocked
            || (!report?.platform?.supported && !autostartActive)
            || !report?.autostart?.supported
            || (!report?.installation?.installed && !report?.autostart?.enabled && !report?.autostart?.stale);
    }
    const pill = document.getElementById('wizard-companion-pill');
    if (pill) pill.textContent = _wizardCompanionError ? 'ERROR' : _wizardCompanionBusy ? 'WORK' : report?.installation?.installed ? 'READY' : 'OPT-IN';
    card.dataset.state = _wizardCompanionError ? 'error' : report?.installation?.installed ? 'ready' : report?.package?.trusted ? 'available' : 'standby';
    const status = document.getElementById('wizard-companion-status');
    if (status) status.textContent = _wizardCompanionError
        || _wizardCompanionNotice
        || (_wizardCompanionBusy === 'install'
            ? 'Windows está instalando el paquete local validado…'
            : _wizardCompanionBusy === 'stop'
                ? 'Cerrando la presencia flotante…'
            : _wizardCompanionBusy === 'launch'
                ? 'Abriendo la presencia flotante…'
                : _wizardCompanionBusy === 'autostart'
                    ? 'Actualizando el inicio de Windows…'
                    : (!_lastConfigSaveOk && !_wizardCompanionReadOnly)
                        ? 'Guarda correctamente la configuración principal para habilitar estas acciones.'
                    : report?.installation?.state === 'damaged'
                        ? 'La instalación quedó incompleta. Pulsa “Reparar Companion”; al terminar se habilitarán “Abrir” e “Iniciar con Windows”.'
                    : report?.action === 'manual_migration_required'
                        ? 'La instalación actual requiere desinstalación con permisos de administrador antes de migrar a la versión por usuario.'
                    : report?.action === 'newer_repair_package_required'
                        ? 'La instalación dañada declara una versión más nueva. Incluye un paquete igual o posterior para repararla sin downgrade.'
                    : unsupportedPlatform
                        ? 'Desktop Companion solo está disponible en Windows x64.'
                    : report?.autostart?.stale
                        ? 'El autoinicio apunta a una instalación anterior; desactívalo o vuelve a activarlo para reparar el destino.'
                    : report?.package?.trusted && report?.package?.signed === false
                        ? 'SHA-256 correcto · esta compilación todavía no tiene firma digital.'
                        : report?.package?.error
                            ? report.package.error
                            : 'Diagnóstico local listo · esta comprobación no descargó ni compiló nada.');
}

document.getElementById('wizard-companion-autostart')?.addEventListener('change', (event) => {
    runWizardCompanionAction('autostart', Boolean(event.target?.checked));
});

/** Render a transient banner near the launch button listing any
 *  warnings the server reported. Auto-clears after 9s. Idempotent —
 *  calling twice replaces the previous banner instead of stacking. */
function _showConfigWarnings(warnings) {
    if (!warnings || warnings.length === 0) {
        clearTimeout(_showConfigWarnings._timer);
        document.getElementById('config-warnings-banner')?.remove();
        return;
    }
    const host = document.querySelector('[data-step="8"]') || document.body;
    let banner = document.getElementById('config-warnings-banner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'config-warnings-banner';
        banner.style.cssText =
            'margin:12px auto; padding:10px 14px; max-width:640px; ' +
            'background:rgba(255,193,7,0.10); border:1px solid rgba(255,193,7,0.35); ' +
            'border-radius:8px; color:#ffd54f; font-size:12px; line-height:1.5;';
        host.prepend(banner);
    }
    banner.innerHTML =
        '<strong><wz-icon>⚠</wz-icon> Avisos al guardar:</strong><ul style="margin:6px 0 0 18px; padding:0;">' +
        warnings.map(w => `<li>${String(w).replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        }[c]))}</li>`).join('') +
        '</ul>';
    clearTimeout(_showConfigWarnings._timer);
    _showConfigWarnings._timer = setTimeout(() => banner.remove(), 9000);
}

// ── IRIS Lifecycle (launch / stop / status) ────────────
//
// The "🚀 Lanzar IRIS" button used to just redirect to /face/, which
// only worked if the user had already started `python -m core.agent`
// in a terminal. Phase 1 makes it a real launcher: POST /api/iris/launch
// spawns the agent detached, we poll until the websocket port answers,
// then redirect. Errors surface as a banner instead of a silent broken
// redirect.
async function launchIris() {
    const btn = document.querySelector('[data-step="8"] .btn-gold');
    // «Abrir IRIS OS» on the first screen opens an already configured Iris without a
    // save in this session; once a save started, it must have finished well.
    const openingConfigured = _configSaveGeneration === 0 && document.body?.dataset?.configured === 'true';
    if (!_lastConfigSaveOk && !openingConfigured) {
        // The concrete reason (an OAuth step, a voice id…), not a guess.
        _showConfigWarnings([_lastConfigSaveError || 'Espera a que termine de guardarse la configuración antes de lanzar IRIS.']);
        return;
    }
    if (_lastAiReadiness?.ready === false) {
        _showConfigWarnings([_lastAiReadiness.message || 'Falta configurar tu proveedor de IA antes de lanzar IRIS.']);
        return;
    }
    if (!await _ensureEngineConsent()) {
        _showConfigWarnings([_engineConsentBlockedReason('iniciar el motor y abrir IRIS')]);
        return;
    }
    const originalText = btn ? btn.textContent : '';
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Abriendo IRIS OS…';
    }
    try {
        // The first-screen edition is a real OS preference, even when the
        // user launches directly without walking through the final summary.
        await commitInstallerTheme();
        if (window.parent !== window && new URLSearchParams(location.search).get('desktop') === '1'
            && new URL(document.referrer || location.href).origin === location.origin) {
            window.parent.postMessage({ type: 'iris.desktop.launch' }, location.origin);
            return;
        }
        const res = await fetch('/api/iris/launch', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            throw new Error(data.detail || data.message || `HTTP ${res.status}`);
        }
        // Wait for the websocket port to answer before redirecting —
        // otherwise the user gets a "site can't be reached" page in the
        // half-second between spawn and uvicorn binding.
        const port = data.port || 8765;
        const ok = await waitForIrisPort(port, 10000);
        if (!ok) {
            // Don't block the redirect entirely — IRIS is alive per
            // the API; the port might just be slow on first boot
            // (Whisper download). Show a hint.
            console.warn('IRIS launched but port did not respond in time');
        }
        // Update the badge immediately, then navigate.
        await refreshIrisStatus();
        window.location.href = `http://127.0.0.1:${port}/face/`;
    } catch (err) {
        if (btn) {
            btn.disabled = false;
            btn.textContent = originalText || 'Lanzar IRIS';
        }
        await irisTell({ title: 'No se pudo iniciar IRIS', icon: 'warning', message: String(err.message || err) });
    }
}

async function stopIris() {
    if (!await irisAsk({
        title: 'Detener IRIS', icon: 'warning', tone: 'danger',
        message: 'El avatar dejará de responder hasta que vuelvas a abrir IRIS.',
        question: '¿Detenerla?', confirmLabel: 'Detener',
    })) return;
    try {
        const res = await fetch('/api/iris/stop', { method: 'POST' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        await refreshIrisStatus();
    } catch (err) {
        await irisTell({ title: 'No se pudo detener IRIS', icon: 'warning', message: String(err.message || err) });
    }
}

// Poll loopback TCP via fetch — `/face/` is a static route on the
// agent's uvicorn so it answers as soon as the server is bound. We
// use HEAD to avoid pulling the index HTML; failures are treated as
// "not ready yet" rather than fatal.
async function waitForIrisPort(port, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        try {
            const r = await fetch(`http://127.0.0.1:${port}/face/`, {
                method: 'HEAD',
                mode: 'no-cors',
                cache: 'no-store',
            });
            // no-cors gives an opaque response; *any* response means a
            // server answered, which is all we need.
            return true;
        } catch (_) {
            await new Promise(r => setTimeout(r, 250));
        }
    }
    return false;
}

// Status badge — top-right corner of the wizard. Polled every 2s
// while the wizard is open. We don't bother with WebSockets here;
// the polling overhead is trivial (one localhost JSON call) and
// keeps the wiring stupid-simple.
async function refreshIrisStatus() {
    const badge = document.getElementById('iris-status-badge');
    if (!badge) return;
    try {
        const res = await fetch('/api/iris/status', { cache: 'no-store' });
        const data = await res.json();
        renderIrisStatusBadge(badge, data);
    } catch (_) {
        // Wizard backend itself is gone — show neutral state.
        renderIrisStatusBadge(badge, { running: false, _offline: true });
    }
}

function renderIrisStatusBadge(badge, data) {
    badge.classList.remove('running', 'stopped', 'offline');
    if (data._offline) {
        badge.classList.add('offline');
        badge.innerHTML = '<span class="dot"></span> Wizard offline';
        return;
    }
    if (data.running) {
        badge.classList.add('running');
        const uptime = formatUptime(data.uptime_s || 0);
        badge.innerHTML =
            `<span class="dot"></span> IRIS activa <span class="badge-meta">PID ${data.pid} · ${uptime}</span>` +
            `<button class="badge-stop" onclick="stopIris()" title="Detener IRIS">Stop</button>`;
    } else {
        badge.classList.add('stopped');
        badge.innerHTML = '<span class="dot"></span> IRIS detenida';
    }
}

function formatUptime(seconds) {
    if (seconds < 60) return `${seconds}s`;
    const m = Math.floor(seconds / 60);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    const mr = m % 60;
    return `${h}h ${mr}m`;
}

// Boot the polling loop once the wizard DOM is ready. 2s cadence is
// snappy enough for "I just clicked Launch" feedback without being
// chatty.
(function initIrisStatusPolling() {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initIrisStatusPolling);
        return;
    }
    refreshIrisStatus();
    setInterval(refreshIrisStatus, 2000);
})();


// ── Avatar Upload & Auto-Rig Handlers ──────────────────

function setupAvatarHandlers() {
    const uploadZone = document.getElementById('upload-zone');
    const modelFileInput = document.getElementById('model-file');

    if (uploadZone) {
        uploadZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadZone.classList.add('drag-active');
        });
        uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-active'));
        uploadZone.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadZone.classList.remove('drag-active');
            const file = e.dataTransfer?.files?.[0];
            if (file) handleAvatarUpload(file);
        });
    }

    if (modelFileInput) {
        modelFileInput.addEventListener('change', () => {
            const file = modelFileInput.files?.[0];
            if (file) handleAvatarUpload(file);
        });
    }
}

async function handleAvatarUpload(file) {
    const infoPanel = document.getElementById('model-info');
    const rigSection = document.getElementById('rig-section');
    const rigResult = document.getElementById('rig-result');

    if (infoPanel) {
        infoPanel.style.display = 'block';
        infoPanel.innerHTML = '<div class="loading-spinner"></div><p>Subiendo modelo...</p>';
    }
    if (rigSection) rigSection.style.display = 'none';
    if (rigResult) rigResult.style.display = 'none';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const res = await fetch('/api/upload-model', {
            method: 'POST',
            body: formData
        });
        const info = await res.json();

        if (res.ok) {
            wizardState.avatar_vrm_path = info.path;
            wizardState.uploadedModel = info.filename;

            let html = `<div class="info-row"><span class="label">Modelo cargado</span><span class="value ok">${escapeHtml(info.filename)}</span></div>`;
            html += `<div class="info-row"><span class="label">Tamaño</span><span class="value">${formatBytes(info.size_bytes)}</span></div>`;

            const isVrm = info.filename.toLowerCase().endsWith('.vrm');
            const hasVrmExt = info.has_vrm_extension === true;

            if (isVrm || hasVrmExt) {
                html += `<div class="note"><wz-icon>✅</wz-icon> Modelo VRM válido. Física de tela y animaciones anime habilitadas.</div>`;
            } else if (info.warning) {
                // .glb without VRMC_vrm — loads as static, but animations limited.
                html += `<div class="note highlight-section" style="margin-top:15px; border:2px solid var(--amber) !important; padding:15px; background: color-mix(in srgb, var(--installer-accent) 8%, transparent); border-radius:10px;">
                    <p style="margin-bottom:8px; font-weight:bold;"><wz-icon>⚠</wz-icon> GLB sin extensión VRM</p>
                    <p style="font-size:0.9em;">${escapeHtml(info.warning)}</p>
                </div>`;
            } else {
                html += `<div class="note"><wz-icon>✅</wz-icon> Formato compatible.</div>`;
            }
            if (infoPanel) {
                infoPanel.innerHTML = html;
                infoPanel.style.display = 'block';
            }
        } else {
            if (infoPanel) infoPanel.innerHTML = `<p class="err">Error: ${escapeHtml(info.detail || 'Fallo desconocido al subir')}</p>`;
        }
    } catch (e) {
        if (infoPanel) infoPanel.innerHTML = `<p class="err">Error de conexión: ${escapeHtml(e.message)}</p>`;
    } finally {
        const input = document.getElementById('model-file');
        if (input) input.value = '';
    }
}

async function rigModel() {
    const filename = wizardState.uploadedModel;
    const infoPanel = document.getElementById('model-info');

    if (!filename) {
        await irisTell({ title: 'Falta el modelo', icon: 'info', message: 'Sube un modelo primero.' });
        return;
    }

    // Buscamos o creamos el contenedor de resultados dentro del panel de info
    let result = document.getElementById('rig-result');
    if (!result) {
        result = document.createElement('div');
        result.id = 'rig-result';
        result.className = 'info-panel highlight-section';
        result.style.marginTop = '10px';
        if (infoPanel) infoPanel.appendChild(result);
    }

    // Desactivar botones de rigging para evitar spam
    const rigBtn = document.querySelector('button[onclick="rigModel()"]');
    if (rigBtn) {
        rigBtn.disabled = true;
        rigBtn.innerHTML = '<div class="loading-spinner" style="width:14px; height:14px; border-width:2px;"></div> Procesando huesos y piel...';
        rigBtn.style.opacity = '0.7';
    }

    result.style.display = 'block';
    result.innerHTML = '<p>Auto-rigging en progreso... IRIS está mapeando el esqueleto. Esto puede tardar hasta 2 minutos.</p>';

    try {
        const res = await fetch(`/api/rig-model?filename=${encodeURIComponent(filename)}&method=auto`, {
            method: 'POST',
        });
        const d = await res.json();

        if (d.status === 'success') {
            wizardState.avatar_vrm_path = d.output_path;

            if (rigBtn) {
                rigBtn.innerHTML = '<wz-icon>✅</wz-icon> RIGGING COMPLETADO';
                rigBtn.style.background = 'var(--green-dim)';
            }

            if (d.method === 'heuristic') {
                result.innerHTML = `<div class="warn"><wz-icon>⚠</wz-icon> Rigging superficial aplicado. Notamos que no hay Blender cargado, por lo que IRIS usó física heurística.</div>
                <div class="info-row"><span class="label">Archivo VRM</span><span class="value">${d.output_path.split(/[\\/]/).pop()}</span></div>`;
            } else {
                result.innerHTML = `
                    <div class="ok"><wz-icon>✅</wz-icon> Rigging físico completado con éxito (${d.method})</div>
                    <div class="info-row"><span class="label">Huesos detectados</span><span class="value">${d.bones_found || 'Normal'}</span></div>
                    <div class="info-row"><span class="label">Avatar Listo</span><span class="value">${d.output_path.split(/[\\/]/).pop()}</span></div>
                `;
            }
        } else {
            console.error('[Wizard] Rigging failed:', d);
            if (rigBtn) {
                rigBtn.disabled = false;
                rigBtn.innerHTML = '<wz-icon>✨</wz-icon> REINTENTAR AUTO-RIG';
                rigBtn.style.background = 'var(--red-dim)';
            }
            result.innerHTML = `<div class="err"><wz-icon>❌</wz-icon> Error en rigging: ${escapeHtml(d.message || 'Fallo interno en el motor de esqueleto')}</div>`;
        }
    } catch (e) {
        console.error('[Wizard] Communication crash:', e);
        if (rigBtn) {
            rigBtn.disabled = false;
            rigBtn.innerHTML = '<wz-icon>✨</wz-icon> REINTENTAR CONEXIÓN';
        }
        result.innerHTML = `<div class="err"><wz-icon>❌</wz-icon> Error de conexión: ${escapeHtml(e.message)}</div>`;
    }
}

// ── Init ────────────────────────────────────────────────

function formatBytes(bytes, decimals = 2) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}


async function initWizard() {
    console.log('[Wizard] Iniciando sistema robusto v1.4...');
    _prepareDocumentationLinks();
    await loadWizardRuntime();

// 1. Cargar configuracion actual
    try {
        const res = await fetch('/api/current-config');
        if (res.ok) {
            const config = await res.json();
            renderInstallerConfigReadout(config);
            _engineConsent = config.hermes_security_acknowledged === true;

            // Do not let a slow /api/current-config response undo a choice
            // the user already made while the first screen was loading.
            if (!visualThemeTouched) {
                const savedTheme = VISUAL_THEMES[config.visual_theme]
                    ? config.visual_theme
                    : 'iris';
                wizardState.visual_theme_selected = Boolean(config.visual_theme_selected);
                applyInstallerTheme(savedTheme, {
                    selected: wizardState.visual_theme_selected,
                    persisted: wizardState.visual_theme_selected,
                });
            }

            // One name field is shared by the official and custom modes.
            const savedName = (config.assistant_name || '').trim();
            if (savedName) {
                const nameE = document.getElementById('assistant-name');
                if (nameE) nameE.value = savedName;
                wizardState.assistant_name = savedName;
            }

            // Personalidad — primero seleccionar el radio para actualizar el UI
            const rawPersonality = (config.personality_template || 'iris').toLowerCase();
            const savedPersonality = rawPersonality === 'custom' ? 'custom' : 'iris';
            const personalityRadio = document.querySelector(`input[name="personality"][value="${savedPersonality}"]`);
            if (personalityRadio) {
                // Click en el LABEL (radio-card), no el input directo, para que
                // el event handler del card actualice correctamente wizardState y el UI
                personalityRadio.closest('.radio-card')?.click();
            } else {
                wizardState.personality_template = savedPersonality;
            }

            const legacyPersonality = (config.custom_personality || '').trim();
            const savedInstructions = (config.personality_instructions || '').trim()
                || (savedPersonality === 'custom' ? legacyPersonality : '');
            const irisInstructions = document.getElementById('iris-personality-instructions');
            const customInstructions = document.getElementById('custom-personality-instructions');
            if (savedPersonality === 'custom' && customInstructions) customInstructions.value = savedInstructions;
            if (savedPersonality === 'iris' && irisInstructions) irisInstructions.value = savedInstructions;
            const backstory = document.getElementById('custom-backstory');
            if (backstory) backstory.value = (config.custom_backstory || '').trim();
            const includeIris = document.getElementById('include-iris-backstory');
            if (includeIris) includeIris.checked = config.include_iris_backstory !== false;
            wizardState.personality_instructions = savedInstructions;
            wizardState.personality_traits = Array.isArray(config.personality_traits)
                ? config.personality_traits
                : [];
            wizardState.custom_backstory = (config.custom_backstory || '').trim();
            wizardState.include_iris_backstory = config.include_iris_backstory !== false;

            // Avatar: restaurar selección guardada
            const savedAvatarType = config.avatar_vrm_path ? 'vrm' : 'iris';
            wizardState.avatar_choice = savedAvatarType;
            wizardState.avatar_vrm_path = config.avatar_vrm_path || '';

            // Seleccionar el radio correcto
            const avatarRadio = document.querySelector(`input[name="avatar"][value="${savedAvatarType}"]`);
            if (avatarRadio) {
                document.querySelectorAll('input[name="avatar"]').forEach(r => r.checked = false);
                document.querySelectorAll('input[name="avatar"]').forEach(input => {
                    input.closest('.radio-card')?.classList.remove('selected');
                });
                avatarRadio.checked = true;
                avatarRadio.closest('label')?.classList.add('selected');
            }

            // Mostrar upload section si había VRM guardado
            if (savedAvatarType === 'vrm') {
                const uploadSection = document.getElementById('model-upload-section');
                if (uploadSection) uploadSection.style.display = 'block';
                // Si hay path del VRM, mostrar info del modelo ya cargado
                if (wizardState.avatar_vrm_path) {
                    const infoPanel = document.getElementById('model-info');
                    if (infoPanel) {
                        const filename = wizardState.avatar_vrm_path.split(/[\\/]/).pop();
                        infoPanel.innerHTML = `<div class="info-row"><span class="label">Modelo cargado</span><span class="value ok">${escapeHtml(filename)}</span></div><div class="note"><wz-icon>✅</wz-icon> Modelo VRM cargado previamente.</div>`;
                        infoPanel.style.display = 'block';
                    }
                }
            }

            // TTS — restore the saved provider + per-engine voice id so
            // re-opening the wizard shows what the user actually has
            // configured today, not the static defaults. Previously the
            // wizard always opened on "Edge-TTS / Dalia" regardless of
            // what was on disk, making it look like the previous
            // selection had been lost.
            const savedTTS = (config.tts_provider || '').trim();
            if (savedTTS) {
                wizardState.tts_provider = savedTTS;
                const ttsRadio = document.querySelector(`input[name="tts"][value="${savedTTS}"]`);
                if (ttsRadio) {
                    document.querySelectorAll('input[name="tts"]').forEach(r => r.checked = false);
                    document.querySelectorAll('input[name="tts"]').forEach(input => {
                        input.closest('.radio-card')?.classList.remove('selected');
                    });
                    ttsRadio.checked = true;
                    ttsRadio.closest('label.radio-card')?.classList.add('selected');
                }
            }
            // Edge-TTS — the selected voice id is what `loadVoices()`
            // highlights in the grid. We store it in module-level
            // `selectedVoice` (declared near the top of this file) so
            // `renderVoices` marks the right card on first paint.
            if (config.edge_tts_voice) {
                wizardState.edge_tts_voice = config.edge_tts_voice;
                try { selectedVoice = config.edge_tts_voice; } catch {}
            }
            // Kokoro — restore the <select>'s value if the option exists.
            if (config.kokoro_voice) {
                const migratedKokoroVoice = {
                    ef_alice: 'af_heart',
                    ef_bella: 'af_bella',
                    ef_sarah: 'af_sarah',
                }[config.kokoro_voice] || config.kokoro_voice;
                wizardState.kokoro_voice = migratedKokoroVoice;
                const koSel = document.getElementById('kokoro-voice');
                if (koSel && [...koSel.options].some(o => o.value === migratedKokoroVoice)) {
                    koSel.value = migratedKokoroVoice;
                }
            }

            // HF speech-to-speech / Qwen3-TTS — restore both the reviewed
            // checkpoint and its preset speaker or local clone reference.
            const hfModelSelect = document.getElementById('hf-s2s-model');
            if (config.hf_s2s_model) {
                wizardState.hf_s2s_model = config.hf_s2s_model;
                if (hfModelSelect && [...hfModelSelect.options].some(o => o.value === config.hf_s2s_model)) {
                    hfModelSelect.value = config.hf_s2s_model;
                }
            }
            const hfSpeakerSelect = document.getElementById('hf-s2s-speaker');
            if (config.hf_s2s_speaker) {
                wizardState.hf_s2s_speaker = config.hf_s2s_speaker;
                if (hfSpeakerSelect && [...hfSpeakerSelect.options].some(o => o.value === config.hf_s2s_speaker)) {
                    hfSpeakerSelect.value = config.hf_s2s_speaker;
                }
            }
            if (config.hf_s2s_ref_audio) wizardState.hf_s2s_ref_audio = config.hf_s2s_ref_audio;
            if (config.hf_s2s_ref_text) wizardState.hf_s2s_ref_text = config.hf_s2s_ref_text;

            wizardState.hermes_wake_mode = 'keep';
            wizardState.hermes_wake_phrase = config.hermes_wake_phrase || '';
            // ElevenLabs / MiniMax voice ids — these live on the API
            // Keys step (free-text fields). Restoring them here means
            // a user re-running the wizard sees their saved id, not a
            // blank input that would silently revert on next save.
            const elevenInput = document.getElementById('key-elevenlabs-voice');
            if (elevenInput && config.elevenlabs_voice_id) {
                elevenInput.value = config.elevenlabs_voice_id;
                wizardState.elevenlabs_voice_id = config.elevenlabs_voice_id;
            }
            const minimaxInput = document.getElementById('key-minimax-voice');
            if (minimaxInput && config.minimax_voice_id) {
                minimaxInput.value = config.minimax_voice_id;
                wizardState.minimax_voice_id = config.minimax_voice_id;
            }
            // Restore Fish Audio's public settings; never return its saved key.
            const fishAudioInput = document.getElementById('key-fish-audio-voice');
            if (fishAudioInput) fishAudioInput.value = config.fish_audio_voice_id || '';
            const fishAudioModelInput = document.getElementById('fish-audio-model');
            if (fishAudioModelInput) fishAudioModelInput.value = config.fish_audio_model || 's2.1-pro-free';
            _setFishAudioVoiceName(config.fish_audio_voice_id ? config.fish_audio_voice_name || '' : '');
            const fishAudioSample = document.getElementById('fish-audio-sample');
            if (fishAudioSample) fishAudioSample.disabled = !config.fish_audio_voice_id;
            // Voices in the assistant's language first, when Fish has that language.
            const fishAudioLanguage = document.getElementById('fish-audio-language');
            const assistantLanguage = String(config.language || '').slice(0, 2).toLowerCase();
            if (fishAudioLanguage && [...fishAudioLanguage.options].some(option => option.value && option.value === assistantLanguage)) {
                fishAudioLanguage.value = assistantLanguage;
            }
            // NeuTTS — surfacing the saved reference path so the user
            // knows their clone is configured. If empty, the recorder
            // UI prompts them to capture one.
            if (config.neutts_ref_audio) wizardState.neutts_ref_audio = config.neutts_ref_audio;
            if (config.neutts_ref_text)  wizardState.neutts_ref_text  = config.neutts_ref_text;
            const remoteFallbackInput = document.getElementById('tts-allow-remote-fallback');
            if (remoteFallbackInput) {
                remoteFallbackInput.checked = Boolean(config.tts_allow_remote_fallback);
            }

            // Fish-Speech — server URL + optional reference audio path.
            const fishUrlInput = document.getElementById('fish-speech-url');
            if (fishUrlInput && config.fish_speech_url) {
                fishUrlInput.value = config.fish_speech_url;
                wizardState.fish_speech_url = config.fish_speech_url;
            }
            const fishRefInput = document.getElementById('fish-speech-ref-audio');
            if (fishRefInput && config.fish_speech_ref_audio) {
                fishRefInput.value = config.fish_speech_ref_audio;
                wizardState.fish_speech_ref_audio = config.fish_speech_ref_audio;
            }
            const fishRefTextInput = document.getElementById('fish-speech-ref-text');
            if (fishRefTextInput && config.fish_speech_ref_text) {
                fishRefTextInput.value = config.fish_speech_ref_text;
                wizardState.fish_speech_ref_text = config.fish_speech_ref_text;
            }
            const fishReferenceIdInput = document.getElementById('fish-speech-reference-id');
            if (fishReferenceIdInput && config.fish_speech_reference_id) {
                fishReferenceIdInput.value = config.fish_speech_reference_id;
                wizardState.fish_speech_reference_id = config.fish_speech_reference_id;
            }
            // QA12: a profile that never chose an edition starts there, before the welcome.
            if (document.body.dataset.configured !== 'true' && !config.visual_theme_selected
                && !visualThemeTouched && currentStep === 0) {
                startThemeFirst();
            }

            // Toggle the voice-selector / kokoro / clone sections based
            // on the restored provider so the UI matches the state.
            if (typeof updateVoiceSelectorVisibility === 'function') {
                try { updateVoiceSelectorVisibility(); } catch {}
            }

            console.log('[Wizard] Configuración cargada.');
        }
    } catch(e) {
        console.warn('[Wizard] Error al cargar config previa:', e);
        renderInstallerConfigReadout({});
    }

    await initIdentityStudio();

    // 2. Setup Handlers (Independientes)
    const runSetup = (name, fn) => {
        try {
            fn();
            console.log(`[Wizard] ${name} activo.`);
        } catch(e) {
            console.error(`[Wizard] Error crítico en ${name}:`, e);
        }
    };

    runSetup('Canales', setupChannelHandlers);
    runSetup('Avatar', setupAvatarHandlers);
    // detectHardware se ejecuta automáticamente al entrar en el paso 2, eliminamos duplicado


    // 3. Update Final. The local QA preview renders the final optional card
    // without calling showSummaryAndSave(), so visual checks never rewrite a
    // real profile merely to reach step 8.
    if (_wizardCompanionReadOnly) {
        document.querySelectorAll('.wizard-step').forEach((element) => {
            element.classList.toggle('active', element.dataset.step === '8');
        });
        document.body.dataset.wizardStep = '8';
        document.body.dataset.wizardFlow = 'preview';
        const progress = document.getElementById('progress-fill');
        const indicator = document.getElementById('step-indicator');
        const summary = document.getElementById('config-summary');
        const launch = document.querySelector('[data-step="8"] .btn-gold');
        if (progress) progress.style.width = '100%';
        if (indicator) indicator.textContent = 'QA PREVIEW';
        if (summary) summary.innerHTML = '<div class="info-row"><span class="label">Vista segura</span><span class="value ok">Sin guardar configuración</span></div>';
        if (launch) launch.disabled = true;
        setInstallerStatus('PREVIEW', 'COMPANION');
        setInstallerModeLabel('READ ONLY');
        void loadWizardCompanionLifecycle();
    } else {
        try { updateUI(); } catch(e) {}
    }
    console.log('[Wizard] Inicialización completada.');
}

// Aseguramos que el botón de rigging aparezca si hay un modelo pendiente
function checkModelPendingOnStepChange(step) {
    if (step === 5 && wizardState.uploadedModel) {
        const infoPanel = document.getElementById('model-info');
        if (infoPanel && infoPanel.style.display === 'block') {
            console.log('[Wizard] Verificando estado de rigging para:', wizardState.uploadedModel);
            // Si no hay ya un botón de rigging y no es un VRM, lo inyectamos
            if (!infoPanel.innerHTML.includes('rigModel()') && !wizardState.uploadedModel.toLowerCase().endsWith('.vrm')) {
                const btnHtml = `<div class="note highlight-section" style="margin-top:15px; border:2px solid var(--amber) !important; padding:15px; background: color-mix(in srgb, var(--installer-accent) 10%, transparent); border-radius:10px;">
                    <p style="margin-bottom:10px; font-weight:bold;"><wz-icon>✨</wz-icon> Modelo detectado — ¿Quieres añadir esqueleto y físicas?</p>
                    <button class="btn-primary" onclick="rigModel()" style="padding: 14px 20px; border-radius: 8px; cursor: pointer; font-weight: bold; width: 100%; font-size: 14px;"><wz-icon>✨</wz-icon> REALIZAR AUTO-RIG PROFUNDO (CON FÍSICAS)</button>
                </div>`;
                infoPanel.innerHTML += btnHtml;
            }
        }
    }
}

// start
initWizard();

