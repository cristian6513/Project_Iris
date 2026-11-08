/**
 * IRIS OS app host (F09.2): loads and keeps the instance of each app that has its own module.
 *
 * The module of an app comes from the catalog the build generates (static imports of reviewed
 * files), never from a path or URL in a manifest. The host imports it once, asks it for a
 * controller with `createApp(context)` and keeps one instance per app:
 *
 *   unloaded → loading → ready            (`ensure`; concurrent calls share one load)
 *                      ↘ error → loading  (only through an explicit `retry`)
 *   any → disposed                        (`dispose`, idempotent; a later `ensure` starts anew)
 *
 * Closing or minimizing a window is not disposing: the shell only reports visibility, so an
 * app keeps its state, forms and work. A load that finishes after its instance was disposed is
 * discarded and disposed too, so it cannot mount anything.
 *
 * Controller contract: `mount(root)` and `dispose()` are required; `setVisibility({ visible,
 * reason })`, `refresh({ force, opening })` and `navigate(payload)` are optional.
 *
 * An app with its own folder may also have a stylesheet (`styles[id]`, a module exporting the
 * CSS text): it loads together with the module and goes to `installStyle(id, css)` first.
 */

export class AppDisposedError extends Error {
    constructor(id) {
        super(`La app ${id} se cerró mientras cargaba.`);
        this.name = 'AppDisposedError';
    }
}

function checkedController(controller, id) {
    if (!controller || typeof controller.mount !== 'function' || typeof controller.dispose !== 'function') {
        throw new TypeError(`La app ${id} no devolvió un controlador con mount() y dispose().`);
    }
    return controller;
}

export function createAppHost({ modules = {}, styles = {}, installStyle = null, makeContext = () => ({}), onError = null } = {}) {
    const records = new Map();
    // F17: a function returns the current table, so an app installed from Iris Store while the
    // desktop runs loads like the others; a plain object stays the fixed table of the build.
    const table = (source) => (typeof source === 'function' ? source() : source) || {};

    function record(id) {
        // `visible`: what the shell last reported; `told`: what the controller last heard.
        if (!records.has(id)) records.set(id, { state: 'unloaded', promise: null, controller: null, error: null, life: null, visible: false, told: null, mounted: null });
        return records.get(id);
    }

    function load(id) {
        const entry = record(id);
        const loader = table(modules)[id];
        const styleLoader = table(styles)[id];
        if (typeof loader !== 'function') {
            entry.state = 'error';
            entry.error = new Error(`La app ${id} no tiene un módulo registrado.`);
            return Promise.reject(entry.error);
        }
        const life = new AbortController();
        entry.life = life;
        entry.state = 'loading';
        entry.error = null;
        entry.promise = (async () => {
            let controller = null;
            try {
                // The app's stylesheet (F09.3) loads with its module and is in place before it renders.
                const [module, style] = await Promise.all([loader(), typeof styleLoader === 'function' ? styleLoader() : null]);
                if (life.signal.aborted) throw new AppDisposedError(id);
                if (style) {
                    if (typeof style.default !== 'string') throw new TypeError(`La hoja de estilos de ${id} no es válida.`);
                    installStyle?.(id, style.default);
                }
                if (typeof module?.createApp !== 'function') throw new TypeError(`El módulo de ${id} no exporta createApp().`);
                const base = makeContext(id) || {};
                const current = () => records.get(id)?.life === life;
                const context = Object.freeze({
                    ...base,
                    signal: life.signal,
                    // The shell's live answer when it gives one; always false once this instance is retired.
                    isVisible: () => current() && (typeof base.isVisible === 'function' ? base.isVisible() === true : records.get(id).visible),
                });
                controller = checkedController(await module.createApp(context), id);
                if (life.signal.aborted) throw new AppDisposedError(id);
            } catch (error) {
                // A late answer for a disposed instance is cleaned up and never published.
                if (controller) {
                    try { controller.dispose(); } catch { /* already reporting the first error */ }
                }
                if (entry.life === life && !life.signal.aborted) {
                    entry.state = 'error';
                    entry.error = error;
                    entry.promise = null;
                    onError?.(id, error);
                }
                throw error;
            }
            entry.state = 'ready';
            entry.controller = controller;
            return controller;
        })();
        // Callers decide what to do with a failure; an unobserved rejection is not a crash.
        entry.promise.catch(() => {});
        return entry.promise;
    }

    const host = {
        state: (id) => records.get(id)?.state || 'unloaded',
        error: (id) => records.get(id)?.error || null,
        get: (id) => (records.get(id)?.state === 'ready' ? records.get(id).controller : null),

        /** The instance of `id`: an existing one, the load in progress, or a new load. */
        ensure(id) {
            const entry = record(id);
            if (entry.state === 'ready') return Promise.resolve(entry.controller);
            if (entry.state === 'loading') return entry.promise;
            if (entry.state === 'error') return Promise.reject(entry.error);
            return load(id);
        },

        /** Try again after an error. Any other state behaves like `ensure`. */
        retry(id) {
            const entry = record(id);
            if (entry.state !== 'error') return host.ensure(id);
            entry.state = 'unloaded';
            return load(id);
        },

        /**
         * Mount the instance in `root` once; returns true the first time for that root. It does not
         * report visibility: the shell first refreshes the app, then reports it (one load on open).
         */
        mount(id, root) {
            const entry = records.get(id);
            if (entry?.state !== 'ready' || !root || entry.mounted === root) return false;
            try {
                entry.controller.mount(root);
            } catch (error) {
                // A controller that cannot mount is retired and the app is left in «error», so
                // «Reintentar» builds a fresh instance (Codex H01). The caller shows the error.
                entry.life?.abort();
                try { entry.controller.dispose(); } catch { /* reporting the mount error */ }
                Object.assign(entry, { state: 'error', error, controller: null, promise: null, mounted: null, told: null });
                onError?.(id, error);
                throw error;
            }
            entry.mounted = root;
            entry.told = null;
            return true;
        },

        /** Report visibility; a mounted controller hears each change once. */
        setVisibility(id, visible, reason = '') {
            const entry = record(id);
            entry.visible = !!visible;
            if (entry.state !== 'ready' || !entry.mounted || entry.told === entry.visible) return;
            entry.told = entry.visible;
            try {
                entry.controller.setVisibility?.({ visible: entry.visible, reason });
            } catch (error) {
                onError?.(id, error); // one app's failure never stops the shell's sync
            }
        },

        /** Retire the instance for good. Safe to call twice or while it is still loading. */
        dispose(id) {
            const entry = records.get(id);
            if (!entry) return false;
            records.delete(id);
            entry.life?.abort();
            if (entry.state === 'ready') {
                try {
                    entry.controller.dispose();
                } catch (error) {
                    onError?.(id, error);
                }
            }
            return true;
        },

        disposeAll() {
            for (const id of [...records.keys()]) host.dispose(id);
        },
    };
    return Object.freeze(host);
}
