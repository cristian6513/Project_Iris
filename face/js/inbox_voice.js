/** F08b: one local return utterance, with an actual playback receipt.
 * No speechSynthesis fallback, chat message, retry or server speakers.
 */
import { TtsAudioPlayer } from './tts_audio_player.js?v=4';
import { noticeDeferral } from './inbox_state.js?v=1';

const ID = /^[0-9a-f]{32}$/;

export class InboxVoicePlayer {
    constructor({ send = () => {}, getContext = () => ({}), getLipSync, getSpeechAnimator,
        audioFactory = (url) => new globalThis.Audio(url), urlApi = globalThis.URL,
        now = () => Date.now() / 1000, setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = (id) => clearTimeout(id) } = {}) {
        this.send = send;
        this.getContext = getContext;
        this.now = now;
        this.setTimer = setTimer;
        this.clearTimer = clearTimer;
        this.seen = new Set();
        this.current = null;
        this.player = new TtsAudioPlayer({ getLipSync, getSpeechAnimator, urlApi,
            audioFactory: (url) => {
                const session = this.current;
                const audio = audioFactory(url);
                const ended = (outcome) => this._finish(session, outcome);
                audio.addEventListener('playing', (event) => {
                    if (this.current !== session) { event.stopImmediatePropagation?.(); audio.pause(); return; }
                    if (!this._allowed()) { event.stopImmediatePropagation?.(); this.stop(); return; }
                    session.started = true;
                });
                audio.addEventListener('ended', () => ended(session.started ? 'spoken' : 'failed'));
                audio.addEventListener('error', () => ended(session.started ? 'ambiguous' : 'failed'));
                const play = audio.play.bind(audio);
                audio.play = () => {
                    if (!this._allowed()) { ended('failed'); throw new Error('Voice deferred'); }
                    session.requested = true;
                    try {
                        const result = play();
                        result?.catch?.(() => ended(session.started ? 'ambiguous' : 'failed'));
                        return result;
                    } catch (error) { ended('failed'); throw error; }
                };
                return audio;
            },
        });
    }

    _allowed() {
        const context = this.getContext();
        return context.connected === true && context.documentLike?.hasFocus?.() !== false
            && !noticeDeferral(context);
    }

    present(payload) {
        const context = this.getContext();
        if (!payload || !ID.test(payload.id || '') || !ID.test(payload.token || '')
            || payload.surface_id !== context.surfaceId || this.seen.has(payload.id)) return 'ignored';
        this.seen.add(payload.id);
        if (this.seen.size > 200) this.seen.delete(this.seen.values().next().value);
        const session = { payload, started: false, requested: false, done: false, timer: null };
        if (this.current || !this._allowed() || !Number.isFinite(payload.expires_at)
            || payload.expires_at <= this.now() || typeof payload.audio !== 'string'
            || !payload.audio || payload.audio.length > 12 * 1024 * 1024
            || !['audio/wav', 'audio/mpeg', 'audio/ogg'].includes(payload.mime)) {
            this._finish(session, 'failed');
            return 'deferred';
        }
        this.current = session;
        session.timer = this.setTimer(() => this.stop(), Math.min(45000, (payload.expires_at - this.now()) * 1000));
        try {
            const result = this.player.handle(payload);
            if (result.status !== 'play-requested') this._finish(session, 'failed');
        } catch { this._finish(session, 'failed'); }
        return 'attempted';
    }

    _finish(session, outcome) {
        if (!session || session.done) return;
        session.done = true;
        this.clearTimer(session.timer);
        if (this.current === session) {
            this.current = null;
            this.player.stop();
            this.player.getLipSync()?.stop?.();
        }
        this.send({ id: session.payload.id, token: session.payload.token, outcome });
    }

    sync() {
        if (this.current && !this._allowed()) this.stop();
    }

    stop() {
        this._finish(this.current, this.current?.requested ? 'ambiguous' : 'failed');
    }
}
