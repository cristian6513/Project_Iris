import test from 'node:test';
import assert from 'node:assert/strict';
import { createWidget } from '../ui/widget.js';
import { createApp } from '../ui/main.js';
import { createCalendarSource } from '../ui/state.js?v=2';

class Node {
    constructor(tag) { Object.assign(this, { tag, children: [], listeners: {}, attributes: {}, dataset: {}, textContent: '', className: '', disabled: false,
        checked: false, value: '', style: { setProperty() {} } }); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    fire(type, extra = {}) { for (const fn of this.listeners[type] || []) fn({ target: this, preventDefault() {}, stopPropagation() {}, ...extra }); }
    focus() { doc.activeElement = this; }
    contains(node) { return this.all().includes(node); }
    all() { return [this, ...this.children.flatMap((node) => node.all())]; }
    find(part) { return this.all().find((node) => (node.dataset.widgetPart || node.dataset.part) === part); }
    findAll(part) { return this.all().filter((node) => (node.dataset.widgetPart || node.dataset.part) === part); }
    key(key) { return this.all().find((node) => (node.dataset.calendarKey || node.dataset.key) === key); }
    querySelector(selector) { return this.key(selector.match(/data-calendar-key="([^"]+)"/)?.[1]); }
    text() { return this.all().map((node) => node.textContent).join(' '); }
}
const doc = globalThis.document = { activeElement: null, createElement: (tag) => new Node(tag) };
const NOW = Date.parse('2026-10-16T15:00:00Z');
const PREFS = { revision: 1, time_zone: 'America/Bogota', hidden_calendars: [], reminders_enabled: false };
const ACCOUNT = { id: `gacc_${'a'.repeat(32)}`, label: 'Trabajo', auth_state: 'connected', granted: ['calendar.read'], sync: { last_success_at: '2026-10-16T14:00:00Z' } };
const CALENDAR = { key: `cal_${'b'.repeat(32)}`, account_id: ACCOUNT.id, name: 'Proyecto' };
const EVENT = { id: `event_${'c'.repeat(32)}`, calendar_key: CALENDAR.key, account_id: ACCOUNT.id, title: 'Reunión real guardada',
    all_day: false, start: '2026-10-16T16:00:00Z', end: '2026-10-16T17:00:00Z' };
const DATA = { schema: 'iris.calendar/v1', available: true, complete: true, accounts: [ACCOUNT], calendars: [CALENDAR], events: [EVENT], preferences: PREFS };
const CONFIG = { instance_id: `wdg_${'d'.repeat(32)}`, widget_id: 'calendar.month', config_version: 1, size: 'compact', calendars: [] };
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function setup({ data = DATA, prefs = PREFS, fail = false, now = NOW } = {}) {
    const calls = [], opened = [], configurations = [], timers = new Map(); let counter = 0;
    const fetchImpl = async (url, init = {}) => {
        calls.push({ url, ...init });
        if (fail && !url.includes('/preferences')) return { ok: false, status: 503, json: async () => ({ error: { message: 'No disponible' } }) };
        return { ok: true, json: async () => structuredClone(url.includes('/preferences') ? prefs : data) };
    };
    const source = createCalendarSource({ fetchImpl, setTimer: (fn) => { timers.set(++counter, fn); return counter; }, clearTimer: (id) => timers.delete(id) });
    const root = new Node('section');
    const widget = createWidget({ root, config: CONFIG, openApp: (...args) => opened.push(args), configure: (value) => { configurations.push(value); return true; } },
        { source, now: typeof now === 'function' ? now : () => now, documentLike: doc });
    return { widget, root, source, calls, opened, configurations, timers, fetchImpl };
}

test('adding a hidden widget is passive, and visible reads only profile preferences and local cache', async () => {
    const { widget, root, calls, source } = setup(); assert.equal(calls.length, 0);
    widget.setVisibility(true); await flush();
    assert.equal(root.findAll('cell').length, 0); assert.match(root.text(), /Reunión real guardada/);
    assert.ok(calls.every((call) => !call.method || call.method === 'GET'));
    assert.equal(calls.length, 2); assert.match(root.find('date-label').textContent, /octubre/);
    widget.dispose(); source.dispose();
});

test('app and widget share one preference read, one range read and one visual poll', async () => {
    const { widget, source, calls, timers, fetchImpl } = setup();
    const appRoot = new Node('main'); const app = createApp({}, { source, fetchImpl, now: () => NOW }); app.mount(appRoot);
    widget.setVisibility(true); const opening = app.refresh({ opening: true }); app.setVisibility({ visible: true }); await opening; await flush();
    assert.equal(calls.filter((call) => call.url.includes('/preferences')).length, 1);
    assert.equal(calls.filter((call) => call.url.startsWith('/api/calendar?')).length, 1);
    assert.equal(timers.size, 1);
    app.dispose(); const poll = [...timers.values()][0]; timers.clear(); await poll();
    assert.equal(calls.filter((call) => call.url.startsWith('/api/calendar?')).length, 2, 'the widget keeps updating after the app closes');
    widget.setVisibility(false); assert.equal(timers.size, 0);
    widget.dispose(); source.dispose();
});

test('visible date navigation and events reuse the Calendar app with exact account/date identity', async () => {
    const { widget, root, opened, source } = setup(); widget.setVisibility(true); await flush();
    root.key(`event:${EVENT.id}`).fire('click');
    assert.deepEqual(opened[0], ['calendar', { date: '2026-10-16', event_id: EVENT.id, account_id: ACCOUNT.id }]);
    root.key('open').fire('click');
    assert.equal(opened[1][1].date, '2026-10-16');
    assert.equal(root.key('prev'), undefined); assert.equal(root.key('next'), undefined);
    widget.dispose(); source.dispose();
});

test('ongoing all-day events remain visible; their exclusive end removes yesterday’s event', async () => {
    const ongoing = { ...EVENT, id: 'ongoing', all_day: true, start_date: '2026-10-15', end_date: '2026-10-17', start: null, end: null };
    const finished = { ...ongoing, id: 'finished', end_date: '2026-10-16' };
    const { widget, root, source } = setup({ data: { ...DATA, events: [ongoing, finished] } });
    widget.setVisibility(true); await flush();
    assert.ok(root.key('event:ongoing')); assert.equal(root.key('event:finished'), undefined);
    assert.match(root.text(), /Hoy · Todo el día/);
    widget.dispose(); source.dispose();
});

test('compact view shows at most three events, dates future all-day events and discloses its bounded period', async () => {
    const events = [1,2,3,4].map(n => ({ ...EVENT, id: `all-${n}`, all_day:true, start:null, end:null,
        start_date: `2026-10-${16+n}`, end_date:`2026-10-${17+n}` }));
    const { widget, root, source } = setup({ data:{...DATA,events} }); widget.setVisibility(true); await flush();
    assert.equal(root.findAll('event-title').length,3); assert.match(root.find('period').textContent,/Hasta .*3 de 4/);
    assert.match(root.findAll('event-time')[0].textContent,/17.*Todo el día/);
    assert.notEqual(root.find('filters').open,true); assert.equal(root.findAll('grid').length,0);
    widget.dispose();source.dispose();
});

test('shared preference updates hide events immediately without a second sync or model call', async () => {
    const { widget, root, source, calls } = setup(); widget.setVisibility(true); await flush();
    source.setPreferences({ ...PREFS, revision: 2, hidden_calendars: [CALENDAR.key] });
    assert.equal(root.key(`event:${EVENT.id}`), undefined); assert.equal(calls.length, 2);
    source.setPreferences({ ...PREFS, revision: 1 }); assert.equal(root.key(`event:${EVENT.id}`), undefined, 'a stale response never restores a hidden calendar');
    widget.dispose(); source.dispose();
});

test('empty, disconnected, partial, unavailable and error states stay distinct', async () => {
    for (const [data, fail, expected] of [
        [{ ...DATA, events: [] }, false, /Sin eventos guardados/],
        [{ ...DATA, accounts: [], calendars: [], events: [] }, false, /Conectar Calendario/],
        [{ ...DATA, complete: false, events: [] }, false, /Vista incompleta/],
        [{ ...DATA, available: false, events: [] }, false, /Calendario no disponible/],
        [DATA, true, /No disponible/],
    ]) {
        const { widget, root, source } = setup({ data, fail }); widget.setVisibility(true); await flush();
        assert.match(root.text(), expected);
        if (fail || !data.available || !data.complete) assert.doesNotMatch(root.text(), /Sin eventos guardados/);
        widget.dispose(); source.dispose();
    }
});

test('remote event text is not HTML, and missing configured calendars never borrow another account', async () => {
    const { widget, root, source, opened } = setup({ data: { ...DATA, events: [{ ...EVENT, title: '<img src=x onerror=bad()>' }] } });
    widget.setVisibility(true); await flush(); assert.ok(root.all().some((node) => node.textContent === '<img src=x onerror=bad()>'));
    assert.ok(!root.all().some((node) => node.tag === 'img'));
    widget.configure({ ...CONFIG, calendars: [`cal_${'f'.repeat(32)}`] });
    assert.equal(root.key(`event:${EVENT.id}`), undefined); assert.match(root.text(), /ya no están disponibles/);
    assert.deepEqual(opened, []); widget.dispose(); source.dispose();
});

test('a future all-day event cannot hide the nearer timed meeting from the three-event summary', async () => {
    const later = [1, 2, 3].map((n) => ({ ...EVENT, id: `later-${n}`, title: 'Futuro', all_day: true, start: null, end: null,
        start_date: `2026-10-${20 + n}`, end_date: `2026-10-${21 + n}` }));
    const { widget, root, source } = setup({ data: { ...DATA, events: [...later, EVENT] } }); widget.setVisibility(true); await flush();
    assert.equal(root.findAll('event-title')[0].textContent, EVENT.title); assert.equal(root.findAll('event-title').length, 3);
    widget.dispose(); source.dispose();
});

test('a widget hidden during preference loading does not start a late calendar read', async () => {
    let finish; const wait=new Promise(resolve=>{finish=resolve;}); const calls=[];
    const source=createCalendarSource({fetchImpl:async(url)=>{calls.push(url);return wait;}});
    const root=new Node('section');const widget=createWidget({root,config:CONFIG},{source,documentLike:doc});
    widget.setVisibility(true);widget.setVisibility(false);
    finish({ok:true,json:async()=>PREFS});await flush();
    assert.deepEqual(calls,['/api/calendar/preferences']);
    widget.dispose();source.dispose();
});

test('a visible widget rolls over its month/time zone without presenting the old range as the new empty agenda',async()=>{
    let clock=Date.parse('2026-10-31T22:00:00Z');const h=setup({now:()=>clock});h.widget.setVisibility(true);await flush();
    const before=h.calls.filter(c=>c.url.startsWith('/api/calendar?')).at(-1).url;
    clock=Date.parse('2026-11-01T15:00:00Z');h.source.setPreferences({...PREFS,revision:2});
    assert.match(h.root.find('period').textContent,/Lectura pendiente/);assert.doesNotMatch(h.root.text(),/Sin eventos guardados/);
    await flush();const after=h.calls.filter(c=>c.url.startsWith('/api/calendar?')).at(-1).url;assert.notEqual(after,before);
    assert.match(h.root.find('date-label').textContent,/noviembre/);h.widget.dispose();h.source.dispose();
});

test('retiring the last visible view aborts its request even if another consumer is hidden', async()=>{
    let signal;const source=createCalendarSource({fetchImpl:async(url,init)=>{signal=init.signal;return new Promise(()=>{});}});
    const range={start:'2026-10-01',end:'2026-11-01',time_zone:'UTC'};
    const a=source.acquire(()=>{}),b=source.acquire(()=>{});
    a.setVisible(true);void a.read(range);void b.read(range);a.release();
    assert.equal(signal.aborted,true);b.release();source.dispose();
});

test('a declined invitation never takes one of the three «Próximos» places (review 2026-10-19)', async () => {
    const declined = { ...EVENT, id: `event_${'e'.repeat(32)}`, title: 'Invitación rechazada', declined: true,
        start: '2026-10-16T15:30:00Z', end: '2026-10-16T16:00:00Z' };
    const { widget, root, source } = setup({ data: { ...DATA, events: [declined, EVENT] } }); widget.setVisibility(true); await flush();
    assert.deepEqual(root.findAll('event-title').map((node) => node.textContent), ['Reunión real guardada']);
    widget.dispose(); source.dispose();
});
