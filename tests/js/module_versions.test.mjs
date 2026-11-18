// Every importer of a module asks for the same «?v=».
//
// The version is how Face busts the browser cache, and the browser keys modules
// by full URL: «./x.js?v=4» and «./x.js?v=5» are two separate copies of x.js,
// each with its own module state, and the older one may come from the cache.
// On 2026-11-17 nine modules were imported with two or three versions (the
// audit of that day). Paths are resolved, so «./x.js» from face/js and
// «../js/x.js» from face/companion count as the same module. The app catalog
// versions its imports with a content digest instead of a number; that is a
// version too, so a module imported both ways would be two copies as well.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const SKIP = /^(node_modules|vendor|lib|dist|build)$/;
const SPECIFIER = /(["'`])((?:\.\.?\/|js\/)[^"'`?\s]+\.m?js)\?v=([0-9A-Za-z]+)\1/g;

function sources(dir, out = []) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) { if (!SKIP.test(entry.name)) sources(full, out); }
        else if (/\.(m?js|html)$/.test(entry.name)) out.push(full);
    }
    return out;
}

const fromRoot = (path) => relative(ROOT, path).split(sep).join('/');

function versionsByModule() {
    const modules = new Map();
    for (const file of ['face', 'apps'].flatMap((folder) => sources(join(ROOT, folder)))) {
        for (const [, , specifier, version] of readFileSync(file, 'utf8').matchAll(SPECIFIER)) {
            const target = fromRoot(resolve(dirname(file), specifier));
            if (!modules.has(target)) modules.set(target, new Map());
            const importers = modules.get(target);
            if (!importers.has(version)) importers.set(version, new Set());
            importers.get(version).add(fromRoot(file));
        }
    }
    return modules;
}

test('each versioned module is imported with a single version', () => {
    const modules = versionsByModule();
    assert.ok(modules.size > 100, `found only ${modules.size} versioned modules; the scan is broken`);
    const mixed = [...modules]
        .filter(([, versions]) => versions.size > 1)
        .map(([target, versions]) => `${target}: ${[...versions]
            .map(([version, importers]) => `v=${version} (${[...importers].join(', ')})`).join(' / ')}`);
    assert.deepEqual(mixed, [], `give these importers the same version:\n${mixed.join('\n')}`);
});

test('the scan resolves paths, so a module imported from two folders counts once', () => {
    const modules = versionsByModule();
    const player = modules.get('face/js/tts_audio_player.js');
    assert.ok(player, 'tts_audio_player.js should be found through its importers');
    const importers = [...player.values()].flatMap((set) => [...set]);
    assert.ok(importers.includes('face/js/app.js') && importers.includes('face/companion/companion.js'),
        `expected the room and Companion among its importers, got ${importers.join(', ')}`);
});
