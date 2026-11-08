import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
    copyAnimationAssets, factoryContract, prepareAnimationAssets, releaseManifest,
} from '../../companion/scripts/animation_assets.mjs';
import { animationContextsFromDocument, resolveAnimationContext } from '../../face/js/animation_context_resolver.js';

const source = fileURLToPath(new URL('../../face/animations', import.meta.url));

test('Companion packages only the approved factory assets and playable context fallbacks', async () => {
    const original = await readFile(join(source, 'manifest.json'));
    const assets = await prepareAnimationAssets(source);
    const manifest = JSON.parse(assets.get('manifest.json'));
    assert.equal(Object.keys(manifest.clips).length, 59);
    assert.deepEqual([...assets.keys()].sort(), [
        ...Object.values(factoryContract.clips).map((item) => item.filename),
        'manifest.json', 'contexts.json', 'emotions.json', 'README.md',
        'iris.vrm', 'LICENSE-IRIS-VRM.txt', // IRIS' own avatar, under its own licence
    ].sort());
    for (const field of ['activity_clips', 'menu_poses']) {
        assert.deepEqual(manifest[field], JSON.parse(original)[field]);
    }
    // Every packaged clip keeps its face; a clip left out takes its profile with it.
    assert.deepEqual(manifest.facial_profiles, Object.fromEntries(Object.entries(JSON.parse(original).facial_profiles)
        .filter(([name]) => name in manifest.clips)));
    assert.equal(manifest.pose_map.sit_chair, undefined);
    for (const key of ['window_lean_in', 'window_look_idle', 'window_lean_out']) {
        assert.equal(manifest.clips[key], `iris_${key}.vrma`);
        assert.ok(manifest.runtime_only.includes(key));
    }
    const contexts = animationContextsFromDocument(JSON.parse(assets.get('contexts.json')));
    for (const action of ['walk_tour', 'walk_in_place']) {
        const result = resolveAnimationContext(action, { surface: 'desktop_companion', contexts });
        assert.equal(result.animation, 'idle_casual');
        assert.equal(result.movement, 'window-walk');
        assert.ok(assets.has(manifest.clips[result.animation]));
    }
    assert.deepEqual(await readFile(join(source, 'manifest.json')), original);
});

for (const filename of ['/absolute.vrma', 'C:/file.vrma', '../outside.vrma', '..\\outside.vrma', '\\\\server\\file.vrma']) {
    test(`Companion rejects unsafe animation reference ${filename}`, async () => {
        const manifest = JSON.parse(await readFile(join(source, 'manifest.json')));
        manifest.clips.extra = filename;
        assert.throws(() => releaseManifest(manifest), /Invalid animation filename/);
    });
}

test("IRIS' avatar is pinned like the clips and travels with its licence", async () => {
    assert.deepEqual(factoryContract.avatar, { filename: 'iris.vrm',
        sha256: 'c1dc7d03e96b4e166f13bced882ae20c09e13bb3129dd2ea42b56aae0ccd3531', license: 'LICENSE-IRIS-VRM.txt' });
    const assets = await prepareAnimationAssets(source);
    assert.match(assets.get('LICENSE-IRIS-VRM.txt').toString('utf8'), /NO forma parte de la licencia MIT/);
    for (const avatar of [{ ...factoryContract.avatar, sha256: '0'.repeat(64) }]) {
        await assert.rejects(prepareAnimationAssets(source, { ...factoryContract, avatar }), /Incorrect avatar hash/);
    }
    for (const avatar of [{ ...factoryContract.avatar, filename: 'iris.glb' },
        { ...factoryContract.avatar, filename: '../iris.vrm' }, { ...factoryContract.avatar, license: 'iris_idle.vrma' },
        { ...factoryContract.avatar, sha256: 'x' }]) {
        await assert.rejects(prepareAnimationAssets(source, { ...factoryContract, avatar }), /avatar|filename/i);
    }
});

test('Companion rejects changed hashes and unknown contract versions', async () => {
    const contract = structuredClone(factoryContract);
    contract.clips.idle.sha256 = '0'.repeat(64);
    await assert.rejects(prepareAnimationAssets(source, contract), /Incorrect animation hash/);
    contract.schema = 999;
    await assert.rejects(prepareAnimationAssets(source, contract), /Unknown factory contract/);
});

test('Companion refuses real symlink/junction inputs and linked output directories', async () => {
    const temp = await mkdtemp(join(tmpdir(), 'iris-companion-assets-'));
    const alias = join(temp, 'alias');
    try {
        await symlink(source, alias, process.platform === 'win32' ? 'junction' : 'dir');
        await assert.rejects(prepareAnimationAssets(alias), /Linked animation path/);
        await assert.rejects(copyAnimationAssets(source, alias), /Linked animation path/);
    } finally {
        // rm does not traverse directory symlinks/junctions.
        await rm(alias, { force: true });
        await rm(temp, { recursive: true, force: true });
    }
});

test('Companion refuses stale private output instead of carrying it into a bundle', async () => {
    const temp = await mkdtemp(join(tmpdir(), 'iris-companion-assets-'));
    try {
        await writeFile(join(temp, '_draft.glb'), 'private');
        await assert.rejects(copyAnimationAssets(source, temp), /Unexpected old animation/);
        assert.equal(await readFile(join(temp, '_draft.glb'), 'utf8'), 'private');
    } finally {
        await rm(temp, { recursive: true, force: true });
    }
});

test('Face retains the idle fallback for an excluded optional seated clip', async () => {
    const sourceText = await readFile(new URL('../../face/js/scene_actions.js', import.meta.url), 'utf8');
    const block = sourceText.match(/^    _playHotspotPose\([^]*?^    \}/m)?.[0];
    assert.ok(block);
    const play = new Function(`return ({${block.trim()}})._playHotspotPose`)();
    const calls = [];
    const state = {
        idleSystem: { trigger: () => false, playClipExternal: () => false },
        avatar: { playAnimation: (name) => calls.push(name) },
    };
    play.call(state, { animationName: 'sit_chair', fallbackAnimation: 'idle' });
    assert.deepEqual(calls, ['idle']);
});
