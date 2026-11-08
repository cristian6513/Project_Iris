/** Privacy contract for the optional Desktop Companion camera.
 *
 * Keeping the capture constraints and the derived payload in a pure module
 * makes it difficult for later UI work to accidentally add microphone input
 * or forward a frame through the shared WebSocket.
 */

export const COMPANION_CAMERA_CONSTRAINTS = Object.freeze({
    video: Object.freeze({
        width: Object.freeze({ ideal: 640 }),
        height: Object.freeze({ ideal: 480 }),
        frameRate: Object.freeze({ ideal: 15, max: 24 }),
    }),
    audio: false,
});

const clamp = (value, min, max, fallback) => {
    const number = Number(value);
    return Number.isFinite(number) ? Math.min(max, Math.max(min, number)) : fallback;
};

export function buildCompanionEmotionPayload(snapshot = {}) {
    const label = String(snapshot?.label || 'neutral')
        .trim()
        .toLowerCase()
        .replace(/[^a-z_-]/g, '')
        .slice(0, 24) || 'neutral';
    return {
        label,
        valence: clamp(snapshot?.valence, -1, 1, 0),
        arousal: clamp(snapshot?.arousal, 0, 1, 0.3),
        confidence: clamp(snapshot?.confidence, 0, 1, 0),
        intensity: clamp(snapshot?.intensity, 0, 1, 0),
    };
}
