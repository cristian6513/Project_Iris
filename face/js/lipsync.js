/**
 * IRIS Lip Sync Engine — Full Implementation
 *
 * Two modes:
 * 1. Viseme-driven: Maps TTS word boundary / viseme data → avatar mouth shapes
 * 2. Audio-reactive:  Analyses audio amplitude via Web Audio API → mouth shapes
 *
 * Both modes produce a single 0–1 value via avatar.setMouthOpen(), and, when the spoken text is
 * known (each word's, or the sentence's), the vowel shape via avatar.setMouthShape() (vowel_track.js).
 */

import { VISEMES, layout, mouthAt, vowelUnits } from './vowel_track.js?v=1';

export class LipSyncEngine {
    /**
     * @param {import('./avatar.js').AvatarManager} avatar
     */
    constructor(avatar) {
        this.avatar = avatar;

        // Viseme queue (from TTS word boundaries)
        this.visemeQueue = [];
        this.visemeStartTime = 0;
        this.visemeActive = false;

        // Audio-reactive state
        this.audioContext = null;
        this.analyser = null;
        this.audioSource = null;
        this.audioReactive = false;

        // Smoothing
        this._smoothedMouth = 0;

        // Vowel shapes (vowel_track.js): the sentence's track while the audio leads.
        this.audioTrack = null;
        this.audioElement = null;
        this._targetShape = null;
        this._shape = null;
    }

    // ── Viseme-driven lip sync ──────────────────────────

    /**
     * Process viseme / word-boundary data from TTS.
     * Called when "lip_sync" message arrives over WebSocket.
     *
     * @param {Array<{time:number, duration?:number, text?:string, viseme?:string}>} visemes
     */
    processVisemes(visemes) {
        this.stopAudioReactive();
        this.visemeQueue = visemes.map(v => {
            const units = vowelUnits(v.text || '');
            return {
                time:      v.time,
                duration:  v.duration || 0.18,
                intensity: this._textToIntensity(v.text || ''),
                track:     units.length ? layout(units) : null,
            };
        });
        this.visemeStartTime = performance.now() / 1000;
        this.visemeActive = true;
    }

    /**
     * Heuristic: vowels open the mouth more, long words slightly less.
     */
    _textToIntensity(text) {
        if (!text) return 0.5;
        const vowels = (text.match(/[aeiouáéíóúü]/gi) || []).length;
        const total  = text.length || 1;
        return Math.min(1, 0.2 + (vowels / total) * 0.8);
    }

    stop() {
        this.visemeActive = false;
        this.visemeQueue = [];
        this._smoothedMouth = 0;
        this._targetShape = this._shape = null;
        if (this.avatar) this.avatar.setMouthOpen(0);
        this.avatar?.setMouthShape?.(null);
    }

    isDrivingMouth() {
        return this.visemeActive || (this.audioReactive && !!this.analyser);
    }

    // ── Audio-reactive lip sync ─────────────────────────

    /**
     * Start analysing an <audio> element or MediaStream for amplitude.
     *
     * @param {HTMLAudioElement|MediaStream} source
     * @param {{text?: string}} [options] what she says in it, for the vowel shapes
     */
    startAudioReactive(source, { text = '' } = {}) {
        const units = vowelUnits(text);
        this.audioTrack = units.length ? layout(units) : null;
        this.audioElement = typeof HTMLAudioElement !== 'undefined' && source instanceof HTMLAudioElement ? source : null;
        if (!this.audioContext) {
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (this.audioContext.state === 'suspended') {
            this.audioContext.resume?.().catch?.(() => {});
        }

        this.analyser = this.audioContext.createAnalyser();
        this.analyser.fftSize = 512;
        this.analyser.smoothingTimeConstant = 0.6;

        if (source instanceof HTMLAudioElement) {
            // Prevent double-connect (each element can only be connected once)
            if (!source._irisConnected) {
                this.audioSource = this.audioContext.createMediaElementSource(source);
                this.audioSource.connect(this.analyser);
                this.analyser.connect(this.audioContext.destination);
                source._irisConnected = true;

                // Also tee the same audio into a MediaStreamDestination
                // so the WebRTC Host (webrtc_host.js) can forward
                // IRIS's voice to a phone running in Remote mode.
                // Without this tap the phone receives video only, which
                // is the whole point of "modo local — usar parlantes".
                // We stash the stream on `window` because the
                // HostClient is created lazily from the side panel.
                try {
                    if (!window._irisTTSStreamDest) {
                        const dest = this.audioContext.createMediaStreamDestination();
                        this.audioSource.connect(dest);
                        window._irisTTSStreamDest = dest;
                        window._irisTTSStream = dest.stream;
                        // If a Host is already running, retrofit it
                        // with the new audio stream.
                        try { window._irisHostClient?.setAudioStream?.(dest.stream); }
                        catch { /* ignore */ }
                    }
                } catch (err) {
                    console.warn('[lipsync] failed to create TTS stream destination:', err);
                }
            }
        } else if (source instanceof MediaStream) {
            this.audioSource = this.audioContext.createMediaStreamSource(source);
            this.audioSource.connect(this.analyser);
            // Don't connect to destination — avoids echo
        }

        this.audioReactive = true;
    }

    stopAudioReactive() {
        this.audioReactive = false;
        this.audioTrack = null;
        this.audioElement = null;
        try { this.audioSource?.disconnect?.(); } catch { /* ignore */ }
        try { this.analyser?.disconnect?.(); } catch { /* ignore */ }
        this.analyser = null;
        this.audioSource = null;
    }

    // ── Update (called every frame) ─────────────────────

    update(delta) {
        let targetMouth = 0;

        // Priority 1: viseme data
        if (this.visemeActive && this.visemeQueue.length > 0) {
            targetMouth = this._getVisemeMouth();
        }
        // Priority 2: audio-reactive
        else if (this.audioReactive && this.analyser) {
            targetMouth = this._getAudioMouth();
        }
        // Between words the last vowel holds while the mouth closes; with no speech, no shape.
        if (!this.visemeActive && !(this.audioReactive && this.analyser)) this._targetShape = null;

        // Smooth the transition (fast attack, slower release)
        const speed = targetMouth > this._smoothedMouth ? 15 : 8;
        this._smoothedMouth += (targetMouth - this._smoothedMouth) * speed * delta;

        // Clamp and send to avatar
        const mouth = Math.max(0, Math.min(1, this._smoothedMouth));
        if (this.avatar) this.avatar.setMouthOpen(mouth);
        this._sendShape(delta);
    }

    /** The vowel shape eases from one to the next (a vowel lasts about 0.1 s). */
    _sendShape(delta) {
        const target = this._targetShape;
        if (!target) {
            if (this._shape) { this._shape = null; this.avatar?.setMouthShape?.(null); }
            return;
        }
        const k = Math.min(1, 22 * (Number.isFinite(delta) ? delta : 0));
        const shape = this._shape ?? { ...target };
        for (const v of VISEMES) shape[v] = (shape[v] ?? 0) + ((target[v] ?? 0) - (shape[v] ?? 0)) * k;
        this._shape = shape;
        this.avatar?.setMouthShape?.(shape);
    }

    _getVisemeMouth() {
        const elapsed = performance.now() / 1000 - this.visemeStartTime;
        let mouth = 0;

        for (const v of this.visemeQueue) {
            const start = v.time;
            const end   = v.time + v.duration;
            if (elapsed >= start && elapsed <= end) {
                // Bell curve: sin(0→π) over the viseme duration
                const progress = (elapsed - start) / v.duration;
                const at = mouthAt(v.track, progress);
                if (at) {
                    // Her lips through the word's vowels and consonants, within the word's breath.
                    mouth = v.intensity * at.open * Math.sin(progress * Math.PI) ** 0.35;
                    this._targetShape = at.shape;
                } else {
                    mouth = v.intensity * Math.sin(progress * Math.PI);
                }
                break;
            }
        }

        // Check if all visemes have been played
        const last = this.visemeQueue[this.visemeQueue.length - 1];
        if (elapsed > last.time + last.duration + 0.5) {
            this.visemeActive = false;
        }

        return mouth;
    }

    _getAudioMouth() {
        const buf = new Uint8Array(this.analyser.frequencyBinCount);
        this.analyser.getByteFrequencyData(buf);

        // Voice fundamentals are roughly 85–300 Hz.
        // At 44100 Hz and fftSize 512, each bin ≈ 86 Hz.
        // Bins 1–4 cover ~86–344 Hz.
        const voiceBins = Math.min(8, buf.length);
        let sum = 0;
        for (let i = 0; i < voiceBins; i++) sum += buf[i];
        const avg = sum / voiceBins / 255;

        // Threshold + scale
        const level = Math.max(0, (avg - 0.08) * 2.5);
        // The vowel at this point of the sentence, laid over the audio's length.
        const audio = this.audioElement;
        const at = this.audioTrack && audio?.duration > 0 ? mouthAt(this.audioTrack, audio.currentTime / audio.duration) : null;
        if (!at) return level;
        this._targetShape = at.shape;
        // The audio's timing is approximate: consonants only soften the opening.
        return level * (0.35 + 0.65 * at.open);
    }
}

