/** Shared runtime state for authored body animations; no microphone access. */
export class AvatarActivity {
    constructor() { this.reset(); }

    reset() {
        this.status = 'idle';
        this.wake = 'off';
        this.manual = 'off';
        this.manualRevision = -1;
        this.speaking = false;
    }

    handle(type, payload = {}) {
        if (type === 'status') {
            this.status = payload.status || 'idle';
            if (['idle', 'ready', 'error', 'offline'].includes(this.status)) {
                // A completed response must not remain in its old STT state.
                if (['transcribing', 'processing'].includes(this.manual)) this.manual = 'off';
                if (['transcribing', 'processing'].includes(this.wake)) this.wake = 'off';
            }
        } else if (type === 'hermes_wake_state') {
            this.wake = payload.state || 'off';
        } else if (type === 'hermes_voice_state') {
            if (!Number.isFinite(payload.revision) || payload.revision < this.manualRevision) return;
            this.manualRevision = payload.revision;
            this.manual = payload.state || 'off';
        } else if (type === 'speech_start') {
            this.speaking = true;
            this.status = 'speaking';
        } else if (type === 'speech_end') {
            this.speaking = false;
            this.status = 'idle';
            if (['transcribing', 'processing'].includes(this.manual)) this.manual = 'off';
            if (['transcribing', 'processing'].includes(this.wake)) this.wake = 'off';
        }
    }

    get state() {
        // Wake's "listening" means waiting for the wake word, not recording.
        if (['recording', 'stopping'].includes(this.manual)
            || ['detected', 'recording'].includes(this.wake)) return 'listening';
        if (this.speaking) return 'speaking';
        if (['transcribing', 'processing'].includes(this.manual)
            || ['transcribing', 'processing'].includes(this.wake)
            || ['thinking', 'processing', 'tool'].includes(this.status)) return 'thinking';
        return 'idle';
    }
}
