// Generated from apps/calendar/ui/widget.js by scripts/build_app_catalog.mjs: edit the original.
/** F12b: a compact consumer of F11's cache. No OAuth, sync, reminders, TTS or model calls. */
import { defaultTimeZone, dayAt, addDays, viewRange, dateLabel,
    filterEvents, getCalendarSource, createCalendarSource } from './state.js?v=2';

export function createWidget({ root, config, openApp = () => {}, configure = () => false },
    { source = null, fetchImpl = null, now = () => Date.now(), documentLike = globalThis.document } = {}) {
    const ownSource = !source && Boolean(fetchImpl);
    const calendarSource = source || (ownSource ? createCalendarSource({ fetchImpl }) : getCalendarSource());
    const state = { date: dayAt(now()), zone: defaultTimeZone(), data: null, loading: true, error: '',
        hidden: [], prefsRevision: -1, notice: '', saving: false, filters: [...config.calendars] };
    let visible = false, disposed = false, starting = null, started = false, lastRender = '', filtersOpen = false;
    const consumer = calendarSource.acquire((record) => {
        if (disposed) return;
        const oldRange = JSON.stringify(range());
        state.data = record.data; state.loading = record.loading; state.error = record.error;
        acceptPreferences(record.data?.preferences);
        state.date = today();
        if (visible && oldRange !== JSON.stringify(range())) {
            state.data = null; state.loading = true;
            queueMicrotask(() => { if (!disposed && visible) void consumer.read(range()); });
        }
        render();
    });
    function acceptPreferences(prefs) {
        if (!prefs || !Number.isInteger(prefs.revision) || prefs.revision < state.prefsRevision) return;
        state.prefsRevision = prefs.revision;
        if (prefs.revision > 0) state.zone = prefs.time_zone;
        state.hidden = prefs.hidden_calendars || [];
    }
    const range = () => ({ ...viewRange(state.date), time_zone: state.zone });
    const today = () => dayAt(now(), state.zone);
    const el = (tag, part, text = '', key = '') => {
        const node = documentLike.createElement(tag); node.className = `iris-calendar-widget-${part}`;
        node.dataset.widgetPart = part; if (key) node.dataset.calendarKey = key;
        if (text) node.textContent = text; return node;
    };
    const button = (text, key, action, label = text) => {
        const node = el('button', 'button', text, key); node.type = 'button'; node.setAttribute('aria-label', label);
        node.addEventListener('click', () => void action()); return node;
    };
    function selectedAccount() {
        if (state.filters.length !== 1) return '';
        return state.data?.calendars.find((item) => item.key === state.filters[0])?.account_id || '';
    }
    function open(event = null) {
        openApp('calendar', { date: event ? (event.all_day ? event.start_date : dayAt(event.start, state.zone)) : state.date,
            event_id: event?.id || '', account_id: event?.account_id || selectedAccount() });
    }
    async function start() {
        if (starting || disposed) return starting;
        starting = (async () => {
            try { acceptPreferences(await calendarSource.loadPreferences({ force: started })); }
            catch (error) { if (!disposed) state.notice = 'No se pudieron leer tus ajustes de Calendario. Reintenta para recuperarlos.'; }
            if (!disposed && visible) { const force = started; if (!started) state.date = today(); started = true; await consumer.read(range(), { force }); }
        })().finally(() => { starting = null; });
        return starting;
    }
    function statusMessage() {
        if (!state.data) return state.error || 'Leyendo tu calendario…';
        if (state.data.available !== true) return 'Calendario no disponible. Revisa Conexiones.';
        if (state.error) return 'No se pudo actualizar la vista. Se conserva la última copia guardada.';
        if (state.data.accounts.some((account) => account.auth_state === 'reauth_required')) return 'Una cuenta necesita volver a conectarse. Los datos pueden estar desactualizados.';
        if (state.data.complete !== true) return 'Vista incompleta. Revisa los detalles en Calendario.';
        return state.loading ? 'Actualizando la vista guardada…' : 'Datos guardados · Solo lectura';
    }
    function render() {
        if (disposed || !visible) return;
        const signature = JSON.stringify(state);
        if (signature === lastRender) return; lastRender = signature;
        const active = documentLike.activeElement;
        const focused = root.contains?.(active) ? active?.dataset?.calendarKey : '';
        const scroll = root.scrollTop || 0;
        const app = el('div', 'body'); app.className += ' iris-calendar-widget';
        const header = el('header', 'header');
        const title = el('div', 'identity'); title.append(el('span', 'eyebrow', 'TU AGENDA'), el('h2', 'title', 'Calendario'));
        header.append(title, button('Abrir Calendario', 'open', () => open())); app.append(header);
        const todayCard = el('div', 'today');
        todayCard.append(el('strong', 'date-number', String(Number(today().slice(-2)))),
            el('span', 'date-label', dateLabel(today(), { weekday: 'long', month: 'long' })));
        app.append(todayCard);
        const allEvents = filterEvents(state.data?.events || [], { hidden: state.hidden });
        const events = state.filters.length ? allEvents.filter((event) => state.filters.includes(event.calendar_key)) : allEvents;
        const status = el('p', 'status', statusMessage()); status.setAttribute('role', state.error ? 'alert' : 'status'); app.append(status);
        const accounts = state.data?.accounts || [];
        const connected = accounts.filter((account) => ['connected', 'partial'].includes(account.auth_state) && account.granted?.includes('calendar.read'));
        if (state.data && !connected.length) app.append(button('Conectar Calendario', 'connect', () => openApp('conexiones')));
        const details = el('details', 'filters'); details.open = filtersOpen;
        details.append(el('summary', 'filter-summary', 'Elegir calendarios'));
        details.addEventListener('toggle', () => { filtersOpen = details.open; });
        const filters = el('label', 'filter', 'Mostrar'); const select = el('select', 'select', '', 'filter'); select.setAttribute('aria-label', 'Calendarios del widget');
        const option = (value, text) => { const node = el('option', 'option', text); node.value = value; select.append(node); };
        option('', 'Todos los calendarios visibles');
        for (const calendar of state.data?.calendars || []) option(calendar.key, calendar.name || 'Calendario sin nombre');
        const unknown = state.filters.some((key) => !state.data?.calendars.some((calendar) => calendar.key === key));
        if (state.filters.length > 1 || unknown) option('__saved__', unknown ? 'Selección guardada no disponible' : 'Varios calendarios elegidos');
        select.value = state.filters.length > 1 || unknown ? '__saved__' : state.filters[0] || '';
        select.disabled = state.saving || !state.data;
        select.addEventListener('change', async () => {
            if (select.value === '__saved__' || state.saving) return;
            const next = select.value ? [select.value] : []; state.saving = true; state.notice = ''; render();
            try { if (await configure(next) === false) state.notice = 'No se guardó el filtro. Revisa Personalizar escritorio.'; }
            catch { state.notice = 'No se pudo guardar el filtro.'; }
            finally { if (!disposed) { state.saving = false; render(); } }
        }); filters.append(select); details.append(filters);
        app.append(el('h3', 'day-title', 'Próximos eventos'));
        // Keep the same cache range as the monthly app. The period is explicit, not an
        // assertion about events outside the loaded range. Ongoing events remain visible.
        // «Próximos» are meetings the user attends: a declined invitation never takes one of the three places.
        const ordered = events.filter((event) => !event.declined && (event.all_day ? event.end_date > today() : Date.parse(event.end) > now()))
            .map((event) => ({ event, day: event.all_day ? (event.start_date < today() ? today() : event.start_date) : dayAt(event.start, state.zone) }))
            .sort((a, b) => a.day.localeCompare(b.day) || Number(b.event.all_day) - Number(a.event.all_day)
                || Date.parse(a.event.start || 0) - Date.parse(b.event.start || 0) || a.event.id.localeCompare(b.event.id))
            .map(({ event }) => event);
        const upcoming = ordered.slice(0, 3);
        for (const event of upcoming) {
            const when = event.all_day ? (event.start_date <= today() ? 'Hoy · Todo el día' : `${dateLabel(event.start_date)} · Todo el día`) : new Intl.DateTimeFormat('es', { timeZone: state.zone, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(event.start));
            const item = button('', `event:${event.id}`, () => open(event), `${event.title || 'Sin título'}. ${when}`); item.className += ' iris-calendar-widget-event';
            item.append(el('span', 'event-time', when), el('strong', 'event-title', event.title || 'Sin título'),
                el('small', 'event-account', accounts.find((account) => account.id === event.account_id)?.label || 'Google Calendar')); app.append(item);
        }
        if (!upcoming.length) app.append(el('p', 'empty', state.error || !state.data || state.data.complete !== true || state.data.available !== true
            ? 'No se puede confirmar si hay eventos en esta vista.' : unknown ? 'Los calendarios elegidos ya no están disponibles.' : 'Sin eventos guardados con esta selección.'));
        const count = !state.data ? 'Lectura pendiente' : `${ordered.length > 3 ? '3 de ' + ordered.length : ordered.length} eventos en ${state.data.complete === true && state.data.available === true ? 'la copia guardada' : 'esta vista incompleta'}`;
        app.append(el('small', 'period', `Hasta ${dateLabel(addDays(range().end, -1))} · ${count}`), details);
        const dates = connected.map((account) => (account.sync?.['calendar.read'] || account.sync)?.last_success_at).filter((value) => Number.isFinite(Date.parse(value)));
        if (dates.length) app.append(el('small', 'last-sync', `Última lectura: ${new Date(dates.sort()[0]).toLocaleString('es', { timeZone: state.zone })}`));
        app.append(el('small', 'zone', state.zone));
        if (state.notice) { app.append(el('p', 'status', state.notice), button('Reintentar', 'retry', () => { state.notice = ''; return start(); })); }
        else if (state.error) app.append(button('Volver a leer', 'retry', () => consumer.read(range())));
        if (accounts.some((account) => account.auth_state === 'reauth_required')) app.append(button('Revisar Conexiones', 'reconnect', () => openApp('conexiones')));
        root.replaceChildren(app); root.scrollTop = scroll;
        if (focused) root.querySelector?.(`[data-calendar-key="${focused}"]`)?.focus();
    }
    return {
        configure(value) { if (!value || disposed) return; state.filters = [...value.calendars]; render(); },
        setVisibility(value) {
            if (disposed) return;
            const next = Boolean(value), changed = next !== visible; visible = next;
            consumer.setVisible(visible);
            if (changed && visible) { lastRender = ''; render(); void start(); }
        },
        dispose() { disposed = true; consumer.release(); if (ownSource) calendarSource.dispose(); root.replaceChildren(); },
    };
}
