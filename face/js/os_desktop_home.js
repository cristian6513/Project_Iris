/** Profile home preferences. Visibility never changes an app's permissions or lifecycle. */
import { HomeReorder } from './os_home_reorder.js?v=4';
import { homeRecovery } from './os_recovery.js?v=1';
import { HomeContextGestures } from './os_home_context_gestures.js?v=1';
export const HOME_SCHEMA = 'iris.desktop-home/v1';
const APP_ID = /^[a-z][a-z0-9-]{1,31}$/;
const REF = /^(?:app:[a-z][a-z0-9-]{1,31}|widget:wdg_[a-f0-9]{32})$/;
export function homeLayout(value) {
    if (!value || value.schema !== HOME_SCHEMA || !Number.isSafeInteger(value.revision) || value.revision < 0
        || value.revision > 1000000000) throw new Error('El inicio guardado no es compatible. No se restableció.');
    for (const [key, limit, pattern] of [['shortcuts', 64, APP_ID], ['hidden_apps', 64, APP_ID], ['order', 128, REF]]) {
        const items = value[key];
        if (!Array.isArray(items) || items.length > limit || new Set(items).size !== items.length
            || items.some((item) => typeof item !== 'string' || !pattern.test(item))) throw new Error('Las preferencias del inicio no son válidas.');
    }
    if (value.order.some((ref) => ref.startsWith('app:') && !value.shortcuts.includes(ref.slice(4)))) throw new Error('El orden del inicio no es válido.');
    return structuredClone(value);
}

export function orderedHomeRefs(layout, widgetIds = []) {
    if (!layout) return [];
    const available = [...layout.shortcuts.map((id) => `app:${id}`), ...widgetIds.map((id) => `widget:${id}`)];
    const live = new Set(available);
    return [...layout.order.filter((ref) => live.has(ref)), ...available.filter((ref) => !layout.order.includes(ref))];
}

export class HomePreferences {
    constructor({ fetchImpl = (...args) => globalThis.fetch(...args) } = {}) {
        this.fetchImpl = fetchImpl; this.layout = null; this.error = ''; this.busy = false;
        this.loading = null; this.listeners = new Set(); this.disposed = false; this.controllers = new Set();
        // F17: apps uninstalled while a request may still answer with an older home (Codex L02).
        this.retiredApps = new Set(); this.idleWaiters = []; this.reads = 0;
        // Keep removal history after reinstall: each response is checked against when its request began (L03).
        this.retirement = 0; this.retiredAt = new Map();
    }
    subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
    notify() { for (const fn of this.listeners) fn(); }
    async request(method = 'GET', body) {
        const controller = new AbortController(); this.controllers.add(controller);
        try {
            const response = await this.fetchImpl('/api/desktop/home', { method, cache: 'no-store', signal: controller.signal,
                headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined });
            const data = await response.json();
            if (!response.ok) { const error = new Error(data?.error?.message || 'No se pudo guardar el inicio.'); error.status = response.status; throw error; }
            return homeLayout(data);
        } finally { this.controllers.delete(controller); }
    }
    async load() {
        if (this.disposed || this.busy) return;
        if (this.loading) return this.loading;
        this.reads += 1;
        const retirement = this.retirement;
        this.loading = (async () => {
            try { const value = await this.request(); if (!this.disposed) { this.layout = this.withoutRetired(value, retirement); this.error = ''; } }
            catch (error) { if (!this.disposed && error.name !== 'AbortError') { this.error = error.message; this.recovery = homeRecovery(error); } }
            finally { if (!this.disposed) { this.loading = null; this.notify(); } }
        })();
        this.notify(); return this.loading;
    }
    async change(update) {
        if (!this.layout || this.busy || this.loading || this.disposed) return false;
        const candidate = structuredClone(this.layout);
        if (update(candidate) === false) return false;
        const retirement = this.retirement;
        homeLayout(candidate); this.busy = true; this.error = ''; this.notify();
        try {
            const value = await this.request('PUT', candidate);
            if (!this.disposed) this.layout = this.withoutRetired(value, retirement);
            return !this.disposed;
        } catch (error) {
            if (!this.disposed && error.name !== 'AbortError') {
                this.error = error.message;
                this.recovery = homeRecovery(error);
                if (error.status === 409) {
                    const retirement = this.retirement;
                    try { const value = await this.request(); if (!this.disposed) this.layout = this.withoutRetired(value, retirement); }
                    catch { if (!this.disposed) this.error += ' No se pudo recuperar el inicio actual. Reintenta la lectura.'; }
                }
            }
            return false;
        } finally {
            if (!this.disposed) { this.busy = false; this.notify(); }
            for (const resolve of this.idleWaiters.splice(0)) resolve();
        }
    }
    /** A home without the shortcuts of apps uninstalled meanwhile: an older answer cannot bring them back. */
    withoutRetired(layout, retirement = this.retirement) {
        if (!layout || (!this.retiredApps.size && !this.retiredAt.size)) return layout;
        const gone = (id) => this.retiredApps.has(id) || (this.retiredAt.get(id) || 0) > retirement;
        return { ...layout, shortcuts: layout.shortcuts.filter((id) => !gone(id)), hidden_apps: layout.hidden_apps.filter((id) => !gone(id)),
            order: layout.order.filter((ref) => !(ref.startsWith('app:') && gone(ref.slice(4)))) };
    }
    /**
     * F17: apps uninstalled from Iris Store. Core already removed their shortcuts: they go here at
     * once, even while a save is in flight, and Core's home is read again once nothing is pending.
     */
    async forgetApps(appIds) {
        if (!appIds?.length || this.disposed) return true;
        const retirement = ++this.retirement;
        for (const id of appIds) { this.retiredApps.add(id); this.retiredAt.set(id, retirement); }
        if (this.layout) { this.layout = this.withoutRetired(this.layout); this.notify(); }
        while (!this.disposed) {
            if (this.busy) { await new Promise((resolve) => this.idleWaiters.push(resolve)); continue; }
            if (this.loading) { await this.loading; continue; }
            const reads = this.reads;
            await this.load();
            if (this.reads === reads) continue;
            if (this.error) return false;
            this.retiredApps.clear(); // Core's own answer, read after the removal
            return true;
        }
        return false;
    }
    /** A new installation may add shortcuts, but cannot validate replies started before its removal (L03). */
    restoreApps(appIds) { for (const id of appIds || []) this.retiredApps.delete(id); }
    pin(id) { return this.change((value) => { if (value.shortcuts.includes(id) || value.shortcuts.length >= 64) return false; value.shortcuts.push(id); }); }
    remove(id) { return this.change((value) => { value.shortcuts = value.shortcuts.filter((key) => key !== id); value.order = value.order.filter((ref) => ref !== `app:${id}`); }); }
    hide(id, hidden) { return this.change((value) => {
        const present = value.hidden_apps.includes(id); if (present === hidden) return false;
        value.hidden_apps = hidden ? [...value.hidden_apps, id] : value.hidden_apps.filter((key) => key !== id);
    }); }
    move(ref, by, widgetIds) { return this.change((value) => {
        const refs = orderedHomeRefs(value, widgetIds), from = refs.indexOf(ref), to = from + by;
        if (from < 0 || to < 0 || to >= refs.length) return false;
        [refs[from], refs[to]] = [refs[to], refs[from]]; value.order = refs;
    }); }
    place(ref, target, widgetIds, revision) { return this.change((value) => {
        if (value.revision !== revision) return false;
        const refs = orderedHomeRefs(value, widgetIds), from = refs.indexOf(ref), to = refs.indexOf(target);
        if (from < 0 || to < 0 || from === to) return false;
        refs.splice(from, 1); refs.splice(to, 0, ref); value.order = refs;
    }); }
    dispose() { this.disposed = true; for (const controller of this.controllers) controller.abort(); this.controllers.clear(); this.listeners.clear(); }
}

export function hidingDescription(id) {
    return ['mail', 'calendar'].includes(id)
        ? 'La sincronización y los avisos que tengas activados continuarán.'
        : 'Solo oculta el icono; la app sigue funcionando.';
}

export function drawerApps(registry, layout, filter = '') {
    const text = (value) => String(value).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es');
    const query = text(filter.trim()), hidden = new Set(layout?.hidden_apps || []);
    const apps = registry.launcher().filter((app) => !hidden.has(app.id) && text(`${app.name} ${app.description}`).includes(query));
    return query ? apps.sort((a, b) => Number(text(b.name).includes(query)) - Number(text(a.name).includes(query))) : apps;
}

/** The existing widget list becomes the shared home grid; widget instances are never cloned. */
export class DesktopHome {
    constructor({ root, drawer, model, widgets, registry, icon, openApp, showHome = () => {}, documentLike = document }) {
        Object.assign(this, { root, drawer, model, widgets, registry, icon, openApp, showHome, document: documentLike });
        this.widgets.list.tabIndex = 0; this.widgets.list.setAttribute('aria-label', 'Accesos y widgets del inicio');
        this.shortcutNodes = new Map(); this.editing = false; this.filter = ''; this.menu = null;
        this.root.classList.add('os-home-personalized');
        this.root.parentElement.classList.add('personalized-home');
        this.toolbar = this.node('div', 'os-home-toolbar');
        this.message = this.node('div', 'os-home-message'); this.message.setAttribute('role', 'status');
        this.root.append(this.toolbar, this.message);
        this.editButton = this.button('Personalizar', () => this.editing ? this.toggleEditing() : this.openHomeMenu(this.editButton));
        this.editButton.dataset.homeFocus = 'personalize';
        this.toolbar.append(this.editButton);
        this.help = this.node('span', 'os-home-gesture-hint', 'Arrastra para ordenar. Clic derecho o mantén pulsado para ver opciones.');
        this.toolbar.append(this.help);
        this.search = this.node('input', 'os-drawer-search'); this.search.type = 'search';
        this.search.placeholder = 'Buscar aplicaciones…'; this.search.setAttribute('aria-label', 'Buscar aplicaciones');
        this.search.addEventListener('input', () => { this.filter = this.search.value; this.renderDrawer(); });
        this.search.addEventListener('keydown', (ev) => {
            if (['Enter', 'ArrowDown'].includes(ev.key)) {
                ev.preventDefault(); const first = this.drawer.querySelector('[data-home-open]');
                if (ev.key === 'Enter') first?.click(); else first?.focus();
            }
        });
        this.drawer.querySelector('header').after(this.search);
        this.grid = this.drawer.querySelector('.os-launcher-grid');
        this.abort = new AbortController();
        this.document.addEventListener('pointerdown', (ev) => {
            if (this.menu && !this.menu.contains(ev.target) && !this.menuAnchor?.contains(ev.target)) this.closeMenu(false);
        }, { signal: this.abort.signal });
        this.document.defaultView?.addEventListener('resize', () => this.closeMenu(false), { signal: this.abort.signal });
        this.unsubscribe = this.model.subscribe(() => this.render());
        // Icons and widgets move directly, as on a phone: no edit mode needed (QA 2026-10-20).
        this.reorder = new HomeReorder({ list: this.widgets.list, model: this.model, documentLike,
            widgetIds: () => this.widgets.layout?.widgets.map((item) => item.instance_id) || [],
            enabled: () => !this.disabled && !this.widgets.busy && !this.widgets.loading && !this.widgets.editing
                && this.visible !== false && this.root.isConnected,
            onLift: () => this.closeMenu(false), onSettle: () => this.syncWidgets(),
            announce: (text) => this.announce(text) });
        this.gestures = new HomeContextGestures({ surfaces: [this.root.parentElement, this.drawer], documentLike,
            enabled: () => !this.disposed && this.document.visibilityState !== 'hidden',
            resolve: (target) => this.contextTarget(target),
            open: (target, point) => target.id ? this.openMenu(target.id, target.anchor, target.home, point)
                : this.openHomeMenu(target.anchor, point) });
        this.render();
    }
    node(tag, className = '', text = '') { const node = this.document.createElement(tag); node.className = className; node.textContent = text; return node; }
    button(text, action, className = 'os-home-button') {
        const node = this.node('button', className, text); node.type = 'button'; node.addEventListener('click', action); return node;
    }
    get disabled() { return this.model.busy || !!this.model.loading || !this.model.layout; }
    announce(text) {
        this.message.textContent = this.model.error || text;
        // Outside edit mode a drag note fades away instead of staying on the home.
        clearTimeout(this.announceTimer);
        if (!this.editing && !this.model.error && text) {
            this.announceTimer = setTimeout(() => { if (this.message.textContent === text && !this.editing) this.message.textContent = ''; }, 3000);
        }
    }
    focusKey() { return this.document.activeElement?.dataset?.homeFocus || ''; }
    restoreFocus(key) {
        if (!key) return;
        const nodes = [...this.root.querySelectorAll('[data-home-focus]'), ...this.grid.querySelectorAll('[data-home-focus]')];
        let node = nodes.find((entry) => entry.dataset.homeFocus === key);
        if (node?.disabled && /^(?:-1|1):/.test(key)) {
            const alternate = key.startsWith('-1:') ? key.replace('-1:', '1:') : key.replace('1:', '-1:');
            node = nodes.find((entry) => entry.dataset.homeFocus === alternate && !entry.disabled);
        }
        if (node && !node.disabled) node.focus();
        else this.editButton.focus();
    }
    tile(app, { home = false } = {}) {
        const row = this.node('article', home ? 'os-home-tile' : 'os-drawer-tile');
        const launch = this.button('', () => this.openApp(app.id), 'os-home-launch');
        launch.dataset.homeOpen = app.id; launch.dataset.homeFocus = `${home ? 'home' : 'drawer'}:${app.id}`;
        const glyph = this.node('span', 'os-home-glyph'); glyph.innerHTML = this.icon(app.icon);
        launch.append(glyph, this.node('strong', '', app.launcher?.label || app.name));
        if (!home) launch.append(this.node('small', '', app.description));
        launch.setAttribute('aria-description', 'Clic para abrir. Clic derecho, pulsación prolongada o Mayús+F10 para ver opciones.');
        row.append(launch);
        return row;
    }
    renderDrawer() {
        const focus = this.focusKey();
        const query = this.filter.trim();
        const apps = drawerApps(this.registry, this.model.layout, query);
        this.grid.replaceChildren(...apps.map((app) => this.tile(app)));
        if (!apps.length) this.grid.append(this.node('p', 'os-home-empty', query ? 'No hay aplicaciones que coincidan.' : 'No hay iconos visibles. Puedes recuperarlos en Aplicaciones.'));
        this.grid.append(this.button('Administrar aplicaciones', () => this.openApp('app-manager'), 'os-drawer-manage'));
        if (focus.startsWith('drawer:') || focus.startsWith('options:drawer:')) this.restoreFocus(focus);
    }
    render() {
        this.reorder?.cancel(false);
        const focus = this.focusKey() || this.pendingFocus || '';
        if (this.disabled) this.pendingFocus = focus;
        else this.pendingFocus = '';
        this.editButton.textContent = this.editing ? 'Listo' : 'Personalizar';
        this.editButton.disabled = false;
        if (this.editing) { this.editButton.removeAttribute?.('aria-haspopup'); this.editButton.removeAttribute?.('aria-expanded'); }
        else { this.editButton.setAttribute('aria-haspopup', 'menu'); this.editButton.setAttribute('aria-expanded', String(this.menuAnchor === this.editButton)); }
        for (const update of this.menuUpdates || []) update();
        this.message.replaceChildren();
        if (this.model.error) {
            const recovery = this.model.recovery || homeRecovery(new Error(this.model.error));
            this.message.append(this.document.createTextNode(recovery.message + ' '), this.button(recovery.action, () => void this.model.load()));
            const details = this.node('details', 'os-home-error-detail');
            details.append(this.node('summary', '', 'Detalles del problema'), this.node('span', '', recovery.detail)); this.message.append(details);
        } else this.message.textContent = this.model.busy ? 'Guardando inicio…' : !this.model.layout ? 'Leyendo tu inicio…'
            : this.editing ? 'Arrastra los iconos y widgets, o usa Anterior y Siguiente. Quitar un acceso no desinstala la app.' : '';
        const ids = new Set(this.model.layout?.shortcuts || []);
        for (const [id, node] of this.shortcutNodes) if (!ids.has(id)) { node.remove(); this.shortcutNodes.delete(id); }
        for (const id of ids) {
            if (!this.shortcutNodes.has(id)) {
                const app = this.registry.get(id);
                const node = app ? this.tile(app, { home: true }) : this.node('article', 'os-home-tile', 'Aplicación no disponible');
                this.shortcutNodes.set(id, node);
            }
        }
        this.syncWidgets(); this.renderDrawer();
        if (!this.disabled && focus && !focus.startsWith('drawer:') && !focus.startsWith('options:drawer:')) this.restoreFocus(focus);
    }
    syncWidgets() {
        this.reorder?.cancel(false);
        const focus = this.focusKey();
        const widgetIds = this.widgets.layout?.widgets.map((item) => item.instance_id) || [];
        const refs = orderedHomeRefs(this.model.layout, widgetIds);
        // Widget preferences may arrive first, including when the home store cannot be read.
        if (!this.model.layout) refs.push(...widgetIds.map((id) => `widget:${id}`));
        const nodes = refs.map((ref) => [ref, ref.startsWith('app:') ? this.shortcutNodes.get(ref.slice(4)) : this.widgets.cards.get(ref.slice(7))?.root])
            .filter(([, node]) => node);
        this.root.classList.toggle('editing-home', this.editing);
        nodes.forEach(([ref, node], index) => {
            node.dataset.homeRef = ref;
            node.querySelector('.os-home-item-controls')?.remove();
            if (this.editing) {
                // Icon buttons: the row must fit an icon the size of the dock's. The whole item drags.
                const name = node.getAttribute('aria-label') || node.querySelector('strong')?.textContent || 'este elemento';
                const controls = this.node('div', 'os-home-item-controls');
                const control = (label, icon, action) => {
                    const button = this.button('', action, 'os-home-button os-home-control');
                    button.innerHTML = this.icon(icon); button.setAttribute('aria-label', `${label}: ${name}`); button.title = label;
                    return button;
                };
                for (const [delta, label] of [[-1, 'Anterior'], [1, 'Siguiente']]) {
                    const button = control(label, 'chevron', () => void this.model.move(ref, delta, widgetIds));
                    button.dataset.homeFocus = `${delta}:${ref}`; button.disabled = this.disabled || index + delta < 0 || index + delta >= nodes.length;
                    if (delta < 0) button.classList.add('os-home-control-back');
                    controls.append(button);
                }
                const remove = control('Quitar', 'close', () => ref.startsWith('app:') ? void this.model.remove(ref.slice(4)) : void this.widgets.remove(ref.slice(7)));
                remove.disabled = this.disabled || this.widgets.busy || !!this.widgets.loading; remove.dataset.homeFocus = `remove:${ref}`; controls.append(remove);
                if (ref.startsWith('widget:')) node.prepend(controls); else node.append(controls);
            }
        });
        const list = this.widgets.list;
        // A dropped item keeps its new place while it saves; the saved order arrives right after.
        if (!this.reorder?.settling && nodes.some(([, node], index) => list.children[index] !== node)) list.append(...nodes.map(([, node]) => node));
        let empty = this.root.querySelector('.os-home-empty-state');
        if (!nodes.length && this.model.layout) {
            if (!empty) { empty = this.node('p', 'os-home-empty-state', 'Tu inicio está libre. Abre Aplicaciones y agrega tus accesos o widgets.'); this.root.append(empty); }
        } else empty?.remove();
        this.widgets.syncVisibility();
        if (focus && !this.disabled) this.restoreFocus(focus);
    }
    toggleEditing() {
        if (!this.editing && this.disabled) return;
        this.widgets.closeEditor(false); this.editing = !this.editing; this.widgets.homeEditing = this.editing;
        this.render(); this.widgets.syncVisibility(); this.editButton.focus();
    }
    drawerOpened() { this.closeMenu(false); this.search.focus(); }
    contextTarget(target) {
        if (!target?.closest) return null;
        if (target.closest('.os-home-item-controls, .os-widgets-card, .os-widgets-editor')) return null;
        const tile = target.closest('.os-home-tile, .os-drawer-tile');
        if (tile && (this.widgets.list.contains(tile) || this.grid.contains(tile))) {
            const launch = tile.querySelector('[data-home-open]');
            return launch ? { id: launch.dataset.homeOpen, home: this.widgets.list.contains(tile), anchor: launch, element: tile } : null;
        }
        if ([this.root.parentElement, this.root, this.widgets.list].includes(target) || target.closest('.os-home-empty-state')) {
            return { anchor: this.editButton, element: this.root.parentElement };
        }
        return null;
    }
    openWidgets(widgetId = '') {
        this.showHome();
        if (this.editing) { this.editing = false; this.widgets.homeEditing = false; this.render(); }
        this.widgets.openEditor({ widgetId, anchor: this.editButton });
    }
    openHomeMenu(anchor = this.editButton, point) {
        const { action, finish } = this.createMenu('Personalizar inicio', anchor, point);
        action('Widgets', () => this.openWidgets(), { icon: 'apps', detail: 'Explora vistas previas y añade un widget.', disabled: false });
        action('Editar inicio', () => this.toggleEditing(), { icon: 'edit', detail: 'Mueve o quita accesos y widgets.' });
        action('Administrar aplicaciones', () => this.openApp('app-manager'), { icon: 'settings', disabled: false });
        finish();
    }
    openMenu(id, anchor, home = false, point) {
        const app = this.registry.get(id); if (!app) return;
        const { action, finish } = this.createMenu(`Opciones de ${app.name}`, anchor, point);
        if (home) action('Quitar de inicio', () => this.model.remove(id), { icon: 'close' });
        else action(this.model.layout?.shortcuts.includes(id) ? 'Ya está en inicio' : 'Agregar a inicio', () => this.model.pin(id),
            { icon: 'home', disabled: () => this.disabled || this.model.layout?.shortcuts.includes(id) || this.model.layout?.shortcuts.length >= 64 });
        for (const widget of app.widgets || []) action(`Agregar widget: ${widget.name}`, () => this.openWidgets(widget.id),
            { icon: 'apps', detail: 'Ver vista previa', disabled: false });
        action('Ocultar del cajón', () => this.model.hide(id, true), { icon: 'block', detail: hidingDescription(id) });
        if (['mail', 'calendar'].includes(id)) action('Gestionar cuentas en Conexiones', () => this.openApp('conexiones'), { icon: 'link', disabled: false });
        action('Administrar aplicaciones', () => this.openApp('app-manager'), { icon: 'settings', disabled: false });
        finish();
    }
    createMenu(label, anchor, point) {
        this.closeMenu(false);
        this.menuAnchor = anchor; const menu = this.node('div', 'os-home-context');
        this.menuUpdates = [];
        this.menu = menu; menu.setAttribute('role', 'menu'); menu.setAttribute('aria-label', label);
        if (anchor === this.editButton) anchor.setAttribute('aria-expanded', 'true');
        const action = (label, run, { detail = '', icon = 'apps', disabled = () => this.disabled } = {}) => {
            const button = this.button('', () => { if (button.disabled) return; this.closeMenu(); void run(); }, 'os-home-menu-action');
            const glyph = this.node('span', 'os-home-menu-icon'); glyph.innerHTML = this.icon(icon);
            const text = this.node('span', 'os-home-menu-label', label); button.append(glyph, text);
            button.setAttribute('role', 'menuitem');
            const update = () => { button.disabled = typeof disabled === 'function' ? !!disabled() : disabled; };
            this.menuUpdates.push(update); update();
            if (detail) { const info = this.node('small', '', detail); info.id = `home-help-${menu.children.length}`; text.append(info); button.setAttribute('aria-describedby', info.id); }
            menu.append(button);
        };
        const finish = () => {
            const panel = this.root.closest('.hermes-workbench'); panel.append(menu);
            const rect = anchor.getBoundingClientRect(), box = panel.getBoundingClientRect();
            menu.style.left = `${Math.max(8, Math.min((point?.x ?? rect.left) - box.left, box.width - menu.offsetWidth - 8))}px`;
            menu.style.top = `${Math.max(8, Math.min((point?.y ?? rect.bottom + 4) - box.top, box.height - menu.offsetHeight - 8))}px`;
            menu.querySelector('button:not(:disabled)')?.focus();
        };
        menu.addEventListener('keydown', (ev) => {
            if (ev.key === 'Escape') { ev.stopPropagation(); ev.preventDefault(); this.closeMenu(); }
            if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(ev.key)) {
                ev.preventDefault(); const buttons = [...menu.querySelectorAll('button:not(:disabled)')], index = buttons.indexOf(this.document.activeElement);
                const next = ev.key === 'Home' ? 0 : ev.key === 'End' ? buttons.length - 1 : (index + (ev.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
                buttons[next]?.focus();
            }
            if (ev.key === 'Tab') this.closeMenu(false);
        });
        return { action, finish };
    }
    closeMenu(focus = true) {
        this.menu?.remove(); this.menu = null; this.menuUpdates = [];
        if (this.menuAnchor === this.editButton && !this.editing) this.menuAnchor.setAttribute('aria-expanded', 'false');
        if (focus && this.menuAnchor?.isConnected) this.menuAnchor.focus(); this.menuAnchor = null;
    }
    setVisibility(visible) {
        if (visible === this.visible) return;
        this.visible = visible;
        if (!visible) { this.gestures.cancel(); this.reorder.cancel(false); this.closeMenu(false); if (this.widgets.editing) this.widgets.closeEditor(false); }
    }
    dispose() { this.disposed = true; clearTimeout(this.announceTimer); this.gestures.dispose(); this.reorder.dispose(); this.closeMenu(false); this.abort.abort(); this.unsubscribe(); this.toolbar.remove(); this.message.remove(); for (const node of this.shortcutNodes.values()) node.remove(); }
}
