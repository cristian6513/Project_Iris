export const GAME_KEYS = Object.freeze({ help: 'Ctrl+Alt+F8', mute: 'Ctrl+Alt+F9', exit: 'Ctrl+Alt+F10' });
const canonical = value => {
    if (typeof value !== 'string') return '';
    const parts = value.split('+').map(s => s.trim()), key = parts.pop();
    if (parts.length < 2 || parts.length > 3 || new Set(parts).size !== parts.length ||
        parts.some(p => !['Ctrl', 'Alt', 'Shift'].includes(p)) || !/^(?:[A-Z0-9]|F(?:[1-9]|10|11))$/.test(key)) return '';
    return [...parts.sort(), key].join('+');
};
export function validGameKeys(keys) {
    const values = ['help', 'mute', 'exit'].map(k => canonical(keys?.[k]));
    return values.every(Boolean) && new Set(values).size === 3;
}

/** Native registration is serialized, session-bound and opt-in. Store only key names. */
export class GameShortcuts {
    constructor({ onAction, onState = () => {}, storage, clock = Date.now } = {}) {
        Object.assign(this, { onAction, onState, storage, clock });
        this.bindings = { ...GAME_KEYS }; this.enabled = false; this.session = '';
        this.revision = 0; this.queue = Promise.resolve(); this.lastHelp = -Infinity;
        try {
            const saved = JSON.parse(storage?.getItem('irisGameKeys.v1') || 'null');
            if (validGameKeys(saved)) this.bindings = saved;
        } catch { /* Defaults remain available without browser storage. */ }
    }
    async connect(bridge) {
        this.active = false;
        this.bridge = bridge;
        if (!bridge?.native || !bridge.setGameHotkeys) {
            this.onState({ active: false, message: 'Los atajos globales necesitan Companion para Windows.' }); return;
        }
        await bridge.onWindowEvent('iris-game-hotkey', event => {
            const data = event.payload || event;
            if (!this.active || !this.enabled || !this.session || data.session_id !== this.session) return;
            if (!['help', 'mute', 'exit'].includes(data.action)) return;
            if (data.action === 'help' && this.clock() - this.lastHelp < 1500) return;
            if (data.action === 'help') this.lastHelp = this.clock();
            this.onAction(data.action);
        });
        return this.apply();
    }
    setSession(session) {
        if (session === this.session) return this.queue;
        this.session = session; this.active = false;
        return this.apply();
    }
    configure(enabled, bindings) {
        if (!validGameKeys(bindings)) {
            this.enabled = false;
            this.apply('Usa combinaciones distintas, con dos modificadores (Ctrl, Alt o Shift) y A–Z, 0–9 o F1–F11.');
            return this.queue;
        }
        this.enabled = enabled === true; this.bindings = { ...bindings }; this.active = false;
        return this.apply();
    }
    apply(error = '') {
        const revision = ++this.revision;
        this.active = false;
        this.queue = this.queue.catch(() => {}).then(async () => {
            if (revision !== this.revision) return;
            const bridge = this.bridge;
            if (!bridge?.native || !bridge.setGameHotkeys) {
                this.onState({ active: false, enabled: false, message: error || 'Atajos disponibles en Companion para Windows.' }); return;
            }
            const session = this.session, enabled = this.enabled && Boolean(session);
            try {
                await bridge.setGameHotkeys(session, enabled ? this.bindings : null);
                if (revision !== this.revision) return;
                this.active = enabled;
                try { this.storage?.setItem('irisGameKeys.v1', JSON.stringify(this.bindings)); } catch { /* Session still works. */ }
                this.onState({ active: enabled, enabled: this.enabled, message: error || (enabled ? 'Atajos activos durante esta sesión.' : this.enabled ? 'Se activarán al compartir una ventana.' : 'Atajos globales desactivados.') });
            } catch (reason) {
                if (revision !== this.revision) return;
                this.enabled = this.active = false;
                this.onState({ active: false, enabled: false, message: String(reason || 'No pude registrar los atajos. Usa los botones del modo juego.') });
                try { await bridge.setGameHotkeys('', null); } catch { /* Native hide/reload also clears them. */ }
            }
        });
        return this.queue;
    }
    dispose() { this.enabled = false; return this.setSession(''); }
}
