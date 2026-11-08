/** F12a: built-in widgets only; profile-backed preferences independent of OS windows. */
export const WIDGET_SCHEMA = 'iris.desktop-widgets/v1';
// Live bindings: they include the apps installed from Iris Store once app.js has loaded them (F17).
import { OS_WIDGET_CATALOG as WIDGET_CATALOG, OS_WIDGET_MODULES, OS_WIDGET_STYLES } from './os_app_sources.js?v=4';
import { renderWidgetPreview } from './os_widget_preview.js?v=1';
export { WIDGET_CATALOG };

export function widgetLayout(value) {
    if (!value || value.schema !== WIDGET_SCHEMA || !Number.isSafeInteger(value.revision) || value.revision < 0
        || !['left', 'right'].includes(value.side) || !Array.isArray(value.widgets) || value.widgets.length > 4) throw new Error('Preferencias de widgets no compatibles.');
    const ids = new Set();
    for (const item of value.widgets) {
        if (!item || !/^wdg_[a-f0-9]{32}$/.test(item.instance_id) || ids.has(item.instance_id)
            || typeof item.widget_id !== 'string' || !/^[a-z][a-z0-9-]{1,31}\.[a-z][a-z0-9-]{0,31}$/.test(item.widget_id)
            || !Number.isSafeInteger(item.config_version) || item.config_version < 1 || item.config_version > 1000000
            || typeof item.size !== 'string' || !/^[a-z][a-z0-9-]{0,23}$/.test(item.size)
            || !Array.isArray(item.calendars) || item.calendars.length > 50 || new Set(item.calendars).size !== item.calendars.length
            || item.calendars.some((key) => typeof key !== 'string' || !/^cal_[a-f0-9]{32}$/.test(key))) throw new Error('Preferencias de widgets no compatibles.');
        ids.add(item.instance_id);
    }
    return structuredClone(value);
}

export class DesktopWidgets {
    constructor({ root, factories = {}, openApp = () => {}, isVisible = () => true,
        isExposed = () => true, fetchImpl = (...args) => globalThis.fetch(...args), documentLike = globalThis.document,
        integrated = false, onChange = () => {}, services = {}, askIris = null } = {}) {
        Object.assign(this, { root, factories, openApp, isVisible, isExposed, fetchImpl, document: documentLike, integrated, onChange, services, askIris });
        this.layout = null; this.cards = new Map(); this.visible = false; this.disposed = false;
        this.busy = false; this.loading = null; this.error = ''; this.editing = false; this.epoch = 0;
        this.controllers = new Set();
        // F17: apps uninstalled while a request may still answer with an older layout (Codex L02).
        this.retiredApps = new Set(); this.idleWaiters = [];
        // Keep removal history after reinstall: each response is checked against when its request began (L03).
        this.retirement = 0; this.retiredAt = new Map();
        this.styleSheets = new Map(); // widget id → the <style> this host added (Codex L01)
        this.toolbar = this.node('div', 'toolbar'); this.list = this.node('div', 'list');
        this.editor = this.node('section', 'editor'); this.editor.hidden = true;
        this.editor.setAttribute('aria-label', 'Galería de widgets');
        this.editor.setAttribute('role', 'region');
        this.root.append(this.toolbar, this.list, this.editor);
        this.onScroll = () => this.syncVisibility(); this.list.addEventListener('scroll', this.onScroll);
        this.editor.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') { ev.stopPropagation(); ev.preventDefault(); this.closeEditor(); } });
        this.renderControls();
    }
    node(tag, part, text = '') {
        const node = this.document.createElement(tag); node.className = `os-widgets-${part}`;
        node.dataset.widgetPart = part; if (text) node.textContent = text; return node;
    }
    button(text, key, action, disabled = false) {
        const node = this.node('button', 'button', text); node.type = 'button'; node.dataset.widgetKey = key;
        node.disabled = disabled; node.addEventListener('click', () => { if (!node.disabled) action(); }); return node;
    }
    async call(method = 'GET', body) {
        const controller = new AbortController(); this.controllers.add(controller);
        try {
            const response = await this.fetchImpl('/api/desktop/widgets', { method, cache: 'no-store', signal: controller.signal,
                headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined });
            const data = await response.json();
            if (!response.ok) { const error = new Error(data?.error?.message || 'No se pudo guardar el escritorio.'); error.status = response.status; throw error; }
            return widgetLayout(data);
        } finally { this.controllers.delete(controller); }
    }
    async load() {
        if (this.disposed || this.busy) return;
        if (this.loading) return this.loading;
        const epoch = ++this.epoch;
        const retirement = this.retirement;
        this.loading = (async () => {
            try {
                const layout = await this.call(); if (this.disposed || epoch !== this.epoch) return;
                this.layout = this.withoutRetired(layout, retirement); this.error = ''; this.reconcile();
            } catch (error) { if (!this.disposed && error.name !== 'AbortError') this.error = error.message; }
            finally { if (!this.disposed) { this.loading = null; this.renderControls(); } }
        })();
        return this.loading;
    }
    async save(widgets, side = this.layout?.side) {
        if (this.busy || this.loading || !this.layout || this.disposed) return;
        const candidate = widgetLayout({ ...this.layout, side, widgets });
        const restoreKey = this.editor.contains?.(this.document.activeElement) ? this.document.activeElement?.dataset?.widgetKey : '';
        const retirement = this.retirement;
        this.busy = true; this.error = ''; this.renderControls();
        try {
            const saved = await this.call('PUT', candidate); if (this.disposed) return;
            this.layout = this.withoutRetired(saved, retirement); this.reconcile();
            return true;
        } catch (error) {
            if (!this.disposed && error.name !== 'AbortError') {
                this.error = error.message;
                if (error.status === 409) {
                    const retirement = this.retirement;
                    try { this.layout = this.withoutRetired(await this.call(), retirement); if (!this.disposed) this.reconcile(); }
                    catch { this.error += ' Vuelve a cargar los widgets.'; }
                }
            }
            return false;
        } finally {
            if (!this.disposed) {
                this.busy = false;
                this.renderControls(this.editing && this.editor.contains?.(this.document.activeElement) ? restoreKey : '');
            }
            for (const resolve of this.idleWaiters.splice(0)) resolve();
        }
    }
    /** A layout without the instances of apps uninstalled meanwhile: an older answer cannot bring them back. */
    withoutRetired(layout, retirement = this.retirement) {
        if (!layout || (!this.retiredApps.size && !this.retiredAt.size)) return layout;
        const gone = (id) => this.retiredApps.has(id) || (this.retiredAt.get(id) || 0) > retirement;
        return { ...layout, widgets: layout.widgets.filter((item) => !gone(item.widget_id.split('.')[0])) };
    }
    /** Resolves once no save is in flight. */
    idle() { return this.busy ? new Promise((resolve) => this.idleWaiters.push(resolve)) : Promise.resolve(); }
    /**
     * F17: apps uninstalled from Iris Store (Codex L02). Core already dropped their instances: they go
     * here at once, even while a save is in flight, and Core's layout is read again once nothing is
     * pending. Resolves true when that read succeeded; the instances are gone either way.
     */
    async forgetApps(appIds) {
        if (!appIds?.length || this.disposed) return true;
        const retirement = ++this.retirement;
        for (const id of appIds) { this.retiredApps.add(id); this.retiredAt.set(id, retirement); }
        this.purgeAppStyles(appIds);
        if (this.layout) { this.layout = this.withoutRetired(this.layout); this.reconcile(); }
        while (!this.disposed) {
            if (this.busy) { await this.idle(); continue; }
            if (this.loading) { await this.loading; continue; }
            const epoch = this.epoch;
            await this.load();
            if (this.epoch === epoch) continue; // a save started first: wait for it, then read
            if (this.error) return false;
            this.retiredApps.clear(); // Core's own answer, read after the removal
            return true;
        }
        return false;
    }
    /** A new installation may add widgets, but cannot validate replies started before its removal (L03). */
    restoreApps(appIds) { for (const id of appIds || []) this.retiredApps.delete(id); }
    /** F17 (Codex L01): drop the widget stylesheets of these apps, mounted now or not, so a later version brings its own. */
    purgeAppStyles(appIds) {
        const owners = new Set(appIds);
        const owned = (widgetId) => owners.has(String(widgetId || '').split('.')[0]);
        // The sheets this host added, whatever the DOM can search; then any other copy in <head>.
        for (const [widgetId, sheet] of this.styleSheets) if (owned(widgetId)) { sheet.remove?.(); this.styleSheets.delete(widgetId); }
        for (const sheet of [...(this.document.head?.querySelectorAll?.('style[data-widget-style]') || [])]) {
            if (owned(sheet.dataset?.widgetStyle)) sheet.remove?.();
        }
    }
    add(widgetId) {
        if (!this.layout || this.layout.widgets.length >= 4 || !Object.hasOwn(WIDGET_CATALOG, widgetId)) return;
        const instance_id = `wdg_${crypto.randomUUID().replaceAll('-', '')}`;
        const definition = WIDGET_CATALOG[widgetId];
        return this.save([...this.layout.widgets, { instance_id, widget_id: widgetId, config_version: definition.configVersion, size: definition.size, calendars: [] }]);
    }
    remove(id) { return this.save(this.layout.widgets.filter((item) => item.instance_id !== id)); }
    /** F17: an app updated from Iris Store — its instances start again with the new module and style. */
    remountApp(appId) {
        if (!this.layout || this.disposed) return;
        for (const config of this.layout.widgets) {
            const card = this.cards.get(config.instance_id);
            if (!card || !config.widget_id.startsWith(`${appId}.`)) continue;
            this.retire(card);
            this.document.querySelector?.(`style[data-widget-style="${config.widget_id}"]`)?.remove?.();
            this.styleSheets.delete(config.widget_id);
            card.abort = new AbortController();
            card.root.replaceChildren?.();
            void this.mountCard(card, config);
        }
        this.syncVisibility();
    }
    /** F17: the catalog changed (an app installed or removed while the desktop runs): redraw the gallery. */
    refreshCatalog() {
        if (this.disposed) return;
        if (this.filterWidget && !Object.hasOwn(WIDGET_CATALOG, this.filterWidget)) this.filterWidget = '';
        this.renderControls();
    }
    move(id, by) {
        const widgets = [...this.layout.widgets], from = widgets.findIndex((item) => item.instance_id === id), to = from + by;
        if (from < 0 || to < 0 || to >= widgets.length) return;
        [widgets[from], widgets[to]] = [widgets[to], widgets[from]]; return this.save(widgets);
    }
    configure(id, calendars) {
        return this.save(this.layout.widgets.map((item) => item.instance_id === id ? { ...item, calendars } : item));
    }
    openEditor({ widgetId = '', anchor } = {}) {
        if (this.disposed) return;
        this.returnControl = anchor || this.toolbar.querySelector?.('button');
        this.filterWidget = Object.hasOwn(WIDGET_CATALOG, widgetId) ? widgetId : '';
        this.editing = true; this.renderControls(); this.editor.querySelector?.('button')?.focus();
    }
    closeEditor(focus = true) {
        this.editing = false; this.renderControls();
        if (focus) (this.returnControl || this.toolbar.querySelector?.('button'))?.focus();
    }
    renderControls(preferredKey = '') {
        if (this.disposed) return;
        const focus = this.document.activeElement;
        const key = preferredKey || (this.root.contains?.(focus) ? focus?.dataset?.widgetKey : '');
        this.toolbar.replaceChildren(); this.toolbar.hidden = this.integrated;
        if (!this.integrated) {
            const toggle = this.button('Personalizar escritorio', 'customize', () => this.editing ? this.closeEditor() : this.openEditor());
            toggle.setAttribute('aria-expanded', String(this.editing)); this.toolbar.append(toggle);
        }
        this.editor.hidden = !this.editing;
        this.root.classList?.toggle('choosing-widgets', this.editing);
        const header = this.node('header', 'gallery-header');
        header.append(this.node('h2', 'title', 'Widgets'), this.button('Listo', 'close', () => this.closeEditor()));
        this.editor.replaceChildren(header,
            this.node('p', 'hint', 'Elige cómo se verá tu inicio. Estas vistas usan datos de ejemplo, sin conexiones ni reproducción.'));
        const status = this.node('p', 'hint', this.busy ? 'Guardando widget…' : `${this.layout?.widgets.length || 0} de 4 widgets en tu inicio.`);
        status.setAttribute('role', 'status'); this.editor.append(status);
        if (this.error) { const error = this.node('p', 'error', this.error); error.setAttribute('role', 'alert'); this.editor.append(error); }
        if (!this.layout) this.editor.append(this.node('p', 'hint', this.loading ? 'Leyendo tus widgets…' : 'Tus widgets aún no están disponibles.'),
            this.button('Reintentar', 'retry', () => void this.load(), this.busy || !!this.loading));
        const disabled = this.busy || !!this.loading || !this.layout;
        if (this.filterWidget) this.editor.append(this.button('Ver todos los widgets', 'all', () => { this.filterWidget = ''; this.renderControls(); this.editor.querySelector?.('button')?.focus(); }));
        const gallery = this.node('div', 'gallery');
        if (this.editing) for (const [id, definition] of Object.entries(WIDGET_CATALOG)) {
            if (this.filterWidget && this.filterWidget !== id) continue;
            const card = this.node('article', 'gallery-card');
            const name = this.node('h3', 'gallery-name', definition.name);
            card.append(name, this.node('span', 'gallery-size', definition.size === 'compact' ? 'Compacto' : definition.size),
                renderWidgetPreview(this.document, definition),
                this.button(this.busy ? 'Guardando…' : `Añadir ${definition.name}`, `add:${id}`, () => void this.add(id), disabled || this.layout?.widgets.length >= 4));
            gallery.append(card);
        }
        this.editor.append(gallery);
        if (this.layout) {
            const positions = this.node('div', 'positions'); positions.setAttribute('role', 'group'); positions.setAttribute('aria-label', 'Lado de los widgets');
            for (const [side, text] of [['left', 'Izquierda'], ['right', 'Derecha']]) {
                const button = this.button(text, side, () => void this.save(this.layout.widgets, side), disabled);
                button.setAttribute('aria-pressed', String(this.layout.side === side)); positions.append(button);
            }
            if (!this.integrated) this.editor.append(positions);
            this.layout.widgets.forEach((item, index) => {
                const name = `${WIDGET_CATALOG[item.widget_id]?.name || 'Widget no disponible'} ${index + 1}`;
                const row = this.node('div', 'order-row'); row.append(this.node('strong', 'name', name),
                    this.button('↑', `up:${item.instance_id}`, () => void this.move(item.instance_id, -1), disabled || index === 0),
                    this.button('↓', `down:${item.instance_id}`, () => void this.move(item.instance_id, 1), disabled || index === this.layout.widgets.length - 1),
                    this.button('Quitar', `remove:${item.instance_id}`, () => void this.remove(item.instance_id), disabled));
                row.children[1].setAttribute('aria-label', `Subir ${name}`);
                row.children[2].setAttribute('aria-label', `Bajar ${name}`);
                row.children[3].setAttribute('aria-label', `Quitar ${name}`);
                if (this.integrated) { row.children[2].remove(); row.children[1].remove(); }
                this.editor.append(row);
            });
        }
        if (key) {
            let replacement = this.root.querySelector?.(`[data-widget-key="${key}"]`);
            if (replacement?.disabled && /^(up|down):/.test(key)) replacement = this.root.querySelector?.(`[data-widget-key="${key.startsWith('up:') ? key.replace('up:', 'down:') : key.replace('down:', 'up:')}"]`);
            if (!replacement || replacement.disabled || (!this.editing && this.editor.contains?.(replacement))) replacement = (this.editing ? this.editor : this.toolbar).querySelector?.('button');
            replacement?.focus();
        }
        if (this.visible) this.syncVisibility();
        this.onChange();
    }
    reconcile() {
        if (!this.layout || this.disposed) return;
        this.root.dataset.side = this.layout.side;
        const ids = new Set(this.layout.widgets.map((item) => item.instance_id));
        for (const [id, card] of this.cards) if (!ids.has(id)) { this.retire(card); card.root.remove(); this.cards.delete(id); }
        for (const config of this.layout.widgets) {
            let card = this.cards.get(config.instance_id);
            if (!card) {
                const root = this.node('article', 'card'); root.setAttribute('aria-label', `Widget ${WIDGET_CATALOG[config.widget_id]?.name || 'no disponible'}`);
                // Whose surface this is: a click here can authorize that app's «Avisar a Iris» (Codex SDK02).
                if (WIDGET_CATALOG[config.widget_id]?.app) root.dataset.widgetApp = WIDGET_CATALOG[config.widget_id].app;
                card = { root, controller: null, id: config.instance_id, abort: new AbortController() }; this.cards.set(config.instance_id, card);
                void this.mountCard(card, config);
            }
            try { card.controller?.configure?.(config); } catch { this.failCard(card); }
        }
        const ordered = this.layout.widgets.map((config) => this.cards.get(config.instance_id).root);
        if (this.integrated) {
            // The home owns mixed ordering. A refresh must not detach a widget someone is using.
            const fresh = ordered.filter((root) => root.parentNode !== this.list);
            if (fresh.length) this.list.append(...fresh);
        } else if (ordered.some((root, index) => this.list.children[index] !== root)) this.list.append(...ordered);
        this.syncVisibility();
        this.onChange();
    }
    async mountCard(card, config) {
        // One mount per life of the card: after remountApp (F17), an older load that ends late is disposed.
        const life = card.abort;
        const stale = () => this.disposed || this.cards.get(config.instance_id) !== card || card.abort !== life;
        try {
            const definition = WIDGET_CATALOG[config.widget_id];
            if (!definition || config.config_version !== definition.configVersion || config.size !== definition.size) {
                card.root.replaceChildren(this.node('p', 'error', 'Este widget no está disponible en esta versión. Puedes quitarlo desde Personalizar escritorio.'));
                return;
            }
            const app = definition.app;
            const options = { root: card.root, config, openApp: this.openApp, signal: card.abort.signal,
                ...(this.askIris ? { askIris: (request) => this.askIris(app, request) } : {}),
                services: Object.hasOwn(this.services, config.widget_id) ? this.services[config.widget_id] : Object.freeze({}),
                configure: (calendars) => this.configure(config.instance_id, calendars) };
            let controller;
            if (this.factories[config.widget_id]) controller = await this.factories[config.widget_id](options);
            else {
                const [module, style] = await Promise.all([OS_WIDGET_MODULES[config.widget_id](), OS_WIDGET_STYLES[config.widget_id]?.()]);
                if (stale()) return;
                if (style && !this.document.querySelector?.(`style[data-widget-style="${config.widget_id}"]`)) {
                    const sheet = this.node('style', 'style'); sheet.dataset.widgetStyle = config.widget_id; sheet.textContent = style.default;
                    this.document.head.append(sheet); this.styleSheets.set(config.widget_id, sheet);
                }
                controller = await module.createWidget(options);
            }
            if (stale()) { try { controller?.dispose?.(); } catch {} return; }
            card.controller = controller;
            if (!controller || typeof controller.dispose !== 'function' || typeof controller.setVisibility !== 'function') throw new Error('Widget no disponible.');
            const current = this.layout.widgets.find((item) => item.instance_id === config.instance_id);
            controller.configure?.(current); this.syncVisibility(); this.onChange();
        } catch {
            if (stale()) return;
            this.failCard(card);
        }
    }
    retire(card) {
        card.abort?.abort();
        const controller = card.controller; card.controller = null;
        try { controller?.dispose?.(); } catch { /* A faulty widget cannot prevent the others retiring. */ }
    }
    failCard(card) {
        if (this.disposed || this.cards.get(card.id) !== card) return;
        this.retire(card);
        card.root.replaceChildren(this.node('p', 'error', 'No se pudo cargar este widget.'),
            this.button('Reintentar widget', `retry:${card.id}`, () => {
                this.retire(card); card.root.remove(); this.cards.delete(card.id); this.reconcile();
            }));
    }
    syncVisibility() {
        if (this.disposed) return;
        const visible = Boolean(this.isVisible());
        const returned = visible && !this.visible; this.visible = visible;
        for (const card of this.cards.values()) {
            try { card.controller?.setVisibility?.(visible && !this.editing && !this.homeEditing && this.isExposed(card.root)); }
            catch { this.failCard(card); }
        }
        if (returned) void this.load();
    }
    dispose() {
        this.disposed = true; this.epoch++;
        this.list.removeEventListener?.('scroll', this.onScroll);
        for (const controller of this.controllers) controller.abort(); this.controllers.clear();
        for (const card of this.cards.values()) this.retire(card); this.cards.clear();
        this.root.replaceChildren();
    }
}
