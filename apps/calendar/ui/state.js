/** Calendar dates and a shared, cache-only source. No Google request occurs in this module.
 * App and future widget can register independent consumers; equal reads share one request.
 * Hidden consumers retain their snapshot and stop polling. Remote sync is an explicit action.
 */
export const VIEWS = Object.freeze(['day', 'week', 'month', 'agenda']);
const DAY = 86400000;

export function validDate(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith('0000-')) return false;
    const date = new Date(`${value}T12:00:00Z`);
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function validTimeZone(zone) {
    try { new Intl.DateTimeFormat('en', { timeZone: zone }).format(0); return typeof zone === 'string' && !!zone; }
    catch { return false; }
}

export const defaultTimeZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';

export function dayAt(value = Date.now(), timeZone = defaultTimeZone()) {
    const parts = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' })
        .formatToParts(new Date(value));
    const take = (name) => parts.find((part) => part.type === name)?.value;
    return `${take('year')}-${take('month')}-${take('day')}`;
}

export function addDays(day, count) {
    if (!validDate(day)) throw new TypeError('Fecha no válida.');
    return new Date(Date.parse(`${day}T12:00:00Z`) + count * DAY).toISOString().slice(0, 10);
}

export function addMonths(day, count) {
    if (!validDate(day)) throw new TypeError('Fecha no válida.');
    const [year, month, date] = day.split('-').map(Number);
    const first = new Date(`${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-01T12:00:00Z`);
    first.setUTCMonth(first.getUTCMonth() + count);
    const last = new Date(first.getTime());
    last.setUTCMonth(last.getUTCMonth() + 1, 0);
    first.setUTCDate(Math.min(date, last.getUTCDate()));
    return first.toISOString().slice(0, 10);
}

export const weekStart = (day) => addDays(day, -new Date(`${day}T12:00:00Z`).getUTCDay());
export const monthDays = (day) => {
    const first = `${day.slice(0, 7)}-01`;
    const start = weekStart(first);
    return Array.from({ length: 42 }, (_, index) => addDays(start, index));
};

export function viewRange(day, view = 'month') {
    if (!validDate(day)) throw new TypeError('Fecha no válida.');
    if (view === 'day') return { start: day, end: addDays(day, 1) };
    if (view === 'week') { const start = weekStart(day); return { start, end: addDays(start, 7) }; }
    if (view === 'agenda') return { start: day, end: addDays(day, 31) };
    const days = monthDays(day);
    return { start: days[0], end: addDays(days.at(-1), 1) };
}

export function moveDate(day, view, direction) {
    return view === 'day' ? addDays(day, direction) : view === 'week' ? addDays(day, direction * 7) : addMonths(day, direction);
}

export function dateLabel(day, options = {}) {
    return new Intl.DateTimeFormat('es', { timeZone: 'UTC', day: 'numeric', month: 'long', year: 'numeric', ...options })
        .format(new Date(`${day}T12:00:00Z`));
}

export function eventOnDay(event, day, timeZone) {
    if (!validDate(day)) return false;
    if (event.all_day) return validDate(event.start_date) && validDate(event.end_date)
        && event.start_date <= day && event.end_date > day;
    const start = Date.parse(event.start), end = Date.parse(event.end);
    return Number.isFinite(start) && Number.isFinite(end) && end > start
        && dayAt(start, timeZone) <= day && dayAt(end - 1, timeZone) >= day;
}

/** Convert each timed event only twice, then group by plain Gregorian dates.
 * A month and its mini-calendar must not repeat expensive zone conversions for every cell.
 */
export function eventsByDay(events, { start, end, timeZone }) {
    const grouped = new Map();
    if (!validDate(start) || !validDate(end) || start >= end) return grouped;
    for (const event of events) {
        let first, after;
        if (event.all_day) {
            if (!validDate(event.start_date) || !validDate(event.end_date) || event.start_date >= event.end_date) continue;
            first = event.start_date; after = event.end_date;
        } else {
            const from = Date.parse(event.start), to = Date.parse(event.end);
            if (!Number.isFinite(from) || !Number.isFinite(to) || from >= to) continue;
            first = dayAt(from, timeZone); after = addDays(dayAt(to - 1, timeZone), 1);
        }
        for (let day = first < start ? start : first, stop = after > end ? end : after; day < stop; day = addDays(day, 1)) {
            if (!grouped.has(day)) grouped.set(day, []);
            grouped.get(day).push(event);
        }
    }
    return grouped;
}

export function sortEvents(events) {
    return [...events].sort((a, b) => Number(b.all_day) - Number(a.all_day)
        || (a.all_day ? String(a.start_date).localeCompare(String(b.start_date)) : Date.parse(a.start) - Date.parse(b.start))
        || String(a.id).localeCompare(String(b.id)));
}

export function filterEvents(events, { hidden = [], query = '', account = '' } = {}) {
    const excluded = new Set(hidden);
    const words = String(query).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es').split(/\s+/).filter(Boolean);
    return sortEvents(events.filter((event) => !excluded.has(event.calendar_key) && (!account || event.account_id === account)
        && words.every((word) => `${event.title || ''} ${event.location || ''} ${event.description || ''}`
            .normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es').includes(word))));
}

export function safeMeetingUrl(value) {
    if (typeof value !== 'string' || !/^https:\/\/[^/]/i.test(value) || value.length > 2000 || /[\x00-\x20\\]/.test(value)
        || /^https:\/\/[^/?#]*@/i.test(value)) return null;
    try {
        const url = new URL(value);
        if (url.protocol !== 'https:' || url.username || url.password || (url.port && url.port !== '443')) return null;
        // The server extracts only conference video entry points. Match its public HTTPS URL
        // contract so Jitsi/Webex and other video providers work as well as Meet/Teams/Zoom.
        const host = url.hostname.toLowerCase().replace(/\.$/, '');
        const parts = host.split('.');
        const local = ['localhost', 'local', 'localdomain', 'internal', 'lan', 'home', 'test', 'invalid', 'example'];
        if (parts.length < 2 || host.length > 253 || /^\d+\.\d+\.\d+\.\d+$/.test(host)
            || local.some((suffix) => host === suffix || host.endsWith(`.${suffix}`))
            || !parts.every((part) => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(part))
            || !/^(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})$/.test(parts.at(-1))) return null;
        url.hostname = host;
        return url.href;
    } catch { return null; }
}

function readFailure(response, payload) {
    const error = new Error(payload?.error?.message || (response.status === 403
        ? 'El calendario solo se puede consultar desde este equipo.' : 'No se pudo consultar el calendario.'));
    error.status = response.status;
    error.code = payload?.error?.code || '';
    return error;
}

export function createCalendarSource({ fetchImpl = (...args) => globalThis.fetch(...args), pollMs = 30000, maxCachedRanges = 12,
    setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
    const cache = new Map(), pending = new Map(), consumers = new Set();
    let timer = null, disposed = false, preferences = null, preferenceRead = null;
    const acceptPreferences = (value) => {
        if (!value || !Number.isSafeInteger(value.revision) || !validTimeZone(value.time_zone)
            || !Array.isArray(value.hidden_calendars) || typeof value.reminders_enabled !== 'boolean') throw new Error('Preferencias de Calendario no válidas.');
        if (preferences && value.revision < preferences.revision) return preferences;
        preferences = structuredClone(value);
        for (const [key, record] of [...cache]) if (record.data) publish(key, { ...record, data: { ...record.data, preferences } });
        return preferences;
    };
    const keyOf = (range) => `${range.start}|${range.end}|${range.time_zone}`;
    const publish = (key, record) => {
        cache.delete(key);
        cache.set(key, record);
        const active = new Set([...consumers].map((consumer) => consumer.key));
        for (const cached of cache.keys()) {
            if (cache.size <= Math.max(1, maxCachedRanges)) break;
            if (!active.has(cached)) cache.delete(cached);
        }
        for (const consumer of consumers) if (consumer.key === key) consumer.notify(record);
    };
    const cancelTimer = () => { clearTimer(timer); timer = null; };
    const schedule = () => {
        cancelTimer();
        if (disposed || ![...consumers].some((consumer) => consumer.visible && consumer.range)) return;
        timer = setTimer(async () => {
            timer = null;
            const ranges = new Map([...consumers].filter((consumer) => consumer.visible && consumer.range)
                .map((consumer) => [consumer.key, consumer.range]));
            await Promise.allSettled([...ranges.values()].map(read));
            schedule();
        }, pollMs);
    };
    const retireUnused = () => {
        for (const [key, entry] of pending) if (![...consumers].some((consumer) => consumer.key === key)) {
            entry.controller.abort(); pending.delete(key);
        }
    };
    function read(range) {
        if (disposed) return Promise.resolve();
        const key = keyOf(range);
        if (pending.has(key)) return pending.get(key).promise;
        const controller = new AbortController();
        const previous = cache.get(key);
        const entry = { controller, promise: null };
        pending.set(key, entry);
        publish(key, { data: previous?.data || null, loading: true, error: '' });
        entry.promise = (async () => {
            try {
                const query = new URLSearchParams(range);
                const response = await fetchImpl(`/api/calendar?${query}`, { cache: 'no-store', signal: controller.signal });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw readFailure(response, data);
                if (data.schema !== 'iris.calendar/v1' || !Array.isArray(data.events) || !Array.isArray(data.calendars)
                    || !Array.isArray(data.accounts)) throw new Error('Iris devolvió un calendario no válido.');
                if (disposed || pending.get(key) !== entry || controller.signal.aborted) return;
                if (data.preferences && (!preferences || data.preferences.revision >= preferences.revision)) preferences = data.preferences;
                publish(key, { data: { ...data, ...(preferences ? { preferences } : {}) }, loading: false, error: '' });
            } catch (error) {
                if (disposed || pending.get(key) !== entry || controller.signal.aborted) return;
                publish(key, { data: previous?.data || null, loading: false, error: error?.message || 'No se pudo consultar el calendario.' });
            } finally {
                if (pending.get(key) === entry) pending.delete(key);
            }
        })();
        return entry.promise;
    }
    return {
        loadPreferences({ force = false } = {}) {
            if (disposed) return Promise.reject(new Error('El calendario ya está cerrado.'));
            if (preferenceRead) return preferenceRead.promise;
            if (preferences && !force) return Promise.resolve(preferences);
            const entry = { controller: new AbortController() }; preferenceRead = entry;
            entry.promise = (async () => {
                try {
                    const response = await fetchImpl('/api/calendar/preferences', { cache: 'no-store', signal: entry.controller.signal });
                    const value = await response.json(); if (!response.ok) throw readFailure(response, value);
                    if (disposed || entry.controller.signal.aborted) return null;
                    return acceptPreferences(value);
                } finally { if (preferenceRead === entry) preferenceRead = null; }
            })();
            return entry.promise;
        },
        setPreferences: acceptPreferences,
        acquire(notify) {
            if (disposed) throw new Error('El calendario ya está cerrado.');
            const consumer = { notify, range: null, key: '', visible: false };
            consumers.add(consumer);
            return {
                read(range, { force = true } = {}) {
                    if (!consumers.has(consumer)) return Promise.resolve();
                    consumer.range = { ...range };
                    consumer.key = keyOf(range);
                    retireUnused();
                    const stored = cache.get(consumer.key);
                    if (stored) notify(stored);
                    schedule();
                    return force || !stored || stored.loading ? read(range) : Promise.resolve();
                },
                setVisible(value) {
                    consumer.visible = Boolean(value);
                    if (!consumer.visible) for (const [key, entry] of pending) {
                        if (![...consumers].some((item) => item.visible && item.key === key)) { entry.controller.abort(); pending.delete(key); }
                    }
                    schedule();
                },
                release() {
                    consumers.delete(consumer); retireUnused();
                    if (consumer.visible) for (const [key, entry] of pending) {
                        if (![...consumers].some((item) => item.visible && item.key === key)) { entry.controller.abort(); pending.delete(key); }
                    }
                    schedule();
                },
            };
        },
        dispose() {
            disposed = true; cancelTimer();
            preferenceRead?.controller.abort(); preferenceRead = null;
            for (const { controller } of pending.values()) controller.abort();
            pending.clear(); consumers.clear(); cache.clear();
        },
    };
}

let sharedSource;
export const getCalendarSource = () => (sharedSource ||= createCalendarSource());
