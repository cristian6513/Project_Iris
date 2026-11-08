/**
 * IRIS OS app manifests (F09.1): validation of each `apps/<id>/app.json`.
 *
 * A manifest is data only. It names an app, its icon and how the shell presents it. It never
 * carries code, URLs or paths that the product imports, and declaring a capability grants
 * nothing: Core decides. The build validates every manifest before it generates the catalog
 * (scripts/build_app_catalog.mjs); the shell validates again, so one bad entry stays local and
 * the other apps keep working.
 */

import { validateAppExtension } from './os_app_extension.js?v=2';
export const APP_MANIFEST_SCHEMA = 'iris.app-manifest/v1';
export const APP_SDK_VERSION = 2;
export const APP_ACCENTS = Object.freeze(['cyan', 'magenta', 'amber']);
export const APP_PRESENTATIONS = Object.freeze(['window', 'tab']);
// The shell's own destinations. They are never app IDs.
export const SHELL_DESTINATIONS = Object.freeze(['desktop', 'apps']);
// Views built into the shell answer for these apps until each one moves to its own module.
export const APP_UI_ADAPTERS = Object.freeze(['legacy']);
// An app with its own module names a reviewed file of face/js; the build turns it into a static
// import (os_app_catalog.js). Only a bare file name: no folders, no URL.
export const APP_UI_MODULE = /^[a-z][a-z0-9_]{0,63}\.js$/;
// An app whose interface lives in its own folder (apps/<id>/ui/) names its entry module and,
// optionally, one stylesheet there; the build exports that folder to face/apps/<id>/ (F09.3).
export const APP_UI_ENTRY = /^[a-z][a-z0-9_-]{0,63}\.js$/;
export const APP_UI_STYLE = /^[a-z][a-z0-9_-]{0,63}\.css$/;

const ID = /^[a-z][a-z0-9-]{1,31}$/;
const VERSION = /^\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[0-9A-Za-z.-]{1,32})?$/;
const NEED = /^[a-z][a-z0-9_.-]{1,63}$/;
const CONTROL = /[\u0000-\u001f\u007f]/;
const WINDOW_LIMITS = Object.freeze({ width: [360, 1600], height: [300, 1200] });

const FIELDS = Object.freeze({
    manifest: ['schema', 'sdkVersion', 'id', 'name', 'version', 'description', 'icon', 'accent',
        'presentation', 'window', 'launcher', 'ui', 'requestedCapabilities', 'services', 'widgets', 'extension'],
    window: ['width', 'height'],
    launcher: ['visible', 'order', 'label'],
    ui: ['adapter', 'module', 'entry', 'style'],
    widget: ['id', 'name', 'entry', 'style', 'size', 'configVersion', 'preview'],
});

const isObject = (value) => !!value && typeof value === 'object' && !Array.isArray(value);

function unknownFields(value, allowed, path, errors) {
    for (const key of Object.keys(value)) {
        if (!allowed.includes(key)) errors.push(`${path}${key}: campo desconocido`);
    }
}

function text(value, path, max, errors, { required = true } = {}) {
    if (value === undefined && !required) return undefined;
    if (typeof value !== 'string' || !value.trim()) {
        errors.push(`${path}: debe ser un texto no vacío`);
        return undefined;
    }
    const clean = value.trim();
    if (clean.length > max) errors.push(`${path}: máximo ${max} caracteres`);
    if (CONTROL.test(clean)) errors.push(`${path}: no admite caracteres de control`);
    return clean;
}

function integer(value, path, [min, max], errors) {
    if (!Number.isInteger(value) || value < min || value > max) {
        errors.push(`${path}: debe ser un entero entre ${min} y ${max}`);
        return undefined;
    }
    return value;
}

function needs(value, path, errors) {
    if (value === undefined) return [];
    if (!Array.isArray(value) || value.length > 16) {
        errors.push(`${path}: debe ser una lista de hasta 16 identificadores`);
        return [];
    }
    const seen = new Set();
    for (const item of value) {
        if (typeof item !== 'string' || !NEED.test(item)) errors.push(`${path}: identificador inválido`);
        else if (seen.has(item)) errors.push(`${path}: «${item}» repetido`);
        seen.add(item);
    }
    return [...seen];
}

// A passive illustration drawn by the shell, never another executable entry point.
function widgetPreview(raw, errors) {
    if (raw === undefined) return undefined;
    if (!isObject(raw)) { errors.push('widgets.preview: debe ser un objeto'); return undefined; }
    unknownFields(raw, ['layout', 'title', 'subtitle', 'items'], 'widgets.preview.', errors);
    if (!['agenda', 'player', 'summary'].includes(raw.layout)) errors.push('widgets.preview.layout: agenda, player o summary');
    const title = text(raw.title, 'widgets.preview.title', 60, errors);
    const subtitle = text(raw.subtitle, 'widgets.preview.subtitle', 80, errors, { required: false });
    const items = [];
    if (!Array.isArray(raw.items) || raw.items.length > 3) errors.push('widgets.preview.items: lista de hasta tres ejemplos');
    else for (const item of raw.items) {
        if (!isObject(item)) { errors.push('widgets.preview.items: cada ejemplo debe ser un objeto'); continue; }
        unknownFields(item, ['label', 'detail'], 'widgets.preview.items.', errors);
        const label = text(item.label, 'widgets.preview.items.label', 60, errors);
        const detail = text(item.detail, 'widgets.preview.items.detail', 60, errors, { required: false });
        items.push(Object.freeze({ label, ...(detail ? { detail } : {}) }));
    }
    return Object.freeze({ layout: raw.layout, title, ...(subtitle ? { subtitle } : {}), items: Object.freeze(items) });
}

/**
 * Validate one manifest. Returns `{ ok, manifest, errors, incompatible }`; `manifest` is the
 * normalized, frozen form (defaults filled in) when `ok`. `iconNames`, when given, lists the
 * icons of os_icons.js the manifest may use.
 */
export function validateAppManifest(raw, { iconNames = null } = {}) {
    const errors = [];
    if (!isObject(raw)) return { ok: false, manifest: null, errors: ['El manifiesto debe ser un objeto JSON.'], incompatible: false };
    if (raw.schema !== APP_MANIFEST_SCHEMA) errors.push(`schema: debe ser «${APP_MANIFEST_SCHEMA}»`);
    // A newer SDK is not guessed from the app's name or version: it simply does not load here.
    if (!Number.isInteger(raw.sdkVersion) || raw.sdkVersion < 1) errors.push('sdkVersion: debe ser un entero positivo');
    const incompatible = Number.isInteger(raw.sdkVersion) && raw.sdkVersion > APP_SDK_VERSION;
    if (incompatible) errors.push(`sdkVersion: ${raw.sdkVersion} no es compatible con esta versión de IRIS OS (SDK ${APP_SDK_VERSION})`);
    unknownFields(raw, FIELDS.manifest, '', errors);

    const id = typeof raw.id === 'string' && ID.test(raw.id) ? raw.id : undefined;
    if (!id) errors.push('id: minúsculas, números y guiones; 2 a 32 caracteres, empezando por letra');
    else if (SHELL_DESTINATIONS.includes(id)) errors.push(`id: «${id}» está reservado por el escritorio`);
    const name = text(raw.name, 'name', 40, errors);
    const version = typeof raw.version === 'string' && VERSION.test(raw.version) ? raw.version : undefined;
    if (!version) errors.push('version: formato «mayor.menor.parche»');
    const description = text(raw.description, 'description', 80, errors);

    const icon = typeof raw.icon === 'string' ? raw.icon : undefined;
    if (!icon) errors.push('icon: nombre de un icono de os_icons.js');
    else if (iconNames && !iconNames.includes(icon)) errors.push(`icon: «${icon}» no existe en os_icons.js`);
    const accent = raw.accent ?? 'cyan';
    if (!APP_ACCENTS.includes(accent)) errors.push(`accent: uno de ${APP_ACCENTS.join(', ')}`);
    const presentation = raw.presentation;
    if (!APP_PRESENTATIONS.includes(presentation)) errors.push(`presentation: uno de ${APP_PRESENTATIONS.join(', ')}`);

    let windowSize;
    if (presentation === 'window') {
        if (!isObject(raw.window)) errors.push('window: obligatorio para presentation «window»');
        else {
            unknownFields(raw.window, FIELDS.window, 'window.', errors);
            windowSize = {
                width: integer(raw.window.width, 'window.width', WINDOW_LIMITS.width, errors),
                height: integer(raw.window.height, 'window.height', WINDOW_LIMITS.height, errors),
            };
        }
    } else if (raw.window !== undefined) {
        errors.push('window: solo para presentation «window»');
    }

    const launcherRaw = raw.launcher ?? {};
    let launcher;
    if (!isObject(launcherRaw)) errors.push('launcher: debe ser un objeto');
    else {
        unknownFields(launcherRaw, FIELDS.launcher, 'launcher.', errors);
        const visible = launcherRaw.visible ?? true;
        if (typeof visible !== 'boolean') errors.push('launcher.visible: true o false');
        launcher = {
            visible,
            order: integer(launcherRaw.order ?? 1000, 'launcher.order', [0, 9999], errors),
            label: text(launcherRaw.label, 'launcher.label', 32, errors, { required: false }),
        };
    }

    let ui;
    if (!isObject(raw.ui)) errors.push('ui: obligatorio');
    else {
        unknownFields(raw.ui, FIELDS.ui, 'ui.', errors);
        const kinds = ['adapter', 'module', 'entry'].filter((key) => raw.ui[key] !== undefined);
        if (kinds.length !== 1) errors.push('ui: indica solo uno de «adapter», «module» o «entry»');
        else if (kinds[0] === 'adapter') {
            if (!APP_UI_ADAPTERS.includes(raw.ui.adapter)) errors.push('ui.adapter: esta versión solo admite «legacy»');
            ui = { adapter: raw.ui.adapter };
        } else if (kinds[0] === 'module') {
            if (typeof raw.ui.module !== 'string' || !APP_UI_MODULE.test(raw.ui.module)) {
                errors.push('ui.module: nombre de un archivo .js de face/js, sin carpetas');
            }
            ui = { module: raw.ui.module };
        } else {
            if (typeof raw.ui.entry !== 'string' || !APP_UI_ENTRY.test(raw.ui.entry)) {
                errors.push('ui.entry: nombre de un archivo .js de la carpeta ui/ de la app, sin subcarpetas');
            }
            ui = { entry: raw.ui.entry };
        }
        if (raw.ui.style !== undefined) {
            if (!ui?.entry) errors.push('ui.style: solo para apps con «entry»');
            else if (typeof raw.ui.style !== 'string' || !APP_UI_STYLE.test(raw.ui.style)) {
                errors.push('ui.style: nombre de un archivo .css de la carpeta ui/ de la app');
            } else ui.style = raw.ui.style;
        }
    }

    const requestedCapabilities = needs(raw.requestedCapabilities, 'requestedCapabilities', errors);
    const services = needs(raw.services, 'services', errors);
    const widgets = [];
    if (raw.widgets !== undefined) {
        if (!Array.isArray(raw.widgets) || raw.widgets.length > 4) errors.push('widgets: lista de hasta cuatro widgets');
        else {
            const seen = new Set();
            for (const widget of raw.widgets) {
                if (!isObject(widget)) { errors.push('widgets: cada widget debe ser un objeto'); continue; }
                unknownFields(widget, FIELDS.widget, 'widgets.', errors);
                const prefix = `${id}.`;
                if (typeof widget.id !== 'string' || !widget.id.startsWith(prefix)
                    || !/^[a-z][a-z0-9-]{0,31}$/.test(widget.id.slice(prefix.length))) errors.push('widgets.id: id de la app seguido de un nombre, por ejemplo calendar.month');
                if (seen.has(widget.id)) errors.push('widgets.id: widget repetido');
                seen.add(widget.id);
                const widgetName = text(widget.name, 'widgets.name', 40, errors);
                if (typeof widget.entry !== 'string' || !APP_UI_ENTRY.test(widget.entry)) errors.push('widgets.entry: archivo .js de ui/, sin carpetas ni URL');
                if (widget.style !== undefined && (typeof widget.style !== 'string' || !APP_UI_STYLE.test(widget.style))) errors.push('widgets.style: archivo .css de ui/, sin carpetas ni URL');
                if (widget.size !== 'compact') errors.push('widgets.size: esta versión admite compact');
                if (widget.configVersion !== 1) errors.push('widgets.configVersion: esta versión admite 1');
                const preview = widgetPreview(widget.preview, errors);
                widgets.push(Object.freeze({ id: widget.id, name: widgetName, entry: widget.entry,
                    ...(widget.style ? { style: widget.style } : {}), size: widget.size, configVersion: widget.configVersion,
                    ...(preview ? { preview } : {}) }));
            }
        }
    }
    const extension = Object.hasOwn(raw, 'extension') ? validateAppExtension(raw.extension, raw.sdkVersion, errors) : undefined;
    if (errors.length) return { ok: false, manifest: null, errors, incompatible };

    const manifest = {
        schema: APP_MANIFEST_SCHEMA, sdkVersion: raw.sdkVersion, id, name, version, description, icon, accent,
        presentation, ...(windowSize ? { window: Object.freeze(windowSize) } : {}),
        launcher: Object.freeze(launcher.label ? launcher : { visible: launcher.visible, order: launcher.order }),
        ui: Object.freeze(ui),
        requestedCapabilities: Object.freeze(requestedCapabilities), services: Object.freeze(services),
        ...(widgets.length ? { widgets: Object.freeze(widgets) } : {}),
        ...(extension ? { extension: Object.freeze(extension) } : {}),
    };
    return { ok: true, manifest: Object.freeze(manifest), errors: [], incompatible: false };
}

/**
 * Validate a whole catalog. Invalid or incompatible entries are left out; an ID used twice
 * rejects every entry with it, rather than silently picking one. The rest stays usable.
 */
export function validateAppCatalog(entries, options = {}) {
    const results = (Array.isArray(entries) ? entries : []).map((raw, index) => ({ index, raw, ...validateAppManifest(raw, options) }));
    const counts = new Map();
    for (const { manifest } of results) if (manifest) counts.set(manifest.id, (counts.get(manifest.id) || 0) + 1);
    const apps = [];
    const errors = [];
    for (const result of results) {
        const id = result.manifest?.id || (typeof result.raw?.id === 'string' ? result.raw.id : '');
        if (!result.ok) errors.push({ index: result.index, id, incompatible: result.incompatible, errors: result.errors });
        else if (counts.get(id) > 1) errors.push({ index: result.index, id, incompatible: false, errors: [`id: «${id}» está repetido en el catálogo`] });
        else apps.push(result.manifest);
    }
    return { apps, errors };
}
