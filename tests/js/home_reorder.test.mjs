import test from 'node:test';
import assert from 'node:assert/strict';
import { HomePreferences } from '../../face/js/os_desktop_home.js';
import { HomeReorder } from '../../face/js/os_home_reorder.js';
import { homeDocument } from '../fixtures/home_dom.mjs';

const W = 'wdg_' + 'b'.repeat(32);
const initial = () => ({ schema:'iris.desktop-home/v1', revision:4, shortcuts:['theme','player','mail'], hidden_apps:[], order:[] });
function setup({ conflict=false }={}) {
    let saved=initial(); const calls=[];
    const model=new HomePreferences({fetchImpl:async(url,init)=>{
        calls.push({url,...init});
        if(init.method==='PUT') {
            if(conflict) { saved={...saved,revision:5,shortcuts:['mail'],order:[]};return {ok:false,status:409,json:async()=>({error:{message:'Cambió en otra ventana'}})}; }
            saved={...JSON.parse(init.body),revision:saved.revision+1};
        }
        return {ok:true,json:async()=>structuredClone(saved)};
    }});
    return {model,calls};
}
test('dropping into another position persists a mixed order once; an old revision or foreign ref cannot edit', async()=>{
    const {model,calls}=setup(); await model.load();
    assert.equal(await model.place('app:theme',`widget:${W}`,[W],4),true);
    assert.deepEqual(model.layout.order,['app:player','app:mail',`widget:${W}`,'app:theme']);
    await model.place('app:mail','app:player',[W],4); await model.place('app:not-installed','app:player',[W],5);
    assert.equal(calls.filter(c=>c.method==='PUT').length,1);
});
test('conflict retrieves the other window order without replaying a drag', async()=>{
    const {model,calls}=setup({conflict:true});await model.load();
    assert.equal(await model.place('app:theme','app:player',[W],4),false);
    assert.deepEqual(model.layout.shortcuts,['mail']);assert.equal(calls.filter(c=>c.method==='PUT').length,1);
});
// QA 2026-10-20: icons and widgets move directly, as on a phone or the Windows desktop.
function dragHarness({ place = async () => true, irisReduced = false } = {}) {
    const doc = homeDocument(), panel = doc.createElement('main'), list = doc.createElement('div');
    if (irisReduced) panel.dataset.reducedMotion = 'true';  // Theme Studio → Reducir movimiento
    doc.body.append(panel); panel.append(list);
    const animations = [];
    const items = ['app:theme', `widget:${W}`, 'app:mail'].map((ref) => {
        const node = doc.createElement('article'); node.dataset.homeRef = ref; node.animate = () => animations.push(ref);
        node.append(doc.createElement('button')); list.append(node); return node;
    });
    let hit = null, on = true; const timers = [], saves = [], notes = [], settled = [];
    doc.elementFromPoint = () => hit;
    const model = { layout: { revision: 1 }, place: (...args) => { saves.push(args); return place(...args); } };
    const drag = new HomeReorder({ list, model, documentLike: doc, widgetIds: () => [W], enabled: () => on,
        announce: (text) => notes.push(text), onSettle: () => settled.push(true),
        frame: () => 1, cancelFrame() {}, schedule: (fn) => timers.push(fn), unschedule() {}, now: () => 0 });
    const pointer = (extra = {}) => ({ button: 0, isPrimary: true, pointerId: 1, pointerType: 'mouse', clientX: 0, clientY: 0, ...extra });
    return { doc, list, items, drag, model, saves, notes, settled, pointer, animations, order: () => list.children.map((n) => n.dataset.homeRef),
        over: (node) => { hit = node; }, hold: () => timers.splice(0).forEach((fn) => fn()), disable: () => { on = false; } };
}
const ORIGINAL = ['app:theme', `widget:${W}`, 'app:mail'];

test('a mouse drags once it moves past a jitter; the others make room live and one save happens on release', async () => {
    const h = dragHarness(), [theme, , mail] = h.items; let opened = 0;
    theme.children[0].addEventListener('click', () => opened++);
    theme.children[0].emit('pointerdown', h.pointer()); h.over(mail);
    h.doc.emit('pointermove', h.pointer({ clientX: 4, clientY: 3 }));
    assert.equal(h.drag.drag, null, 'a 5 px jitter is still a click');
    h.doc.emit('pointermove', h.pointer({ clientX: 40 }));
    assert.deepEqual(h.order(), [`widget:${W}`, 'app:mail', 'app:theme'], 'the gap follows the pointer');
    assert.ok(theme.classList.contains('os-home-dragging')); assert.match(theme.style.transform, /translate/);
    assert.equal(h.saves.length, 0, 'nothing is saved while dragging');
    h.doc.emit('pointerup', h.pointer({ pointerId: 2 })); assert.equal(h.saves.length, 0, 'another pointer does nothing');
    h.doc.emit('pointerup', h.pointer({ clientX: 40 }));
    assert.deepEqual(h.saves, [['app:theme', 'app:mail', [W], 1]]);
    assert.equal(h.drag.settling, true); assert.equal(theme.style.transform, ''); assert.ok(!theme.classList.contains('os-home-dragging'));
    theme.children[0].click(); assert.equal(opened, 0, 'the release does not also open the app');
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(h.drag.settling, false); assert.equal(h.settled.length, 1); assert.match(h.notes.at(-1), /guardado/);
    theme.children[0].click(); assert.equal(opened, 1); h.drag.dispose();
});

test('a plain click never lifts, and a failed save hands the order back to the home', async () => {
    const h = dragHarness({ place: async () => false }), [theme, , mail] = h.items;
    theme.children[0].emit('pointerdown', h.pointer()); h.doc.emit('pointerup', h.pointer());
    assert.equal(h.drag.drag, null); assert.equal(h.drag.pending, null);
    theme.children[0].emit('pointerdown', h.pointer()); h.over(mail); h.doc.emit('pointermove', h.pointer({ clientX: 40 }));
    h.doc.emit('pointerup', h.pointer({ clientX: 40 })); await new Promise((resolve) => setImmediate(resolve));
    assert.equal(h.settled.length, 1, 'the home re-syncs with the order Core kept'); assert.match(h.notes.at(-1), /No se cambió/);
    h.drag.dispose();
});

test('a finger scrolls unless it first holds still; after the hold it moves the item, not the page', () => {
    const h = dragHarness(), [theme, , mail] = h.items, touch = (extra) => h.pointer({ pointerType: 'touch', ...extra });
    theme.emit('pointerdown', touch()); h.over(mail);
    h.doc.emit('pointermove', touch({ clientY: 30 })); h.hold(); h.doc.emit('pointermove', touch({ clientY: 60 }));
    assert.equal(h.drag.drag, null, 'a swipe before the hold scrolls'); assert.deepEqual(h.order(), ORIGINAL);
    theme.emit('pointerdown', touch());
    assert.equal(h.list.emit('touchmove').defaultPrevented, false, 'the page still scrolls before the hold');
    h.hold();
    assert.equal(h.list.emit('touchmove').defaultPrevented, true, 'after the hold the finger owns the item');
    h.doc.emit('pointermove', touch({ clientX: 40 }));
    assert.deepEqual(h.order(), [`widget:${W}`, 'app:mail', 'app:theme']);
    h.doc.emit('pointerup', touch({ clientX: 40 })); assert.equal(h.saves.length, 1); h.drag.dispose();
});

test('Escape, a cancelled pointer, hiding, a busy home or another window never save and put the order back', () => {
    for (const mode of ['escape', 'pointercancel', 'hidden', 'blur', 'busy', 'revision']) {
        const h = dragHarness(), [theme, , mail] = h.items;
        theme.emit('pointerdown', h.pointer()); h.over(mail); h.doc.emit('pointermove', h.pointer({ clientX: 40 }));
        assert.notDeepEqual(h.order(), ORIGINAL, mode);
        if (mode === 'escape') h.doc.emit('keydown', { key: 'Escape' });
        if (mode === 'pointercancel') h.doc.emit('pointercancel', h.pointer());
        if (mode === 'hidden') { h.doc.visibilityState = 'hidden'; h.doc.emit('visibilitychange'); }
        if (mode === 'blur') h.doc.defaultView.emit('blur');
        if (mode === 'busy') h.disable();
        if (mode === 'revision') h.model.layout.revision++;
        h.doc.emit('pointerup', h.pointer({ clientX: 40 }));
        assert.equal(h.saves.length, 0, mode); assert.deepEqual(h.order(), ORIGINAL, mode);
        assert.ok(!theme.classList.contains('os-home-dragging'), mode); h.drag.dispose();
    }
});

test('Codex H01: after Escape (or blur) the still-pressed button is released without opening the app', () => {
    for (const mode of ['escape', 'blur']) {
        const h = dragHarness(), [theme, , mail] = h.items, launch = theme.children[0]; let opened = 0;
        launch.addEventListener('click', () => opened++);
        launch.emit('pointerdown', h.pointer()); h.over(mail); h.doc.emit('pointermove', h.pointer({ clientX: 12 }));
        if (mode === 'escape') h.doc.emit('keydown', { key: 'Escape' }); else h.doc.defaultView.emit('blur');
        h.doc.emit('pointerup', h.pointer({ clientX: 12 })); launch.click();
        assert.equal(opened, 0, `${mode}: the release of the cancelled gesture opens nothing`);
        assert.deepEqual(h.order(), ORIGINAL, mode); assert.equal(h.saves.length, 0, mode);
        launch.emit('pointerdown', h.pointer()); h.doc.emit('pointerup', h.pointer()); launch.click();
        assert.equal(opened, 1, `${mode}: the next ordinary click still opens`); h.drag.dispose();
    }
});

test('Codex H02: Theme Studio «Reducir movimiento» turns the shift and drop effects off, like Windows does', async () => {
    for (const irisReduced of [false, true]) {
        const h = dragHarness({ irisReduced }), [theme, , mail] = h.items;
        theme.emit('pointerdown', h.pointer()); h.over(mail); h.doc.emit('pointermove', h.pointer({ clientX: 40 }));
        h.doc.emit('pointerup', h.pointer({ clientX: 40 }));
        assert.equal(h.saves.length, 1, 'dragging still works');
        if (irisReduced) assert.deepEqual(h.animations, []); else assert.ok(h.animations.length > 0, 'control: effects play by default');
        await new Promise((resolve) => setImmediate(resolve)); h.drag.dispose();
    }
});

test('edit buttons, fields and the right button keep their own press; a widget image cannot start a browser drag', () => {
    const h = dragHarness(), [theme, widget] = h.items;
    const controls = h.doc.createElement('div'); controls.className = 'os-home-item-controls';
    const remove = h.doc.createElement('button'); controls.append(remove); theme.append(controls);
    const field = h.doc.createElement('input'); widget.append(field);
    for (const [node, extra] of [[remove, {}], [field, {}], [theme.children[0], { button: 2 }]]) {
        node.emit('pointerdown', h.pointer(extra)); assert.equal(h.drag.pending, null);
    }
    const image = h.doc.createElement('img'); widget.append(image);
    assert.equal(image.emit('dragstart').defaultPrevented, true); h.drag.dispose();
});
