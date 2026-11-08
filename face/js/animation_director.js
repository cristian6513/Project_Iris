import { AvatarActivity } from './avatar_activity.js?v=1';

import {
    resolveAnimationContext,
} from './animation_context_resolver.js?v=1';

/**
 * Shared animation playback policy for Face and Desktop Companion.
 *
 * Dependencies are getters because Face can replace AvatarManager/IdleSystem
 * after a VRM upload. Holding the original instances here would silently route
 * future LLM gestures to a stale avatar.
 */
export class AnimationDirector {
    constructor({
        getIdle,
        getAvatarManager,
        onMood = () => {},
        surface = 'face',
        contexts = {},
        logger = console,
    } = {}) {
        this.getIdle = typeof getIdle === 'function' ? getIdle : () => null;
        this.getAvatarManager = typeof getAvatarManager === 'function'
            ? getAvatarManager
            : () => null;
        this.onMood = typeof onMood === 'function' ? onMood : () => {};
        this.surface = surface;
        this.contexts = contexts;
        this.logger = logger || console;
        this.activity = new AvatarActivity();
    }

    handleRuntimeEvent(type, payload) {
        this.activity.handle(type, payload);
    }

    resetActivity() {
        this.activity.reset();
        this.getIdle()?.setActivity?.('idle');
    }

    updateActivity() {
        this.getIdle()?.setActivity?.(this.activity.state);
    }

    setContexts(contexts = {}) {
        this.contexts = contexts;
    }

    async play(requestedName, { reload = true } = {}) {
        const resolution = resolveAnimationContext(requestedName, {
            surface: this.surface,
            contexts: this.contexts,
        });
        const name = resolution.animation;
        if (!name || name === 'idle') {
            return { ...resolution, status: 'idle' };
        }

        const idle = this.getIdle();
        const avatarManager = this.getAvatarManager();

        // Named local poses include safe procedural gestures and room actions
        // in Face. Companion context resolution runs before this point, so a
        // room-only name is never executed on the transparent surface.
        if (idle?.trigger?.(name)) {
            this.onMood(name);
            return { ...resolution, status: 'pose' };
        }

        let played = idle?.playClipExternal?.(name);
        if (played) {
            this.onMood(name);
            return { ...resolution, status: 'clip' };
        }

        const quickPoseFallback = {
            nod: 'subtle_nod',
        }[name] || null;
        if (quickPoseFallback && idle?.trigger?.(quickPoseFallback)) {
            this.onMood(quickPoseFallback);
            return { ...resolution, status: 'procedural-pose', fallback: quickPoseFallback };
        }
        if (name === 'shake') {
            avatarManager?.playAnimation?.('negar');
            return { ...resolution, status: 'procedural-avatar', fallback: 'negar' };
        }

        // The VRMA player initializes lazily. One manifest reload preserves
        // the existing fresh-boot recovery without duplicating it per surface.
        if (reload && idle?.reloadManifest) {
            try {
                this.logger.info?.(
                    `[animation] '${name}' did not play — forcing manifest reload and retrying.`,
                );
                await idle.reloadManifest();
                if (this.getIdle() !== idle) return { ...resolution, status: 'superseded' };
                played = idle.playClipExternal?.(name);
                if (played) {
                    this.onMood(name);
                    return { ...resolution, status: 'clip-after-reload' };
                }
                const hasPlayer = !!idle._vrmaPlayer;
                const knows = hasPlayer
                    ? idle._vrmaPlayer.hasAnimation?.(name)
                    : false;
                this.logger.warn?.(
                    `[animation] could not play '${name}'. ` +
                    `vrmaPlayer=${hasPlayer ? 'ready' : 'null (no humanoid VRM?)'}, ` +
                    `clipLoaded=${Boolean(knows)}.`,
                );
            } catch (error) {
                this.logger.warn?.('[animation] reloadManifest retry failed:', error);
            }
        }

        const poseFallbacks = {
            // thinking_pose is intentionally blocked without its optional
            // full-body VRMA. head_tilt remains a safe bone-only fallback in
            // native bundles that cannot redistribute that third-party clip.
            think: ['thinking_pose', 'head_tilt'],
            nod: ['subtle_nod'],
            // Full arm wave requires a baked clip. If it is unavailable,
            // keep the response visible with the rig-safe subtle nod instead
            // of AvatarManager's legacy `nod_big` (too aggressive on some
            // custom VRMs with long hair/accessories).
            wave: ['wave_hand', 'subtle_nod'],
            look_window: ['look_window'],
            look_around: ['look_around'],
        }[name] || [];
        for (const poseFallback of poseFallbacks) {
            if (!idle?.trigger?.(poseFallback)) continue;
            this.onMood(poseFallback);
            return { ...resolution, status: 'procedural-pose', fallback: poseFallback };
        }

        // Preserve a visible bone-only fallback for canonical names as well
        // as the old Spanish ids. In particular, `wave` must still produce a
        // friendly nod when VRMA loading is unavailable/offline; passing the
        // canonical id straight to AvatarManager used to no-op silently.
        const avatarFallback = {
            wave: 'saludar_feliz',
            think: 'pensar',
            nod: 'asentir',
            shake: 'negar',
        }[name] || resolution.requestedName;
        avatarManager?.playAnimation?.(avatarFallback);
        return { ...resolution, status: 'procedural-avatar', fallback: avatarFallback };
    }
}
