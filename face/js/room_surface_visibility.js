// Room controls and the workbench are siblings. Never make their shared root inert.
const ROOM_CONTROLS = '#action-buttons, #room-selector, #pose-menu, #vrma-uploader, '
    + '#inspect-top-toggle, #emotion-display, .inspect-room-hud, '
    + '.panel-toggle-btn, .side-panel, #panel-backdrop';

export class RoomSurfaceVisibility {
    constructor(root = document) {
        this.root = root;
        this.saved = null;
    }

    hide(workbench) {
        if (this.saved) return;
        const nodes = [...this.root.querySelectorAll(ROOM_CONTROLS)];
        const active = this.root.activeElement;
        this.returnFocus = nodes.some(node => node.contains(active)) ? active : null;
        this.workbench = workbench;
        // Move focus out before hiding its ancestors from assistive technology.
        if (this.returnFocus || active === this.root.body) {
            workbench?.querySelector('[data-os-chat]')?.focus({ preventScroll: true });
            if (this.returnFocus && this.root.activeElement === active) active.blur();
        }
        this.saved = nodes.map(node => ({ node, inert: node.inert, ariaHidden: node.getAttribute('aria-hidden') }));
        for (const { node } of this.saved) {
            node.inert = true;
            node.setAttribute('aria-hidden', 'true');
        }
    }

    restore() {
        if (!this.saved) return;
        for (const { node, inert, ariaHidden } of this.saved) {
            node.inert = inert;
            if (ariaHidden === null) node.removeAttribute('aria-hidden');
            else node.setAttribute('aria-hidden', ariaHidden);
        }
        this.saved = null;
        const active = this.root.activeElement;
        // Do not take focus away from another dialog/surface opened meanwhile.
        if (active === this.root.body || this.workbench?.contains(active)) {
            const target = this.returnFocus?.isConnected ? this.returnFocus : this.root.getElementById('btn-iris-os');
            target?.focus({ preventScroll: true });
        }
        this.returnFocus = null;
        this.workbench = null;
    }
}
