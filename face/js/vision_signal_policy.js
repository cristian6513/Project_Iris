/**
 * Build the privacy-safe presence signal sent to IRIS Core.
 *
 * `observation` must come from a completed MediaPipe inference. A missing or
 * stale observation means the analysis pipeline is unavailable, not that the
 * user is absent, so callers must not publish anything in that case.
 */
export function buildVisionSignal(observation, {
    nowMs = globalThis.performance?.now?.() ?? Date.now(),
    maxAgeMs = 1800,
} = {}) {
    const timestamp = Number(observation?.timestamp);
    if (!Number.isFinite(timestamp)) return null;
    const age = Number(nowMs) - timestamp;
    if (!Number.isFinite(age) || age < 0 || age > maxAgeMs) return null;
    if (typeof observation?.present !== 'boolean') return null;

    const present = observation.present;
    const measuredMotion = ['still', 'low', 'active'].includes(observation?.motion)
        ? observation.motion
        : present ? 'low' : 'still';
    return {
        present,
        confidence: present ? 0.92 : 0.7,
        motion: present ? measuredMotion : 'still',
    };
}
