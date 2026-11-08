import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, mkdtemp, mkdir, cp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { validateAppManifest } from '../../face/js/os_app_manifest.js';
import { buildAppCatalog } from '../../scripts/build_app_catalog.mjs';
import { OS_WIDGET_CATALOG, OS_WIDGET_MODULES } from '../../face/js/os_app_catalog.js';

const root = new URL('../..', import.meta.url);
const base = JSON.parse(await readFile(new URL('apps/calendar/app.json', root), 'utf8'));

test('widgets are optional; only a local UI folder can supply them', () => {
    const plain = structuredClone(base); delete plain.widgets;
    assert.equal(validateAppManifest(plain).ok, true);
    assert.equal(validateAppManifest(base).ok, true);
    assert.equal(validateAppManifest({ ...base, ui: { adapter: 'legacy' } }).ok, true);
});

test('passive previews are optional, bounded data and never paths, markup entries or executable UI', () => {
    const widget = structuredClone(base.widgets[0]); delete widget.preview;
    assert.equal(validateAppManifest({ ...base, widgets: [widget] }).ok, true);
    for (const preview of [null, 'image.png', { layout: 'iframe', title: 'x', items: [] },
        { layout: 'agenda', title: 'x', items: [], url: 'https://outside.invalid' },
        { layout: 'agenda', title: 'x', items: [], entry: 'preview.js' },
        { layout: 'summary', title: 'x'.repeat(61), items: [] },
        { layout: 'summary', title: 'x', items: Array(4).fill({ label: 'x' }) },
        { layout: 'summary', title: 'x', items: [{ label: 'x', html: '<img>' }] },
        { layout: 'summary', title: 'x', items: [{ label: 'bad\u0000text' }] }]) {
        assert.equal(validateAppManifest({ ...base, widgets: [{ ...widget, preview }] }).ok, false, JSON.stringify(preview));
    }
    const result = validateAppManifest(base); assert.equal(result.ok, true);
    assert.deepEqual(result.manifest.widgets[0].preview, base.widgets[0].preview);
    assert.equal(Object.isFrozen(result.manifest.widgets[0].preview.items[0]), true);
});

test('widget metadata rejects external paths, duplicate ids, newer config and invented permissions', () => {
    for (const changes of [{ entry: '../x.js' }, { entry: 'https://bad.invalid/a.js' }, { style: '/outside.css' },
        { id: 'another.month' }, { size: 'fullscreen' }, { configVersion: 2 }, { configVersion: true },
        { grants: ['calendar.read'] }, { name: 'Bad\u0000name' }]) {
        assert.equal(validateAppManifest({ ...base, widgets: [{ ...base.widgets[0], ...changes }] }).ok, false, JSON.stringify(changes));
    }
    assert.equal(validateAppManifest({ ...base, widgets: [base.widgets[0], base.widgets[0]] }).ok, false);
});

test('both clients and Python receive widget metadata from app.json, and imports are build selected', async () => {
    const result = await buildAppCatalog();
    assert.deepEqual(Object.keys(OS_WIDGET_CATALOG), ['calendar.month', 'player.transport']);
    assert.equal(OS_WIDGET_CATALOG['calendar.month'].app, 'calendar');
    assert.equal(typeof OS_WIDGET_MODULES['calendar.month'], 'function');
    assert.equal(await readFile(new URL('core/widget_catalog.py', root), 'utf8'), result.widgetCatalogText);
    assert.ok(result.exports.has('calendar/widget.js'));
    assert.ok(result.exports.has('calendar/widget.css.js'));
    assert.ok(result.exports.has('player/widget.js'));
    assert.ok(result.exports.has('player/widget.css.js'));
    assert.equal(typeof OS_WIDGET_MODULES['player.transport'], 'function');
    assert.ok(!result.text.includes('apps/player/undefined'), 'a legacy app gets no invented createApp entry');
});

test('a widget with no factory or stylesheet stops the build before publishing', async () => {
    const work = await mkdtemp(join(tmpdir(), 'iris-widget-manifest-'));
    try {
        await mkdir(join(work, 'face/js'), { recursive: true });
        await mkdir(join(work, 'apps/calendar'), { recursive: true });
        for (const file of ['os_app_manifest.js', 'os_app_extension.js', 'os_icons.js']) await cp(new URL(`face/js/${file}`, root), join(work, 'face/js', file));
        await cp(new URL('apps/calendar/ui', root), join(work, 'apps/calendar/ui'), { recursive: true });
        await writeFile(join(work, 'apps/calendar/app.json'), JSON.stringify({ ...base, widgets: [{ ...base.widgets[0], entry: 'missing.js' }] }));
        await assert.rejects(buildAppCatalog(work), /no exporta createWidget/);
        await writeFile(join(work, 'apps/calendar/app.json'), JSON.stringify({ ...base, widgets: [{ ...base.widgets[0], style: 'missing.css' }] }));
        await assert.rejects(buildAppCatalog(work), /falta el estilo/);
        await rm(join(work, 'apps/calendar/ui/style.css'));
        await writeFile(join(work, 'apps/calendar/app.json'), JSON.stringify({ ...base, ui: { adapter: 'legacy' } }));
        assert.ok((await buildAppCatalog(work)).exports.has('calendar/widget.js'));
        await writeFile(join(work, 'apps/calendar/app.json'), JSON.stringify({ ...base, ui: { adapter: 'legacy' }, widgets: [{ ...base.widgets[0], entry: 'missing.js' }] }));
        await assert.rejects(buildAppCatalog(work), /no exporta createWidget/);
    } finally { await rm(work, { recursive: true, force: true }); }
});
