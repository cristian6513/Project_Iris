/* Game mode panel in Companion: the words and choices it shows.
 *
 * Pure functions, so the copy a first-time player reads and the presets are
 * tested without a DOM. mountGameMode (game_capture.js) renders them.
 */

/** «Más opciones» offers three choices instead of number fields. */
export const GAME_PRESETS = Object.freeze({
    interval: Object.freeze([90, 45, 20]),
    limit: Object.freeze([50, 100, 0]),
    style: Object.freeze(['tips', 'chat']),
});
export const GAME_PREFERENCES_KEY = 'irisGameCommentary.v1';
const DEFAULTS = Object.freeze({ interval: 45, limit: 100, style: 'tips' });

/** A saved or Core value maps to the closest offered choice. 0 means «Sin tope». */
export function presetFor(kind, value) {
    const list = GAME_PRESETS[kind];
    if (kind === 'style') return list.includes(value) ? value : DEFAULTS.style;
    const number = Number(value);
    if (!Number.isFinite(number)) return DEFAULTS[kind];
    if (kind === 'limit' && number === 0) return 0;
    return list.filter(v => v !== 0)
        .reduce((best, v) => Math.abs(v - number) < Math.abs(best - number) ? v : best, DEFAULTS[kind]);
}

export function readGamePreferences(storage) {
    let saved = {};
    try { saved = JSON.parse(storage?.getItem(GAME_PREFERENCES_KEY) || '{}') || {}; } catch { /* Defaults. */ }
    return { interval: presetFor('interval', saved.interval ?? DEFAULTS.interval),
        limit: presetFor('limit', saved.limit ?? DEFAULTS.limit), style: presetFor('style', saved.style) };
}

export function saveGamePreferences(storage, preferences) {
    const clean = readGamePreferences({ getItem: () => JSON.stringify(preferences) });
    try { storage?.setItem(GAME_PREFERENCES_KEY, JSON.stringify(clean)); } catch { /* This run keeps them. */ }
    return clean;
}

const KEY_FROM_CODE = code => /^Key[A-Z]$/.test(code) ? code.slice(3)
    : /^Digit[0-9]$/.test(code) ? code.slice(5)
        : /^F(?:[1-9]|1[01])$/.test(code) ? code : '';
const MODIFIER_KEYS = new Set(['Control', 'Alt', 'AltGraph', 'Shift', 'Meta', 'OS']);

/** Turns a keydown into the «Ctrl+Alt+F8» format GameShortcuts accepts.
 * Physical codes, so Spanish AltGr layouts still give letters, not «@». */
export function comboFromKeyEvent(event = {}) {
    const modifiers = [event.ctrlKey && 'Ctrl', event.altKey && 'Alt', event.shiftKey && 'Shift'].filter(Boolean);
    if (MODIFIER_KEYS.has(event.key)) return { waiting: true, combo: '' };
    const key = KEY_FROM_CODE(String(event.code || ''));
    if (event.metaKey || modifiers.length < 2 || !key) return { waiting: false, combo: '' };
    return { waiting: false, combo: [...modifiers, key].join('+') };
}

export const displayCombo = combo => String(combo || '').split('+').join(' + ');

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/** Everything the panel and the compact bar say about a session. */
export function gameModeView(state = {}, { pending = false, keys = {}, available = true } = {}) {
    const c = { enabled: false, status: 'off', ...DEFAULTS, ...(state.commentary || {}) };
    const count = plural(Number(state.analyses) || 0, 'consulta', 'consultas');
    const game = String(state.game || '').trim() || 'tu juego';
    const style = c.style === 'chat' ? 'charla' : 'consejos';
    const busy = { analysing: 'mirando el juego…', speaking: 'comentando…' }[c.status];
    const tone = state.muted ? 'muted' : ['limit', 'error'].includes(c.status) ? 'warn' : busy ? 'busy' : 'ready';
    let auto;
    if (pending) auto = 'Esperando tu confirmación…';
    else if (state.muted) auto = 'Activa la voz para usarlos.';
    else if (c.enabled) auto = busy ? `${busy[0].toUpperCase()}${busy.slice(1)}` : `Cada ${c.interval} s · ${style}.`;
    else auto = {
        limit: `En pausa: llegaste al máximo de ${c.limit} consultas.`,
        error: 'En pausa por un error. Puedes volver a activarlos.',
    }[c.status] || 'Apagados. Iris solo habla si le preguntas.';
    const help = displayCombo(keys.help);
    return {
        view: state.active ? 'playing' : 'start',
        tone,
        title: `Mirando · ${game}`,
        card: [state.spoiler_free === false ? '' : 'Sin spoilers', `${count} en esta partida`,
            state.muted ? 'respuestas por texto' : ''].filter(Boolean).join(' · '),
        bar: [state.muted ? 'sin voz' : busy || (c.enabled ? `comentarios cada ${c.interval} s`
            : c.status === 'limit' ? 'límite alcanzado' : 'a petición'), count].join(' · '),
        auto,
        autoOn: Boolean(c.enabled || pending),
        startLabel: state.preparing ? 'Esperando la ventana…' : 'Elegir ventana del juego',
        status: state.message || (!available && !state.active ? 'Disponible cuando Iris esté conectada.' : ''),
        askStep: keys.enabled && help ? `Con «Hablar», el chat o ${help}.` : 'Con «Hablar» o escribiendo en el chat.',
        keyHint: keys.active && help ? `Sin salir del juego: ${help}`
            : 'Para preguntar sin salir del juego, activa los atajos en Más opciones.',
        mute: state.muted ? 'Activar voz' : 'Silenciar',
    };
}

/** Shown once, in the speech bubble, the first time a window is shared. */
export function gameWelcome(keys = {}) {
    const help = keys.enabled && keys.help ? `, escríbeme o usa ${displayCombo(keys.help)}` : ' o escríbeme en el chat';
    return `Listo. Pregúntame lo que quieras sobre tu juego: pulsa «Hablar»${help}.`;
}
