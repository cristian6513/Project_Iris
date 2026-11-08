import test from 'node:test';
import assert from 'node:assert/strict';
import { createAppCaller, withAppCaller } from '../../face/js/os_app_client.js';
import { createAppHost } from '../../face/js/os_app_host.js';
import { validateAppManifest } from '../../face/js/os_app_manifest.js';

test('manual calls are pinned to the instance and abort with its window/widget', async () => {
    const abort = new AbortController(), calls = [];
    const call = createAppCaller('contador', `/api/app-packages/files/ins_${'a'.repeat(32)}/main.js`, abort.signal,
        async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({ status: 'ok', data: { count: 3 } }) }; });
    assert.deepEqual(await call('increment', { amount: 3 }), { count: 3 });
    assert.equal(calls[0].url, '/api/app-packages/contador/call/increment');
    assert.equal(calls[0].options.headers['X-Iris-App-Installation'], `ins_${'a'.repeat(32)}`);
    assert.equal(calls[0].options.signal, abort.signal);
    await assert.rejects(() => call('../outside'), /Operación/);
    assert.equal(calls.length, 1);
    abort.abort(); assert.equal(calls[0].options.signal.aborted, true);
});

test('a retired backend result never looks like a success in the app', async () => {
    const call = createAppCaller('contador', `/api/app-packages/files/ins_${'a'.repeat(32)}/main.js`, null,
        async () => ({ ok: true, json: async () => ({ status: 'unavailable', message: 'La app cambió.' }) }));
    await assert.rejects(() => call('status'), /La app cambió/);
});

test('the app host injects the caller only for an installed Python app', async () => {
    let context;
    const module = withAppCaller({ createApp: c => { context = c; return { mount() {}, dispose() {} }; } },
        'createApp', 'contador', `/api/app-packages/files/ins_${'b'.repeat(32)}/main.js`);
    const host = createAppHost({ modules: { contador: async () => module } });
    await host.ensure('contador');
    assert.equal(typeof context.call, 'function');
    assert.equal(context.signal.aborted, false);
    host.dispose('contador');
    assert.equal(context.signal.aborted, true);
});

test('extension declarations require SDK 2 and are data, not activation grants', () => {
    const base = { schema: 'iris.app-manifest/v1', sdkVersion: 2, id: 'demo', name: 'Demo', version: '1.0.0',
        description: 'Ejemplo', icon: 'sparkle', presentation: 'window', window: { width: 420, height: 360 }, ui: { entry: 'main.js' },
        extension: { schema: 'iris.app-extension/v1', python: 'main.py', skill: true,
            operations: [{ action: 'status', label: 'Leer estado', effect: 'read', arguments: {} }] } };
    assert.equal(validateAppManifest(base).ok, true);
    assert.equal(validateAppManifest({ ...base, sdkVersion: 1 }).ok, false);
    assert.equal(validateAppManifest({ ...base, extension: { ...base.extension, python: '../main.py' } }).ok, false);
    assert.equal(validateAppManifest({ ...base, extension: { ...base.extension, dependencies: ['requests'] } }).ok, false);
});
