import { ROOM_ATMOSPHERES } from './room_atmosphere.js?v=2';

/** Shared select binding for inspection and Quick Configuration, with lazy room wiring. */
export function bindRoomAtmosphereControls(root, getAtmosphere) {
    const select = root?.querySelector('[data-room-atmosphere]');
    const note = root?.querySelector('[data-room-atmosphere-note]');
    if (!select) return { refresh() {}, dispose() {} };
    select.replaceChildren(...Object.entries(ROOM_ATMOSPHERES).map(([value, preset]) => {
        const option = select.ownerDocument.createElement('option');
        option.value = value; option.textContent = preset.label; return option;
    }));
    let controller = null, unsubscribe = null;
    const render = (state) => {
        select.disabled = !state;
        select.value = state?.mode || 'original';
        if (note) note.textContent = state
            ? `${state.description} ${state.persisted === false ? 'Solo esta sesión: no se pudo guardar.' : 'Solo luz · selección manual.'}`
            : 'Habitación no disponible.';
    };
    const refresh = () => {
        const next = getAtmosphere?.() || null;
        if (next !== controller) {
            unsubscribe?.(); controller = next;
            unsubscribe = controller?.subscribe(render) || null;
        }
        render(controller?.snapshot());
    };
    const onChange = () => {
        const requested = select.value;
        refresh(); // The room may have been assigned after the panel was built.
        controller?.setMode(requested);
    };
    select.addEventListener('change', onChange);
    refresh();
    return {
        refresh,
        dispose() { unsubscribe?.(); select.removeEventListener('change', onChange); },
    };
}
