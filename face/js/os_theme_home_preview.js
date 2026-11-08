/** Passive projection of the saved Home, never a second shell or widget host. */
import { orderedHomeRefs } from './os_desktop_home.js?v=10';
import { widgetLayout } from './os_widgets.js?v=12';

export function homePreviewItems(layout, widgets, registry, catalog) {
    if (!layout) return [];
    const instances = new Map((widgets?.widgets || []).map(item => [item.instance_id, item]));
    return orderedHomeRefs(layout, [...instances.keys()]).map(ref => {
        if (ref.startsWith('app:')) {
            const app = registry.get(ref.slice(4));
            return { ref, kind: 'app', name: app?.launcher?.label || app?.name || 'App no disponible', icon: app?.icon || 'apps' };
        }
        const instance = instances.get(ref.slice(7)), definition = catalog[instance?.widget_id];
        const compatible = definition && definition.configVersion === instance.config_version && definition.size === instance.size;
        return { ref, kind: 'widget', name: compatible ? definition.name : 'Widget no disponible',
            icon: compatible ? registry.get(definition.app)?.icon || 'apps' : 'apps' };
    });
}

export class ThemeHomePreview {
    constructor({ root, model, getWidgets = () => null, registry, catalog, icon,
        fetchImpl = (...args) => globalThis.fetch(...args), documentLike = globalThis.document }) {
        Object.assign(this, { root, model, getWidgets, registry, catalog, icon, fetchImpl, document: documentLike });
        this.visible = false; this.disposed = false; this.widgets = null; this.error = '';
        this.pending = null; this.generation = 0; this.signature = '';
        this.unsubscribe = model.subscribe(() => this.render());
        this.root.setAttribute('role', 'img'); this.root.setAttribute('aria-label', 'Vista previa de tu Inicio');
    }
    widgetPreferences() {
        const live = this.getWidgets();
        return live && (!this.widgets || live.revision >= this.widgets.revision) ? live : this.widgets;
    }
    async refresh() {
        if (this.disposed || !this.visible || this.pending) return this.pending?.promise;
        const generation = ++this.generation, controller = new AbortController();
        const pending = { controller, promise: null }; this.pending = pending; this.error = ''; this.render();
        pending.promise = (async () => {
            const readWidgets = async () => {
                try {
                    // Read preferences only: calling DesktopWidgets.load here could mount live widgets.
                    const response = await this.fetchImpl('/api/desktop/widgets', { cache: 'no-store', signal: controller.signal });
                    if (!response.ok) throw new Error('widgets unavailable');
                    const value = widgetLayout(await response.json());
                    if (!this.disposed && generation === this.generation) this.widgets = value;
                } catch (error) {
                    if (!this.disposed && generation === this.generation && error.name !== 'AbortError')
                        this.error = 'No se pudo actualizar la vista de tus widgets.';
                }
            };
            await Promise.all([this.model.load(), readWidgets()]);
        })().finally(() => {
            if (!this.disposed && generation === this.generation) { this.pending = null; this.render(); }
        });
        return pending.promise;
    }
    node(tag, part, text = '') {
        const node = this.document.createElement(tag); node.className = `os-theme-home-${part}`;
        node.dataset.previewPart = part; node.textContent = text; return node;
    }
    glyph(name) {
        const glyph = this.node('span', 'icon'); glyph.innerHTML = this.icon(name);
        glyph.setAttribute('aria-hidden', 'true'); return glyph;
    }
    render() {
        if (this.disposed || !this.visible) return;
        const widgets = this.widgetPreferences(), items = homePreviewItems(this.model.layout, widgets, this.registry, this.catalog);
        const error = this.model.error || this.error;
        const signature = JSON.stringify([items, Boolean(this.model.layout), Boolean(widgets), Boolean(this.pending), error]);
        if (signature === this.signature) return; this.signature = signature;
        const frame = this.node('div', 'frame'); frame.setAttribute('aria-hidden', 'true');
        const header = this.node('div', 'header'); header.append(this.glyph('desktop'), this.node('strong', 'brand', 'IRIS OS'));
        frame.append(header);
        const body = this.node('div', 'body'), rail = this.node('div', 'rail');
        rail.append(this.node('span', 'portrait'), this.glyph('desktop'), this.glyph('settings')); body.append(rail);
        const grid = this.node('div', 'grid');
        for (const item of items.slice(0, 8)) {
            const tile = this.node('div', item.kind); tile.dataset.previewRef = item.ref;
            tile.append(this.glyph(item.icon), this.node('span', 'name', item.name));
            if (item.kind === 'widget') { const sample = this.node('div', 'widget-shape'); sample.append(this.node('i', 'line'), this.node('i', 'line')); tile.append(sample); }
            grid.append(tile);
        }
        if (!items.length) grid.append(this.node('span', 'empty', !this.model.layout
            ? this.model.error ? 'Inicio no disponible' : 'Leyendo tu Inicio…'
                : !widgets ? this.error ? 'Widgets no disponibles' : 'Leyendo tu Inicio…' : 'Tu inicio está libre'));
        body.append(grid); frame.append(body);
        const dock = this.node('div', 'dock');
        for (const [name, label] of [['chat', 'Chat'], ['apps', 'Apps'], ['desktop', 'Inicio'], ['desktop', 'Panel']]) {
            const entry = this.node('span', 'dock-item'); entry.append(this.glyph(name), this.node('span', 'dock-label', label)); dock.append(entry);
        }
        frame.append(dock);
        const info = this.node('p', 'status', error ? `${error} ${this.model.layout && widgets ? 'Se conserva la última vista disponible.' : 'No se puede mostrar todo tu Inicio.'}`
            : this.pending ? 'Actualizando la vista de tu Inicio…'
                : items.length > 8 ? `Vista del Inicio · ${items.length - 8} elementos más fuera de la miniatura.`
                    : 'Vista del Inicio · Los widgets se representan sin activarlos.');
        info.setAttribute('role', 'status');
        this.root.setAttribute('aria-label', !this.model.layout ? this.model.error ? 'Inicio no disponible' : 'Leyendo tu Inicio'
            : !widgets ? 'Vista previa incompleta de Inicio' : !items.length ? 'Vista previa: tu Inicio está vacío'
                : `Vista previa del Inicio: ${items.length} elementos. ${items.slice(0, 8).map(item => item.name).join(', ')}.`);
        this.root.replaceChildren(frame, info);
    }
    setVisibility(value) {
        if (this.disposed || this.visible === Boolean(value)) return;
        this.visible = Boolean(value);
        if (this.visible) { this.signature = ''; this.render(); void this.refresh(); }
        else { this.generation++; this.pending?.controller.abort(); this.pending = null; }
    }
    dispose() {
        if (this.disposed) return;
        this.disposed = true; this.generation++; this.pending?.controller.abort(); this.pending = null;
        this.unsubscribe(); this.root.replaceChildren();
    }
}
