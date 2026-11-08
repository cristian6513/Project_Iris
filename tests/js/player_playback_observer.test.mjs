import test from 'node:test';
import assert from 'node:assert/strict';
import { observePlayerPlayback, playerPlaybackSnapshot } from '../../face/js/player_playback_observer.js';

class Media extends EventTarget {
    src = ''; paused = true; ended = false; seeking = false; readyState = 0;
    muted = false; volume = 0.8; error = null;
    getAttribute() { return this.src; }
    emit(name, props = {}) { Object.assign(this, props); this.dispatchEvent(new Event(name)); }
    play() { throw Error('Observer must not play audio'); }
    load() { throw Error('Observer must not load audio'); }
}
const track = { streamUrl: '/api/player/tracks/one/stream', title: 'Una canción', artist: 'Artista', path: 'PRIVATE', secret: 'PRIVATE' };
const setup = () => {
    const media = new Media(), states = [];
    const observer = observePlayerPlayback(media, () => track, s => states.push(s));
    return { media, states, observer, last: () => states.at(-1) };
};

test('decorative playback remains idle on creation, and never starts media', () => {
    const { states, observer } = setup();
    assert.deepEqual(states, [{ status: 'idle', muted: false, title: '', artist: '' }]);
    observer.refresh(); assert.equal(states.length, 1);
});

test('play is only a request: room waits for playing even with buffered media', () => {
    const { media, last } = setup();
    media.emit('play', { src: track.streamUrl, paused: false, readyState: 4 });
    assert.equal(last().status, 'loading');
    media.emit('playing'); assert.equal(last().status, 'playing');
    media.emit('pause', { paused: true }); assert.equal(last().status, 'paused');
});

test('waiting and seek suspend activity, but a network stall with buffered audio does not', () => {
    const { media, last } = setup();
    media.emit('playing', { src: track.streamUrl, paused: false, readyState: 4 });
    media.emit('stalled'); assert.equal(last().status, 'playing');
    media.emit('waiting'); assert.equal(last().status, 'loading');
    media.emit('playing'); assert.equal(last().status, 'playing');
    media.emit('seeking', { seeking: true }); assert.equal(last().status, 'loading');
    media.emit('seeked', { seeking: false }); assert.equal(last().status, 'playing');
});

test('error, end and source removal cannot leave the room playing', () => {
    const { media, last } = setup();
    media.emit('playing', { src: track.streamUrl, paused: false, readyState: 4 });
    media.emit('error', { error: { code: 3 } }); assert.equal(last().status, 'error');
    media.emit('emptied', { error: null, src: '', paused: true }); assert.equal(last().status, 'idle');
    media.emit('playing', { src: track.streamUrl, paused: false });
    media.emit('ended', { ended: true }); assert.equal(last().status, 'idle');
});

test('snapshot sends bounded display fields only, with no URL, paths or stale track title', () => {
    const media = new Media(); media.src = track.streamUrl;
    const snapshot = playerPlaybackSnapshot(media, { ...track, title: 'X\n'.repeat(150) });
    assert.deepEqual(Object.keys(snapshot), ['status', 'muted', 'title', 'artist']);
    assert.ok(snapshot.title.length <= 96); assert.ok(!snapshot.title.includes('\n'));
    assert.ok(Object.isFrozen(snapshot));
    assert.equal(playerPlaybackSnapshot(media, { ...track, streamUrl: '/different' }).title, '');
});

test('volume and mute update indicators without timeupdate polling', () => {
    const { media, last, states } = setup();
    media.emit('playing', { src: track.streamUrl, paused: false, readyState: 4 });
    media.emit('volumechange', { volume: 0 }); assert.equal(last().muted, true);
    media.emit('volumechange', { volume: 0.8, muted: true }); assert.equal(last().muted, true);
    media.emit('volumechange', { muted: false }); assert.equal(last().muted, false);
    const count = states.length; media.emit('timeupdate'); assert.equal(states.length, count);
});

test('optional observer failure cannot break audio listeners, and disposal detaches them', () => {
    const media = new Media(); let calls = 0;
    const observer = observePlayerPlayback(media, () => track, () => { calls++; throw Error('No room'); });
    assert.equal(calls, 1);
    observer.dispose(); media.emit('playing', { src: track.streamUrl, paused: false, readyState: 4 });
    observer.refresh(); assert.equal(calls, 1);
});
