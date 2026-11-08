/**
 * F01 model selector in the chat composer.
 *
 * A pill next to the reasoning control shows this conversation's model and
 * whether Hermes confirmed it; the dialog lets the user pick "Predeterminado"
 * or any model of Hermes' authenticated inventory, and, separately and with a
 * confirmation, make that model Hermes' default. Every text that comes from
 * Hermes is set with textContent.
 *
 * Writes carry the conversation they were made for (`context`); one that
 * finishes after the conversation changed is never chained to another one.
 * Keyboard focus survives re-renders: controls carry a stable focus key.
 */

import {
    defaultAction,
    normalizeSelectorState,
    optionGroups,
    parseSelectionValue,
    pillView,
    requestErrorText,
    statusView,
    turnNotice,
} from './model_selector_state.js?v=4';

const FILTER_THRESHOLD = 12;

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function focusKeyed(node, key) {
    node.dataset.focus = key;
    return node;
}

export class ModelSelector {
    constructor({ host, fetchImpl = (...args) => fetch(...args), onNotice = null } = {}) {
        this.host = host;
        this.fetch = fetchImpl;
        this.onNotice = onNotice;
        this.state = null;
        this.loading = false;
        this.writing = false;
        this.requestError = '';
        this.query = '';
        this.confirmingDefault = false;
        this._seq = 0; // every request that can replace the view, in start order
        this._readSeq = 0; // the newest read: only it ends `loading`
        this._latestRead = null;
        this._pendingFocus = null;
        this._initialFocus = false;
        this._uid = `model-selector-${Math.random().toString(36).slice(2, 8)}`;
        if (!host) return;
        this._build();
        this.render();
    }

    _build() {
        this.pill = el('button', 'model-pill');
        this.pill.type = 'button';
        this.pill.id = `${this._uid}-pill`;
        this.pill.setAttribute('aria-haspopup', 'dialog');
        this.pill.setAttribute('aria-expanded', 'false');
        this.pill.setAttribute('aria-controls', `${this._uid}-dialog`);
        this.pillDot = el('span', 'model-pill-dot');
        this.pillDot.setAttribute('aria-hidden', 'true');
        this.pillLabel = el('span', 'model-pill-label');
        this.pillDetail = el('span', 'model-pill-detail');
        this.pill.append(this.pillDot, this.pillLabel, this.pillDetail);
        this.pill.addEventListener('click', () => this.toggle());

        this.dialog = el('div', 'model-popover');
        this.dialog.id = `${this._uid}-dialog`;
        this.dialog.hidden = true;
        // Focusable itself, so focus stays inside while its controls reload.
        this.dialog.tabIndex = -1;
        this.dialog.setAttribute('role', 'dialog');
        this.dialog.setAttribute('aria-label', 'Modelo de esta conversación');
        this.dialog.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.stopPropagation();
                this.close({ focusPill: true });
            }
        });

        const head = el('div', 'model-popover-head');
        head.append(el('strong', '', 'Modelo de esta conversación'));
        this.refreshBtn = focusKeyed(el('button', 'model-popover-refresh', 'Actualizar catálogo'), 'refresh');
        this.refreshBtn.type = 'button';
        this.refreshBtn.addEventListener('click', () => this.refresh({ refreshInventory: true }));
        head.append(this.refreshBtn);

        this.status = el('p', 'model-popover-status');
        this.status.setAttribute('role', 'status');
        this.status.setAttribute('aria-live', 'polite');

        this.filter = focusKeyed(el('input', 'model-popover-filter'), 'filter');
        this.filter.type = 'search';
        this.filter.placeholder = 'Buscar modelo o proveedor';
        this.filter.setAttribute('aria-label', 'Buscar modelo o proveedor');
        this.filter.addEventListener('input', () => {
            this.query = this.filter.value;
            this._renderOptions();
        });

        this.options = el('div', 'model-popover-options');
        this.options.setAttribute('role', 'radiogroup');
        this.options.setAttribute('aria-label', 'Modelos disponibles');
        this.options.addEventListener('change', (event) => {
            const input = event.target;
            if (input?.name === `${this._uid}-model`) this._choose(input.value);
        });

        this.defaultBox = el('div', 'model-popover-default');
        // Which models are listed is chosen in the "Modelos del selector" app:
        // one login can publish hundreds of them (qa9).
        this.configureBox = el('div', 'model-popover-configure');
        this.configureNote = el('span', 'model-popover-configure-note');
        this.configureBtn = focusKeyed(el('button', 'model-popover-configure-btn', 'Elegir modelos visibles'), 'configure');
        this.configureBtn.type = 'button';
        this.configureBtn.addEventListener('click', () => {
            this.close();
            window.dispatchEvent(new CustomEvent('IRIS:open-os-app', { detail: { app: 'models' } }));
        });
        this.configureBox.append(this.configureNote, this.configureBtn);
        this.dialog.append(head, this.status, this.filter, this.options, this.configureBox, this.defaultBox);

        this.host.prepend(this.pill);
        // The chat composer clips overflow and its section is transformed, so
        // the dialog lives on <body> and is placed next to the pill.
        document.body.append(this.dialog);
        this._onViewportChange = () => { if (this.isOpen) this._place(); };
        window.addEventListener('resize', this._onViewportChange);
        window.addEventListener('scroll', this._onViewportChange, true);
        document.addEventListener('pointerdown', (event) => {
            if (this.dialog.hidden) return;
            if (this.dialog.contains(event.target) || this.pill.contains(event.target)) return;
            this.close();
        });
    }

    get isOpen() {
        return Boolean(this.dialog && !this.dialog.hidden);
    }

    get busy() {
        return this.loading || this.writing;
    }

    /** Above the pill when there is room (the composer sits low), else below. */
    _place() {
        const pill = this.pill.getBoundingClientRect();
        const anchor = (this.host.closest('.chat-composer') || this.host).getBoundingClientRect();
        const margin = 8;
        const width = Math.min(380, Math.max(260, anchor.width), window.innerWidth - margin * 2);
        const left = Math.min(Math.max(anchor.left, margin), window.innerWidth - width - margin);
        const above = pill.top - margin * 2;
        const below = window.innerHeight - pill.bottom - margin * 2;
        const style = this.dialog.style;
        style.left = `${left}px`;
        style.width = `${width}px`;
        if (above >= 240 || above >= below) {
            style.top = '';
            style.bottom = `${window.innerHeight - pill.top + 6}px`;
            style.maxHeight = `${Math.min(460, above)}px`;
        } else {
            style.bottom = '';
            style.top = `${pill.bottom + 6}px`;
            style.maxHeight = `${Math.min(460, below)}px`;
        }
    }

    toggle() {
        if (this.isOpen) this.close({ focusPill: true });
        else this.open();
    }

    open() {
        if (!this.dialog) return;
        this.dialog.hidden = false;
        this.pill.setAttribute('aria-expanded', 'true');
        this.confirmingDefault = false;
        this._pendingFocus = null;
        this._initialFocus = true;
        this._place();
        // Inside the dialog at once; the best option gets focus once loaded.
        this.dialog.focus();
        this.render();
        this.refresh();
    }

    close({ focusPill = false } = {}) {
        if (!this.dialog) return;
        this.dialog.hidden = true;
        this.pill.setAttribute('aria-expanded', 'false');
        this.confirmingDefault = false;
        this._pendingFocus = null;
        this._initialFocus = false;
        if (focusPill) this.pill.focus();
    }

    // ── focus ────────────────────────────────────────────────────────────

    _focusKeyOf(node) {
        if (!node || node === this.dialog || !this.dialog.contains(node)) return null;
        if (node.name === `${this._uid}-model`) return `radio:${node.value}`;
        return node.dataset?.focus ? `key:${node.dataset.focus}` : null;
    }

    _findFocus(key) {
        if (key.startsWith('radio:')) {
            const value = key.slice('radio:'.length);
            return [...this.options.querySelectorAll('input')].find((input) => input.value === value) || null;
        }
        const wanted = key.slice('key:'.length);
        return [this.refreshBtn, this.filter, this.configureBtn, ...this.defaultBox.querySelectorAll('button')]
            .find((node) => node.dataset.focus === wanted) || null;
    }

    _bestFocus() {
        const inputs = [...this.options.querySelectorAll('input')];
        return inputs.find((input) => input.checked && !input.disabled)
            || inputs.find((input) => !input.disabled)
            || (!this.refreshBtn.disabled ? this.refreshBtn : null);
    }

    /** Re-render, keeping keyboard focus on the same logical control. */
    _withFocus(renderFn) {
        const active = document.activeElement;
        const inDialog = this.isOpen && (active === this.dialog || this.dialog.contains(active));
        const key = inDialog ? (this._focusKeyOf(active) || this._pendingFocus) : null;
        renderFn();
        if (!this.isOpen || !inDialog) return;
        if (key) {
            const target = this._findFocus(key);
            if (target && !target.disabled && !target.hidden) {
                target.focus();
                this._pendingFocus = null;
                return;
            }
            if (target) {
                // Disabled while a request runs: hold focus on the dialog and
                // return to the same control when it comes back.
                this._pendingFocus = key;
                this.dialog.focus();
                return;
            }
            // The control is gone (another conversation, a closed confirmation).
            this._pendingFocus = null;
            (this._bestFocus() || this.dialog).focus();
            return;
        }
        if (this._initialFocus && !this.busy && this.state) {
            this._initialFocus = false;
            (this._bestFocus() || this.dialog).focus();
        }
    }

    // ── requests ─────────────────────────────────────────────────────────

    async _request(url, init) {
        const response = await this.fetch(url, init);
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(requestErrorText(body));
        return body;
    }

    /** Read the state again; `keepError` keeps the message of a write that just failed. */
    refresh({ refreshInventory = false, keepError = false } = {}) {
        if (!this.host) return Promise.resolve();
        const seq = ++this._seq;
        const read = ++this._readSeq;
        this.loading = true;
        this.render();
        this._latestRead = (async () => {
            try {
                const body = await this._request(`/api/models/selector${refreshInventory ? '?refresh=true' : ''}`);
                if (seq !== this._seq) return;
                this.state = normalizeSelectorState(body);
                if (!keepError) this.requestError = '';
            } catch (error) {
                if (seq !== this._seq || keepError) return;
                this.requestError = `No pude leer el modelo: ${error.message || error}`;
            } finally {
                // The newest read ends `loading` even when a write replaced its view.
                if (read === this._readSeq) {
                    this.loading = false;
                    this.render();
                }
            }
        })();
        return this._latestRead;
    }

    /** Until no read is pending (one may start while waiting for another). */
    async _settled() {
        for (let guard = 0; this.loading && this._latestRead && guard < 10; guard += 1) {
            await this._latestRead;
        }
    }

    /**
     * One write at a time; `{ok, stale, body}` (stale: a newer request replaced the view).
     * A refused write is followed by a read of the active conversation: the
     * view it was made from may be another conversation's by now.
     */
    async _put(url, payload) {
        if (this.writing) return { ok: false, stale: false, body: null };
        const seq = ++this._seq;
        this.writing = true;
        this.render();
        let result = { ok: false, stale: false, body: null };
        try {
            const body = await this._request(url, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            result = { ok: true, stale: seq !== this._seq, body };
            if (!result.stale) {
                this.state = normalizeSelectorState(body);
                this.requestError = '';
            }
        } catch (error) {
            result = { ok: false, stale: seq !== this._seq, body: null };
            this.requestError = error.message || 'No pude cambiar el modelo.';
        } finally {
            this.writing = false;
            this.render();
        }
        if (!result.ok) this.refresh({ keepError: true });
        return result;
    }

    _choose(value, { context = this.state?.conversation.context || '', sessionId = this.state?.conversation.sessionId || '' } = {}) {
        if (this.writing) return Promise.resolve({ ok: false, stale: false, body: null });
        if (value === 'default') {
            return this._put('/api/models/conversation', { mode: 'default', session_id: sessionId, context });
        }
        const { provider, model } = parseSelectionValue(value);
        return this._put('/api/models/conversation', { mode: 'custom', provider, model, session_id: sessionId, context });
    }

    async _makeDefault(action) {
        if (this.writing || !this.state) return;
        const context = this.state.conversation.context;
        const sessionId = this.state.conversation.sessionId;
        const changed = await this._put('/api/models/default', { provider: action.provider, model: action.model, context });
        this.confirmingDefault = false;
        this.render();
        if (!changed.ok) return;
        // The conversation's own model is now the default: let THAT
        // conversation follow the default, only if it is still the active one,
        // also after the reads Core's events started meanwhile have settled.
        if (changed.body?.conversation?.context !== context) return;
        await this._settled();
        if (this.state?.conversation.context !== context) return;
        await this._choose('default', { context, sessionId });
        this._findFocus('key:refresh')?.focus?.();
    }

    /** WebSocket events from app.js. */
    handleEvent(type, payload = {}) {
        if (!this.host) return;
        if (type === 'model_turn') {
            const notice = turnNotice(payload);
            if (notice) this.onNotice?.(notice);
        }
        if (['model_turn', 'model_selection_changed', 'chat_history_replace', 'hello'].includes(type)) {
            this.refresh();
        }
    }

    // ── rendering ────────────────────────────────────────────────────────

    render() {
        if (!this.pill) return;
        const pill = pillView(this.state, { loading: this.loading && !this.state, requestError: this.requestError });
        this.pill.dataset.tone = pill.tone;
        this.pillLabel.textContent = pill.label;
        this.pillDetail.textContent = pill.detail;
        this.pill.title = pill.title;
        this.pill.setAttribute('aria-label', `Modelo: ${pill.label}, ${pill.detail}. ${pill.title}`);
        if (!this.isOpen) return;
        this._withFocus(() => {
            const status = statusView(this.state, { loading: this.busy, requestError: this.requestError });
            this.status.dataset.tone = status.tone;
            this.status.textContent = status.text;
            this.refreshBtn.disabled = this.busy || !this.state?.supported;
            this._renderOptions();
            this._renderDefault();
        });
    }

    _renderOptions() {
        const view = optionGroups(this.state, this.query);
        this.filter.hidden = view.total - view.hidden <= FILTER_THRESHOLD;
        this.options.replaceChildren();
        this.configureBox.hidden = !this.state?.supported;
        this.configureNote.textContent = view.hidden
            ? `${view.hidden} ${view.hidden === 1 ? 'modelo oculto' : 'modelos ocultos'}`
            : (this.state?.visible ? 'Todos tus modelos elegidos' : 'Se muestran todos los modelos');
        if (!this.state) return;
        if (view.defaultOption) {
            this.options.append(this._radio(view.defaultOption.value, view.defaultOption.label,
                view.defaultOption.detail, view.defaultOption.checked, view.defaultOption.disabled || this.busy));
        }
        if (this.state.inventoryError) {
            this.options.append(el('p', 'model-popover-empty', `No pude leer el catálogo de modelos: ${this.state.inventoryError}`));
        }
        for (const group of view.groups) {
            const section = el('div', 'model-group');
            section.dataset.selectable = String(group.selectable);
            const title = el('div', 'model-group-title', group.label);
            if (group.current) title.append(el('span', 'model-group-badge', 'actual'));
            section.append(title);
            if (group.warning) section.append(el('p', 'model-group-warning', group.warning));
            for (const option of group.models) {
                section.append(this._radio(option.value, option.model, option.isDefault ? 'predeterminado' : '',
                    option.checked, option.disabled || this.busy));
            }
            this.options.append(section);
        }
        if (this.state.supported && !view.groups.length && !this.state.inventoryError) {
            this.options.append(el('p', 'model-popover-empty',
                this.query ? 'Ningún modelo coincide con la búsqueda.' : 'No hay modelos disponibles con una sesión o clave guardada.'));
        }
    }

    _radio(value, label, detail, checked, disabled) {
        const row = el('label', 'model-option');
        const input = el('input');
        input.type = 'radio';
        input.name = `${this._uid}-model`;
        input.value = value;
        input.checked = checked;
        input.disabled = disabled;
        row.append(input, el('span', 'model-option-label', label));
        if (detail) row.append(el('span', 'model-option-detail', detail));
        return row;
    }

    _button(className, text, key, onClick) {
        const button = focusKeyed(el('button', className, text), key);
        button.type = 'button';
        button.disabled = this.busy;
        button.addEventListener('click', onClick);
        return button;
    }

    _renderDefault() {
        this.defaultBox.replaceChildren();
        if (!this.state?.supported) return;
        const current = this.state.default.model || 'sin modelo en la configuración';
        this.defaultBox.append(el('p', 'model-default-line', `Predeterminado de IRIS: ${current}`));
        if (this.state.warning) this.defaultBox.append(el('p', 'model-group-warning', this.state.warning));
        if (!this.state.defaultWritable && this.state.defaultReason) {
            this.defaultBox.append(el('p', 'model-default-line', this.state.defaultReason));
        }
        const action = defaultAction(this.state);
        if (!action) {
            this.confirmingDefault = false;
            return;
        }
        if (!this.confirmingDefault) {
            this.defaultBox.append(this._button('model-default-btn', action.label, 'default-action', () => {
                this.confirmingDefault = true;
                this._renderDefault();
                // Safe default: a stray Enter cancels instead of changing Hermes' global model.
                this._findFocus('key:default-cancel')?.focus();
            }));
            return;
        }
        this.defaultBox.append(el('p', 'model-default-warning', action.confirm));
        const row = el('div', 'model-default-actions');
        row.append(
            this._button('model-default-btn model-default-confirm', 'Cambiar predeterminado', 'default-confirm',
                () => this._makeDefault(action)),
            this._button('model-default-btn model-default-cancel', 'Cancelar', 'default-cancel', () => {
                this.confirmingDefault = false;
                this._renderDefault();
                this._findFocus('key:default-action')?.focus();
            }),
        );
        this.defaultBox.append(row);
    }
}
