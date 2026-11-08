import test from 'node:test';
import assert from 'node:assert/strict';
import { AnimationLibrarySync, validateAnimationSnapshot } from '../../face/js/animation_library_sync.js';
import { fixture, deferred, tick } from './animation_reload_fixture.mjs';

function harness() {
    const h = fixture(), timers = new Map(), handlers = new Map(), requests = [], errors = [];
    let seq = 0, data = h.snapshot('one', { wave: h.resource('one.vrma') }), offline = false;
    h.clip(data.resources.wave.url, 0);
    const doc = { visibilityState: 'visible', addEventListener: (type, handler) => handlers.set(type, handler), removeEventListener: type => handlers.delete(type) };
    const sync = new AnimationLibrarySync({ getIdle: () => h.idle, document: doc, window: doc,
        onError: message => errors.push(message), setTimer: cb => { timers.set(++seq, cb); return seq; }, clearTimer: id => timers.delete(id),
        fetchSnapshot: async options => {
            requests.push(options);
            if (offline) throw Error('offline');
            return new Response(options.headers['If-None-Match'] === '"' + data.revision + '"' ? null : JSON.stringify(data), {
                status: options.headers['If-None-Match'] === '"' + data.revision + '"' ? 304 : 200, headers: { etag: '"' + data.revision + '"' },
            });
        },
    });
    return { ...h, sync, doc, handlers, timers, requests, errors, change: value => { data = value; }, offline: value => { offline = value; } };
}

test('one active poll revalidates with 304, pauses hidden, reconciles focus and disposes listeners', async () => {
    const h = harness(); h.sync.start(); h.sync.start(); await tick();
    assert.equal(h.timers.size, 1); assert.equal(h.loads.length, 1);
    const action = h.player.actions.get('wave'); h.player.play('wave'); h.player.update(0.5);
    await h.sync.refresh(); assert.equal(h.requests.at(-1).headers['If-None-Match'], '"one"');
    assert.equal(h.loads.length, 1); assert.equal(h.player.current, action); assert.equal(action.time, 0.5);
    h.doc.visibilityState = 'hidden'; h.handlers.get('visibilitychange')();
    assert.equal(h.timers.size, 0); const requests = h.requests.length; await h.sync.refresh(); assert.equal(h.requests.length, requests);
    const resource = h.resource('two.vrma'); h.clip(resource.url, 1); h.change(h.snapshot('two', { wave: resource }));
    h.doc.visibilityState = 'visible'; h.handlers.get('visibilitychange')(); await tick();
    assert.equal(h.player._revisions.get('wave'), resource.revision); assert.equal(h.timers.size, 1);
    h.handlers.get('focus')(); await tick(); assert.equal(h.timers.size, 1);
    h.sync.dispose(); assert.equal(h.timers.size, 0); assert.equal(h.handlers.size, 0);
});

test('Core outage preserves bundle/last-good poses and reconnection applies its override', async () => {
    const h = harness(); h.offline(true);
    const bundled = h.resource('bundle.vrma'); h.clip(bundled.url, -1);
    await h.idle.applyManifest(h.snapshot('bundle', { wave: bundled }));
    await h.sync.refresh(); assert.equal(h.player._revisions.get('wave'), bundled.revision);
    assert.deepEqual(h.errors, ['offline']);
    h.offline(false); await h.sync.refresh(); assert.equal(h.player._revisions.get('wave'), 'one.vrma');
    h.offline(true); await h.sync.refresh(); assert.equal(h.player._revisions.get('wave'), 'one.vrma');
});

test('event during a pending response revalidates once more without overlapping fetches', async () => {
    const h = harness(), gate = deferred(); let calls = 0, active = 0, peak = 0;
    const a = h.snapshot('a', {}), b = h.snapshot('b', {});
    h.sync.fetchSnapshot = async () => { peak = Math.max(peak, ++active); const index = ++calls; if (index === 1) await gate.promise; active--; return new Response(JSON.stringify(index === 1 ? a : b)); };
    const first = h.sync.refresh(); const second = h.sync.refresh(); gate.resolve();
    await Promise.all([first, second]); await tick();
    assert.equal(peak, 1); assert.equal(calls, 2); assert.equal(h.sync.snapshot.revision, 'b');
});

test('native sync loads Core override through authenticated bridge and releases blobs on failure', async () => {
    const h = harness(), urls = [], releases = [];
    h.sync.bridge = { native: true, async coreAssetUrl(url) { urls.push(url); return 'blob:core'; }, releaseCoreAssetUrl: url => releases.push(url) };
    await h.sync.refresh(); assert.equal(h.errors.length, 1); assert.deepEqual(releases, ['blob:core']);
    h.clip('blob:core', 0.5); await h.sync.refresh();
    assert.equal(h.player._revisions.get('wave'), 'one.vrma'); assert.equal(releases.length, 2);
    assert.ok(urls.every(url => url.startsWith('/face/animations/')));
    await h.sync.refresh(); assert.equal(releases.length, 2, '304/identical revision must not download a new blob');
});

for (const path of ['/absolute.vrma', '../escape.vrma', '..\\escape.vrma', 'C:\\model.vrma', '\\\\server\\model.vrma']) {
    test(`snapshot validation rejects path ${path} before bridge access`, () => {
        const h = fixture(), resource = h.resource('valid.vrma'); resource.filename = path;
        const snapshot = h.snapshot('bad', { wave: resource });
        assert.throws(() => validateAnimationSnapshot(snapshot), /inválid/);
    });
}

test('snapshot validation rejects remote token destinations and unknown format', () => {
    const h = fixture(), resource = h.resource('valid.vrma');
    resource.url = 'https://other.test/steal'; assert.throws(() => validateAnimationSnapshot(h.snapshot('bad', { wave: resource })));
    resource.url = '/face/animations/valid.vrma?token=secret'; assert.throws(() => validateAnimationSnapshot(h.snapshot('bad', { wave: resource })));
    resource.url = '/face/animations/valid.vrma'; resource.format = 'BREW'; assert.throws(() => validateAnimationSnapshot(h.snapshot('bad', { wave: resource })));
});

test('disposing during snapshot JSON parsing cannot apply or restart polling', async () => {
    const h = harness(), json = deferred();
    h.sync.fetchSnapshot = async () => ({ ok: true, status: 200, json: () => json.promise, headers: new Headers() });
    h.sync.start(); await tick(); h.sync.dispose(); json.resolve(h.snapshot('late', {})); await tick();
    assert.equal(h.sync.snapshot, null); assert.equal(h.loads.length, 0); assert.equal(h.timers.size, 0);
});

test('malformed metadata cannot retire a previously valid library', async () => {
    const h = harness(); await h.sync.refresh();
    const previous = h.player.clips.get('wave');
    h.change(h.snapshot('bad', {}, { body_only_clips: 'not-a-list' }));
    await h.sync.refresh();
    assert.equal(h.player.clips.get('wave'), previous); assert.equal(h.sync.snapshot.revision, 'one');
    assert.match(h.errors[0], /Metadatos/);
});

test('Face and Companion reconcile a factory pose, a profile override and its failed replacement', async () => {
    const factory = 'iris_wave.vrma';
    const digest = 'a'.repeat(64), personal = `wave.${digest}.vrma`;
    for (const native of [false, true]) {
        const h = harness(), downloaded = [], released = [];
        if (native) h.sync.bridge = {
            native: true,
            async coreAssetUrl(url) { downloaded.push(url); return 'blob:' + url; },
            releaseCoreAssetUrl(url) { released.push(url); },
        };
        const resource = (filename, source, revision) => ({
            ...h.resource(filename, revision), source,
            url: `/face/animations/${filename}?v=${revision}`,
        });
        const base = resource(factory, 'factory', 'factory-bytes');
        h.clip(native ? 'blob:' + base.url : base.url, 0);
        h.change(h.snapshot('factory', { wave: base }));
        await h.sync.refresh();
        const initial = h.player.clips.get('wave');
        const override = resource(personal, 'user', digest);
        h.clip(native ? 'blob:' + override.url : override.url, 1);
        h.change(h.snapshot('personal', { wave: override }));
        await h.sync.refresh();
        assert.notEqual(h.player.clips.get('wave'), initial);
        assert.equal(h.player._revisions.get('wave'), digest);
        const valid = h.player.clips.get('wave');
        const bad = resource(`wave.${'b'.repeat(64)}.vrma`, 'user', 'b'.repeat(64));
        h.change(h.snapshot('bad-upload', { wave: bad }));
        await h.sync.refresh();
        assert.equal(h.player.clips.get('wave'), valid);
        assert.equal(h.player._revisions.get('wave'), digest);
        assert.ok(h.errors.at(-1).includes('Se conserva'));
        if (native) {
            assert.deepEqual(downloaded, [base.url, override.url, bad.url]);
            assert.equal(released.length, 3);
        }
        h.sync.dispose();
    }
});
