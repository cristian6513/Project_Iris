/** Calendario F11. Cache-only on open; refreshing Google and visual reminders need a click.
 * The three panes follow the user's reference inside Iris' existing window, without a second shell.
 */
import { VIEWS, validDate, validTimeZone, defaultTimeZone, dayAt, addDays, monthDays, viewRange,
    moveDate, dateLabel, eventOnDay, eventsByDay, filterEvents, safeMeetingUrl, getCalendarSource, createCalendarSource } from './state.js?v=2';
import { createPreparation } from './preparation.js?v=2';

const CSS = 'iris-calendar';
const VIEW_LABELS = Object.freeze({ day: 'Día', week: 'Semana', month: 'Mes', agenda: 'Agenda' });
const WEEKDAYS = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];
const clock = (iso, zone) => new Intl.DateTimeFormat('es', { timeZone: zone, hour: '2-digit', minute: '2-digit' }).format(new Date(iso));

export function createApp(context = {}, { fetchImpl = null, source = null,
    now = () => Date.now() } = {}) {
    const document = globalThis.document;
    const state = {
        date: dayAt(now()), view: 'month', query: '', selected: '', account: '',
        zone: defaultTimeZone(), hidden: [], reminders: false, revision: 0,
        data: null, loading: true, error: '', notice: '', syncing: false, saving: false, confirming: false,
    };
    // An injected fetch is an isolated source (tests/preview). Production shares the consumer source.
    const request = fetchImpl || ((...args) => globalThis.fetch(...args));
    const ownSource = !source && Boolean(fetchImpl);
    const calendarSource = source || (ownSource ? createCalendarSource({ fetchImpl }) : getCalendarSource());
    let root = null, disposed = false, visible = false, preferencesLoaded = false, bootstrap = null;
    let preferenceEpoch = 0, navigationEpoch = 0;
    let renderedEvents = [], renderedDays = new Map();
    const preparation = createPreparation({ document, call, confirm: context.confirm, fetchImpl: request, changed: () => render() });
    const controllers = new Set();
    const consumer = calendarSource.acquire((record) => {
        if (disposed) return;
        const before = JSON.stringify([state.data, state.loading, state.error]);
        state.data = record.data;
        state.loading = record.loading;
        state.error = record.error;
        const oldZone = state.zone;
        if (record.data?.preferences && record.data.preferences.revision >= state.revision && !state.saving) acceptPreferences(record.data.preferences);
        if (visible && state.zone !== oldZone) queueMicrotask(() => { if (!disposed && visible) void consumer.read(activeRange()); });
        if (state.account && state.data && !state.data.accounts.some((account) => account.id === state.account)) state.account = '';
        if (state.selected && state.data && !state.data.events.some((event) => event.id === state.selected)) state.selected = '';
        if (state.data) context.setBrief?.(`${state.data.events.length} ${state.data.events.length === 1 ? 'evento' : 'eventos'}`);
        if (before !== JSON.stringify([state.data, state.loading, state.error])) render();
    });

    const el = (tag, part = '', text = '', key = '') => {
        const node = document.createElement(tag);
        node.className = part ? `${CSS}-${part}` : CSS;
        node.dataset.part = part || 'app';
        if (key) node.dataset.key = key;
        if (text) node.textContent = text;
        return node;
    };
    const button = (part, text, key, action, { disabled = false, label = '', selected = false } = {}) => {
        const node = el('button', part, text, key);
        node.type = 'button'; node.disabled = disabled;
        if (label) node.setAttribute('aria-label', label);
        if (selected) node.dataset.selected = 'true';
        node.addEventListener('click', () => { if (!node.disabled) void action(); });
        return node;
    };
    const connected = () => (state.data?.accounts || []).filter((account) => Array.isArray(account.granted) && account.granted.includes('calendar.read')
        && ['connected', 'partial'].includes(account.auth_state));
    const syncTargets = () => connected().filter((account) => !state.account || account.id === state.account).map((account) => account.id);
    const allEvents = () => renderedEvents;
    const dayEvents = (day) => renderedDays.get(day) || [];
    const activeRange = () => ({ ...viewRange(state.date, state.view), time_zone: state.zone });
    const today = () => dayAt(now(), state.zone);
    const shortMonth = (day) => dateLabel(day, { day: undefined, month: 'long', year: 'numeric' });
    const color = (node, value) => {
        // Only a literal hex colour from the calendar record reaches CSS, never a CSS expression.
        if (/^#[0-9a-f]{6}$/i.test(value || '')) node.style?.setProperty('--calendar-color', value);
    };

    async function call(path, { method = 'GET', body } = {}) {
        const controller = new AbortController(); controllers.add(controller);
        try {
            const response = await request(`/api/calendar${path}`, { method, cache: 'no-store', signal: controller.signal,
                headers: body ? { 'Content-Type': 'application/json' } : undefined,
                body: body ? JSON.stringify(body) : undefined });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                const error = new Error(payload?.error?.message || 'Iris no pudo completar la petición.');
                error.status = response.status; throw error;
            }
            return payload;
        } finally { controllers.delete(controller); }
    }

    function acceptPreferences(prefs) {
        if (!prefs || !Number.isInteger(prefs.revision)) throw new Error('No se pudieron leer las preferencias del calendario.');
        state.revision = prefs.revision;
        if (prefs.revision > 0 && validTimeZone(prefs.time_zone)) state.zone = prefs.time_zone;
        state.hidden = Array.isArray(prefs.hidden_calendars) ? prefs.hidden_calendars.filter((key) => typeof key === 'string') : [];
        state.reminders = prefs.reminders_enabled === true;
    }

    async function start() {
        if (bootstrap) return bootstrap;
        bootstrap = (async () => {
            try {
                acceptPreferences(await calendarSource.loadPreferences());
                if (!navigationEpoch) state.date = dayAt(now(), state.zone);
                preferencesLoaded = true;
            } catch (error) {
                if (disposed || error?.name === 'AbortError') return;
                state.notice = 'No se pudieron leer tus preferencias. Puedes consultar el calendario; vuelve a abrirlo para recuperarlas.';
            }
            if (!disposed) await consumer.read(activeRange());
        })();
        return bootstrap.finally(() => { bootstrap = null; });
    }

    async function savePreferences(changes) {
        if (state.saving || disposed || !preferencesLoaded) return;
        const epoch = ++preferenceEpoch;
        state.saving = true; state.notice = ''; render();
        try {
            const prefs = await call('/preferences', { method: 'PATCH', body: { revision: state.revision,
                time_zone: state.zone, hidden_calendars: state.hidden, reminders_enabled: state.reminders, ...changes } });
            if (disposed || epoch !== preferenceEpoch) return;
            const oldZone = state.zone;
            acceptPreferences(prefs);
            calendarSource.setPreferences(prefs);
            state.notice = 'Preferencia guardada.';
            if (state.zone !== oldZone) { state.selected = ''; await consumer.read(activeRange()); }
        } catch (error) {
            if (disposed || epoch !== preferenceEpoch || error?.name === 'AbortError') return;
            state.notice = error?.message || 'No se pudo guardar la preferencia.';
            if (error?.status === 409) {
                try { acceptPreferences(await call('/preferences')); await consumer.read(activeRange()); }
                catch { state.notice += ' Vuelve a abrir Calendario para recuperar el estado.'; }
            }
        } finally {
            if (!disposed && epoch === preferenceEpoch) { state.saving = false; render(); }
        }
    }

    async function sync() {
        if (state.syncing || disposed || !syncTargets().length) return;
        state.syncing = true; state.notice = ''; render();
        try {
            const accountIds = syncTargets();
            const result = await call('/sync', { method: 'POST', body: { account_ids: accountIds } });
            if (disposed) return;
            state.notice = result.accepted === true
                ? (result.errors?.length ? 'Solo se pudo solicitar la actualización de algunas cuentas. Revisa Conexiones.'
                    : 'Actualización solicitada. Los datos guardados se mostrarán mientras Google responde.')
                : 'No se pudo solicitar la actualización. Revisa el estado de las cuentas en Conexiones.';
            await consumer.read(activeRange());
        } catch (error) {
            if (!disposed && error?.name !== 'AbortError') state.notice = error?.message || 'No se pudo pedir la actualización.';
        } finally { if (!disposed) { state.syncing = false; render(); } }
    }

    async function remindersToggle() {
        if (state.saving || state.confirming || disposed) return;
        const enabled = !state.reminders;
        if (enabled) {
            state.confirming = true; render();
            let agreed = false;
            try {
                agreed = await context.confirm?.({ title: 'Recordatorios de Calendario',
                    message: 'Iris añadirá avisos visuales a la Bandeja 60 minutos antes y al comenzar tus eventos con hora. No avisará de los eventos de todo el día. No usará voz ni IA.',
                    question: '¿Activar estos recordatorios?', confirmLabel: 'Activar recordatorios', cancelLabel: 'Ahora no' });
            } catch {
                if (!disposed) state.notice = 'No se pudo abrir la confirmación. Los recordatorios siguen apagados.';
            } finally { state.confirming = false; render(); }
            if (!agreed || disposed) return;
        }
        await savePreferences({ reminders_enabled: enabled });
    }

    function chooseDay(day, { eventId = '', fetch = true } = {}) {
        if (!validDate(day) || disposed) return;
        const before = JSON.stringify(activeRange());
        navigationEpoch += 1;
        state.date = day; state.selected = eventId;
        render();
        if (fetch && before !== JSON.stringify(activeRange())) void consumer.read(activeRange());
    }

    function setView(view) {
        if (!VIEWS.includes(view) || view === state.view) return;
        state.view = view; render(); void consumer.read(activeRange());
    }

    function eventButton(event, part = 'event', { detail = true } = {}) {
        const label = event.all_day ? 'Todo el día' : clock(event.start, state.zone);
        const node = button(part, '', `event:${part}:${event.id}`, () => {
            state.selected = event.id;
            if (!eventOnDay(event, state.date, state.zone)) state.date = event.all_day ? event.start_date : dayAt(event.start, state.zone);
            render();
        }, { selected: state.selected === event.id, label: `${label}. ${event.title || 'Sin título'}${event.declined ? '. Rechazada' : ''}` });
        color(node, state.data?.calendars.find((calendar) => calendar.key === event.calendar_key)?.color);
        node.append(el('span', `${part}-time`, label), el('strong', `${part}-title`, event.title || 'Sin título'));
        // A declined invitation stays visible, as in Google Calendar's option, but reads as not attended.
        if (event.declined) { node.dataset.declined = 'true'; node.append(el('small', `${part}-declined`, 'Rechazada')); }
        if (detail && event.location) node.append(el('small', `${part}-location`, event.location));
        return node;
    }

    function dayKey(event, day, { mini = false } = {}) {
        let next;
        if (event.key === 'ArrowRight') next = addDays(day, 1);
        else if (event.key === 'ArrowLeft') next = addDays(day, -1);
        else if (event.key === 'ArrowDown') next = addDays(day, 7);
        else if (event.key === 'ArrowUp') next = addDays(day, -7);
        else if (event.key === 'PageDown') next = moveDate(day, 'month', 1);
        else if (event.key === 'PageUp') next = moveDate(day, 'month', -1);
        if (!next) return;
        event.preventDefault?.(); chooseDay(next);
        findKey(`${mini ? 'mini' : 'date'}:${next}`)?.focus?.();
    }

    function calendarMonth({ mini = false } = {}) {
        const wrap = el('div', mini ? 'mini-grid' : 'month-grid');
        wrap.setAttribute('role', 'grid'); wrap.setAttribute('aria-label', shortMonth(state.date));
        const headings = el('div', mini ? 'mini-row' : 'month-row'); headings.setAttribute('role', 'row');
        WEEKDAYS.forEach((name) => {
            const head = el('span', mini ? 'mini-weekday' : 'weekday', mini ? name[0] : name);
            head.setAttribute('role', 'columnheader'); headings.append(head);
        });
        wrap.append(headings);
        let row;
        for (const [index, day] of monthDays(state.date).entries()) {
            if (index % 7 === 0) { row = el('div', mini ? 'mini-row' : 'month-row'); row.setAttribute('role', 'row'); wrap.append(row); }
            const cell = el('div', mini ? 'mini-cell' : 'day-cell');
            cell.setAttribute('role', 'gridcell');
            cell.dataset.outside = String(day.slice(0, 7) !== state.date.slice(0, 7));
            cell.dataset.today = String(day === today());
            cell.dataset.chosen = String(day === state.date);
            const date = button(mini ? 'mini-date' : 'date', String(Number(day.slice(-2))), `${mini ? 'mini' : 'date'}:${day}`, () => chooseDay(day),
                { label: dateLabel(day, { weekday: 'long' }) });
            date.tabIndex = day === state.date ? 0 : -1;
            date.setAttribute('aria-pressed', String(day === state.date));
            if (day === today()) date.setAttribute('aria-current', 'date');
            date.addEventListener('keydown', (event) => dayKey(event, day, { mini }));
            cell.append(date);
            const events = dayEvents(day);
            if (mini && events.length) { const dot = el('span', 'mini-dot'); dot.setAttribute('aria-hidden', 'true'); cell.append(dot); }
            else if (!mini) {
                cell.append(...events.slice(0, 2).map((event) => eventButton(event, 'chip', { detail: false })));
                if (events.length > 2) cell.append(button('more', `+${events.length - 2} más`, `more:${day}`, () => {
                    chooseDay(day); setView('day');
                }, { label: `Ver los ${events.length} eventos del ${dateLabel(day)}` }));
            }
            row.append(cell);
        }
        return wrap;
    }

    function sidebar() {
        const rail = el('aside', 'rail');
        const intro = el('div', 'intro');
        intro.append(el('span', 'eyebrow', 'TU TIEMPO, EN IRIS'), el('h2', 'app-title', 'Calendario'),
            el('p', 'read-only', 'Solo lectura · Google Calendar'),
            el('p', 'read-only', 'Eventos y cumpleaños. Las tareas de Google Tasks no se incluyen.'));
        const mini = el('section', 'mini');
        const miniHead = el('div', 'mini-head');
        miniHead.append(button('mini-prev', '‹', 'mini-prev', () => chooseDay(moveDate(state.date, 'month', -1)), { label: 'Mes anterior' }),
            el('strong', 'mini-title', shortMonth(state.date)),
            button('mini-next', '›', 'mini-next', () => chooseDay(moveDate(state.date, 'month', 1)), { label: 'Mes siguiente' }));
        mini.append(miniHead, calendarMonth({ mini: true }));
        rail.append(intro, mini);
        const accounts = el('section', 'filters');
        accounts.append(el('h3', 'section-title', 'MIS CALENDARIOS'));
        const calendars = state.data?.calendars || [];
        for (const account of state.data?.accounts || []) {
            const own = calendars.filter((calendar) => calendar.account_id === account.id);
            const group = el('div', 'calendar-group');
            group.append(el('p', 'account-label', account.label || account.email || 'Cuenta de Google'));
            if (!own.length) group.append(el('small', 'account-state', account.auth_state === 'reauth_required' ? 'Hay que reconectar esta cuenta.'
                : !account.granted?.includes('calendar.read') ? 'Sin permiso de Calendario.' : 'Todavía no hay calendarios guardados.'));
            for (const calendar of own) {
                const row = el('label', 'calendar-filter'); color(row, calendar.color);
                const check = el('input', 'calendar-check', '', `filter:${calendar.key}`);
                check.type = 'checkbox'; check.checked = !state.hidden.includes(calendar.key); check.disabled = state.saving || !preferencesLoaded;
                check.addEventListener('change', () => {
                    const hidden = new Set(state.hidden);
                    if (check.checked) hidden.delete(calendar.key); else hidden.add(calendar.key);
                    void savePreferences({ hidden_calendars: [...hidden] });
                });
                row.append(check, el('span', 'calendar-name', calendar.name || 'Sin nombre')); group.append(row);
            }
            accounts.append(group);
        }
        if (state.data?.available === true && state.data.complete === true && !state.data.accounts.length
            && !state.loading && !state.error) accounts.append(el('p', 'empty-small', 'Conecta una cuenta para ver tu agenda.'));
        const connections = button('connections', 'Gestionar conexiones', 'connections', () => context.openApp?.('conexiones'));
        accounts.append(connections); rail.append(accounts);
        const options = el('section', 'options');
        const zoneLabel = el('label', 'zone-label', 'Zona horaria');
        const zone = el('select', 'zone', '', 'zone'); zone.setAttribute('aria-label', 'Zona horaria del calendario');
        const zones = [...new Set([state.zone, defaultTimeZone(), 'UTC', 'America/Bogota', 'Europe/Madrid', 'America/New_York', 'America/Mexico_City'])];
        // Intl supplies the complete list where available, while the current value is always preserved.
        const available = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : [];
        for (const value of [...new Set([...zones, ...available])]) {
            const option = el('option', 'zone-option', value); option.value = value; option.selected = value === state.zone; zone.append(option);
        }
        zone.value = state.zone; zone.disabled = state.saving || !preferencesLoaded;
        zone.addEventListener('change', () => { if (validTimeZone(zone.value)) void savePreferences({ time_zone: zone.value }); });
        zoneLabel.append(zone);
        const reminder = button('reminders', state.reminders ? 'Recordatorios activados' : 'Activar recordatorios', 'reminders', remindersToggle,
            { disabled: state.saving || state.confirming || !preferencesLoaded });
        reminder.setAttribute('aria-pressed', String(state.reminders));
        options.append(zoneLabel, reminder, el('small', 'reminder-hint', 'Avisos visuales en la Bandeja. Nunca voz ni IA.'));
        rail.append(options);
        return rail;
    }

    function toolbar() {
        const header = el('header', 'toolbar');
        const line = el('div', 'toolbar-line');
        const navigation = el('div', 'navigation');
        navigation.append(button('prev', '‹', 'prev', () => chooseDay(moveDate(state.date, state.view, -1)), { label: 'Periodo anterior' }),
            button('next', '›', 'next', () => chooseDay(moveDate(state.date, state.view, 1)), { label: 'Periodo siguiente' }));
        line.append(navigation, el('h2', 'period', state.view === 'day' ? dateLabel(state.date) : shortMonth(state.date)),
            button('today', 'Hoy', 'today', () => chooseDay(today())));
        const views = el('div', 'views'); views.setAttribute('role', 'group'); views.setAttribute('aria-label', 'Vista de Calendario');
        for (const view of VIEWS) {
            const node = button('view', VIEW_LABELS[view], `view:${view}`, () => setView(view), { selected: view === state.view });
            node.setAttribute('aria-pressed', String(view === state.view)); views.append(node);
        }
        header.append(line, views);
        const tools = el('div', 'tools');
        const search = el('input', 'search', '', 'search'); search.type = 'search'; search.value = state.query;
        search.placeholder = 'Buscar en este periodo'; search.setAttribute('aria-label', 'Buscar eventos en el periodo visible');
        search.addEventListener('input', () => { state.query = search.value; render(); });
        const date = el('input', 'go-date', '', 'go-date'); date.type = 'date'; date.value = state.date;
        date.setAttribute('aria-label', 'Ir a una fecha'); date.addEventListener('change', () => chooseDay(date.value));
        const account = el('select', 'account-select', '', 'account-select'); account.setAttribute('aria-label', 'Filtrar por cuenta');
        const all = el('option', 'account-option', 'Todas las cuentas'); all.value = ''; account.append(all);
        for (const item of state.data?.accounts || []) {
            const option = el('option', 'account-option', item.label || item.email || 'Cuenta de Google'); option.value = item.id; account.append(option);
        }
        account.value = state.account;
        account.addEventListener('change', () => { state.account = account.value; render(); });
        const running = connected().some(account => (!state.account || account.id === state.account) && account.sync?.state === 'running');
        const refresh = button('sync', state.syncing ? 'Solicitando…' : running ? 'Actualizando desde Google…' : 'Actualizar desde Google', 'sync', sync,
            { disabled: state.syncing || running || !syncTargets().length });
        tools.append(search, date, account, refresh); header.append(tools);
        return header;
    }

    function emptyEventsMessage(scope = 'day') {
        const period = scope === 'agenda' ? 'los próximos 31 días' : scope === 'period' ? 'este periodo' : 'este día';
        if (!state.data) return state.loading ? `Leyendo los eventos de ${period}…`
            : `No se pudieron consultar los eventos de ${period}. Puedes reintentar.`;
        if (state.data.available !== true) return 'Los eventos del calendario no están disponibles. Revisa Conexiones.';
        if (state.error) return `La última copia guardada no muestra eventos en ${period}; no se pudo actualizar.`;
        if (state.loading) return `Actualizando ${period}… La copia guardada no muestra eventos con estos filtros.`;
        if (state.data.complete !== true) return state.query
            ? `Sin coincidencias entre los eventos mostrables en ${period}. La vista está incompleta.`
            : `No hay eventos mostrables en ${period}. La vista está incompleta.`;
        if (state.query) return `Nada coincide con tu búsqueda en ${period}.`;
        return `Sin eventos guardados en ${period}${state.hidden.length || state.account ? ' con estos filtros' : ''}.`;
    }

    function listView() {
        const body = el('div', 'list-view');
        if (!state.data) {
            body.append(el('p', 'empty', emptyEventsMessage(state.view === 'agenda' ? 'agenda' : 'period')));
            return body;
        }
        const range = viewRange(state.date, state.view);
        const days = [];
        for (let day = range.start; day < range.end; day = addDays(day, 1)) days.push(day);
        let count = 0;
        for (const day of days) {
            const events = dayEvents(day);
            if (!events.length && state.view === 'agenda') continue;
            const section = el('section', 'list-day'); section.dataset.today = String(day === today());
            section.append(button('list-date', dateLabel(day, { weekday: 'long' }), `list-date:${day}`, () => chooseDay(day)));
            section.append(...events.map((event) => eventButton(event)));
            if (!events.length) section.append(el('p', 'empty-small', emptyEventsMessage()));
            count += events.length; body.append(section);
        }
        if (!count && state.view === 'agenda') body.append(el('p', 'empty', emptyEventsMessage('agenda')));
        return body;
    }

    function detailPanel() {
        const panel = el('aside', 'detail');
        const head = el('div', 'detail-head');
        head.append(el('h2', 'chosen-date', dateLabel(state.date, { weekday: 'long' })));
        panel.append(head, el('h3', 'section-title', 'EN ESTE DÍA'));
        const events = dayEvents(state.date);
        const list = el('div', 'day-events'); list.append(...events.map((event) => eventButton(event)));
        if (!events.length) list.append(el('p', 'empty-small', emptyEventsMessage()));
        panel.append(list);
        const current = allEvents().find((event) => event.id === state.selected);
        if (current) {
            const card = el('article', 'event-detail'); color(card, state.data.calendars.find((calendar) => calendar.key === current.calendar_key)?.color);
            card.append(el('span', 'eyebrow', 'DETALLE DEL EVENTO'), el('h2', 'event-name', current.title || 'Sin título'));
            const dates = current.all_day
                ? `Todo el día · ${dateLabel(current.start_date)}${addDays(current.end_date, -1) !== current.start_date ? ` – ${dateLabel(addDays(current.end_date, -1))}` : ''}`
                : `${dateLabel(dayAt(current.start, state.zone))} · ${clock(current.start, state.zone)} – ${dayAt(current.start, state.zone) !== dayAt(current.end, state.zone) ? `${dateLabel(dayAt(current.end, state.zone))} · ` : ''}${clock(current.end, state.zone)}`;
            card.append(el('p', 'event-dates', dates), el('small', 'event-zone', state.zone));
            const calendar = state.data.calendars.find((item) => item.key === current.calendar_key);
            const account = state.data.accounts.find((item) => item.id === current.account_id);
            card.append(el('p', 'event-calendar', `${calendar?.name || 'Calendario'} · ${account?.label || account?.email || 'Google'}`));
            if (current.location) card.append(el('p', 'event-location', current.location));
            const join = safeMeetingUrl(current.join_url);
            if (join) {
                const link = el('a', 'join', 'Unirse a la reunión'); link.href = join; link.target = '_blank'; link.rel = 'noopener noreferrer';
                card.append(link);
            }
            if (current.description) card.append(el('p', 'event-description', current.description));
            if (current.recurring) card.append(el('small', 'recurring', 'Forma parte de un evento recurrente.'));
            if (current.declined) card.append(el('p', 'detail-declined', 'Rechazaste esta invitación: Iris no te avisará de este evento.'));
            panel.append(card);
        } else if (events.length) panel.append(el('p', 'detail-hint', 'Selecciona un evento para ver sus detalles.'));
        panel.append(preparation.render(current || null));
        return panel;
    }

    function findKey(key) {
        const walk = (node) => {
            if (node?.dataset?.key === key) return node;
            for (const child of node?.children || []) { const found = walk(child); if (found) return found; }
            return null;
        };
        return walk(root);
    }

    function render() {
        if (!root || disposed) return;
        renderedEvents = filterEvents(state.data?.events || [], { hidden: state.hidden, query: state.query, account: state.account });
        const days = monthDays(state.date), range = viewRange(state.date, state.view);
        renderedDays = eventsByDay(renderedEvents, { start: days[0] < range.start ? days[0] : range.start,
            end: addDays(days.at(-1), 1) > range.end ? addDays(days.at(-1), 1) : range.end, timeZone: state.zone });
        const focus = root.contains?.(document.activeElement) ? document.activeElement : null;
        const key = focus?.dataset?.key;
        const selection = focus && typeof focus.selectionStart === 'number' ? [focus.selectionStart, focus.selectionEnd] : null;
        const oldScroll = [...(root.querySelectorAll?.('[data-scroll]') || [])].map((node) => [node.dataset.scroll, node.scrollTop]);
        const host = el('div', 'host');
        const app = el('section'); app.dataset.view = state.view;
        const center = el('main', 'center'); center.append(toolbar());
        const status = el('div', 'status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
        const message = state.error || state.notice || (state.loading ? (state.data ? 'Actualizando la vista guardada…' : 'Leyendo el calendario guardado…')
            : state.data?.complete === false ? 'Vista parcial. Hay eventos que no se pueden mostrar; revisa los avisos.' : 'Datos guardados · solo lectura');
        status.append(el('p', 'status-text', message));
        if (state.error) status.append(button('retry', 'Reintentar', 'retry', () => consumer.read(activeRange())));
        if (state.data) {
            for (const warning of state.data.warnings || []) {
                const text = typeof warning === 'string' ? warning : warning.message;
                if (text) status.append(el('p', 'warning', text));
            }
            for (const account of state.data.accounts) {
                const sync = account.sync?.['calendar.read'] || account.sync;
                const at = sync?.last_success_at;
                if (at && Number.isFinite(Date.parse(at))) status.append(el('small', 'last-sync',
                    `${account.label || account.email || 'Google'} · Última lectura ${new Date(at).toLocaleString('es', { timeZone: state.zone })}`));
            }
        }
        center.append(status);
        if (!state.data && state.error) center.append(el('p', 'empty', 'No se pudo consultar tu calendario. No se ha cambiado ninguna cuenta.'));
        else {
            const content = el('div', 'content'); content.dataset.scroll = 'content';
            content.append(state.view === 'month' ? calendarMonth() : listView()); center.append(content);
            if (state.data && !state.data.events.length && !state.loading) center.append(el('p', 'empty-caption',
                state.data.available === true && state.data.complete === true && !state.error && !connected().length
                    ? 'Conecta Calendario en Conexiones para empezar.' : emptyEventsMessage('period')));
        }
        const rail = sidebar(), detail = detailPanel();
        rail.dataset.scroll = 'rail'; detail.dataset.scroll = 'detail';
        app.append(rail, center, detail); host.append(app); root.replaceChildren(host);
        for (const [name, top] of oldScroll) {
            const node = root.querySelector?.(`[data-scroll="${name}"]`); if (node) node.scrollTop = top;
        }
        if (key) {
            const replacement = findKey(key); replacement?.focus?.();
            if (selection && replacement?.setSelectionRange) replacement.setSelectionRange(...selection);
        }
    }

    function dispose() {
        if (disposed) return;
        disposed = true; preferenceEpoch += 1;
        preparation.dispose();
        for (const controller of controllers) controller.abort(); controllers.clear();
        consumer.release(); if (ownSource) calendarSource.dispose();
        root?.replaceChildren(); root = null;
    }
    context.signal?.addEventListener?.('abort', dispose, { once: true });
    return {
        mount(target) { root = target; render(); },
        refresh({ force = false, opening = false } = {}) {
            if (!preferencesLoaded) return start();
            if (force || opening || !state.data) return consumer.read(activeRange());
            return undefined;
        },
        setVisibility({ visible: shown }) { visible = Boolean(shown); consumer.setVisible(visible); preparation.setVisibility(visible); },
        navigate(payload = {}) {
            if (!payload || typeof payload !== 'object') return;
            if (typeof payload.account_id === 'string' && (!payload.account_id || /^gacc_[0-9a-f]{32}$/.test(payload.account_id))) state.account = payload.account_id;
            if (payload.event_id) state.query = '';
            if (validDate(payload.date)) chooseDay(payload.date, { eventId: typeof payload.event_id === 'string' ? payload.event_id : '' });
            else if (typeof payload.event_id === 'string') { state.selected = payload.event_id; render(); }
        },
        dispose,
    };
}
