import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { desktopStatus } from '../../face/js/os_desktop_status.js';

const connected = { runtimeChecked: true, runtimeReady: true };
test('desktop status distinguishes checking, disconnected, idle and confirmed active work', () => {
    assert.match(desktopStatus().text, /Comprobando/);
    assert.equal(desktopStatus({ runtimeChecked: true }).text, 'Sin conexión local');
    assert.equal(desktopStatus(connected).text, 'En espera');
    for (const [state, word] of [['thinking','Pensando'],['speaking','Respondiendo'],['tool','herramienta'],['skill','habilidad'],
        ['subagent','tareas'],['job','rutina'],['waiting','respuesta'],['done','terminado'],['error','atención']]) {
        assert.ok(desktopStatus({ ...connected, state }).text.includes(word));
    }
    assert.equal(desktopStatus({ ...connected, runtimeReady: false, state: 'thinking' }).tone, 'offline');
    assert.equal(desktopStatus({ ...connected, state: 'unknown' }).text, 'En espera');
});
test('arming wake never claims a user recording; only confirmed capture/transcription does', () => {
    assert.equal(desktopStatus({ ...connected, wake: { active: true, state: 'listening' } }).text, 'En espera');
    assert.equal(desktopStatus({ ...connected, manualVoice: null, wake: null }).text, 'En espera');
    for (const owner of ['wake', 'manualVoice']) {
        assert.equal(desktopStatus({ ...connected, [owner]: { state: 'recording' } }).text, 'Escuchando…');
        assert.equal(desktopStatus({ ...connected, [owner]: { state: 'transcribing' } }).text, 'Transcribiendo…');
    }
});
test('the real header renderer uses runtime readiness, plain text and stable live-region updates', async () => {
    const source = await readFile(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
    const method = source.match(/^    _renderDesktopStatus\([^]*?^    \}/m)[0];
    const render = new Function('desktopStatus', `return ({${method}})._renderDesktopStatus`)(desktopStatus);
    let text = '', writes = 0;
    const label = { get textContent() { return text; }, set textContent(value) { text = value; writes++; } };
    const root = { dataset: {}, querySelector: () => label };
    const shell = { state: 'idle', panel: { querySelector: () => root }, _runtimeStatusLoadedAt: 0,
        currentTool: '<private>', currentDetail: 'personal data not for the header' };
    render.call(shell); assert.match(text, /Comprobando/);
    shell._runtimeStatusLoadedAt = 1; shell._runtimeStatus = { status: 'running' }; render.call(shell);
    assert.equal(text, 'En espera'); const settled = writes; render.call(shell); assert.equal(writes, settled);
    shell.state = 'thinking'; render.call(shell); assert.equal(text, 'Pensando…');
    shell._runtimeStatusError = 'unavailable'; render.call(shell); assert.equal(text, 'Sin conexión local');
    assert.equal(root.dataset.tone, 'offline'); assert.equal(root.title, text);
    assert.doesNotMatch(text, /private|personal/);
});

test('the real panel refresh updates the header even while Settings is open, without loading the dashboard', async () => {
    const source = await readFile(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
    const extract = name => source.match(new RegExp(`^    ${name}\\([^]*?^    \\}`, 'm'))[0];
    const meta = { idle: { accent: '#abcdef', label: 'En espera' }, thinking: { accent: '#abcdef', label: 'Pensando' } };
    const methods = new Function('desktopStatus', 'STATE_META', `return ({${['_renderPanel','_renderDesktopStatus'].map(extract).join(',')}})`)(desktopStatus, meta);
    const label = { textContent: '' }, root = { dataset: {}, querySelector: () => label };
    let refreshes = 0;
    const shell = { ...methods, _open: true, _activeTab: 'settings', state: 'thinking', timeline: [],
        _runtimeStatusLoadedAt: 1, _runtimeStatus: { status: 'running' },
        panel: { style: { setProperty() {} }, querySelector: selector => selector === '.os-top-status' ? root : null },
        _renderVisibleAppSurfaces() { assert.equal(this._activeTab, 'settings'); refreshes++; },
        _renderDashboard() { assert.fail('Settings must not load the dashboard'); } };
    shell._renderPanel(); assert.equal(label.textContent, 'Pensando…'); assert.equal(refreshes, 1);
    shell._open = false; shell.state = 'idle'; shell._renderPanel(); assert.equal(label.textContent, 'Pensando…');
});
