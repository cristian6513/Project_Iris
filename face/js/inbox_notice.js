/**
 * F07: the visual notice on return. Core sends it to this surface after a new
 * interaction here; it groups the results the user has not seen and the work
 * still running. It is silent: it never speaks, never opens or writes in the
 * chat, never takes the focus and never marks anything read.
 *
 * Each notice gets exactly one receipt. Shown: it was really on screen.
 * Deferred (hidden page, chat being written in, Iris speaking or listening):
 * Core releases it and the results wait for a better moment. Closing it or
 * opening the inbox from it changes nothing else.
 */

import { OUTCOME_LABELS, noticeDeferral, noticeHeadline, normalizeNotice } from './inbox_state.js?v=1';

const VISIBLE_MS = 30000;
const MAX_ROWS = 3;

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

export class InboxNotice {
    constructor({ send = () => false, onOpenInbox = null, openLabel = 'Abrir bandeja', host = null,
        setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = (id) => clearTimeout(id) } = {}) {
        this.send = send;
        this.onOpenInbox = onOpenInbox;
        this.openLabel = openLabel;
        this.host = host;
        this._setTimer = setTimer;
        this._clearTimer = clearTimer;
        this._answered = new Set(); // notice ids that already have their receipt
        this._timer = null;
        this.current = null;
    }

    _host() {
        if (!this.host) {
            this.host = el('div', 'inbox-notice-host');
            globalThis.document?.body?.append?.(this.host);
        }
        return this.host;
    }

    _receipt(notice, shown, reason = '') {
        if (this._answered.has(notice.id)) return;
        this._answered.add(notice.id);
        if (this._answered.size > 200) this._answered.delete(this._answered.values().next().value);
        this.send({ id: notice.id, token: notice.token, generation: notice.generation, shown,
            ...(reason ? { reason } : {}) });
    }

    /** A notice from Core; ``context`` says whether this surface can show it now. */
    present(payload, { surfaceId = '', documentLike = globalThis.document, status = '', quiet = false,
        chatBusy = false } = {}) {
        const notice = normalizeNotice(payload, surfaceId);
        if (!notice || this._answered.has(notice.id)) return 'ignored';
        const deferral = noticeDeferral({ documentLike, status, quiet, chatBusy });
        if (deferral) {
            this._receipt(notice, false, deferral);
            return 'deferred';
        }
        this._render(notice);
        this._receipt(notice, true);
        return 'shown';
    }

    _render(notice) {
        this.close();
        this.current = notice;
        const card = el('section', 'inbox-notice');
        card.setAttribute('role', 'status');
        card.setAttribute('aria-live', 'polite');
        card.setAttribute('aria-label', 'Aviso al volver');
        card.dataset.noticeId = notice.id;
        card.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') this.close();
        });
        const head = el('div', 'inbox-notice-head');
        head.append(el('span', 'inbox-notice-kicker', 'AL VOLVER'));
        const close = el('button', 'inbox-notice-close', '×');
        close.type = 'button';
        close.setAttribute('aria-label', 'Cerrar el aviso (no marca nada como leído)');
        close.addEventListener('click', () => this.close());
        head.append(close);
        card.append(head, el('strong', 'inbox-notice-title', noticeHeadline(notice)));
        const list = el('ul', 'inbox-notice-list');
        for (const row of notice.results.slice(0, MAX_ROWS)) {
            const item = el('li', `inbox-notice-row tone-${OUTCOME_LABELS[row.outcome].tone}`);
            item.append(el('span', 'inbox-notice-row-title', row.title),
                el('span', 'inbox-notice-row-state', OUTCOME_LABELS[row.outcome].label));
            if (row.excerpt) item.append(el('p', 'inbox-notice-excerpt', row.excerpt));
            list.append(item);
        }
        for (const row of notice.running.slice(0, Math.max(0, MAX_ROWS - notice.results.length))) {
            const item = el('li', 'inbox-notice-row tone-running');
            item.append(el('span', 'inbox-notice-row-title', row.title),
                el('span', 'inbox-notice-row-state', row.state === 'unknown' ? 'Sin confirmar' : 'En curso'));
            list.append(item);
        }
        const hidden = notice.results.length + notice.running.length - list.children.length;
        card.append(list);
        if (hidden > 0) card.append(el('p', 'inbox-notice-more', `y ${hidden} más en la bandeja.`));
        if (this.onOpenInbox) {
            const open = el('button', 'inbox-notice-open', this.openLabel);
            open.type = 'button';
            open.addEventListener('click', () => { this.close(); this.onOpenInbox(); });
            card.append(open);
        }
        this._host().replaceChildren(card);
        this._timer = this._setTimer(() => { this._timer = null; this.close(); }, VISIBLE_MS);
    }

    close() {
        this._clearTimer(this._timer);
        this._timer = null;
        this.current = null;
        this.host?.replaceChildren?.();
    }
}
