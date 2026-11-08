import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

import {
    companionWebSocketProtocols,
    createCompanionBridge,
    isTauriRuntime,
    localAnimationBase,
} from '../../face/js/companion_native_bridge.js';


test('native detection only accepts the Tauri runtime marker', () => {
    assert.equal(isTauriRuntime({}), false);
    assert.equal(isTauriRuntime({ __TAURI_INTERNALS__: {} }), true);
});


test('pairing token travels in a WebSocket subprotocol, never the URL', () => {
    assert.deepEqual(
        companionWebSocketProtocols('safe_token-1'),
        ['iris-companion-v1', 'iris-token.safe_token-1'],
    );
});


test('animation assets stay local to both browser and packaged page', () => {
    assert.equal(
        localAnimationBase('http://localhost:8765/face/companion/index.html'),
        'http://localhost:8765/face/animations',
    );
    assert.equal(
        localAnimationBase('http://tauri.localhost/face/companion/index.html'),
        'http://tauri.localhost/face/animations',
    );
});


test('browser bridge preserves same-origin behavior without a secret', async () => {
    const bridge = await createCompanionBridge({});
    assert.equal(bridge.native, false);
    assert.equal(bridge.apiBase, '');
    assert.equal(bridge.token, '');
    assert.equal(bridge.wsUrl, null);
    assert.deepEqual(bridge.wsProtocols, []);
    assert.deepEqual(await bridge.hideCompanion(), { hidden: false, reason: 'native-required' });
    assert.deepEqual(await bridge.startWindowDrag(), { started: false, reason: 'native-required' });
});

test('native snapshots and clip bytes use the existing pairing header, never a URL token', async () => {
    const calls = [], revoked = [];
    const source = readFileSync(new URL('../../face/js/companion_native_bridge.js', import.meta.url), 'utf8')
        .replaceAll('export ', '').replace("await import('@tauri-apps/api/core')", 'await Promise.resolve({invoke: mockInvoke})');
    const create = new Function('mockInvoke', 'fetch', 'URL', source + '\nreturn createCompanionBridge;')(
        async () => ({ apiBase: 'http://127.0.0.1:8765', wsUrl: 'ws://127.0.0.1:8765/ws', token: 'test-private' }),
        async (url, options) => { calls.push({ url, options }); return new Response('bytes'); },
        { createObjectURL: () => 'blob:clip', revokeObjectURL: url => revoked.push(url) });
    const bridge = await create({ __TAURI_INTERNALS__: {} });
    await bridge.apiFetch('/api/animation-manifest', { headers: { 'If-None-Match': '"rev"' } });
    const url = await bridge.coreAssetUrl('/face/animations/wave.hash.bvh?v=hash');
    assert.equal(url, 'blob:clip'); bridge.releaseCoreAssetUrl(url); bridge.releaseCoreAssetUrl(url);
    assert.deepEqual(revoked, ['blob:clip']);
    for (const call of calls) {
        assert.equal(call.options.headers.get('X-IRIS-Companion-Token'), 'test-private');
        assert.equal(call.url.includes('test-private'), false);
    }
    assert.equal(calls[0].options.headers.get('If-None-Match'), '"rev"'); bridge.dispose();
});
