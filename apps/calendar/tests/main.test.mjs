import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../ui/main.js';

class Node {
    constructor(tag) { Object.assign(this, { tag, children: [], listeners: {}, attributes: {}, dataset: {}, textContent: '', className: '', disabled: false,
        checked: false, value: '', style: { setProperty() {} } }); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    fire(type, extra = {}) { for (const fn of this.listeners[type] || []) fn({ type, target: this, preventDefault() {}, ...extra }); }
    focus() { globalThis.document.activeElement = this; }
    contains(node) { return this.all().includes(node); }
    all() { return [this, ...this.children.flatMap((node) => node.all())]; }
    find(part) { return this.all().find((node) => node.dataset.part === part); }
    findAll(part) { return this.all().filter((node) => node.dataset.part === part); }
    key(key) { return this.all().find((node) => node.dataset.key === key); }
    text() { return this.all().map((node) => node.textContent).join(' '); }
}
globalThis.document = { activeElement: null, createElement: (tag) => new Node(tag) };
const NOW = Date.parse('2026-10-15T16:00:00Z');
const PREFS = { revision: 1, time_zone: 'America/Bogota', hidden_calendars: [], reminders_enabled: false };
const ACCOUNT = { id: 'gacc_11111111111111111111111111111111', email: 'one@example.test', label: 'Cuenta de prueba', auth_state: 'connected', granted: ['calendar.read'],
    sync: { state: 'idle', last_success_at: '2026-10-15T15:00:00Z' } };
const CALENDAR = { key: 'cal_22222222222222222222222222222222', account_id: ACCOUNT.id, name: 'Trabajo', color: '#c080f0', time_zone: 'America/Bogota' };
const EVENT = { id: 'event_33333333333333333333333333333333', account_id: ACCOUNT.id, calendar_key: CALENDAR.key, title: 'Reunión con el equipo',
    start: '2026-10-15T17:00:00Z', end: '2026-10-15T18:00:00Z', all_day: false, start_date: null, end_date: null,
    time_zone: 'America/Bogota', location: 'Sala 1', description: 'Preparar el proyecto.', join_url: 'https://meet.google.com/abc-defg-hij', recurring: false };
const SNAPSHOT = { schema: 'iris.calendar/v1', complete: true, available: true, accounts: [ACCOUNT], calendars: [CALENDAR], events: [EVENT], warnings: [] };
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const defer = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; };

function setup({ snapshot = SNAPSHOT, prefs = PREFS, answer = false, routes = {} } = {}) {
    const calls = [], confirms = [], opened = [], briefs = [];
    const fetchImpl = async (url, init = {}) => {
        const method = init.method || 'GET'; const path = url.split('?')[0];
        const call = { method, url, path, body: init.body ? JSON.parse(init.body) : undefined }; calls.push(call);
        const route = routes[`${method} ${path}`];
        let result;
        if (route) result = await (typeof route === 'function' ? route(call) : route);
        else if (path === '/api/calendar/preferences') result = [200, method === 'PATCH' ? { ...prefs, ...call.body, revision: prefs.revision + 1 } : prefs];
        else result = [200, snapshot];
        return { ok: result[0] < 400, status: result[0], json: async () => result[1] };
    };
    const app = createApp({ openApp: (id) => opened.push(id), setBrief: (brief) => briefs.push(brief),
        confirm: async (options) => { confirms.push(options); return typeof answer === 'function' ? answer() : answer; } }, { fetchImpl, now: () => NOW });
    const root = new Node('div'); app.mount(root);
    return { app, root, calls, confirms, opened, briefs };
}

test('mount is passive; opening reads preferences and local cache but never starts OAuth or Google sync', async () => {
    const { app, root, calls, briefs } = setup();
    assert.equal(calls.length, 0);
    await app.refresh({ opening: true });
    assert.equal(calls.length, 2); assert.ok(calls.every((call) => call.method === 'GET'));
    assert.equal(new URL(calls[1].url, 'http://local').searchParams.get('time_zone'), 'America/Bogota');
    assert.match(root.text(), /Solo lectura/); assert.deepEqual(briefs, ['1 evento']); app.dispose();
});

test('Google refresh shows the confirmed running state and becomes available after it finishes',async()=>{
    let running=true;
    const {app,root,calls}=setup({routes:{'GET /api/calendar':()=>[200,{...SNAPSHOT,
        accounts:[{...ACCOUNT,sync:{...ACCOUNT.sync,state:running?'running':'idle'}}]}]}});
    await app.refresh({opening:true});
    assert.match(root.find('sync').textContent,/Actualizando desde Google/);
    assert.equal(root.find('sync').disabled,true);
    root.find('sync').fire('click');await settle();
    assert.ok(calls.every(call=>call.method==='GET'));
    running=false;await app.refresh({force:true});
    assert.equal(root.find('sync').textContent,'Actualizar desde Google');
    assert.equal(root.find('sync').disabled,false);app.dispose();
});

test('all four views render their actual date ranges and navigation is cache-only', async () => {
    const { app, root, calls } = setup(); await app.refresh({ opening: true });
    assert.equal(root.findAll('day-cell').length, 42);
    for (const [view, part, count] of [['day', 'list-day', 1], ['week', 'list-day', 7], ['agenda', 'list-day', 1], ['month', 'day-cell', 42]]) {
        root.key(`view:${view}`).fire('click'); await settle(); assert.equal(root.findAll(part).length, count);
    }
    root.key('next').fire('click'); await settle(); assert.match(root.find('period').textContent, /noviembre/);
    root.key('today').fire('click'); await settle(); assert.match(root.find('period').textContent, /octubre/);
    assert.ok(calls.every((call) => call.method === 'GET')); app.dispose();
});

test('selecting an event shows real details and opens only the validated meeting destination', async () => {
    const { app, root } = setup(); await app.refresh({ opening: true });
    root.find('chip').fire('click');
    assert.equal(root.find('event-name').textContent, EVENT.title); assert.equal(root.find('event-location').textContent, 'Sala 1');
    assert.equal(root.find('join').href, EVENT.join_url); assert.equal(root.find('join').target, '_blank'); assert.equal(root.find('join').rel, 'noopener noreferrer');
    app.dispose();
});

test('remote titles and descriptions are text; unsafe meeting URLs never become an action', async () => {
    const event = { ...EVENT, title: '<img src=x onerror=alert(1)>', description: '<script>bad()</script>', join_url: 'https://evil.example/meet' };
    const { app, root } = setup({ snapshot: { ...SNAPSHOT, events: [event] } }); await app.refresh({ opening: true });
    root.find('chip').fire('click'); assert.equal(root.find('event-name').textContent, event.title);
    assert.equal(root.find('event-description').textContent, event.description); assert.equal(root.find('join'), undefined); app.dispose();
});

test('query and calendar filters affect the actual events, and filter saves use the profile revision', async () => {
    const { app, root, calls } = setup(); await app.refresh({ opening: true });
    let search = root.find('search'); search.value = 'nada'; search.focus(); search.fire('input');
    assert.equal(root.findAll('chip').length, 0); assert.equal(globalThis.document.activeElement.dataset.key, 'search');
    search = root.find('search'); search.value = ''; search.fire('input');
    const check = root.find('calendar-check'); check.checked = false; check.fire('change'); await settle();
    const patch = calls.find((call) => call.method === 'PATCH');
    assert.equal(patch.body.revision, 1); assert.deepEqual(patch.body.hidden_calendars, [CALENDAR.key]);
    assert.equal(root.findAll('chip').length, 0); app.dispose();
});

test('an empty account state offers the real Conexiones app', async () => {
    const { app, root, opened } = setup({ snapshot: { ...SNAPSHOT, accounts: [], calendars: [], events: [] } });
    await app.refresh({ opening: true }); root.find('connections').fire('click');
    assert.deepEqual(opened, ['conexiones']); assert.equal(root.find('sync').disabled, true); assert.match(root.text(), /Conecta Calendario/); app.dispose();
});

test('denying reminder confirmation sends no mutation; accepting explains visual-only effects', async () => {
    for (const answer of [false, true]) {
        const { app, root, calls, confirms } = setup({ answer }); await app.refresh({ opening: true });
        root.find('reminders').fire('click'); await settle();
        assert.equal(confirms.length, 1); assert.match(confirms[0].message, /60 minutos antes/); assert.match(confirms[0].message, /No usará voz ni IA/);
        const patch = calls.find((call) => call.method === 'PATCH'); assert.equal(Boolean(patch), answer);
        if (answer) { assert.equal(patch.body.reminders_enabled, true); assert.equal(root.find('reminders').attributes['aria-pressed'], 'true'); }
        app.dispose();
    }
});

test('a double click while the reminder modal is open is a single confirmation', async () => {
    const wait = defer(); const { app, root, confirms, calls } = setup({ answer: () => wait.promise }); await app.refresh({ opening: true });
    const button = root.find('reminders'); button.fire('click'); button.fire('click');
    assert.equal(confirms.length, 1); wait.resolve(false); await settle(); assert.equal(calls.filter((call) => call.method === 'PATCH').length, 0); app.dispose();
});

test('refresh is manual, calendar-only for selected accounts, and a rejected 202 is not called success', async () => {
    for (const accepted of [false, true]) {
        const { app, root, calls } = setup({ routes: { 'POST /api/calendar/sync': [202, { accepted, account_ids: accepted ? [ACCOUNT.id] : [], errors: accepted ? [] : [{ code: 'busy' }] }] } });
        await app.refresh({ opening: true }); const sync = root.find('sync'); sync.fire('click'); sync.fire('click'); await settle();
        const posts = calls.filter((call) => call.method === 'POST'); assert.equal(posts.length, 1); assert.deepEqual(posts[0].body, { account_ids: [ACCOUNT.id] });
        assert.match(root.find('status-text').textContent, accepted ? /Actualización solicitada/ : /No se pudo solicitar/); app.dispose();
    }
});

test('permissions and reauthentication are honest and cannot start a sync', async () => {
    for (const account of [{ ...ACCOUNT, granted: ['gmail.read'] }, { ...ACCOUNT, auth_state: 'reauth_required' }]) {
        const { app, root, calls } = setup({ snapshot: { ...SNAPSHOT, accounts: [account], calendars: [], events: [] } });
        await app.refresh({ opening: true }); assert.equal(root.find('sync').disabled, true);
        root.find('sync').fire('click'); assert.equal(calls.filter((call) => call.method === 'POST').length, 0); app.dispose();
    }
});

test('revision conflicts reload server preferences without claiming the failed change was saved', async () => {
    let gets = 0;
    const { app, root } = setup({ routes: {
        'GET /api/calendar/preferences': () => [200, ++gets === 1 ? PREFS : { ...PREFS, revision: 2, hidden_calendars: [CALENDAR.key] }],
        'PATCH /api/calendar/preferences': [409, { error: { message: 'Otra ventana cambió las preferencias.' } }],
    } });
    await app.refresh({ opening: true }); const filter = root.find('calendar-check'); filter.checked = false; filter.fire('change'); await settle(); await settle();
    assert.equal(gets, 2); assert.match(root.find('status-text').textContent, /Otra ventana/); assert.equal(root.find('calendar-check').checked, false); app.dispose();
});

test('a partial snapshot shows its warnings instead of an unqualified empty result', async () => {
    const { app, root } = setup({ snapshot: { ...SNAPSHOT, complete: false, events: [], warnings: [{ code: 'unsupported', message: 'No se pueden mostrar algunas repeticiones.' }] } });
    await app.refresh({ opening: true }); assert.match(root.find('status-text').textContent, /Vista parcial/); assert.match(root.find('warning').textContent, /repeticiones/); app.dispose();
});

test('keyboard navigation changes the selected day and restores focus to its new date control', async () => {
    const { app, root } = setup(); await app.refresh({ opening: true });
    const date = root.key('date:2026-10-15'); date.focus(); date.fire('keydown', { key: 'ArrowRight' });
    assert.equal(globalThis.document.activeElement.dataset.key, 'date:2026-10-16');
    assert.match(root.find('chosen-date').textContent, /16/); app.dispose();
});

test('navigation from the inbox opens the supplied date and event, retaining the single window', async () => {
    const { app, root } = setup(); await app.refresh({ opening: true });
    app.navigate({ date: '2026-10-15', event_id: EVENT.id }); assert.equal(root.find('event-name').textContent, EVENT.title);
    app.navigate({ date: 'not-a-date', event_id: EVENT.id }); assert.equal(root.find('event-name').textContent, EVENT.title); app.dispose();
});

test('dispose aborts an in-flight read and ignores a late result', async () => {
    const wait = defer();
    const { app, root } = setup({ routes: { 'GET /api/calendar': (_call) => wait.promise } });
    const reading = app.refresh({ opening: true }); await settle(); app.dispose();
    wait.resolve([200, SNAPSHOT]); await reading; assert.equal(root.children.length, 0);
});

test('the mini-calendar keeps keyboard focus on its own next date', async () => {
    const { app, root } = setup(); await app.refresh({ opening: true });
    const date = root.key('mini:2026-10-15'); date.focus(); date.fire('keydown', { key: 'ArrowRight' });
    assert.equal(globalThis.document.activeElement.dataset.key, 'mini:2026-10-16'); app.dispose();
});

test('a selected account without Calendar permission cannot borrow another account permission to sync', async () => {
    const second = { ...ACCOUNT, id: 'gacc_44444444444444444444444444444444', granted: ['gmail.read'] };
    const { app, root, calls } = setup({ snapshot: { ...SNAPSHOT, accounts: [ACCOUNT, second] } }); await app.refresh({ opening: true });
    const account = root.find('account-select'); account.value = second.id; account.fire('change');
    assert.equal(root.find('sync').disabled, true); root.find('sync').fire('click'); await settle();
    assert.equal(calls.filter((call) => call.method === 'POST').length, 0); app.dispose();
});

test('an account removed by Conexiones does not leave an invisible stale filter', async () => {
    let reads = 0;
    const second = { ...ACCOUNT, id: 'gacc_44444444444444444444444444444444' };
    const { app, root } = setup({ routes: { 'GET /api/calendar': () => [200, ++reads === 1 ? { ...SNAPSHOT, accounts: [ACCOUNT, second] } : SNAPSHOT] } });
    await app.refresh({ opening: true }); const account = root.find('account-select'); account.value = second.id; account.fire('change');
    assert.equal(root.findAll('chip').length, 0);
    await app.refresh({ force: true }); assert.equal(root.find('account-select').value, ''); assert.equal(root.findAll('chip').length, 1); app.dispose();
});

test('a failed reminder modal leaves reminders off and reports the failure without a POST', async () => {
    const { app, root, calls } = setup({ answer: () => { throw new Error('modal unavailable'); } }); await app.refresh({ opening: true });
    root.find('reminders').fire('click'); await settle();
    assert.match(root.find('status-text').textContent, /No se pudo abrir la confirmación/);
    assert.equal(calls.filter((call) => call.method === 'PATCH').length, 0); assert.equal(root.find('reminders').attributes['aria-pressed'], 'false'); app.dispose();
});

test('opening selects today in the saved zone, while an early inbox navigation keeps its explicit date', async () => {
    const prefs = { ...PREFS, time_zone: 'Asia/Tokyo' };
    const { app, root } = setup({ prefs }); await app.refresh({ opening: true });
    assert.match(root.find('chosen-date').textContent, /16 de octubre/); app.dispose();
    const waiting = defer(); const early = setup({ routes: { 'GET /api/calendar/preferences': () => waiting.promise } });
    const opening = early.app.refresh({ opening: true }); early.app.navigate({ date: '2026-11-03' });
    waiting.resolve([200, prefs]); await opening; assert.match(early.root.find('chosen-date').textContent, /3 de noviembre/); early.app.dispose();
});

test('cache read errors are recoverable and never become an empty success', async () => {
    let reads = 0;
    const { app, root } = setup({ routes: { 'GET /api/calendar': () => ++reads === 1
        ? [503, { error: { message: 'Core está ocupado.' } }] : [200, SNAPSHOT] } });
    await app.refresh({ opening: true }); assert.match(root.find('status-text').textContent, /Core está ocupado/);
    root.find('retry').fire('click'); await settle(); assert.equal(root.findAll('chip').length, 1);
    assert.equal(root.find('retry'), undefined); app.dispose();
});

test('a rejected preference write restores the confirmed checkbox and zone instead of showing an unsaved choice', async () => {
    const { app, root } = setup({ routes: { 'PATCH /api/calendar/preferences': [503, { error: { message: 'No se pudo guardar.' } }] } });
    await app.refresh({ opening: true });
    const check = root.find('calendar-check'); check.checked = false; check.fire('change'); await settle();
    assert.equal(root.find('calendar-check').checked, true);
    const zone = root.find('zone'); zone.value = 'UTC'; zone.fire('change'); await settle();
    assert.equal(root.find('zone').value, PREFS.time_zone); assert.match(root.find('status-text').textContent, /No se pudo guardar/); app.dispose();
});

test('before a confirmed snapshot the detail panel says it is reading, never that the day is empty', () => {
    const { app, root } = setup();
    assert.match(root.find('day-events').text(), /Leyendo los eventos/);
    assert.ok(!/Sin eventos|No hay eventos|Conecta una cuenta/.test(root.text()));
    app.dispose();
});

test('switching to Day or Agenda awaits its new range without briefly declaring it empty', async () => {
    for (const view of ['day', 'agenda']) {
        const waiting = defer(); let reads = 0;
        const { app, root } = setup({ routes: { 'GET /api/calendar': () => ++reads === 1
            ? [200, SNAPSHOT] : waiting.promise } });
        await app.refresh({ opening: true });
        root.key(`view:${view}`).fire('click');
        assert.match(root.find('list-view').text(), /Leyendo los eventos/);
        assert.match(root.find('day-events').text(), /Leyendo los eventos/);
        assert.ok(!/Sin eventos|No hay eventos|Nada coincide/.test(root.find('list-view').text()));
        assert.ok(!/Sin eventos|No hay eventos|Sin coincidencias/.test(root.find('day-events').text()));
        waiting.resolve([200, { ...SNAPSHOT, events: [] }]); await settle();
        assert.match(root.find('list-view').text(), /Sin eventos guardados/);
        assert.match(root.find('day-events').text(), /Sin eventos guardados/);
        app.dispose();
    }
});

test('an unavailable first read leaves details unavailable and does not invent an empty day or absent account', async () => {
    const { app, root } = setup({ routes: { 'GET /api/calendar': [503, { error: { message: 'Core está ocupado.' } }] } });
    await app.refresh({ opening: true });
    assert.match(root.find('day-events').text(), /No se pudieron consultar los eventos/);
    assert.ok(!/Sin eventos|No hay eventos|Conecta una cuenta/.test(root.text()));
    app.dispose();
});

test('a failed new range is unavailable even though a different range previously had events', async () => {
    let reads = 0;
    const { app, root } = setup({ routes: { 'GET /api/calendar': () => ++reads === 1
        ? [200, SNAPSHOT] : [503, { error: { message: 'No se pudo consultar este rango.' } }] } });
    await app.refresh({ opening: true }); root.key('view:day').fire('click'); await settle();
    assert.match(root.find('day-events').text(), /No se pudieron consultar los eventos/);
    assert.ok(!/Sin eventos|No hay eventos/.test(root.find('detail').text()));
    app.dispose();
});

test('partial empty data only claims that no events could be displayed, in every pane and list view', async () => {
    for (const view of ['day', 'week', 'agenda']) {
        const { app, root } = setup({ snapshot: { ...SNAPSHOT, complete: false, events: [],
            warnings: [{ code: 'unsupported', message: 'Hay eventos que no se pudieron mostrar.' }] } });
        await app.refresh({ opening: true }); root.key(`view:${view}`).fire('click'); await settle();
        for (const part of ['list-view', 'day-events', 'empty-caption']) {
            const text = root.find(part).text();
            assert.match(text, /eventos mostrables/); assert.match(text, /incompleta/);
            assert.ok(!/Sin eventos guardados|No hay eventos guardados|Sin eventos en/.test(text));
        }
        app.dispose();
    }
});

test('an unavailable projection is not described as no calendars connected or an empty day', async () => {
    const { app, root } = setup({ snapshot: { ...SNAPSHOT, available: false, complete: false,
        accounts: [], calendars: [], events: [] } });
    await app.refresh({ opening: true }); root.key('view:agenda').fire('click'); await settle();
    for (const part of ['list-view', 'day-events', 'empty-caption']) assert.match(root.find(part).text(), /no están disponibles/);
    assert.ok(!/Conecta una cuenta|Conecta Calendario|Sin eventos/.test(root.text()));
    app.dispose();
});

const PREVIEW = { id: 'a'.repeat(32), route: { provider: 'custom:test', model: 'planner', endpoint: 'https://model.example.test/v1/' },
    sources: { event: { title: '<script>data only</script>', start: EVENT.start, end: EVENT.end, all_day: false }, notes: 'Notas inventadas' } };
const JOB = { id: PREVIEW.id, event_id: EVENT.id, title: EVENT.title, route: PREVIEW.route, sources: PREVIEW.sources,
    state: 'completed', running: false, created_at: NOW / 1000,
    result: { summary: '<img src=x onerror=bad()>', points: ['Revisar alcance'], questions: ['¿Qué falta?'], sources: ['event', 'notes'] } };
function prepRoutes(extra = {}) {
    return { 'GET /api/models/apps': [200, { default: { provider: 'custom:test', model: 'planner' },
        inventory: { providers: [{ id: 'custom:test', label: 'Mi proveedor', authenticated: true, models: ['planner', 'planner-2'] }] } }],
        'GET /api/calendar/preparation': [200, { jobs: [] }],
        'POST /api/calendar/preparation/preview': [200, { preview: PREVIEW }],
        [`POST /api/calendar/preparation/${PREVIEW.id}/start`]: [200, { job: JOB }], ...extra };
}
async function openPreparation(testbed) {
    await testbed.app.refresh({ opening: true }); testbed.root.find('chip').fire('click');
    testbed.root.find('prep-open').fire('click'); await settle();
    assert.equal(testbed.root.find('prep-provider').value, 'custom:test');
    assert.equal(testbed.root.find('prep-model').value, 'planner');
    for (const [part, value] of [['prep-notes', 'Notas inventadas']]) {
        const n = testbed.root.find(part); n.value = value; n.fire('input');
    }
    testbed.root.find('prep-preview').fire('click'); await settle();
}

test('F15 preview contains the selected event and exact fields but denial never starts a model job', async () => {
    const t = setup({ routes: prepRoutes() }); await openPreparation(t);
    const request = t.calls.find(c => c.path.endsWith('/preparation/preview'));
    assert.deepEqual(request.body, { event_id: EVENT.id, date: '2026-10-15', provider: 'custom:test', model: 'planner', description: false, notes: 'Notas inventadas' });
    assert.match(t.root.find('prep-source').textContent, /Título: <script>data only<\/script>/);
    t.root.find('prep-send').fire('click'); await settle();
    assert.match(t.confirms[0].message, /custom:test/); assert.match(t.confirms[0].message, /https:\/\/model.example.test/);
    assert.equal(t.calls.filter(c => c.path.endsWith('/start')).length, 0); t.app.dispose();
});

test('F15 confirmation submits only the reviewed intent; returned text and saved sources are inert', async () => {
    let sent = false;
    const t = setup({ answer: true, routes: prepRoutes({
        [`POST /api/calendar/preparation/${PREVIEW.id}/start`]: () => { sent = true; return [200, { job: JOB }]; },
        'GET /api/calendar/preparation': () => [200, { jobs: sent ? [JOB] : [] }],
    }) }); await openPreparation(t);
    const btn = t.root.find('prep-send'); btn.fire('click'); btn.fire('click'); await settle();
    assert.deepEqual(t.calls.find(c => c.path.endsWith('/start')).body, { confirmed: true });
    assert.equal(t.calls.filter(c => c.path.endsWith('/start')).length, 1);
    assert.equal(t.root.find('prep-summary').textContent, JOB.result.summary);
    assert.match(t.root.find('prep-saved-sources').text(), /data only/); t.app.dispose();
});

test('F15 editing a source invalidates its preview and changing selection cancels pending consent', async () => {
    const wait = defer(); const t = setup({ answer: () => wait.promise, routes: prepRoutes() }); await openPreparation(t);
    const notes = t.root.find('prep-notes'); notes.value = 'Otras notas'; notes.fire('input');
    assert.equal(t.root.find('prep-send'), undefined);
    t.root.find('prep-preview').fire('click'); await settle(); t.root.find('prep-send').fire('click');
    t.app.navigate({ date: '2026-10-16' }); wait.resolve(true); await settle();
    assert.equal(t.calls.filter(c => c.path.endsWith('/start')).length, 0); t.app.dispose();
});

test('F15 model selection reuses connected providers and changing it discards the reviewed preview', async () => {
    const t = setup({ routes: prepRoutes() }); await openPreparation(t);
    assert.equal(t.root.find('prep-provider').tag, 'select');
    const model = t.root.find('prep-model'); model.value = 'planner-2'; model.fire('change');
    assert.equal(t.root.find('prep-send'), undefined);
    t.root.find('prep-preview').fire('click'); await settle();
    assert.equal(t.calls.filter(c => c.path.endsWith('/preview')).at(-1).body.model, 'planner-2');
    assert.equal(t.calls.filter(c => c.path.endsWith('/start')).length, 0); t.app.dispose();
});

test('F15 local saved preparations remain readable when the original event is no longer in the calendar', async () => {
    const t = setup({ snapshot: { ...SNAPSHOT, events: [] }, routes: prepRoutes({ 'GET /api/calendar/preparation': [200, { jobs: [JOB] }] }) });
    await t.app.refresh({ opening: true }); t.root.find('prep-open').fire('click'); await settle();
    assert.equal(t.root.find('prep-summary').textContent, JOB.result.summary);
    assert.equal(t.root.find('prep-provider'), undefined); assert.equal(t.calls.filter(c => c.method !== 'GET').length, 0); t.app.dispose();
});

test('F15 closing the app never cancels Core work, and a failed history read is not an empty success', async () => {
    const t = setup({ routes: prepRoutes({ 'GET /api/calendar/preparation': [503, { error: { message: 'Historial no disponible.' } }] }) });
    await t.app.refresh({ opening: true }); t.root.find('prep-open').fire('click'); await settle();
    assert.match(t.root.find('prep-status').textContent, /Historial no disponible/);
    t.app.dispose(); assert.equal(t.calls.filter(c => c.path.endsWith('/cancel')).length, 0);
});

test('F15 keeps keyboard focus on a busy preview button and blocks duplicate work', async () => {
    const wait=defer();const t=setup({routes:prepRoutes({'POST /api/calendar/preparation/preview':()=>wait.promise})});
    await t.app.refresh({opening:true});t.root.find('chip').fire('click');t.root.find('prep-open').fire('click');await settle();
    const b=t.root.find('prep-preview');b.focus();b.fire('click');
    const pending=t.root.find('prep-preview');assert.equal(pending.attributes['aria-disabled'],'true');
    assert.equal(pending.disabled,false);assert.equal(globalThis.document.activeElement,pending);
    pending.fire('click');assert.equal(t.calls.filter(c=>c.path.endsWith('/preview')).length,1);
    wait.resolve([200,{preview:PREVIEW}]);await settle();
    assert.equal(globalThis.document.activeElement.dataset.key,'prep-preview');t.app.dispose();
});

test('a declined invitation stays listed but reads as declined, with no reminder promise (review 2026-10-19)', async () => {
    const declined = { ...EVENT, declined: true };
    const { app, root } = setup({ snapshot: { ...SNAPSHOT, events: [declined] } }); await app.refresh({ opening: true });
    const chip = root.find('chip');
    assert.equal(chip.dataset.declined, 'true');
    chip.fire('click');
    assert.match(root.find('detail-declined').textContent, /Rechazaste esta invitación/);
    app.dispose();
    const plain = setup(); await plain.app.refresh({ opening: true });
    plain.root.find('chip').fire('click');
    assert.equal(plain.root.find('chip').dataset.declined, undefined);
    assert.equal(plain.root.find('detail-declined'), undefined);
    plain.app.dispose();
});
