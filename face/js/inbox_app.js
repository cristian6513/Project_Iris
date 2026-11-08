/**
 * F07 "Bandeja": the general inbox, over Core's `/api/inbox`.
 *
 * Automation results can be read, marked unread, snoozed, dismissed and
 * restored here, each action naming the revision it saw; Vision notices are
 * listed with their state and keep their own actions in the Vision view.
 * Opening the inbox marks nothing read. It refreshes when Core says something
 * changed, and only while it is visible (otherwise on the next showing); a
 * source that could not be read is said, never shown as empty. Every text goes
 * through textContent.
 */

import {
    OUTCOME_LABELS,
    ORIGIN_LABELS,
    QUIET_MINUTES,
    SNOOZE_MINUTES,
    VIEWS,
    actionBody,
    actionLabel,
    calendarOpenPayload,
    mailOpenPayload,
    filterItems,
    inboxErrorText,
    isUnread,
    nextChangeAt,
    normalizeInbox,
    safeCalendarJoinUrl,
    sourceProblems,
    statusText,
} from './inbox_state.js?v=6';

const LONG_TEXT = 420;

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function when(seconds) {
    const date = new Date(seconds * 1000);
    return Number.isFinite(date.getTime()) && seconds > 0
        ? date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
        : 'Fecha no disponible';
}

function focusedKey(controls) {
    const active = globalThis.document?.activeElement;
    for (const [key, node] of controls) if (node === active) return key;
    return '';
}

export class InboxApp {
    constructor({ root = null, fetchImpl = globalThis.fetch?.bind(globalThis), isVisible = () => false,
        onSummary = () => {}, onOpenVision = null, onOpenAutomations = null, onOpenCalendar = null, onOpenMail = null,
        onOpenApp = null,
        setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = (id) => clearTimeout(id) } = {}) {
        this.root = root;
        this.fetch = fetchImpl;
        this.isVisible = isVisible;
        this.onSummary = onSummary;
        this.onOpenVision = onOpenVision;
        this.onOpenAutomations = onOpenAutomations;
        this.onOpenCalendar = onOpenCalendar;
        this.onOpenMail = onOpenMail;
        this.onOpenApp = onOpenApp;
        this._setTimer = setTimer;
        this._clearTimer = clearTimer;
        this.state = null;
        this.loading = false;
        this.busy = false; // an action is being saved
        this.error = '';
        this.actionError = '';
        this.notice = '';
        this.view = 'unread';
        this.origin = 'all';
        this.dirty = true;
        this._generation = 0;
        this._reloadPending = false;
        this._timer = null;
        this._controls = new Map();
        this._snooze = new Map(); // chosen delay per item, kept across refreshes
        if (!root) return;
        this._build();
        this.render();
    }

    // ── Structure ──────────────────────────────────────────────────────

    _build() {
        this.surface = el('section', 'inbox-app');
        const hero = el('header', 'inbox-hero');
        const intro = el('div', 'inbox-intro');
        intro.append(el('span', 'inbox-kicker', 'IRIS OS / BANDEJA'), el('h2', '', 'Bandeja'),
            el('p', '', 'Resultados de automatizaciones y avisos de Vision y Calendario, para cuando tú decidas.'));
        this.counter = el('div', 'inbox-counter');
        this.counterValue = el('strong', '', '—');
        this.counter.append(this.counterValue, el('span', '', 'SIN LEER'));
        hero.append(intro, this.counter);

        this.status = el('p', 'inbox-status');
        this.status.setAttribute('role', 'status');
        this.status.setAttribute('aria-live', 'polite');
        this.problems = el('div', 'inbox-problems');
        this.problems.setAttribute('role', 'alert');

        const toolbar = el('div', 'inbox-toolbar');
        this.viewButtons = {};
        const views = el('div', 'inbox-views');
        views.setAttribute('role', 'group');
        views.setAttribute('aria-label', 'Filtrar la bandeja');
        for (const view of VIEWS) {
            const button = this._button('', () => { this.view = view; this.render(); });
            button.dataset.view = view;
            this.viewButtons[view] = button;
            views.append(button);
        }
        const originLabel = el('label', 'inbox-origin');
        originLabel.append(el('span', '', 'Origen'));
        this.originSelect = el('select');
        this.originSelect.setAttribute('aria-label', 'Filtrar por origen');
        for (const [value, label] of [['all', 'Todo'], ['automation', 'Automatizaciones'], ['vision', 'Vision'],
            ['calendar', 'Calendario'], ['gmail', 'Correo'], ['meeting', 'Preparación IA'],
            ['background', 'Tareas en segundo plano'], ['app', 'Apps']]) {
            const option = el('option', '', label);
            option.value = value;
            this.originSelect.append(option);
        }
        this.originSelect.addEventListener('change', () => { this.origin = this.originSelect.value; this.render(); });
        originLabel.append(this.originSelect);
        this.refreshBtn = this._button('Actualizar', () => void this.load(true));
        toolbar.append(views, originLabel, this.refreshBtn);

        const layout = el('div', 'inbox-layout');
        this.list = el('ul', 'inbox-list');
        this.list.setAttribute('aria-label', 'Elementos de la bandeja');
        this.side = el('aside', 'inbox-side');
        layout.append(this.list, this.side);
        this.surface.append(hero, this.status, this.problems, toolbar, layout);
        this.root.replaceChildren(this.surface);
    }

    _button(label, onClick, className = '') {
        const button = el('button', className, label);
        button.type = 'button';
        button.addEventListener('click', () => { if (!button.disabled) onClick(); });
        return button;
    }

    // ── Data ───────────────────────────────────────────────────────────

    async _request(url, init = {}) {
        let response;
        try {
            response = await this.fetch(url, { cache: 'no-store', ...init });
        } catch {
            throw new Error('Core no responde. Puedes reintentar.');
        }
        let body = null;
        try { body = await response.json(); } catch { body = null; }
        if (!response.ok) {
            const error = new Error(inboxErrorText(body, 'No se pudo completar la acción.'));
            error.code = body?.detail?.code || '';
            throw error;
        }
        return body;
    }

    /** Core or Vision said the inbox changed: refresh now if visible, else on the next showing. */
    changed() {
        this.dirty = true;
        if (this.busy) this._reloadPending = true;
        else if (this.isVisible()) void this.load(true);
    }

    async load(force = false) {
        if (!this.root) return;
        if (this.busy) { this._reloadPending = true; return; }
        if (!force && this.state && !this.dirty) { this.render(); this._schedule(); return; }
        const generation = ++this._generation;
        this.loading = true;
        this.render();
        try {
            const body = await this._request('/api/inbox');
            if (generation !== this._generation) return; // an older answer: discarded
            this.state = normalizeInbox(body);
            this.error = '';
            this.dirty = false;
            this.onSummary(this.state.unread);
        } catch (error) {
            if (generation !== this._generation) return;
            this.error = error.message; // the last good state stays visible, marked as not updated
        } finally {
            if (generation === this._generation) {
                this.loading = false;
                this.render();
                this._schedule();
            }
        }
    }

    /** One refresh when the next snooze returns or notice expires; none while hidden. */
    _schedule() {
        this._clearTimer(this._timer);
        this._timer = null;
        if (!this.state || !this.isVisible()) return;
        const at = nextChangeAt(this.state);
        if (!at) return;
        const elapsed = Math.max(0, Date.now() / 1000 - this.state.receivedAt);
        const ms = Math.min(2147483647, Math.max(500, (at - this.state.serverNow - elapsed) * 1000 + 250));
        this._timer = this._setTimer(() => {
            this._timer = null;
            this.dirty = true;
            if (this.isVisible()) void this.load(true);
        }, ms);
    }

    syncVisibility() {
        if (!this.isVisible()) {
            this._clearTimer(this._timer);
            this._timer = null;
        } else if (this.dirty && !this.loading) {
            void this.load(true);
        } else if (!this._timer) {
            this._schedule();
        }
    }

    stop() {
        this._clearTimer(this._timer);
        this._timer = null;
        this._generation += 1;
        this.loading = false;
    }

    async _post(url, body) {
        return this._request(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body) });
    }

    async _act(item, action) {
        if (this.busy) return;
        this.busy = true;
        this.actionError = '';
        this.notice = '';
        this.render();
        try {
            const minutes = this._snooze.get(item.key) ?? (item.origin === 'calendar' ? 10 : 60);
            const answer = await this._post('/api/inbox/action', actionBody(item, action, minutes));
            this.notice = answer?.changed === false ? 'Ya estaba así.' : {
                read: 'Marcada como leída.', unread: 'Marcada como sin leer.',
                snooze: item.origin === 'calendar' ? 'Aviso pospuesto. El evento no cambia.'
                    : 'Pospuesta. Volverá sola; la tarea no se repite.', dismiss: 'Descartada. Puedes restaurarla.',
                restore: 'Restaurada.',
            }[action] || '';
        } catch (error) {
            this.actionError = error.message;
        } finally {
            this.busy = false;
            this._reloadPending = false;
            this.dirty = true;
            await this.load(true);
        }
    }

    async _setPreferences(change) {
        if (this.busy || !this.state) return;
        this.busy = true;
        this.actionError = '';
        this.notice = '';
        this.render();
        try {
            await this._post('/api/inbox/preferences', { expected_revision: this.state.preferences.revision, ...change });
            this.notice = 'return_notice' in change
                ? (change.return_notice ? 'Aviso al volver activado.' : 'Aviso al volver desactivado.')
                : change.quiet_minutes ? 'Avisos al volver silenciados.' : 'Silencio quitado.';
        } catch (error) {
            this.actionError = error.message;
        } finally {
            this.busy = false;
            this._reloadPending = false;
            this.dirty = true;
            await this.load(true);
        }
    }

    // ── Rendering ──────────────────────────────────────────────────────

    render() {
        if (!this.root) return;
        const state = this.state;
        this.counterValue.textContent = state ? String(state.unread).padStart(2, '0') : '—';
        this.status.textContent = this.busy ? 'Guardando…'
            : this.actionError ? this.actionError
                : this.error ? (state ? `${this.error} Se muestra lo último que respondió Core.` : this.error)
                    : this.loading && !state ? 'Cargando la bandeja…'
                        : this.notice || (state ? 'Abrir la bandeja o ver un aviso no marca nada como leído.' : '');
        this.status.dataset.tone = this.actionError || this.error ? 'error' : this.notice ? 'ok' : 'info';
        const problems = state ? sourceProblems(state) : [];
        this.problems.replaceChildren(...problems.map((message) => el('p', '', message)));
        this.problems.hidden = !problems.length;
        for (const [view, button] of Object.entries(this.viewButtons)) {
            const unread = state ? filterItems(state, { view: 'unread', origin: this.origin }).filter(isUnread).length : 0;
            button.textContent = { unread: `Sin leer · ${unread}`, all: 'Todo', snoozed: 'Pospuestas',
                dismissed: 'Descartadas' }[view];
            button.setAttribute('aria-pressed', String(this.view === view));
        }
        this.originSelect.value = this.origin;
        this.refreshBtn.disabled = this.loading || this.busy;
        this._renderList();
        this._renderSide();
    }

    _renderList() {
        const focused = focusedKey(this._controls);
        this._controls = new Map();
        const state = this.state;
        if (!state) { this.list.replaceChildren(); return; }
        const items = filterItems(state, { view: this.view, origin: this.origin });
        if (!items.length) {
            const empty = el('li', 'inbox-empty');
            const unreadable = sourceProblems(state).length > 0;
            empty.append(el('strong', '', unreadable ? 'No se pudo leer todo' : 'Nada por aquí'),
                el('p', '', unreadable ? 'Lo que no se pudo leer no significa que no haya resultados.'
                    : this.view === 'unread' ? 'No hay nada sin leer. No hace falta llenar el silencio.'
                        : 'No hay elementos con este filtro.'));
            this.list.replaceChildren(empty);
            return;
        }
        const open = new Set([...this.list.querySelectorAll('details')].filter((node) => node.open)
            .map((node) => node.dataset.key));
        this.list.replaceChildren(...items.map((item) => this._renderItem(item, open)));
        this._controls.get(focused)?.focus?.({ preventScroll: true });
    }

    _renderItem(item, open) {
        const card = el('li', 'inbox-item');
        card.dataset.key = item.key;
        card.dataset.origin = item.origin;
        card.dataset.unread = String(isUnread(item));
        const meta = el('div', 'inbox-item-meta');
        const chip = item.origin === 'app' ? item.appName.toLocaleUpperCase('es') : ORIGIN_LABELS[item.origin];
        meta.append(el('span', 'inbox-origin-chip', chip), el('time', '', when(item.updatedAt)));
        const head = el('div', 'inbox-item-head');
        const outcome = OUTCOME_LABELS[item.outcome];
        head.append(el('strong', '', item.title), el('span', `inbox-pill tone-${outcome.tone}`, outcome.label));
        card.append(meta, head);
        if (item.text) {
            if (item.text.length > LONG_TEXT) {
                const details = el('details', 'inbox-text-more');
                details.dataset.key = item.key;
                details.open = open.has(item.key);
                const summary = el('summary', '', 'Ver completo');
                this._controls.set(`${item.key}:more`, summary);
                details.append(summary, el('p', 'inbox-text', item.text));
                card.append(el('p', 'inbox-text inbox-text-preview', `${item.text.slice(0, LONG_TEXT).trimEnd()}…`), details);
            } else {
                card.append(el('p', 'inbox-text', item.text));
            }
        }
        card.append(el('p', 'inbox-item-state', item.origin === 'vision'
            ? `${statusText(item, when)} Sus acciones están en la vista Vision.` : statusText(item, when)));
        const actions = el('div', 'inbox-actions');
        if (item.origin === 'vision') {
            if (this.onOpenVision) {
                const open = this._button('Abrir en Vision', () => this.onOpenVision(item.sourceId));
                this._controls.set(`${item.key}:vision`, open);
                actions.append(open);
            }
        } else {
            for (const action of item.actions) {
                if (action === 'snooze') {
                    const delay = el('select', 'inbox-delay');
                    delay.setAttribute('aria-label', `Cuánto posponer «${item.title}»`);
                    for (const minutes of SNOOZE_MINUTES) {
                        const option = el('option', '', minutes < 60 ? `${minutes} min` : `${minutes / 60} h`);
                        option.value = String(minutes);
                        delay.append(option);
                    }
                    delay.value = String(this._snooze.get(item.key) ?? (item.origin === 'calendar' ? 10 : 60));
                    delay.disabled = this.busy;
                    delay.addEventListener('change', () => this._snooze.set(item.key, Number(delay.value)));
                    this._controls.set(`${item.key}:delay`, delay);
                    actions.append(delay);
                }
                const button = this._button(actionLabel(item, action), () => void this._act(item, action),
                    action === 'read' ? 'inbox-primary' : '');
                button.disabled = this.busy;
                this._controls.set(`${item.key}:${action}`, button);
                actions.append(button);
            }
            if (item.origin === 'automation' && this.onOpenAutomations && item.ruleId) {
                const history = this._button('Ver en Automatizaciones', () => this.onOpenAutomations(item.ruleId));
                this._controls.set(`${item.key}:history`, history);
                actions.append(history);
            }
            if (item.origin === 'app' && this.onOpenApp && item.appId) {
                const open = this._button(`Abrir ${item.appName}`, () => this.onOpenApp(item.appId));
                this._controls.set(`${item.key}:app`, open);
                actions.append(open);
            }
            if (item.origin === 'gmail') {
                const destination = mailOpenPayload(item);
                if (destination && this.onOpenMail) actions.append(this._button('Abrir en Correo', () => this.onOpenMail(destination)));
            }
            if (['calendar', 'meeting'].includes(item.origin)) {
                const destination = calendarOpenPayload(item);
                if (this.onOpenCalendar && destination) {
                    const openCalendar = this._button('Abrir en Calendario', () => this.onOpenCalendar(destination));
                    this._controls.set(`${item.key}:calendar`, openCalendar);
                    actions.append(openCalendar);
                }
                const url = safeCalendarJoinUrl(item.joinUrl);
                if (url) {
                    const join = el('a', 'inbox-primary', 'Unirse');
                    join.href = url;
                    join.target = '_blank';
                    join.rel = 'noopener noreferrer';
                    this._controls.set(`${item.key}:join`, join);
                    actions.append(join);
                }
            }
        }
        if (actions.children.length) card.append(actions);
        return card;
    }

    _renderSide() {
        const state = this.state;
        const prefs = state?.preferences;
        const now = state ? state.serverNow + Math.max(0, Date.now() / 1000 - state.receivedAt) : 0;
        const quiet = Boolean(prefs && prefs.quietUntil > now);
        const notice = el('section', 'inbox-card');
        notice.append(el('span', 'inbox-kicker', 'AL VOLVER'),
            el('h3', '', !prefs?.available ? 'Aviso al volver' : !prefs.returnNotice ? 'Aviso al volver: apagado'
                : quiet ? 'Aviso al volver: en silencio' : 'Aviso al volver: activado'),
            el('p', '', 'Cuando vuelvas a usar Iris, un aviso visual agrupa los resultados que no has visto. '
                + 'Sin voz, sin abrir el chat y sin marcar nada como leído.'));
        if (!prefs?.available) {
            notice.append(el('small', 'inbox-help', 'Se puede ajustar cuando exista alguna automatización.'));
        } else {
            const toggle = this._button(prefs.returnNotice ? 'Desactivar aviso al volver' : 'Activar aviso al volver',
                () => void this._setPreferences({ return_notice: !prefs.returnNotice }));
            toggle.disabled = this.busy;
            toggle.setAttribute('aria-pressed', String(prefs.returnNotice));
            notice.append(toggle);
            if (quiet) {
                notice.append(el('p', '', `En silencio hasta ${when(prefs.quietUntil)}.`));
                const lift = this._button('Quitar silencio', () => void this._setPreferences({ quiet_minutes: 0 }));
                lift.disabled = this.busy;
                notice.append(lift);
            } else if (prefs.returnNotice) {
                const row = el('div', 'inbox-quiet');
                const select = el('select');
                select.setAttribute('aria-label', 'Cuánto tiempo silenciar el aviso al volver');
                for (const minutes of QUIET_MINUTES) {
                    const option = el('option', '', `${minutes / 60} h`);
                    option.value = String(minutes);
                    select.append(option);
                }
                select.value = String(QUIET_MINUTES[0]);
                select.disabled = this.busy;
                const silence = this._button('Silenciar', () => {
                    const minutes = Number(select.value);
                    // Only a listed duration: 0 would mean "lift the silence".
                    if (QUIET_MINUTES.includes(minutes)) void this._setPreferences({ quiet_minutes: minutes });
                });
                silence.disabled = this.busy;
                row.append(select, silence);
                notice.append(row);
            }
        }
        notice.append(el('small', 'inbox-help', 'Se aplaza mientras escribes en el chat, Iris habla o escucha, '
            + 'o la ventana está oculta. El modo Focus de Windows todavía no se detecta.'));

        const how = el('section', 'inbox-card');
        how.append(el('span', 'inbox-kicker', 'CÓMO FUNCIONA'));
        const list = el('ul', 'inbox-how');
        for (const line of [
            'Mostrada en un aviso no es leída: solo tú la marcas como leída.',
            'Posponer la trae de vuelta más tarde; no repite la tarea ni cambia su resultado.',
            'Los fallos se explican tal como ocurrieron.',
            'Los avisos de Vision se gestionan en su propia vista.',
            'Los recordatorios del Calendario no cambian el evento ni sus permisos en Google.',
        ]) list.append(el('li', '', line));
        how.append(list);
        this.side.replaceChildren(notice, how);
    }
}
