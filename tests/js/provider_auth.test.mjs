import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../../wizard/ui/provider_auth.js', import.meta.url), 'utf8');
function setup(fetchImpl) {
    const context = vm.createContext({ setTimeout, clearTimeout, AbortSignal });
    vm.runInContext(source, context);
    const map = new Map();
    const storage = { getItem: key => map.get(key), setItem: (key, value) => map.set(key, value), removeItem: key => map.delete(key) };
    let selected = 'openai';
    const changes = [], connected = [];
    const options = { getProvider: () => selected, onChange: state => changes.push(state),
        onAuthenticated: id => connected.push(id), storage, fetchImpl, interval: 100000 };
    const controller = new context.IrisProviderAuth(options);
    return { controller, changes, connected, map, options, Class: context.IrisProviderAuth, select: p => { selected = p; } };
}
const id = 'abcdefghijklmnopqrstuvwx';
const pending = { provider: 'openai', session_id: id, status: 'pending', url: 'https://auth.openai.com/codex/device', user_code: 'TEST-1234' };
const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });

test('reload resumes existing operation without starting another login', async () => {
    const calls = [];
    const env = setup(async (url, options) => {
        calls.push(options.method || 'GET');
        return response(options.method === 'POST' ? pending : { ...pending, status: 'approved' });
    });
    await env.controller.start('openai');
    env.controller.stopPoll();
    const reloaded = new env.Class(env.options);
    await reloaded.resume('openai');
    assert.deepEqual(calls, ['POST', 'GET']);
    assert.deepEqual(env.connected, ['openai']);
    assert.equal(env.map.size, 0);
});

test('switching provider while POST is in flight cancels late session', async () => {
    let finish, entered;
    const started = new Promise(resolve => { entered = resolve; });
    const calls = [];
    const env = setup(async (url, options) => {
        calls.push(options.method);
        if (options.method === 'POST') {
            entered();
            return new Promise(resolve => { finish = () => resolve(response(pending)); });
        }
        return response({ ...pending, status: 'cancelled' });
    });
    const login = env.controller.start('openai');
    await started;
    env.select('nous');
    await env.controller.cancel();
    finish();
    await login;
    assert.deepEqual(calls, ['POST', 'DELETE']);
    assert.equal(env.connected.length, 0);
    assert.equal(env.map.size, 0);
});

test('an old poll response cannot authenticate the newly selected provider', async () => {
    let finish;
    const env = setup(async (url, options) => {
        if (options.method === 'POST') return response(pending);
        if (options.method === 'DELETE') return response({ ...pending, status: 'cancelled' });
        return new Promise(resolve => { finish = () => resolve(response({ ...pending, status: 'approved' })); });
    });
    await env.controller.start('openai');
    clearTimeout(env.controller.timer);
    const poll = env.controller.poll(env.controller.generation);
    env.select('nous');
    await env.controller.cancel();
    finish();
    await poll;
    assert.equal(env.connected.length, 0);
    assert.equal(env.map.size, 0);
});

test('a network interruption preserves the operation for rechecking', async () => {
    let fail = true;
    const env = setup(async (url, options) => {
        if (options.method === 'POST') return response(pending);
        if (fail) throw new Error('offline');
        return response({ ...pending, status: 'approved' });
    });
    await env.controller.start('openai');
    await env.controller.recheck();
    assert.equal(env.changes.at(-1).status, 'disconnected');
    assert.equal(env.map.size, 1);
    fail = false;
    await env.controller.recheck();
    assert.deepEqual(env.connected, ['openai']);
});

test('failed cancellation does not start another login', async () => {
    let posts = 0;
    const env = setup(async (url, options) => {
        if (options.method === 'POST') { posts++; return response(pending); }
        throw new Error('offline');
    });
    await env.controller.start('openai');
    await env.controller.start('nous');
    assert.equal(posts, 1);
    assert.equal(env.changes.at(-1).status, 'disconnected');
    assert.equal(env.map.size, 1);
});

test('session storage includes no device codes or authorization URL', async () => {
    const env = setup(async () => response(pending));
    await env.controller.start('openai');
    env.controller.stopPoll();
    const saved = [...env.map.values()][0];
    assert.equal(saved.includes('TEST-1234'), false);
    assert.equal(saved.includes('https://'), false);
});

test('cancellation reports approval if the provider finished first', async () => {
    const env = setup(async (_, options) => response(options.method === 'POST'
        ? pending : { ...pending, status: 'approved' }));
    await env.controller.start('openai');
    await env.controller.cancel();
    assert.equal(env.changes.at(-1).status, 'approved');
    assert.deepEqual(env.connected, ['openai']);
    assert.equal(env.map.size, 0);
});

test('starting after reload can cancel a saved operation for another provider', async () => {
    const calls = [];
    const env = setup(async (url, options) => {
        calls.push(options.method);
        return response(options.method === 'DELETE' ? { ...pending, status: 'cancelled' }
            : { ...pending, provider: 'nous', status: 'approved' });
    });
    env.map.set(env.controller.key, JSON.stringify({ provider: 'openai', session_id: id }));
    env.select('nous');
    await env.controller.start('nous');
    assert.deepEqual(calls, ['DELETE', 'POST']);
    assert.deepEqual(env.connected, ['nous']);
    assert.equal(env.map.size, 0);
});

test('an earlier cancellation cannot erase a newer login', async () => {
    const deletes = [];
    const env = setup(async (_, options) => {
        if (options.method === 'POST') return response(pending);
        return new Promise(resolve => deletes.push(() => resolve(response({ ...pending, status: 'cancelled' }))));
    });
    await env.controller.start('openai');
    const older = env.controller.cancel({ silent: true });
    const newer = env.controller.start('openai');
    assert.equal(deletes.length, 2);
    deletes[1]();
    await newer;
    deletes[0]();
    assert.equal(await older, false);
    assert.equal(env.controller.session.status, 'pending');
    assert.equal(env.map.size, 1);
    env.controller.stopPoll();
});

test('cancelling before POST returns unlocks the UI and cancels the late worker', async () => {
    let finish, entered;
    const started = new Promise(resolve => { entered = resolve; });
    const calls = [];
    const env = setup(async (_, options) => {
        calls.push(options.method);
        if (options.method === 'POST') {
            entered();
            return new Promise(resolve => { finish = () => resolve(response(pending)); });
        }
        return response({ ...pending, status: 'cancelled' });
    });
    const login = env.controller.start('openai');
    await started;
    await env.controller.cancel();
    assert.equal(env.changes.at(-1).status, 'cancelled');
    finish();
    await login;
    assert.deepEqual(calls, ['POST', 'DELETE']);
    assert.equal(env.changes.at(-1).status, 'cancelled');
    assert.equal(env.map.size, 0);
});

test('a packaged engine error is shown as sent and never polled as a login (A08.6)', async () => {
    const calls = [];
    const packagedError = {
        provider: 'openai', session_id: id, status: 'error', code: 'not_installed',
        message: 'El motor incluido no está disponible. Repara Iris para conectar tu cuenta.',
    };
    const env = setup(async (_, options) => { calls.push(options.method || 'GET'); return response(packagedError); });
    await env.controller.start('openai');
    assert.deepEqual(calls, ['POST']);
    assert.equal(env.changes.at(-1).message, packagedError.message);
    assert.equal(env.connected.length, 0);
    assert.equal(env.map.size, 0);
    assert.equal(env.controller.timer, null);
});

test('denied or expired accounts stay unauthenticated and leave nothing to resume', async () => {
    for (const status of ['denied', 'expired', 'external']) {
        const env = setup(async () => response({ ...pending, status, url: undefined, user_code: undefined }));
        await env.controller.start('openai');
        assert.equal(env.changes.at(-1).status, status);
        assert.equal(env.connected.length, 0);
        assert.equal(env.map.size, 0);
    }
});
