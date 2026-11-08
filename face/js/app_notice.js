/** App notices (core/app_notices.py): what apps ask for, and the card shown when one falls due.
 *
 * An app's window or widget calls `context.notify({ title, text, at, key })`. Core stores and
 * schedules it, so it is shown even if the app was closed; the shell names the app, never the
 * notice. The websocket then carries an id and a token only: the card reads the text from this
 * PC's route and answers with a receipt. Closing the card does not mark it read in the Bandeja.
 */
import { noticeDeferral } from './inbox_state.js?v=6';

const ID = /^[0-9a-f]{32}$/;
const APP_ID = /^[a-z][a-z0-9-]{1,31}$/;
const KEY = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/;

const element = (tag, className, text) => {
    const node = document.createElement(tag);
    node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
};

/** The body Core receives; `at` is a Date or milliseconds (as Date.now()), Core counts seconds. */
export function appNoticeBody(appId, { title, text = '', at = 0, key = '' } = {}) {
    if (!APP_ID.test(appId || '')) throw new TypeError('Esta app no puede enviar avisos.');
    if (typeof title !== 'string' || !title.trim() || title.length > 120) throw new TypeError('title: de 1 a 120 caracteres.');
    if (typeof text !== 'string' || text.length > 1000) throw new TypeError('text: hasta 1000 caracteres.');
    if (key !== '' && (typeof key !== 'string' || !KEY.test(key))) throw new TypeError('key: letras, números y _ . : -, hasta 64.');
    const ms = at instanceof Date ? at.getTime() : at;
    if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) throw new TypeError('at: una fecha (Date) o milisegundos.');
    return { app: appId, title, text, at: ms ? ms / 1000 : 0, key };
}

async function answer(response) {
    const value = await response.json().catch(() => ({}));
    if (!response.ok) {
        const error = new Error(value?.detail?.message || 'Iris no guardó el aviso.');
        error.code = value?.detail?.code || 'unavailable';
        throw error;
    }
    return value;
}

/** What the shell gives each app: bound to the app and the installation that own the context, so a
 * view left over from an uninstalled or updated version cannot schedule anything (Codex SDK03). */
export function appNoticesFor(appId, installation, fetchImpl = (...args) => fetch(...args)) {
    const json = { 'Content-Type': 'application/json' };
    return {
        notify: async (options) => {
            if (!/^ins_[0-9a-f]{32}$/.test(installation || '')) throw new TypeError('Esta app no tiene una instalación válida.');
            const body = { ...appNoticeBody(appId, options), installation };
            const value = await answer(await fetchImpl('/api/app-notices', { method: 'POST', headers: json, body: JSON.stringify(body) }));
            return { status: value.status, id: value.notice?.id, at: Math.round((value.notice?.due_at || 0) * 1000),
                shown: value.mode === 'show' };
        },
        cancelNotice: async (key) => {
            if (!KEY.test(key || '')) throw new TypeError('key: letras, números y _ . : -, hasta 64.');
            return (await answer(await fetchImpl(`/api/app-notices/${appId}/${encodeURIComponent(key)}`, { method: 'DELETE' }))).cancelled === true;
        },
        scheduledNotices: async () => {
            const value = await answer(await fetchImpl(`/api/app-notices/scheduled/${appId}`, { cache: 'no-store' }));
            return (value.notices || []).map((notice) => ({ id: notice.id, key: notice.key, title: notice.title,
                at: Math.round(notice.due_at * 1000) }));
        },
    };
}

/** The person's choice per installed app (show, inbox, off), for Administrar aplicaciones. */
export function appNoticeModes(fetchImpl = (...args) => fetch(...args)) {
    return {
        load: async () => (await answer(await fetchImpl('/api/app-notices/modes', { cache: 'no-store' }))).modes || {},
        set: async (appId, mode) => {
            await answer(await fetchImpl(`/api/app-notices/modes/${appId}`, { method: 'PUT',
                headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }) }));
            return mode;
        },
    };
}

export class AppNotice {
    constructor({ send = () => false, onOpenApp = null, fetchImpl = (...args) => fetch(...args), host = null,
        setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = (id) => clearTimeout(id) } = {}) {
        this.send = send; this.onOpenApp = onOpenApp; this.fetch = fetchImpl; this.host = host;
        this.setTimer = setTimer; this.clearTimer = clearTimer;
        // Drawn cards are never drawn again; one in progress is not taken twice. A deferred or
        // unreadable offer is not remembered: Core offers it again with a new token (Codex SDK01).
        this.shown = new Set(); this.inFlight = new Set(); this.timer = null; this.current = null; this.epoch = 0;
    }

    async present(payload, options = {}) {
        const id = payload?.notice_id;
        if (!ID.test(id || '') || !ID.test(payload?.token || '') || payload.surface_id !== (options.surfaceId ?? '')
            || this.shown.has(id) || this.inFlight.has(id)) return 'ignored';
        this.inFlight.add(id);
        try {
            const outcome = await this._present(payload, options);
            if (outcome === 'shown') {
                this.shown.add(id);
                if (this.shown.size > 200) this.shown.delete(this.shown.values().next().value);
            }
            return outcome;
        } finally {
            this.inFlight.delete(id);
        }
    }

    async _present(payload, { surfaceId = '', documentLike = globalThis.document, busy = false } = {}) {
        const receipt = (shown) => this.send({ notice_id: payload.notice_id, token: payload.token,
            surface_id: surfaceId, shown });
        const isBusy = () => Boolean(typeof busy === 'function' ? busy() : busy)
            || Boolean(noticeDeferral({ documentLike }));
        try {
            if (isBusy() || documentLike?.visibilityState !== 'visible') { receipt(false); return 'deferred'; }
            const epoch = ++this.epoch;
            const response = await this.fetch(`/api/app-notices/${payload.notice_id}`, { cache: 'no-store' });
            if (!response.ok) { receipt(false); return 'unavailable'; }
            const value = await response.json();
            if (epoch !== this.epoch || isBusy() || documentLike?.visibilityState !== 'visible') {
                receipt(false); return 'deferred';
            }
            if (value?.id !== payload.notice_id || !APP_ID.test(value?.app_id || '')) { receipt(false); return 'invalid'; }
            this._render(value);
            receipt(true);
            return 'shown';
        } catch { receipt(false); return 'unavailable'; }
    }

    _render(value) {
        this.close(false);
        this.current = value.id;
        if (!this.host) {
            this.host = element('div', 'inbox-notice-host app-notice-host');
            document.body.append(this.host);
        }
        const name = String(value.app_name || value.app_id).slice(0, 80);
        const card = element('section', 'inbox-notice app-notice');
        card.dataset.app = value.app_id;
        card.setAttribute('role', 'status'); card.setAttribute('aria-live', 'polite');
        card.setAttribute('aria-label', `Aviso de ${name}`);
        card.addEventListener('keydown', (event) => { if (event.key === 'Escape') this.close(); });
        const head = element('div', 'inbox-notice-head');
        head.append(element('span', 'inbox-notice-kicker', name.toLocaleUpperCase('es')));
        const close = element('button', 'inbox-notice-close', '×');
        close.type = 'button'; close.setAttribute('aria-label', 'Cerrar el aviso');
        close.addEventListener('click', () => this.close()); head.append(close);
        card.append(head, element('strong', 'inbox-notice-title', String(value.title || '').slice(0, 120)));
        if (value.text) card.append(element('p', 'inbox-notice-excerpt', String(value.text).slice(0, 1000)));
        if (this.onOpenApp) {
            const actions = element('div', 'inbox-actions');
            const open = element('button', 'inbox-notice-open', `Abrir ${name}`); open.type = 'button';
            open.addEventListener('click', () => { this.close(); this.onOpenApp(value.app_id); });
            actions.append(open); card.append(actions);
        }
        this.host.replaceChildren(card);
        this.timer = this.setTimer(() => this.close(), 30000);
    }

    close(invalidate = true) {
        if (invalidate) this.epoch++;
        this.clearTimer(this.timer); this.timer = null; this.current = null;
        this.host?.replaceChildren();
    }
}
