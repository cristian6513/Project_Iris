import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validDate, dayAt, addDays, addMonths, monthDays, viewRange, moveDate, eventOnDay, eventsByDay, sortEvents,
    filterEvents, safeMeetingUrl, createCalendarSource } from '../ui/state.js';

const defer = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; };
const snapshot = (events = []) => ({ schema: 'iris.calendar/v1', accounts: [], calendars: [], events });
const response = (data = snapshot(), status = 200) => ({ ok: status < 400, status, json: async () => data });
const RANGE = { start: '2026-10-01', end: '2026-11-01', time_zone: 'America/Bogota' };

test('strict Gregorian dates, leap days and month boundaries', () => {
    assert.equal(validDate('2024-02-29'), true);
    assert.equal(validDate('2025-02-29'), false);
    assert.equal(validDate('2026-2-01'), false);
    assert.equal(validDate('0000-01-01'), false);
    assert.equal(addDays('2024-02-28', 1), '2024-02-29');
    assert.equal(addDays('2024-02-29', 1), '2024-03-01');
    assert.equal(addMonths('2024-01-31', 1), '2024-02-29');
    assert.equal(addMonths('2026-01-31', -1), '2025-12-31');
});

test('six-week month includes adjacent days and each view has an exclusive end', () => {
    const days = monthDays('2026-10-15');
    assert.equal(days.length, 42); assert.equal(days[0], '2026-09-27'); assert.equal(days.at(-1), '2026-11-07');
    assert.deepEqual(viewRange('2026-10-15'), { start: '2026-09-27', end: '2026-11-08' });
    assert.deepEqual(viewRange('2026-10-15', 'week'), { start: '2026-10-11', end: '2026-10-18' });
    assert.deepEqual(viewRange('2026-10-15', 'day'), { start: '2026-10-15', end: '2026-10-16' });
    assert.deepEqual(viewRange('2026-10-15', 'agenda'), { start: '2026-10-15', end: '2026-11-15' });
    assert.equal(moveDate('2026-12-29', 'week', 1), '2027-01-05');
});

test('dates use the chosen zone on both sides of UTC midnight', () => {
    assert.equal(dayAt('2026-10-16T02:00:00Z', 'America/Bogota'), '2026-10-15');
    assert.equal(dayAt('2026-10-16T02:00:00Z', 'Asia/Tokyo'), '2026-10-16');
});

test('an all-day range ends exclusively, without converting its dates to local midnight', () => {
    const event = { all_day: true, start_date: '2026-10-14', end_date: '2026-10-16' };
    assert.equal(eventOnDay(event, '2026-10-13', 'Pacific/Honolulu'), false);
    assert.equal(eventOnDay(event, '2026-10-14', 'Pacific/Honolulu'), true);
    assert.equal(eventOnDay(event, '2026-10-15', 'Asia/Tokyo'), true);
    assert.equal(eventOnDay(event, '2026-10-16', 'Asia/Tokyo'), false);
});

test('timed events span local dates and do not leak into the day of an exclusive midnight', () => {
    const event = { all_day: false, start: '2026-10-15T04:00:00Z', end: '2026-10-16T05:00:00Z' };
    assert.equal(eventOnDay(event, '2026-10-14', 'America/Bogota'), true);
    assert.equal(eventOnDay(event, '2026-10-15', 'America/Bogota'), true);
    assert.equal(eventOnDay(event, '2026-10-16', 'America/Bogota'), false);
});

test('DST spring and autumn changes retain the correct local day', () => {
    for (const event of [
        { start: '2026-03-08T06:30:00Z', end: '2026-03-08T07:30:00Z', day: '2026-03-08' },
        { start: '2026-11-01T05:30:00Z', end: '2026-11-01T06:30:00Z', day: '2026-11-01' },
    ]) {
        assert.equal(eventOnDay(event, event.day, 'America/New_York'), true);
        assert.equal(eventOnDay(event, addDays(event.day, 1), 'America/New_York'), false);
    }
});

test('day grouping agrees with event membership across DST, midnight and all-day boundaries', () => {
    const events = [
        { id: 'dst', start: '2026-03-08T06:30:00Z', end: '2026-03-08T07:30:00Z' },
        { id: 'midnight', start: '2026-03-07T22:00:00-05:00', end: '2026-03-09T00:00:00-04:00' },
        { id: 'all-day', all_day: true, start_date: '2026-03-07', end_date: '2026-03-09' },
    ];
    const grouped = eventsByDay(events, { start: '2026-03-06', end: '2026-03-11', timeZone: 'America/New_York' });
    for (let day = '2026-03-06'; day < '2026-03-11'; day = addDays(day, 1)) {
        assert.deepEqual((grouped.get(day) || []).map((event) => event.id), events.filter((event) => eventOnDay(event, day, 'America/New_York')).map((event) => event.id));
    }
});

test('day grouping converts each timed event twice instead of once per month cell', () => {
    const Original = Intl.DateTimeFormat; let conversions = 0;
    Intl.DateTimeFormat = new Proxy(Original, { construct(target, args) { conversions += 1; return Reflect.construct(target, args); } });
    try {
        const events = Array.from({ length: 100 }, (_, index) => ({ id: String(index), start: '2026-10-15T17:00:00Z', end: '2026-10-15T18:00:00Z' }));
        const grouped = eventsByDay(events, { start: '2026-09-27', end: '2026-11-08', timeZone: 'America/Bogota' });
        assert.equal(grouped.get('2026-10-15').length, 100); assert.equal(conversions, 200);
    } finally { Intl.DateTimeFormat = Original; }
});

test('query words, accents, account and hidden calendars compose without modifying the source', () => {
    const events = [
        { id: 'a', account_id: 'one', calendar_key: 'work', title: 'Reunión equipo', location: 'Bogotá', start: '2026-10-15T17:00Z' },
        { id: 'b', account_id: 'two', calendar_key: 'work2', title: 'Reunión equipo', location: 'Bogotá', start: '2026-10-15T17:00Z' },
        { id: 'c', account_id: 'one', calendar_key: 'private', title: 'Reunión equipo', location: 'Bogotá', start: '2026-10-15T17:00Z' },
    ];
    assert.deepEqual(filterEvents(events, { account: 'one', query: 'bogota reunion', hidden: ['private'] }).map((event) => event.id), ['a']);
    assert.equal(events.length, 3);
});

test('video conference actions follow the server public HTTPS contract across providers', () => {
    for (const url of ['https://meet.google.com/abc-defg-hij', 'https://company.zoom.us/j/123', 'https://teams.microsoft.com/l/meetup-join/abc',
        'https://meet.jit.si/iris-demo', 'https://company.webex.com/meet/demo']) assert.ok(safeMeetingUrl(url));
    for (const url of ['javascript:alert(1)', 'http://meet.google.com/a', 'https://meet.google.com.evil.test/a', 'https://user@meet.google.com/a',
        'https://127.0.0.1/a', 'https://[::1]/a', 'https://intranet.local/a', 'https://meet.google.com:444/a', 'https://meet.google.com/\\bad',
        'https://meet.google.com/\nbad', 'https://@meet.google.com/a', 'https:////meet.google.com/a']) assert.equal(safeMeetingUrl(url), null);
});

test('timed events are ordered by instant rather than the spelling of their UTC offsets', () => {
    const events = [{ id: 'later', start: '2026-10-15T12:00:00-05:00' }, { id: 'earlier', start: '2026-10-15T16:00:00Z' }];
    assert.deepEqual(sortEvents(events).map((event) => event.id), ['earlier', 'later']);
});

test('two consumers requesting the same range share one cache read', async () => {
    const waiting = defer(), calls = [], first = [], second = [];
    const source = createCalendarSource({ fetchImpl: async (url, init) => { calls.push({ url, init }); return waiting.promise; } });
    const a = source.acquire((record) => first.push(record)), b = source.acquire((record) => second.push(record));
    const one = a.read(RANGE), two = b.read(RANGE);
    assert.equal(calls.length, 1); assert.equal(calls[0].init.cache, 'no-store');
    waiting.resolve(response(snapshot([{ id: 'saved' }]))); await Promise.all([one, two]);
    assert.equal(first.at(-1).data.events[0].id, 'saved'); assert.equal(second.at(-1).data.events[0].id, 'saved');
    assert.ok(calls[0].url.includes('time_zone=America%2FBogota'));
    source.dispose();
});

test('a previous range cannot publish into a consumer after navigation even if its abort is ignored', async () => {
    const waits = [defer(), defer()], signals = [], seen = [];
    let index = 0;
    const source = createCalendarSource({ fetchImpl: async (_url, init) => { signals.push(init.signal); return waits[index++].promise; } });
    const consumer = source.acquire((record) => seen.push(record));
    const old = consumer.read(RANGE), current = consumer.read({ ...RANGE, start: '2026-11-01', end: '2026-12-01' });
    assert.equal(signals[0].aborted, true);
    waits[1].resolve(response(snapshot([{ id: 'new' }]))); await current;
    waits[0].resolve(response(snapshot([{ id: 'old' }]))); await old;
    assert.equal(seen.at(-1).data.events[0].id, 'new'); source.dispose();
});

test('a failed cache read retains the previous result and reports an error instead of an empty agenda', async () => {
    let first = true;
    const source = createCalendarSource({ fetchImpl: async () => first
        ? (first = false, response(snapshot([{ id: 'kept' }]))) : response({ error: { message: 'Sin conexión con Core.' } }, 503) });
    const seen = [], consumer = source.acquire((record) => seen.push(record));
    await consumer.read(RANGE); await consumer.read(RANGE);
    assert.equal(seen.at(-1).data.events[0].id, 'kept'); assert.equal(seen.at(-1).error, 'Sin conexión con Core.');
    source.dispose();
});

test('one visual poll exists for both consumers; hiding both clears it without any sync POST', async () => {
    const timers = new Map(), calls = []; let id = 0;
    const source = createCalendarSource({ fetchImpl: async (url, init) => { calls.push({ url, init }); return response(); },
        setTimer: (fn) => { timers.set(++id, fn); return id; }, clearTimer: (key) => timers.delete(key) });
    const a = source.acquire(() => {}), b = source.acquire(() => {});
    await a.read(RANGE); await b.read(RANGE, { force: false });
    assert.equal(timers.size, 0);
    a.setVisible(true); b.setVisible(true); assert.equal(timers.size, 1);
    const run = [...timers.values()][0]; timers.clear(); await run();
    assert.equal(calls.length, 2, 'initial read plus one shared poll');
    assert.ok(calls.every((call) => !call.init.method || call.init.method === 'GET'));
    a.setVisible(false); assert.equal(timers.size, 1);
    b.setVisible(false); assert.equal(timers.size, 0); source.dispose();
});

test('releasing one view does not abort a request still used by another; retiring the last does', async () => {
    const wait = defer(); let signal;
    const source = createCalendarSource({ fetchImpl: async (_url, init) => { signal = init.signal; return wait.promise; } });
    const a = source.acquire(() => {}), b = source.acquire(() => {});
    const reading = a.read(RANGE); b.read(RANGE); a.release(); assert.equal(signal.aborted, false);
    b.release(); assert.equal(signal.aborted, true); wait.resolve(response()); await reading; source.dispose();
});

test('the shared source bounds inactive cached periods while retaining an active widget range', async () => {
    const calls = [];
    const source = createCalendarSource({ maxCachedRanges: 2, fetchImpl: async (url) => { calls.push(url); return response(); } });
    const app = source.acquire(() => {}), widget = source.acquire(() => {});
    await widget.read(RANGE);
    for (let month = 2; month <= 5; month += 1) {
        await app.read({ ...RANGE, start: `2026-0${month}-01`, end: `2026-0${month + 1}-01` });
    }
    const before = calls.length;
    await widget.read(RANGE, { force: false }); assert.equal(calls.length, before, 'active widget stays cached');
    await app.read({ ...RANGE, start: '2026-02-01', end: '2026-03-01' }, { force: false });
    assert.equal(calls.length, before + 1, 'old inactive period is loaded again instead of accumulating forever'); source.dispose();
});
