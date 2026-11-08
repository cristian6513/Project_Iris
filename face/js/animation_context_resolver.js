/**
 * Resolve an LLM-requested animation for a concrete IRIS surface.
 *
 * The LLM contract stays untouched: it still emits `animacion_virtual`.
 * This pure post-processing step adapts room-dependent actions only after
 * the transport event reaches a surface that lacks the referenced furniture.
 */

export const LEGACY_ANIMATION_ALIASES = Object.freeze({
    saludar_feliz: 'wave',
    pensar: 'think',
    asentir: 'nod',
    negar: 'shake',
});

export function normalizeAnimationName(value) {
    const requested = typeof value === 'string' ? value.trim() : '';
    return requested || 'idle';
}

export function animationContextsFromDocument(document = {}) {
    if (!document || typeof document !== 'object' || Array.isArray(document)) return {};
    const contexts = document.contexts && typeof document.contexts === 'object'
        ? document.contexts
        : document;
    return contexts && !Array.isArray(contexts) ? contexts : {};
}

export function resolveAnimationContext(requestedValue, {
    surface = 'face',
    contexts = {},
    aliases = LEGACY_ANIMATION_ALIASES,
} = {}) {
    const requestedName = normalizeAnimationName(requestedValue);
    const canonicalName = aliases[requestedName] || requestedName;
    const contextDocument = animationContextsFromDocument(contexts);
    const surfaceRules = contextDocument[surface];
    const rule = surfaceRules && typeof surfaceRules === 'object'
        ? (surfaceRules[requestedName] || surfaceRules[canonicalName])
        : null;

    const animation = typeof rule?.animation === 'string' && rule.animation.trim()
        ? rule.animation.trim()
        : canonicalName;
    const movement = typeof rule?.movement === 'string' && rule.movement.trim()
        ? rule.movement.trim()
        : null;
    const reason = typeof rule?.reason === 'string' ? rule.reason.trim() : '';

    return {
        requestedName,
        canonicalName,
        animation,
        movement,
        reason,
        adapted: animation !== canonicalName || movement !== null,
        surface,
    };
}

export async function loadAnimationContexts(
    url = '/face/animations/contexts.json',
    fetchImpl = globalThis.fetch,
) {
    if (typeof fetchImpl !== 'function') return {};
    try {
        const response = await fetchImpl(url, { cache: 'no-store' });
        if (!response?.ok) return {};
        return animationContextsFromDocument(await response.json());
    } catch {
        return {};
    }
}
