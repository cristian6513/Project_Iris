import { validateTransition, themeTransitions } from './os_transition_format.js?v=1';

/** Per-element ownership; animation completion is never a navigation/close precondition. */
export class TransitionPlayer {
    constructor({ reduced = () => false, documentLike = globalThis.document, media = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)') } = {}) {
        this.reduced = reduced; this.document = documentLike; this.media = media;
        this.active = new Map(); this.disposed = false;
        this.onMotion = () => { if (this.media?.matches) this.cancelAll(); };
        this.onVisibility = () => { if (this.document?.visibilityState === 'hidden') this.cancelAll(); };
        this.media?.addEventListener?.('change', this.onMotion);
        this.document?.addEventListener?.('visibilitychange', this.onVisibility);
    }
    blocked() { return this.disposed || this.reduced() || this.media?.matches || this.document?.visibilityState === 'hidden'; }
    play(target, value) {
        this.cancel(target);
        if (this.blocked()) return { played: false, reason: 'reduced-or-hidden' };
        if (!target?.animate || target.isConnected === false) return { played: false, reason: 'unavailable' };
        let effect;
        try { effect = validateTransition(value); } catch { return { played: false, reason: 'invalid' }; }
        try {
            // No fill or commitStyles: cancellation and completion both reveal the original geometry.
            const animation = target.animate(effect.keyframes, { duration: effect.duration, easing: effect.easing, iterations: 1, fill: 'none' });
            this.active.set(target, animation);
            const release = () => {
                if (this.active.get(target) !== animation) return;
                this.active.delete(target);
                try { animation.cancel(); } catch { /* already released by the platform */ }
            };
            animation.finished.then(release, release);
            if (this.active.size > 32) this.cancel(this.active.keys().next().value);
            return { played: true };
        } catch { this.cancel(target); return { played: false, reason: 'unavailable' }; }
    }
    playTheme(target, theme) {
        const motion = themeTransitions(theme);
        const value = motion.transitionLibrary.find(item => `custom:${item.id}` === motion.desktopTransition);
        if (!value) { this.cancel(target); return { played: false, reason: 'builtin' }; }
        return this.play(target, value);
    }
    cancel(target) {
        const animation = this.active.get(target);
        this.active.delete(target);
        try { animation?.cancel(); } catch { /* removal must never block the shell */ }
    }
    cancelAll() { for (const target of this.active.keys()) this.cancel(target); }
    dispose() {
        this.disposed = true; this.cancelAll();
        this.media?.removeEventListener?.('change', this.onMotion);
        this.document?.removeEventListener?.('visibilitychange', this.onVisibility);
    }
}
