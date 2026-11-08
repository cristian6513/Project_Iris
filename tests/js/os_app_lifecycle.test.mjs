import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { OS_APP_CATALOG } from '../../face/js/os_app_catalog.js';
import { createAppRegistry } from '../../face/js/os_app_registry.js';

const OS_APPS = createAppRegistry(OS_APP_CATALOG);

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}
function requestHarness(kind) {
    const requests = [];
    const fetch = (url, options) => {
        const pending = deferred(); requests.push({ url, options, ...pending }); return pending.promise;
    };
    const state = { _renderAsyncAgents() {}, _renderRoutines() {}, _responseError: async () => 'HTTP error' };
    const names = kind === 'agent'
        ? { method: 'loadAsyncRunDetail', selected: '_selectedAsyncRunId', data: '_asyncRunDetail',
            loading: '_asyncRunDetailLoading', error: '_asyncRunDetailError' }
        : { method: 'loadCronJobHistory', selected: '_selectedCronJobId', data: '_cronJobOutputs',
            loading: '_cronJobOutputsLoading', error: '_cronJobOutputsError' };
    const load = method(names.method, { fetch });
    const select = id => { state[names.selected] = id; return load.call(state, id); };
    const response = id => ({ ok: true, json: async () => ({ success: true, id, outputs: [id] }) });
    return { state, requests, names, select, response };
}

for (const kind of ['agent', 'routine']) {
    test(kind + ': latest selection wins even if the old response arrives last', async () => {
        const h = requestHarness(kind);
        const a = h.select('a'); const b = h.select('b');
        assert.equal(h.requests.length, 2);
        assert.equal(h.requests[0].options.signal.aborted, true);
        h.requests[1].resolve(h.response('b')); await b;
        const expected = kind === 'agent' ? { success: true, id: 'b', outputs: ['b'] } : ['b'];
        assert.deepEqual(h.state[h.names.data], expected);
        h.requests[0].resolve(h.response('a')); await a;
        assert.deepEqual(h.state[h.names.data], expected);
        assert.equal(h.state[h.names.loading], false);
    });
    test(kind + ': obsolete errors do not clear the new loading state or publish an error', async () => {
        const h = requestHarness(kind);
        const a = h.select('a'); const b = h.select('b');
        assert.equal(h.requests.length, 2);
        h.requests[0].reject(new Error('old request failed')); await a;
        assert.equal(h.state[h.names.loading], true);
        assert.equal(h.state[h.names.error], '');
        h.requests[1].resolve(h.response('b')); await b;
        assert.equal(h.state[h.names.loading], false);
    });
    test(kind + ': repeated reads of the same pending selection are coalesced', async () => {
        const h = requestHarness(kind); const a = h.select('a'); await h.select('a');
        assert.equal(h.requests.length, 1);
        h.requests[0].resolve(h.response('a')); await a;
    });
}

test('routine history cache belongs to the fetched job, not the current selection', async () => {
    const h = requestHarness('routine'); const a = h.select('a');
    h.requests[0].resolve(h.response('a')); await a;
    const b = h.select('b'); assert.equal(h.requests.length, 2);
    h.requests[1].resolve(h.response('b')); await b;
    await h.select('b'); assert.equal(h.requests.length, 2);
});

const source = readFileSync(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
function method(name, deps = {}) {
    const block = source.match(new RegExp('^    (?:async )?' + name + '\\([^]*?^    \\}', 'm'))?.[0];
    assert.ok(block, name + ' must exist');
    return new Function(...Object.keys(deps), 'return ({' + block.trim() + '})')(...Object.values(deps))[name];
}
function harness() {
    const document = { visibilityState: 'visible' };
    const timers = new Map();
    let next = 0;
    const deps = { document, setInterval: fn => { timers.set(++next, fn); return next; },
        clearInterval: id => timers.delete(id) };
    const state = { _open: true, _activeTab: 'desktop', _desktopMode: 'home', _osWindows: new Map(),
        missionReads: 0, agentReads: 0, loadMissions() { this.missionReads++; },
        loadAsyncAgents() { this.agentReads++; } };
    for (const name of ['_hasVisibleAppSurface', '_hasVisibleAsyncAgentsSurface', '_syncMissionPolling',
        '_startMissionPolling', '_stopMissionPolling', '_syncAsyncAgentsPolling', '_startAsyncAgentsPolling',
        '_stopAsyncAgentsPolling']) state[name] = method(name, deps);
    const sync = () => { state._syncMissionPolling(); state._syncAsyncAgentsPolling(); };
    const window = name => { const record = { el: { isConnected: true }, minimized: false };
        state._osWindows.set(name, record); return record; };
    return { state, document, timers, sync, window };
}

test('app visibility excludes minimized, disconnected and hidden parent surfaces', () => {
    const h = harness(); const record = h.window('missions');
    assert.equal(h.state._hasVisibleAppSurface('missions'), true);
    for (const [target, key, value] of [[record, 'minimized', true], [record.el, 'isConnected', false],
        [h.state, '_open', false], [h.state, '_activeTab', 'settings'],
        [h.state, '_desktopMode', 'dashboard'], [h.document, 'visibilityState', 'hidden']]) {
        const previous = target[key]; target[key] = value;
        assert.equal(h.state._hasVisibleAppSurface('missions'), false, key);
        target[key] = previous;
    }
    h.state._osWindows.delete('missions'); h.state._activeTab = 'missions';
    assert.equal(h.state._hasVisibleAppSurface('missions'), true);
});

test('mission and agent windows each poll once and stop when hidden', () => {
    const h = harness(); const mission = h.window('missions'); h.window('agents');
    h.sync(); h.sync(); assert.equal(h.timers.size, 2);
    for (const tick of [...h.timers.values()]) tick();
    assert.equal(h.state.missionReads, 1); assert.equal(h.state.agentReads, 1);
    mission.minimized = true; h.sync(); assert.equal(h.timers.size, 1);
    h.document.visibilityState = 'hidden'; h.sync(); assert.equal(h.timers.size, 0);
    h.document.visibilityState = 'visible'; mission.minimized = false;
    h.sync(); assert.equal(h.timers.size, 2);
    h.state._open = false; h.sync(); assert.equal(h.timers.size, 0);
});

test('periodic callbacks recheck visibility before querying Hermes', () => {
    const h = harness(); h.window('missions'); h.window('agents'); h.sync();
    h.state._desktopMode = 'dashboard';
    for (const tick of [...h.timers.values()]) tick();
    assert.equal(h.state.missionReads + h.state.agentReads, 0);
    assert.equal(h.timers.size, 0);
});

test('reopening, minimizing, restoring and closing an existing window resync both pollers', () => {
    const h = harness(); const record = h.window('missions');
    record.el.classList = { add() {}, remove() {}, contains: () => false };
    Object.assign(h.state, { panel: { dataset: {}, querySelectorAll: () => [] },
        _windowLayout: {}, _windowViewAnchors: new Map(), _saveWindowLayout() {},
        _focusWindow() {}, _renderDesktopHome() {}, _captureWindowLayout() {},
        _restoreDesktopFocus() {}, _loadWindowApp() {}, _setDesktopMode() {}, _syncAppPolling: h.sync });
    record.el.remove = () => { record.el.isConnected = false; };
    record.minimized = true;
    method('_openWindow', { document: h.document, OS_WINDOW_APPS: { missions: {} } }).call(h.state, 'missions');
    assert.equal(h.timers.size, 1);
    method('_minimizeWindow').call(h.state, 'missions'); assert.equal(h.timers.size, 0);
    method('_restoreWindow').call(h.state, 'missions'); assert.equal(h.timers.size, 1);
    method('_closeWindow').call(h.state, 'missions'); assert.equal(h.timers.size, 0);
});

test('page visibility restores polling and deferred notifications only for visible windows', () => {
    const h = harness(); h.window('missions'); h.window('agents'); h.window('notifications');
    let notificationReads = 0;
    let inboxReads = 0;
    Object.assign(h.state, { _syncAppPolling: h.sync, _renderPanel() {}, _notificationsTab: 'vision',
        _loadNotificationsTab: method('_loadNotificationsTab'),
        _notificationApp: { load() { notificationReads++; } }, _inboxApp: { load() { inboxReads++; } } });
    const change = method('_handleAppVisibilityChange', { document: h.document });
    h.sync(); h.document.visibilityState = 'hidden'; change.call(h.state);
    assert.equal(h.timers.size, 0); assert.equal(notificationReads, 0);
    h.document.visibilityState = 'visible'; change.call(h.state);
    assert.equal(h.timers.size, 2); assert.equal(notificationReads, 1);
    assert.equal(h.state.missionReads, 1); assert.equal(h.state.agentReads, 1);
    h.state._notificationsTab = 'inbox'; change.call(h.state); // F07: the window shows the general inbox
    assert.equal(notificationReads, 1); assert.equal(inboxReads, 1);
    h.state._activeTab = 'settings'; change.call(h.state);
    assert.equal(h.timers.size, 0); assert.equal(notificationReads, 1); assert.equal(inboxReads, 1);
});

test('Automatizaciones learns each open, minimize, restore, close and page visibility change', () => {
    const h = harness(); const record = h.window('automations');
    const seen = [];
    record.el.classList = { add() {}, remove() {}, contains: () => false };
    record.el.remove = () => { record.el.isConnected = false; };
    Object.assign(h.state, { panel: { dataset: {}, querySelectorAll: () => [] },
        _windowLayout: {}, _windowViewAnchors: new Map(), _saveWindowLayout() {},
        _focusWindow() {}, _renderDesktopHome() {}, _captureWindowLayout() {},
        _restoreDesktopFocus() {}, _loadWindowApp() {}, _setDesktopMode() {}, _renderPanel() {},
        _syncCameraPreview() {},
        // F09.2: the app host relays what the shell reports to the Automatizaciones instance.
        _appHost: { state: () => 'ready', setVisibility: (id, visible) => { if (id === 'automations') seen.push(visible); } } });
    h.state._syncAppPolling = method('_syncAppPolling');
    h.state._syncModuleVisibility = method('_syncModuleVisibility', { OS_APPS });
    record.minimized = true;
    method('_openWindow', { document: h.document, OS_WINDOW_APPS: { automations: {} } }).call(h.state, 'automations');
    method('_minimizeWindow').call(h.state, 'automations');
    method('_restoreWindow').call(h.state, 'automations');
    const change = method('_handleAppVisibilityChange', { document: h.document });
    h.document.visibilityState = 'hidden'; change.call(h.state);
    h.document.visibilityState = 'visible'; change.call(h.state);
    method('_closeWindow').call(h.state, 'automations');
    assert.deepEqual(seen, [true, false, true, false, true, false]);
});

test('restoring saved minimized windows does not fetch their apps', () => {
    const h = harness(); const window = h.window('missions'); window.minimized = true;
    h.state._loadApp = method('_loadApp', { OS_APPS });
    h.state._appAdapters = method('_createAppAdapters').call(h.state);
    method('_loadWindowApp').call(h.state, 'missions'); assert.equal(h.state.missionReads, 0);
    window.minimized = false;
    method('_loadWindowApp').call(h.state, 'missions'); assert.equal(h.state.missionReads, 1);
});

test('choosing a historical delivery cancels the previous routine history request', async () => {
    const h = requestHarness('routine'); const a = h.select('a');
    h.state._resetCronJobHistory = method('_resetCronJobHistory');
    method('_selectCronOutput').call(h.state, 'delivery', 'b');
    assert.equal(h.requests[0].options.signal.aborted, true);
    assert.equal(h.state._cronJobOutputsLoading, false);
    h.requests[0].resolve(h.response('a')); await a;
    assert.deepEqual(h.state._cronJobOutputs, []);
    assert.equal(h.state._selectedCronOutputId, 'delivery');
});

for (const cached of [false, true]) {
    test(`Gateway Doctor window loads logs with ${cached ? 'cached' : 'fresh'} status and coalesces reads`, async () => {
        const h = harness(); const window = h.window('doctor'); const logs = deferred();
        let logReads = 0, statusReads = 0;
        const fetch = async url => {
            if (url.startsWith('/api/gateway-logs')) { logReads++; return logs.promise; }
            statusReads++; return { ok: true, json: async () => ({ running: true }) };
        };
        Object.assign(h.state, { _renderGatewayManager() {}, _renderDashboard() {},
            _gatewayLoadedAt: cached ? Date.now() : 0, _responseError: async () => 'HTTP error' });
        for (const name of ['loadGatewayStatus', 'loadGatewayLogs', '_loadVisibleGatewayLogs']) {
            h.state[name] = method(name, { fetch });
        }
        await h.state.loadGatewayStatus(); await h.state.loadGatewayStatus();
        assert.equal(statusReads, cached ? 0 : 1); assert.equal(logReads, 1);
        logs.resolve({ ok: true, json: async () => ({ lines: ['ready'] }) });
        await new Promise(resolve => setImmediate(resolve));
        assert.deepEqual(h.state._gatewayLogs, { lines: ['ready'] });
        assert.equal(h.state._gatewayLogsLoading, false);
        window.minimized = true; await h.state.loadGatewayStatus(true); assert.equal(logReads, 1);
    });
    test(`Routines window loads selected history with ${cached ? 'cached' : 'fresh'} jobs`, async () => {
        const h = harness(); const window = h.window('routines'); let histories = 0;
        const fetch = async () => ({ ok: true, json: async () => ({ jobs: [{ id: 'a' }] }) });
        Object.assign(h.state, { _selectedCronJobId: 'a', _cronLoadedAt: cached ? Date.now() : 0,
            _cronJobId: job => job.id, _renderRoutines() {}, _renderDashboard() {}, loadCronOutputs() {},
            loadCronJobHistory(id) { assert.equal(id, 'a'); histories++; } });
        h.state._loadSelectedCronHistory = method('_loadSelectedCronHistory');
        await method('loadRoutines', { fetch }).call(h.state);
        assert.equal(histories, 1);
        window.minimized = true; await method('loadRoutines', { fetch }).call(h.state);
        assert.equal(histories, 1);
    });
}

test('refreshing jobs discards history for a removed job and selects a remaining job', async () => {
    const h = harness(); h.window('routines');
    Object.assign(h.state, { _selectedCronJobId: 'removed', _cronJobOutputs: ['old'],
        _cronJobOutputsLoadedId: 'removed', _cronJobOutputsLoadedAt: Date.now(),
        _cronJobId: job => job.id, _renderRoutines() {}, _renderDashboard() {}, loadCronOutputs() {},
        loadCronJobHistory(id) { assert.equal(id, 'new'); } });
    for (const name of ['_loadSelectedCronHistory', '_resetCronJobHistory']) h.state[name] = method(name);
    const fetch = async () => ({ ok: true, json: async () => ({ jobs: [{ id: 'new' }] }) });
    await method('loadRoutines', { fetch }).call(h.state);
    assert.equal(h.state._selectedCronJobId, 'new'); assert.deepEqual(h.state._cronJobOutputs, []);
    assert.equal(h.state._cronJobOutputsLoadedAt, 0); assert.equal(h.state._cronJobOutputsLoadedId, '');
});
