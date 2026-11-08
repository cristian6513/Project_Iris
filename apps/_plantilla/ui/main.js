/**
 * Template of an IRIS OS app with its own folder (F09.3): interface only, example data.
 *
 * IRIS OS imports this module the first time the app opens and calls `createApp(context)` once.
 * The returned controller keeps the app alive while its window closes or minimizes; only
 * `dispose()` retires it. `context` gives:
 *   context.id / context.app   the app's id and its app.json (read-only)
 *   context.isVisible()        whether the person can see it right now
 *   context.openApp(id)        open another IRIS OS app
 *   context.setBrief(text)     the short text under the app's name in the launcher
 *   context.signal             aborted when the app is retired
 * Texts go through textContent (never innerHTML) and styles live in style.css, under .mi-app.
 *
 * Renaming the app: replace «mi-app» with its id in this folder (app.json, CSS below and
 * style.css). Tests find elements by their data-part, so they keep working.
 */

// The app's class prefix: every class is CSS or CSS-<part>, matching style.css.
const CSS = 'mi-app';

// Example data, shown as such. A real app reads its own source instead (see _plantilla-servicio).
export const EXAMPLE_ITEMS = Object.freeze([
    { id: 'uno', title: 'Primer elemento', detail: 'Un dato de ejemplo para ver la lista.' },
    { id: 'dos', title: 'Segundo elemento', detail: 'Otro dato de ejemplo, con más texto para ver cómo se ajusta.' },
    { id: 'tres', title: 'Tercer elemento', detail: 'Elige uno para ver su detalle.' },
]);

export function filterItems(items, query = '') {
    const words = String(query).toLocaleLowerCase('es').split(/\s+/).filter(Boolean);
    return items.filter((item) => {
        const text = `${item.title} ${item.detail}`.toLocaleLowerCase('es');
        return words.every((word) => text.includes(word));
    });
}

export const briefText = (items) => (items.length === 1 ? '1 elemento' : `${items.length} elementos`);

export function createApp(context = {}) {
    const document = globalThis.document;
    const state = { items: [], query: '', selected: '', status: 'loading' };
    let root = null;
    let parts = null;
    let clock = null; // refreshes «Abierta hace…» only while visible
    let openedAt = Date.now();

    /** One element of the app: class CSS-<part> (CSS for the root) and data-part for tests. */
    const el = (tag, part = '', text = '') => {
        const node = document.createElement(tag);
        node.className = part ? `${CSS}-${part}` : CSS;
        node.dataset.part = part || 'app';
        if (text) node.textContent = text;
        return node;
    };

    /** Redraw. The list is rebuilt, so the element that had keyboard focus gets it back. */
    function render({ focus = '' } = {}) {
        if (!parts) return;
        const shown = filterItems(state.items, state.query);
        parts.status.textContent = state.status === 'loading' ? 'Cargando…'
            : state.status === 'error' ? 'No se pudieron leer los datos. Pulsa «Actualizar».'
            : shown.length ? `${briefText(shown)} · datos de ejemplo` : 'Nada coincide con la búsqueda.';
        const buttons = new Map();
        parts.list.replaceChildren(...shown.map((item) => {
            const button = el('button', 'item', item.title);
            button.type = 'button';
            button.setAttribute('aria-pressed', String(item.id === state.selected));
            button.addEventListener('click', () => select(item.id));
            buttons.set(item.id, button);
            return button;
        }));
        buttons.get(focus)?.focus?.();
        const current = state.items.find((item) => item.id === state.selected);
        parts.detail.textContent = current ? current.detail : 'Elige un elemento.';
        parts.age.textContent = `Abierta hace ${Math.round((Date.now() - openedAt) / 60000)} min`;
    }

    function select(id, { focus = true } = {}) {
        state.selected = id;
        render({ focus: focus ? id : '' });
    }

    async function load() {
        state.status = 'loading';
        render();
        try {
            state.items = [...EXAMPLE_ITEMS]; // a real app awaits its source here
            state.status = 'ready';
            context.setBrief?.(briefText(state.items));
        } catch {
            state.status = 'error';
        }
        if (!context.signal?.aborted) render();
    }

    function stopClock() {
        clearInterval(clock);
        clock = null;
    }

    return {
        mount(target) {
            root = target;
            const app = el('section');
            const header = el('header', 'header');
            const search = el('input', 'search');
            search.type = 'search';
            search.placeholder = 'Buscar';
            search.setAttribute('aria-label', 'Buscar elementos');
            search.addEventListener('input', () => { state.query = search.value; render(); });
            const refresh = el('button', 'refresh', 'Actualizar');
            refresh.type = 'button';
            refresh.addEventListener('click', () => { void load(); });
            header.append(search, refresh);
            parts = {
                status: el('p', 'status'),
                list: el('div', 'list'),
                detail: el('p', 'detail'),
                age: el('small', 'age'),
            };
            parts.status.setAttribute('role', 'status');
            app.append(header, parts.status, parts.list, parts.detail, parts.age);
            root.replaceChildren(app);
            render();
        },
        /** Opening or restoring the window. `force`: the person asked for fresh data. */
        refresh({ force = false, opening = false } = {}) {
            if (opening) openedAt = Date.now();
            if (force || !state.items.length) return load();
            render();
            return undefined;
        },
        /** Work that only matters on screen runs while visible; hidden, it stops. */
        setVisibility({ visible }) {
            stopClock();
            if (visible) clock = setInterval(render, 30000);
        },
        /** Another part of Iris opens the app on one element: `{ item: 'dos' }`. */
        navigate(payload = {}) {
            if (typeof payload.item === 'string') select(payload.item, { focus: false });
        },
        /** Retired for good: free what the app created. */
        dispose() {
            stopClock();
            root?.replaceChildren();
            root = null;
            parts = null;
        },
    };
}
