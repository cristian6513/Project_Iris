/** A second view of the shell's one Player. No audio element, folder picker or timer. */
export function createWidget({ root, openApp = () => {}, services = {}, signal },
    { documentLike = globalThis.document } = {}) {
    const player = services.player;
    let visible = false, disposed = false, release = null, busy = false, notice = '';
    let snapshot = player?.read?.() || null;
    const el = (tag, part, text = '') => {
        const node = documentLike.createElement(tag); node.className = `iris-player-widget-${part}`;
        node.dataset.playerWidget = part; node.textContent = text; return node;
    };
    const body = el('div', 'body'); body.className += ' iris-player-widget';
    const header = el('header', 'header'), identity = el('div', 'identity');
    identity.append(el('span', 'eyebrow', 'MÚSICA LOCAL'), el('h2', 'heading', 'IRIS Player'));
    const open = el('button', 'open', 'Abrir Player'); open.type = 'button';
    open.addEventListener('click', () => { if (!disposed && visible) openApp('player'); });
    header.append(identity, open);
    const track = el('div', 'track'), art = el('span', 'art', '♪'); art.setAttribute('aria-hidden', 'true');
    const labels = el('div', 'labels'), title = el('strong', 'title'), artist = el('span', 'artist');
    labels.append(title, artist); track.append(art, labels);
    const controls = el('div', 'controls'); controls.setAttribute('role', 'group'); controls.setAttribute('aria-label', 'Reproducción local');
    const buttons = {};
    for (const [action, label, glyph] of [['previous', 'Canción anterior', '‹'], ['play-pause', 'Reproducir', '▶'], ['next', 'Canción siguiente', '›']]) {
        const button = el('button', action, glyph); button.type = 'button'; button.setAttribute('aria-label', label);
        button.addEventListener('click', () => void command(action)); buttons[action] = button; controls.append(button);
    }
    const progress = el('progress', 'progress'); progress.max = 1; progress.value = 0;
    progress.setAttribute('aria-label', 'Progreso de la canción');
    const time = el('span', 'time'), status = el('p', 'status'); status.setAttribute('role', 'status');
    const retry = el('button', 'retry', 'Reintentar lectura'); retry.type = 'button';
    retry.addEventListener('click', () => void load());
    body.append(header, track, controls, progress, time, status, retry);
    // Keep the same controls mounted: a media event between mousedown/click must not lose a click or focus.
    root.replaceChildren(body);
    function duration(value) {
        const seconds = Math.max(0, Math.floor(Number(value) || 0));
        return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    }
    function render() {
        if (disposed || !visible) return;
        const state = snapshot || {};
        const playing = ['playing', 'loading'].includes(state.status);
        const blocked = !player || !state.loaded || state.loading || busy || state.pending || !state.configured || !state.count;
        for (const button of Object.values(buttons)) button.disabled = Boolean(blocked);
        buttons['play-pause'].textContent = playing ? 'Ⅱ' : '▶';
        buttons['play-pause'].setAttribute('aria-label', playing ? 'Pausar' : 'Reproducir');
        title.textContent = state.title || (state.configured ? 'Elige tu música' : 'Tu música, en este equipo');
        artist.textContent = state.artist || (state.count ? `${state.count} canciones locales` : 'Sin servicios externos');
        progress.max = state.duration > 0 ? state.duration : 1;
        progress.value = Math.min(state.elapsed || 0, progress.max);
        time.textContent = `${duration(state.elapsed)} / ${state.duration > 0 ? duration(state.duration) : '—:—'}`;
        let message = 'Sin reproducción';
        if (!player) message = 'Player no está disponible en esta superficie. Abre IRIS OS.';
        else if (state.loading) message = 'Leyendo tu biblioteca local…';
        else if (notice || state.error) message = notice || state.error;
        else if (!state.loaded) message = 'La biblioteca no se pudo leer. Reintenta.';
        else if (!state.configured) message = 'Elige una carpeta desde Player para empezar.';
        else if (!state.count) message = 'La carpeta autorizada no contiene canciones compatibles.';
        else message = { playing: 'Reproduciendo', loading: 'Preparando audio…', paused: 'En pausa', error: 'No se pudo reproducir esta canción.' }[state.status] || message;
        if (status.textContent !== message) status.textContent = message;
        retry.hidden = !player || state.loaded;
        retry.disabled = Boolean(state.loading || busy || state.pending);
        body.setAttribute('aria-busy', String(Boolean(state.loading || busy || state.pending)));
    }
    async function load() {
        if (!player || disposed || !visible) return;
        notice = '';
        try { await player.load(); }
        catch { notice = 'No se pudo leer tu biblioteca. Reintenta.'; }
        if (!disposed && visible) { snapshot = player.read(); render(); }
    }
    async function command(action) {
        if (disposed || !visible || busy || buttons[action]?.disabled) return;
        busy = true; notice = ''; render();
        try {
            if (await player.command(action) === false) notice = 'No se pudo completar el control. Revisa Player antes de reintentarlo.';
        } catch { notice = 'El control no se confirmó. Revisa Player antes de reintentarlo.'; }
        finally { if (!disposed) { busy = false; snapshot = player.read(); render(); } }
    }
    function dispose() {
        if (disposed) return;
        disposed = true; release?.(); release = null;
        signal?.removeEventListener('abort', dispose); root.replaceChildren();
    }
    signal?.addEventListener('abort', dispose, { once: true });
    if (signal?.aborted) dispose();
    return {
        configure() {},
        setVisibility(value) {
            if (disposed || visible === Boolean(value)) return;
            visible = Boolean(value);
            if (visible) {
                release = player?.subscribe?.((value) => { snapshot = value; render(); }) || null;
                render(); void load();
            } else { release?.(); release = null; }
        },
        dispose,
    };
}
