import { GRAPHICS_QUALITIES } from './graphics_quality.js?v=2';

const QUALITY_HELP = {
    saving: 'Reduce la resolución 3D y los efectos de brillo para gastar menos. El texto y los controles conservan su nitidez. Las texturas del avatar se limitan a 1024 px la próxima vez que se carga.',
    balanced: 'Equilibrio entre detalle y consumo. Recomendado para empezar.',
    high: 'Más nitidez en pantallas de alta densidad y bordes más suaves. Puede aumentar el uso de GPU.',
};
const confirmed = data => [0, 30, 60].includes(data?.render_fps_limit)
    && Object.hasOwn(GRAPHICS_QUALITIES, data?.render_quality);

/** Profile-backed rendering preferences, shared live with Face and Companion. */
export class RenderSettings {
    constructor(root, { fetchImpl = globalThis.fetch } = {}) {
        this.root = root; this.fetch = fetchImpl.bind(globalThis); this.busy = false;
        root.innerHTML = `<header><span>Rendimiento</span><strong class="os-update-pill" data-render-pill>FPS</strong></header>
            <p>Ajusta el detalle 3D y los FPS de la habitación y el avatar, incluido Companion.</p>
            <label>Calidad gráfica <select data-render-quality aria-label="Calidad gráfica" aria-describedby="render-quality-help">
                <option value="saving">Ahorro</option><option value="balanced">Equilibrado · recomendado</option>
                <option value="high">Alta calidad</option></select></label>
            <p id="render-quality-help" data-render-help></p>
            <label>Fotogramas por segundo <select data-render-fps aria-label="Fotogramas por segundo">
                <option value="30">30 FPS · menor consumo</option><option value="60">60 FPS · recomendado</option>
                <option value="0">Sin límite · mayor consumo</option></select></label>
            <p>No cambia la velocidad de la voz ni la frecuencia del análisis de cámara. Las animaciones se pausan al ocultar la vista.</p>
            <div class="os-update-actions"><button type="button" disabled>Guardar rendimiento</button></div>
            <p role="status" aria-live="polite">Cargando configuración…</p>`;
        this.select = root.querySelector('[data-render-fps]'); this.button = root.querySelector('button');
        this.quality = root.querySelector('[data-render-quality]');
        this.help = root.querySelector('[data-render-help]');
        this.status = root.querySelector('[role="status"]'); this.select.disabled = true; this.quality.disabled = true;
        this.pill = root.querySelector('[data-render-pill]');
        this.dirty = false;
        this.quality.value = 'balanced'; this._showHelp();
        this.select.onchange = this.quality.onchange = () => {
            this.dirty = true; this._showHelp(); this.status.textContent = 'Cambios de rendimiento sin guardar.';
        };
        this.button.onclick = () => this.save();
    }
    async load() {
        if (this.busy || this.dirty) return;
        this.busy = true;
        try {
            const response = await this.fetch('/api/render-settings', { cache: 'no-store' });
            if (!response.ok) throw new Error('No pude leer el rendimiento. Vuelve a abrir Ajustes para reintentar.');
            const data = await response.json();
            if (!confirmed(data)) throw new Error('No pude leer el rendimiento. Vuelve a abrir Ajustes para reintentar.');
            this.select.value = String(data.render_fps_limit);
            this.quality.value = data.render_quality; this._showHelp();
            this._showSaved(data);
            this.select.disabled = false; this.quality.disabled = false; this.button.disabled = false;
            this.status.textContent = 'Se aplica a las vistas abiertas al guardar.';
        } catch (error) { this.status.textContent = error.message; }
        finally { this.busy = false; }
    }
    async save() {
        if (this.busy || this.select.disabled) return;
        this.busy = true; this.button.disabled = true; this.select.disabled = true; this.quality.disabled = true;
        try {
            const response = await this.fetch('/api/render-settings', { method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ render_fps_limit: Number(this.select.value), render_quality: this.quality.value }) });
            if (!response.ok) throw new Error('No pude guardar. La configuración anterior sigue activa; puedes reintentar.');
            const data = await response.json();
            if (!confirmed(data)) throw new Error('El servidor no confirmó el rendimiento. Vuelve a abrir Ajustes para comprobarlo.');
            this.select.value = String(data.render_fps_limit);
            this.quality.value = data.render_quality; this._showHelp();
            this._showSaved(data);
            this.dirty = false;
            this.status.textContent = 'Rendimiento guardado y aplicado.';
        } catch (error) { this.status.textContent = error.message; }
        finally { this.busy = false; this.button.disabled = false; this.select.disabled = false; this.quality.disabled = false; }
    }
    /** Saved values only: editing or a failed save must not change this pill. */
    _showSaved({ render_fps_limit: fps, render_quality: quality }) {
        if (this.pill) this.pill.textContent = `${GRAPHICS_QUALITIES[quality].label} · ${fps === 0 ? 'Sin límite' : `${fps} FPS`}`;
    }
    _showHelp() {
        this.help.textContent = QUALITY_HELP[this.quality.value] || '';
    }
}
