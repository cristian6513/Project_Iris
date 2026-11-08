import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, deferred, tick, THREE } from './animation_reload_fixture.mjs';

test('same semantic name replaces inactive bytes and extension; identical revision never downloads or restarts', async () => {
    const h = fixture(); h.clip('/old.vrma', 0); h.clip('/new.bvh', 1);
    await h.player.load('wave', '/old.vrma', { revision: 'a' });
    const old = h.player.actions.get('wave');
    assert.equal(await h.player.load('wave', '/new.bvh', { revision: 'b', format: 'bvh' }), true);
    assert.notEqual(h.player.actions.get('wave'), old); assert.equal(h.player.mixer.stats.actions.total, 1);
    h.player.play('wave'); h.player.update(0.7);
    const current = h.player.current, time = current.time;
    await h.player.load('wave', '/other-url', { revision: 'b', format: 'bvh' });
    assert.equal(h.loads.length, 2); assert.equal(h.player.current, current); assert.equal(current.time, time);
});

test('active replacement captures root before stopping, keeps 1x duration and retires old bindings after blending', async () => {
    const h = fixture(); h.clip('/a.vrma', 0, 2); h.clip('/b.vrma', 2, 7);
    await h.player.load('wave', '/a.vrma', { revision: 'a' }); h.player.play('wave', { loop: false, fadeIn: 0 }); h.player.update(0.5);
    const before = h.hips.position.clone(), old = h.player.current;
    await h.player.load('wave', '/b.vrma', { revision: 'b' });
    assert.ok(h.hips.position.distanceTo(before) < 1e-6); assert.equal(h.root.position.y, 0.7);
    assert.equal(h.player.current.loop, THREE.LoopOnce); assert.equal(h.player.current.clampWhenFinished, true);
    assert.equal(h.player.current.timeScale, 1); assert.equal(h.player.current.getClip().duration, 7);
    assert.equal(h.player._retiredClips.has(old.getClip()), true);
    h.player.update(0.8);
    assert.equal(h.player._retiredClips.size, 0); assert.equal(h.player.mixer.stats.actions.total, 1);
    assert.ok(Math.abs(h.player.current.time - 0.8) < 1e-6);
});

test('out-of-order replacements and failures never restore an obsolete revision', async () => {
    const h = fixture(); for (const name of ['a', 'b', 'c']) h.clip('/' + name + '.vrma', name.charCodeAt(0));
    await h.player.load('wave', '/a.vrma', { revision: 'a' }); h.player.play('wave');
    const gate = deferred(); h.gates.set('/b.vrma', gate);
    const slow = h.player.load('wave', '/b.vrma', { revision: 'b' }); await tick();
    await h.player.load('wave', '/c.vrma', { revision: 'c' }); gate.resolve();
    assert.equal(await slow, false); assert.equal(h.player._revisions.get('wave'), 'c');
    const current = h.player.current;
    assert.equal(await h.player.load('wave', '/missing.vrma', { revision: 'd' }), false);
    assert.equal(h.player.current, current); assert.equal(h.player._revisions.get('wave'), 'c');
    h.clip('/missing.vrma', 4); assert.equal(await h.player.load('wave', '/missing.vrma', { revision: 'd' }), true);
});

test('single-frame rest remains finite after revision and a retired avatar cannot accept late bytes', async () => {
    const h = fixture(); h.clip('/rest.vrma', 0, 0);
    await h.player.load('idle', '/rest.vrma', { revision: 'one' }); h.player.play('idle');
    h.player.update(10); assert.ok(h.hips.position.toArray().every(Number.isFinite));
    const gate = deferred(); h.gates.set('/rest.vrma', gate);
    const pending = h.player.load('idle', '/rest.vrma', { revision: 'two' }); await tick();
    h.idle.dispose(); gate.resolve(); assert.equal(await pending, false); assert.equal(h.player.clips.size, 0);
});

test('active one-shot listener follows the new action then returns to valid idle exactly once', async () => {
    const h = fixture(), idle = h.resource('idle.vrma'), a = h.resource('a.vrma'), b = h.resource('b.vrma');
    h.clip(idle.url, 0); h.clip(a.url, 1, 2); h.clip(b.url, 2, 4);
    await h.idle.applyManifest(h.snapshot('a', { idle, wave: a })); h.idle.playClipExternal('wave'); h.advance(0.5);
    await h.idle.applyManifest(h.snapshot('b', { idle, wave: b })); h.advance(2.5);
    assert.equal(h.player.currentName, 'wave');
    h.advance(2); assert.equal(h.player.currentName, 'idle');
    h.advance(1); assert.equal(h.player.mixer.stats.actions.inUse, 1);
    assert.equal(h.player.mixer._listeners.finished.length, 0);
});

test('facial metadata commits with its clip; failed revisions keep old ownership and can retry', async () => {
    const h = fixture(), a = h.resource('a.vrma'), b = h.resource('b.vrma'); h.clip(a.url, 0);
    await h.idle.applyManifest(h.snapshot('a', { wave: a }, { body_only_clips: ['wave'], facial_profiles: { wave: { emotion: 'feliz' } } }));
    h.idle.playClipExternal('wave');
    const next = h.snapshot('b', { wave: b }, { body_only_clips: [], facial_profiles: { wave: { emotion: 'triste' } } });
    assert.deepEqual((await h.idle.applyManifest(next)).failed, ['wave']);
    assert.equal(h.avatar.avatar.animationOwnsBlink, false); assert.equal(h.avatar.avatar.poseFacialProfile.emotion, 'feliz');
    h.clip(b.url, 1); await h.idle.applyManifest(next);
    assert.equal(h.avatar.avatar.animationOwnsBlink, true); assert.equal(h.avatar.avatar.poseFacialProfile, null);
    assert.equal(h.idle._vrmaManifest.facial_profiles.wave.emotion, 'triste');
});

test('withdrawal invalidates pending replacements and gracefully returns to saved rest', async () => {
    const h = fixture(), a = h.resource('a.vrma'), b = h.resource('b.vrma'); h.clip(a.url, 1); h.clip(b.url, 2);
    await h.idle.applyManifest(h.snapshot('a', { wave: a })); h.idle.playClipExternal('wave'); h.advance(0.5);
    const gate = deferred(); h.gates.set(b.url, gate);
    const pending = h.idle.applyManifest(h.snapshot('b', { wave: b })); await tick();
    const before = h.hips.position.clone(); await h.idle.applyManifest(h.snapshot('c', {}));
    assert.equal(h.player.hasAnimation('wave'), false); assert.ok(h.hips.position.distanceTo(before) < 1e-6);
    gate.resolve(); await pending; h.advance(1);
    assert.equal(h.player.clips.size, 0); assert.equal(h.player.mixer.stats.actions.total, 0);
    assert.equal(h.player.mixer.stats.bindings.total, 0); assert.equal(h.root.position.y, 0.7);
});

for (const format of ['vrma', 'bvh', 'fbx', 'glb', 'gltf']) test(`authenticated blob dispatches explicit ${format} and revokes after parsing`, async () => {
    const h = fixture(), url = 'blob:opaque'; h.clip(url, 1); let released = 0;
    const gate = deferred(); h.gates.set(url, gate);
    const promise = h.player.load('wave', '/unused', { revision: 'v1', format,
        resolveAsset: async () => ({ url, release: () => released++ }) });
    await tick(); assert.equal(released, 0); gate.resolve(); assert.equal(await promise, true);
    assert.equal(released, 1); assert.equal(h.loads[0].format, format === 'gltf' ? 'glb' : format);
});

test('unknown format cannot reach a loader or obtain an authenticated URL', async () => {
    const h = fixture(); let requested = false;
    assert.equal(await h.player.load('wave', '/secret', { revision: 'x', format: 'BREW', resolveAsset: () => { requested = true; } }), false);
    assert.equal(requested, false); assert.equal(h.loads.length, 0);
});

test('reselecting the loaded revision invalidates a different pending candidate', async () => {
    const h = fixture(); h.clip('/a.vrma', 0); h.clip('/b.vrma', 1);
    await h.player.load('wave', '/a.vrma', { revision: 'a' });
    const gate = deferred(); h.gates.set('/b.vrma', gate);
    const pending = h.player.load('wave', '/b.vrma', { revision: 'b' }); await tick();
    await h.player.load('wave', '/a.vrma', { revision: 'a' }); gate.resolve();
    assert.equal(await pending, false); assert.equal(h.player._revisions.get('wave'), 'a');
});

test('reverting to the previous manifest while a new revision is pending cancels that revision', async () => {
    const h = fixture(), a = h.resource('a.vrma'), b = h.resource('b.vrma'); h.clip(a.url, 0); h.clip(b.url, 1);
    const original = h.snapshot('a', { wave: a }); await h.idle.applyManifest(original);
    const gate = deferred(); h.gates.set(b.url, gate);
    const pending = h.idle.applyManifest(h.snapshot('b', { wave: b })); await tick();
    await h.idle.applyManifest(original); gate.resolve(); await pending;
    assert.equal(h.player._revisions.get('wave'), a.revision); assert.equal(h.idle._manifestRevision, 'a');
});

test('an in-flight Core snapshot prevents the frame loop from starting a competing local initialization', async () => {
    const h = fixture(), resource = h.resource('slow.vrma'); h.clip(resource.url, 0);
    h.idle._vrmaInitAttempted = false;
    const gate = deferred(); h.gates.set(resource.url, gate);
    const pending = h.idle.applyManifest(h.snapshot('remote', { wave: resource }));
    h.idle.update(1 / 60, 0);
    assert.equal(h.idle._vrmaInitAttempted, true); assert.equal(h.idle._vrmaInitPromise, null);
    gate.resolve(); await pending; assert.equal(h.idle._manifestRevision, 'remote');
});

test('an explicit format change dispatches again even when the byte revision is identical', async () => {
    const h = fixture(), url = 'blob:same-bytes'; h.clip(url, 1);
    await h.player.load('wave', url, { revision: 'same', format: 'vrma' });
    const previous = h.player.actions.get('wave');
    assert.equal(await h.player.load('wave', url, { revision: 'same', format: 'fbx' }), true);
    assert.deepEqual(h.loads.map(load => load.format), ['vrma', 'fbx']);
    assert.notEqual(h.player.actions.get('wave'), previous);
    assert.equal(h.player._revisions.get('wave'), 'same');
    assert.equal(h.player._formats.get('wave'), 'fbx');
    assert.equal(h.player.mixer.stats.actions.total, 1);
    h.idle.dispose();
});

test('pending identical bytes serve the newest manifest metadata without a second download', async () => {
    const h = fixture(), wave = h.resource('slow.vrma', 'bytes-one'); h.clip(wave.url, 1);
    const gate = deferred(); h.gates.set(wave.url, gate);
    const first = h.idle.applyManifest(h.snapshot('metadata-one', { wave }, {
        body_only_clips: [], facial_profiles: { wave: { emotion: 'feliz' } },
        pose_map: { greet: 'wave' }, activity_clips: { listening: { clip: 'wave', loop: false } },
    }));
    await tick();
    const latest = h.idle.applyManifest(h.snapshot('metadata-two', { wave }, {
        body_only_clips: ['wave'], facial_profiles: { wave: { emotion: 'triste' } },
        pose_map: { greet: 'wave', wave_hand: 'wave' }, activity_clips: { listening: { clip: 'wave', loop: true } },
    }));
    await tick(); assert.equal(h.loads.length, 1);
    gate.resolve();
    const results = await Promise.all([first, latest]);
    assert.deepEqual(results.map(result => result.failed), [[], []]);
    assert.equal(h.loads.length, 1);
    assert.equal(h.idle._manifestRevision, 'metadata-two');
    assert.deepEqual(h.idle._vrmaManifest.body_only_clips, ['wave']);
    assert.equal(h.idle._vrmaManifest.facial_profiles.wave.emotion, 'triste');
    assert.equal(h.idle._vrmaManifest.pose_map.wave_hand, 'wave');
    assert.equal(h.idle._vrmaManifest.activity_clips.listening.loop, true);
    h.idle.playClipExternal('wave');
    assert.equal(h.avatar.avatar.animationOwnsBlink, false);
    assert.equal(h.avatar.avatar.poseFacialProfile.emotion, 'triste');
    h.idle.dispose();
});

test('failed clips retain omitted or failed mappings while accepted mappings win and retired clips stay absent', async () => {
    const h = fixture(), a = h.resource('wave-a.vrma'), b = h.resource('wave-b.vrma');
    const nod = h.resource('nod.vrma'), think = h.resource('think.vrma'), missing = h.resource('missing.vrma');
    h.clip(a.url, 0); h.clip(nod.url, 1); h.clip(think.url, 2);
    await h.idle.applyManifest(h.snapshot('before', { wave: a, nod }, {
        pose_map: { omitted: 'wave', remapped: 'wave', accepted: 'wave', retired: 'nod' },
        activity_clips: {
            listening: { clip: 'wave', loop: false }, thinking: { clip: 'wave', loop: false },
            speaking: { clip: 'wave', loop: false }, retired: { clip: 'nod', loop: false },
        },
    }));
    const result = await h.idle.applyManifest(h.snapshot('after', { wave: b, think, missing }, {
        pose_map: { remapped: 'missing', accepted: 'think', retired: 'wave' },
        activity_clips: {
            thinking: { clip: 'missing', loop: true }, speaking: { clip: 'think', loop: true },
            retired: { clip: 'wave', loop: true },
        },
    }));
    assert.deepEqual(result.failed.sort(), ['missing', 'wave']);
    const effective = h.idle._vrmaManifest;
    assert.equal(effective.clips.wave, a.filename);
    assert.equal(effective.pose_map.omitted, 'wave');
    assert.equal(effective.pose_map.remapped, 'wave');
    assert.equal(effective.pose_map.accepted, 'think');
    assert.deepEqual(effective.activity_clips.listening, { clip: 'wave', loop: false });
    assert.deepEqual(effective.activity_clips.thinking, { clip: 'wave', loop: false });
    assert.deepEqual(effective.activity_clips.speaking, { clip: 'think', loop: true });
    assert.notEqual(effective.pose_map.retired, 'nod');
    assert.notEqual(effective.activity_clips.retired?.clip, 'nod');
    assert.equal(h.player.hasAnimation('nod'), false);
    assert.equal(Object.hasOwn(effective.clips, 'nod'), false);
    h.idle.dispose();
});

test('withdrawing an inactive outgoing clip preserves the sampled transition and releases its bindings', async () => {
    const h = fixture(); h.clip('/wave.vrma', 2); h.clip('/idle.vrma', 0);
    await h.player.load('wave', '/wave.vrma', { revision: 'wave' });
    await h.player.load('idle', '/idle.vrma', { revision: 'idle' });
    h.player.play('wave', { loop: false, fadeIn: 0 }); h.player.update(0.5);
    const outgoing = h.player.actions.get('wave'), outgoingClip = outgoing.getClip();
    h.player.play('idle', { loop: true, fadeIn: 0.6 }); h.player.update(0.1);
    assert.equal(h.player.currentName, 'idle');
    assert.ok(h.player.transitionRemaining > 0);
    const before = h.hips.position.clone();
    h.player.remove('wave');
    assert.ok(h.hips.position.distanceTo(before) < 1e-6);
    assert.equal(h.player.hasAnimation('wave'), false);
    h.player.update(1);
    assert.equal(h.player.transitionRemaining, 0);
    assert.equal(h.player.mixer.existingAction(outgoingClip), null);
    assert.equal(h.player.mixer.stats.actions.total, 1);
    assert.equal(h.player.mixer.stats.bindings.total, 1);
    h.player.stop(0); h.player.remove('idle');
    assert.equal(h.player.mixer.stats.actions.total, 0);
    assert.equal(h.player.mixer.stats.bindings.total, 0);
    assert.equal(h.root.position.y, 0.7);
    h.idle.dispose();
});
