/**
 * Shared speech/affect lifecycle for Face and Desktop Companion.
 *
 * Mood is visible immediately; the avatar's mouth stays neutral for lipsync
 * until speech ends. Getters always address the currently mounted avatar.
 * Audio ownership, visemes and completion receipts remain in TtsAudioPlayer.
 */
export class SpeechPresentation {
    constructor({
        getIdle = () => null,
        getLipSync = () => null,
        getSpeechAnimator = () => null,
        getAudioPlayer = () => null,
        applyExpression = () => {},
        onMood = () => {},
        onSpeaking = () => {},
        timers = globalThis,
        holdMs = 3500,
    } = {}) {
        Object.assign(this, { getIdle, getLipSync, getSpeechAnimator, getAudioPlayer,
            applyExpression, onMood, onSpeaking, timers, holdMs });
        this.emotion = 'neutral';
        this.speaking = false;
        this._revision = 0;
        this._decayTimer = null;
        this._disposed = false;
    }

    get expression() {
        return this.speaking ? 'neutral' : this.emotion;
    }

    start({ emotion } = {}) {
        if (this._disposed) return;
        this._cancelDecay();
        this.speaking = true;
        this._setMood(emotion);
        this.getSpeechAnimator()?.setSyntheticMouthEnabled?.(false);
        this.syncAvatar();
        this.onSpeaking(true);
    }

    end() {
        if (this._disposed || !this.speaking) return false;
        this.speaking = false;
        this._stopSpeech();
        this.applyExpression(this.expression);
        const revision = ++this._revision;
        this._decayTimer = this.timers.setTimeout(() => {
            // A cleared callback may already be queued. It must never reset
            // a newer response, an explicit emotion or a replaced surface.
            if (this._disposed || this.speaking || revision !== this._revision) return;
            this.setEmotion('neutral');
        }, this.holdMs);
        return true;
    }

    setEmotion(emotion) {
        if (this._disposed) return;
        this._cancelDecay();
        this._setMood(emotion);
        this.applyExpression(this.expression);
    }

    _setMood(emotion) {
        this.emotion = typeof emotion === 'string' && emotion.trim() ? emotion : 'neutral';
        this.getIdle()?.feedIrisEmotion?.(this.emotion);
        this.onMood(this.emotion);
    }

    /** Reapply state after a VRM finishes loading, without restarting audio. */
    syncAvatar() {
        if (this._disposed) return;
        this.applyExpression(this.expression);
        this.getIdle()?.feedIrisEmotion?.(this.emotion);
        if (this.speaking) {
            this.getIdle()?.pause?.();
            this.getSpeechAnimator()?.start?.();
        }
    }

    /** Disconnect/interruption: release speech state with no delayed emotion. */
    reset() {
        if (this._disposed) return;
        this._cancelDecay();
        this.speaking = false;
        this._stopSpeech();
        this._setMood('neutral');
        this.applyExpression(this.expression);
    }

    dispose() {
        if (this._disposed) return;
        this.reset();
        this._disposed = true;
    }

    _stopSpeech() {
        this.getLipSync()?.stop?.();
        this.getLipSync()?.stopAudioReactive?.();
        this.getAudioPlayer()?.stop?.();
        this.getSpeechAnimator()?.stop?.();
        this.getSpeechAnimator()?.setSyntheticMouthEnabled?.(true);
        this.getIdle()?.resume?.();
        this.onSpeaking(false);
    }

    _cancelDecay() {
        ++this._revision;
        if (this._decayTimer !== null) this.timers.clearTimeout(this._decayTimer);
        this._decayTimer = null;
    }
}
