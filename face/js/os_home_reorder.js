/** Direct rearranging, as on a phone or the Windows desktop: drag an icon or widget and the others
 * make room. Pointer-only presentation; HomePreferences remains the sole persistence owner. */
const MOUSE_SLOP = 6, TOUCH_SLOP = 10, HOLD_MS = 550, CLICK_GUARD_MS = 400;
// Pressing these keeps their own meaning: typing, sliders, and the edit-mode buttons.
const OWN_GESTURE = 'input, select, textarea, [contenteditable="true"], [contenteditable=""], [role="slider"], .os-home-item-controls';

export class HomeReorder {
    constructor({ list, model, widgetIds, enabled, announce, onLift = () => {}, onSettle = () => {}, documentLike = document,
        frame = (fn) => requestAnimationFrame(fn), cancelFrame = (id) => cancelAnimationFrame(id),
        schedule = (fn, ms) => setTimeout(fn, ms), unschedule = (id) => clearTimeout(id), now = () => Date.now() }) {
        Object.assign(this, { list, model, widgetIds, enabled, announce, onLift, onSettle, document: documentLike,
            frame, cancelFrame, schedule, unschedule, now });
        this.abort = new AbortController(); this.drag = null; this.pending = null; this.frameId = null;
        this.settling = false; this.disposed = false; this.guardUntil = 0; this.cancelledPointer = null;
        const bind = (node, name, fn, options = {}) => node?.addEventListener(name, fn, { ...options, signal: this.abort.signal });
        bind(list, 'pointerdown', (ev) => this.press(ev));
        // A link or image inside a widget would start the browser's own drag and steal the pointer.
        bind(list, 'dragstart', (ev) => { if (ev.target.closest?.('[data-home-ref]')) ev.preventDefault(); });
        // After a touch hold the finger moves the item, not the page.
        bind(list, 'touchmove', (ev) => { if (this.drag || this.pending?.armed) ev.preventDefault(); }, { passive: false });
        bind(list, 'click', (ev) => {
            if (this.guardUntil && this.now() <= this.guardUntil) { this.guardUntil = 0; ev.preventDefault(); ev.stopImmediatePropagation(); }
        }, { capture: true });
        bind(this.document, 'pointermove', (ev) => this.move(ev));
        bind(this.document, 'pointerup', (ev) => this.finish(ev));
        bind(this.document, 'pointercancel', (ev) => {
            if (ev.pointerId === this.drag?.pointerId || ev.pointerId === this.pending?.pointerId) this.cancel(!!this.drag);
            if (ev.pointerId === this.cancelledPointer) this.cancelledPointer = null;  // no click follows a cancelled pointer
        });
        bind(this.document, 'keydown', (ev) => { if (ev.key === 'Escape' && this.drag) { ev.preventDefault(); ev.stopPropagation(); this.cancel(); } });
        bind(this.document.defaultView, 'blur', () => this.cancel(!!this.drag));
        bind(this.document, 'visibilitychange', () => { if (this.document.visibilityState === 'hidden') this.cancel(!!this.drag); });
    }
    items() { return [...this.list.children].filter((node) => node.dataset?.homeRef); }
    reducedMotion() {
        // Either Windows or Theme Studio → Reducir movimiento (the panel's data-reduced-motion) turns the effects off.
        return !!(this.document.defaultView?.matchMedia?.('(prefers-reduced-motion: reduce)').matches
            || this.list.closest?.('[data-reduced-motion="true"]')
            || this.document.documentElement?.dataset?.irisReducedMotion === 'true');
    }
    press(ev) {
        if (ev.button !== 0 || ev.isPrimary === false || this.drag || !this.enabled()) return;
        this.cancelledPointer = null;  // a new press: an older cancelled gesture is over
        const item = ev.target.closest?.('[data-home-ref]');
        if (!item || !this.list.contains(item)) return;
        const handle = ev.target.closest('[data-home-drag]');
        if (!handle && (ev.target.closest(OWN_GESTURE) || onScrollbar(ev))) return;
        this.release();
        const touch = ev.pointerType === 'touch';
        // A mouse drags once it moves; a finger first holds still, so a swipe still scrolls the home.
        const pending = { pointerId: ev.pointerId, item, handle, touch, armed: !touch, x: ev.clientX, y: ev.clientY };
        this.pending = pending;
        if (handle) { ev.preventDefault(); handle.focus?.(); this.lift(); }
        else if (touch) pending.timer = this.schedule(() => { if (this.pending === pending) pending.armed = true; }, HOLD_MS);
    }
    release() {
        if (this.pending) this.unschedule(this.pending.timer);
        this.pending = null;
    }
    lift() {
        const { pointerId, item, handle, x, y } = this.pending; this.release();
        const revision = this.model.layout?.revision;
        if (!this.enabled() || !Number.isSafeInteger(revision)) return;
        const items = this.items(), box = item.getBoundingClientRect();
        this.drag = { pointerId, item, handle, revision, ref: item.dataset.homeRef, order: items.map((node) => node.dataset.homeRef),
            from: items.indexOf(item), grab: { x: x - box.left, y: y - box.top }, x, y, over: null };
        item.classList.add('os-home-dragging'); this.list.classList.add('os-home-reordering');
        this.document.getSelection?.()?.removeAllRanges?.();
        this.onLift();
        this.announce('Suelta donde quieras dejarlo. Escape cancela.');
        this.follow();
    }
    move(ev) {
        const pending = this.pending;
        if (pending && ev.pointerId === pending.pointerId) {
            const distance = Math.hypot(ev.clientX - pending.x, ev.clientY - pending.y);
            if (!pending.armed) { if (distance > TOUCH_SLOP) this.release(); return; }
            if (distance <= (pending.touch ? TOUCH_SLOP : MOUSE_SLOP)) return;
            this.lift();
        }
        const drag = this.drag;
        if (!drag || ev.pointerId !== drag.pointerId) return;
        if (!this.enabled() || this.model.layout?.revision !== drag.revision) { this.cancel(); return; }
        ev.preventDefault(); drag.x = ev.clientX; drag.y = ev.clientY;
        this.follow(); this.scrollFrame();
    }
    follow() {
        const drag = this.drag; if (!drag) return;
        // The dragged item ignores the pointer (CSS), so this finds what lies beneath it.
        const hit = this.document.elementFromPoint(drag.x, drag.y)?.closest?.('[data-home-ref]');
        const over = hit && hit !== drag.item && this.list.contains(hit) ? hit : null;
        // Only a newly entered item moves the gap: an item of another size cannot make it flicker.
        if (over !== drag.over) {
            drag.over = over;
            if (over) this.shift(() => {
                const items = this.items();
                if (items.indexOf(over) > items.indexOf(drag.item)) over.after(drag.item); else over.before(drag.item);
            });
        }
        this.track();
    }
    track() {
        const { item, x, y, grab } = this.drag;
        item.style.transform = '';
        const box = item.getBoundingClientRect();
        item.style.transform = `translate(${Math.round(x - grab.x - box.left)}px, ${Math.round(y - grab.y - box.top)}px) scale(1.04)`;
    }
    shift(change) {
        const drag = this.drag, others = this.items().filter((node) => node !== drag.item);
        const before = others.map((node) => node.getBoundingClientRect());
        change();
        if (this.reducedMotion()) return;
        others.forEach((node, index) => {
            const after = node.getBoundingClientRect(), dx = before[index].left - after.left, dy = before[index].top - after.top;
            if ((dx || dy) && typeof node.animate === 'function') {
                node.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], { duration: 160, easing: 'ease-out' });
            }
        });
    }
    scrollFrame() {
        if (this.frameId !== null || !this.drag) return;
        this.frameId = this.frame(() => {
            this.frameId = null; if (!this.drag) return;
            const box = this.list.getBoundingClientRect(), { x, y } = this.drag;
            const before = this.list.scrollTop;
            if (x >= box.left && x <= box.right && y >= box.top && y <= box.bottom) {
                const delta = y < box.top + 38 ? -10 : y > box.bottom - 38 ? 10 : 0;
                this.list.scrollTop += delta;
                if (delta && this.list.scrollTop !== before) { this.follow(); this.scrollFrame(); }
            }
        });
    }
    finish(ev) {
        if (ev.pointerId === this.cancelledPointer) {
            // Escape or blur ended the drag while the button was still down: that release opens nothing either.
            this.cancelledPointer = null; this.guardUntil = this.now() + CLICK_GUARD_MS; return;
        }
        if (this.pending && ev.pointerId === this.pending.pointerId) { this.release(); return; }  // an ordinary click
        const drag = this.drag; if (!drag || ev.pointerId !== drag.pointerId) return;
        drag.x = ev.clientX; drag.y = ev.clientY;
        const valid = this.enabled() && this.model.layout?.revision === drag.revision;
        if (valid) this.follow();
        const index = this.items().indexOf(drag.item);
        const target = valid && index >= 0 && index !== drag.from ? drag.order[index] : null;
        // The release over the item must not also open it.
        this.guardUntil = this.now() + CLICK_GUARD_MS;
        this.cancel(false, !target, true);
        if (target) {
            // The new order stays on screen while it saves; the home re-syncs with the saved one after.
            this.settling = true;
            void this.model.place(drag.ref, target, this.widgetIds(), drag.revision).catch(() => false).then((saved) => {
                this.settling = false; if (this.disposed) return;
                this.onSettle();
                this.announce(saved ? 'Orden guardado.' : 'No se cambió el orden. Revisa el aviso del inicio.');
            });
        } else this.announce('Movimiento cancelado.');
        if (drag.handle?.isConnected && !drag.handle.disabled) drag.handle.focus();
    }
    cancel(notify = true, restore = true, released = false) {
        this.release();
        if (this.frameId !== null) this.cancelFrame(this.frameId); this.frameId = null;
        const drag = this.drag; if (!drag) return;
        this.drag = null;
        if (!released) this.cancelledPointer = drag.pointerId;
        drag.item.classList.remove('os-home-dragging'); this.list.classList.remove('os-home-reordering');
        const offset = drag.item.style.transform; drag.item.style.transform = '';
        if (restore) this.restore(drag);
        else if (offset && !this.reducedMotion() && typeof drag.item.animate === 'function') {
            drag.item.animate([{ transform: offset }, { transform: 'none' }], { duration: 140, easing: 'ease-out' });
        }
        if (notify) this.announce('Movimiento cancelado.');
    }
    restore(drag) {
        const items = this.items();
        if (items.length === drag.order.length && items.every((node, index) => node.dataset.homeRef === drag.order[index])) return;
        const byRef = new Map(items.map((node) => [node.dataset.homeRef, node]));
        this.list.append(...drag.order.map((ref) => byRef.get(ref)).filter(Boolean));
    }
    dispose() { this.disposed = true; this.cancel(false); this.abort.abort(); }
}

function onScrollbar(ev) {
    // A press on a widget's own scrollbar scrolls it; it does not pick the widget up.
    const node = ev.target;
    return node?.clientWidth > 0 && (ev.offsetX > node.clientWidth || ev.offsetY > node.clientHeight);
}
