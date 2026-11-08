/**
 * F17: the apps of this session — the bundled catalog (build) plus the apps installed from a file
 * through Iris Store (Core, /api/app-packages). app.js calls loadInstalledApps() once, before it
 * builds the desktop; the exports are live bindings, so every importer then sees the merged list.
 * Installing or uninstalling reloads the desktop, so the drawer, windows and widgets agree.
 * (No top-level await: the packaged web build also targets older engines.)
 *
 * An installed app runs like a bundled one, with the same access as Iris: the person accepted
 * that when installing it. Its manifest is validated again here and it may never take the ID of
 * a bundled app; its modules load only from Core's package route, never from a URL in the data.
 */
import {
    OS_APP_CATALOG as BUNDLED_CATALOG, OS_APP_MODULES as BUNDLED_MODULES, OS_APP_STYLES as BUNDLED_STYLES,
    OS_WIDGET_CATALOG as BUNDLED_WIDGETS, OS_WIDGET_MODULES as BUNDLED_WIDGET_MODULES, OS_WIDGET_STYLES as BUNDLED_WIDGET_STYLES,
} from './os_app_catalog.js?v=27';
import { validateAppManifest } from './os_app_manifest.js?v=5';
import { OS_ICON_PATHS } from './os_icons.js?v=3';
import { withAppCaller } from './os_app_client.js?v=2';

const PACKAGE_FILE = /^\/api\/app-packages\/files\/ins_[0-9a-f]{32}\/[a-z0-9][a-z0-9_.-]{0,63}(?:\/[a-z0-9][a-z0-9_.-]{0,63}){0,2}$/;

/** Bundled sources plus installed packages; pure, so the rules are testable without Core. */
export function mergeAppSources(bundled, installed, { importer = (url) => import(/* @vite-ignore */ url), warn = () => {} } = {}) {
    const catalog = [...bundled.catalog], modules = { ...bundled.modules }, styles = { ...bundled.styles };
    const widgets = { ...bundled.widgets }, widgetModules = { ...bundled.widgetModules }, widgetStyles = { ...bundled.widgetStyles };
    const taken = new Set(bundled.catalog.map((app) => app.id));
    const apps = new Map();
    for (const entry of Array.isArray(installed) ? installed : []) {
        const { ok, manifest, errors } = validateAppManifest(entry?.manifest, { iconNames: Object.keys(OS_ICON_PATHS) });
        const urls = [entry?.entry, entry?.style, ...Object.values(entry?.widgets || {}).flatMap((item) => [item?.entry, item?.style])]
            .filter((url) => url !== undefined);
        const problem = !ok ? errors.join('; ')
            : manifest.id !== entry.id ? 'el id no coincide'
                : taken.has(manifest.id) ? 'su id ya pertenece a otra app'
                    : !manifest.ui.entry ? 'no trae su propia interfaz'
                        : !urls.every((url) => typeof url === 'string' && PACKAGE_FILE.test(url)) ? 'una ruta de archivo no es del almacén de Iris'
                            : (manifest.widgets || []).some((widget) => !entry.widgets?.[widget.id]?.entry) ? 'falta un widget' : '';
        if (problem) { warn(`[IRIS OS] App instalada «${entry?.id}» no cargada: ${problem}`); continue; }
        taken.add(manifest.id);
        catalog.push(manifest);
        modules[manifest.id] = async () => {
            const module = await importer(entry.entry);
            return withAppCaller(module, 'createApp', manifest.id, entry.entry, { python: Boolean(manifest.extension?.python) });
        };
        if (entry.style) styles[manifest.id] = () => importer(entry.style);
        for (const widget of manifest.widgets || []) {
            widgets[widget.id] = { name: widget.name, app: manifest.id, size: widget.size, configVersion: widget.configVersion,
                ...(widget.preview ? { preview: widget.preview } : {}) };
            widgetModules[widget.id] = async () => {
                const module = await importer(entry.widgets[widget.id].entry);
                return withAppCaller(module, 'createWidget', manifest.id, entry.entry, { python: Boolean(manifest.extension?.python) });
            };
            if (entry.widgets[widget.id].style) widgetStyles[widget.id] = () => importer(entry.widgets[widget.id].style);
        }
        // `installation`: the entry URL names one installation, so an update is a different value.
        apps.set(manifest.id, Object.freeze({ version: manifest.version, installedAt: entry.installed_at, size: entry.size, installation: entry.entry,
            ...(manifest.extension ? { extension: manifest.extension } : {}) }));
    }
    return {
        catalog: Object.freeze(catalog), modules: Object.freeze(modules), styles: Object.freeze(styles),
        widgets: Object.freeze(widgets), widgetModules: Object.freeze(widgetModules), widgetStyles: Object.freeze(widgetStyles),
        installed: apps,
    };
}

/**
 * Core's list of installed apps. At start a failure means none (the bundled apps keep working);
 * with `strict`, a failure throws, so a refresh never mistakes it for «every app was removed».
 */
export async function readInstalledApps(fetchImpl = globalThis.fetch, { strict = false } = {}) {
    const fail = (cause) => {
        if (strict) throw new Error('Iris no pudo leer las apps instaladas. Inténtalo de nuevo.', { cause });
        return [];
    };
    if (typeof globalThis.location === 'undefined' || typeof fetchImpl !== 'function') return fail(null);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4000);
    let apps = null, cause = null;
    try {
        const response = await fetchImpl('/api/app-packages', { cache: 'no-store', signal: controller.signal });
        const data = response.ok ? await response.json() : null;
        if (Array.isArray(data?.apps)) apps = data.apps;
        else cause = new Error(response.ok ? 'respuesta sin lista de apps' : `HTTP ${response.status}`);
    } catch (error) {
        cause = error;
    } finally {
        clearTimeout(timer);
    }
    return apps ?? fail(cause);
}

/** What changed between two INSTALLED_APPS maps: new IDs, gone IDs, and IDs with another installation. */
export function compareInstalledApps(before, after) {
    const added = [], removed = [], updated = [];
    for (const [id, app] of after) {
        if (!before.has(id)) added.push(id);
        else if (before.get(id).installation !== app.installation) updated.push(id);
    }
    for (const id of before.keys()) if (!after.has(id)) removed.push(id);
    return { added, removed, updated };
}

const BUNDLED = Object.freeze({
    catalog: BUNDLED_CATALOG, modules: BUNDLED_MODULES, styles: BUNDLED_STYLES,
    widgets: BUNDLED_WIDGETS, widgetModules: BUNDLED_WIDGET_MODULES, widgetStyles: BUNDLED_WIDGET_STYLES,
});

export let OS_APP_CATALOG = BUNDLED.catalog;
export let OS_APP_MODULES = BUNDLED.modules;
export let OS_APP_STYLES = BUNDLED.styles;
export let OS_WIDGET_CATALOG = BUNDLED.widgets;
export let OS_WIDGET_MODULES = BUNDLED.widgetModules;
export let OS_WIDGET_STYLES = BUNDLED.widgetStyles;
/** Apps installed from a file: id → { version, installedAt, size, installation }. */
export let INSTALLED_APPS = new Map();

function publish(installed) {
    const sources = mergeAppSources(BUNDLED, installed, { warn: (text) => console.warn(text) });
    OS_APP_CATALOG = sources.catalog; OS_APP_MODULES = sources.modules; OS_APP_STYLES = sources.styles;
    OS_WIDGET_CATALOG = sources.widgets; OS_WIDGET_MODULES = sources.widgetModules; OS_WIDGET_STYLES = sources.widgetStyles;
    INSTALLED_APPS = sources.installed;
    return sources;
}

let loading = null;
let refreshing = Promise.resolve();

/** Read Core's installed apps once and merge them into the exports above; later calls reuse it. */
export function loadInstalledApps(fetchImpl = globalThis.fetch) {
    loading ||= readInstalledApps(fetchImpl).then(publish);
    return loading;
}

/**
 * F17: read again after Iris Store installed, updated or removed an app, without reloading the
 * page. Refreshes run one after another; a failure throws and leaves the current list as it was.
 * Returns the merged sources and `changes` ({ added, removed, updated } app IDs).
 */
export function reloadInstalledApps(fetchImpl = globalThis.fetch) {
    const run = async () => {
        const installed = await readInstalledApps(fetchImpl, { strict: true });
        const before = INSTALLED_APPS;
        const sources = publish(installed);
        loading = Promise.resolve(sources);
        return { ...sources, changes: compareInstalledApps(before, sources.installed) };
    };
    const next = refreshing.then(run, run);
    refreshing = next.catch(() => {});
    return next;
}
