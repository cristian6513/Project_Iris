import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { copyFile, link, lstat, mkdir, mkdtemp, readFile, readdir, rename, rm, symlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { after, before, test } from 'node:test';
import { prepareVisionAssets } from '../../scripts/vision_assets.mjs';

const ROOT = path.resolve(fileURLToPath(new URL('../..', import.meta.url)));
const pins = JSON.parse(await readFile(new URL('../../scripts/vision_assets.json', import.meta.url), 'utf8'));
let scratch, project;
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const sourcePath = source => path.join(project, ...source.split('/'));
const exists = async filename => { try { await lstat(filename); return true; } catch (e) { if (e.code === 'ENOENT') return false; throw e; } };

before(async () => {
    // Every negative probe operates on a dedicated fixture, never the developer
    // dependency tree, current Face vendor directory, or a personal profile.
    scratch = await mkdtemp(path.join(ROOT, 'artifacts', 'a08-3-vision-tests-'));
    project = path.join(scratch, 'project with spaces');
    for (const entry of pins.files) {
        const target = sourcePath(entry.source);
        await mkdir(path.dirname(target), { recursive: true });
        await copyFile(path.join(ROOT, ...entry.source.split('/')), target);
    }
});

after(async () => {
    // Resolve and constrain the recursive cleanup to this test's own fixture.
    if (!scratch || !path.resolve(scratch).startsWith(path.join(ROOT, 'artifacts', 'a08-3-vision-tests-'))) {
        throw new Error('Unsafe Vision fixture cleanup');
    }
    await rm(scratch, { recursive: true, force: true });
});

test('preparation copies the exact verified offline runtime, model and license', async () => {
    const target = path.join(scratch, 'output', 'Vision local');
    const manifest = await prepareVisionAssets({ projectRoot: project, target });
    assert.equal(manifest.mediapipe_version, '0.10.35');
    assert.equal(manifest.model.sha256, pins.model.sha256);
    assert.equal(manifest.license, 'Apache-2.0');
    assert.equal(manifest.files.length, 9);
    assert.deepEqual(JSON.parse(await readFile(path.join(target, 'manifest.json'), 'utf8')), manifest);
    for (const entry of manifest.files) {
        const bytes = await readFile(path.join(target, ...entry.path.split('/')));
        assert.equal(bytes.length, entry.size);
        assert.equal(digest(bytes), entry.sha256);
    }
    assert.deepEqual((await readdir(target)).sort(), [
        'MediaPipe-Apache-2.0.txt', 'face_landmarker.task', 'manifest.json', 'vision_bundle.mjs', 'wasm',
    ].sort());
    assert.equal((await readdir(path.join(target, 'wasm'))).length, 6);
});

test('an existing preview or staging is never overwritten', async () => {
    const target = path.join(scratch, 'preview');
    await mkdir(target);
    await writeFile(path.join(target, 'keep.txt'), 'previous preview');
    await assert.rejects(prepareVisionAssets({ projectRoot: project, target }), /must be a new directory/);
    assert.equal(await readFile(path.join(target, 'keep.txt'), 'utf8'), 'previous preview');
    assert.deepEqual(await readdir(target), ['keep.txt']);
});

for (const resource of ['vision_bundle.mjs', 'wasm/vision_wasm_internal.wasm', 'face_landmarker.task', 'MediaPipe-Apache-2.0.txt']) {
    test(`corrupt ${resource} fails before creating output parents`, async () => {
        const entry = pins.files.find(file => file.path === resource), source = sourcePath(entry.source);
        const previous = await readFile(source);
        const target = path.join(scratch, `failed-${path.posix.basename(resource)}`, 'vision');
        try {
            const corrupt = Buffer.from(previous); corrupt[0] ^= 1;
            await writeFile(source, corrupt);
            await assert.rejects(prepareVisionAssets({ projectRoot: project, target }), /SHA-256 mismatch/);
            assert.equal(await exists(path.dirname(target)), false);
        } finally { await writeFile(source, previous); }
    });
}

test('wrong package version is rejected before writing output', async () => {
    const source = sourcePath(pins.files[0].source), previous = await readFile(source);
    const target = path.join(scratch, 'wrong-version');
    try {
        const pkg = JSON.parse(previous); pkg.version = '0.10.34';
        await writeFile(source, JSON.stringify(pkg));
        await assert.rejects(prepareVisionAssets({ projectRoot: project, target }), /Incompatible MediaPipe/);
        assert.equal(await exists(target), false);
    } finally { await writeFile(source, previous); }
});

test('missing model and missing license are rejected before writing output', async () => {
    for (const resource of ['face_landmarker.task', 'MediaPipe-Apache-2.0.txt']) {
        const source = sourcePath(pins.files.find(file => file.path === resource).source);
        const moved = source + '.fixture-backup', target = path.join(scratch, `missing-${resource}`);
        try {
            await rename(source, moved);
            await assert.rejects(prepareVisionAssets({ projectRoot: project, target }), /ENOENT/);
            assert.equal(await exists(target), false);
        } finally { await rename(moved, source); }
    }
});

test('extra WASM input is rejected instead of entering an inventory', async () => {
    const extra = sourcePath('companion/node_modules/@mediapipe/tasks-vision/wasm/unapproved.js');
    const target = path.join(scratch, 'extra-wasm');
    try {
        await writeFile(extra, 'unapproved');
        await assert.rejects(prepareVisionAssets({ projectRoot: project, target }), /Unexpected or missing/);
        assert.equal(await exists(target), false);
    } finally { await rm(extra); }
});

test('absolute, traversal, ADS, reserved names and trailing aliases are rejected', async () => {
    for (const target of ['relative-target', scratch + '/unused/../escape',
        scratch + '/unused/./alias', scratch + '/vision:stream', scratch + '/vision.',
        scratch + '/vision ', scratch + '/CON', scratch + '/nested\\..\\escape']) {
        await assert.rejects(prepareVisionAssets({ projectRoot: project, target }), /absolute|unsafe|alias|traversal/);
    }
    assert.equal(await exists(path.join(scratch, 'unused')), false);
    assert.equal(await exists(path.join(scratch, 'escape')), false);
});

test('case aliases on existing parents are rejected', async () => {
    const parent = path.join(scratch, 'CaseParent');
    await mkdir(parent);
    await assert.rejects(prepareVisionAssets({ projectRoot: project,
        target: path.join(scratch, 'caseparent', 'vision') }), /Path alias/);
    assert.deepEqual(await readdir(parent), []);
});

test('a linked source directory or target parent is rejected before writing', async () => {
    const targetParent = path.join(scratch, 'linked-output'), realParent = path.join(scratch, 'real-output');
    await mkdir(realParent);
    await symlink(realParent, targetParent, process.platform === 'win32' ? 'junction' : 'dir');
    await assert.rejects(prepareVisionAssets({ projectRoot: project, target: path.join(targetParent, 'vision') }), /Link rejected/);
    assert.deepEqual(await readdir(realParent), []);
    const source = sourcePath('companion/vendor/mediapipe'), moved = source + '-real';
    await rename(source, moved);
    try {
        await symlink(moved, source, process.platform === 'win32' ? 'junction' : 'dir');
        await assert.rejects(prepareVisionAssets({ projectRoot: project, target: path.join(scratch, 'linked-source') }), /Link rejected/);
        assert.equal(await exists(path.join(scratch, 'linked-source')), false);
    } finally { await rm(source); await rename(moved, source); }
});

test('hardlinked model is rejected before writing output', async () => {
    const source = sourcePath('companion/vendor/mediapipe/face_landmarker.task'), alias = path.join(scratch, 'model-alias');
    try {
        await link(source, alias);
        await assert.rejects(prepareVisionAssets({ projectRoot: project, target: path.join(scratch, 'hardlink-output') }), /unlinked file/);
        assert.equal(await exists(path.join(scratch, 'hardlink-output')), false);
    } finally { await rm(alias); }
});

test('contract traversal and case duplicates are rejected before writing output', async () => {
    const scripts = path.join(scratch, 'isolated-scripts');
    await mkdir(scripts);
    await copyFile(path.join(ROOT, 'scripts', 'vision_assets.mjs'), path.join(scripts, 'vision_assets.mjs'));
    const pinsPath = path.join(scripts, 'vision_assets.json');
    const isolated = await import(pathToFileURL(path.join(scripts, 'vision_assets.mjs')).href);
    for (const mutate of [
        value => { value.files[1].source = '../outside.mjs'; },
        value => { value.files[1].path = '../outside.mjs'; },
        value => { value.files[2].path = 'VISION_BUNDLE.MJS'; },
    ]) {
        const invalid = structuredClone(pins); mutate(invalid);
        await writeFile(pinsPath, JSON.stringify(invalid));
        const target = path.join(scratch, 'bad-contract-output');
        await assert.rejects(isolated.prepareVisionAssets({ projectRoot: project, target }), /traversal|alias/);
        assert.equal(await exists(target), false);
    }
});

test('importing the Face preparation entry point does not build or touch preview', async () => {
    const imported = await import('../../scripts/prepare_face_vision.mjs');
    assert.equal(imported.prepareVisionAssets, prepareVisionAssets);
    assert.equal(typeof imported.main, 'function');
    await assert.rejects(imported.main(['--target', '../escape']), /traversal/);
    await assert.rejects(imported.main(['--unsupported']), /Usage/);
});
