/** One-shot, passive presentation. Never opens UI, acquires sensors or starts voice. */
export class VisionPresentation {
    constructor({ now = () => Date.now() / 1000 } = {}) {
        this.now = now;
        this.seen = new Set();
    }

    async present(payload, { surfaceId, vision, status, documentLike,
        quiet = false, reducedMotion = false, idle, director } = {}) {
        const now = this.now();
        if (!payload || !/^[a-f0-9]{32}$/.test(payload.id || '')
            || payload.surface_id !== surfaceId || payload.kind !== 'returned'
            || payload.animation !== 'wave' || payload.delivery !== 'inbox'
            || payload.contains_media !== false || !Number.isFinite(payload.created_at)
            || !Number.isFinite(payload.expires_at) || !Number.isFinite(now)
            || payload.created_at > now || payload.expires_at <= now
            || payload.expires_at - payload.created_at > 8 || this.seen.has(payload.id)) return false;
        // Even a suppressed receipt cannot be replayed when the surface becomes free.
        this.seen.add(payload.id);
        if (this.seen.size > 256) this.seen.delete(this.seen.values().next().value);
        const field = documentLike?.activeElement;
        const editable = field?.isContentEditable || ['TEXTAREA', 'INPUT'].includes(field?.tagName);
        if (vision?.enabled !== true || vision.paused === true
            || vision.experience?.greetings === false || vision.experience?.profile === 'silent'
            || Number(vision.paused_until || 0) > now
            || documentLike?.visibilityState !== 'visible' || editable || quiet || reducedMotion
            || !['idle', 'ready'].includes(status)
            || typeof idle?.isExplicitAnimationActive !== 'function'
            || idle.isExplicitAnimationActive() || typeof director?.play !== 'function') return false;
        try {
            // Ready clips or existing procedural fallback only; no delayed manifest reload
            // which could start a gesture after a newer Hermes command.
            await director.play('wave', { reload: false });
            return true;
        } catch {
            return false; // Inbox remains available; no automatic retry.
        }
    }
}
