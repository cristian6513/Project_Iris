/**
 * IRIS OS app registry (F09.1): one catalog for the shell's windows, tabs and launcher.
 *
 * The catalog comes from the build (os_app_catalog.js, generated from apps/<id>/app.json).
 * The registry validates it again, keeps the valid apps and answers what a destination is:
 * an app window, an app tab (Cámara, Ajustes), an internal place of the shell (desktop,
 * launcher) or unknown. It holds no code: the app host (os_app_host.js) loads the apps with
 * their own module and the shell's adapters load the rest. Nothing here grants a permission.
 */
import { SHELL_DESTINATIONS, validateAppCatalog } from './os_app_manifest.js?v=5';

export function createAppRegistry(entries, { iconNames = null, onError = null } = {}) {
    const { apps, errors } = validateAppCatalog(entries, { iconNames });
    for (const error of errors) onError?.(error);
    const byId = new Map(apps.map((app) => [app.id, app]));
    const launcher = apps
        .filter((app) => app.launcher.visible)
        .sort((a, b) => (a.launcher.order - b.launcher.order) || a.id.localeCompare(b.id));
    // Metadata the window manager reads (title, icon, accent and initial size), by app ID.
    const windows = Object.freeze(Object.fromEntries(apps
        .filter((app) => app.presentation === 'window')
        .map((app) => [app.id, Object.freeze({ title: app.name, icon: app.icon, accent: app.accent, window: app.window })])));

    return Object.freeze({
        errors: Object.freeze(errors),
        get: (id) => byId.get(String(id ?? '')) || null,
        list: ({ presentation = null } = {}) => apps.filter((app) => !presentation || app.presentation === presentation),
        launcher: () => launcher.slice(),
        windowTable: () => windows,
        /** Whether the app brings its own code (ui.module or ui.entry) instead of a shell view. */
        hasOwnCode: (id) => {
            const ui = byId.get(String(id ?? ''))?.ui;
            return !!(ui?.module || ui?.entry);
        },
        /** What a destination is. Empty means the desktop; unknown IDs are reported, not guessed. */
        resolve(target) {
            const id = String(target ?? '').trim().toLowerCase() || 'desktop';
            if (SHELL_DESTINATIONS.includes(id)) return { kind: 'internal', id };
            const app = byId.get(id);
            return app ? { kind: app.presentation, id, app } : { kind: 'unknown', id };
        },
    });
}
