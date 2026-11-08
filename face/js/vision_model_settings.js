/**
 * F02 "Visión": the Modelos app tab where the user chooses the model that
 * looks at images (Hermes' `auxiliary.vision` of the active profile). Reads
 * `GET /api/vision-model` and saves with `PUT /api/vision-model`.
 *
 * Opening the tab sends no image and changes no conversation model (F01).
 * Reads and writes are ordered like "Modelos del selector": a save locks the
 * draft, a reload asked for meanwhile waits for it, and an answer for an older
 * request is discarded. Every text from Hermes is set with textContent.
 */

import {
    choiceGroups,
    draftFromState,
    lastSummary,
    normalizeVisionState,
    payloadFor,
    routeSummary,
    sameDraft,
} from './vision_model_state.js?v=1';

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function errorText(body, fallback) {
    const detail = body?.detail;
    if (typeof detail === 'string' && detail) return detail;
    if (detail && typeof detail.message === 'string' && detail.message) return detail.message;
    return fallback;
}

export class VisionModelApp {
    constructor({ root = null, fetchImpl = globalThis.fetch?.bind(globalThis) } = {}) {
        this.root = root;
        this.fetch = fetchImpl;
        this.state = null;
        this.saved = null;
        this.draft = null;
        this.loading = false;
        this.saving = false;
        this.error = '';
        this.notice = '';
        this._generation = 0;
        this._reloadAfterSave = null;
        if (!root) return;
        this._build();
        this.render();
    }

    get dirty() {
        return Boolean(this.draft && this.saved && !sameDraft(this.draft, this.saved));
    }

    _build() {
        this.surface = el('section', 'vision-model');
        const head = el('header', 'vision-model-head');
        const intro = el('div', 'vision-model-intro');
        intro.append(
            el('span', 'vision-model-kicker', 'Modelo auxiliar'),
            el('strong', '', 'Modelo de visión'),
            el('p', '', 'Elige qué modelo mira las imágenes: las que adjuntas al chat y las fotos que le pidas a Iris. '
                + 'No cambia el modelo de tus conversaciones.'),
        );
        this.profileChip = el('span', 'vision-model-profile', 'Perfil de Hermes: …');
        head.append(intro, this.profileChip);

        this.modes = el('fieldset', 'vision-model-modes');
        this.modes.append(el('legend', '', 'Qué modelo usar'));
        this.autoRadio = this._radio('auto', 'Automático',
            'Hermes decide: el modelo del chat si lee imágenes; si no, su ruta de visión automática.');
        this.modelRadio = this._radio('model', 'Un modelo concreto',
            'Ese modelo describe cada imagen y el chat recibe su descripción.');
        this.select = el('select', 'vision-model-select');
        this.select.setAttribute('aria-label', 'Modelo de visión');
        this.excludedNote = el('p', 'vision-model-note', '');
        this.modes.append(this.autoRadio.label, this.modelRadio.label, this.select, this.excludedNote);

        this.route = el('div', 'vision-model-route');
        this.route.setAttribute('role', 'status');
        this.routeTitle = el('strong', '', 'Ruta efectiva');
        this.routeLines = el('ul', 'vision-model-route-lines');
        this.lastLine = el('p', 'vision-model-last', '');
        this.route.append(this.routeTitle, this.routeLines, this.lastLine);

        this.status = el('p', 'vision-model-status');
        this.status.setAttribute('role', 'status');
        this.status.setAttribute('aria-live', 'polite');

        const footer = el('footer', 'vision-model-actions');
        this.refreshBtn = this._button('Comprobar de nuevo', () => void this.load(true, { refresh: true }));
        this.discardBtn = this._button('Descartar', () => {
            if (this.saving) return;
            this.draft = this.saved ? { ...this.saved } : null;
            this.notice = '';
            this.render();
        });
        this.saveBtn = this._button('Guardar', () => void this.save(), 'vision-model-save');
        footer.append(this.refreshBtn, this.discardBtn, this.saveBtn);

        this.surface.append(head, this.modes, this.route, this.status, footer);
        this.root.replaceChildren(this.surface);

        this.select.addEventListener('change', () => {
            if (this.saving || !this.draft) return;
            this.draft = { mode: 'model', key: this.select.value };
            this.notice = '';
            this.render();
        });
    }

    _radio(value, title, help) {
        const label = el('label', 'vision-model-mode');
        const input = el('input');
        input.type = 'radio';
        input.name = 'vision-model-mode';
        input.value = value;
        const copy = el('span', '');
        copy.append(el('strong', '', title), el('small', '', help));
        label.append(input, copy);
        input.addEventListener('change', () => {
            if (this.saving || !this.draft || !input.checked) return;
            const first = this.select.options[0]?.value || '';
            this.draft = value === 'auto' ? { mode: 'auto', key: '' } : { mode: 'model', key: this.draft.key || this.select.value || first };
            this.notice = '';
            this.render();
        });
        return { label, input };
    }

    _button(text, action, className = '') {
        const node = el('button', className, text);
        node.type = 'button';
        node.addEventListener('click', () => {
            if (!node.disabled) action();
        });
        return node;
    }

    async load(force = false, { refresh = false } = {}) {
        if (!this.root) return;
        if (this.saving) {
            this._reloadAfterSave = { refresh: refresh || Boolean(this._reloadAfterSave?.refresh) };
            return;
        }
        if (this.state && !force) return;
        const generation = ++this._generation;
        this.loading = true;
        this.error = '';
        this.render();
        try {
            const response = await this.fetch(`/api/vision-model${refresh ? '?refresh=true' : ''}`, { cache: 'no-store' });
            const body = await response.json().catch(() => ({}));
            if (generation !== this._generation) return;
            if (!response.ok) throw new Error(errorText(body, 'No pude leer el modelo de visión.'));
            this._apply(normalizeVisionState(body));
        } catch (error) {
            if (generation !== this._generation) return;
            this.error = error?.message || 'No pude leer el modelo de visión.';
        } finally {
            if (generation === this._generation) {
                this.loading = false;
                this.render();
            }
        }
    }

    _apply(state) {
        const keepDraft = this.dirty && this.state?.profile === state.profile;
        this.state = state;
        this.saved = draftFromState(state);
        if (!keepDraft) this.draft = { ...this.saved };
    }

    async save() {
        if (!this.state || !this.dirty || this.saving || this.loading || !this.state.writable) return;
        const generation = ++this._generation;
        this.saving = true;
        this.error = '';
        this.notice = '';
        this.render();
        try {
            const response = await this.fetch('/api/vision-model', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payloadFor(this.state, this.draft)),
            });
            const body = await response.json().catch(() => ({}));
            if (generation !== this._generation) return;
            if (response.status === 409) {
                this.error = errorText(body, 'El perfil cambió mientras se guardaba.');
                this._reloadAfterSave = { refresh: false };
                return;
            }
            if (!response.ok) throw new Error(errorText(body, 'Hermes no guardó el modelo de visión.'));
            this.state = normalizeVisionState(body);
            this.saved = draftFromState(this.state);
            this.draft = { ...this.saved };
            this.notice = 'Modelo de visión guardado en Hermes.';
        } catch (error) {
            if (generation === this._generation) this.error = error?.message || 'Hermes no guardó el modelo de visión.';
        } finally {
            if (generation === this._generation) this.saving = false;
            this.render();
            const reload = this._reloadAfterSave;
            this._reloadAfterSave = null;
            if (reload && !this.saving) void this.load(true, reload);
        }
    }

    render() {
        if (!this.surface) return;
        const state = this.state;
        const busy = this.saving || (this.loading && !state);
        this.profileChip.textContent = `Perfil de Hermes: ${state?.profile || '…'}`;

        const { groups, excluded } = choiceGroups(state, this.draft);
        const selected = this.draft?.mode === 'model' ? this.draft.key : '';
        if (document.activeElement !== this.select || this.select.dataset.signature !== JSON.stringify(groups)) {
            this.select.replaceChildren();
            if (!groups.length) this.select.append(el('option', '', 'Hermes no ofrece modelos con sesión'));
            for (const group of groups) {
                const node = el('optgroup');
                node.label = group.label;
                for (const row of group.models) {
                    const option = el('option', '', `${row.model} · ${row.providerLabel}${row.missing ? ' (ya no está en el catálogo)' : ''}`);
                    option.value = row.key;
                    node.append(option);
                }
                this.select.append(node);
            }
            this.select.dataset.signature = JSON.stringify(groups);
        }
        if (selected) this.select.value = selected;
        this.excludedNote.textContent = excluded
            ? `${excluded} ${excluded === 1 ? 'modelo no aparece' : 'modelos no aparecen'}: Hermes indica que no leen imágenes.`
            : '';

        const locked = busy || !state || !state.writable;
        this.autoRadio.input.checked = this.draft?.mode !== 'model';
        this.modelRadio.input.checked = this.draft?.mode === 'model';
        this.autoRadio.input.disabled = locked;
        this.modelRadio.input.disabled = locked || !groups.length;
        this.select.disabled = locked || this.draft?.mode !== 'model' || !groups.length;

        const summary = routeSummary(state);
        this.route.dataset.tone = summary.tone;
        this.routeLines.replaceChildren(...summary.lines.map((line) => el('li', '', line)));
        this.lastLine.textContent = lastSummary(state);

        let status = '';
        if (this.saving) status = 'Guardando en Hermes…';
        else if (this.loading) status = state ? 'Comprobando con Hermes…' : 'Consultando a Hermes… puede tardar unos segundos.';
        else if (this.error) status = this.error;
        else if (state?.error) status = state.error;
        else if (state?.catalogError) status = `Catálogo no disponible: ${state.catalogError}`;
        else if (state && !state.writable) status = state.reason || 'Este modelo de visión se cambia desde Hermes.';
        else if (this.notice) status = this.notice;
        else if (this.dirty) status = 'Cambios sin guardar.';
        this.status.textContent = status;
        this.status.dataset.tone = this.error || state?.error ? 'error' : '';

        this.refreshBtn.disabled = this.saving || this.loading;
        this.discardBtn.disabled = this.saving || !this.dirty;
        this.saveBtn.disabled = locked || this.loading || !this.dirty;
    }
}
