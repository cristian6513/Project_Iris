/** One revision poll per visible surface. Asset bytes remain owned by the player. */
export function validateAnimationSnapshot(snapshot) {
    if (!snapshot || typeof snapshot.revision !== 'string' || !snapshot.manifest?.clips
        || typeof snapshot.manifest.clips !== 'object' || Array.isArray(snapshot.manifest.clips)
        || !snapshot.resources || typeof snapshot.resources !== 'object' || Array.isArray(snapshot.resources)) throw Error('Manifest de animaciones inválido.');
    for (const key of ['body_only_clips', 'return_idle_pool', 'idle_pool', 'menu_poses', 'ambient_poses', 'runtime_only']) {
        const value = snapshot.manifest[key];
        if (value != null && (!Array.isArray(value) || value.some(item => typeof item !== 'string'))) throw Error('Metadatos de animación inválidos.');
    }
    for (const key of ['pose_map', 'activity_clips', 'facial_profiles']) {
        const value = snapshot.manifest[key];
        if (value != null && (typeof value !== 'object' || Array.isArray(value))) throw Error('Metadatos de animación inválidos.');
    }
    if (Object.values(snapshot.manifest.pose_map || {}).some(value => typeof value !== 'string')
        || Object.values(snapshot.manifest.activity_clips || {}).some(value => !value || typeof value.clip !== 'string')) throw Error('Metadatos de animación inválidos.');
    for (const [name, resource] of Object.entries(snapshot.resources)) {
        const filename = resource?.filename;
        if (!/^[a-zA-Z0-9_]+$/.test(name) || ['__proto__', 'prototype', 'constructor'].includes(name)
            || typeof filename !== 'string' || !filename || filename.startsWith('.')
            || /[/\\:%\x00-\x1f]/.test(filename) || !['vrma', 'bvh', 'fbx', 'glb', 'gltf'].includes(resource.format)
            || filename.split('.').pop().toLowerCase() !== resource.format
            || typeof resource.revision !== 'string' || !resource.revision
            || snapshot.manifest.clips[name] !== filename) throw Error('Recurso de animación inválido.');
        if (!resource.url?.startsWith('/face/animations/')) throw Error('URL de animación inválida.');
        const url = new URL(resource.url, 'http://iris.local');
        if (url.origin !== 'http://iris.local' || decodeURIComponent(url.pathname) !== '/face/animations/' + filename
            || url.hash || [...url.searchParams.keys()].some(key => key !== 'v')) throw Error('URL de animación inválida.');
    }
    if (Object.keys(snapshot.manifest.clips).some(name => !Object.hasOwn(snapshot.resources, name))) throw Error('Manifest incompleto.');
    return snapshot;
}

export class AnimationLibrarySync {
    constructor({ getIdle, bridge = null, fetchSnapshot = null, onError = () => {},
        document = globalThis.document, window = globalThis.window,
        setTimer = (callback, delay) => globalThis.setTimeout(callback, delay),
        clearTimer = timer => globalThis.clearTimeout(timer), interval = 5000 } = {}) {
        Object.assign(this, { getIdle, bridge, onError, document, window, setTimer, clearTimer, interval });
        this.fetchSnapshot = fetchSnapshot || ((options) => bridge
            ? bridge.apiFetch('/api/animation-manifest', options) : fetch('/api/animation-manifest', options));
        this.snapshot = null;
        this.etag = null;
        this.disposed = false;
        this._visibility = () => {
            this.clearTimer(this.timer); this.timer = null;
            if (this.active) void this.refresh();
            else this.controller?.abort();
        };
        this._focus = () => { if (this.active) void this.refresh(); };
    }

    get active() { return !this.disposed && this.document?.visibilityState !== 'hidden'; }

    start() {
        if (this.started || this.disposed) return;
        this.started = true;
        this.document?.addEventListener('visibilitychange', this._visibility);
        this.window?.addEventListener('focus', this._focus);
        void this.refresh();
    }

    _schedule() {
        this.clearTimer(this.timer); this.timer = null;
        if (this.started && this.active) this.timer = this.setTimer(() => { this.timer = null; void this.refresh(); }, this.interval);
    }

    async refresh() {
        if (!this.active) return;
        try {
            if (!this.request) {
                const controller = new AbortController(); this.controller = controller;
                this.request = (async () => {
                    const response = await this.fetchSnapshot({ cache: 'no-cache', signal: controller.signal,
                        headers: this.etag ? { 'If-None-Match': this.etag } : {} });
                    if (response.status === 304) return;
                    if (!response.ok) throw Error('No se pudo actualizar la biblioteca de poses. Se conserva la versión anterior.');
                    const snapshot = validateAnimationSnapshot(await response.json());
                    if (!this.active || controller.signal.aborted) return;
                    this.snapshot = snapshot;
                    this.etag = response.headers.get('etag') || '"' + snapshot.revision + '"';
                })().finally(() => {
                    this.request = null; this._schedule();
                    if (this.again && this.active) { this.again = false; queueMicrotask(() => this.refresh()); }
                });
            } else {
                // An event may arrive while a response with an older snapshot is in flight.
                this.again = true;
            }
            await this.request;
            return await this.apply();
        } catch (error) {
            if (this.active && error.name !== 'AbortError') this._report(error.message);
        } finally {
            if (this.again && this.active && !this.request) { this.again = false; void this.refresh(); }
        }
    }

    async apply() {
        const idle = this.getIdle?.();
        if (!this.active || !idle) return;
        idle.refreshLibrary = () => this.refresh();
        if (!this.snapshot) return;
        const snapshot = this.snapshot;
        const result = await idle.applyManifest(snapshot, this.bridge?.native ? async resource => {
            const url = await this.bridge.coreAssetUrl(resource.url);
            return { url, release: () => this.bridge.releaseCoreAssetUrl(url) };
        } : null);
        if (!this.active || this.getIdle() !== idle || this.snapshot !== snapshot) return;
        if (result?.failed?.length) this._report('No se pudieron actualizar estas poses: ' + result.failed.join(', ') + '. Se conserva la versión válida.');
        else this.error = null;
        return result;
    }

    _report(message) {
        if (message !== this.error) { this.error = message; this.onError(message); }
    }

    dispose() {
        if (this.disposed) return;
        this.disposed = true;
        this.clearTimer(this.timer); this.timer = null;
        this.controller?.abort();
        this.document?.removeEventListener('visibilitychange', this._visibility);
        this.window?.removeEventListener('focus', this._focus);
        if (this.getIdle?.()) this.getIdle().refreshLibrary = null;
        this.snapshot = null;
    }
}
