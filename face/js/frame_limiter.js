/** A shared render budget; 0 means the display's requestAnimationFrame cadence. */
export function renderLimit(value) { return [0, 30, 60].includes(value) ? value : 60; }

export class FrameLimiter {
    constructor(limit = 60) { this.setLimit(limit); }
    setLimit(value) { this.limit = renderLimit(value); this.last = null; }
    ready(now, visible = true) {
        if (!visible) { this.last = null; return false; }
        if (!Number.isFinite(now)) return false;
        if (this.limit === 0) { this.last = now; return true; }
        const interval = 1000 / this.limit;
        const tolerance = 2; // rAF delivery jitters around a display refresh boundary.
        if (this.last === null || now < this.last - tolerance) { this.last = now; return true; }
        const elapsed = now - this.last;
        if (elapsed + tolerance < interval) return false;
        // Retain fractional time so 144/165 Hz displays can still average 30/60.
        // The budget may be up to tolerance ms ahead after an early frame;
        // don't treat that as a clock reset or render the next callback again.
        this.last = elapsed > 250 ? now : this.last + Math.max(1, Math.floor(elapsed / interval)) * interval;
        return true;
    }
}
