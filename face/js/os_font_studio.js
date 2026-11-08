import { FONT_CHOICES, FONT_MAX_BYTES, FONT_ID, fontSettings, checkFontBytes } from './os_fonts.js?v=2';
import { irisConfirm } from './iris_dialog.js?v=9';

export class FontStudio {
    constructor({ root, runtime, getTheme, commit, fetchImpl = (...args) => fetch(...args), documentLike = document, confirm = irisConfirm }) {
        Object.assign(this, { root, runtime, getTheme, commit, fetchImpl, document: documentLike, confirm });
        this.visible = false; this.fonts = []; this.loaded = false; this.busy = false; this.notice = ''; this.runtimeStatus = '';
        this.sequence = 0; this.signature = ''; this.staged = null; this.controller = null;
    }
    node(tag, text = '') { const n = this.document.createElement(tag); n.textContent = text; return n; }
    button(text, run) { const b = this.node('button', text); b.type = 'button'; b.disabled = this.busy; b.onclick = () => void run(); return b; }
    status(text) { this.runtimeStatus = text; this.render(); }
    async read() {
        if (this.busy || !this.visible) return;
        this.busy = true; this.notice = ''; const sequence = ++this.sequence;
        this.controller = new AbortController(); this.render();
        try {
            const response = await this.fetchImpl('/api/os/fonts', { cache: 'no-store', signal: this.controller.signal });
            const data = await response.json(); if (!response.ok || !Array.isArray(data.fonts)) throw new Error('No pude leer tus fuentes. Vuelve a intentarlo.');
            if (sequence === this.sequence) { this.fonts = data.fonts; this.loaded = true; }
        } catch(error) { if (sequence === this.sequence && error.name !== 'AbortError') this.notice = error.message; }
        finally { if (sequence === this.sequence) { this.busy = false; this.controller = null; this.render(); } }
    }
    async prepare(file) {
        if (this.busy || !file || !this.visible) return;
        if (file.size > FONT_MAX_BYTES) { this.notice = 'Selecciona una fuente WOFF2 de hasta 2 MB.'; this.render(); return; }
        this.cancelPreview(); const sequence = ++this.sequence; this.busy = true; this.notice = ''; this.render();
        try {
            const buffer = checkFontBytes(await file.arrayBuffer());
            const face = await this.runtime.decode(`IrisFontPreview_${sequence}`, buffer);
            if (sequence !== this.sequence || !this.visible) return;
            this.runtime.fonts.add(face); this.runtime.preview = face;
            this.staged = { buffer, label: file.name.replace(/\.woff2$/i, '').slice(0, 60) || 'Fuente propia', family: face.family };
        } catch (error) { if (sequence === this.sequence) this.notice = `No pude abrir esa fuente. ${error.message}`; }
        finally { if (sequence === this.sequence) { this.busy = false; this.render(); } }
    }
    cancelPreview() { this.runtime.clearPreview(); this.staged = null; }
    choose(slot, value) {
        if (this.busy) return;
        const next = { ...this.getTheme(), [slot]: value };
        this.notice = this.commit(next) ? 'Tipografía guardada.' : 'No pude guardar la elección. Se conserva la anterior.';
        this.render();
    }
    async save(slot) {
        if (this.busy || !this.staged || !this.visible) return;
        const staged = this.staged, before = JSON.stringify(fontSettings(this.getTheme())), sequence = ++this.sequence;
        this.busy = true; this.notice = ''; this.controller = new AbortController(); this.render();
        try {
            const response = await this.fetchImpl('/api/os/fonts', { method: 'POST', signal: this.controller.signal,
                headers: { 'Content-Type': 'application/octet-stream', 'X-Iris-Font-Name': encodeURIComponent(staged.label) }, body: staged.buffer });
            const data = await response.json();
            if (!response.ok || !FONT_ID.test(data.id) || typeof data.label !== 'string') throw new Error(typeof data?.detail === 'string' ? data.detail : data?.error?.message || 'No pude guardar esa fuente.');
            if (sequence !== this.sequence || !this.visible) return;
            this.fonts = [...this.fonts.filter(f => f.id !== data.id), data];
            if (before !== JSON.stringify(fontSettings(this.getTheme()))) this.notice = 'La fuente quedó en tu biblioteca. Tu elección más reciente se conserva.';
            else this.notice = this.commit({ ...this.getTheme(), [slot]: data.id }) ? 'Fuente guardada y aplicada.' : 'La fuente quedó en tu biblioteca, pero no se aplicó. Elígela de nuevo para guardar.';
            this.cancelPreview();
        } catch (error) { if (sequence === this.sequence && error.name !== 'AbortError') this.notice = error.message; }
        finally { if (sequence === this.sequence) { this.busy = false; this.controller = null; this.render(); } }
    }
    async remove(id) {
        if (this.busy || !this.visible) return;
        const sequence = ++this.sequence;
        if (!await this.confirm({ title: 'Quitar fuente propia', message: 'Se quitará de este perfil. Si estaba en uso, texto y títulos volverán a sus fuentes incluidas.', confirmLabel: 'Quitar fuente' })) return;
        if (!this.visible || sequence !== this.sequence) return;
        const next = { ...this.getTheme() }; if (next.textFont === id) next.textFont = 'outfit'; if (next.titleFont === id) next.titleFont = 'syne';
        if (!this.commit(next)) { this.notice = 'No pude guardar la selección. No se quitó la fuente.'; this.render(); return; }
        this.busy = true; this.controller = new AbortController(); this.render();
        try {
            const response = await this.fetchImpl(`/api/os/fonts/${id}`, { method: 'DELETE', signal: this.controller.signal });
            if (!response.ok) throw new Error('No pude quitar la fuente. Puedes reintentarlo; tu selección ya está guardada.');
            if (sequence === this.sequence) { this.fonts = this.fonts.filter(f => f.id !== id); this.notice = 'Fuente quitada de este perfil.'; }
        } catch(error) { if (sequence === this.sequence && error.name !== 'AbortError') this.notice = error.message; }
        finally { if (sequence === this.sequence) { this.busy = false; this.controller = null; this.render(); } }
    }
    render() {
        if (!this.visible) return;
        const settings = fontSettings(this.getTheme());
        const signature = JSON.stringify([settings, this.fonts, this.loaded, this.busy, this.notice, this.runtimeStatus, this.staged?.label]);
        if (signature === this.signature) return; this.signature = signature;
        const focus = this.document.activeElement?.dataset?.fontFocus || this.pendingFocus;
        if (this.busy) this.pendingFocus = focus; else this.pendingFocus = '';
        const head = this.node('h3', 'Tipografía'); const hint = this.node('p', 'Elige el texto y los títulos de Iris OS. Prueba tus archivos WOFF2 antes de aplicarlos. No se instalan en Windows.');
        const fields = this.node('div'); fields.className = 'os-font-fields';
        for (const [slot, label] of [['textFont', 'Texto'], ['titleFont', 'Títulos']]) {
            const row = this.node('label', label), select = this.node('select'); select.setAttribute('aria-label', `Fuente de ${label.toLowerCase()}`); select.dataset.fontFocus = slot;
            for (const [id, choice] of [...Object.entries(FONT_CHOICES), ...this.fonts.map(f => [f.id, { label: f.label }])]) { const option = this.node('option', choice.label); option.value = id; select.append(option); }
            if (![...Object.keys(FONT_CHOICES), ...this.fonts.map(f => f.id)].includes(settings[slot])) { const option = this.node('option', 'Fuente guardada · no disponible'); option.value = settings[slot]; select.append(option); }
            select.value = settings[slot]; select.disabled = this.busy; select.onchange = () => this.choose(slot, select.value); row.append(select); fields.append(row);
        }
        const sample = this.node('div'); sample.className = 'os-font-sample';
        sample.append(this.node('strong', 'Tu espacio, a tu manera'), this.node('p', 'Buenos días. ¿Qué hacemos hoy? · ÁÉÍÓÚ · ñ · 0123456789'));
        const actions = this.node('div'); actions.className = 'os-font-actions';
        const input = this.node('input'); input.type = 'file'; input.accept = '.woff2,font/woff2'; input.hidden = true; input.onchange = () => void this.prepare(input.files?.[0]);
        const add = this.button('Añadir fuente WOFF2', () => input.click()); add.dataset.fontFocus = 'add';
        const refresh = this.button('Volver a leer', () => this.read()); refresh.dataset.fontFocus = 'refresh'; actions.append(add, refresh, input);
        const status = this.node('p', this.runtimeStatus || this.notice || (this.busy ? 'Preparando tipografía…' : 'Hasta 8 fuentes · 2 MB por archivo.')); status.setAttribute('role', 'status');
        this.root.replaceChildren(head, hint, fields, sample, actions, status);
        if (this.runtimeStatus) actions.append(this.button('Reintentar carga', () => this.runtime.retry(this.getTheme())));
        if (this.staged) {
            const preview = this.node('div'); preview.className = 'os-font-sample os-font-pending'; preview.style.fontFamily = this.staged.family;
            preview.append(this.node('strong', this.staged.label), this.node('p', 'Así se verá: Buenos días · ¿Cómo estás? · ñ · 0123456789'));
            this.root.append(preview, this.node('p', 'La vista previa usa el estilo que contiene este archivo; los pesos y caracteres disponibles dependen de su autor.'),
                this.button('Guardar para texto', () => this.save('textFont')), this.button('Guardar para títulos', () => this.save('titleFont')),
                this.button('Cancelar vista previa', () => { this.sequence++; this.cancelPreview(); this.render(); }));
        }
        if (this.fonts.length) {
            const details = this.node('details'), summary = this.node('summary', `Fuentes propias (${this.fonts.length}/8)`); details.open = !!this.libraryOpen;
            details.ontoggle = () => { this.libraryOpen = details.open; }; details.append(summary);
            for (const font of this.fonts) { const row = this.node('p', font.label + ' '); row.append(this.button('Quitar', () => this.remove(font.id))); details.append(row); }
            this.root.append(details);
        }
        if (focus) [...this.root.querySelectorAll('[data-font-focus]')].find(n => n.dataset.fontFocus === focus && !n.disabled)?.focus();
    }
    setVisibility(value) {
        if (this.visible === Boolean(value)) return; this.visible = Boolean(value);
        if (this.visible) { this.signature = ''; this.render(); void this.read(); }
        else { this.sequence++; this.controller?.abort(); this.controller = null; this.busy = false; this.cancelPreview(); }
    }
    dispose() { this.setVisibility(false); this.root.replaceChildren(); }
}
