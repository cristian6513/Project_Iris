import test from 'node:test';
import assert from 'node:assert/strict';
import { HomeContextGestures } from '../../face/js/os_home_context_gestures.js';

function setup() {
    const surface = new EventTarget(), doc = new EventTarget(); doc.defaultView = new EventTarget();
    const anchor = { isConnected: true }, tile = { contains: (x) => x === anchor };
    let timer, clock = 0, enabled = true; const opened = [];
    const gesture = new HomeContextGestures({ surfaces: [surface], documentLike: doc,
        resolve: (target) => target === anchor ? { id: 'calendar', anchor, element: tile } : null,
        open: (...args) => opened.push(args), enabled: () => enabled,
        schedule: (fn, ms) => { assert.equal(ms, 550); timer = fn; return 1; }, unschedule: () => { timer = null; }, now: () => clock });
    const fire = (target, type, fields = {}) => {
        const ev = new Event(type, { cancelable: true });
        Object.assign(ev, { button: 0, pointerId: 1, pointerType: 'touch', isPrimary: true, clientX: 40, clientY: 50, ...fields });
        Object.defineProperty(ev, 'target', { value: anchor }); target.dispatchEvent(ev); return ev;
    };
    return { surface, doc, anchor, gesture, fire, opened, hold: () => timer?.(), advance: (n) => { clock += n; }, disable: () => { enabled = false; } };
}
test('one hold opens once, suppresses the native context menu and consumes the trailing click, even after a long hold', () => {
    const h = setup(); let launches = 0; h.surface.addEventListener('click', () => launches++);
    h.fire(h.surface, 'pointerdown'); h.hold(); h.advance(5000);
    assert.equal(h.opened.length, 1); assert.equal(h.fire(h.surface, 'contextmenu').defaultPrevented, true);
    assert.equal(h.opened.length, 1);
    h.fire(h.doc, 'pointerup'); assert.equal(h.fire(h.surface, 'click').defaultPrevented, true); assert.equal(launches, 0);
    h.fire(h.surface, 'click'); assert.equal(launches, 1); h.gesture.dispose();
});
test('normal tap, mouse hold, scroll, movement, multitouch, cancellation and hiding never open a touch menu', () => {
    for (const mode of ['tap', 'mouse', 'move', 'scroll', 'two', 'cancel', 'hidden', 'blur', 'resize', 'disabled', 'detached']) {
        const h = setup(); h.fire(h.surface, 'pointerdown', mode === 'mouse' ? { pointerType: 'mouse' } : {});
        if (mode === 'tap') h.fire(h.doc, 'pointerup');
        if (mode === 'move') h.fire(h.doc, 'pointermove', { clientX: 52 });
        if (mode === 'scroll') h.fire(h.surface, 'scroll');
        if (mode === 'two') h.fire(h.doc, 'pointerdown', { pointerId: 2, isPrimary: false });
        if (mode === 'cancel') h.fire(h.doc, 'pointercancel');
        if (mode === 'hidden') { h.doc.visibilityState = 'hidden'; h.fire(h.doc, 'visibilitychange'); }
        if (['blur', 'resize'].includes(mode)) h.fire(h.doc.defaultView, mode);
        if (mode === 'disabled') h.disable();
        if (mode === 'detached') h.anchor.isConnected = false;
        h.hold(); assert.equal(h.opened.length, 0, mode); assert.equal(h.fire(h.surface, 'click').defaultPrevented, false, mode); h.gesture.dispose();
    }
});
test('right click and keyboard context keys open the same target; ordinary keys and disposal do nothing', () => {
    const h = setup();
    assert.equal(h.fire(h.surface, 'contextmenu').defaultPrevented, true);
    h.fire(h.surface, 'keydown', { key: 'F10', shiftKey: true });
    h.fire(h.surface, 'keydown', { key: 'ContextMenu' });
    h.fire(h.surface, 'keydown', { key: 'Enter' });
    assert.equal(h.opened.length, 3); assert.equal(h.opened[1][1], undefined);
    h.gesture.dispose(); h.fire(h.surface, 'pointerdown'); h.hold(); h.fire(h.surface, 'contextmenu'); assert.equal(h.opened.length, 3);
});

test('an early native touch contextmenu wins over the hold timer and still suppresses the release click', () => {
    const h = setup(); let launches = 0; h.surface.addEventListener('click', () => launches++);
    h.fire(h.surface, 'pointerdown'); h.fire(h.surface, 'contextmenu'); h.hold(); h.advance(5000);
    assert.equal(h.opened.length, 1); h.fire(h.doc, 'pointerup');
    h.fire(h.surface, 'click'); assert.equal(launches, 0); h.gesture.dispose();
});
