import {
    createPlayerState,
    currentPlayerTrack,
    filterPlayerTracks,
    recommendLocalTracks,
    reducePlayerState,
} from './iris_player_state.js?v=2';
import { observePlayerPlayback } from './player_playback_observer.js?v=1';

const PLAYER_LAST_TRACK_KEY = 'irisPlayer.lastTrack.v1';
const PLAYER_VOLUME_KEY = 'irisPlayer.volume.v1';
const PLAYER_PLAY_CONFIRM_TIMEOUT_MS = 8_000;

function escapeHtml(value = '') {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function escapeAttr(value = '') {
    return escapeHtml(value).replace(/`/g, '&#096;');
}

function playerIcon(name = 'music') {
    const paths = {
        music: '<path d="M8 18V6l10-2v12"/><circle cx="6" cy="18" r="2.5"/><circle cx="16" cy="16" r="2.5"/><path d="M8 9l10-2"/>',
        folder: '<path d="M3 7h7l2 2h9v10H3z"/><path d="M3 7V5h7l2 2"/>',
        search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 4 4"/>',
        heart: '<path d="M20 8.5c0 5-8 10-8 10s-8-5-8-10A4.5 4.5 0 0 1 12 5.7 4.5 4.5 0 0 1 20 8.5z"/>',
        recent: '<circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/>',
        library: '<path d="M4 5h4v14H4zM10 5h4v14h-4zM16 7l3-1 2 12-3 1z"/>',
        previous: '<path d="M6 5v14M18 6l-8 6 8 6z"/>',
        next: '<path d="M18 5v14M6 6l8 6-8 6z"/>',
        play: '<path d="m8 5 11 7-11 7z"/>',
        pause: '<path d="M7 5h4v14H7zM14 5h4v14h-4z"/>',
        shuffle: '<path d="M4 7h3c4 0 6 10 10 10h3M17 14l3 3-3 3M4 17h3c1.5 0 2.7-1.4 3.8-3M14 8c1-1 2-1 3-1h3M17 4l3 3-3 3"/>',
        repeat: '<path d="M5 7h12l3 3-3 3M19 17H7l-3-3 3-3"/>',
        refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 8a7 7 0 0 1 11.7-1L20 9M4 15l2.2 2a7 7 0 0 0 11.7-1"/>',
        volume: '<path d="M4 10h4l5-4v12l-5-4H4zM16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
        lock: '<rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    };
    return `<span class="iris-player-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${paths[name] || paths.music}</svg></span>`;
}

function formatDuration(value) {
    const seconds = Math.max(0, Number(value) || 0);
    if (!Number.isFinite(seconds)) return '--:--';
    const whole = Math.floor(seconds);
    return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

function coverHue(id = '') {
    let hash = 0;
    for (const char of String(id)) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
    return Math.abs(hash) % 360;
}

function readStoredNumber(key, fallback) {
    const stored = localStorage.getItem(key);
    if (stored === null || stored.trim() === '') return fallback;
    const value = Number(stored);
    return Number.isFinite(value) ? value : fallback;
}

export class IrisPlayerApp {
    constructor({ root, onSummary = null, onPlayback = null } = {}) {
        this.root = root || null;
        this.onSummary = onSummary;
        this.state = createPlayerState({
            volume: Math.min(1, Math.max(0, readStoredNumber(PLAYER_VOLUME_KEY, 0.8))),
        });
        this.meta = { configured: false, folderName: '', scannedAt: '', count: 0 };
        this.query = '';
        this.filter = 'all';
        this.loading = false;
        this.error = '';
        this.notice = '';
        this.selectionOrigin = 'manual';
        this.selectionReason = '';
        this._loaded = false;
        this._loadPromise = null;
        this._playReported = '';
        this._noticeTimer = 0;
        this._widgetListeners = new Set();
        this._widgetCommandPending = false;

        if (!this.root) return;
        this.root.innerHTML = '<div class="iris-player-surface"></div><audio class="iris-player-audio" preload="metadata"></audio>';
        this.surface = this.root.querySelector('.iris-player-surface');
        this.audio = this.root.querySelector('.iris-player-audio');
        this.audio.volume = this.state.volume;
        this._bind();
        this._playbackObserver = observePlayerPlayback(
            this.audio, () => currentPlayerTrack(this.state), (snapshot) => {
                this._playbackSnapshot = snapshot;
                this._notifyWidgets();
                onPlayback?.(snapshot);
            },
        );
        this.render();
    }

    _bind() {
        this.root?.addEventListener('click', (event) => {
            const action = event.target?.closest?.('[data-player-action]');
            if (action && !action.disabled) {
                this._runAction(action.dataset.playerAction || '', action.dataset.trackId || '');
                return;
            }
            const track = event.target?.closest?.('[data-player-track]');
            if (track) this.playTrack(track.dataset.playerTrack || '');
        });
        this.root?.addEventListener('input', (event) => {
            if (event.target?.matches?.('[data-player-search]')) {
                this.query = event.target.value || '';
                this._renderTrackList();
            }
            if (event.target?.matches?.('[data-player-progress]')) {
                const seconds = Number(event.target.value || 0);
                if (this.audio && Number.isFinite(seconds)) this.audio.currentTime = seconds;
            }
            if (event.target?.matches?.('[data-player-volume]')) {
                const volume = Math.min(1, Math.max(0, Number(event.target.value || 0)));
                this.state = reducePlayerState(this.state, { type: 'SET_VOLUME', volume });
                if (this.audio) this.audio.volume = volume;
                localStorage.setItem(PLAYER_VOLUME_KEY, String(volume));
                this._updateTimeControls();
            }
        });
        this.root?.addEventListener('submit', (event) => {
            const form = event.target?.closest?.('[data-player-folder-form]');
            if (!form) return;
            event.preventDefault();
            const field = form.querySelector('[data-player-folder-path]');
            this._configureFolder(field?.value || '');
        });
        this.audio?.addEventListener('loadedmetadata', () => this._updateTimeControls());
        this.audio?.addEventListener('timeupdate', () => {
            this.state = reducePlayerState(this.state, { type: 'SET_ELAPSED', seconds: this.audio.currentTime });
            this._updateTimeControls();
            if (this.audio.currentTime >= 30 && this._playReported !== this.state.currentTrackId) {
                this._playReported = this.state.currentTrackId || '';
                this._reportPlayed(this._playReported);
            }
        });
        this.audio?.addEventListener('play', () => {
            this.state = reducePlayerState(this.state, { type: 'PLAY' });
            this.render();
        });
        this.audio?.addEventListener('pause', () => {
            if (this.audio.ended) return;
            this.state = reducePlayerState(this.state, { type: 'PAUSE' });
            this.render();
        });
        this.audio?.addEventListener('ended', () => {
            const finishedTrackId = this.state.currentTrackId || '';
            if (finishedTrackId && this._playReported !== finishedTrackId) {
                this._playReported = finishedTrackId;
                this._reportPlayed(finishedTrackId);
            }
            this.state = reducePlayerState(this.state, { type: 'TRACK_ENDED' });
            this._playReported = '';
            this._syncAudio(true);
        });
        this.audio?.addEventListener('error', () => {
            this.state = reducePlayerState(this.state, {
                type: 'PLAYBACK_ERROR',
                error: 'No se pudo reproducir este archivo. Puede estar dañado o usar un códec no compatible con el navegador.',
            });
            this.render();
        });
    }

    setHermesConnected(connected) {
        const previous = this.state.capabilities.hermesConnected;
        this.state = reducePlayerState(this.state, {
            type: 'SET_CAPABILITIES',
            capabilities: { hermesConnected: connected === true },
        });
        if (previous !== this.state.capabilities.hermesConnected) this.render();
    }

    /** A reviewed widget gets display data and transport only, never the audio/DOM/library paths. */
    widgetService() {
        if (!this._widgetService) this._widgetService = Object.freeze({
            read: () => this._widgetSnapshot(),
            subscribe: (listener) => {
                this._widgetListeners.add(listener);
                listener(this._widgetSnapshot());
                return () => this._widgetListeners.delete(listener);
            },
            load: () => this.load(),
            command: async (action) => {
                if (!['play-pause', 'previous', 'next'].includes(action)
                    || this._widgetCommandPending || this.loading || !this._loaded
                    || !this.audio || !this.meta.configured || !this.state.library.total) return false;
                this._widgetCommandPending = true; this._notifyWidgets();
                try { return await this._runAction(action) !== false; }
                finally { this._widgetCommandPending = false; this._notifyWidgets(); }
            },
        });
        return this._widgetService;
    }

    _widgetSnapshot() {
        const track = currentPlayerTrack(this.state);
        const seconds = (value) => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
        return Object.freeze({
            loaded: this._loaded, loading: this.loading, pending: this._widgetCommandPending,
            configured: this.meta.configured, count: this.state.library.total,
            title: track?.title || '', artist: track?.artist || '',
            status: this._playbackSnapshot?.status || 'idle',
            elapsed: seconds(this.audio?.currentTime), duration: seconds(this.audio?.duration),
            error: this.error || this.state.error || '',
        });
    }

    _notifyWidgets() {
        const snapshot = this._widgetSnapshot();
        for (const listener of this._widgetListeners) {
            try { listener(snapshot); } catch { /* A view cannot interrupt playback. */ }
        }
    }

    async handleIntent(intent = {}) {
        const requestId = String(intent?.request_id || '');
        const result = (status, message, data = {}) => ({
            schema: 'iris.app-result/v1',
            request_id: requestId,
            status,
            message,
            data,
        });
        if (
            intent?.schema !== 'iris.app-intent/v1'
            || intent?.app !== 'player'
            || !requestId
            || typeof intent?.action !== 'string'
            || !intent?.args
            || typeof intent.args !== 'object'
            || Array.isArray(intent.args)
        ) {
            return result('denied', 'La solicitud de IRIS Player no es válida.');
        }

        await this.load();
        if (!this.meta.configured) {
            return result('unavailable', 'Selecciona una carpeta desde IRIS Player antes de usar controles por voz o chat.');
        }
        if (!this.state.library.total) {
            return result('unavailable', 'La carpeta autorizada no contiene canciones compatibles.');
        }

        const action = intent.action;
        const args = intent.args;
        const currentData = (track, origin, reason = '') => ({
            selection_origin: origin,
            track_id: track?.id || '',
            title: track?.title || '',
            artist: track?.artist || '',
            reason,
            playback_state: this.audio?.paused === false ? 'playing' : this.state.playback,
        });

        if (action === 'pause') {
            const current = currentPlayerTrack(this.state);
            if (!current) return result('unavailable', 'No hay una canción activa para pausar.');
            this.audio?.pause();
            this.state = reducePlayerState(this.state, { type: 'PAUSE' });
            this.render();
            return result('ok', 'Reproducción pausada.', currentData(current, this.selectionOrigin));
        }

        if (action === 'set_volume') {
            const percent = args.percent;
            if (!Number.isInteger(percent) || percent < 0 || percent > 100) {
                return result('denied', 'El volumen solicitado no es válido.');
            }
            const volume = percent / 100;
            this.state = reducePlayerState(this.state, { type: 'SET_VOLUME', volume });
            if (this.audio) this.audio.volume = volume;
            localStorage.setItem(PLAYER_VOLUME_KEY, String(volume));
            this.render();
            return result('ok', `Volumen ajustado a ${percent}%.`, {
                percent,
                playback_state: this.audio?.paused === false ? 'playing' : this.state.playback,
            });
        }

        let target = null;
        let origin = 'intent_hermes';
        let reason = '';
        if (action === 'play') {
            target = args.track_id
                ? this.state.library.tracks.find((track) => track.id === args.track_id)
                : currentPlayerTrack(this.state) || this.state.library.tracks[0];
            if (!target) return result('denied', 'La canción solicitada no pertenece a la biblioteca activa.');
        } else if (action === 'play_by_query') {
            const matches = filterPlayerTracks(this.state.library.tracks, { query: args.query });
            if (!matches.length) {
                return result('unavailable', 'No encontré coincidencias en la biblioteca autorizada.');
            }
            const needle = String(args.query || '').trim().toLocaleLowerCase();
            const exact = matches.filter((track) => (
                track.title.toLocaleLowerCase() === needle
                || track.artist.toLocaleLowerCase() === needle
                || track.album.toLocaleLowerCase() === needle
            ));
            if (exact.length !== 1 && matches.length > 1) {
                return result('needs_choice', 'Encontré varias coincidencias.', {
                    candidates: matches.slice(0, 5).map((track) => ({
                        track_id: track.id,
                        title: track.title,
                        artist: track.artist,
                    })),
                });
            }
            target = exact[0] || matches[0];
            origin = 'query';
            reason = `Coincide con “${String(args.query || '').trim()}”.`;
        } else if (action === 'play_by_intent') {
            const recommendation = recommendLocalTracks(this.state.library, {
                intent: args.intent,
                limit: 1,
            })[0];
            if (!recommendation) return result('unavailable', 'Todavía no hay música disponible para elegir.');
            target = recommendation.track;
            origin = 'intent_local';
            reason = recommendation.reasons?.[0] || 'Elegida mediante reglas locales de la biblioteca.';
        } else if (action === 'next' || action === 'previous') {
            this.state = reducePlayerState(this.state, { type: action.toUpperCase() });
            target = currentPlayerTrack(this.state);
            if (!target) return result('unavailable', 'No hay otra canción disponible en la cola.');
        } else {
            return result('denied', 'La acción solicitada no está disponible en IRIS Player.');
        }

        this.selectionOrigin = origin;
        this.selectionReason = reason;
        this.state = reducePlayerState(this.state, { type: 'PLAY_TRACK', trackId: target.id });
        localStorage.setItem(PLAYER_LAST_TRACK_KEY, target.id);
        this._playReported = '';
        const started = await this._syncAudio(true);
        if (!started) {
            return result('denied', 'El navegador bloqueó el inicio de audio. Pulsa reproducir una vez en IRIS Player.');
        }
        return result('ok', `Reproduciendo ${target.title}.`, currentData(target, origin, reason));
    }

    async load(force = false) {
        if (this._loadPromise) return this._loadPromise;
        if (this._loaded && !force) return undefined;
        const audioBeforeRefresh = force ? this._audioSnapshot() : null;
        this._loadPromise = (async () => {
            this.loading = true;
            this.error = '';
            this.render();
            try {
                const response = await fetch(`/api/player/library${force ? '?refresh=true' : ''}`, { cache: 'no-store' });
                const payload = await this._jsonResponse(response);
                this._applyLibrary(payload);
                if (audioBeforeRefresh) await this._reconcileAudioAfterRefresh(audioBeforeRefresh);
                this._loaded = true;
            } catch (error) {
                this.error = error?.message || 'IRIS Player no está disponible.';
            } finally {
                this.loading = false;
                this.render();
            }
        })();
        try {
            return await this._loadPromise;
        } finally {
            this._loadPromise = null;
        }
    }

    _applyLibrary(payload = {}) {
        const previousTrack = this.state.currentTrackId || localStorage.getItem(PLAYER_LAST_TRACK_KEY) || '';
        this.state = reducePlayerState(this.state, { type: 'LIBRARY_REFRESH', payload });
        if (!this.state.currentTrackId && previousTrack && this.state.library.tracks.some((track) => track.id === previousTrack)) {
            this.state = reducePlayerState(this.state, { type: 'PLAY_TRACK', trackId: previousTrack });
            this.state = reducePlayerState(this.state, { type: 'PAUSE' });
        }
        this.meta = {
            configured: payload.configured === true || Boolean(payload.folder_name || payload.folderName),
            folderName: String(payload.folder_name || payload.folderName || ''),
            scannedAt: String(payload.scanned_at || payload.scannedAt || payload.updated_at || ''),
            count: Number(payload.count ?? payload.total ?? this.state.library.total) || 0,
        };
        this.onSummary?.(this.meta.configured
            ? `${this.state.library.total} canciones locales`
            : 'Selecciona una carpeta local');
    }

    async _jsonResponse(response) {
        let payload = {};
        try { payload = await response.json(); } catch { payload = {}; }
        if (!response.ok) throw new Error(payload.detail || payload.message || `Error ${response.status}`);
        return payload;
    }

    async _pickFolder() {
        this.loading = true;
        this.error = '';
        this.notice = 'Esperando selección de carpeta…';
        this.render();
        try {
            const response = await fetch('/api/player/folder/pick', { method: 'POST' });
            const payload = await this._jsonResponse(response);
            if (payload.cancelled) {
                this._showNotice('No se cambió la biblioteca.');
            } else {
                this._stopAudioForLibraryChange();
                this._applyLibrary(payload);
                this._showNotice(`Biblioteca preparada: ${this.state.library.total} canciones.`);
            }
        } catch (error) {
            this.error = error?.message || 'No pude abrir el selector de carpeta.';
        } finally {
            this.loading = false;
            this.render();
        }
    }

    async _configureFolder(path) {
        const normalized = String(path || '').trim();
        if (!normalized) {
            this._showNotice('Escribe una ruta local o utiliza el selector de carpeta.');
            return;
        }
        this.loading = true;
        this.error = '';
        this.render();
        try {
            const response = await fetch('/api/player/folder', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: normalized }),
            });
            const payload = await this._jsonResponse(response);
            this._stopAudioForLibraryChange();
            this._applyLibrary(payload);
            this._showNotice(`Biblioteca preparada: ${this.state.library.total} canciones.`);
        } catch (error) {
            this.error = error?.message || 'No pude usar esa carpeta.';
        } finally {
            this.loading = false;
            this.render();
        }
    }

    async _refresh() {
        if (!this.meta.configured) {
            this._showNotice('Primero selecciona una carpeta de música.');
            return;
        }
        this.loading = true;
        this.error = '';
        this.render();
        try {
            const audioBeforeRefresh = this._audioSnapshot();
            const response = await fetch('/api/player/refresh', { method: 'POST' });
            const payload = await this._jsonResponse(response);
            this._applyLibrary(payload);
            await this._reconcileAudioAfterRefresh(audioBeforeRefresh);
            this._showNotice('Biblioteca local actualizada.');
        } catch (error) {
            this.error = error?.message || 'No pude actualizar la biblioteca.';
        } finally {
            this.loading = false;
            this.render();
        }
    }

    async _setFavorite(trackId, favorite) {
        try {
            const response = await fetch(`/api/player/tracks/${encodeURIComponent(trackId)}/favorite`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ favorite }),
            });
            const payload = await this._jsonResponse(response);
            this._applyLibrary(payload.library || payload);
            this.render();
        } catch (error) {
            this._showNotice(error?.message || 'No pude guardar el favorito.', true);
        }
    }

    async _reportPlayed(trackId) {
        if (!trackId) return;
        try {
            await fetch(`/api/player/tracks/${encodeURIComponent(trackId)}/played`, { method: 'POST' });
        } catch {
            // Playback must continue even if history persistence is unavailable.
        }
    }

    _runAction(action, trackId = '') {
        if (action === 'pick-folder') return this._pickFolder();
        if (action === 'refresh') return this._refresh();
        if (action === 'play-pause') return this._togglePlayback();
        if (action === 'next' || action === 'previous') {
            this.state = reducePlayerState(this.state, { type: action.toUpperCase() });
            this.selectionOrigin = 'manual';
            this.selectionReason = '';
            return this._syncAudio(true);
        }
        if (action === 'shuffle') {
            this.state = reducePlayerState(this.state, { type: 'SET_SHUFFLE', enabled: !this.state.shuffle });
            return this.render();
        }
        if (action === 'repeat') {
            const modes = ['none', 'all', 'one'];
            this.state = reducePlayerState(this.state, {
                type: 'SET_REPEAT',
                repeat: modes[(modes.indexOf(this.state.repeat) + 1) % modes.length],
            });
            return this.render();
        }
        if (action === 'filter-all' || action === 'filter-favorites' || action === 'filter-recent') {
            this.filter = action.replace('filter-', '');
            return this.render();
        }
        if (action === 'favorite' && trackId) {
            const track = this.state.library.tracks.find((item) => item.id === trackId);
            if (track) return this._setFavorite(trackId, !track.favorite);
        }
        if (action === 'play-recommendation' && trackId) {
            const selected = recommendLocalTracks(this.state.library, {
                limit: this.state.library.total,
            }).find((item) => item.trackId === trackId);
            this.selectionReason = selected?.reasons?.[0] || '';
            return this.playTrack(trackId, 'intent_local');
        }
    }

    playTrack(trackId, origin = 'manual') {
        this.state = reducePlayerState(this.state, { type: 'PLAY_TRACK', trackId });
        this.selectionOrigin = origin === 'intent_local' ? 'intent_local' : 'manual';
        if (this.selectionOrigin === 'manual') this.selectionReason = '';
        if (this.state.currentTrackId) localStorage.setItem(PLAYER_LAST_TRACK_KEY, this.state.currentTrackId);
        this._playReported = '';
        return this._syncAudio(true);
    }

    _togglePlayback() {
        const current = currentPlayerTrack(this.state);
        if (!current) {
            const first = this._visibleTracks()[0] || this.state.library.tracks[0];
            return first ? this.playTrack(first.id) : false;
        }
        if (this.audio?.paused) {
            this.state = reducePlayerState(this.state, { type: 'PLAY' });
            return this._syncAudio(true);
        } else {
            this.audio.pause();
            return true;
        }
    }

    async _syncAudio(autoplay = false) {
        const track = currentPlayerTrack(this.state);
        if (!this.audio || !track?.streamUrl) {
            this.audio?.removeAttribute('src');
            this.audio?.load?.();
            this.render();
            return false;
        }
        const currentSource = this.audio.getAttribute('src') || '';
        if (currentSource !== track.streamUrl) {
            this.audio.src = track.streamUrl;
            this.audio.load();
        } else if (this.state.elapsedSeconds === 0) {
            this.audio.currentTime = 0;
        }
        this.audio.volume = this.state.volume;
        if (autoplay && this.state.playback === 'playing') {
            const started = await this._confirmPlaybackStart();
            if (!started) {
                this.state = reducePlayerState(this.state, { type: 'PAUSE' });
                this._showNotice('Pulsa reproducir para iniciar el audio.', true);
                this.render();
                return false;
            }
            return true;
        } else {
            this.render();
            return true;
        }
    }

    async _confirmPlaybackStart(timeoutMs = PLAYER_PLAY_CONFIRM_TIMEOUT_MS) {
        const audio = this.audio;
        if (!audio?.play) return false;

        return new Promise((resolve) => {
            let settled = false;
            let timer = null;
            const finish = (started) => {
                if (settled) return;
                settled = true;
                if (timer !== null) clearTimeout(timer);
                audio.removeEventListener?.('playing', onPlaying);
                audio.removeEventListener?.('error', onError);
                audio.removeEventListener?.('abort', onError);
                resolve(started === true);
            };
            const onPlaying = () => finish(true);
            const onError = () => finish(false);

            audio.addEventListener?.('playing', onPlaying, { once: true });
            audio.addEventListener?.('error', onError, { once: true });
            audio.addEventListener?.('abort', onError, { once: true });
            timer = setTimeout(() => {
                finish(audio.paused === false && Number(audio.readyState || 0) >= 2);
            }, Math.max(250, Number(timeoutMs) || PLAYER_PLAY_CONFIRM_TIMEOUT_MS));

            try {
                const attempt = audio.play();
                Promise.resolve(attempt).then(() => {
                    finish(audio.paused === false);
                }).catch(() => finish(false));
            } catch {
                finish(false);
            }
        });
    }

    _audioSnapshot() {
        const track = currentPlayerTrack(this.state);
        return {
            trackId: track?.id || '',
            streamUrl: track?.streamUrl || '',
            wasPlaying: this.state.playback === 'playing' && this.audio?.paused === false,
        };
    }

    _stopAudioForLibraryChange() {
        this.audio?.pause?.();
        this.audio?.removeAttribute?.('src');
        this.audio?.load?.();
        this.state = reducePlayerState(this.state, { type: 'STOP' });
        this.selectionOrigin = 'manual';
        this.selectionReason = '';
        this._playReported = '';
        localStorage.removeItem(PLAYER_LAST_TRACK_KEY);
    }

    async _reconcileAudioAfterRefresh(before = {}) {
        if (!before.trackId) return;
        const current = currentPlayerTrack(this.state);
        if (!current || current.id !== before.trackId) {
            this._stopAudioForLibraryChange();
            return;
        }
        this.audio?.pause?.();
        this.audio?.removeAttribute?.('src');
        this.audio?.load?.();
        this.state = reducePlayerState(this.state, { type: 'PAUSE' });
        if (before.wasPlaying) {
            this.state = reducePlayerState(this.state, { type: 'PLAY' });
            await this._syncAudio(true);
        } else {
            await this._syncAudio(false);
        }
    }

    _visibleTracks() {
        const filters = { query: this.query };
        if (this.filter === 'favorites') filters.favorites = true;
        if (this.filter === 'recent') filters.sort = 'recent';
        return filterPlayerTracks(this.state.library.tracks, filters);
    }

    _trackListMarkup() {
        const tracks = this._visibleTracks();
        const current = currentPlayerTrack(this.state);
        if (!tracks.length) {
            return `<div class="iris-player-list-empty">${this.query ? 'No hay coincidencias.' : 'La biblioteca está esperando música.'}</div>`;
        }
        return tracks.slice(0, 160).map((track) => `
            <button type="button" role="option" aria-selected="${current?.id === track.id}" data-player-track="${escapeAttr(track.id)}" class="${current?.id === track.id ? 'active' : ''}">
                <span class="iris-player-mini-cover" style="--player-cover-hue:${coverHue(track.id)}deg">${escapeHtml(track.title.slice(0, 1).toUpperCase())}</span>
                <span><strong>${escapeHtml(track.title)}</strong><small>${escapeHtml(track.artist)}</small></span>
                ${track.favorite ? '<em aria-label="Favorita">♥</em>' : ''}
            </button>
        `).join('');
    }

    _renderTrackList() {
        const list = this.surface?.querySelector('.iris-player-track-list');
        if (list) list.innerHTML = this._trackListMarkup();
    }

    _showNotice(message, isError = false) {
        this.notice = String(message || '');
        if (isError) this.error = this.notice;
        window.clearTimeout?.(this._noticeTimer);
        this._noticeTimer = window.setTimeout?.(() => {
            this.notice = '';
            if (isError) this.error = '';
            this.render();
        }, 4600);
        this.render();
    }

    _updateTimeControls() {
        const elapsed = Number(this.audio?.currentTime || this.state.elapsedSeconds || 0);
        const duration = Number(this.audio?.duration || currentPlayerTrack(this.state)?.duration || 0);
        const progress = this.root?.querySelector('[data-player-progress]');
        const elapsedLabel = this.root?.querySelector('[data-player-elapsed]');
        const durationLabel = this.root?.querySelector('[data-player-duration]');
        const volumeLabel = this.root?.querySelector('[data-player-volume-label]');
        if (progress) {
            progress.max = String(Number.isFinite(duration) && duration > 0 ? duration : 0);
            progress.value = String(Math.min(elapsed, Number(progress.max) || elapsed));
        }
        if (elapsedLabel) elapsedLabel.textContent = formatDuration(elapsed);
        if (durationLabel) durationLabel.textContent = formatDuration(duration);
        if (volumeLabel) volumeLabel.textContent = `${Math.round(this.state.volume * 100)}%`;
        this._notifyWidgets();
    }

    render() {
        this._playbackObserver?.refresh();
        this._notifyWidgets();
        if (!this.surface) return;
        const activeElement = globalThis.document?.activeElement;
        const restoreSearchFocus = Boolean(
            activeElement?.matches?.('[data-player-search]')
            && this.surface.contains(activeElement),
        );
        const searchSelection = restoreSearchFocus
            ? [activeElement.selectionStart, activeElement.selectionEnd]
            : null;
        const current = currentPlayerTrack(this.state);
        const queue = this.state.queueIds
            .map((id) => this.state.library.tracks.find((track) => track.id === id))
            .filter(Boolean);
        const recommendations = recommendLocalTracks(this.state.library, {
            excludeIds: current ? [current.id] : [],
            limit: 4,
        });
        const playing = this.state.playback === 'playing';
        const hermesConnected = this.state.capabilities.hermesConnected;
        const folderName = this.meta.folderName || 'Sin carpeta autorizada';
        const coverStyle = current ? `--player-cover-hue:${coverHue(current.id)}deg` : '--player-cover-hue:188deg';
        const coverLetters = current
            ? current.title.split(/\s+/).slice(0, 2).map((word) => word[0] || '').join('').toUpperCase()
            : 'IR';
        const bars = Array.from({ length: 36 }, (_, index) => {
            const energy = (index * 17) % 83;
            return `<i style="--bar:${energy};height:${14 + energy * 0.54}%"></i>`;
        }).join('');
        const brandBars = Array.from({ length: 7 }, (_, index) => {
            const energy = (index * 23) % 79;
            return `<i style="--bar:${energy};height:${8 + energy * 0.16}px"></i>`;
        }).join('');
        const empty = !this.meta.configured || !this.state.library.total;
        const insightLabel = this.selectionOrigin === 'intent_local'
            ? 'Selección local de IRIS'
            : 'Reproducción manual';
        const insightText = !current
            ? 'El Player funciona sin Hermes. Cuando lo conectes, podrá interpretar peticiones musicales sin recibir tus archivos.'
            : this.selectionOrigin === 'intent_local'
                ? (this.selectionReason || 'Elegida mediante reglas locales dentro de tu biblioteca autorizada.')
                : 'Elegiste esta pista directamente desde tu biblioteca autorizada.';

        this.surface.innerHTML = `
            <section class="iris-player" data-player-state="${empty ? 'empty' : this.state.playback}" data-player-active="${playing ? 'true' : 'false'}">
                <header class="iris-player-toolbar">
                    <div class="iris-player-brand">
                        <span class="iris-player-brand-wave" aria-hidden="true">${brandBars}</span>
                        <div><span>IRIS Player</span><small>Música local. Privada. Solo tuya.</small></div>
                    </div>
                    <label class="iris-player-search">
                        ${playerIcon('search')}
                        <input type="search" data-player-search value="${escapeAttr(this.query)}" placeholder="Buscar música, artistas o álbumes" autocomplete="off">
                    </label>
                    <div class="iris-player-runtime">
                        <span>${playerIcon('lock')} Modo local</span>
                        <small class="${hermesConnected ? 'is-ready' : ''}">${hermesConnected ? 'Hermes conectado' : 'Hermes desconectado'}</small>
                    </div>
                </header>

                <div class="iris-player-grid">
                    <aside class="iris-player-library">
                        <span class="iris-player-label">Biblioteca</span>
                        <nav aria-label="Filtros de biblioteca">
                            <button type="button" data-player-action="filter-all" class="${this.filter === 'all' ? 'active' : ''}">${playerIcon('library')}<span>Toda la música</span><b>${this.state.library.total}</b></button>
                            <button type="button" data-player-action="filter-favorites" class="${this.filter === 'favorites' ? 'active' : ''}">${playerIcon('heart')}<span>Favoritas</span></button>
                            <button type="button" data-player-action="filter-recent" class="${this.filter === 'recent' ? 'active' : ''}">${playerIcon('recent')}<span>Recientes</span></button>
                        </nav>
                        <div class="iris-player-track-list" role="listbox" aria-label="Canciones locales">${this._trackListMarkup()}</div>
                        <section class="iris-player-folder">
                            <span>Tu música, tu elección</span>
                            <div>${playerIcon('folder')}<p><small>Carpeta activa</small><strong>${escapeHtml(folderName)}</strong></p></div>
                            <button type="button" data-player-action="pick-folder" ${this.loading ? 'disabled' : ''}>${this.meta.configured ? 'Cambiar carpeta' : 'Seleccionar carpeta'}</button>
                            <details>
                                <summary>Ruta manual avanzada</summary>
                                <form data-player-folder-form>
                                    <input data-player-folder-path type="text" maxlength="4096" placeholder="C:\\Users\\...\\Música" aria-label="Ruta local de música">
                                    <button type="submit">Usar</button>
                                </form>
                            </details>
                            <footer><b>${this.state.library.total}</b> canciones <i></i><span>Solo se indexa esta carpeta</span></footer>
                        </section>
                    </aside>

                    <main class="iris-player-stage">
                        ${empty ? `
                            <section class="iris-player-empty">
                                <div class="iris-player-empty-orbit">${playerIcon('music')}<i></i><i></i></div>
                                <span>Biblioteca local</span>
                                <strong>${this.loading ? 'Preparando IRIS Player…' : 'Elige dónde vive tu música'}</strong>
                                <p>IRIS no recorre el equipo. Selecciona una carpeta y solo sus archivos de audio formarán parte de la biblioteca.</p>
                                <button type="button" data-player-action="pick-folder" ${this.loading ? 'disabled' : ''}>${playerIcon('folder')} Seleccionar carpeta</button>
                            </section>
                        ` : `
                            <div class="iris-player-cover" style="${coverStyle}">
                                ${current?.artworkUrl ? `<img src="${escapeAttr(current.artworkUrl)}" alt="">` : ''}
                                <span>${escapeHtml(coverLetters)}</span><i></i><i></i>
                            </div>
                            <div class="iris-player-now">
                                <div>
                                    <span>Ahora suena</span>
                                    <strong>${escapeHtml(current?.title || 'Selecciona una canción')}</strong>
                                    <p>${escapeHtml(current?.artist || 'Biblioteca local')}${current?.album ? ` · ${escapeHtml(current.album)}` : ''}</p>
                                </div>
                                ${current ? `<button type="button" data-player-action="favorite" data-track-id="${escapeAttr(current.id)}" class="${current.favorite ? 'active' : ''}" aria-label="${current.favorite ? 'Quitar de favoritas' : 'Añadir a favoritas'}">${playerIcon('heart')}</button>` : ''}
                            </div>
                            <div class="iris-player-spectrum" aria-hidden="true">${bars}</div>
                            <div class="iris-player-progress">
                                <span data-player-elapsed>${formatDuration(this.state.elapsedSeconds)}</span>
                                <input type="range" min="0" max="${Math.max(0, current?.duration || 0)}" step="0.1" value="${Math.max(0, this.state.elapsedSeconds || 0)}" data-player-progress aria-label="Posición de reproducción">
                                <span data-player-duration>${formatDuration(current?.duration || 0)}</span>
                            </div>
                            <div class="iris-player-controls">
                                <button type="button" data-player-action="shuffle" class="${this.state.shuffle ? 'active' : ''}" aria-pressed="${this.state.shuffle}" aria-label="Aleatorio">${playerIcon('shuffle')}</button>
                                <button type="button" data-player-action="previous" aria-label="Anterior">${playerIcon('previous')}</button>
                                <button type="button" data-player-action="play-pause" class="iris-player-primary" aria-label="${playing ? 'Pausar' : 'Reproducir'}">${playerIcon(playing ? 'pause' : 'play')}</button>
                                <button type="button" data-player-action="next" aria-label="Siguiente">${playerIcon('next')}</button>
                                <button type="button" data-player-action="repeat" class="${this.state.repeat !== 'none' ? 'active' : ''}" aria-label="Repetición ${escapeAttr(this.state.repeat)}">${playerIcon('repeat')}<small>${this.state.repeat === 'one' ? '1' : ''}</small></button>
                            </div>
                            <label class="iris-player-volume">${playerIcon('volume')}<input type="range" min="0" max="1" step="0.01" value="${this.state.volume}" data-player-volume aria-label="Volumen"><span data-player-volume-label>${Math.round(this.state.volume * 100)}%</span></label>
                        `}
                    </main>

                    <aside class="iris-player-queue">
                        <header><div><span>En reproducción</span><strong>${queue.length} pistas</strong></div><button type="button" data-player-action="refresh" ${this.loading ? 'disabled' : ''} aria-label="Actualizar biblioteca">${playerIcon('refresh')}</button></header>
                        <div class="iris-player-queue-list">
                            ${queue.slice(0, 80).map((track, index) => `
                                <button type="button" data-player-track="${escapeAttr(track.id)}" class="${current?.id === track.id ? 'active' : ''}">
                                    <b>${String(index + 1).padStart(2, '0')}</b>
                                    <span class="iris-player-mini-cover" style="--player-cover-hue:${coverHue(track.id)}deg">${escapeHtml(track.title.slice(0, 1).toUpperCase())}</span>
                                    <span><strong>${escapeHtml(track.title)}</strong><small>${escapeHtml(track.artist)}</small></span>
                                    <time>${formatDuration(track.duration)}</time>
                                </button>
                            `).join('') || '<p class="iris-player-list-empty">La cola se construirá desde tu biblioteca.</p>'}
                        </div>
                        <section class="iris-player-recommendations">
                            <span>Selección local para ti</span>
                            ${recommendations.map((item) => `
                                <button type="button" data-player-action="play-recommendation" data-track-id="${escapeAttr(item.trackId)}" title="${escapeAttr(item.reasons.join('. '))}">
                                    <span class="iris-player-mini-cover" style="--player-cover-hue:${coverHue(item.trackId)}deg">${escapeHtml(item.track.title.slice(0, 1).toUpperCase())}</span>
                                    <span><strong>${escapeHtml(item.track.title)}</strong><small>${escapeHtml(item.reasons[0])}</small></span>
                                </button>
                            `).join('') || '<p>Las sugerencias aparecerán al conocer tus favoritos e historial local.</p>'}
                        </section>
                    </aside>
                </div>

                <footer class="iris-player-insight ${current ? '' : 'is-empty'}">
                    <div class="iris-player-insight-avatar">IRIS</div>
                    <div><span>${escapeHtml(insightLabel)}</span><p>${escapeHtml(insightText)}</p></div>
                    <strong>${hermesConnected ? 'Hermes disponible' : 'Modo local'}</strong>
                </footer>
                ${(this.error || this.notice) ? `<div class="iris-player-notice ${this.error ? 'is-error' : ''}" role="status">${escapeHtml(this.error || this.notice)}</div>` : ''}
            </section>
        `;
        if (restoreSearchFocus) {
            const search = this.surface.querySelector('[data-player-search]');
            search?.focus?.({ preventScroll: true });
            if (searchSelection && typeof search?.setSelectionRange === 'function') {
                const [start, end] = searchSelection;
                search.setSelectionRange(start ?? search.value.length, end ?? search.value.length);
            }
        }
        this._updateTimeControls();
    }
}
