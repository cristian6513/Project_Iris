import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
function method(name, fetch) {
    const block = source.match(new RegExp('^    (?:async )?' + name + '\\([^]*?^    \\}', 'm'))?.[0];
    assert.ok(block, name);
    return new Function('fetch', 'return ({' + block.trim() + '})')(fetch)[name];
}
function harness() {
    const requests = [];
    const fetch = (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject }));
    const app = { _asyncSource: 'manual', _asyncSelections: {}, _asyncRuns: [{ id: 'same', title: 'Manual', status: 'running' }],
        _chatAsyncRuns: [{ id: 'same', title: 'Chat', status: 'running' }], _selectedAsyncRunId: 'same',
        _renderAsyncAgents() {}, _renderDashboard() {}, _responseError: async () => 'offline' };
    for (const name of ['_asyncRows', '_setAsyncSource', '_selectAsyncRun', 'loadAsyncRunDetail', 'loadAsyncAgents', '_runAsyncAgentAction', '_runState']) app[name] = method(name, fetch);
    const reply = (index, data) => requests[index].resolve({ ok: true, json: async () => data });
    return { app, requests, reply };
}

test('sources with identical IDs remain isolated even when the old detail arrives last', async () => {
    const h = harness();
    const old = h.app.loadAsyncRunDetail('same');
    h.app._setAsyncSource('chat');
    assert.ok(h.requests[0].options.signal.aborted);
    assert.match(h.requests[1].url, /delegations\/same/);
    h.reply(1, { success: true, run: { id: 'same', status: 'completed', result: 'Chat answer' } });
    await new Promise(setImmediate);
    h.reply(0, { success: true, run: { id: 'same', status: 'completed', result: 'Manual answer' } });
    await old;
    assert.equal(h.app._asyncRunDetail.run.result, 'Chat answer');
    assert.equal(h.app._chatAsyncRuns[0].status, 'completed');
    assert.equal(h.app._asyncRuns[0].status, 'running');
    assert.equal(h.app._asyncRunDetail.run.title, 'Chat');
});

test('each source remembers its own selected task', () => {
    const h = harness();
    h.app._asyncRuns.push({ id: 'manual-2' }); h.app._selectedAsyncRunId = 'manual-2';
    h.app.loadAsyncRunDetail = () => {};
    h.app._setAsyncSource('chat'); assert.equal(h.app._selectedAsyncRunId, 'same');
    h.app._setAsyncSource('manual'); assert.equal(h.app._selectedAsyncRunId, 'manual-2');
});

test('a failed capabilities request does not hide chat delegations', async () => {
    const h = harness(); h.app.loadAsyncRunDetail = async () => {};
    const load = h.app.loadAsyncAgents(true);
    h.requests[0].reject(new Error('Gateway offline'));
    h.reply(1, { success: true, scope: 'alice', manual: { success: true, runs: [] }, chat: { success: true, runs: [{ id: 'chat1' }] } });
    await load;
    assert.equal(h.app._chatAsyncRuns[0].id, 'chat1');
    assert.equal(h.app._asyncRunsError, '');
    assert.equal(h.app._hermesCapabilities.success, false);
});

test('changing identity clears the old chat selection and result', async () => {
    const h = harness(); h.app._asyncSource = 'chat'; h.app._asyncScope = 'alice';
    h.app._asyncRunDetail = { run: { id: 'same', result: 'Old identity' } };
    const load = h.app.loadAsyncAgents(true);
    h.reply(0, { success: true });
    h.reply(1, { success: true, scope: 'bob', manual: { success: true, runs: [] }, chat: { success: true, runs: [] } });
    await load;
    assert.equal(h.app._asyncRunDetail, null);
    assert.equal(h.app._selectedAsyncRunId, '');
    assert.deepEqual(h.app._chatAsyncRuns, []);
});

test('chat source cannot submit manual creation or stop requests', async () => {
    const h = harness(); h.app._asyncSource = 'chat';
    await h.app._runAsyncAgentAction('create');
    await h.app._runAsyncAgentAction('stop', 'same');
    assert.equal(h.requests.length, 0);
});

test('forced refresh during a pending poll schedules one follow-up snapshot', async () => {
    const h = harness(); h.app.loadAsyncRunDetail = async () => {};
    const load = h.app.loadAsyncAgents();
    await h.app.loadAsyncAgents(true); await h.app.loadAsyncAgents(true);
    assert.equal(h.requests.length, 2);
    h.reply(0, { success: true }); h.reply(1, { success: true, manual: { success: true, runs: [] }, chat: { success: true, runs: [] } });
    await load;
    assert.equal(h.requests.length, 4);
    assert.match(h.requests[3].url, /force=true/);
    h.reply(2, { success: true }); h.reply(3, { success: true });
    await new Promise(setImmediate);
    assert.equal(h.app._asyncRunsLoading, false);
});

test('native admission, finalization and stopping count as active until terminal', () => {
    const { app } = harness();
    for (const status of ['started', 'dispatched', 'finalizing', 'running', 'stopping']) assert.equal(app._runState({ status }), 'running', status);
    assert.equal(app._runState({ status: 'completed' }), 'done');
    assert.equal(app._runState({ status: 'stalled' }), 'error');
});
