/**
 * Pure, dependency-free state model for IRIS Player.
 *
 * The browser surface never needs filesystem paths or executable actions. The
 * backend exposes opaque track IDs and stream/artwork URLs; this module keeps
 * only that safe projection so local playback remains useful without Hermes.
 */

export const PLAYER_PLAYBACK_STATES = Object.freeze([
    'idle',
    'loading',
    'playing',
    'paused',
    'error',
]);

export const PLAYER_REPEAT_MODES = Object.freeze(['none', 'one', 'all']);

const PLAYBACK_STATE_SET = new Set(PLAYER_PLAYBACK_STATES);
const REPEAT_MODE_SET = new Set(PLAYER_REPEAT_MODES);
const PLAYER_INTENT_STOP_WORDS = new Set([
    'algo', 'cancion', 'canciones', 'con', 'de', 'del', 'el', 'en', 'la',
    'las', 'los', 'me', 'mi', 'musica', 'para', 'pon', 'poner', 'que', 'una',
]);

function clean(value = '') {
    return String(value ?? '').trim();
}

function cleanId(value = '') {
    const id = clean(value);
    if (!id || id.length > 256 || /[\r\n\0\\/]/.test(id) || /^[A-Za-z]:/.test(id)) return '';
    return id;
}

function finiteNumber(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
}

function nonNegativeNumber(value, fallback = 0) {
    return Math.max(0, finiteNumber(value, fallback));
}

function cleanStringList(value) {
    const source = Array.isArray(value)
        ? value
        : (typeof value === 'string' ? value.split(',') : []);
    const seen = new Set();
    return source.reduce((items, entry) => {
        const item = clean(entry);
        const key = item.toLocaleLowerCase();
        if (!item || seen.has(key)) return items;
        seen.add(key);
        items.push(item);
        return items;
    }, []);
}

function normalizedSearchText(value) {
    return clean(value)
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLocaleLowerCase();
}

/**
 * Accept an HTTP(S) backend URL or an origin-relative backend route. Local
 * paths, UNC paths, protocol-relative URLs and executable schemes are dropped.
 */
export function normalizePlayerBackendUrl(value) {
    const candidate = clean(value);
    if (!candidate || /[\r\n\0]/.test(candidate)) return '';
    if (candidate.startsWith('/') && !candidate.startsWith('//') && !candidate.includes('\\')) {
        return candidate;
    }
    try {
        const parsed = new URL(candidate);
        if ((parsed.protocol === 'http:' || parsed.protocol === 'https:') && !parsed.username && !parsed.password) {
            return parsed.href;
        }
    } catch {
        // A non-URL value is intentionally not interpreted as a local path.
    }
    return '';
}

function normalizeDate(value) {
    if (value === null || value === undefined || value === '') return null;
    const timestamp = typeof value === 'number' ? value : Date.parse(String(value));
    if (!Number.isFinite(timestamp)) return null;
    const milliseconds = timestamp < 1e12 ? timestamp * 1000 : timestamp;
    const date = new Date(milliseconds);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function normalizeTrack(entry = {}) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return null;
    const id = cleanId(entry.id ?? entry.track_id ?? entry.trackId);
    if (!id) return null;

    const title = clean(entry.title ?? entry.name) || 'Pista sin título';
    const artist = clean(entry.artist ?? entry.artist_name ?? entry.artistName) || 'Artista desconocido';
    const album = clean(entry.album ?? entry.album_title ?? entry.albumTitle);
    const genre = clean(entry.genre);
    const tags = cleanStringList(entry.tags);
    const streamUrl = normalizePlayerBackendUrl(
        entry.stream_url ?? entry.streamUrl ?? entry.audio_url ?? entry.audioUrl ?? entry.url,
    );
    const artworkUrl = normalizePlayerBackendUrl(
        entry.artwork_url ?? entry.artworkUrl ?? entry.cover_url ?? entry.coverUrl,
    );

    return Object.freeze({
        id,
        title,
        artist,
        album,
        duration: nonNegativeNumber(entry.duration ?? entry.duration_seconds ?? entry.durationSeconds),
        genre,
        tags: Object.freeze(tags),
        favorite: Boolean(entry.favorite ?? entry.is_favorite ?? entry.isFavorite),
        playCount: Math.floor(nonNegativeNumber(entry.play_count ?? entry.playCount)),
        lastPlayed: normalizeDate(entry.last_played ?? entry.lastPlayed),
        streamUrl,
        artworkUrl,
    });
}

function normalizePlaylist(entry = {}, validTrackIds = new Set()) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return null;
    const id = cleanId(entry.id ?? entry.playlist_id ?? entry.playlistId);
    if (!id) return null;
    const trackIds = cleanStringList(entry.track_ids ?? entry.trackIds)
        .filter((trackId) => validTrackIds.has(trackId));
    return Object.freeze({
        id,
        name: clean(entry.name) || 'Playlist sin nombre',
        trackIds: Object.freeze(trackIds),
    });
}

/** Normalize the untrusted library payload to the only fields the UI needs. */
export function normalizePlayerLibrary(payload = {}) {
    const source = payload && typeof payload === 'object' && !Array.isArray(payload) ? payload : {};
    const rawTracks = Array.isArray(source.tracks)
        ? source.tracks
        : (Array.isArray(source.items) ? source.items : []);
    const seenTracks = new Set();
    const tracks = [];
    for (const rawTrack of rawTracks) {
        const track = normalizeTrack(rawTrack);
        if (!track || seenTracks.has(track.id)) continue;
        seenTracks.add(track.id);
        tracks.push(track);
    }

    const rawPlaylists = Array.isArray(source.playlists) ? source.playlists : [];
    const seenPlaylists = new Set();
    const playlists = [];
    for (const rawPlaylist of rawPlaylists) {
        const playlist = normalizePlaylist(rawPlaylist, seenTracks);
        if (!playlist || seenPlaylists.has(playlist.id)) continue;
        seenPlaylists.add(playlist.id);
        playlists.push(playlist);
    }

    return Object.freeze({
        id: clean(source.id ?? source.library_id ?? source.libraryId) || 'local',
        revision: clean(source.revision ?? source.etag),
        updatedAt: normalizeDate(source.updated_at ?? source.updatedAt),
        tracks: Object.freeze(tracks),
        playlists: Object.freeze(playlists),
        total: tracks.length,
    });
}

/** Stable local filtering; source order is preserved unless a sort is asked. */
export function filterPlayerTracks(tracks = [], filters = {}) {
    const list = Array.isArray(tracks) ? tracks : [];
    const query = normalizedSearchText(filters.query);
    const artist = normalizedSearchText(filters.artist);
    const album = normalizedSearchText(filters.album);
    const genre = normalizedSearchText(filters.genre);
    const requiredTags = cleanStringList(filters.tags).map(normalizedSearchText);
    const favoriteOnly = filters.favorite === true || filters.favorites === true;

    const filtered = list.filter((track) => {
        if (!track || !clean(track.id)) return false;
        if (favoriteOnly && track.favorite !== true) return false;
        if (artist && normalizedSearchText(track.artist) !== artist) return false;
        if (album && normalizedSearchText(track.album) !== album) return false;
        if (genre && normalizedSearchText(track.genre) !== genre) return false;
        const trackTags = (Array.isArray(track.tags) ? track.tags : []).map(normalizedSearchText);
        if (requiredTags.some((tag) => !trackTags.includes(tag))) return false;
        if (!query) return true;
        const haystack = [
            track.title,
            track.artist,
            track.album,
            track.genre,
            ...(Array.isArray(track.tags) ? track.tags : []),
        ].map(normalizedSearchText).join(' ');
        return haystack.includes(query);
    });

    const sort = clean(filters.sort).toLocaleLowerCase();
    if (!sort) return filtered;
    return filtered.map((track, index) => ({ track, index })).sort((left, right) => {
        let result = 0;
        if (sort === 'title') result = left.track.title.localeCompare(right.track.title);
        if (sort === 'artist') result = left.track.artist.localeCompare(right.track.artist);
        if (sort === 'recent') {
            result = Date.parse(right.track.lastPlayed || 0) - Date.parse(left.track.lastPlayed || 0);
        }
        if (sort === 'plays') result = right.track.playCount - left.track.playCount;
        return result || left.index - right.index;
    }).map(({ track }) => track);
}

function safeRandomIndex(rng, length) {
    if (length <= 1) return 0;
    const value = finiteNumber(typeof rng === 'function' ? rng() : Math.random(), 0);
    const normalized = Math.min(0.9999999999999999, Math.max(0, value));
    return Math.floor(normalized * length);
}

function shuffleIds(ids, rng) {
    const result = [...ids];
    for (let index = result.length - 1; index > 0; index -= 1) {
        const swapIndex = safeRandomIndex(rng, index + 1);
        [result[index], result[swapIndex]] = [result[swapIndex], result[index]];
    }
    return result;
}

/** Build a queue made exclusively from known track IDs. */
export function buildPlayerQueue(tracks = [], options = {}) {
    const validIds = new Set((Array.isArray(tracks) ? tracks : []).map((track) => clean(track?.id)).filter(Boolean));
    const requestedIds = Array.isArray(options.trackIds)
        ? options.trackIds.map(cleanId)
        : [...validIds];
    const seen = new Set();
    let ids = requestedIds.filter((id) => {
        if (!validIds.has(id) || seen.has(id)) return false;
        seen.add(id);
        return true;
    });

    const startId = cleanId(options.startId);
    if (options.shuffle === true) {
        if (startId && ids.includes(startId)) {
            ids = [startId, ...shuffleIds(ids.filter((id) => id !== startId), options.rng)];
        } else {
            ids = shuffleIds(ids, options.rng);
        }
    } else if (startId && ids.includes(startId) && options.startFirst === true) {
        ids = [startId, ...ids.filter((id) => id !== startId)];
    }
    return ids;
}

export function normalizePlayerCapabilities(capabilities = {}) {
    const hermesConnected = capabilities.hermesConnected === true
        || capabilities.hermes_connected === true
        || capabilities?.hermes?.connected === true;
    return Object.freeze({
        mode: 'local',
        localPlayback: true,
        localRecommendations: true,
        hermesConnected,
        hermesStatus: hermesConnected ? 'connected' : 'disconnected',
        hermesSuggestions: hermesConnected && capabilities.hermesSuggestions !== false,
    });
}

function initialQueue(library, options) {
    const baseQueueIds = buildPlayerQueue(library.tracks, { trackIds: options.queueIds });
    const queueIds = buildPlayerQueue(library.tracks, {
        trackIds: baseQueueIds,
        startId: options.currentTrackId,
        shuffle: options.shuffle === true,
        rng: options.rng,
    });
    const requestedId = cleanId(options.currentTrackId);
    const currentTrackId = queueIds.includes(requestedId) ? requestedId : null;
    return {
        baseQueueIds,
        queueIds,
        queueSource: Array.isArray(options.queueIds) ? 'selection' : 'library',
        currentTrackId,
        queueIndex: currentTrackId ? queueIds.indexOf(currentTrackId) : -1,
    };
}

export function createPlayerState(options = {}) {
    const library = normalizePlayerLibrary(options.library);
    const queue = initialQueue(library, options);
    const requestedPlayback = clean(options.playback);
    const playback = PLAYBACK_STATE_SET.has(requestedPlayback) ? requestedPlayback : 'idle';
    return {
        library,
        ...queue,
        playback: queue.currentTrackId ? playback : 'idle',
        repeat: REPEAT_MODE_SET.has(options.repeat) ? options.repeat : 'none',
        shuffle: options.shuffle === true,
        elapsedSeconds: nonNegativeNumber(options.elapsedSeconds),
        volume: Math.min(1, nonNegativeNumber(options.volume, 0.8)),
        error: playback === 'error' ? clean(options.error) : '',
        capabilities: normalizePlayerCapabilities(options.capabilities),
    };
}

export function currentPlayerTrack(state = {}) {
    const id = cleanId(state.currentTrackId);
    return (Array.isArray(state?.library?.tracks) ? state.library.tracks : [])
        .find((track) => track.id === id) || null;
}

function stateAtIndex(state, queueIndex, playback = 'playing') {
    const currentTrackId = state.queueIds[queueIndex] || null;
    return {
        ...state,
        currentTrackId,
        queueIndex: currentTrackId ? queueIndex : -1,
        playback: currentTrackId ? playback : 'idle',
        elapsedSeconds: 0,
        error: '',
    };
}

export function nextPlayerState(state, options = {}) {
    if (!state || !Array.isArray(state.queueIds) || !state.queueIds.length) return state;
    if (options.fromEnded === true && state.repeat === 'one' && state.currentTrackId) {
        return { ...state, playback: 'playing', elapsedSeconds: 0, error: '' };
    }
    const currentIndex = state.queueIds.indexOf(state.currentTrackId);
    const nextIndex = currentIndex < 0 ? 0 : currentIndex + 1;
    if (nextIndex < state.queueIds.length) return stateAtIndex(state, nextIndex);
    if (state.repeat === 'all') return stateAtIndex(state, 0);
    return {
        ...state,
        currentTrackId: null,
        queueIndex: -1,
        playback: 'idle',
        elapsedSeconds: 0,
        error: '',
    };
}

export function previousPlayerState(state) {
    if (!state || !Array.isArray(state.queueIds) || !state.queueIds.length) return state;
    if (state.currentTrackId && nonNegativeNumber(state.elapsedSeconds) > 3) {
        return { ...state, playback: 'playing', elapsedSeconds: 0, error: '' };
    }
    const currentIndex = state.queueIds.indexOf(state.currentTrackId);
    if (currentIndex > 0) return stateAtIndex(state, currentIndex - 1);
    if ((currentIndex === 0 || currentIndex < 0) && state.repeat === 'all') {
        return stateAtIndex(state, state.queueIds.length - 1);
    }
    return currentIndex === 0
        ? { ...state, playback: 'playing', elapsedSeconds: 0, error: '' }
        : stateAtIndex(state, 0);
}

/** Preserve the active track by ID and choose the nearest survivor if removed. */
export function refreshPlayerLibrary(state, payload) {
    const library = normalizePlayerLibrary(payload);
    const validIds = new Set(library.tracks.map((track) => track.id));
    const oldQueue = Array.isArray(state?.queueIds) ? state.queueIds : [];
    const oldBaseQueue = Array.isArray(state?.baseQueueIds) ? state.baseQueueIds : oldQueue;
    const oldIndex = oldQueue.indexOf(state?.currentTrackId);
    const queueSource = state?.queueSource === 'selection' ? 'selection' : 'library';
    const baseQueueIds = oldBaseQueue.filter((id) => validIds.has(id));
    const queueIds = oldQueue.filter((id) => validIds.has(id));
    if (queueSource === 'library') {
        for (const track of library.tracks) {
            if (!baseQueueIds.includes(track.id)) baseQueueIds.push(track.id);
            if (!queueIds.includes(track.id)) queueIds.push(track.id);
        }
    }

    let currentTrackId = validIds.has(state?.currentTrackId) ? state.currentTrackId : null;
    if (!currentTrackId && queueIds.length && oldIndex >= 0) {
        currentTrackId = queueIds[Math.min(oldIndex, queueIds.length - 1)] || null;
    }
    const queueIndex = currentTrackId ? queueIds.indexOf(currentTrackId) : -1;
    const playback = currentTrackId ? state.playback : 'idle';
    return {
        ...state,
        library,
        baseQueueIds,
        queueIds,
        queueSource,
        currentTrackId,
        queueIndex,
        playback,
        elapsedSeconds: currentTrackId ? nonNegativeNumber(state.elapsedSeconds) : 0,
        error: playback === 'error' ? clean(state.error) : '',
    };
}

function setShuffle(state, enabled, rng) {
    if (enabled === state.shuffle) return state;
    const currentId = state.currentTrackId;
    if (!enabled) {
        const queueIds = buildPlayerQueue(state.library.tracks, {
            trackIds: Array.isArray(state.baseQueueIds) ? state.baseQueueIds : state.queueIds,
        });
        return { ...state, shuffle: false, queueIds, queueIndex: currentId ? queueIds.indexOf(currentId) : -1 };
    }
    const queueIds = buildPlayerQueue(state.library.tracks, {
        trackIds: Array.isArray(state.baseQueueIds) ? state.baseQueueIds : state.queueIds,
        startId: currentId,
        shuffle: true,
        rng,
    });
    return { ...state, shuffle: true, queueIds, queueIndex: currentId ? 0 : -1 };
}

/** Reducer for the finite playback state. Unknown actions are inert. */
export function reducePlayerState(state = createPlayerState(), action = {}, options = {}) {
    if (!action || typeof action.type !== 'string') return state;
    switch (action.type) {
        case 'LIBRARY_LOADING':
            return { ...state, playback: 'loading', error: '' };
        case 'LIBRARY_REFRESH':
            return refreshPlayerLibrary(state, action.payload);
        case 'LIBRARY_ERROR':
        case 'PLAYBACK_ERROR':
            return { ...state, playback: 'error', error: clean(action.error) || 'No se pudo reproducir la pista.' };
        case 'PLAY_TRACK': { // ID only: no URL, path or arbitrary action is accepted here.
            const trackId = cleanId(action.trackId);
            if (!state.library.tracks.some((track) => track.id === trackId)) return state;
            let queueIds = state.queueIds.includes(trackId) ? state.queueIds : buildPlayerQueue(state.library.tracks);
            if (!queueIds.includes(trackId)) queueIds = [trackId];
            const resetToLibrary = !state.queueIds.includes(trackId);
            return {
                ...state,
                baseQueueIds: resetToLibrary ? buildPlayerQueue(state.library.tracks) : state.baseQueueIds,
                queueIds,
                queueSource: resetToLibrary ? 'library' : state.queueSource,
                currentTrackId: trackId,
                queueIndex: queueIds.indexOf(trackId),
                playback: 'playing',
                elapsedSeconds: 0,
                error: '',
            };
        }
        case 'PLAY':
            if (!currentPlayerTrack(state)) return state;
            return { ...state, playback: 'playing', error: '' };
        case 'PAUSE':
            if (!currentPlayerTrack(state)) return state;
            return { ...state, playback: 'paused', error: '' };
        case 'STOP':
            return {
                ...state,
                currentTrackId: null,
                queueIndex: -1,
                playback: 'idle',
                elapsedSeconds: 0,
                error: '',
            };
        case 'NEXT':
            return nextPlayerState(state);
        case 'PREVIOUS':
            return previousPlayerState(state);
        case 'TRACK_ENDED':
            return nextPlayerState(state, { fromEnded: true });
        case 'SET_REPEAT': {
            const repeat = clean(action.repeat);
            return REPEAT_MODE_SET.has(repeat) ? { ...state, repeat } : state;
        }
        case 'SET_SHUFFLE':
            return setShuffle(state, action.enabled === true, options.rng);
        case 'SET_ELAPSED':
            return { ...state, elapsedSeconds: nonNegativeNumber(action.seconds) };
        case 'SET_VOLUME':
            return { ...state, volume: Math.min(1, nonNegativeNumber(action.volume)) };
        case 'SET_CAPABILITIES':
            return { ...state, capabilities: normalizePlayerCapabilities(action.capabilities) };
        default:
            return state;
    }
}

function affinityFromLibrary(tracks) {
    const genre = new Map();
    const tags = new Map();
    for (const track of tracks) {
        const weight = (track.favorite ? 3 : 0) + Math.min(2, Math.log2(1 + track.playCount));
        if (weight <= 0) continue;
        const genreKey = normalizedSearchText(track.genre);
        if (genreKey) genre.set(genreKey, (genre.get(genreKey) || 0) + weight);
        for (const tag of track.tags) {
            const tagKey = normalizedSearchText(tag);
            if (tagKey) tags.set(tagKey, (tags.get(tagKey) || 0) + weight);
        }
    }
    return { genre, tags };
}

/**
 * Deterministic, explainable recommendations from local metadata only.
 * No network or Hermes dependency is introduced by this ranking function.
 */
export function recommendLocalTracks(libraryOrTracks = [], options = {}) {
    const tracks = Array.isArray(libraryOrTracks)
        ? libraryOrTracks
        : (Array.isArray(libraryOrTracks?.tracks) ? libraryOrTracks.tracks : []);
    const limit = Math.max(0, Math.floor(finiteNumber(options.limit, 10)));
    const excluded = new Set(cleanStringList(options.excludeIds));
    const preferredGenres = new Set(cleanStringList(options.preferredGenres).map(normalizedSearchText));
    const preferredTags = new Set(cleanStringList(options.preferredTags).map(normalizedSearchText));
    const intent = normalizedSearchText(options.intent);
    const intentTokens = [...new Set(intent.split(/[^a-z0-9]+/u).filter((token) => (
        token.length >= 3 && !PLAYER_INTENT_STOP_WORDS.has(token)
    )))];
    const affinities = affinityFromLibrary(tracks);
    const now = finiteNumber(options.now, Date.now());

    return tracks.filter((track) => !excluded.has(track.id)).map((track, index) => {
        let score = 0;
        const reasons = [];
        if (intentTokens.length) {
            const searchable = normalizedSearchText([
                track.title,
                track.artist,
                track.album,
                track.genre,
                ...track.tags,
            ].join(' '));
            const matchingIntentTokens = intentTokens.filter((token) => searchable.includes(token));
            if (matchingIntentTokens.length) {
                // An explicit request must outweigh passive history/favorite
                // affinity; otherwise "algo energético" could still pick the
                // user's most-played calm track.
                score += Math.min(24, matchingIntentTokens.length * 12);
                reasons.push(`Coincide con ${matchingIntentTokens.join(', ')}`);
            }
        }
        if (track.favorite) {
            score += 6;
            reasons.push('Está en tus favoritos');
        }
        if (track.playCount > 0) {
            score += Math.min(4, Math.log2(1 + track.playCount));
            reasons.push(`La has reproducido ${track.playCount} ${track.playCount === 1 ? 'vez' : 'veces'}`);
        }

        const genreKey = normalizedSearchText(track.genre);
        if (genreKey && preferredGenres.has(genreKey)) {
            score += 5;
            reasons.push(`Coincide con el género ${track.genre}`);
        } else if (genreKey && (affinities.genre.get(genreKey) || 0) >= 3) {
            score += 2;
            reasons.push(`Se parece a géneros que escuchas`);
        }

        const matchingTags = track.tags.filter((tag) => preferredTags.has(normalizedSearchText(tag)));
        if (matchingTags.length) {
            score += Math.min(6, matchingTags.length * 3);
            reasons.push(`Coincide con ${matchingTags.join(', ')}`);
        } else if (track.tags.some((tag) => (affinities.tags.get(normalizedSearchText(tag)) || 0) >= 3)) {
            score += 1.5;
            reasons.push('Comparte etiquetas con música que disfrutas');
        }

        if (track.lastPlayed) {
            const ageDays = Math.max(0, (now - Date.parse(track.lastPlayed)) / 86_400_000);
            if (ageDays <= 7) {
                score += 1;
                reasons.push('La escuchaste recientemente');
            }
        }
        if (!reasons.length) reasons.push('Está disponible en tu biblioteca local');
        return { track, trackId: track.id, score: Number(score.toFixed(3)), reasons, index };
    }).sort((left, right) => (
        right.score - left.score
        || left.track.title.localeCompare(right.track.title)
        || left.trackId.localeCompare(right.trackId)
        || left.index - right.index
    )).slice(0, limit).map(({ index: _index, ...recommendation }) => recommendation);
}
