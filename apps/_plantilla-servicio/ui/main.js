/**
 * Template of an IRIS OS app that reads its data from its own Python service (F09.3).
 *
 * Same contract as apps/_plantilla (mount, refresh, setVisibility, navigate, dispose); the
 * data comes from GET /api/apps/<id>/items, served by backend/service.py once Core mounts it.
 * `handleIntent` answers the operations Core registered for the app and validated (F09.4):
 * here «show_item», which marks one element on screen.
 * A newer read always wins: an older answer that arrives late is dropped, and retiring the
 * app cancels what is still in flight.
 *
 * Renaming the app: replace «mi-servicio» with its id in this folder (app.json, CSS below and
 * style.css). The service URL uses context.id; tests find elements by data-part.
 */

// The app's class prefix: every class is CSS or CSS-<part>, matching style.css.
const CSS = 'mi-servicio';

const SOURCES = Object.freeze({ ejemplo: 'datos de ejemplo', perfil: 'datos de tu perfil' });

export function createApp(context = {}, { fetchImpl = (...args) => globalThis.fetch(...args) } = {}) {
    const document = globalThis.document;
    const state = { items: [], source: '', status: 'loading', error: '', shown: '' };
    let parts = null;
    let root = null;
    let generation = 0;
    let request = null;
    // Retiring the app (its context signal) cancels whatever read is still in flight.
    context.signal?.addEventListener?.('abort', () => request?.abort(), { once: true });

    /** One element of the app: class CSS-<part> (CSS for the root) and data-part for tests. */
    const el = (tag, part = '', text = '') => {
        const node = document.createElement(tag);
        node.className = part ? `${CSS}-${part}` : CSS;
        node.dataset.part = part || 'app';
        if (text) node.textContent = text;
        return node;
    };

    function render() {
        if (!parts) return;
        parts.status.textContent = state.status === 'loading' ? 'Cargando…'
            : state.status === 'error' ? `${state.error} Pulsa «Actualizar».`
            : state.items.length ? `${state.items.length} · ${SOURCES[state.source] || state.source}` : 'No hay elementos.';
        parts.list.replaceChildren(...state.items.map((item) => {
            const row = el('li', 'item');
            if (item.id === state.shown) row.setAttribute('aria-current', 'true');
            row.append(el('strong', 'title', item.title), el('span', 'detail', item.detail));
            return row;
        }));
    }

    async function load() {
        const mine = ++generation;
        request?.abort();
        request = new AbortController();
        const signal = request.signal;
        state.status = 'loading';
        render();
        try {
            const response = await fetchImpl(`/api/apps/${context.id}/items`, { cache: 'no-store', signal });
            const body = await response.json().catch(() => ({}));
            if (mine !== generation) return; // an older read: dropped
            if (response.status === 404) throw new Error('El servicio de esta app no está activo en Iris.');
            if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'El servicio no respondió bien.');
            state.items = Array.isArray(body.items) ? body.items : [];
            state.source = String(body.source || '');
            state.status = 'ready';
            context.setBrief?.(state.items.length === 1 ? '1 elemento' : `${state.items.length} elementos`);
        } catch (error) {
            if (mine !== generation || signal.aborted) return;
            state.status = 'error';
            state.error = error?.message || 'No se pudieron leer los datos.';
        }
        render();
    }

    return {
        mount(target) {
            root = target;
            const app = el('section');
            const refresh = el('button', 'refresh', 'Actualizar');
            refresh.type = 'button';
            refresh.addEventListener('click', () => { void load(); });
            parts = { status: el('p', 'status'), list: el('ul', 'list') };
            parts.status.setAttribute('role', 'status');
            app.append(refresh, parts.status, parts.list);
            root.replaceChildren(app);
            render();
        },
        refresh({ force = false } = {}) {
            if (force || state.status !== 'ready') return load();
            return undefined;
        },
        setVisibility() {},
        /** An operation Core validated and the person allowed: answer with what really happened. */
        async handleIntent({ action, args = {} } = {}) {
            if (action !== 'show_item') return { status: 'error', message: 'Acción no admitida por esta app.' };
            if (state.status !== 'ready') await load();
            if (!state.items.some((item) => item.id === args.item)) {
                return { status: 'error', message: 'Ese elemento no está en la lista.' };
            }
            state.shown = args.item;
            render();
            return { status: 'ok', message: 'Elemento mostrado.', data: { item: args.item } };
        },
        dispose() {
            generation += 1;
            request?.abort();
            root?.replaceChildren();
            root = null;
            parts = null;
        },
    };
}
