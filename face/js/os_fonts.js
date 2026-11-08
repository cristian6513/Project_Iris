/** Fixed stacks and profile-local IDs, never CSS supplied by a theme file. */
export const FONT_MAX_BYTES = 2_000_000;
export const FONT_ID = /^font_[a-f0-9]{64}$/;
export const FONT_CHOICES = Object.freeze({ outfit: { label: 'Outfit · incluida', stack: 'IrisOutfit, system-ui, sans-serif' },
    syne: { label: 'Syne · incluida', stack: 'IrisSyne, system-ui, sans-serif' },
    system: { label: 'Sistema', stack: 'system-ui, sans-serif' } });
export const validFont = (id) => typeof id === 'string' && (Object.hasOwn(FONT_CHOICES, id) || FONT_ID.test(id));
export function fontSettings(value, strict = false) {
    const result = {};
    for (const [slot, fallback] of [['textFont', 'outfit'], ['titleFont', 'syne']]) {
        const id = value?.[slot] ?? fallback;
        if (strict && !validFont(id)) throw new Error('La tipografía del tema no es compatible.');
        result[slot] = validFont(id) ? id : fallback;
    }
    return result;
}
export function checkFontBytes(buffer) {
    const view = new Uint8Array(buffer);
    if (view.length < 48 || view.length > FONT_MAX_BYTES || String.fromCharCode(...view.slice(0, 4)) !== 'wOF2')
        throw new Error('Selecciona una fuente WOFF2 de hasta 2 MB.');
    return buffer;
}

/** Own at most the selected faces plus a temporary preview. Latest selection wins. */
export class ThemeFonts {
    constructor({ root, overlayRoot = globalThis.document?.documentElement, fonts = globalThis.document?.fonts,
        FontFaceImpl = globalThis.FontFace, fetchImpl = (...args) => fetch(...args), onStatus = () => {} }) {
        Object.assign(this, { root, overlayRoot, fonts, FontFaceImpl, fetchImpl, onStatus });
        this.cache = new Map(); this.sequence = 0; this.key = ''; this.disposed = false; this.preview = null;
    }
    async decode(family, buffer) {
        checkFontBytes(buffer);
        if (!this.FontFaceImpl || !this.fonts) throw new Error('Esta ventana no puede cargar fuentes propias. Usa una fuente incluida.');
        const face = new this.FontFaceImpl(family, buffer);
        let timer;
        try { return await Promise.race([face.load(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('La fuente tardó demasiado en abrirse. Elige otra.')), 10_000); })]); }
        finally { clearTimeout(timer); }
    }
    async load(id) {
        if (!FONT_ID.test(id)) throw new Error('Fuente no válida.');
        if (!this.cache.has(id)) {
            const entry = { face: null, controller: new AbortController(), promise: null };
            entry.promise = (async () => {
                const response = await this.fetchImpl(`/api/os/fonts/${id}`, { cache: 'no-store', signal: entry.controller.signal });
                if (!response.ok) throw new Error('La fuente no está disponible en este perfil. Elige otra o vuelve a subirla.');
                const face = await this.decode(`IrisFont_${id.slice(5)}`, await response.arrayBuffer());
                entry.face = face; return face;
            })().catch(error => { if (this.cache.get(id) === entry) this.cache.delete(id); throw error; });
            this.cache.set(id, entry);
        }
        return this.cache.get(id).promise;
    }
    style(slot, stack) {
        this.root.style.setProperty(slot === 'textFont' ? '--font-main' : '--font-display', stack);
        if (slot === 'textFont') this.root.style.setProperty('--font-body', stack);
        this.overlayRoot?.style.setProperty(slot === 'textFont' ? '--iris-ui-font' : '--iris-ui-title-font', stack);
    }
    apply(theme) {
        if (this.disposed) return;
        const settings = fontSettings(theme), key = JSON.stringify(settings);
        if (key === this.key) return;
        this.key = key; const sequence = ++this.sequence;
        const needed = new Set(Object.values(settings).filter(id => FONT_ID.test(id)));
        for (const [id, entry] of this.cache) if (!needed.has(id)) { entry.controller.abort(); if (entry.face) this.fonts?.delete(entry.face); this.cache.delete(id); }
        const failures = [];
        const tasks = Object.entries(settings).map(async ([slot, id]) => {
            this.style(slot, FONT_CHOICES[slot === 'textFont' ? 'outfit' : 'syne'].stack);
            if (FONT_CHOICES[id]) { this.style(slot, FONT_CHOICES[id].stack); return; }
            try {
                const face = await this.load(id);
                if (this.disposed || sequence !== this.sequence) return;
                this.fonts.add(face); this.style(slot, `IrisFont_${id.slice(5)}, system-ui, sans-serif`);
            } catch (error) { if (!this.disposed && sequence === this.sequence) failures.push(error.message); }
        });
        this.onStatus(needed.size ? 'Cargando tipografía…' : '');
        void Promise.all(tasks).then(() => {
            if (!this.disposed && sequence === this.sequence) this.onStatus(failures.length ? `${failures[0]} Mientras tanto se usa la fuente incluida.` : '');
        });
    }
    retry(theme) { this.key = ''; this.apply(theme); }
    clearPreview() { if (this.preview) this.fonts?.delete(this.preview); this.preview = null; }
    dispose() {
        this.disposed = true; this.sequence++; this.clearPreview();
        for (const entry of this.cache.values()) { entry.controller.abort(); if (entry.face) this.fonts?.delete(entry.face); }
        this.cache.clear();
    }
}
