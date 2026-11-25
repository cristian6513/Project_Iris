import { irisConfirm } from './iris_dialog.js?v=9';
import { captureFrame } from './vision_snapshot.js?v=4';
import { GameShortcuts } from './game_shortcuts.js?v=1';
import { layoutGameBubble } from './game_bubble.js?v=1';
import { comboFromKeyEvent, displayCombo, gameModeView, gameWelcome, presetFor,
    readGamePreferences, saveGamePreferences } from './game_mode_ui.js?v=1';

const stopTracks = stream => stream?.getTracks?.().forEach(track => track.stop());
const failure = message => new Error(message);

/** The selected OS stream never leaves this controller. Core asks for a fresh
 * frame per question or explicitly enabled commentary; never records video. */
export class GameCapture {
    constructor({ send, onState = () => {}, onText = () => {}, confirm = irisConfirm,
        media = globalThis.navigator?.mediaDevices, capture = captureFrame,
        doc = globalThis.document, timers = globalThis } = {}) {
        Object.assign(this, { send, onState, onText, confirm, media, capture, doc, timers });
        this.state = { active: false, message: '' };
        this.stream = null;
        this.generation = 0;
        this.request = '';
        this.frame = null;
        this.consent = null;
        this.startTimer = null;
        this.sessionTimer = null;
        this.seen = new Set();
        this.onHidden = () => { if (this.doc?.visibilityState === 'hidden') this.stop('Modo juego detenido al ocultar Companion.'); };
        this.onPageHide = () => this.stop();
        doc?.addEventListener?.('visibilitychange', this.onHidden);
        doc?.defaultView?.addEventListener?.('pagehide', this.onPageHide);
    }

    emit(extra = {}) {
        this.state = { ...this.state, ...extra };
        this.onState(this.state);
    }

    prepare({ game = '', spoilerFree = true } = {}) {
        this.stop('', false);
        const request = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
        this.request = request;
        this.options = { game: String(game).slice(0, 120), spoilerFree: spoilerFree === true };
        this.emit({ active: false, preparing: true, message: 'Comprobando el destino de visión…' });
        this.startTimer = this.timers.setTimeout(() => this.stop('La preparación venció. Inténtalo otra vez.'), 180000);
        if (this.send('game_session_command', { command: 'prepare', request_id: request }) === false) {
            this.stop('No hay conexión con Iris.');
        }
    }

    async prepared(payload) {
        if (!this.request || payload.request_id !== this.request || !payload.session_id) return;
        const gen = this.generation;
        this.id = payload.session_id;
        try {
            const consent = new AbortController();
            this.consent = consent;
            const destination = `${payload.destination?.model || ''} (${payload.destination?.provider || ''})`;
            const approved = await this.confirm({
                title: 'Compartir la ventana del juego', icon: 'monitor',
                message: 'Iris mirará solo la ventana que elijas, y solo cuando le preguntes. No graba vídeo ni toca el juego.',
                question: `Cada pregunta envía una imagen a ${destination} y usa tu plan de IA.`,
                details: { label: 'Más detalles', text: 'Después, Windows abre su selector: elige la ventana del juego, no la pantalla entera. El análisis puede usar imágenes temporales, que se borran al terminar o cancelar. Tu proveedor aplica sus propias políticas. La voz puede consumir cuota aparte. Puedes salir cuando quieras.' },
                confirmLabel: 'Elegir ventana', cancelLabel: 'Cancelar',
                signal: consent.signal,
            });
            if (this.consent === consent) this.consent = null;
            if (gen !== this.generation) return;
            if (!approved) { this.stop('No se compartió ninguna ventana.'); return; }
            if (!this.media?.getDisplayMedia) throw failure('Este WebView no permite compartir una ventana.');
            const stream = await this.media.getDisplayMedia({ video: {
                displaySurface: 'window', frameRate: { ideal: 2, max: 5 },
            }, audio: false,
                monitorTypeSurfaces: 'exclude' });
            if (gen !== this.generation) { stopTracks(stream); return; }
            this.stream = stream;
            const track = stream.getVideoTracks?.()[0];
            if (!track || track.readyState !== 'live' || track.muted || track.getSettings?.().displaySurface !== 'window') {
                throw failure('Selecciona una ventana del juego. Este modo no comparte el monitor completo ni pestañas.');
            }
            for (const t of stream.getTracks()) {
                t.addEventListener?.('ended', () => { if (this.stream === stream) this.stop('La ventana dejó de compartirse.'); }, { once: true });
                t.addEventListener?.('mute', () => { if (this.stream === stream) this.stop('La ventana no está disponible. Vuelve a elegirla.'); }, { once: true });
            }
            if (stream.getAudioTracks?.().length) throw failure('Esta sesión solo comparte imagen.');
            this.send('game_session_command', { command: 'start', session_id: this.id, confirmed: true,
                surface: 'window', spoiler_free: this.options.spoilerFree,
                game: this.options.game || String(track.label || 'Juego compartido').slice(0, 120) });
            this.emit({ message: 'Ventana elegida; esperando autorización de Iris…' });
        } catch (error) {
            if (gen === this.generation) this.stop(error?.name === 'NotAllowedError' ? 'Selección cancelada.' : error?.message || 'No pude compartir esa ventana.');
        }
    }

    update(payload = {}) {
        if (payload.session_id && payload.session_id !== this.id) return;
        if (payload.request_id && this.request && payload.request_id !== this.request) return;
        if (!payload.active) {
            this.stop(payload.message || '', false);
            return;
        }
        if (!this.stream || payload.session_id !== this.id) return;
        this.timers.clearTimeout(this.startTimer);
        this.request = '';
        if (!this.sessionTimer) this.sessionTimer = this.timers.setTimeout(() => this.stop('La sesión alcanzó tres horas. Vuelve a elegir la ventana.'), 10800000);
        this.emit({ message: '', ...payload, preparing: false });
    }

    async take(payload) {
        if (!this.state.active || !this.stream || payload.session_id !== this.id || !payload.request_id || this.seen.has(payload.request_id)) return;
        this.seen.add(payload.request_id);
        if (this.seen.size > 64) this.seen.delete(this.seen.values().next().value);
        this.frame?.abort();
        const controller = new AbortController(); this.frame = controller;
        const gen = this.generation;
        const timer = this.timers.setTimeout(() => controller.abort(), 10000);
        try {
            const track = this.stream.getVideoTracks()[0];
            if (track.readyState !== 'live' || track.muted || this.doc?.visibilityState === 'hidden') throw failure('unavailable');
            const captured = await this.capture(this.stream, { signal: controller.signal, maxSide: 1280, quality: .85 });
            if (gen !== this.generation || controller.signal.aborted || this.frame !== controller) return;
            this.send('game_session_frame_result', { session_id: this.id, request_id: payload.request_id,
                status: 'ok', image: captured.image });
        } catch {
            if (gen === this.generation) this.send('game_session_frame_result', { session_id: this.id, request_id: payload.request_id, status: 'unavailable' });
        } finally {
            this.timers.clearTimeout(timer);
            if (this.frame === controller) this.frame = null;
        }
    }

    mute() {
        if (this.state.active) this.send('game_session_command', { command: 'mute', session_id: this.id, muted: !this.state.muted });
    }

    async commentary({ interval = 45, limit = 100, style = 'tips' } = {}) {
        if (!this.state.active || this.commentaryPending) return;
        if (this.state.commentary?.enabled) {
            this.send('game_session_command', { command: 'commentary', session_id: this.id, enabled: false });
            return;
        }
        if (this.state.muted || !Number.isInteger(interval) || interval < 20 || interval > 300 ||
            !Number.isInteger(limit) || limit < 0 || limit > 1000 || !['tips', 'chat'].includes(style)) {
            this.emit({ message: 'Activa la voz y revisa el intervalo (20–300 s) y el límite (0–1000).' }); return;
        }
        const gen = this.generation, id = this.id, consent = new AbortController();
        this.consent = consent; this.commentaryPending = true; this.emit({});
        try {
            const destination = `${this.state.destination?.model || ''} (${this.state.destination?.provider || ''})`;
            const used = this.state.analyses || 0;
            const approved = await this.confirm({ title: '¿Activar comentarios automáticos?', icon: 'chat',
                message: `Iris mirará tu juego cada ${interval} s, cuando esté libre, y comentará solo si vale la pena. Cada mirada envía una imagen a ${destination}.`,
                question: 'Cada mirada cuenta como una consulta: puede gastar tus tokens o tu límite de uso mucho más rápido de lo normal, incluso si Iris decide quedarse callada.',
                details: { label: 'Más detalles', text: `${limit ? `Se pausa sola al llegar a ${limit} consultas en esta partida, contando tus preguntas (llevas ${used}).` : `Sin tope de consultas en esta partida (llevas ${used}).`} Lo cambias en Más opciones. La voz puede consumir cuota aparte. Puedes pausar, silenciar o salir cuando quieras.` },
                confirmLabel: 'Activar comentarios', cancelLabel: 'Solo cuando pregunte', signal: consent.signal });
            if (approved && gen === this.generation && id === this.id && this.state.active && !this.state.muted) {
                this.send('game_session_command', { command: 'commentary', session_id: id,
                    enabled: true, confirmed: true, interval, limit, style });
            }
        } catch {
            if (gen === this.generation) this.emit({ message: 'No pude confirmar los comentarios. Inténtalo otra vez.' });
        } finally {
            if (this.consent === consent) {
                this.consent = null;
                this.commentaryPending = false; this.emit({});
            }
        }
    }

    answer(payload) {
        if (this.state.active && payload.session_id === this.id && typeof payload.text === 'string') this.onText(payload.text);
    }

    stop(message = '', notify = true) {
        const id = this.id, request = this.request;
        this.generation += 1;
        this.frame?.abort(); this.frame = null;
        this.consent?.abort(); this.consent = null;
        this.commentaryPending = false;
        stopTracks(this.stream); this.stream = null;
        this.timers.clearTimeout(this.startTimer); this.timers.clearTimeout(this.sessionTimer);
        this.startTimer = this.sessionTimer = null;
        this.request = ''; this.id = ''; this.seen.clear();
        this.emit({ active: false, preparing: false, muted: false, analyses: 0, game: '', message,
            memory: { goal: '', notes: '', entries: 0 },
            commentary: { enabled: false, status: 'off', interval: 45, limit: 100, style: 'tips' } });
        if (notify && id) this.send('game_session_command', { command: 'stop', session_id: id });
        else if (notify && request) this.send('game_session_command', { command: 'cancel_prepare', request_id: request });
    }

    dispose() {
        this.stop();
        this.doc?.removeEventListener?.('visibilitychange', this.onHidden);
        this.doc?.defaultView?.removeEventListener?.('pagehide', this.onPageHide);
    }
}

/** Companion game panel: «Antes de empezar», «Jugando» and «Más opciones».
 * The compact bar and its Exit stay visible even when the avatar-only toggle
 * hides the control rail; tapping the bar reopens the panel. */
export function mountGameMode({ doc = globalThis.document, send, onText, onSilence = () => {}, onSessionEnd = () => {},
    onAskText = () => {}, onNotice = () => {} }) {
    const $ = id => doc.getElementById(id);
    const panel = $('game-mode-panel'), button = $('game-mode-toggle');
    const views = { start: $('game-view-start'), playing: $('game-view-playing'), options: $('game-view-options') };
    const input = $('game-mode-name'), spoilers = $('game-mode-spoilers');
    const start = $('game-mode-start'), cancel = $('game-mode-cancel'), stop = $('game-mode-stop');
    const mute = $('game-mode-mute'), auto = $('game-mode-auto'), status = $('game-mode-status');
    const card = $('game-mode-card'), indicator = $('game-mode-indicator'), label = $('game-mode-label');
    const silence = $('game-mode-silence'), askVoice = $('game-mode-ask-voice'), voice = $('voice-manual');
    const goal = $('game-mode-goal'), notes = $('game-mode-notes'), forget = $('game-mode-forget');
    const keysEnabled = $('game-keys-enabled');
    const keyButtons = Object.fromEntries(['help', 'mute', 'exit'].map(k => [k, $(`game-key-${k}`)]));
    const radios = name => [...panel.querySelectorAll(`input[name="game-${name}"]`)];
    const setText = (id, text) => { const el = $(id); if (el && el.textContent !== text) el.textContent = text; };
    let storage; try { storage = doc.defaultView?.localStorage; } catch { /* Private browser. */ }
    let preferences = readGamePreferences(storage);
    let available = false, nativeKeys = false, optionsOpen = false, capturing = '';
    let keysMessage = 'Atajos globales apagados.';
    let lastMemory = '', lastSession = '';

    const shortcuts = new GameShortcuts({ storage, onState: state => {
        keysMessage = state.message; render();
    }, onAction: action => {
        if (!controller.state.active) return;
        if (action === 'help') send('user_text', { text: 'Mira la ventana del juego y dame una ayuda breve para el objetivo de esta partida, sin inventar lo que no veas.' });
        if (action === 'mute') { onSilence(); if (!controller.state.muted) controller.mute(); }
        if (action === 'exit') { onSilence(); controller.stop('Modo juego finalizado con el atajo.'); }
    } });
    const keys = () => ({ ...shortcuts.bindings, enabled: shortcuts.enabled, active: shortcuts.active });

    function render() {
        const state = controller.state;
        const c = state.commentary || {};
        const session = state.active ? state.session_id : '';
        const began = Boolean(session && !lastSession), ended = Boolean(lastSession && !session);
        lastSession = session;
        if (began) {
            // The window is shared: back to the game with the compact bar.
            optionsOpen = false; capturing = '';
            const focused = panel.contains?.(doc.activeElement);
            panel.hidden = true; button.setAttribute('aria-expanded', 'false');
            if (focused) button.focus?.();
        }
        const view = gameModeView(state, { pending: controller.commentaryPending, keys: keys(), available });
        const shown = optionsOpen ? 'options' : view.view;
        panel.dataset.view = shown;
        for (const [name, el] of Object.entries(views)) el.hidden = name !== shown;
        $('game-mode-back').hidden = !optionsOpen;
        $('game-mode-beta').hidden = optionsOpen;
        setText('game-mode-title', optionsOpen ? 'Más opciones' : 'Modo juego');
        $('game-mode-more').hidden = optionsOpen;
        // Antes de empezar.
        start.disabled = !available || state.active || state.preparing;
        setText('game-mode-start-label', view.startLabel);
        cancel.hidden = !state.preparing;
        input.disabled = spoilers.disabled = Boolean(state.active || state.preparing);
        setText('game-step-ask', view.askStep);
        // Jugando.
        card.dataset.tone = indicator.dataset.tone = view.tone;
        setText('game-mode-card-title', view.title);
        setText('game-mode-card-sub', view.card);
        setText('game-mode-key-hint', view.keyHint);
        setText('game-mode-mute-label', view.mute);
        mute.setAttribute('aria-pressed', String(Boolean(state.muted)));
        mute.dataset.muted = silence.dataset.muted = String(Boolean(state.muted));
        askVoice.disabled = !state.active || Boolean(voice?.disabled);
        auto.disabled = !state.active || state.muted || controller.commentaryPending;
        auto.setAttribute('aria-checked', String(view.autoOn));
        setText('game-mode-auto-summary', view.auto);
        // Más opciones. Core's values while commentary runs; the player's otherwise.
        const shownChoice = c.enabled ? { interval: presetFor('interval', c.interval),
            limit: presetFor('limit', c.limit), style: presetFor('style', c.style) } : preferences;
        const locked = Boolean(c.enabled || controller.commentaryPending);
        for (const name of ['interval', 'limit', 'style']) {
            for (const radio of radios(name)) {
                radio.checked = radio.value === String(shownChoice[name]);
                radio.disabled = locked;
            }
        }
        setText('game-commentary-hint', locked ? 'Pausa los comentarios para cambiar esto.'
            : 'Cada mirada cuenta como una consulta a tu IA.');
        keysEnabled.checked = Boolean(shortcuts.enabled);
        keysEnabled.disabled = !nativeKeys;
        for (const [name, el] of Object.entries(keyButtons)) {
            el.disabled = !nativeKeys;
            el.dataset.capturing = String(capturing === name);
            setText(el.id, capturing === name ? 'Pulsa la combinación…' : displayCombo(shortcuts.bindings[name]));
        }
        setText('game-keys-status', capturing ? 'Pulsa Escape para cancelar.' : keysMessage);
        const memory = state.memory || { goal: '', notes: '', entries: 0 };
        const memoryFields = JSON.stringify([memory.goal, memory.notes]);
        if (lastMemory !== memoryFields) {
            // Never overwrite what the player is typing; its blur saves it.
            if (doc.activeElement !== goal) goal.value = memory.goal || '';
            if (doc.activeElement !== notes) notes.value = memory.notes || '';
            lastMemory = memoryFields;
        }
        goal.disabled = notes.disabled = forget.disabled = !state.active;
        setText('game-mode-memory-status', state.active ? 'Se guarda solo. Todo se borra al salir.' : 'Disponible al empezar la partida.');
        status.hidden = !view.status;
        setText('game-mode-status', view.status);
        // Barra compacta.
        indicator.hidden = !state.active;
        label.textContent = state.active ? view.title : '';
        setText('game-mode-sublabel', state.active ? view.bar : '');
        indicator.title = state.active ? `${state.destination?.model || ''} (${state.destination?.provider || ''})` : '';
        silence.setAttribute('aria-label', state.muted ? 'Activar voz' : 'Silenciar');
        button.setAttribute('aria-pressed', String(Boolean(state.active)));
        doc.body.dataset.gameSession = session ? 'active' : 'off';
        $('speech-dismiss').hidden = !state.active;
        layoutGameBubble(doc);
        if (ended) {
            goal.value = notes.value = ''; lastMemory = '';
            // «Salir» was just hidden with the playing view; keep focus in the panel.
            const lost = !doc.activeElement || doc.activeElement === doc.body || views.playing.contains?.(doc.activeElement);
            if (!panel.hidden && lost) $('game-mode-title').focus?.();
            onSilence(); onSessionEnd();
        }
        void shortcuts.setSession(session);
        if (began) welcome();
    }

    const controller = new GameCapture({ send, doc, onText, onState: render });

    // The first window ever shared: Iris says, in her bubble, how to ask.
    function welcome() {
        let seen = true;
        try { seen = storage?.getItem('irisGameWelcome.v1') === '1'; storage?.setItem('irisGameWelcome.v1', '1'); } catch { seen = false; }
        if (!seen) onNotice(gameWelcome(keys()));
    }
    function open(value, focus = true) {
        if (!value) endCapture();
        panel.hidden = !value; button.setAttribute('aria-expanded', String(value));
        if (!value) optionsOpen = false;
        render();
        if (!focus) return;
        if (value) $('game-mode-title').focus?.();
        else button.focus?.();
    }
    function showOptions(value) {
        optionsOpen = value; endCapture(); render();
        (value ? $('game-mode-back') : $('game-mode-more')).focus?.();
    }
    function saveContext() {
        const memory = controller.state.memory || {};
        if (!controller.state.active || (goal.value.trim() === (memory.goal || '') && notes.value.trim() === (memory.notes || ''))) return;
        send('game_session_command', { command: 'context', session_id: controller.id, goal: goal.value, notes: notes.value });
    }
    function choose(name, value) {
        preferences = saveGamePreferences(storage, { ...preferences, [name]: name === 'style' ? value : Number(value) });
        render();
    }
    function endCapture() { capturing = ''; }
    function captureKey(event) {
        if (!capturing) return;
        if (event.key !== 'Tab') { event.preventDefault(); event.stopPropagation(); }
        if (event.key === 'Tab') { endCapture(); render(); return; }
        if (event.key === 'Escape') { endCapture(); render(); return; }
        const { waiting, combo } = comboFromKeyEvent(event);
        if (waiting) return;
        const name = capturing; endCapture();
        if (!combo) {
            keysMessage = 'Usa dos de Ctrl, Alt o Shift y una letra, un número o F1–F11.'; render(); return;
        }
        void shortcuts.configure(shortcuts.enabled, { ...shortcuts.bindings, [name]: combo });
        render();
    }

    button.addEventListener('click', () => open(panel.hidden));
    $('game-mode-close').addEventListener('click', () => open(false));
    $('game-mode-more').addEventListener('click', () => showOptions(true));
    $('game-mode-back').addEventListener('click', () => showOptions(false));
    panel.addEventListener('keydown', event => {
        if (capturing) { captureKey(event); return; }
        if (event.key !== 'Escape') return;
        event.preventDefault();
        if (optionsOpen) showOptions(false); else open(false);
    });
    start.addEventListener('click', () => controller.prepare({ game: input.value, spoilerFree: spoilers.checked }));
    cancel.addEventListener('click', () => controller.stop('Preparación cancelada.'));
    stop.addEventListener('click', () => controller.stop('Modo juego finalizado.'));
    const toggleVoice = () => { if (!controller.state.muted) onSilence(); controller.mute(); };
    mute.addEventListener('click', toggleVoice);
    silence.addEventListener('click', toggleVoice);
    askVoice.addEventListener('click', () => { open(false, false); voice?.click(); });
    $('game-mode-ask-text').addEventListener('click', () => { open(false, false); onAskText(); });
    $('game-mode-reopen').addEventListener('click', () => open(true));
    auto.addEventListener('click', () => controller.commentary(preferences));
    auto.closest('.game-switch-row')?.addEventListener('click', event => {
        if (event.target !== auto && !auto.disabled) auto.click();
    });
    for (const name of ['interval', 'limit', 'style']) {
        for (const radio of radios(name)) radio.addEventListener('change', () => choose(name, radio.value));
    }
    goal.addEventListener('change', saveContext);
    notes.addEventListener('change', saveContext);
    forget.addEventListener('click', () => send('game_session_command', { command: 'context', session_id: controller.id, forget: true }));
    keysEnabled.addEventListener('change', () => void shortcuts.configure(keysEnabled.checked, { ...shortcuts.bindings }));
    for (const [name, el] of Object.entries(keyButtons)) {
        el.addEventListener('click', () => { capturing = capturing === name ? '' : name; render(); });
        el.addEventListener('blur', () => { if (capturing === name) { endCapture(); render(); } });
    }
    $('game-mode-exit').addEventListener('click', () => controller.stop('Modo juego finalizado.'));
    if (voice && doc.defaultView?.MutationObserver) {
        new doc.defaultView.MutationObserver(render).observe(voice, { attributes: true, attributeFilter: ['disabled'] });
    }
    controller.setAvailable = value => {
        available = value === true; button.disabled = !available;
        controller.emit({});
    };
    controller.setAvailable(false);
    controller.connectNative = bridge => {
        nativeKeys = Boolean(bridge?.native); render();
        return shortcuts.connect(bridge).catch(() => {
            keysMessage = 'No pude conectar los atajos nativos. Los botones siguen disponibles.'; render();
        });
    };
    const dispose = controller.dispose.bind(controller);
    controller.dispose = () => { void shortcuts.dispose(); dispose(); };
    return controller;
}
