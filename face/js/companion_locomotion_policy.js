const BUSY_STATES = new Set([
    'listening',
    'recording',
    'processing',
    'thinking',
    'tool',
    'tool_running',
    'speaking',
    'error',
    'offline',
]);

/**
 * Keep native window movement opt-in and subordinate to the assistant state.
 * The avatar animation is intentionally outside this policy: Hermes' JSON
 * remains authoritative, while only the desktop-window translation is gated.
 */
export function companionLocomotionDecision({
    enabled = false,
    reducedMotion = false,
    quietMode = false,
    hovered = false,
    visible = true,
    native = false,
    operationalState = 'idle',
} = {}) {
    if (!enabled) return { allowed: false, reason: 'disabled' };
    if (!native) return { allowed: false, reason: 'native-required' };
    if (reducedMotion) return { allowed: false, reason: 'reduced-motion' };
    if (quietMode) return { allowed: false, reason: 'quiet-mode' };
    if (hovered) return { allowed: false, reason: 'pointer-hover' };
    if (!visible) return { allowed: false, reason: 'hidden' };

    const state = String(operationalState || 'idle').trim().toLowerCase();
    if (BUSY_STATES.has(state)) return { allowed: false, reason: `state:${state}` };
    return { allowed: true, reason: 'ready' };
}

export function companionLocomotionStatus(preferences = {}) {
    if (!preferences.companion_locomotion_enabled) {
        return { code: 'OFF', detail: 'La ventana permanece fija.' };
    }
    if (preferences.companion_reduced_motion) {
        return { code: 'BLOCKED', detail: 'Movimiento reducido tiene prioridad.' };
    }
    if (preferences.companion_quiet_mode) {
        return { code: 'QUIET', detail: 'Quiet mode bloquea el desplazamiento.' };
    }
    return { code: 'READY', detail: 'Responderá solo a animaciones compatibles.' };
}
