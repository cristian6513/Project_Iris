/**
 * IRIS Camera Mocap — MediaPipe Face Landmarker wrapper
 *
 * Wraps Google's MediaPipe Tasks Vision so IRIS can read the user's
 * face pose + expressions straight off the webcam stream. The heavy
 * lift (neural net inference) runs in WASM inside the browser — the
 * frame never leaves the device.
 *
 * What we extract per frame:
 *   • `headMatrix`   — 4×4 transform used to summarize local head motion.
 *                      It never drives the avatar's bones or expressions.
 *   • `blendshapes`  — 52 ARKit-style coefficients (eyeBlinkLeft,
 *                      mouthSmileRight, browDownLeft, …). Each 0-1.
 *   • `timestamp`    — the performance.now() of the frame that
 *                      produced this result, so the rigger can skip
 *                      stale data when the camera lags.
 *
 * Usage:
 *      const cam = new CameraMocap();
 *      await cam.start(videoEl);           // requires a live stream
 *      // inside your render loop:
 *      const r = cam.latest();
 *      if (r) classifyLocalObservation(r);
 *      cam.stop();                          // retains the cached model
 *
 * This module does NOT own the MediaStream — panel.js already handles
 * getUserMedia and pipes the stream into the <video> element. We just
 * sample from that video each frame.
 */

import { HeadMotionEstimator } from './vision_motion.js?v=2';
import { VisionCaptureTuning } from './vision_capture_tuning.js?v=1';

// Vision is an optional local capability. Missing prepared resources leave
// analysis unavailable and retryable; opening the camera never fetches a CDN.
const LOCAL_BASE = '/face/vendor/mediapipe/';

// Module-level caches so reopening the camera doesn't re-download the
// WASM + model.
let _visionPromise = null;
let _faceLandmarker = null;


export async function loadMediaPipe({ moduleLoader, wasmBase, modelUrl } = {}) {
    if (_faceLandmarker) return _faceLandmarker;
    if (!_visionPromise) {
        _visionPromise = (async () => {
            const localModule = LOCAL_BASE + 'vision_bundle.mjs';
            const mod = moduleLoader
                ? await moduleLoader(localModule)
                : await import(/* @vite-ignore */ localModule);
            const defaultWasm = LOCAL_BASE + 'wasm';
            const defaultModel = LOCAL_BASE + 'face_landmarker.task';
            const { FaceLandmarker, FilesetResolver } = mod;
            if (!FaceLandmarker || !FilesetResolver) {
                throw new Error('MediaPipe vision_bundle missing FaceLandmarker');
            }
            const fileset = await FilesetResolver.forVisionTasks(wasmBase || defaultWasm);
            // Try GPU first (big speedup on most machines), fall back to
            // CPU if the GPU path errors (common on older integrated GPUs
            // or browsers without WebGPU).
            for (const delegate of ['GPU', 'CPU']) {
                try {
                    _faceLandmarker = await FaceLandmarker.createFromOptions(fileset, {
                        baseOptions: {
                            modelAssetPath: modelUrl || defaultModel,
                            delegate,
                        },
                        outputFaceBlendshapes: true,
                        outputFacialTransformationMatrixes: true,
                        runningMode: 'VIDEO',
                        numFaces: 1,
                    });
                    console.info(`[mocap] MediaPipe Face Landmarker ready (delegate: ${delegate})`);
                    return _faceLandmarker;
                } catch (e) {
                    console.warn(`[mocap] ${delegate} delegate failed, falling back:`, e);
                }
            }
            throw new Error('MediaPipe failed with both GPU and CPU delegates');
        })();
    }
    try {
        return await _visionPromise;
    } catch (error) {
        // A transient local resource or delegate failure must be retryable without
        // forcing the user to reload all of IRIS OS.
        _visionPromise = null;
        throw error;
    }
}


export class CameraMocap {
    constructor(options = {}) {
        this.options = options;
        /** @type {HTMLVideoElement|null} */
        this.videoEl = null;
        this.running = false;
        this._raf = 0;
        this._lastTs = -1;

        /**
         * Most recent inference result, or null if we haven't produced
         * one yet (or camera was just stopped). Shape:
         *   { headMatrix: Float32Array(16),
         *     blendshapes: { [categoryName]: score },
         *     timestamp: DOMHighResTimeStamp }
         */
        this.result = null;

        // Latest completed inference, including valid "no face" frames.
        // This is intentionally separate from `result`, which retains the
        // previous face briefly so the avatar can decay smoothly.
        this.presenceObservation = null;
        this.motionEstimator = new HeadMotionEstimator(options.motion || {});
        this.tuning = new VisionCaptureTuning();
        this._generation = 0;

        // Diagnostic counters — surfaced in the side panel for debugging.
        this.stats = { frames: 0, misses: 0, lastLatencyMs: 0 };
    }

    /**
     * Attach to a `<video>` that already has a live MediaStream playing
     * and start sampling frames. Returns once the MediaPipe model is
     * loaded and the first frame has been requested.
     */
    async start(videoEl) {
        if (this.running) return;
        const generation = ++this._generation;
        if (!videoEl || !(videoEl instanceof HTMLVideoElement)) {
            throw new Error('[mocap] start() needs an HTMLVideoElement with a live stream');
        }
        this.videoEl = videoEl;

        // Wait for the first frame so detectForVideo doesn't barf on a
        // 0×0 texture. readyState ≥ 2 means we have enough data.
        if (videoEl.readyState < 2) {
            await new Promise((resolve) => {
                const onReady = () => { videoEl.removeEventListener('loadeddata', onReady); resolve(); };
                videoEl.addEventListener('loadeddata', onReady, { once: true });
                // Safety timeout — if the stream never fires loadeddata
                // we still want to attempt inference; the first frame or
                // two may just be black.
                setTimeout(onReady, 1200);
            });
        }

        if (generation !== this._generation) return;
        await loadMediaPipe(this.options);
        if (generation !== this._generation) return;
        this.running = true;
        this.stats = { frames: 0, misses: 0, lastLatencyMs: 0 };
        this._lastTs = -1;
        this.presenceObservation = null;
        this.motionEstimator.reset();
        this._loop();
    }

    /**
     * Stop sampling. The MediaPipe model stays loaded in memory so a
     * later start() resumes instantly. Call `dispose()` to free it.
     */
    stop() {
        this._generation += 1;
        this.running = false;
        if (this._raf) cancelAnimationFrame(this._raf);
        this._raf = 0;
        this.videoEl = null;
        this.result = null;
        this.presenceObservation = null;
        this.motionEstimator.reset();
        this.tuning.reset();
    }

    /**
     * Fully release the MediaPipe face landmarker and model memory.
     * Safe to call even if stop() hasn't been called yet.
     */
    dispose() {
        this.stop();
        try { _faceLandmarker?.close?.(); } catch { /* noop */ }
        _faceLandmarker = null;
        _visionPromise = null;
    }

    /**
     * Convenience accessor for the render loop — returns the latest
     * result or null if nothing usable has arrived yet.
     */
    latest() {
        return this.result;
    }

    /** Return only a successful, completed presence inference. */
    latestPresence() {
        if (this.tuning.calibration === 'collecting'
            || ['hidden', 'overloaded'].includes(this.tuning.state)) return null;
        return this.presenceObservation;
    }

    configureVision(vision = {}) {
        this.tuning.configure({
            enabled: vision.enabled === true && Number(vision.paused_until || 0) * 1000 <= Date.now(),
            performance: vision.experience?.performance,
        });
    }

    calibrate(action) {
        if (!this.running || !this.tuning.enabled || document.visibilityState !== 'visible') return false;
        if (action === 'reset') { this.tuning.reset(); this.motionEstimator.reset(); return true; }
        if (action !== 'start') return false;
        const started = this.tuning.startCalibration(performance.now());
        if (started) { this.presenceObservation = null; this.motionEstimator.reset(); }
        return started;
    }

    diagnostics() {
        const tune = this.tuning;
        const age = performance.now() - (this.presenceObservation?.timestamp ?? -Infinity);
        const state = !this.running ? 'stopped'
            : tune.state === 'observing' && !(age >= 0 && age <= 3000) ? 'unavailable' : tune.state;
        return {
            frames: Math.min(1e9, this.stats.frames), misses: Math.min(1e9, this.stats.misses),
            latency_ms: Math.min(60000, Math.max(0, this.stats.lastLatencyMs)),
            interval_ms: tune.interval, state,
            calibration: tune.calibration, progress: tune.progress,
            still_threshold: tune.stillThreshold, active_threshold: tune.activeThreshold,
        };
    }

    // ── Private ────────────────────────────────────────────

    _loop() {
        if (!this.running) return;
        this._raf = requestAnimationFrame(() => this._processFrame());
    }

    _processFrame() {
        if (!this.running || !this.videoEl || !_faceLandmarker) {
            this._loop();
            return;
        }

        const video = this.videoEl;
        const frameAt = performance.now();
        if (!this.tuning.shouldProcess(frameAt, document.visibilityState === 'visible')) {
            if (['hidden', 'overloaded'].includes(this.tuning.state)) {
                this.presenceObservation = null;
                this.motionEstimator.reset();
            }
            this._loop();
            return;
        }

        // Skip if the browser hasn't decoded a new frame yet — MediaPipe
        // requires strictly monotonic timestamps, and spamming the same
        // frame wastes CPU.
        const ts = video.currentTime * 1000;
        if (ts === this._lastTs || video.readyState < 2 || video.videoWidth === 0) {
            this._loop();
            return;
        }
        this._lastTs = ts;

        const t0 = performance.now();
        let result;
        try {
            result = _faceLandmarker.detectForVideo(video, t0);
        } catch (e) {
            this.stats.misses += 1;
            this.presenceObservation = null;
            this.motionEstimator.reset();
            this.tuning.interrupt();
            // Common case: the video texture was reset (user toggled cam
            // off then on very quickly). Just skip and try again.
            this._loop();
            return;
        }
        this.stats.lastLatencyMs = performance.now() - t0;
        this.tuning.latency(this.stats.lastLatencyMs, performance.now());

        const hasFace =
            result?.faceBlendshapes?.length > 0 &&
            result?.facialTransformationMatrixes?.length > 0;
        if (!hasFace) {
            this.motionEstimator.reset();
            this.tuning.interrupt();
            this.presenceObservation = { present: false, timestamp: t0, motion: 'still' };
            // User moved out of frame or face is heavily occluded — keep
            // the previous result so the avatar doesn't snap to neutral
            // on a single dropped frame. If the gap grows the rigger
            // will decay toward rest on its own.
            this.stats.misses += 1;
            this._loop();
            return;
        }

        // Reduce the 52 ARKit categories to a flat { name: score } map —
        // much faster to look up by name in the rigger than iterating a
        // list every frame.
        const cats = result.faceBlendshapes[0].categories;
        const blendshapes = Object.create(null);
        for (let i = 0; i < cats.length; i++) {
            blendshapes[cats[i].categoryName] = cats[i].score;
        }

        const headMatrix = result.facialTransformationMatrixes[0].data;
        this.motionEstimator.stillThreshold = this.tuning.stillThreshold;
        this.motionEstimator.activeThreshold = this.tuning.activeThreshold;
        const motion = this.motionEstimator.update(headMatrix, t0);
        this.tuning.sample(this.motionEstimator.instantaneous, t0);
        this.presenceObservation = { present: true, timestamp: t0, motion };
        this.result = {
            headMatrix,
            blendshapes,
            timestamp: t0,
        };
        this.stats.frames += 1;

        this._loop();
    }
}

