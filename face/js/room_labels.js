// Presentation only: keep protocol states, model names and user content unchanged.
export const ROOM_STATUS_LABELS = Object.freeze({
    connected: 'Conectado', idle: 'Listo', ready: 'Listo',
    listening: 'Escuchando…', thinking: 'Pensando…', speaking: 'Hablando…',
    disconnected: 'Desconectado', connecting: 'Conectando…', error: 'Error',
});

export function normalizeRoomText(root = document, irisVersion = '') {
    const labels = {
        '.IRIS-name': 'IRIS', '.IRIS-version': String(irisVersion).trim() ? `v${String(irisVersion).trim()}` : 'v—',
        '#room-selector .room-sel-label': 'Habitación', '#pose-menu-toggle': 'Poses',
        '#emotion-emoji': 'ÁNIMO', '#inspect-top-toggle': 'Explorar',
        '#btn-cam .btn-label': 'Visión', '#btn-chat .btn-label': 'Chat',
        '#btn-brain .btn-label': 'Estado', '#btn-iris-os .btn-label': 'Inicio',
        '#btn-inspector .btn-label': 'Habitación', '#btn-hermes-voice .btn-label': 'Hablar',
        '#btn-memory .btn-label': 'Memoria', '#panel-camera .module-title': 'Visión',
        '#panel-chat .module-title': 'Chat', '#panel-brain .module-title': 'Estado de Iris',
        '#reasoning-pill .reasoning-pill-icon': 'Medio',
    };
    for (const [selector, label] of Object.entries(labels)) {
        const node = root.querySelector(selector);
        if (node) node.textContent = label;
    }
    root.querySelectorAll('.room-btn').forEach(button => {
        const names = { cyber: 'Cyber', sakura: 'Sakura', hacker: 'Hacker', classic: 'Classic' };
        button.textContent = names[button.dataset.room] || button.textContent.trim();
    });
    root.querySelectorAll('#reasoning-select option').forEach(option => {
        const names = { none: 'Ninguno', low: 'Bajo', medium: 'Medio', high: 'Profundo' };
        option.textContent = names[option.value] || option.textContent.trim();
    });
    const input = root.getElementById('chat-input');
    if (input) input.placeholder = 'Escribe a IRIS…  / comandos · Ctrl+K paleta';
}
