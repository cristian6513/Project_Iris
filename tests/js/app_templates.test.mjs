// F09.3: the templates are complete apps. Copied as apps/<id>/ they pass the build, which
// exports only their web files to face/apps/<id>/ and adds them to the catalog; nothing in
// the shell changes for a new app.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { cp, link, mkdir, mkdtemp, readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { buildAppCatalog, syncExports } from '../../scripts/build_app_catalog.mjs';

const ROOT = fileURLToPath(new URL('../..', import.meta.url));

async function projectWithTemplates() {
    const root = await mkdtemp(join(tmpdir(), 'iris-templates-'));
    await mkdir(join(root, 'face/js'), { recursive: true });
    for (const file of ['os_app_manifest.js', 'os_app_extension.js', 'os_icons.js']) await cp(join(ROOT, 'face/js', file), join(root, 'face/js', file));
    await cp(join(ROOT, 'apps/_plantilla'), join(root, 'apps/mi-app'), { recursive: true });
    await cp(join(ROOT, 'apps/_plantilla-servicio'), join(root, 'apps/mi-servicio'), { recursive: true });
    return root;
}

test('copied as apps, both templates build; only their web files reach Face', async () => {
    const root = await projectWithTemplates();
    try {
        const { apps, exports, text } = await buildAppCatalog(root);
        assert.deepEqual(apps.map((app) => app.id), ['mi-app', 'mi-servicio']);
        assert.deepEqual([...exports.keys()].sort(), [
            'mi-app/main.js', 'mi-app/style.css.js', 'mi-servicio/main.js', 'mi-servicio/style.css.js',
        ], 'no backend, tests, README or app.json in Face');
        const entry = exports.get('mi-app/main.js').toString('utf8');
        assert.match(entry, /^\/\/ Generated from apps\/mi-app\/ui\/main\.js by scripts\/build_app_catalog\.mjs: edit the original\.\n/);
        assert.match(entry, /export function createApp\(/);
        const style = await import(`data:text/javascript;base64,${exports.get('mi-app/style.css.js').toString('base64')}`);
        assert.equal(style.default, await readFile(join(root, 'apps/mi-app/ui/style.css'), 'utf8'));
        assert.match(text, /"mi-app": \(\) => import\("\.\.\/apps\/mi-app\/main\.js\?v=[0-9a-f]{10}"\)/);
        assert.match(text, /export const OS_APP_STYLES = Object\.freeze\(\{\n {4}"mi-app": \(\) => import\("\.\.\/apps\/mi-app\/style\.css\.js\?v=[0-9a-f]{10}"\),/);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test('face/apps holds exactly the exports: stale files go, edits come back', async () => {
    const root = await projectWithTemplates();
    try {
        const { exports } = await buildAppCatalog(root);
        assert.equal((await syncExports(root, exports, { write: true })).length, 4);
        assert.deepEqual(await syncExports(root, exports, { write: false }), []);
        await writeFile(join(root, 'face/apps/mi-app/main.js'), 'edited by hand');
        await mkdir(join(root, 'face/apps/vieja'), { recursive: true });
        await writeFile(join(root, 'face/apps/vieja/main.js'), 'an app that is gone');
        assert.deepEqual((await syncExports(root, exports, { write: false })).sort(), [
            'cambia face/apps/mi-app/main.js', 'sobra face/apps/vieja/main.js',
        ]);
        await syncExports(root, exports, { write: true });
        assert.deepEqual(await syncExports(root, exports, { write: false }), []);
        await assert.rejects(readFile(join(root, 'face/apps/vieja/main.js')));
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test('the build refuses what an app folder must not publish', async () => {
    const root = await projectWithTemplates();
    try {
        const ui = join(root, 'apps/mi-app/ui');
        for (const [file, content, pattern] of [
            ['notas.txt', 'x', /solo se publican archivos \.js, \.css/],
            ['extra.css', 'a{}', /una sola hoja de estilos/],
            ['Mayus.js', '', /nombres en minúsculas/],
        ]) {
            await writeFile(join(ui, file), content);
            await assert.rejects(buildAppCatalog(root), pattern);
            await rm(join(ui, file));
        }
        await writeFile(join(ui, 'main.js'), 'export const nada = 1;\n');
        await assert.rejects(buildAppCatalog(root), /apps\/mi-app\/ui\/main\.js: no exporta createApp/);
        await rm(join(ui, 'style.css'));
        await assert.rejects(buildAppCatalog(root), /falta la hoja de estilos/);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test('the templates are not apps of the product: the shipped catalog leaves them out', async () => {
    const { apps } = await buildAppCatalog(ROOT);
    assert.ok(!apps.some((app) => ['mi-app', 'mi-servicio'].includes(app.id)));
    assert.deepEqual(apps.filter((app) => app.ui.entry).map((app) => app.id), ['calendar', 'conexiones', 'mail', 'store'],
                     'the product apps with their own folder');
});


test('two files that would publish the same name are refused, never one silently replacing the other (Codex S3)', async () => {
    const root = await projectWithTemplates();
    try {
        await writeFile(join(root, 'apps/mi-app/ui/style.css.js'), 'export default "otra hoja";\n');
        await assert.rejects(buildAppCatalog(root), /se publicaría como style\.css\.js, igual que apps\/mi-app\/ui\/style\.css/);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test('regenerating never writes through a hardlink in face/apps (Codex S4)', async () => {
    const root = await projectWithTemplates();
    try {
        const { exports } = await buildAppCatalog(root);
        await syncExports(root, exports, { write: true });
        const outside = join(root, 'fuera.js');
        await writeFile(outside, 'OUTSIDE SENTINEL');
        const target = join(root, 'face/apps/mi-app/main.js');
        await rm(target);
        await link(outside, target);
        assert.deepEqual(await syncExports(root, exports, { write: false }), ['es un alias face/apps/mi-app/main.js']);
        await syncExports(root, exports, { write: true });
        assert.equal(await readFile(outside, 'utf8'), 'OUTSIDE SENTINEL', 'the other name keeps its content');
        assert.deepEqual(await readFile(target), exports.get('mi-app/main.js'));
        assert.deepEqual(await syncExports(root, exports, { write: false }), []);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});


/** The classes a template draws: mount it on a stand-in DOM with data, and collect them. */
async function drawnClasses(entry) {
    const nodes = [];
    globalThis.document = {
        activeElement: null,
        createElement(tag) {
            const node = {
                tag, children: [], dataset: {}, attributes: {}, className: '', textContent: '', value: '',
                append(...more) { this.children.push(...more); },
                replaceChildren(...more) { this.children = more; },
                setAttribute(name, value) { this.attributes[name] = String(value); },
                addEventListener() {},
            };
            nodes.push(node);
            return node;
        },
    };
    try {
        const { createApp } = await import(pathToFileURL(entry).href);
        const items = [{ id: 'a', title: 'A', detail: 'x' }];
        const app = createApp({ id: 'x' }, { fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ items, source: 'ejemplo' }) }) });
        app.mount(globalThis.document.createElement('div'));
        await app.refresh({ opening: true });
        app.dispose();
        return new Set(nodes.map((node) => node.className).filter(Boolean));
    } finally {
        delete globalThis.document;
    }
}

/** An app's own tests, run by a child node. The parent runner's NODE_TEST_* variables would make
 * the child skip its files and still exit 0 (Codex V01), so they never reach it. */
function runAppTests(folder) {
    const env = Object.fromEntries(Object.entries(process.env).filter(([name]) => !name.startsWith('NODE_TEST')));
    const run = spawnSync(process.execPath, ['--test', '--test-reporter=spec', '--test-timeout=15000', join(folder, 'tests', 'main.test.mjs')], { encoding: 'utf8', env });
    const count = (label) => Number(run.stdout.match(new RegExp(`^ℹ ${label} (\\d+)$`, 'm'))?.[1] ?? NaN);
    return { status: run.status, pass: count('pass'), fail: count('fail'), output: `${run.stdout}${run.stderr}` };
}

const cssClasses = (css) => new Set([...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/\.([a-z][a-z0-9-]*)/g)].map((match) => match[1]));

test('renaming a template as its README says keeps its tests passing and its styles applied (Codex R14)', async () => {
    const root = await mkdtemp(join(tmpdir(), 'iris-rename-'));
    try {
        await mkdir(join(root, 'face/js'), { recursive: true });
        for (const file of ['os_app_manifest.js', 'os_app_extension.js', 'os_icons.js']) await cp(join(ROOT, 'face/js', file), join(root, 'face/js', file));
        for (const [template, prefix, id] of [['_plantilla', 'mi-app', 'notas-rapidas'], ['_plantilla-servicio', 'mi-servicio', 'lector-datos']]) {
            const folder = join(root, 'apps', id);
            await cp(join(ROOT, 'apps', template), folder, { recursive: true, filter: (source) => !source.includes('__pycache__') });
            // The name lives only in app.json and ui/: tests and backend never need renaming.
            for (const entry of await readdir(folder, { recursive: true, withFileTypes: true })) {
                if (!entry.isFile() || !/\.(json|js|mjs|css|py)$/.test(entry.name)) continue;
                const file = join(entry.parentPath ?? entry.path, entry.name);
                const text = await readFile(file, 'utf8');
                if (file === join(folder, 'app.json') || file.startsWith(join(folder, 'ui'))) await writeFile(file, text.replaceAll(prefix, id));
                else assert.ok(!text.includes(prefix), `${template}: ${entry.name} depends on the name «${prefix}»`);
            }

            const ran = runAppTests(folder);
            assert.ok(ran.status === 0 && ran.fail === 0 && ran.pass >= 2, `${id}: its own tests must run and pass after the rename\n${ran.output}`);
            // Control: a broken test in the copy makes the run fail, so a pass above means they ran.
            const tests = join(folder, 'tests', 'main.test.mjs');
            const valid = await readFile(tests, 'utf8');
            await writeFile(tests, `${valid}\ntest('roto a propósito', () => { throw new TypeError('roto'); });\n`);
            const broken = runAppTests(folder);
            assert.ok(broken.status !== 0 && broken.fail === 1, `${id}: a broken test must fail the run\n${broken.output}`);
            await writeFile(tests, valid);

            const drawn = await drawnClasses(join(folder, 'ui', 'main.js'));
            const styled = cssClasses(await readFile(join(folder, 'ui', 'style.css'), 'utf8'));
            assert.ok(styled.size > 3 && drawn.size > 3);
            assert.deepEqual([...styled].filter((name) => !drawn.has(name)), [], `${id}: style.css has rules the interface never draws`);
            assert.deepEqual([...drawn].filter((name) => name !== id && !name.startsWith(`${id}-`)), [], `${id}: classes outside the app's prefix`);
        }
        assert.deepEqual((await buildAppCatalog(root)).apps.map((app) => app.id), ['lector-datos', 'notas-rapidas']);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});
