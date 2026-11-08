#!/usr/bin/env node
/**
 * Build the IRIS OS app catalog from the reviewed sources in apps/<id>/ (F09.1–F09.3).
 *
 *   node scripts/build_app_catalog.mjs          write face/js/os_app_catalog.js and face/apps/
 *   node scripts/build_app_catalog.mjs --check  fail if either is out of date
 *
 * Every manifest is validated first; any invalid, incompatible or duplicated app fails the
 * build instead of being skipped. Folders starting with «_» or «.» (the templates) are not
 * apps, and plain files beside the apps are documentation.
 *
 * Only public files reach Face, and only by this script:
 * - `ui.module` (F09.2): a reviewed file of face/js exporting createApp gets a static import.
 * - `ui.entry` (F09.3): the app's own apps/<id>/ui/ folder is exported to face/apps/<id>/ (web
 *   files only); its stylesheet becomes a small module so it loads the same way unbundled and
 *   in the Vite package. Python, skills and tests of an app never leave its folder.
 * Imports carry the digest of what they load, so a changed file is never served from cache.
 * face/apps/ holds nothing but this script's output.
 */
import { createHash } from 'node:crypto';
import { lstat, mkdir, readFile, readdir, rename, rm, rmdir, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const PROJECT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const CATALOG_PATH = 'face/js/os_app_catalog.js';
const EXPORT_ROOT = 'face/apps';
const WIDGET_CATALOG_PATH = 'core/widget_catalog.py';
const MAX_MANIFEST_BYTES = 16 * 1024;
const MAX_UI_FILE_BYTES = 512 * 1024;
const MAX_UI_BYTES = 2 * 1024 * 1024;
const UI_EXTENSIONS = new Set(['js', 'css', 'svg', 'png', 'webp', 'json']);
const UI_SEGMENT = /^[a-z0-9][a-z0-9_.-]{0,63}$/;
const createAppExport = /^export (?:async )?function createApp\(/m;

const digest = (bytes) => createHash('sha256').update(bytes).digest('hex').slice(0, 10);

async function plainDirectory(path) {
    const info = await lstat(path);
    if (info.isSymbolicLink() || !info.isDirectory()) throw new Error(`${path}: debe ser una carpeta real`);
}

/** Every file under `root`, as posix paths relative to it; links are refused. */
async function listFiles(root, prefix = '') {
    const files = [];
    for (const entry of (await readdir(join(root, prefix), { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
        const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
        if (entry.isSymbolicLink()) throw new Error(`${rel}: no se admiten enlaces`);
        if (entry.isDirectory()) files.push(...await listFiles(root, rel));
        else if (entry.isFile()) files.push(rel);
        else throw new Error(`${rel}: tipo de archivo no admitido`);
    }
    return files;
}

export async function readAppSources(projectRoot = PROJECT_ROOT) {
    const root = join(projectRoot, 'apps');
    await plainDirectory(root);
    const entries = (await readdir(root, { withFileTypes: true }))
        .filter((entry) => !entry.name.startsWith('_') && !entry.name.startsWith('.'));
    const linked = entries.find((entry) => entry.isSymbolicLink());
    if (linked) throw new Error(`apps/${linked.name}: no se admiten enlaces`);
    // Plain files beside the apps (README.md) are documentation, not apps.
    const folders = entries.filter((entry) => entry.isDirectory()).map((entry) => entry.name).sort();
    const sources = [];
    for (const folder of folders) {
        const path = join(root, folder);
        await plainDirectory(path);
        const file = join(path, 'app.json');
        const info = await lstat(file).catch(() => null);
        if (!info?.isFile() || info.isSymbolicLink()) throw new Error(`apps/${folder}: falta app.json`);
        if (info.size > MAX_MANIFEST_BYTES) throw new Error(`apps/${folder}/app.json: supera ${MAX_MANIFEST_BYTES} bytes`);
        let manifest;
        try {
            manifest = JSON.parse(await readFile(file, 'utf8'));
        } catch (error) {
            throw new Error(`apps/${folder}/app.json: JSON inválido (${error.message})`);
        }
        sources.push({ folder, manifest });
    }
    return sources;
}

/** The web files of an app with `ui.entry`, ready for face/apps/<id>/. */
async function exportAppUi(projectRoot, app, problems) {
    const where = `apps/${app.id}/ui`;
    const root = join(projectRoot, 'apps', app.id, 'ui');
    const info = await lstat(root).catch(() => null);
    if (!info?.isDirectory() || info.isSymbolicLink()) {
        problems.push(`${where}: falta la carpeta de la interfaz`);
        return null;
    }
    let files;
    try {
        files = await listFiles(root);
    } catch (error) {
        problems.push(`${where}/${error.message}`);
        return null;
    }
    const out = new Map();
    const from = new Map(); // output name → source file, to catch two files publishing the same name
    const put = (key, bytes, rel) => {
        if (out.has(key)) {
            problems.push(`${where}/${rel}: se publicaría como ${key}, igual que ${where}/${from.get(key)}`);
            return;
        }
        out.set(key, bytes);
        from.set(key, rel);
    };
    let total = 0;
    for (const rel of files) {
        const segments = rel.split('/');
        const extension = rel.split('.').at(-1);
        if (segments.length > 3 || !segments.every((segment) => UI_SEGMENT.test(segment))) {
            problems.push(`${where}/${rel}: nombres en minúsculas, sin espacios, hasta dos subcarpetas`);
            continue;
        }
        if (!UI_EXTENSIONS.has(extension)) {
            problems.push(`${where}/${rel}: solo se publican archivos .js, .css, .svg, .png, .webp y .json`);
            continue;
        }
        if (extension === 'css' && rel !== app.ui.style && !app.widgets?.some((widget) => widget.style === rel)) {
            problems.push(`${where}/${rel}: una app tiene una sola hoja de estilos, la de ui.style`);
            continue;
        }
        const bytes = await readFile(join(root, rel));
        total += bytes.length;
        if (bytes.length > MAX_UI_FILE_BYTES) problems.push(`${where}/${rel}: supera ${MAX_UI_FILE_BYTES / 1024} KB`);
        const header = `// Generated from ${where}/${rel} by scripts/build_app_catalog.mjs: edit the original.\n`;
        if (extension === 'js') put(rel, Buffer.from(header + bytes.toString('utf8')), rel);
        else if (extension === 'css') put(`${rel}.js`, Buffer.from(`${header}export default ${JSON.stringify(bytes.toString('utf8'))};\n`), rel);
        else put(rel, bytes, rel);
    }
    if (total > MAX_UI_BYTES) problems.push(`${where}: supera ${MAX_UI_BYTES / 1024 / 1024} MB en total`);
    if (app.ui.entry) {
        const entry = out.get(app.ui.entry);
        if (!entry) problems.push(`${where}/${app.ui.entry}: falta el módulo de entrada (ui.entry)`);
        else if (!createAppExport.test(entry.toString('utf8'))) problems.push(`${where}/${app.ui.entry}: no exporta createApp()`);
    }
    if (app.ui.style && !out.has(`${app.ui.style}.js`)) problems.push(`${where}/${app.ui.style}: falta la hoja de estilos (ui.style)`);
    for (const widget of app.widgets || []) {
        const source = out.get(widget.entry);
        if (!source || !/^export (?:async )?function createWidget\(/m.test(source.toString('utf8'))) problems.push(`${where}/${widget.entry}: falta el módulo o no exporta createWidget()`);
        if (widget.style && !out.has(`${widget.style}.js`)) problems.push(`${where}/${widget.style}: falta el estilo del widget`);
    }
    return out;
}

export async function buildAppCatalog(projectRoot = PROJECT_ROOT) {
    const face = (file) => import(pathToFileURL(join(projectRoot, 'face/js', file)).href);
    const { validateAppCatalog } = await face('os_app_manifest.js');
    const { OS_ICON_PATHS } = await face('os_icons.js');
    const sources = await readAppSources(projectRoot);
    const problems = [];
    for (const { folder, manifest } of sources) {
        if (manifest?.id !== folder) problems.push(`apps/${folder}: el id «${manifest?.id}» debe coincidir con la carpeta`);
        if (manifest?.extension) problems.push(`apps/${folder}: extension SDK 2 es un paquete externo; valídalo con app_package_tool.py e instálalo desde Iris Store, fuera del catálogo incluido`);
    }
    const { apps, errors } = validateAppCatalog(sources.map((source) => source.manifest), { iconNames: Object.keys(OS_ICON_PATHS) });
    for (const error of errors) problems.push(`apps/${sources[error.index].folder}/app.json: ${error.errors.join('; ')}`);

    const modules = [];
    const styles = [];
    const widgetModules = [], widgetStyles = [], widgetCatalog = {};
    const exports = new Map(); // path under face/apps → bytes
    const line = (id, path) => `    ${JSON.stringify(id)}: () => import(${JSON.stringify(path)}),`;
    for (const app of apps) {
        if (app.ui.module) {
            // F09.2: a reviewed file of face/js that exports createApp.
            const file = join(projectRoot, 'face/js', app.ui.module);
            const info = await lstat(file).catch(() => null);
            if (!info?.isFile() || info.isSymbolicLink()) {
                problems.push(`apps/${app.id}/app.json: ui.module «${app.ui.module}» no existe en face/js`);
                continue;
            }
            const source = await readFile(file);
            if (!createAppExport.test(source.toString('utf8'))) {
                problems.push(`apps/${app.id}/app.json: face/js/${app.ui.module} no exporta createApp()`);
                continue;
            }
            modules.push(line(app.id, `./${app.ui.module}?v=${digest(source)}`));
        }
        if (app.ui.entry || app.widgets?.length) {
            // A widget owns ui/ files even when its app still uses a reviewed shell adapter.
            const files = await exportAppUi(projectRoot, app, problems);
            if (!files) continue;
            for (const [rel, bytes] of files) exports.set(`${app.id}/${rel}`, bytes);
            const entry = files.get(app.ui.entry);
            if (entry) modules.push(line(app.id, `../apps/${app.id}/${app.ui.entry}?v=${digest(entry)}`));
            const style = app.ui.style && files.get(`${app.ui.style}.js`);
            if (style) styles.push(line(app.id, `../apps/${app.id}/${app.ui.style}.js?v=${digest(style)}`));
            for (const widget of app.widgets || []) {
            widgetCatalog[widget.id] = { name: widget.name, app: app.id, size: widget.size, configVersion: widget.configVersion,
                ...(widget.preview ? { preview: widget.preview } : {}) };
                const widgetEntry = files.get(widget.entry);
                if (widgetEntry) widgetModules.push(line(widget.id, `../apps/${app.id}/${widget.entry}?v=${digest(widgetEntry)}`));
                const widgetStyle = widget.style && files.get(`${widget.style}.js`);
                if (widgetStyle) widgetStyles.push(line(widget.id, `../apps/${app.id}/${widget.style}.js?v=${digest(widgetStyle)}`));
            }
        }
    }
    if (problems.length) throw new Error(`Catálogo de apps inválido:\n- ${problems.join('\n- ')}`);
    const table = (rows) => `Object.freeze({${rows.length ? `\n${rows.join('\n')}\n` : ''}})`;
    const text = [
        '// Generated by scripts/build_app_catalog.mjs from apps/<id>/app.json. Do not edit by hand:',
        '// change the app.json and run `node scripts/build_app_catalog.mjs`.',
        `export const OS_APP_CATALOG = ${JSON.stringify(apps, null, 4)};`,
        '',
        '// Modules of the apps with their own code (ui.module or ui.entry), as static imports chosen by the build.',
        `export const OS_APP_MODULES = ${table(modules)};`,
        '',
        '// Stylesheets of the apps with their own folder (ui.style), as modules exporting the CSS text.',
        `export const OS_APP_STYLES = ${table(styles)};`,
        '',
        '// F12: widget metadata and local imports derived from the same reviewed app.json.',
        `export const OS_WIDGET_CATALOG = Object.freeze(${JSON.stringify(widgetCatalog, null, 4)});`,
        `export const OS_WIDGET_MODULES = ${table(widgetModules)};`,
        `export const OS_WIDGET_STYLES = ${table(widgetStyles)};`,
        '',
    ].join('\n');
    const widgetCatalogText = '# Generated by scripts/build_app_catalog.mjs from apps/*/app.json.\n'
        + 'import json\n\nWIDGETS = json.loads(' + JSON.stringify(JSON.stringify(widgetCatalog)) + ')\n'
        + '\n# IDs of bundled reviewed apps; home preferences do not install apps.\nAPP_IDS = frozenset('
        + JSON.stringify(apps.map((app) => app.id)) + ')\n'
        + '\n# F17: icons an installed app may name (face/js/os_icons.js), for Core\'s package check.\nICON_NAMES = frozenset('
        + JSON.stringify(Object.keys(OS_ICON_PATHS)) + ')\n';
    return { text, apps, exports, widgetCatalogText };
}

/** Differences between face/apps/ and the exports; with `write`, make them equal. */
async function syncExports(projectRoot, exports, { write }) {
    const root = join(projectRoot, EXPORT_ROOT);
    const info = await lstat(root).catch(() => null);
    if (info && (info.isSymbolicLink() || !info.isDirectory())) throw new Error(`${EXPORT_ROOT}: debe ser una carpeta real`);
    const current = info ? await listFiles(root) : [];
    const differences = [];
    for (const rel of current) {
        if (exports.has(rel)) continue;
        differences.push(`sobra ${EXPORT_ROOT}/${rel}`);
        if (write) await rm(join(root, rel));
    }
    for (const [rel, bytes] of exports) {
        const target = join(root, rel);
        const info = await lstat(target).catch(() => null);
        // A file with another name elsewhere (hardlink) is never written through (Codex S4).
        const alias = info && (!info.isFile() || info.nlink > 1);
        const existing = info && !alias ? await readFile(target) : null;
        if (existing && existing.equals(bytes)) continue;
        differences.push(`${alias ? 'es un alias' : existing ? 'cambia' : 'falta'} ${EXPORT_ROOT}/${rel}`);
        if (write) {
            await mkdir(dirname(target), { recursive: true });
            // A new file renamed into place replaces the name only, never another file's content.
            const fresh = `${target}.${process.pid}.tmp`;
            await writeFile(fresh, bytes, { flag: 'wx' });
            await rename(fresh, target);
        }
    }
    if (write && info) {
        // Folders left empty by removed files go too (deepest first).
        const folders = [...new Set(current.flatMap((rel) => rel.split('/').slice(0, -1).map((_, i, parts) => parts.slice(0, i + 1).join('/'))))]
            .sort((a, b) => b.length - a.length);
        for (const folder of folders) await rmdir(join(root, folder)).catch(() => {});
        if (!exports.size) await rmdir(root).catch(() => {});
    }
    return differences;
}

async function main() {
    const check = process.argv.includes('--check');
    const { text, apps, exports, widgetCatalogText } = await buildAppCatalog();
    const target = join(PROJECT_ROOT, CATALOG_PATH);
    const current = await readFile(target, 'utf8').catch(() => null);
    const widgetTarget = join(PROJECT_ROOT, WIDGET_CATALOG_PATH);
    const widgetCurrent = await readFile(widgetTarget, 'utf8').catch(() => null);
    const differences = await syncExports(PROJECT_ROOT, exports, { write: !check });
    if (check) {
        if (current !== text) differences.unshift(`${CATALOG_PATH} no coincide con apps/*/app.json`);
        if (widgetCurrent !== widgetCatalogText) differences.unshift(`${WIDGET_CATALOG_PATH} no coincide con apps/*/app.json`);
        if (differences.length) {
            console.error(`${differences.join('\n')}\nEjecuta: node scripts/build_app_catalog.mjs`);
            process.exitCode = 1;
            return;
        }
        console.log(`${CATALOG_PATH} y ${EXPORT_ROOT}/ al día (${apps.length} apps).`);
        return;
    }
    if (current !== text) await writeFile(target, text, 'utf8');
    if (widgetCurrent !== widgetCatalogText) {
        const fresh = `${widgetTarget}.${process.pid}.tmp`;
        await writeFile(fresh, widgetCatalogText, { flag: 'wx' }); await rename(fresh, widgetTarget);
    }
    console.log(`${CATALOG_PATH}: ${apps.length} apps; ${EXPORT_ROOT}/: ${exports.size} archivos.`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    main().catch((error) => {
        console.error(error.message || error);
        process.exitCode = 1;
    });
}

export { syncExports };
