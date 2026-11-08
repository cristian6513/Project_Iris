import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { RoomSurfaceVisibility } from '../../face/js/room_surface_visibility.js';
import { normalizeRoomText, ROOM_STATUS_LABELS } from '../../face/js/room_labels.js';
import { SidePanel } from '../../face/js/panel.js';

function fixture() {
    const root = { activeElement: null };
    const node = (parent = null, attrs = {}) => ({
        parent, isConnected: true, inert: false, attributes: new Map(Object.entries(attrs)),
        getAttribute(key) { return this.attributes.get(key) ?? null; },
        setAttribute(key, value) { this.attributes.set(key, value); },
        removeAttribute(key) { this.attributes.delete(key); },
        contains(other) { return other === this || Boolean(other?.parent && this.contains(other.parent)); },
        focus() { root.activeElement = this; }, blur() { root.activeElement = root.body; },
    });
    root.body = node(); root.activeElement = root.body;
    const quick = node(root.body), panel = node(root.body), upload = node(root.body, { 'aria-hidden': 'false' });
    const prehidden = node(root.body, { 'aria-hidden': 'true' }); prehidden.inert = true;
    const trigger = node(quick), input = node(panel), workbench = node(root.body), dock = node(workbench), elsewhere = node(root.body);
    workbench.querySelector = () => dock;
    root.querySelectorAll = () => [quick, panel, upload, prehidden];
    root.getElementById = () => trigger;
    return { root, quick, panel, upload, prehidden, trigger, input, workbench, dock, elsewhere, guard: new RoomSurfaceVisibility(root) };
}

test('workbench removes room controls from interaction/accessibility and hands focus to the dock first', () => {
    const f = fixture(); f.input.focus(); f.input.value = 'Un borrador sin enviar';
    f.guard.hide(f.workbench);
    assert.equal(f.root.activeElement, f.dock);
    for (const item of [f.quick, f.panel, f.upload, f.prehidden]) {
        assert.equal(item.inert, true); assert.equal(item.getAttribute('aria-hidden'), 'true');
    }
    assert.equal(f.workbench.inert, false); assert.equal(f.root.body.inert, false);
    assert.equal(f.input.value, 'Un borrador sin enviar');
});

test('closing/reopening restores each original state, including deliberately hidden/inert controls', () => {
    const f = fixture(); f.trigger.focus();
    f.guard.hide(f.workbench); f.guard.hide(f.workbench); // Repeated open must not save the hidden state.
    f.guard.restore(); f.guard.restore();
    assert.equal(f.root.activeElement, f.trigger);
    assert.equal(f.quick.inert, false); assert.equal(f.quick.getAttribute('aria-hidden'), null);
    assert.equal(f.upload.getAttribute('aria-hidden'), 'false');
    assert.equal(f.prehidden.inert, true); assert.equal(f.prehidden.getAttribute('aria-hidden'), 'true');
    f.guard.hide(f.workbench); f.guard.restore(); assert.equal(f.quick.inert, false);
});

test('panel refresh does not bypass its inert parent and restoring never steals another dialog focus', () => {
    const f = fixture(); f.guard.hide(f.workbench);
    f.input.inert = false; f.input.setAttribute('aria-hidden', 'false');
    assert.equal(f.panel.inert, true); // Section-level changes cannot expose a hidden room.
    f.elsewhere.focus(); f.guard.restore(); assert.equal(f.root.activeElement, f.elsewhere);
});

test('closing with a removed room trigger uses the visible OS entry point', () => {
    const f = fixture(); f.input.focus(); f.guard.hide(f.workbench); f.input.isConnected = false;
    f.guard.restore(); assert.equal(f.root.activeElement, f.trigger);
});

test('the real app closes the panel, hides room controls and restores them when leaving the workbench', () => {
    const source = readFileSync(new URL('../../face/js/app.js', import.meta.url), 'utf8');
    const method = name => new Function('RoomSurfaceVisibility', source.match(new RegExp(`    ${name}\\(\\) \\{([^]*?)\\n    \\}`))[1]);
    const calls = [], app = { inspectorMode: false, panel: { close: () => calls.push('close-panel') }, activityCanvas: { panel: {} },
        _roomSurfaceVisibility: { hide: () => calls.push('hide-room'), restore: () => calls.push('restore-room') } };
    const old = globalThis.document;
    globalThis.document = { body: { classList: { add: () => calls.push('show-os'), remove: () => calls.push('show-room') } } };
    try { method('_enterWorkbenchFocusMode').call(app, RoomSurfaceVisibility); method('_exitWorkbenchFocusMode').call(app, RoomSurfaceVisibility); }
    finally { globalThis.document = old; }
    assert.deepEqual(calls, ['close-panel', 'hide-room', 'show-os', 'show-room', 'restore-room']);
});

test('room normalization translates authored labels without modifying the draft, selected room or protocol values', () => {
    const nodes = new Map();
    const get = key => { if (!nodes.has(key)) nodes.set(key, { textContent: 'old' }); return nodes.get(key); };
    const room = { dataset: { room: 'sakura' }, textContent: 'old' };
    const effort = { value: 'high', textContent: 'old' }, input = { value: 'Mi mensaje de prueba' };
    normalizeRoomText({ querySelector: get, querySelectorAll: key => key === '.room-btn' ? [room] : [effort], getElementById: () => input }, '0.9.2');
    assert.equal(get('#room-selector .room-sel-label').textContent, 'Habitación');
    assert.equal(get('#btn-memory .btn-label').textContent, 'Memoria');
    assert.equal(get('#panel-brain .module-title').textContent, 'Estado de Iris');
    assert.equal(get('.IRIS-version').textContent, 'v0.9.2');
    assert.equal(room.textContent, 'Sakura'); assert.equal(room.dataset.room, 'sakura');
    assert.equal(effort.textContent, 'Profundo'); assert.equal(effort.value, 'high');
    assert.equal(input.value, 'Mi mensaje de prueba');
});

test('live room statuses use Spanish while retaining the incoming state for vision', () => {
    const source = readFileSync(new URL('../../face/js/app.js', import.meta.url), 'utf8');
    const body = source.match(/    _updateStatus\(status\) \{([^]*?)\n    \}/)[1];
    const method = new Function('status', 'document', 'ROOM_STATUS_LABELS', body);
    const label = {}, app = {};
    for (const [state, expected] of Object.entries(ROOM_STATUS_LABELS)) {
        method.call(app, state, { querySelector: key => key === '.status-text' ? label : null }, ROOM_STATUS_LABELS);
        assert.equal(label.textContent, expected); assert.equal(app._visionStatus, state);
    }
    method.call(app, 'unexpected', { querySelector: () => label }, ROOM_STATUS_LABELS);
    assert.equal(label.textContent, 'Sin estado');
});

test('reasoning updates do not bring back English badges or change the saved effort', () => {
    const icon = {}, pill = { querySelector: () => icon, dataset: {} };
    for (const [effort, label] of Object.entries({ none: 'Rápido', low: 'Bajo', medium: 'Medio', high: 'Profundo' })) {
        SidePanel.prototype._applyReasoningPillState.call({ reasoningPill: pill, _reasoningEffort: effort });
        assert.equal(icon.textContent, label); assert.equal(pill.dataset.effort, effort);
    }
});
