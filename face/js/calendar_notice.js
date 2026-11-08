/** F11: silent calendar reminders. The websocket carries an opaque intent only;
 * event text is fetched from this PC's guarded route. Only a rendered card gets
 * a shown receipt. Dismissal/opening does not mark its inbox item read.
 */
import { calendarOpenPayload, noticeDeferral, safeCalendarJoinUrl } from './inbox_state.js?v=6';

const ID = /^[0-9a-f]{32}$/;

const element = (tag, className, text) => {
    const node = document.createElement(tag);
    node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
};

export class CalendarNotice {
    constructor({ send = () => false, onOpenCalendar = null, fetchImpl = (...args) => fetch(...args),
        host = null, now = () => Date.now() / 1000,
        // Wrapped: stored bare and called as this.setTimer(...), the browser's timers throw
        // «Illegal invocation», and every reminder failed before rendering (review 2026-10-19).
        setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = (id) => clearTimeout(id) } = {}) {
        this.send = send; this.onOpenCalendar = onOpenCalendar; this.fetch = fetchImpl;
        this.host = host; this.setTimer = setTimer; this.clearTimer = clearTimer; this.now = now;
        this.seen = new Set(); this.timer = null; this.current = null; this.epoch = 0;
    }

    async present(payload, { surfaceId = '', documentLike = globalThis.document, busy = false } = {}) {
        if (!ID.test(payload?.notice_id || '') || !ID.test(payload?.token || '')
            || payload.surface_id !== surfaceId || this.seen.has(payload.notice_id)) return 'ignored';
        this.seen.add(payload.notice_id);
        if (this.seen.size > 200) this.seen.delete(this.seen.values().next().value);
        const receipt = (shown) => this.send({ notice_id: payload.notice_id, token: payload.token,
            surface_id: surfaceId, shown });
        const isBusy = () => Boolean(typeof busy === 'function' ? busy() : busy)
            || Boolean(noticeDeferral({ documentLike }));
        try {
            if (isBusy() || documentLike?.visibilityState !== 'visible') { receipt(false); return 'deferred'; }
            const epoch = ++this.epoch;
            const response = await this.fetch(`/api/calendar/notices/${payload.notice_id}`, { cache: 'no-store' });
            if (!response.ok) { receipt(false); return 'unavailable'; }
            const value = await response.json();
            if (epoch !== this.epoch || isBusy() || documentLike?.visibilityState !== 'visible') {
                receipt(false); return 'deferred';
            }
            const destination = calendarOpenPayload({ origin: 'calendar',
                calendarEventId: value?.event_id, calendarDate: value?.date });
            if (value?.id !== payload.notice_id || !destination
                || !Number.isFinite(value.expires_at) || value.expires_at <= this.now()) {
                receipt(false); return 'invalid';
            }
            this._render(value, destination);
            receipt(true);
            return 'shown';
        } catch { receipt(false); return 'unavailable'; }
    }

    _render(value, destination) {
        this.close(false);
        this.current = value.id;
        if (!this.host) {
            this.host = element('div', 'inbox-notice-host calendar-notice-host');
            document.body.append(this.host);
        }
        const card = element('section', 'inbox-notice calendar-notice');
        card.setAttribute('role', 'status'); card.setAttribute('aria-live', 'polite');
        card.setAttribute('aria-label', 'Recordatorio de Calendario');
        card.addEventListener('keydown', (event) => { if (event.key === 'Escape') this.close(); });
        const head = element('div', 'inbox-notice-head');
        head.append(element('span', 'inbox-notice-kicker', 'CALENDARIO'));
        const close = element('button', 'inbox-notice-close', '×');
        close.type = 'button'; close.setAttribute('aria-label', 'Cerrar el recordatorio');
        close.addEventListener('click', () => this.close()); head.append(close);
        card.append(head, element('strong', 'inbox-notice-title', String(value.title || 'Evento').slice(0, 120)),
            element('p', 'inbox-notice-excerpt', String(value.text || '').slice(0, 2000)));
        const actions = element('div', 'inbox-actions');
        if (this.onOpenCalendar) {
            const open = element('button', 'inbox-notice-open', 'Ver Calendario'); open.type = 'button';
            open.addEventListener('click', () => { this.close(); this.onOpenCalendar(destination); });
            actions.append(open);
        }
        const join = safeCalendarJoinUrl(value.join_url);
        if (join) {
            const link = element('a', 'inbox-notice-open', 'Unirse');
            link.href = join; link.target = '_blank'; link.rel = 'noopener noreferrer'; actions.append(link);
        }
        card.append(actions); this.host.replaceChildren(card);
        this.timer = this.setTimer(() => this.close(), Math.min(30000, Math.max(1, (value.expires_at - this.now()) * 1000)));
    }

    close(invalidate = true) {
        if (invalidate) this.epoch++;
        this.clearTimer(this.timer); this.timer = null; this.current = null;
        this.host?.replaceChildren();
    }
}
