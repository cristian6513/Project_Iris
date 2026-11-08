import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { RoomAvatarZoom, AVATAR_ZOOM_KEY, bindAvatarZoomControls } from '../../face/js/room_avatar_zoom.js';

const memory = () => {
    const entries = new Map();
    return { getItem: key => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value) };
};
const camera = () => ({ zoom: 1, fov: 45, position: { x: 0.34, y: 1.38, z: 4.95 }, updates: 0,
    updateProjectionMatrix() { this.updates++; } });

// These are camera/view contracts, not the OS webcam or capture APIs.
test('the slider magnifies immediately without moving the camera or avatar', () => {
    const view = new RoomAvatarZoom({ storage: memory }), cam = camera();
    view.attachCamera(cam);
    assert.equal(cam.zoom, 1);
    const initial = { ...cam.position };
    view.setValue(50);
    assert.equal(cam.zoom, 1.75);
    view.setValue(100);
    assert.equal(cam.zoom, 2.5);
    assert.deepEqual(cam.position, initial);
    assert.equal(cam.fov, 45);
    assert.equal(cam.updates, 3);
});

test('zoom is bounded; a corrupt preference cannot destroy the projection', () => {
    const store = memory(), view = new RoomAvatarZoom({ storage: () => store }), cam = camera();
    view.attachCamera(cam);
    for (const [input, expected] of [[-500, 1], [999999, 2.5], [NaN, 1], [Infinity, 1], ['invalid', 1], [null, 1]]) {
        view.setValue(input);
        assert.equal(cam.zoom, expected);
    }
    store.setItem(AVATAR_ZOOM_KEY, 'Infinity');
    assert.equal(new RoomAvatarZoom({ storage: () => store }).snapshot().value, 0);
});

test('Explore uses an unzoomed camera and returns to the saved close view', () => {
    const view = new RoomAvatarZoom({ storage: memory }), cam = camera();
    view.attachCamera(cam); view.setValue(80);
    const close = cam.zoom;
    view.setExploring(true);
    assert.equal(cam.zoom, 1);
    assert.equal(view.setValue(0), false);
    assert.equal(view.snapshot().value, 80);
    cam.fov = 42; cam.position.x = -7;
    view.setExploring(false);
    assert.equal(cam.zoom, close);
    assert.equal(cam.fov, 42); assert.equal(cam.position.x, -7);
});

test('reload and reset retain just the room preference; unavailable storage is usable', () => {
    const store = memory(), view = new RoomAvatarZoom({ storage: () => store });
    view.setValue(70);
    const reopened = new RoomAvatarZoom({ storage: () => store }), cam = camera();
    reopened.attachCamera(cam); assert.equal(cam.zoom, 2.05);
    reopened.setValue(0); assert.equal(cam.zoom, 1);
    assert.equal(new RoomAvatarZoom({ storage: () => store }).snapshot().value, 0);
    const blocked = new RoomAvatarZoom({ storage: () => { throw Error('blocked'); } });
    blocked.attachCamera(cam); blocked.setValue(100);
    assert.equal(cam.zoom, 2.5); assert.equal(blocked.snapshot().persisted, false);
    blocked.setExploring(true); blocked.setExploring(false); assert.equal(cam.zoom, 2.5);
});

function controls(getController) {
    const slider = Object.assign(new EventTarget(), { value: '0', setAttribute(key, value) { this[key] = value; } });
    const reset = new EventTarget(), note = {};
    const nodes = { '[data-avatar-zoom]': slider, '[data-avatar-zoom-reset]': reset, '[data-avatar-zoom-note]': note };
    const binding = bindAvatarZoomControls({ querySelector: key => nodes[key] }, getController);
    return { slider, reset, note, binding,
        move(value) { slider.value = String(value); slider.dispatchEvent(new Event('input')); } };
}

test('lazy settings controls act on the current camera, including reset and Explore availability', () => {
    let view = null;
    const ui = controls(() => view);
    assert.equal(ui.slider.disabled, true);
    view = new RoomAvatarZoom({ storage: () => null });
    const cam = camera(); view.attachCamera(cam); ui.binding.refresh();
    assert.equal(ui.slider.disabled, false);
    ui.move(80); assert.equal(cam.zoom, 2.2); assert.match(ui.slider['aria-valuetext'], /2,2/);
    assert.match(ui.note.textContent, /Solo esta sesión/);
    view.setExploring(true);
    assert.equal(ui.slider.disabled, true); assert.equal(ui.reset.disabled, true);
    assert.match(ui.note.textContent, /Sal de Explorar/);
    view.setExploring(false); assert.equal(ui.slider.value, '80');
    ui.reset.dispatchEvent(new Event('click')); assert.equal(cam.zoom, 1);
    assert.equal(ui.reset.disabled, true);
    ui.binding.dispose(); ui.move(100); assert.equal(cam.zoom, 1);
});

test('leaving the real Explore method restores zoom with the normal camera framing', async () => {
    const source = await readFile(new URL('../../face/js/app.js', import.meta.url), 'utf8');
    const block = source.match(/^    _toggleInspectorMode\([^]*?^    \}/m)[0];
    const toggle = new Function('document', 'return ({' + block.trim() + '})')( { getElementById: () => null } )._toggleInspectorMode;
    const cam = camera(), view = new RoomAvatarZoom({ storage: memory });
    cam.position.set = function(x,y,z) { Object.assign(this, { x,y,z }); };
    cam.lookAt = (...target) => { cam.target = target; };
    view.attachCamera(cam); view.setValue(100); view.setExploring(true);
    cam.fov = 42; cam.position.set(-7, 2, 0);
    const app = { inspectorMode: true, avatarZoom: view, camera: cam,
        _setInspectPresetHudVisible() {}, _setActiveInspectPresetButton() {}, _setActiveRoomActionButton() {} };
    toggle.call(app);
    assert.equal(cam.zoom, 2.5); assert.equal(cam.fov, 45);
    assert.deepEqual(cam.target, [0.08, 0.66, 0]);
    assert.deepEqual([cam.position.x,cam.position.y,cam.position.z], [0.34,1.38,4.95]);
});
