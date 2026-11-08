import test from 'node:test';
import assert from 'node:assert/strict';

import { IrisPlayerApp } from '../../face/js/iris_player.js';
import {
    buildPlayerQueue,
    createPlayerState,
    currentPlayerTrack,
    filterPlayerTracks,
    nextPlayerState,
    normalizePlayerBackendUrl,
    normalizePlayerLibrary,
    previousPlayerState,
    recommendLocalTracks,
    reducePlayerState,
    refreshPlayerLibrary,
} from '../../face/js/iris_player_state.js';

const payload = {
    id: 'main',
    updated_at: '2026-08-24T12:00:00Z',
    tracks: [
        {
            id: 'track-1',
            title: 'Lluvia Café',
            artist: 'Aurora',
            album: 'Focus',
            duration_seconds: 180,
            genre: 'Ambient',
            tags: ['tranquila', 'focus'],
            favorite: true,
            play_count: 7,
            last_played: '2026-08-23T12:00:00Z',
            stream_url: '/api/player/tracks/track-1/stream',
            cover_url: 'https://backend.example/art/track-1',
            path: 'C:\\Users\\someone\\Music\\secret.mp3',
            shell_command: 'remove-everything',
        },
        {
            id: 'track-2',
            title: 'Neón',
            artist: 'Vector',
            album: 'Night',
            genre: 'Synthwave',
            tags: ['energética'],
            play_count: 2,
            stream_url: 'file:///C:/Music/neon.mp3',
        },
        {
            id: 'track-3',
            title: 'Brisa',
            artist: 'Aurora',
            genre: 'Ambient',
            tags: ['tranquila'],
            stream_url: '/api/player/tracks/track-3/stream',
        },
        { id: 'track-1', title: 'Duplicada' },
        { title: 'Sin identificador' },
        { id: 'C:\\Music\\leak.mp3', title: 'Ruta usada como ID' },
    ],
    playlists: [
        { id: 'focus', name: 'Focus', track_ids: ['track-1', 'missing', 'track-3'] },
    ],
};

test('normalizes a library to IDs and backend URLs without retaining filesystem paths or actions', () => {
    const library = normalizePlayerLibrary(payload);

    assert.equal(library.total, 3);
    assert.equal(library.tracks[0].streamUrl, '/api/player/tracks/track-1/stream');
    assert.equal(library.tracks[0].artworkUrl, 'https://backend.example/art/track-1');
    assert.equal(library.tracks[1].streamUrl, '');
    assert.equal('path' in library.tracks[0], false);
    assert.equal('shell_command' in library.tracks[0], false);
    assert.deepEqual(library.playlists[0].trackIds, ['track-1', 'track-3']);
    assert.equal(normalizePlayerBackendUrl('C:\\Music\\track.mp3'), '');
    assert.equal(normalizePlayerBackendUrl('javascript:alert(1)'), '');
    assert.equal(normalizePlayerBackendUrl('//other-host/audio'), '');
    assert.equal(normalizePlayerBackendUrl('https://user:secret@backend.example/audio'), '');
});

test('searches accent-insensitively and combines local metadata filters', () => {
    const { tracks } = normalizePlayerLibrary(payload);

    assert.deepEqual(filterPlayerTracks(tracks, { query: 'lluvia cafe' }).map(({ id }) => id), ['track-1']);
    assert.deepEqual(filterPlayerTracks(tracks, { artist: 'Aurora', tags: ['tranquila'] }).map(({ id }) => id), ['track-1', 'track-3']);
    assert.deepEqual(filterPlayerTracks(tracks, { favorites: true }).map(({ id }) => id), ['track-1']);
    assert.deepEqual(filterPlayerTracks(tracks, { sort: 'plays' }).map(({ id }) => id), ['track-1', 'track-2', 'track-3']);
});

test('builds a deterministic queue, rejects unknown IDs and uses the injected RNG for shuffle', () => {
    const { tracks } = normalizePlayerLibrary(payload);
    const ordered = buildPlayerQueue(tracks, { trackIds: ['track-3', 'missing', 'track-1', 'track-3'] });
    const randomValues = [0, 0.5];
    const shuffled = buildPlayerQueue(tracks, {
        startId: 'track-2',
        shuffle: true,
        rng: () => randomValues.shift(),
    });

    assert.deepEqual(ordered, ['track-3', 'track-1']);
    assert.deepEqual(shuffled, ['track-2', 'track-3', 'track-1']);
});

test('playback reducer follows idle, loading, playing, paused and error without depending on Hermes', () => {
    let state = createPlayerState({ library: payload });
    assert.equal(state.playback, 'idle');
    assert.deepEqual(state.capabilities, {
        mode: 'local',
        localPlayback: true,
        localRecommendations: true,
        hermesConnected: false,
        hermesStatus: 'disconnected',
        hermesSuggestions: false,
    });

    state = reducePlayerState(state, { type: 'LIBRARY_LOADING' });
    assert.equal(state.playback, 'loading');
    state = reducePlayerState(state, { type: 'PLAY_TRACK', trackId: 'track-2', path: 'C:\\secret.mp3' });
    assert.equal(state.playback, 'playing');
    assert.equal(currentPlayerTrack(state)?.id, 'track-2');
    state = reducePlayerState(state, { type: 'PAUSE' });
    assert.equal(state.playback, 'paused');
    state = reducePlayerState(state, { type: 'PLAY' });
    assert.equal(state.playback, 'playing');
    state = reducePlayerState(state, { type: 'PLAYBACK_ERROR', error: 'Formato dañado' });
    assert.equal(state.playback, 'error');
    assert.equal(state.error, 'Formato dañado');

    state = reducePlayerState(state, { type: 'STOP' });
    assert.equal(state.playback, 'idle');
    assert.equal(state.currentTrackId, null);
    assert.equal(state.queueIndex, -1);

    const unchanged = reducePlayerState(state, { type: 'RUN_COMMAND', command: 'anything' });
    assert.equal(unchanged, state);
});

test('next, previous and repeat modes handle track boundaries predictably', () => {
    let state = createPlayerState({ library: payload });
    state = reducePlayerState(state, { type: 'PLAY_TRACK', trackId: 'track-3' });
    state = nextPlayerState(state);
    assert.equal(state.currentTrackId, null);
    assert.equal(state.playback, 'idle');

    state = reducePlayerState(state, { type: 'PLAY_TRACK', trackId: 'track-3' });
    state = reducePlayerState(state, { type: 'SET_REPEAT', repeat: 'all' });
    state = nextPlayerState(state);
    assert.equal(state.currentTrackId, 'track-1');

    state = reducePlayerState(state, { type: 'SET_REPEAT', repeat: 'one' });
    const repeated = reducePlayerState(state, { type: 'TRACK_ENDED' });
    assert.equal(repeated.currentTrackId, 'track-1');
    assert.equal(repeated.playback, 'playing');

    state = reducePlayerState(state, { type: 'SET_ELAPSED', seconds: 12 });
    const restarted = previousPlayerState(state);
    assert.equal(restarted.currentTrackId, 'track-1');
    assert.equal(restarted.elapsedSeconds, 0);
});

test('shuffle toggle keeps the current track first and is reproducible with an injected RNG', () => {
    let state = createPlayerState({
        library: payload,
        queueIds: ['track-1', 'track-2', 'track-3'],
        currentTrackId: 'track-2',
        playback: 'playing',
    });
    const randomValues = [0];
    state = reducePlayerState(
        state,
        { type: 'SET_SHUFFLE', enabled: true },
        { rng: () => randomValues.shift() },
    );

    assert.equal(state.shuffle, true);
    assert.equal(state.currentTrackId, 'track-2');
    assert.equal(state.queueIds[0], 'track-2');
    assert.deepEqual(state.queueIds, ['track-2', 'track-3', 'track-1']);

    state = reducePlayerState(state, { type: 'SET_SHUFFLE', enabled: false });
    assert.deepEqual(state.queueIds, ['track-1', 'track-2', 'track-3']);
    assert.equal(state.currentTrackId, 'track-2');
});

test('a selected queue keeps its scope when shuffle changes or the library refreshes', () => {
    let state = createPlayerState({
        library: payload,
        queueIds: ['track-3', 'track-1'],
        currentTrackId: 'track-3',
        playback: 'playing',
    });
    state = reducePlayerState(state, { type: 'SET_SHUFFLE', enabled: true }, { rng: () => 0 });
    state = reducePlayerState(state, { type: 'SET_SHUFFLE', enabled: false });
    assert.deepEqual(state.queueIds, ['track-3', 'track-1']);

    state = refreshPlayerLibrary(state, {
        tracks: [
            { id: 'track-1', title: 'Lluvia' },
            { id: 'track-3', title: 'Brisa' },
            { id: 'track-4', title: 'Nueva' },
        ],
    });
    assert.deepEqual(state.queueIds, ['track-3', 'track-1']);
    assert.equal(state.queueIds.includes('track-4'), false);
});

test('library refresh preserves the current ID and fresh metadata, or chooses the nearest survivor', () => {
    let state = createPlayerState({ library: payload });
    state = reducePlayerState(state, { type: 'PLAY_TRACK', trackId: 'track-2' });
    state = reducePlayerState(state, { type: 'SET_ELAPSED', seconds: 42 });

    const refreshed = refreshPlayerLibrary(state, {
        tracks: [
            { id: 'track-1', title: 'Lluvia Café' },
            { id: 'track-2', title: 'Neón remasterizado', stream_url: '/api/player/tracks/track-2/stream' },
            { id: 'track-4', title: 'Nueva' },
        ],
    });
    assert.equal(refreshed.currentTrackId, 'track-2');
    assert.equal(currentPlayerTrack(refreshed)?.title, 'Neón remasterizado');
    assert.equal(refreshed.elapsedSeconds, 42);

    const withoutCurrent = refreshPlayerLibrary(refreshed, {
        tracks: [
            { id: 'track-1', title: 'Lluvia Café' },
            { id: 'track-4', title: 'Nueva' },
        ],
    });
    assert.equal(withoutCurrent.currentTrackId, 'track-4');
    assert.equal(withoutCurrent.queueIndex, 1);
});

test('local recommendations are deterministic and explain favorite, history, genre and tag signals', () => {
    const library = normalizePlayerLibrary(payload);
    const recommendations = recommendLocalTracks(library, {
        preferredGenres: ['Ambient'],
        preferredTags: ['tranquila'],
        excludeIds: ['track-2'],
        now: Date.parse('2026-08-24T12:00:00Z'),
        limit: 2,
    });

    assert.deepEqual(recommendations.map(({ trackId }) => trackId), ['track-1', 'track-3']);
    assert.ok(recommendations[0].score > recommendations[1].score);
    assert.ok(recommendations[0].reasons.includes('Está en tus favoritos'));
    assert.ok(recommendations[0].reasons.includes('Coincide con el género Ambient'));
    assert.ok(recommendations[0].reasons.includes('Coincide con tranquila'));
});

test('intent recommendations use the requested local metadata instead of ignoring the prompt', () => {
    const library = normalizePlayerLibrary(payload);
    const calm = recommendLocalTracks(library, {
        intent: 'pon música tranquila',
        limit: 3,
    });
    const energetic = recommendLocalTracks(library, {
        intent: 'quiero algo energética',
        limit: 3,
    });

    assert.equal(calm[0].trackId, 'track-1');
    assert.match(calm[0].reasons[0], /tranquila/i);
    assert.equal(energetic[0].trackId, 'track-2');
    assert.match(energetic[0].reasons[0], /energetica/i);
});

test('structured playback acknowledgement reports the state observed by the audio element', async () => {
    globalThis.localStorage = {
        getItem: () => null,
        setItem: () => {},
    };
    const player = new IrisPlayerApp();
    player.meta = { configured: true };
    player.state = createPlayerState({ library: payload });
    player.audio = { paused: true };
    player.load = async () => {};
    player._syncAudio = async () => {
        player.audio.paused = false;
        return true;
    };

    const result = await player.handleIntent({
        schema: 'iris.app-intent/v1',
        app: 'player',
        action: 'play',
        args: { track_id: 'track-1' },
        request_id: 'req-confirmed',
    });

    assert.equal(result.status, 'ok');
    assert.equal(result.data.title, 'Lluvia Café');
    assert.equal(result.data.playback_state, 'playing');
});

test('playback confirmation resolves from the playing event when play promise stays pending', async () => {
    globalThis.localStorage = {
        getItem: () => null,
        setItem: () => {},
    };
    const player = new IrisPlayerApp();
    const audio = new EventTarget();
    Object.assign(audio, {
        paused: true,
        readyState: 0,
        volume: 0.8,
        currentTime: 0,
        getAttribute: () => '',
        load: () => {},
        play: () => {
            audio.paused = false;
            audio.readyState = 4;
            queueMicrotask(() => audio.dispatchEvent(new Event('playing')));
            return new Promise(() => {});
        },
    });
    player.audio = audio;
    player.state = reducePlayerState(createPlayerState({ library: payload }), {
        type: 'PLAY_TRACK',
        trackId: 'track-1',
    });

    const started = await Promise.race([
        player._syncAudio(true),
        new Promise((resolve) => setTimeout(() => resolve('test-timeout'), 500)),
    ]);

    assert.equal(started, true);
});
