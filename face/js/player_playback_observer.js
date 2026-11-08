/** Read-only projection of the existing media element. Never opens or plays audio. */
const EVENTS = ['play', 'playing', 'pause', 'ended', 'waiting', 'stalled',
    'seeking', 'seeked', 'emptied', 'abort', 'error', 'volumechange', 'loadedmetadata'];

function label(value) {
    return typeof value === 'string' ? value.replace(/[\x00-\x1f\x7f]/g, ' ').trim().slice(0, 96) : '';
}

export function playerPlaybackSnapshot(audio, track, confirmed = false, buffering = false) {
    const source = audio?.getAttribute?.('src') || '';
    let status = 'idle';
    if (audio?.error) status = 'error';
    else if (source && !audio.ended) {
        if (audio.paused) status = 'paused';
        else status = confirmed && !buffering && !audio.seeking && audio.readyState >= 3
            ? 'playing' : 'loading';
    }
    const matching = source && source === track?.streamUrl;
    return Object.freeze({
        status,
        muted: audio?.muted === true || audio?.volume === 0,
        title: matching && status !== 'idle' ? label(track.title) : '',
        artist: matching && status !== 'idle' ? label(track.artist) : '',
    });
}

export function observePlayerPlayback(audio, getTrack, onChange) {
    let confirmed = false, buffering = false, last = '', disposed = false;
    const refresh = () => {
        if (disposed) return;
        const snapshot = playerPlaybackSnapshot(audio, getTrack?.(), confirmed, buffering);
        const key = JSON.stringify(snapshot);
        if (key === last) return;
        last = key;
        // A decorative consumer must never interfere with playback/acknowledgement.
        try { onChange?.(snapshot); } catch { /* Optional room unavailable. */ }
    };
    const listener = (event) => {
        if (event.type === 'playing') { confirmed = true; buffering = false; }
        if (['play', 'pause', 'ended', 'emptied', 'abort', 'error'].includes(event.type)) confirmed = false;
        if (event.type === 'waiting') buffering = true;
        if (['play', 'emptied', 'abort', 'error'].includes(event.type)) buffering = false;
        // stalled alone can mean a network stall while buffered audio still plays.
        refresh();
    };
    for (const event of EVENTS) audio?.addEventListener?.(event, listener);
    refresh();
    return {
        refresh,
        dispose() {
            disposed = true;
            for (const event of EVENTS) audio?.removeEventListener?.(event, listener);
        },
    };
}
