/** Optical zoom for the conversation view. Explore keeps its own camera controls. */
export const AVATAR_ZOOM_KEY = 'IRIS.roomAvatarZoom.v1';

function zoomValue(value) {
    if (!['number', 'string'].includes(typeof value) || String(value).trim() === '') return 0;
    const number = Number(value);
    return Number.isFinite(number) ? Math.round(Math.max(0, Math.min(100, number))) : 0;
}

export class RoomAvatarZoom {
    constructor({ storage = () => globalThis.localStorage } = {}) {
        this._storage = storage;
        this._listeners = new Set();
        this.value = 0;
        this.exploring = false;
        this.persisted = null;
        this.camera = null;
        try { this.value = zoomValue(storage()?.getItem(AVATAR_ZOOM_KEY)); } catch { /* Use the normal view. */ }
    }

    attachCamera(camera) {
        this.camera = camera;
        this._apply();
    }

    snapshot() {
        return { value: this.value, zoom: 1 + this.value * 0.015, exploring: this.exploring, persisted: this.persisted };
    }

    subscribe(listener) {
        this._listeners.add(listener);
        listener(this.snapshot());
        return () => this._listeners.delete(listener);
    }

    setValue(value) {
        if (this.exploring) return false;
        this.value = zoomValue(value);
        try {
            const storage = this._storage();
            if (!storage) throw Error('Storage unavailable');
            storage.setItem(AVATAR_ZOOM_KEY, String(this.value));
            this.persisted = true;
        } catch { this.persisted = false; }
        this._apply();
        this._notify();
        return true;
    }

    setExploring(exploring) {
        this.exploring = exploring === true;
        this._apply();
        this._notify();
    }

    _apply() {
        if (!this.camera) return;
        this.camera.zoom = this.exploring ? 1 : this.snapshot().zoom;
        this.camera.updateProjectionMatrix();
    }

    _notify() {
        for (const listener of this._listeners) listener(this.snapshot());
    }
}

/** Lazy binding: SidePanel is constructed before app.js assigns its controller. */
export function bindAvatarZoomControls(root, getController) {
    const slider = root?.querySelector('[data-avatar-zoom]');
    const reset = root?.querySelector('[data-avatar-zoom-reset]');
    const note = root?.querySelector('[data-avatar-zoom-note]');
    if (!slider) return { refresh() {}, dispose() {} };
    let controller = null, unsubscribe = null;
    const render = (state) => {
        slider.disabled = !state || state.exploring;
        slider.value = String(state?.value ?? 0);
        slider.setAttribute('aria-valuetext', state ? state.zoom.toLocaleString('es', { maximumFractionDigits: 1 }) + ' veces' : 'No disponible');
        if (reset) reset.disabled = slider.disabled || state.value === 0;
        if (note) {
            note.textContent = !state ? 'Vista no disponible.' : state.exploring
                ? 'Sal de Explorar para acercar el avatar.' : state.persisted === false
                    ? 'Solo esta sesión: no se pudo guardar.' : '';
            note.hidden = !note.textContent;
        }
    };
    const refresh = () => {
        const next = getController?.() || null;
        if (next !== controller) {
            unsubscribe?.(); controller = next;
            unsubscribe = controller?.subscribe(render) || null;
        }
        render(controller?.snapshot());
    };
    const onInput = () => {
        const value = slider.value;
        refresh();
        controller?.setValue(value);
    };
    const onReset = () => { refresh(); controller?.setValue(0); };
    slider.addEventListener('input', onInput);
    reset?.addEventListener('click', onReset);
    refresh();
    return {
        refresh,
        dispose() {
            unsubscribe?.();
            slider.removeEventListener('input', onInput);
            reset?.removeEventListener('click', onReset);
        },
    };
}
