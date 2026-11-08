/**
 * F07 "Automatizaciones": rules, their history and each result, over Core's
 * `/api/automations`.
 *
 * The window shows what Core answered and never runs anything itself: the
 * engine and the executor live in Core. A rule is created paused; enabling it
 * is an explicit, confirmed step; the `automations.run` permission stays in
 * Capability Center and is never changed from here. It refreshes only while
 * the window is really visible, keeps the form's draft and focus across
 * refreshes, and sets every text from Hermes or the user with textContent.
 */

import {
    CANCELLABLE_STATES,
    ACTIVE_STATES,
    SOURCES,
    cancelText,
    createPayload,
    draftFromRule,
    emptyDraft,
    enableConfirmText,
    errorText,
    minutesText,
    modelChoices,
    normalizeAutomations,
    permissionView,
    ruleView,
    sameDraft,
    sourceView,
    stateView,
    updatePayload,
    validateDraft,
} from './automation_state.js?v=1';
import { normalizeSelectorState, parseSelectionValue } from './model_selector_state.js?v=1';
import { irisConfirm } from './iris_dialog.js?v=9';

const ACTIVE_REFRESH_MS = 5000;
const IDLE_REFRESH_MS = 20000;

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function when(seconds) {
    const date = new Date(seconds * 1000);
    return Number.isFinite(date.getTime()) && seconds > 0
        ? date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
        : 'Fecha no disponible';
}

function pill(view) {
    const node = el('span', `automation-pill tone-${view.tone}`, view.label);
    node.title = view.help || '';
    return node;
}

/** Which of the controls rendered last time has the keyboard focus, by its stable key. */
function focusedKey(controls) {
    const active = globalThis.document?.activeElement;
    for (const [key, node] of controls) if (node === active) return key;
    return '';
}

export class AutomationApp {
    constructor({ root = null, fetchImpl = globalThis.fetch?.bind(globalThis), isVisible = () => false,
        onSummary = () => {}, onOpenApp = null, confirmImpl = (message, options = {}) => irisConfirm({ message, ...options }),
        setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = (id) => clearTimeout(id) } = {}) {
        this.root = root;
        this.fetch = fetchImpl;
        this.isVisible = isVisible;
        this.onSummary = onSummary;
        this.onOpenApp = onOpenApp;
        this.confirm = confirmImpl;
        this._setTimer = setTimer;
        this._clearTimer = clearTimer;
        this.state = null;
        this.loading = false;
        this.saving = false;
        this.error = ''; // the last load failed
        this.actionError = ''; // the last action failed: kept until the user acts again
        this.notice = '';
        this.tab = 'rules';
        this.historyRule = '';
        this.editing = null; // null: form closed; 'new' or a rule id
        this.draft = null;
        this.fieldErrors = {};
        this.selector = null;
        this.selectorError = '';
        this._generation = 0;
        this._reloadPending = false;
        this._timer = null;
        this._ruleControls = new Map(); // refreshes rebuild the lists: focus moves to the same control
        this._historyControls = new Map();
        if (!root) return;
        this._build();
        this.render();
    }

    // ── Structure ──────────────────────────────────────────────────────

    _build() {
        this.surface = el('section', 'automation-app');
        const hero = el('header', 'automation-hero');
        const intro = el('div', 'automation-intro');
        intro.append(el('span', 'automation-kicker', 'IRIS OS / AUTOMATIZACIONES'), el('h2', '', 'Automatizaciones'),
            el('p', '', 'Tareas que Hermes hace mientras no usas Iris. Tú decides cuáles, cuándo y con qué límites.'));
        this.counter = el('div', 'automation-counter');
        this.counterValue = el('strong', '', '—');
        this.counter.append(this.counterValue, el('span', '', 'ACTIVAS'));
        hero.append(intro, this.counter);

        this.status = el('p', 'automation-status');
        this.status.setAttribute('role', 'status');
        this.status.setAttribute('aria-live', 'polite');

        this.tabs = el('div', 'automation-tabs');
        this.tabs.setAttribute('role', 'tablist');
        this.tabs.setAttribute('aria-label', 'Automatizaciones');
        this.tabButtons = {
            rules: this._tab('rules', 'Reglas'),
            history: this._tab('history', 'Historial'),
        };
        this.tabs.append(this.tabButtons.rules, this.tabButtons.history);
        this.tabs.addEventListener('keydown', (event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault?.();
            this._showTab(this.tab === 'rules' ? 'history' : 'rules', { focus: true });
        });

        const layout = el('div', 'automation-layout');
        const main = el('div', 'automation-main');

        this.rulesPane = el('div', 'automation-pane');
        this.rulesPane.setAttribute('role', 'tabpanel');
        const toolbar = el('div', 'automation-toolbar');
        this.newBtn = this._button('Nueva regla', () => this._openForm('new'), 'automation-primary');
        this.refreshBtn = this._button('Actualizar', () => void this.load(true));
        toolbar.append(this.newBtn, this.refreshBtn);
        this.form = this._buildForm();
        this.rulesList = el('ul', 'automation-rules');
        this.rulesPane.append(toolbar, this.form, this.rulesList);

        this.historyPane = el('div', 'automation-pane');
        this.historyPane.setAttribute('role', 'tabpanel');
        this.historyPane.hidden = true;
        const filterLabel = el('label', 'automation-filter');
        filterLabel.append(el('span', '', 'Regla'));
        this.historyFilter = el('select');
        this.historyFilter.setAttribute('aria-label', 'Filtrar el historial por regla');
        this.historyFilter.addEventListener('change', () => {
            this.historyRule = this.historyFilter.value;
            this._renderHistory();
        });
        filterLabel.append(this.historyFilter);
        this.historyList = el('ul', 'automation-history');
        this.historyPane.append(filterLabel, this.historyList);

        main.append(this.rulesPane, this.historyPane);
        this.side = el('aside', 'automation-side');
        layout.append(main, this.side);
        this.surface.append(hero, this.status, this.tabs, layout);
        this.root.replaceChildren(this.surface);
    }

    _tab(id, label) {
        const button = el('button', 'automation-tab', label);
        button.type = 'button';
        button.setAttribute('role', 'tab');
        button.dataset.tab = id;
        button.addEventListener('click', () => this._showTab(id, { focus: false }));
        return button;
    }

    _button(label, onClick, className = '') {
        const button = el('button', className, label);
        button.type = 'button';
        button.addEventListener('click', () => { if (!button.disabled) onClick(); });
        return button;
    }

    _field(label, control, help = '') {
        const wrap = el('label', 'automation-field');
        const error = el('small', 'automation-field-error', '');
        wrap.append(el('span', '', label), control);
        if (help) wrap.append(el('small', 'automation-help', help));
        wrap.append(error);
        return { wrap, error };
    }

    _input(name, type = 'text', attrs = {}) {
        const input = el(type === 'textarea' ? 'textarea' : 'input');
        if (type !== 'textarea') input.type = type;
        input.name = name;
        for (const [key, value] of Object.entries(attrs)) input.setAttribute(key, value);
        input.addEventListener('input', () => {
            if (!this.draft) return;
            this.draft = { ...this.draft, [name]: input.value };
            if (Object.keys(this.fieldErrors).length) {
                this.fieldErrors = validateDraft(this.draft);
                this._renderFieldErrors();
            }
            this._renderFormButtons();
        });
        return input;
    }

    _buildForm() {
        const form = el('form', 'automation-form');
        form.hidden = true;
        form.setAttribute('novalidate', '');
        form.addEventListener('submit', (event) => { event.preventDefault?.(); void this.save(); });
        this.formTitle = el('h3', '', 'Nueva regla');
        this.fields = {};
        this.inputs = {
            title: this._input('title', 'text', { maxlength: '120', autocomplete: 'off' }),
            prompt: this._input('prompt', 'textarea', { maxlength: '4000', rows: '4' }),
            thresholdMinutes: this._input('thresholdMinutes', 'number', { min: '1', max: '1440', step: '1' }),
            timeoutMinutes: this._input('timeoutMinutes', 'number', { min: '1', max: '120', step: '1' }),
            maxRunsPerDay: this._input('maxRunsPerDay', 'number', { min: '1', max: '48', step: '1' }),
        };
        this.fields.title = this._field('Nombre', this.inputs.title);
        this.fields.prompt = this._field('Qué hará Hermes', this.inputs.prompt,
            'Hermes la hace en una sesión propia, sin tu conversación, con sus herramientas y aprobaciones de siempre.');

        const sources = el('fieldset', 'automation-sources');
        sources.append(el('legend', '', 'Cuándo'));
        this.sourceInputs = {};
        for (const [id, source] of Object.entries(SOURCES)) {
            const label = el('label', 'automation-source');
            const input = el('input');
            input.type = 'radio';
            input.name = 'automation-source';
            input.value = id;
            input.addEventListener('change', () => {
                if (this.draft && input.checked) this.draft = { ...this.draft, source: id };
                this._renderThresholdLabel();
                this._renderFormButtons();
            });
            const copy = el('span', 'automation-source-copy');
            copy.append(el('strong', '', source.label), el('small', '', source.help));
            label.append(input, copy);
            this.sourceInputs[id] = { input, label };
            sources.append(label);
        }
        this.fields.source = { wrap: sources, error: el('small', 'automation-field-error', '') };
        sources.append(this.fields.source.error);

        this.fields.thresholdMinutes = this._field('Minutos sin usar Iris', this.inputs.thresholdMinutes);
        this.fields.timeoutMinutes = this._field('Plazo para terminar (minutos)', this.inputs.timeoutMinutes,
            'Si Hermes no termina a tiempo, Iris le pide detenerla y lo dice.');
        this.fields.maxRunsPerDay = this._field('Máximo de veces al día', this.inputs.maxRunsPerDay);
        const numbers = el('div', 'automation-numbers');
        numbers.append(this.fields.thresholdMinutes.wrap, this.fields.timeoutMinutes.wrap, this.fields.maxRunsPerDay.wrap);

        this.modelSelect = el('select');
        this.modelSelect.name = 'model';
        this.modelSelect.addEventListener('change', () => {
            if (this.draft) this.draft = { ...this.draft, model: this.modelSelect.value };
            this._renderFormButtons();
        });
        this.fields.model = this._field('Modelo', this.modelSelect,
            'Elegirlo aquí no cambia el modelo de tu conversación ni el predeterminado de Hermes.');

        this.voiceInput = el('input');
        this.voiceInput.type = 'checkbox';
        this.voiceInput.addEventListener('change', () => {
            if (this.draft) this.draft = { ...this.draft, voice: this.voiceInput.checked };
            this._renderFormButtons();
        });
        const voiceLabel = el('label', 'automation-filter');
        voiceLabel.append(this.voiceInput, el('span', '', 'Leer un resumen al volver (opcional)'));
        const limits = el('div', 'automation-limits');
        limits.append(
            el('p', '', 'Perfil de Hermes: predeterminado.'),
            el('p', '', 'Límites de herramientas o de tokens por tarea: Hermes 0.21.6 no los admite, así que no se ofrecen.'),
            el('p', '', 'El resultado siempre queda en la bandeja. La voz requiere también activar «Entregas por voz» y estar disponible.'),
        );

        const actions = el('div', 'automation-form-actions');
        this.saveBtn = el('button', 'automation-primary', 'Guardar en pausa');
        this.saveBtn.type = 'submit';
        this.cancelFormBtn = this._button('Cancelar', () => this._closeForm());
        actions.append(this.saveBtn, this.cancelFormBtn);

        form.append(this.formTitle, this.fields.title.wrap, this.fields.prompt.wrap, sources, numbers,
            this.fields.model.wrap, voiceLabel, limits, actions);
        return form;
    }

    // ── Data ───────────────────────────────────────────────────────────

    async _request(url, init = {}) {
        let response;
        try {
            response = await this.fetch(url, { cache: 'no-store', ...init });
        } catch {
            throw new Error('Core no responde. Puedes reintentar.');
        }
        let body = null;
        try { body = await response.json(); } catch { body = null; }
        if (!response.ok) {
            const error = new Error(errorText(body, 'No se pudo completar la acción.'));
            error.status = response.status;
            error.code = body?.detail?.code || '';
            throw error;
        }
        return body;
    }

    async load(force = false) {
        if (!this.root) return;
        if (this.saving) { this._reloadPending = true; return; }
        if (!force && this.state && this.loading) return;
        const generation = ++this._generation;
        this.loading = true;
        this.render();
        try {
            const body = await this._request('/api/automations');
            if (generation !== this._generation) return; // an older answer: discarded
            this.state = normalizeAutomations(body);
            this.error = '';
            this.onSummary(this._summary());
        } catch (error) {
            if (generation !== this._generation) return;
            this.error = error.message; // the last good state stays visible, marked as not updated
        } finally {
            if (generation === this._generation) {
                this.loading = false;
                this.render();
                this._schedule();
            }
        }
    }

    _summary() {
        const active = this.state?.rules.filter((rule) => rule.enabled).length || 0;
        const running = this.state?.occurrences.filter((o) => ACTIVE_STATES.has(o.state)).length || 0;
        return running ? `${running} en curso` : active ? `${active} activa${active === 1 ? '' : 's'}` : 'Sin reglas activas';
    }

    /** Refresh only while the window is really visible; the scheduler itself lives in Core. */
    _schedule() {
        this._clearTimer(this._timer);
        this._timer = null;
        if (!this.state || !this.isVisible()) return;
        const busy = this.state.occurrences.some((o) => ACTIVE_STATES.has(o.state));
        const enabled = this.state.rules.some((rule) => rule.enabled);
        if (!busy && !enabled) return;
        this._timer = this._setTimer(() => {
            this._timer = null;
            if (this.isVisible()) void this.load(true);
        }, busy ? ACTIVE_REFRESH_MS : IDLE_REFRESH_MS);
    }

    /** The shell calls this when the window is shown, hidden, minimized or closed. */
    syncVisibility() {
        if (!this.isVisible()) {
            this._clearTimer(this._timer);
            this._timer = null;
        } else if (!this._timer && !this.loading) {
            void this.load(true);
        }
    }

    stop() {
        this._clearTimer(this._timer);
        this._timer = null;
        this._generation += 1;
        this.loading = false;
    }

    async _loadModels() {
        if (this.selector) return;
        try {
            this.selector = normalizeSelectorState(await this._request('/api/models/selector'));
            this.selectorError = this.selector.supported ? '' : 'Este Hermes no permite elegir modelo aquí.';
        } catch (error) {
            this.selectorError = 'No se pudo leer la lista de modelos; se usará el modelo del perfil.';
        }
        this._renderModelOptions();
    }

    // ── Actions ────────────────────────────────────────────────────────

    _openForm(target) {
        if (this.saving) return;
        const rule = target === 'new' ? null : this.state?.rules.find((row) => row.id === target);
        if (target !== 'new' && !rule) return;
        this.editing = target;
        this.draft = rule ? draftFromRule(rule) : emptyDraft();
        this.fieldErrors = {};
        this.notice = '';
        this.actionError = '';
        this._fillForm();
        this._showTab('rules', { focus: false });
        this.render();
        this.inputs.title.focus?.({ preventScroll: true });
        void this._loadModels();
    }

    _closeForm() {
        if (this.saving) return;
        this.editing = null;
        this.draft = null;
        this.fieldErrors = {};
        this.actionError = '';
        this.render();
        this.newBtn.focus?.({ preventScroll: true });
    }

    _fillForm() {
        const draft = this.draft;
        for (const [name, input] of Object.entries(this.inputs)) input.value = draft[name];
        for (const [id, { input }] of Object.entries(this.sourceInputs)) input.checked = draft.source === id;
        this.voiceInput.checked = draft.voice === true;
        this._renderModelOptions();
    }

    async save() {
        if (!this.draft || this.saving) return;
        this.fieldErrors = validateDraft(this.draft);
        if (Object.keys(this.fieldErrors).length) {
            this.notice = '';
            this.actionError = 'Revisa los campos marcados.';
            this.render();
            return;
        }
        const rule = this.editing === 'new' ? null : this.state?.rules.find((row) => row.id === this.editing);
        if (this.editing !== 'new' && !rule) {
            this.actionError = 'Esa regla ya no existe.';
            this.render();
            return;
        }
        this.saving = true;
        this.actionError = '';
        this.render();
        try {
            if (rule) {
                await this._request(`/api/automations/rules/${rule.id}`, {
                    method: 'PUT', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(updatePayload(rule, this.draft)),
                });
                this.notice = 'Cambios guardados. Una regla editada no repite el episodio en curso.';
            } else {
                await this._request('/api/automations/rules', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(createPayload(this.draft)),
                });
                this.notice = 'Guardada en pausa. Actívala cuando quieras que se ejecute.';
            }
            this.editing = null;
            this.draft = null;
        } catch (error) {
            this.actionError = error.message; // the draft stays as it was typed
        } finally {
            this.saving = false;
            this._reloadPending = false;
            await this.load(true);
        }
    }

    async setVoicePreference(key, value) {
        if (this.saving || !this.state?.presence) return;
        const revision = this.state.presence.revision;
        if (key === 'return_voice' && value && !await this.confirm('Iris podrá leer un resumen de las tareas que marques para voz, usando tu motor de voz configurado. Si ese motor usa un servicio externo, recibirá el resumen. No se pide otra respuesta a la IA. No interrumpe el chat ni abre el micrófono. ¿Activar las entregas por voz?', { title: 'Entregas por voz' })) return;
        if (this.saving) return;
        this.saving = true;
        this.actionError = '';
        this.render();
        try {
            await this._request('/api/automations/preferences', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ expected_revision: revision, [key]: value, confirm: key === 'return_voice' && value }) });
            this.notice = 'Preferencia de voz guardada. Las tareas conservan su estado.';
        } catch (error) { this.actionError = error.message; }
        finally { this.saving = false; await this.load(true); }
    }

    async setPresence(enabled) {
        if (this.saving || !this.state?.presence) return;
        const revision = this.state.presence.revision;
        if (enabled && !await this.confirm('Iris consultará cuándo hubo teclado o ratón en tu sesión de Windows, incluso minimizado. No lee lo que escribes ni usa la cámara. No mide si estás frente al PC. Las reglas solo se ejecutan si las activas y tienen permiso. ¿Activar la observación?', { title: 'Inactividad de Windows' })) return;
        if (this.saving) return;
        this.saving = true;
        this.actionError = '';
        this.render();
        try {
            await this._request('/api/automations/preferences', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ expected_revision: revision, desktop_idle: enabled, confirm: enabled }) });
            this.notice = enabled ? 'Observación de Windows activada. Empieza a contar desde ahora.' : 'Observación de Windows desactivada.';
        } catch (error) { this.actionError = error.message; }
        finally { this.saving = false; await this.load(true); }
    }

    async _setEnabled(rule, enabled) {
        if (this.saving) return;
        if (enabled && !await this.confirm(enableConfirmText(rule, this.state), {
            title: 'Activar automatización', icon: 'bolt', question: '¿Activarla?', confirmLabel: 'Activar', confirmIcon: 'bolt',
        })) return;
        this.saving = true;
        this.actionError = '';
        this.notice = '';
        this.render();
        try {
            const body = await this._request(`/api/automations/rules/${rule.id}`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(enabled
                    ? { expected_revision: rule.revision, enabled: true, confirm: true }
                    : { expected_revision: rule.revision, enabled: false }),
            });
            this.notice = enabled
                ? (body?.warning || 'Activada. Se ejecutará cuando se cumpla su condición.')
                : 'En pausa. No se ejecutará; lo que ya estaba en Hermes sigue su curso.';
        } catch (error) {
            this.actionError = error.message;
        } finally {
            this.saving = false;
            await this.load(true);
        }
    }

    async _cancel(occurrence) {
        if (this.saving) return;
        const view = stateView(occurrence.state);
        if (!await this.confirm(occurrence.runKnown
            ? `Se pedirá a Hermes detener esta tarea (${view.label.toLowerCase()}). Aparecerá detenida solo cuando Hermes lo confirme. ¿Continuar?`
            : '¿Cancelar esta ejecución? No se volverá a enviar a Hermes.', {
            title: occurrence.runKnown ? 'Detener tarea' : 'Cancelar ejecución', icon: 'warning', tone: 'danger',
            confirmLabel: occurrence.runKnown ? 'Detener' : 'Cancelar ejecución', cancelLabel: 'Volver',
        })) return;
        this.saving = true;
        this.actionError = '';
        this.notice = '';
        this.render();
        try {
            const body = await this._request(`/api/automations/occurrences/${occurrence.id}/cancel`, { method: 'POST' });
            this.notice = cancelText(body?.result);
        } catch (error) {
            this.actionError = error.message;
        } finally {
            this.saving = false;
            await this.load(true);
        }
    }

    /** Open the history on one rule (from the inbox's «Ver en Automatizaciones»). */
    showHistory(ruleId = '') {
        this.historyRule = typeof ruleId === 'string' ? ruleId : '';
        this._showTab('history', { focus: false });
    }

    _showTab(tab, { focus }) {
        this.tab = tab === 'history' ? 'history' : 'rules';
        this.render();
        if (focus) this.tabButtons[this.tab].focus?.({ preventScroll: true });
    }

    // ── Rendering ──────────────────────────────────────────────────────

    render() {
        if (!this.root) return;
        const state = this.state;
        this.counterValue.textContent = state ? String(state.rules.filter((r) => r.enabled).length).padStart(2, '0') : '—';
        this.status.textContent = this.saving ? 'Guardando…'
            : this.actionError ? this.actionError
                : this.error ? (state ? `${this.error} Se muestra lo último que respondió Core.` : this.error)
                    : this.loading && !state ? 'Cargando automatizaciones…'
                        : state?.storeProblem ? `El almacén de automatizaciones no está disponible: ${state.storeProblem}`
                            : this.notice || (state ? 'Las automatizaciones corren en Core, no en esta ventana: cerrarla no las detiene.' : '');
        this.status.dataset.tone = this.actionError || this.error || state?.storeProblem ? 'error' : this.notice ? 'ok' : 'info';
        for (const [id, button] of Object.entries(this.tabButtons)) {
            const selected = this.tab === id;
            button.setAttribute('aria-selected', String(selected));
            button.setAttribute('tabindex', selected ? '0' : '-1');
        }
        this.rulesPane.hidden = this.tab !== 'rules';
        this.historyPane.hidden = this.tab !== 'history';
        this.newBtn.disabled = this.saving || !state || Boolean(state.storeProblem) || this.editing !== null;
        this.refreshBtn.disabled = this.loading || this.saving;
        this._renderForm();
        this._renderRules();
        this._renderHistory();
        this._renderSide();
    }

    _renderForm() {
        this.form.hidden = this.editing === null;
        if (this.editing === null) return;
        const rule = this.editing === 'new' ? null : this.state?.rules.find((row) => row.id === this.editing);
        this.formTitle.textContent = rule ? `Editar «${rule.title}»` : 'Nueva regla';
        for (const [id, { input, label }] of Object.entries(this.sourceInputs)) {
            const ready = Boolean(this.state?.readySources.has(id));
            input.disabled = !ready || this.saving;
            label.dataset.disabled = String(!ready);
        }
        for (const input of Object.values(this.inputs)) input.disabled = this.saving;
        this.modelSelect.disabled = this.saving;
        this.voiceInput.disabled = this.saving || !this.state?.presence?.voiceSupported;
        this._renderFieldErrors();
        this._renderFormButtons();
    }

    _renderFieldErrors() {
        for (const [name, field] of Object.entries(this.fields)) {
            const message = this.fieldErrors[name] || '';
            field.error.textContent = message;
            const control = this.inputs[name] || (name === 'model' ? this.modelSelect : null);
            if (control) control.setAttribute('aria-invalid', String(Boolean(message)));
        }
    }

    _renderFormButtons() {
        this._renderThresholdLabel();
        const rule = this.editing && this.editing !== 'new' ? this.state?.rules.find((row) => row.id === this.editing) : null;
        this.saveBtn.textContent = rule ? 'Guardar cambios' : 'Guardar en pausa';
        this.saveBtn.disabled = this.saving || !this.draft || (rule && sameDraft(this.draft, draftFromRule(rule)));
        this.cancelFormBtn.disabled = this.saving;
    }

    _renderThresholdLabel() {
        const title = this.fields?.thresholdMinutes?.wrap?.children?.[0];
        if (title) title.textContent = this.draft?.source === 'desktop_idle' ? 'Minutos sin usar Windows' : 'Minutos sin usar Iris';
    }

    _renderModelOptions() {
        if (!this.draft) return;
        const value = this.draft.model;
        const options = [el('option', '', 'Modelo del perfil (el de Hermes)')];
        options[0].value = '';
        let found = value === '';
        for (const group of modelChoices(this.selector)) {
            const optgroup = el('optgroup');
            optgroup.label = group.label;
            for (const model of group.models) {
                const option = el('option', '', model.label);
                option.value = model.value;
                if (model.value === value) found = true;
                optgroup.append(option);
            }
            options.push(optgroup);
        }
        if (!found) { // a model the rule names that the inventory no longer lists stays visible
            const chosen = parseSelectionValue(value);
            const option = el('option', '', chosen ? `${chosen.model} (${chosen.provider}) · no disponible ahora` : 'Modelo guardado');
            option.value = value;
            options.push(option);
        }
        this.modelSelect.replaceChildren(...options);
        this.modelSelect.value = value;
        this.fields.model.error.textContent = this.fieldErrors.model || this.selectorError;
    }

    _renderRules() {
        const state = this.state;
        const focused = focusedKey(this._ruleControls);
        this._ruleControls = new Map();
        if (!state) { this.rulesList.replaceChildren(); return; }
        if (!state.rules.length) {
            const empty = el('li', 'automation-empty');
            empty.append(el('strong', '', 'Aún no hay reglas'),
                el('p', '', 'Crea una: se guarda en pausa y solo se ejecuta cuando la actives y Capability Center lo permita.'));
            this.rulesList.replaceChildren(empty);
            return;
        }
        this.rulesList.replaceChildren(...state.rules.map((rule) => {
            const view = ruleView(rule, state);
            const card = el('li', 'automation-rule');
            card.dataset.ruleId = rule.id;
            const head = el('div', 'automation-rule-head');
            head.append(el('strong', '', rule.title), pill(view.status));
            const meta = el('p', 'automation-rule-meta', `${view.condition} · ${view.model} · máx. ${rule.maxRunsPerDay}/día`);
            const prompt = el('p', 'automation-rule-prompt', rule.prompt);
            const last = el('p', 'automation-rule-last', view.last
                ? `Última vez: ${stateView(view.last.state).label} · ${when(view.last.updatedAt)}`
                : 'Todavía no se ha ejecutado.');
            const help = el('small', 'automation-help', view.status.help);
            const actions = el('div', 'automation-rule-actions');
            const busy = this.saving || this.editing !== null;
            const toggle = this._button(rule.enabled ? 'Pausar' : 'Activar', () => void this._setEnabled(rule, !rule.enabled),
                rule.enabled ? '' : 'automation-primary');
            toggle.disabled = busy || (!rule.enabled && Boolean(rule.problem));
            const edit = this._button('Editar', () => this._openForm(rule.id));
            edit.disabled = busy;
            const history = this._button('Historial', () => {
                this.historyRule = rule.id;
                this._showTab('history', { focus: false });
            });
            actions.append(toggle, edit, history);
            this._ruleControls.set(`${rule.id}:toggle`, toggle);
            this._ruleControls.set(`${rule.id}:edit`, edit);
            this._ruleControls.set(`${rule.id}:history`, history);
            card.append(head, meta, prompt, last, help, actions);
            return card;
        }));
        this._ruleControls.get(focused)?.focus?.({ preventScroll: true });
    }

    _renderHistory() {
        const state = this.state;
        const options = [el('option', '', 'Todas')];
        options[0].value = '';
        for (const rule of state?.rules || []) {
            const option = el('option', '', rule.title);
            option.value = rule.id;
            options.push(option);
        }
        this.historyFilter.replaceChildren(...options);
        if (this.historyRule && !state?.rules.some((rule) => rule.id === this.historyRule)) this.historyRule = '';
        this.historyFilter.value = this.historyRule;
        const focused = focusedKey(this._historyControls);
        this._historyControls = new Map();
        if (!state) { this.historyList.replaceChildren(); return; }
        const titles = new Map(state.rules.map((rule) => [rule.id, rule.title]));
        const rows = state.occurrences.filter((o) => !this.historyRule || o.ruleId === this.historyRule);
        if (!rows.length) {
            const empty = el('li', 'automation-empty');
            empty.append(el('strong', '', 'Sin ejecuciones todavía'),
                el('p', '', 'Cuando una regla activa se cumpla, aparecerá aquí con su estado real y su resultado.'));
            this.historyList.replaceChildren(empty);
            return;
        }
        const open = new Set([...this.historyList.querySelectorAll('details')]
            .filter((node) => node.open).map((node) => node.dataset.occurrenceId));
        this.historyList.replaceChildren(...rows.map((occurrence) => {
            const view = stateView(occurrence.state);
            const item = el('li', 'automation-run');
            item.dataset.occurrenceId = occurrence.id;
            const head = el('div', 'automation-run-head');
            head.append(el('strong', '', titles.get(occurrence.ruleId) || 'Regla eliminada'), pill(view));
            item.append(head, el('p', 'automation-run-meta', `${when(occurrence.createdAt)} · ${view.help}`));
            if (occurrence.result || occurrence.error) {
                const details = el('details', 'automation-run-detail');
                details.dataset.occurrenceId = occurrence.id;
                details.open = open.has(occurrence.id);
                const summary = el('summary', '', occurrence.result
                    ? (occurrence.result.status === 'completed' ? 'Ver resultado' : 'Ver qué pasó')
                    : 'Ver detalle');
                this._historyControls.set(`${occurrence.id}:detail`, summary);
                details.append(summary);
                details.append(el('p', 'automation-run-text', occurrence.result?.text || occurrence.error));
                if (occurrence.result) {
                    details.append(el('small', 'automation-help', `Terminó: ${when(occurrence.result.finishedAt)}`));
                }
                item.append(details);
            }
            if (CANCELLABLE_STATES.has(occurrence.state)) {
                const cancel = this._button(occurrence.runKnown ? 'Pedir que se detenga' : 'Cancelar',
                    () => void this._cancel(occurrence));
                cancel.disabled = this.saving;
                this._historyControls.set(`${occurrence.id}:cancel`, cancel);
                item.append(cancel);
            }
            return item;
        }));
        this._historyControls.get(focused)?.focus?.({ preventScroll: true });
    }

    _renderSide() {
        const focusedPreference = focusedKey(this._preferenceButtons || new Map());
        const state = this.state;
        const permission = permissionView(state?.permission);
        const perm = el('section', 'automation-card');
        perm.dataset.tone = permission.tone;
        perm.append(el('span', 'automation-kicker', 'PERMISO'), el('h3', '', `Automatizaciones · ${permission.label}`),
            el('p', '', permission.text));
        if (this.onOpenApp) {
            perm.append(this._button('Abrir Capability Center', () => this.onOpenApp('permissions')));
        }
        perm.append(el('small', 'automation-help', 'Esta ventana nunca cambia el permiso por ti.'));

        const sources = el('section', 'automation-card');
        sources.append(el('span', 'automation-kicker', 'CUÁNDO PUEDE CORRER'));
        for (const id of Object.keys(SOURCES)) {
            const view = state ? sourceView(id, state) : { label: SOURCES[id].label, now: '', ready: false, help: SOURCES[id].help };
            const row = el('div', 'automation-source-state');
            row.dataset.ready = String(view.ready);
            row.append(el('strong', '', view.label), el('small', '', view.ready ? view.now : view.help));
            sources.append(row);
        }

        const native = el('section', 'automation-card');
        const prefs = state?.presence;
        native.append(el('span', 'automation-kicker', 'INACTIVIDAD DE WINDOWS'),
            el('p', '', prefs?.supported ? 'Opcional. Solo tu sesión de Windows; no lee teclas ni abre sensores.' : 'Disponible con el puente nativo de Windows actualizado.'));
        const observe = this._button(prefs?.desktopIdle ? 'Desactivar observación de Windows' : 'Activar observación de Windows',
            () => void this.setPresence(!prefs?.desktopIdle));
        observe.disabled = this.saving || !prefs || (!prefs.supported && !prefs.desktopIdle);
        native.append(observe);

        const voice = el('section', 'automation-card');
        voice.append(el('span', 'automation-kicker', 'ENTREGAS POR VOZ'),
            el('p', '', 'Apagadas por defecto. Solo las tareas que marques para voz; el texto siempre queda en la bandeja.'),
            el('small', 'automation-help', 'Se omiten con el chat, el micrófono o Hey Hermes activos, sin foco, en silencio o en Focus de Iris. Un aviso incierto no se repite solo.'));
        const voiceToggle = this._button(prefs?.returnVoice ? 'Desactivar entregas por voz' : 'Activar entregas por voz',
            () => void this.setVoicePreference('return_voice', !prefs?.returnVoice));
        voiceToggle.disabled = this.saving || !prefs?.voiceSupported;
        const focusToggle = this._button(prefs?.focus ? 'Desactivar Focus de Iris' : 'Activar Focus de Iris',
            () => void this.setVoicePreference('focus', !prefs?.focus));
        focusToggle.disabled = this.saving || !prefs;
        voice.append(voiceToggle, focusToggle);
        voice.append(el('small', 'automation-help', 'Focus de Iris silencia estos resúmenes; las tareas y sus resultados siguen disponibles.'));

        const how = el('section', 'automation-card');
        how.append(el('span', 'automation-kicker', 'CÓMO FUNCIONA'));
        const list = el('ul', 'automation-how');
        for (const line of [
            'Una vez por episodio: si vuelves y te vas otra vez, cuenta de nuevo desde cero.',
            'Si vuelves mientras Hermes trabaja, la tarea sigue y su resultado queda aquí.',
            'Una sola tarea a la vez, con el plazo y el máximo diario de cada regla.',
            `Tiempo mínimo: ${minutesText(60)}; Iris debe estar abierto para contar.`,
        ]) list.append(el('li', '', line));
        how.append(list);
        this.side.replaceChildren(perm, native, voice, sources, how);
        this._preferenceButtons = new Map([['observe', observe], ['voice', voiceToggle], ['focus', focusToggle]]);
        this._preferenceButtons.get(focusedPreference)?.focus?.({ preventScroll: true });
    }
}

/**
 * F09.2: Automatizaciones as an app with its own module. The app host creates it the first time
 * its window opens and keeps it when the window closes or minimizes; the view refreshes only
 * while the host reports it visible. The rules and their runs live in Core, never here.
 */
export function createApp(context = {}) {
    let app = null;
    return {
        mount(root) {
            const host = (root.ownerDocument || document).createElement('div');
            host.className = 'automation-host';
            root.replaceChildren(host);
            app = new AutomationApp({
                root: host,
                isVisible: () => context.isVisible?.() === true,
                onSummary: (summary) => context.setBrief?.(summary),
                onOpenApp: typeof context.openApp === 'function' ? context.openApp : null,
            });
        },
        setVisibility() {
            app?.syncVisibility();
        },
        refresh({ force = false, opening = false } = {}) {
            return app?.load(opening || force);
        },
        /** `{ history: ruleId }` opens the history on that rule (the inbox's «Ver en Automatizaciones»). */
        navigate(payload = {}) {
            if (typeof payload?.history === 'string') app?.showHistory(payload.history);
        },
        dispose() {
            app?.stop();
            app?.root?.remove?.();
            app = null;
        },
    };
}
