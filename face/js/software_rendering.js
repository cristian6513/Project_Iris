/**
 * A PC that draws the room without a GPU (a virtual machine, a missing or broken
 * driver) runs WebGL on the CPU. There «Equilibrado» at 60 FPS can be too much, and a
 * first-time user would not know to look in Ajustes → Rendimiento.
 *
 * Iris offers «Ahorro» at 30 FPS once, and changes nothing unless the user accepts.
 * The offer needs the saved settings read from Core: without them Iris cannot tell
 * the factory values from a choice, and a guess could undo that choice (Codex,
 * 2026-11-17: a failed read made the first version overwrite «Alta, sin límite»).
 */
import { GRAPHICS_QUALITIES } from './graphics_quality.js?v=3';

const SOFTWARE_RENDERERS = /swiftshader|llvmpipe|softpipe|lavapipe|microsoft basic render|\bwarp\b|software rasterizer/i;
const QUALITIES = new Set(['saving', 'balanced', 'high']);
const FPS_LIMITS = new Set([0, 30, 60]);

export const SOFTWARE_RENDERING_KEY = 'iris.softwareRenderingSaving';
export const SOFTWARE_RENDERING_CHOICE = Object.freeze({ render_quality: 'saving', render_fps_limit: 30 });

/** The GPU WebGL reports: the unmasked name when the browser gives it, else the plain one. */
export function rendererName(gl) {
    try {
        const info = gl?.getExtension?.('WEBGL_debug_renderer_info');
        return String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER) || '');
    } catch {
        return '';
    }
}

export function isSoftwareRenderer(name) {
    return SOFTWARE_RENDERERS.test(String(name || ''));
}

function settingsLabel({ render_quality: quality, render_fps_limit: fps }) {
    return `${GRAPHICS_QUALITIES[quality].label} ${fps === 0 ? 'sin límite de FPS' : `a ${fps} FPS`}`;
}

/** The dialog: what is happening, what the user has now, and both ways out. */
export function savingSuggestion(saved) {
    return {
        title: 'Tu equipo dibuja sin aceleración gráfica',
        message: 'La habitación se está dibujando con el procesador, sin aceleración gráfica, '
            + `así que puede ir lenta. Ahora usas ${settingsLabel(saved)}. `
            + 'Con Ahorro a 30 FPS suele ir más fluida. Puedes cambiarlo cuando quieras en Ajustes → Rendimiento.',
        confirmLabel: 'Probar Ahorro a 30 FPS',
        cancelLabel: 'Mantener mi configuración',
    };
}

function wasHandled(storage) {
    try { return storage?.getItem(SOFTWARE_RENDERING_KEY) === '1'; } catch { return false; }
}

function markHandled(storage) {
    try { storage?.setItem(SOFTWARE_RENDERING_KEY, '1'); } catch { /* the offer may come back once more */ }
}

async function savedSettings(fetchImpl) {
    try {
        const response = await fetchImpl('/api/render-settings', { cache: 'no-store' });
        if (!response?.ok) return null;
        const saved = await response.json();
        return QUALITIES.has(saved?.render_quality) && FPS_LIMITS.has(saved?.render_fps_limit) ? saved : null;
    } catch {
        return null;
    }
}

/**
 * Offers «Ahorro» at 30 FPS when the renderer is software. Returns what happened:
 * null (no offer), 'kept', 'saved' or 'unsaved' (accepted, applied for this session,
 * but Core did not save it; the offer comes back on the next start).
 */
export async function suggestSavingOnSoftwareRendering({
    gl, ask, apply, storage = globalThis.localStorage, fetchImpl = globalThis.fetch,
} = {}) {
    if (!isSoftwareRenderer(rendererName(gl)) || wasHandled(storage)) return null;
    const saved = await savedSettings(fetchImpl);
    if (!saved) return null;
    if (saved.render_quality === 'saving' && saved.render_fps_limit === 30) {
        markHandled(storage);
        return null;
    }
    if (!await ask(savingSuggestion(saved))) {
        markHandled(storage);
        return 'kept';
    }
    apply?.({ ...SOFTWARE_RENDERING_CHOICE });
    try {
        const response = await fetchImpl('/api/render-settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(SOFTWARE_RENDERING_CHOICE),
        });
        if (response?.ok) {
            markHandled(storage);
            return 'saved';
        }
    } catch { /* this session keeps «Ahorro»; the offer comes back next start */ }
    return 'unsaved';
}
