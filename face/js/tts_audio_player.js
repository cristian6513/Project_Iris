/**
 * Shared browser/WebView TTS playback for every IRIS avatar surface.
 *
 * Only the backend-elected audio owner receives bytes. This class keeps blob
 * lifetime, replacement and viseme anchoring identical in Face and the future
 * Desktop Companion.
 */

export class TtsAudioPlayer {
    constructor({
        getLipSync,
        getSpeechAnimator,
        onPlaybackResult = () => {},
        audioFactory = (url) => new globalThis.Audio(url),
        urlApi = globalThis.URL,
        logger = console,
    } = {}) {
        this.getLipSync = typeof getLipSync === 'function' ? getLipSync : () => null;
        this.getSpeechAnimator = typeof getSpeechAnimator === 'function'
            ? getSpeechAnimator
            : () => null;
        this.audioFactory = audioFactory;
        this.urlApi = urlApi;
        this.logger = logger || console;
        this.onPlaybackResult = onPlaybackResult;
        this._finishPlayback = null;
        this.audio = null;
        this.objectUrl = null;
    }

    _visemesOnly(visemes) {
        const lipSync = this.getLipSync();
        this.getSpeechAnimator()?.setSyntheticMouthEnabled?.(visemes.length === 0);
        if (visemes.length > 0) lipSync?.processVisemes?.(visemes);
    }

    _revoke(url) {
        if (!url) return;
        try { this.urlApi?.revokeObjectURL?.(url); } catch {}
    }

    _report(payload, status) {
        if (!payload?.playback_id) return;
        try { this.onPlaybackResult?.({ playback_id: payload.playback_id, status }); }
        catch (error) { this.logger.warn?.('[tts_audio] receipt failed:', error); }
    }

    stop() {
        const audio = this.audio;
        const url = this.objectUrl;
        const finish = this._finishPlayback;
        this._finishPlayback = null;
        this.audio = null;
        this.objectUrl = null;
        try { audio?.pause?.(); } catch {}
        this.getLipSync()?.stopAudioReactive?.();
        this._revoke(url);
        finish?.('stopped');
    }

    handle(payload) {
        if (!payload) return { status: 'ignored' };
        const visemes = Array.isArray(payload.visemes) ? payload.visemes : [];
        const base64 = typeof payload.audio === 'string' ? payload.audio : '';

        if (!base64) {
            this._visemesOnly(visemes);
            return { status: 'visemes-only' };
        }

        let bytes;
        try {
            const raw = globalThis.atob(base64);
            bytes = new Uint8Array(raw.length);
            for (let index = 0; index < raw.length; index += 1) {
                bytes[index] = raw.charCodeAt(index);
            }
        } catch (error) {
            this.logger.warn?.('[tts_audio] base64 decode failed:', error);
            this._report(payload, 'failed');
            this._visemesOnly(visemes);
            return { status: 'decode-failed' };
        }

        if (bytes.length === 'FALLBACK_FAILED'.length) {
            const asText = new TextDecoder().decode(bytes);
            if (asText === 'FALLBACK_FAILED') {
                this.logger.warn?.('[tts_audio] server reported FALLBACK_FAILED — visemes only');
                this._report(payload, 'failed');
                this._visemesOnly(visemes);
                return { status: 'provider-failed' };
            }
        }

        const mime = payload.mime || 'audio/mpeg';
        const blob = new Blob([bytes], { type: mime });
        const url = this.urlApi.createObjectURL(blob);
        const lipSync = this.getLipSync();
        lipSync?.stop?.();
        lipSync?.stopAudioReactive?.();
        this.getSpeechAnimator()?.setSyntheticMouthEnabled?.(false);

        // Same-turn replacement and barge-in must not overlap audio or leak
        // object URLs.
        this.stop();
        let reported = false;
        const finish = (status) => {
            if (reported) return;
            reported = true;
            if (this._finishPlayback === finish) this._finishPlayback = null;
            this._report(payload, status);
        };
        let audio;
        try { audio = this.audioFactory(url); }
        catch (error) {
            this._revoke(url);
            finish('failed');
            return { status: 'play-rejected' };
        }
        this._finishPlayback = finish;
        audio.preload = 'auto';
        this.audio = audio;
        this.objectUrl = url;

        audio.onplaying = () => {
            if (this.audio !== audio) return;
            if (visemes.length > 0) lipSync?.processVisemes?.(visemes);
            else lipSync?.startAudioReactive?.(audio);
        };
        audio.onended = () => {
            if (this.audio !== audio) return;
            finish('ended');
            lipSync?.stopAudioReactive?.();
            this._revoke(url);
            if (this.audio === audio) {
                this.audio = null;
                this.objectUrl = null;
            }
        };
        audio.onerror = (error) => {
            if (this.audio !== audio) return;
            finish('failed');
            this.logger.warn?.('[tts_audio] playback error:', error);
            this._visemesOnly(visemes);
            this._revoke(url);
            if (this.audio === audio) {
                this.audio = null;
                this.objectUrl = null;
            }
        };

        let playPromise;
        try {
            playPromise = audio.play();
        } catch (error) {
            this.logger.warn?.('[tts_audio] audio.play() threw:', error?.message || error);
            audio.onerror?.(error);
            return { status: 'play-rejected' };
        }
        if (playPromise && typeof playPromise.catch === 'function') {
            playPromise.catch((error) => {
                if (this.audio !== audio) return;
                audio.onerror?.(error);
            });
        }
        return { status: 'play-requested' };
    }
}
