import { BUILTIN_TRANSITIONS, TRANSITION_LIMITS, exportTransition, parseTransitionFile, themeTransitions, validateTransition } from './os_transition_format.js?v=1';
import { TransitionPlayer } from './os_transition_player.js?v=1';
import { TRANSITION_EXAMPLE_CSS } from './os_transition_examples.js?v=1';
import { irisConfirm } from './iris_dialog.js?v=9';

const builtinLabels = ['Sin animación', 'Fade', 'Slide', 'Zoom', 'Glitch', 'Iris'];
const builtinAnimations = { fade: 'irisWindowFade', slide: 'irisWindowSlide', zoom: 'irisWindowZoom', glitch: 'irisDesktopGlitch', iris: 'irisWindowIris' };

/** Theme Studio's importer. A draft is inert until «Guardar y usar» succeeds. */
export class TransitionStudio {
    constructor({ root, getTheme, commit, documentLike = globalThis.document, confirm = irisConfirm, download } = {}) {
        this.root = root; this.getTheme = getTheme; this.commit = commit; this.document = documentLike;
        this.confirm = confirm; this.visible = false; this.disposed = false; this.generation = 0; this.candidates = [];
        this.download = download || ((contents, name, type) => this.downloadFile(contents, name, type));
        this.urls = new Map();
        this.player = new TransitionPlayer({ reduced: () => !!this.getTheme().reducedMotion, documentLike });
        if (!root) return;
        root.innerHTML = `
            <header><span>Transiciones</span></header>
            <p>Al entrar al escritorio y abrir ventanas.</p>
            <div class="motion-overview"><div>
            <label>Transición activa<select data-motion="installed" aria-label="Transición activa"></select></label>
            <p class="motion-meta" data-motion="info"></p>
            <div class="motion-actions">
                <button type="button" data-motion-action="preview">Probar selección</button>
                <button type="button" data-motion-action="export">Exportar transición</button>
                <button type="button" data-motion-action="remove">Quitar</button>
            </div>
            </div>
            <div class="motion-stage" aria-hidden="true"><div class="motion-sample"><span>IRIS OS</span><strong>Tu ventana</strong><i></i><i></i></div></div>
            </div>
            <details data-motion="importer"><summary>Añadir transición</summary>
                <p>Elige un CSS individual de fotogramas o un paquete de transición de Iris. Primero podrás revisarlo.</p>
                <label>Seleccionar archivo<input data-motion="file" type="file" accept=".css,text/css,.json,application/json"></label>
                <label>O pega el CSS o el paquete<textarea data-motion="source" rows="4" maxlength="65536" spellcheck="false" placeholder="@keyframes entrada { … }"></textarea></label>
                <div class="motion-actions"><button type="button" data-motion-action="analyze">Revisar contenido</button><button type="button" data-motion-action="example">Cargar ejemplo</button><button type="button" data-motion-action="example-css">Descargar ejemplo CSS</button></div>
                <p>Admite opacidad, desplazamiento, escala y giro. Si algo no es compatible, Iris lo indicará.</p>
                <div data-motion="draft" hidden>
                    <label>Animación del archivo<select data-motion="candidate"></select></label>
                    <label>Nombre<input data-motion="name" maxlength="80" autocomplete="off"></label>
                    <div class="motion-fields"><label>Autor<input data-motion="author" maxlength="120" autocomplete="off"></label><label>Licencia<input data-motion="license" maxlength="120" autocomplete="off" placeholder="Sin indicar"></label></div>
                    <label>Duración (ms)<input data-motion="duration" type="number" min="80" max="2000" step="10"></label>
                    <details><summary>Atribución conservada</summary><pre data-motion="attribution"></pre></details>
                    <div class="motion-actions"><button type="button" data-motion-action="preview-draft">Probar antes de guardar</button><button type="button" class="motion-primary" data-motion-action="save">Guardar y usar</button><button type="button" data-motion-action="cancel">Descartar</button></div>
                </div>
            </details>
            <p class="motion-status" data-motion="status" role="status" aria-live="polite"></p>`;
        this.onClick = event => {
            const button = event.target.closest?.('[data-motion-action]');
            if (!button || button.disabled) return;
            void this.action(button.dataset.motionAction).catch(error => this.status(error.message, true));
        };
        this.onChange = event => {
            if (event.target === this.$('file')) { void this.readFile(event.target.files?.[0]); event.target.value = ''; }
            if (event.target === this.$('candidate')) this.populateDraft();
            if (event.target === this.$('installed')) this.select(event.target.value);
        };
        this.onInput = event => { if (this.$('draft')?.contains(event.target)) this.stopPreview(); };
        root.addEventListener('click', this.onClick); root.addEventListener('change', this.onChange); root.addEventListener('input', this.onInput);
        this.render();
    }
    $(name) { return this.root?.querySelector(`[data-motion="${name}"]`); }
    button(name) { return this.root?.querySelector(`[data-motion-action="${name}"]`); }
    motion() { return themeTransitions(this.getTheme()); }
    selected() { const state = this.motion(); return state.transitionLibrary.find(item => `custom:${item.id}` === state.desktopTransition); }
    status(message, error = false) {
        const node = this.$('status'); if (!node) return;
        node.textContent = message; node.dataset.error = String(error);
    }
    render() {
        if (!this.root || this.disposed) return;
        const state = this.motion(), select = this.$('installed');
        if (this.selection !== state.desktopTransition) this.stopPreview();
        this.selection = state.desktopTransition;
        const choices = [...BUILTIN_TRANSITIONS.map((id, i) => ({ value: id, name: builtinLabels[i] })),
            ...state.transitionLibrary.map(item => ({ value: `custom:${item.id}`, name: item.name }))];
        const signature = JSON.stringify(choices);
        if (signature !== this.signature) {
            select.replaceChildren(...choices.map(item => {
                const option = this.document.createElement('option'); option.value = item.value; option.textContent = item.name; return option;
            })); this.signature = signature;
        }
        select.value = state.desktopTransition;
        const selected = this.selected();
        this.$('info').textContent = selected ? `${selected.author || 'Autor sin indicar'} · ${selected.license || 'Licencia sin indicar'} · ${selected.duration} ms`
            : 'Incluida con Iris. Puedes añadir y compartir tus propias transiciones.';
        this.button('export').disabled = !selected; this.button('remove').disabled = !selected;
        this.button('preview').disabled = state.desktopTransition === 'none';
        if (this.getTheme().reducedMotion || this.player.blocked()) this.stopPreview();
    }
    setVisibility(visible) {
        this.visible = !!visible && !this.disposed;
        if (!this.visible) {
            this.generation++; this.stopPreview();
            if (this.reading) { this.reading = false; this.status('Lectura cancelada. Vuelve a seleccionar el archivo.'); }
        }
        else this.render();
    }
    async readFile(file) {
        if (!file || !this.visible || this.disposed) return;
        const generation = ++this.generation;
        this.reading = false;
        this.clearDraft();
        if (file.size > TRANSITION_LIMITS.bytes) { this.status('El archivo supera 64 KiB.', true); return; }
        this.status('Leyendo transición…');
        this.reading = true;
        try {
            const source = await file.text();
            if (!this.visible || generation !== this.generation || this.disposed) return;
            this.stage(source);
        } catch (error) {
            if (generation === this.generation && this.visible) { this.reading = false; this.status(error.message || 'No se pudo leer el archivo.', true); }
        }
    }
    stage(source) {
        this.reading = false;
        this.generation++; this.clearDraft();
        try {
            this.candidates = parseTransitionFile(source).transitions;
            this.$('candidate').replaceChildren(...this.candidates.map((item, i) => {
                const option = this.document.createElement('option'); option.value = String(i); option.textContent = item.name; return option;
            }));
            this.$('candidate').value = '0'; this.$('draft').hidden = false; this.$('importer').open = true;
            this.populateDraft(); this.status('Contenido compatible. Revísalo y pruébalo antes de guardar.');
        } catch (error) { this.status(error.message, true); }
    }
    populateDraft() {
        this.stopPreview();
        const item = this.candidates[Number(this.$('candidate').value)]; if (!item) return;
        for (const name of ['name', 'author', 'license', 'duration']) this.$(name).value = String(item[name]);
        this.$('attribution').textContent = item.attribution || 'El archivo no incluye comentarios de atribución.';
    }
    draft() {
        const item = this.candidates[Number(this.$('candidate').value)];
        if (!item) throw new Error('Primero revisa un archivo de transición.');
        return validateTransition({ ...item, name: this.$('name').value, author: this.$('author').value,
            license: this.$('license').value, duration: Number(this.$('duration').value) });
    }
    clearDraft() {
        this.stopPreview(); this.candidates = [];
        if (this.$('draft')) this.$('draft').hidden = true;
    }
    select(value) {
        this.stopPreview();
        try {
            const next = { ...this.getTheme(), desktopTransition: value }; themeTransitions(next, { strict: true });
            if (!this.commit(next)) throw new Error('No se pudo guardar la selección. Se conserva la anterior.');
            this.status('Transición seleccionada.');
        } catch (error) { this.status(error.message, true); }
        this.render();
    }
    stopPreview() {
        this.player.cancelAll(); clearTimeout(this.builtinTimer);
        const sample = this.$('sample') || this.root?.querySelector('.motion-sample');
        if (sample?.style) sample.style.animation = '';
    }
    preview(value, builtin = '') {
        this.stopPreview();
        if (!this.visible || this.player.blocked()) { this.status('La vista previa está pausada por movimiento reducido o porque la ventana está oculta.'); return; }
        const sample = this.root.querySelector('.motion-sample');
        if (builtinAnimations[builtin]) {
            void sample.offsetWidth;
            sample.style.animation = `${builtinAnimations[builtin]} 420ms cubic-bezier(.18,.86,.22,1) both`;
            this.builtinTimer = setTimeout(() => { sample.style.animation = ''; }, 500);
            this.status('Vista previa de la transición incluida.'); return;
        }
        const result = this.player.play(sample, value);
        this.status(result.played ? 'Vista previa de la transición. Probarla no cambia tu selección.' : 'La vista previa no está disponible en este navegador.', !result.played);
    }
    async action(action) {
        if (!this.visible || this.disposed) return;
        if (action === 'analyze') this.stage(this.$('source').value);
        else if (action === 'example') {
            this.stage(TRANSITION_EXAMPLE_CSS); this.$('author').value = 'IRIS OS Contributors'; this.$('license').value = 'MIT';
        } else if (action === 'example-css') this.download(TRANSITION_EXAMPLE_CSS, 'iris-entrada-suave.css', 'text/css');
        else if (action === 'cancel') {
            this.generation++; this.reading = false; this.clearDraft(); this.$('source').value = ''; this.status('Importación descartada.');
            this.root.querySelector('[data-motion-action="analyze"]')?.focus?.();
        }
        else if (action === 'preview-draft') this.preview(this.draft());
        else if (action === 'preview') this.preview(this.selected(), this.motion().desktopTransition);
        else if (action === 'save') {
            const item = this.draft(), theme = this.getTheme(), library = themeTransitions(theme).transitionLibrary;
            if (library.length >= TRANSITION_LIMITS.library) throw new Error('Ya tienes 20 transiciones. Quita una antes de añadir otra.');
            const id = `motion-${globalThis.crypto.randomUUID()}`;
            const next = { ...theme, transitionLibrary: [...library, { ...item, id }], desktopTransition: `custom:${id}` };
            if (!this.commit(next)) throw new Error('No se pudo guardar. La transición sigue en revisión y tu tema anterior se conserva.');
            this.clearDraft(); this.render(); this.status('Transición guardada y seleccionada.');
            this.$('installed').focus?.();
        } else if (action === 'export') {
            const item = this.selected(); if (!item) return;
            this.download(exportTransition(item), `iris-${item.id}.json`, 'application/json');
            this.status('Paquete preparado para descargar, con sus fotogramas y atribución.');
        } else if (action === 'remove') {
            const item = this.selected(); if (!item) return;
            const generation = this.generation;
            const accepted = await this.confirm({ title: 'Quitar transición', message: `¿Quitar «${item.name}» de tus transiciones?`, confirmLabel: 'Quitar', tone: 'danger' });
            if (!accepted || !this.visible || this.disposed || generation !== this.generation) return;
            const theme = this.getTheme(), motion = themeTransitions(theme);
            const next = { ...theme, transitionLibrary: motion.transitionLibrary.filter(row => row.id !== item.id),
                desktopTransition: motion.desktopTransition === `custom:${item.id}` ? 'none' : motion.desktopTransition };
            if (!this.commit(next)) throw new Error('No se pudo quitar. Se conserva la transición.');
            this.stopPreview(); this.render(); this.status('Transición quitada.');
            this.$('installed').focus?.();
        }
    }
    downloadFile(contents, name, type) {
        const url = URL.createObjectURL(new Blob([contents], { type })), link = this.document.createElement('a');
        link.href = url; link.download = name; this.document.body.appendChild(link); link.click(); link.remove();
        // Let the browser/WebView consume the URL before releasing it.
        this.urls.set(url, setTimeout(() => { URL.revokeObjectURL(url); this.urls.delete(url); }, 30_000));
    }
    dispose() {
        this.disposed = true; this.generation++; this.stopPreview(); this.player.dispose();
        this.root?.removeEventListener('click', this.onClick); this.root?.removeEventListener('change', this.onChange); this.root?.removeEventListener('input', this.onInput);
        for (const [url, timer] of this.urls) { clearTimeout(timer); URL.revokeObjectURL(url); } this.urls.clear();
    }
}
