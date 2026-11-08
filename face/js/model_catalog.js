/**
 * "Modelos del selector": the IRIS OS app where the user picks which models
 * the chat's model selector lists (qa9). Reads the same document as the
 * selector (`/api/models/selector`) and saves with `PUT /api/models/visible`.
 * Every text that comes from Hermes is set with textContent.
 *
 * Reads and writes are ordered (Codex qa10 review, R4): while a save runs no
 * control can change the draft, a reload asked for meanwhile waits for it, and
 * a read that started before a save is discarded when it answers. Keyboard
 * focus survives re-renders through a stable key per control.
 */

import { normalizeSelectorState } from './model_selector_state.js?v=4';
import {
    catalogView,
    draftFromState,
    draftPayload,
    sameDraft,
    toggleModel,
    toggleProvider,
} from './model_catalog_state.js?v=2';

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function button(text, action, className = '') {
    const node = el('button', className, text);
    node.type = 'button';
    node.dataset.action = action;
    return node;
}

export class ModelCatalogApp {
    constructor({ root = null, fetchImpl = globalThis.fetch?.bind(globalThis) } = {}) {
        this.root = root;
        this.fetch = fetchImpl;
        this.state = null;
        this.saved = null;
        this.draft = null;
        this.query = '';
        this.loading = false;
        this.saving = false;
        this.error = '';
        this.notice = '';
        this._generation = 0; // every read and write, in start order
        this._reloadAfterSave = null;
        this._pendingFocus = null;
        this._countNodes = new Map();
        this._open = new Set(); // providers whose list the user unfolded or folded
        this._folded = new Set();
        if (!root) return;
        this._build();
        this.render();
    }

    get dirty() {
        return !sameDraft(this.draft, this.saved);
    }

    _build() {
        this.surface = el('section', 'model-catalog');
        // Focusable itself, so focus stays inside while its controls are rebuilt.
        this.surface.tabIndex = -1;
        const head = el('header', 'model-catalog-head');
        const intro = el('div', 'model-catalog-intro');
        intro.append(
            el('span', 'model-catalog-kicker', 'Selector del chat'),
            el('strong', '', 'Modelos del selector'),
            el('p', '', 'Elige qué modelos aparecen al cambiar el modelo de una conversación. '
                + 'Ocultar un modelo no lo elimina del proveedor, y el que use una conversación sigue visible.'),
        );
        this.summary = el('div', 'model-catalog-summary');
        this.summaryCount = el('strong', '', '–');
        this.summaryText = el('span', '', 'Cargando…');
        this.summary.append(this.summaryCount, this.summaryText);
        head.append(intro, this.summary);

        const toolbar = el('div', 'model-catalog-toolbar');
        this.search = el('input', 'model-catalog-search');
        this.search.type = 'search';
        this.search.placeholder = 'Buscar modelo o proveedor';
        this.search.setAttribute('aria-label', 'Buscar modelo o proveedor');
        this.search.dataset.focus = 'search';
        this.refreshBtn = button('Actualizar catálogo', 'refresh');
        this.allBtn = button('Mostrar todos', 'all');
        this.noneBtn = button('Ocultar todos', 'none');
        toolbar.append(this.search, this.refreshBtn, this.allBtn, this.noneBtn);

        this.status = el('p', 'model-catalog-status');
        this.status.setAttribute('role', 'status');
        this.status.setAttribute('aria-live', 'polite');
        this.groups = el('div', 'model-catalog-groups');

        const footer = el('footer', 'model-catalog-actions');
        this.dirtyNote = el('span', 'model-catalog-dirty', '');
        this.discardBtn = button('Descartar', 'discard');
        this.saveBtn = button('Guardar', 'save', 'model-catalog-save');
        footer.append(this.dirtyNote, this.discardBtn, this.saveBtn);

        this.surface.append(head, toolbar, this.status, this.groups, footer);
        this.root.replaceChildren(this.surface);

        this.search.addEventListener('input', () => {
            this.query = this.search.value;
            this._withFocus(() => this._renderGroups());
        });
        this.surface.addEventListener('click', (event) => {
            const target = event.target?.closest?.('button[data-action]');
            if (!target || target.disabled) return;
            const { action, provider } = target.dataset;
            if (action === 'refresh') void this.load(true, { refreshInventory: true });
            else if (action === 'save') void this.save();
            else if (this.saving) return; // nothing may change what is being saved
            else if (action === 'all') this._setDraft(null);
            else if (action === 'none') this._setDraft(new Set());
            else if (action === 'provider-all') this._setDraft(toggleProvider(this.draft, this.state, provider, true));
            else if (action === 'provider-none') this._setDraft(toggleProvider(this.draft, this.state, provider, false));
            else if (action === 'discard') this._setDraft(this.saved);
        });
        this.groups.addEventListener('change', (event) => {
            const input = event.target;
            if (input?.type !== 'checkbox' || !input.dataset.value) return;
            if (this.saving) {
                input.checked = !input.checked; // undo: the draft is being saved
                return;
            }
            this.draft = toggleModel(this.draft, this.state, input.dataset.value, input.checked);
            this.notice = '';
            this._renderCounts();
        });
        this.groups.addEventListener('toggle', (event) => {
            const details = event.target;
            const id = details?.dataset?.provider;
            if (!id) return;
            if (details.open) { this._open.add(id); this._folded.delete(id); }
            else { this._folded.add(id); this._open.delete(id); }
        }, true);
    }

    async _request(url, init) {
        if (!this.fetch) throw new Error('La red local de IRIS no está disponible.');
        const response = await this.fetch(url, { cache: 'no-store', ...init });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = body?.detail;
            throw new Error((detail && typeof detail === 'object' ? detail.message : detail) || `HTTP ${response.status}`);
        }
        return body;
    }

    /** Read the inventory and the saved choice; unsaved edits are kept. */
    async load(force = false, { refreshInventory = false } = {}) {
        if (!this.root || (this.state && !force)) return;
        if (this.saving) {
            // Read after the save, or this read could return the choice before it.
            this._reloadAfterSave = { refreshInventory: refreshInventory || this._reloadAfterSave?.refreshInventory };
            return;
        }
        if (this.loading) return;
        const generation = ++this._generation;
        this.loading = true;
        this.error = '';
        this.render();
        try {
            const body = await this._request(`/api/models/selector${refreshInventory ? '?refresh=true' : ''}`);
            if (generation !== this._generation) return; // a save started meanwhile: its result wins
            const keepDraft = this.state && this.dirty;
            this.state = normalizeSelectorState(body);
            this.saved = draftFromState(this.state);
            if (!keepDraft) this.draft = this.saved;
        } catch (error) {
            if (generation === this._generation) this.error = `No pude leer el catálogo: ${error.message || error}`;
        } finally {
            this.loading = false;
            this.render();
        }
    }

    async save() {
        if (this.saving || this.loading || !this.dirty) return;
        const generation = ++this._generation;
        const sent = this.draft;
        this.saving = true;
        this.error = '';
        this.render();
        try {
            const body = await this._request('/api/models/visible', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ models: draftPayload(sent) }),
            });
            if (generation === this._generation) {
                this.saved = Array.isArray(body.visible)
                    ? normalizeSelectorState({ visible: body.visible }).visible
                    : null;
                // Controls are disabled while saving; should anything have changed
                // the draft anyway, that newer edit is kept as unsaved.
                if (this.draft === sent) this.draft = this.saved;
                if (this.state) this.state.visible = this.saved;
                this.notice = this.saved === null
                    ? 'Guardado: el selector muestra todos los modelos.'
                    : 'Guardado: el selector del chat ya muestra tu selección.';
            }
        } catch (error) {
            this.error = `No se guardó la selección: ${error.message || error}`;
        } finally {
            this.saving = false;
            this.render();
            const reload = this._reloadAfterSave;
            this._reloadAfterSave = null;
            if (reload) void this.load(true, reload);
        }
    }

    _setDraft(draft) {
        this.draft = draft === null ? null : new Set(draft);
        this.notice = '';
        this._withFocus(() => {
            this._renderGroups();
            this._renderCounts();
        });
    }

    // ── focus ────────────────────────────────────────────────────────────

    _focusKeyOf(node) {
        if (!node || node === this.surface || !this.surface.contains(node)) return null;
        if (node.dataset?.value) return `model:${node.dataset.value}`;
        if (node.dataset?.action) return `action:${node.dataset.action}:${node.dataset.provider || ''}`;
        return node.dataset?.focus ? `key:${node.dataset.focus}` : null;
    }

    _findFocus(key) {
        const [kind, ...rest] = key.split(':');
        const wanted = rest.join(':');
        const candidates = [this.search, this.refreshBtn, this.allBtn, this.noneBtn, this.discardBtn, this.saveBtn,
            ...this.groups.querySelectorAll('button'), ...this.groups.querySelectorAll('input')];
        return candidates.find((node) => {
            if (kind === 'model') return node.dataset?.value === wanted;
            if (kind === 'action') return `${node.dataset?.action}:${node.dataset?.provider || ''}` === wanted;
            return node.dataset?.focus === wanted;
        }) || null;
    }

    /** Re-render, keeping keyboard focus on the same logical control. */
    _withFocus(renderFn) {
        const active = document.activeElement;
        const inside = active === this.surface || this.surface.contains(active);
        const key = inside ? (this._focusKeyOf(active) || this._pendingFocus) : null;
        renderFn();
        if (!inside || !key) return;
        const target = this._findFocus(key);
        if (target && !target.disabled) {
            target.focus();
            this._pendingFocus = null;
        } else {
            // Disabled while a request runs, or gone with a search: keep focus in the app
            // and return to the same control when it comes back.
            this._pendingFocus = target ? key : null;
            this.surface.focus();
        }
    }

    // ── rendering ────────────────────────────────────────────────────────

    render() {
        if (!this.surface) return;
        this._withFocus(() => {
            const busy = this.loading || this.saving;
            this.search.disabled = !this.state;
            this.refreshBtn.disabled = busy;
            this.allBtn.disabled = busy || !this.state;
            this.noneBtn.disabled = busy || !this.state;
            this._renderGroups();
            this._renderCounts();
        });
    }

    _statusText() {
        if (this.error) return { tone: 'error', text: this.error };
        if (this.loading) return { tone: 'loading', text: 'Leyendo el catálogo de modelos…' };
        if (this.saving) return { tone: 'loading', text: 'Guardando…' };
        if (this.notice) return { tone: 'ok', text: this.notice };
        if (this.state && !this.state.supported) {
            return { tone: 'error', text: 'El servicio de IA está detenido: pulsa «Iniciar IRIS» para consultar sus modelos.' };
        }
        if (this.state?.inventoryError) return { tone: 'error', text: `No se recibió el catálogo de modelos: ${this.state.inventoryError}` };
        return { tone: '', text: '' };
    }

    _renderCounts() {
        if (!this.surface) return;
        const view = catalogView(this.state, this.draft, this.query);
        this.summaryCount.textContent = this.state ? String(view.listed) : '–';
        this.summaryText.textContent = this.state
            ? (view.showsAll ? `de ${view.total} · se muestran todos` : `de ${view.total} visibles`)
            : 'Cargando…';
        for (const group of view.groups) {
            const counter = this._countNodes.get(group.id);
            if (counter) counter.textContent = `${group.listed} de ${group.total}`;
        }
        const status = this._statusText();
        this.status.dataset.tone = status.tone;
        this.status.textContent = status.text;
        this.status.hidden = !status.text;
        const busy = this.loading || this.saving;
        this.dirtyNote.textContent = this.dirty ? 'Cambios sin guardar' : '';
        this.saveBtn.disabled = busy || !this.dirty;
        this.discardBtn.disabled = busy || !this.dirty;
    }

    _renderGroups() {
        if (!this.groups) return;
        const view = catalogView(this.state, this.draft, this.query);
        this.groups.replaceChildren();
        this._countNodes.clear();
        if (!this.state) return;
        if (!view.groups.length) {
            this.groups.append(el('p', 'model-catalog-empty', this.query
                ? 'Ningún modelo coincide con la búsqueda.'
                : 'No se recibieron modelos. Inicia sesión con un proveedor en Configuración.'));
            return;
        }
        for (const group of view.groups) {
            const details = el('details', 'model-catalog-group');
            details.dataset.provider = group.id;
            details.dataset.authenticated = String(group.authenticated);
            // Open what can be used; a search opens every match.
            details.open = Boolean(this.query) || this._open.has(group.id)
                || (group.authenticated && !this._folded.has(group.id));
            const summary = el('summary', 'model-catalog-group-head');
            summary.append(el('strong', '', group.label));
            const count = el('span', 'model-catalog-group-count', `${group.listed} de ${group.total}`);
            this._countNodes.set(group.id, count);
            summary.append(count);
            details.append(summary);
            if (group.warning) details.append(el('p', 'model-catalog-warning', group.warning));
            const bulk = el('div', 'model-catalog-group-actions');
            const all = button('Mostrar todos', 'provider-all');
            const none = button('Ocultar todos', 'provider-none');
            for (const control of [all, none]) {
                control.dataset.provider = group.id;
                control.disabled = this.saving;
            }
            bulk.append(all, none);
            details.append(bulk);
            const list = el('div', 'model-catalog-models');
            for (const option of group.models) {
                const row = el('label', 'model-catalog-model');
                const input = el('input');
                input.type = 'checkbox';
                input.checked = option.checked;
                input.dataset.value = option.value;
                input.disabled = this.saving;
                row.append(input, el('span', 'model-catalog-model-name', option.model));
                if (option.current) row.append(el('em', '', 'en uso'));
                else if (option.isDefault) row.append(el('em', '', 'predeterminado'));
                list.append(row);
            }
            details.append(list);
            this.groups.append(details);
        }
    }
}
