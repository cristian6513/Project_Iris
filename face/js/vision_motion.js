const ROTATION_INDICES = Object.freeze([0, 1, 2, 4, 5, 6, 8, 9, 10]);
const TRANSLATION_INDICES = Object.freeze([12, 13, 14]);

function finiteMatrix(value) {
    if (!value || typeof value.length !== 'number' || value.length < 16) return null;
    const matrix = Array.from(value).slice(0, 16);
    return matrix.every(Number.isFinite) ? matrix : null;
}

/**
 * Classify coarse head motion from consecutive MediaPipe pose matrices.
 *
 * The output is deliberately small (still/low/active), contains no biometric
 * geometry and is stable across normal camera frame-rate variation.
 */
export class HeadMotionEstimator {
    constructor({
        stillThreshold = 0.3,
        activeThreshold = 1.2,
        smoothing = 0.45,
    } = {}) {
        this.stillThreshold = stillThreshold;
        this.activeThreshold = activeThreshold;
        this.smoothing = smoothing;
        this.reset();
    }

    reset() {
        this.previous = null;
        this.previousAt = 0;
        this.score = 0;
        this.instantaneous = null;
    }

    update(value, nowMs = performance.now()) {
        const matrix = finiteMatrix(value);
        const timestamp = Number(nowMs);
        this.instantaneous = null;
        if (!matrix || !Number.isFinite(timestamp)) { this.reset(); return 'still'; }
        if (!this.previous || timestamp <= this.previousAt || timestamp - this.previousAt > 500) {
            this.previous = matrix;
            this.previousAt = timestamp;
            this.score = 0;
            return 'still';
        }

        const elapsedSeconds = Math.max(1 / 60, Math.min(0.5, (timestamp - this.previousAt) / 1000));
        let rotationSquared = 0;
        for (const index of ROTATION_INDICES) {
            const delta = matrix[index] - this.previous[index];
            rotationSquared += delta * delta;
        }
        const rotationDelta = Math.sqrt(rotationSquared / ROTATION_INDICES.length);
        let translationSquared = 0;
        for (const index of TRANSLATION_INDICES) {
            const delta = matrix[index] - this.previous[index];
            translationSquared += delta * delta;
        }
        // MediaPipe translation is much larger than its normalized rotation
        // components, so scale it before combining both signals.
        const translationDelta = Math.min(Math.sqrt(translationSquared) / 12, 1);
        const instantaneous = (rotationDelta + translationDelta) / elapsedSeconds;
        this.instantaneous = instantaneous;
        this.score = this.score * (1 - this.smoothing) + instantaneous * this.smoothing;
        this.previous = matrix;
        this.previousAt = timestamp;

        if (this.score >= this.activeThreshold) return 'active';
        if (this.score >= this.stillThreshold) return 'low';
        return 'still';
    }
}

export { finiteMatrix };
