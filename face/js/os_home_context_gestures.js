/** Delegated context gestures. A touch scroll never becomes a context action. */
export class HomeContextGestures {
    constructor({ surfaces, resolve, open, documentLike = document, enabled = () => true,
        schedule = (fn, ms) => setTimeout(fn, ms), unschedule = (id) => clearTimeout(id), now = () => Date.now() }) {
        Object.assign(this, { resolve, open, document: documentLike, enabled, schedule, unschedule, now });
        this.abort = new AbortController(); this.pending = null; this.blocked = null;
        const listen = (node, type, fn, options = {}) => node?.addEventListener(type, fn, { ...options, signal: this.abort.signal });
        for (const surface of surfaces) {
            listen(surface, 'pointerdown', (ev) => this.start(ev));
            listen(surface, 'contextmenu', (ev) => this.context(ev));
            listen(surface, 'keydown', (ev) => {
                if (ev.key === 'ContextMenu' || (ev.shiftKey && ev.key === 'F10')) {
                    const target = this.enabled() && this.resolve(ev.target);
                    if (target) { ev.preventDefault(); this.cancel(); this.open(target); }
                }
            });
            // Capture before the app's ordinary click handler. A held icon must not launch.
            listen(surface, 'click', (ev) => {
                if (this.suppresses(ev.target)) { ev.preventDefault(); ev.stopImmediatePropagation(); this.blocked = null; }
            }, { capture: true });
            listen(surface, 'scroll', () => this.cancel(), { capture: true, passive: true });
        }
        listen(documentLike, 'pointermove', (ev) => this.move(ev), { passive: true });
        listen(documentLike, 'pointerup', (ev) => this.end(ev));
        listen(documentLike, 'pointercancel', (ev) => this.end(ev));
        listen(documentLike, 'pointerdown', (ev) => { if (this.pending && ev.pointerId !== this.pending.id) this.cancel(); }, { capture: true });
        listen(documentLike, 'visibilitychange', () => { if (documentLike.visibilityState === 'hidden') this.cancel(); });
        listen(documentLike.defaultView, 'blur', () => this.cancel());
        listen(documentLike.defaultView, 'resize', () => this.cancel());
    }
    suppresses(node) {
        if (!this.blocked || this.now() > this.blocked.until) return false;
        return this.blocked.element === node || this.blocked.element.contains?.(node);
    }
    start(ev) {
        this.cancel();
        if (!this.enabled() || !ev.isPrimary || ev.button !== 0 || !['touch', 'pen'].includes(ev.pointerType)) return;
        const target = this.resolve(ev.target); if (!target) return;
        const pending = { id: ev.pointerId, x: ev.clientX, y: ev.clientY, target, fired: false };
        this.pending = pending;
        pending.timer = this.schedule(() => {
            if (this.pending !== pending || !this.enabled() || !target.anchor.isConnected) return;
            pending.fired = true;
            this.blocked = { element: target.element, until: Infinity };
            this.open(target, { x: pending.x, y: pending.y });
        }, 550);
    }
    move(ev) {
        const p = this.pending;
        if (p && ev.pointerId === p.id && Math.hypot(ev.clientX - p.x, ev.clientY - p.y) > 10) this.cancel();
    }
    end(ev) { if (ev.pointerId === this.pending?.id) this.cancel(); }
    context(ev) {
        const target = this.enabled() && this.resolve(ev.target); if (!target) return;
        ev.preventDefault();
        if (this.suppresses(ev.target)) return;
        // Some touch browsers emit their native contextmenu before our hold timer.
        // Keep consuming that gesture until release instead of letting its click launch.
        if (this.pending?.target.element === target.element) {
            this.unschedule(this.pending.timer); this.pending.fired = true;
            this.blocked = { element: target.element, until: Infinity };
            this.open(target, { x: ev.clientX, y: ev.clientY }); return;
        }
        this.cancel();
        if (['touch', 'pen'].includes(ev.pointerType)) this.blocked = { element: target.element, until: this.now() + 1000 };
        this.open(target, ev.clientX || ev.clientY ? { x: ev.clientX, y: ev.clientY } : undefined);
    }
    cancel() {
        if (this.pending) this.unschedule(this.pending.timer);
        this.pending = null;
        if (this.blocked?.until === Infinity) this.blocked.until = this.now() + 1000;
    }
    dispose() { this.cancel(); this.blocked = null; this.abort.abort(); }
}
