/** Local inference pacing and voluntary noise calibration. No media storage or uploads. */
export const VISION_INTERVALS = Object.freeze({ economy: 200, balanced: 67, responsive: 34 });
export class VisionCaptureTuning {
    constructor() { this.enabled = false; this.performance = 'balanced'; this.reset(); }
    reset() {
        this.lastFrame = -Infinity; this.pauseUntil = 0; this.slowFrames = 0;
        this.state = 'stopped'; this.calibration = 'idle'; this.samples = [];
        this.startedAt = 0; this.progress = 0; this.stillThreshold = 0.3; this.activeThreshold = 1.2;
        this.lastSampleAt = null;
        this.firstSampleAt = null;
    }
    configure({ enabled = false, performance = 'balanced' } = {}) {
        if (this.enabled && !enabled) this.reset();
        this.enabled = enabled === true;
        this.performance = Object.hasOwn(VISION_INTERVALS, performance) ? performance : 'balanced';
    }
    get interval() { return this.enabled ? VISION_INTERVALS[this.performance] : 0; }
    startCalibration(now) {
        if (!this.enabled || this.calibration === 'collecting' || !Number.isFinite(now)) return false;
        this.samples = []; this.startedAt = now; this.progress = 0;
        this.lastSampleAt = null;
        this.firstSampleAt = null;
        this.calibration = 'collecting'; return true;
    }
    interrupt() {
        if (this.calibration === 'collecting') {
            this.calibration = 'insufficient'; this.samples = []; this.progress = 0;
        }
    }
    shouldProcess(now, visible = true) {
        if (this.enabled && !visible) {
            this.state = 'hidden'; this.interrupt(); return false;
        }
        if (now < this.pauseUntil) { this.state = 'overloaded'; return false; }
        this.state = this.calibration === 'collecting' ? 'calibrating' : 'observing';
        if (this.calibration === 'collecting' && now - this.startedAt >= 30000) this.interrupt();
        if (now - this.lastFrame < this.interval) return false;
        this.lastFrame = now; return true;
    }
    latency(ms, now) {
        this.slowFrames = ms >= 200 ? this.slowFrames + 1 : 0;
        if (this.enabled && this.slowFrames >= 3) {
            this.pauseUntil = now + 2000; this.slowFrames = 0;
            this.state = 'overloaded'; this.interrupt();
        }
    }
    sample(score, now) {
        if (this.calibration !== 'collecting') return;
        if (now - this.startedAt >= 30000) { this.interrupt(); return; }
        if (Number.isFinite(score)) {
            if (this.lastSampleAt != null && (now <= this.lastSampleAt || now - this.lastSampleAt > 500)) {
                this.interrupt(); return;
            }
            this.lastSampleAt = now;
            if (this.firstSampleAt === null) this.firstSampleAt = now;
        }
        if (Number.isFinite(score) && score >= 0 && this.samples.length < 1200) this.samples.push(score);
        const elapsed = now - this.startedAt;
        this.progress = Math.max(0, Math.min(99, Math.floor(elapsed / 150)));
        if (elapsed < 15000 || this.firstSampleAt === null || now - this.firstSampleAt < 14000) return;
        if (this.samples.length < 40) {
            if (elapsed >= 30000) { this.interrupt(); }
            return;
        }
        const sorted = this.samples.slice().sort((a, b) => a - b);
        const noise = sorted[Math.floor((sorted.length - 1) * 0.9)];
        this.samples = []; // Never retain scores after calibration.
        this.progress = 100;
        if (noise > 0.4) { this.calibration = 'noisy'; return; }
        this.stillThreshold = Math.max(0.3, Math.min(0.6, noise * 1.5));
        this.activeThreshold = Math.max(1.2, Math.min(2.4, this.stillThreshold * 4));
        this.calibration = 'ready';
    }
}
