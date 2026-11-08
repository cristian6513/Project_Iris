/**
 * Conexiones (F10.4): the window over Iris' Google connections, which Core owns (F10.1–F10.3).
 *
 * It never holds a credential. It reads GET /api/connections/google (this PC only) and shows the
 * accounts, the permissions Google really granted, and how far each service was read: states and
 * numbers, never a mail or an event. Opening the window starts nothing: connecting, reading,
 * changing permissions or disconnecting only happen on a click. The consent page opens in the
 * system browser from a real link, and the window keeps saying «Esperando autorización» until Core
 * says otherwise: opening the browser is not authorizing. Polling runs only while it is visible.
 */

const CSS = 'conexiones';
const API = '/api/connections/google';
const POLL_SESSION_MS = 1500;
const POLL_READING_MS = 3000;
const GOOGLE_CONSENT = 'https://accounts.google.com/';
const ACTIVE = new Set(['starting', 'awaiting_user', 'validating']);
const MAX_CLIENT_BYTES = 64 * 1024;
const CLIENT_HELP = 'Usa el JSON de un cliente OAuth de tipo Aplicación de escritorio. No una API key ni un archivo de cuenta de servicio.';

export const SERVICES = Object.freeze([
    { id: 'calendar.read', name: 'Calendario', unit: ['evento', 'eventos'],
      effect: 'Ver tus calendarios y eventos. Iris no crea ni cambia nada.' },
    { id: 'gmail.read', name: 'Correo', unit: ['correo', 'correos'],
      effect: 'Leer inicialmente tus correos recientes y conservar sus actualizaciones posteriores: remitente, asunto y extracto. Iris no envía, no borra ni marca nada como leído.' },
]);
const AUTH = Object.freeze({ connected: 'Conectada', partial: 'Permisos parciales',
    reauth_required: 'Hay que reconectarla', disconnected: 'Desconectada' });
const REVOCATION = Object.freeze({
    pending: 'Pidiendo a Google que revoque el acceso…',
    confirmed: 'Google revocó el acceso de Iris.',
    unknown: 'No se pudo confirmar si Google revocó el acceso. Puedes comprobarlo en tu cuenta de Google.',
    failed: 'Google no revocó el acceso. Puedes hacerlo desde tu cuenta de Google.',
});
const SESSION_END = Object.freeze({
    committed: 'Cuenta conectada. Iris aún no ha leído nada: usa «Actualizar datos» cuando quieras.',
    partial: 'Cuenta conectada, pero Google no concedió todos los permisos pedidos.',
    cancelled: 'Autorización cancelada.',
});

export function relativeTime(iso, now = Date.now()) {
    const then = Date.parse(iso || '');
    if (!Number.isFinite(then)) return '';
    const minutes = Math.round((now - then) / 60000);
    if (minutes < 1) return 'hace un momento';
    if (minutes < 60) return `hace ${minutes} min`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `hace ${hours} h`;
    const days = Math.round(hours / 24);
    return days === 1 ? 'hace 1 día' : `hace ${days} días`;
}

const clock = (iso) => {
    const at = new Date(iso || '');
    return Number.isFinite(at.getTime()) ? at.toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' }) : '';
};

const newRequestId = () => (globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9]/g, '').slice(0, 40);

function failure(status) {
    if (status === 403) return 'Las conexiones solo se administran en este equipo.';
    if (status === 404) return 'Ese elemento ya no existe.';
    if (status === 503) return 'La conexión con Google no está disponible en esta instalación.';
    return 'Iris no respondió como se esperaba. Inténtalo de nuevo.';
}

export function createApp(context = {}, { fetchImpl = (...args) => globalThis.fetch(...args), now = () => Date.now(),
                                         requestId = newRequestId } = {}) {
    const document = globalThis.document;
    const state = {
        status: 'loading', error: '', notice: '', available: false, accounts: [], session: null,
        chosen: new Set(['calendar.read']),
        editing: '', editChosen: new Set(),
        disconnecting: '', revokeRemote: false, clearCache: false,
        busy: new Set(),
        client: null, clientEditing: false, clientDraft: '', clientFile: '', fileReading: false,
    };
    let root = null;
    let visible = false;
    let timer = null;
    let generation = 0;
    let sessionEpoch = 0;
    let disposed = false;
    let fileEpoch = 0;
    const inFlight = new Set();

    /** One element: class CSS-<part> (CSS for the root), data-part for tests, data-key for focus. */
    const el = (tag, part = '', text = '', key = '') => {
        const node = document.createElement(tag);
        node.className = part ? `${CSS}-${part}` : CSS;
        node.dataset.part = part || 'app';
        if (key) node.dataset.key = key;
        if (text) node.textContent = text;
        return node;
    };

    const button = (part, text, key, onClick, { disabled = false, tone = '' } = {}) => {
        const node = el('button', part, text, key);
        node.type = 'button';
        node.disabled = disabled;
        if (tone) node.dataset.tone = tone;
        node.addEventListener('click', () => { void onClick(); });
        return node;
    };

    const checkbox = (part, label, key, checked, onChange, { disabled = false, hint = '' } = {}) => {
        const wrap = el('label', part);
        const input = el('input', `${part}-input`, '', key);
        input.type = 'checkbox';
        input.checked = checked;
        input.disabled = disabled;
        input.addEventListener('change', () => { void onChange(input.checked); });
        const text = el('span', `${part}-text`);
        text.append(el('strong', `${part}-label`, label));
        if (hint) text.append(el('small', `${part}-hint`, hint));
        wrap.append(input, text);
        return wrap;
    };

    async function call(path, { method = 'GET', body } = {}) {
        const controller = new AbortController();
        inFlight.add(controller);
        try {
            const response = await fetchImpl(`${API}${path}`, {
                method, cache: 'no-store', signal: controller.signal,
                headers: body ? { 'Content-Type': 'application/json' } : undefined,
                body: body ? JSON.stringify(body) : undefined,
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                const error = new Error(payload?.error?.message || failure(response.status));
                error.code = payload?.error?.code || '';
                error.status = response.status;
                throw error;
            }
            return payload;
        } finally {
            inFlight.delete(controller);
        }
    }

    const sessionActive = () => Boolean(state.session && ACTIVE.has(state.session.state));
    const reading = () => state.accounts.some((account) => account.remote_revocation === 'pending'
        || Object.values(account.sync || {}).some((service) => service?.state === 'running'));

    function schedule() {
        clearTimeout(timer);
        timer = null;
        if (disposed || !visible) return;  // hidden: no polling; showing again picks it up
        if (sessionActive()) timer = setTimeout(() => { void pollSession(); }, POLL_SESSION_MS);
        else if (reading()) timer = setTimeout(() => { void load(); }, POLL_READING_MS);
    }

    // What the window shows from Core. A poll that brings nothing new does not redraw, so a click
    // in progress is never lost to a replaced button.
    const shown = () => JSON.stringify([state.status, state.error, state.notice, state.available, state.accounts, state.session, state.client]);

    function setSession(session) {
        if (state.session?.id !== session?.id) sessionEpoch += 1;
        state.session = session;
    }

    const sessionGone = 'Iris ya no encuentra esa autorización. Revisa la cuenta y vuelve a conectar Google si hace falta.';

    async function load({ missingSessionId = '' } = {}) {
        const mine = ++generation;
        const epoch = sessionEpoch;
        const before = shown();
        try {
            const snapshot = await call('');
            if (mine !== generation || disposed) return;
            state.available = snapshot.available === true;
            state.client = snapshot.client && typeof snapshot.client === 'object' ? snapshot.client : null;
            state.accounts = Array.isArray(snapshot.accounts) ? snapshot.accounts : [];
            // A snapshot started before a new attempt must not replace that attempt. A successful
            // fresh snapshot, including session:null after a Core restart, is authoritative.
            if (epoch === sessionEpoch) {
                const session = snapshot.session;
                if (session && ACTIVE.has(session.state) && session.id !== missingSessionId) setSession(session);
                else {
                    const wasActive = sessionActive();
                    setSession(null);
                    if (session && !ACTIVE.has(session.state)) state.notice = sessionMessage(session);
                    else if (wasActive) state.notice = sessionGone;
                }
            }
            state.status = 'ready';
            state.error = '';
            const active = state.accounts.filter((account) => account.auth_state !== 'disconnected').length;
            context.setBrief?.(active === 1 ? '1 cuenta' : active ? `${active} cuentas` : 'Sin cuentas');
        } catch (error) {
            if (mine !== generation || disposed || error?.name === 'AbortError') return;
            state.status = 'error';
            state.error = error?.message || failure(0);
        }
        if (shown() !== before) render();
        schedule();
    }

    async function act(key, work) {
        if (state.busy.has(key) || disposed) return;  // a double click is one request
        state.busy.add(key);
        state.notice = '';
        render();
        try {
            await work();
        } catch (error) {
            if (disposed || error?.name === 'AbortError') return;
            state.notice = error?.message || failure(0);
            if (error?.code === 'revision_conflict') await load();
        } finally {
            state.busy.delete(key);
            if (!disposed) {
                render();
                schedule();
            }
        }
    }

    const sessionMessage = (session) => SESSION_END[session.state]
        || session.error?.message || 'La autorización no se completó.';

    function finishSession(session) {
        setSession(null);
        state.notice = sessionMessage(session);
        // The accounts may be unchanged (denial, timeout, cancellation): show the terminal now,
        // without relying on the account refresh to cause a redraw.
        render();
        schedule();
        void load();
    }

    async function pollSession() {
        const session = state.session;
        if (!session || disposed) return;
        const epoch = sessionEpoch;
        const before = shown();
        try {
            const current = await call(`/sessions/${encodeURIComponent(session.id)}`);
            if (disposed || epoch !== sessionEpoch || state.session?.id !== session.id) return;
            state.session = current;
            if (!ACTIVE.has(current.state)) {
                finishSession(current);
                return;
            }
        } catch (error) {
            if (disposed || epoch !== sessionEpoch || state.session?.id !== session.id || error?.name === 'AbortError') return;
            if (error?.status === 404 || error?.code === 'not_found') {
                await load({ missingSessionId: session.id });
                return;
            }
            state.notice = error?.message || failure(0);
            render();
        }
        if (shown() !== before) render();
        schedule();
    }

    const connect = (services, accountId = null) => act('connect', async () => {
        const epoch = ++sessionEpoch;
        const session = await call('/sessions', { method: 'POST', body: {
            services, request_id: requestId(), ...(accountId ? { account_id: accountId } : {}),
        } });
        if (disposed || epoch !== sessionEpoch) return;
        if (ACTIVE.has(session.state)) setSession(session);
        else finishSession(session);
        state.editing = '';
    });

    const cancel = () => act('cancel', async () => {
        const session = state.session;
        if (!session) return;
        const epoch = sessionEpoch;
        try {
            const current = await call(`/sessions/${encodeURIComponent(session.id)}/cancel`, { method: 'POST' });
            if (disposed || epoch !== sessionEpoch || state.session?.id !== session.id) return;
            if (ACTIVE.has(current.state)) setSession(current);
            else finishSession(current);
        } catch (error) {
            if (disposed || epoch !== sessionEpoch || state.session?.id !== session.id) return;
            if (error?.status === 404 || error?.code === 'not_found') await load({ missingSessionId: session.id });
            else throw error;
        }
    });

    const replaceAccount = (account) => {
        state.accounts = state.accounts.map((item) => (item.id === account.id ? { ...item, ...account, sync: item.sync } : item));
    };

    const toggle = (account, service, on) => act(`periodic:${account.id}:${service}`, async () => {
        const enabled = SERVICES.map((item) => item.id).filter((id) => (id === service
            ? on : account.services?.[id]?.enabled));
        replaceAccount(await call(`/accounts/${encodeURIComponent(account.id)}/services`, {
            method: 'PATCH', body: { services: enabled, revision: account.revision } }));
    });

    const sync = (account) => act(`sync:${account.id}`, async () => {
        const reading = await call(`/accounts/${encodeURIComponent(account.id)}/sync`, { method: 'POST', body: {} });
        state.accounts = state.accounts.map((item) => (item.id === account.id ? { ...item, sync: reading } : item));
    });

    const disconnect = (account) => act(`disconnect:${account.id}`, async () => {
        const label = account.label || account.email || 'esta cuenta';
        const message = [
            'Iris dejará de usar esta cuenta en este equipo.',
            state.revokeRemote
                ? 'También pedirá a Google que revoque el acceso de Iris. Puede afectar a otras instalaciones de Iris conectadas con esta misma cuenta.'
                : 'El permiso que diste en Google sigue activo hasta que lo revoques allí.',
            state.clearCache ? 'Se borrarán los datos que Iris guardó de esta cuenta.' : 'Los datos ya leídos se conservan en este equipo.',
        ].join(' ');
        const confirmed = await context.confirm?.({ title: `Desconectar ${label}`, message, tone: 'danger',
            confirmLabel: 'Desconectar', cancelLabel: 'Volver' });
        if (confirmed !== true) return;  // without Iris' dialog, nothing is disconnected
        const updated = await call(`/accounts/${encodeURIComponent(account.id)}/disconnect`, { method: 'POST', body: {
            revoke_remote: state.revokeRemote, clear_cache: state.clearCache, revision: account.revision } });
        state.disconnecting = '';
        replaceAccount(updated);
        await load();
    });

    const forget = (account) => act(`forget:${account.id}`, async () => {
        const confirmed = await context.confirm?.({
            title: `Quitar ${account.label || account.email || 'esta cuenta'}`,
            message: 'Iris olvidará esta cuenta y los datos que guardó de ella. Para volver a usarla, conéctala de nuevo.',
            tone: 'danger', confirmLabel: 'Quitar', cancelLabel: 'Volver' });
        if (confirmed !== true) return;
        await call(`/accounts/${encodeURIComponent(account.id)}`, { method: 'DELETE' });
        await load();
    });

    // ── Drawing ─────────────────────────────────────────────────────────

    function readingText(service, sync) {
        if (!sync) return 'Aún no leído.';
        if (sync.state === 'running') return 'Leyendo…';
        if (sync.state === 'backoff') return `${sync.error?.message || 'Google no respondió.'} Se volverá a intentar a las ${clock(sync.next_attempt_at)}.`;
        if (sync.state === 'error') return sync.error?.message || 'No se pudo leer.';
        if (!sync.last_success_at) return 'Aún no leído.';
        const count = Number(sync.records) || 0;
        const amount = `${count} ${count === 1 ? service.unit[0] : service.unit[1]}`;
        const partial = sync.coverage?.complete === false ? ' (lectura inicial limitada)' : '';
        return `Última lectura ${relativeTime(sync.last_success_at, now())} · ${amount}${partial}`;
    }

    function serviceRow(account, service) {
        const info = account.services?.[service.id] || {};
        const active = account.auth_state === 'connected' || account.auth_state === 'partial';
        const row = el('li', 'service');
        const head = el('div', 'service-head');
        head.append(el('strong', 'service-name', service.name),
            el('span', 'permission', info.granted ? 'Permiso concedido'
                : info.availability === 'permission_missing' ? 'Sin permiso' : 'No conectado'));
        row.append(head);
        if (info.granted && active) {
            row.append(checkbox('periodic', 'Actualizar automáticamente', `periodic:${account.id}:${service.id}`,
                info.enabled === true, (on) => toggle(account, service.id, on),
                { disabled: state.busy.has(`periodic:${account.id}:${service.id}`), hint: 'Cada 15 minutos mientras Iris está abierto.' }));
            row.append(el('p', 'reading', readingText(service, account.sync?.[service.id])));
        }
        return row;
    }

    function accountCard(account) {
        const card = el('li', 'account');
        card.dataset.account = account.id;
        const head = el('header', 'account-head');
        const name = el('div', 'account-name');
        name.append(el('strong', 'account-label', account.label || account.email || 'Cuenta de Google'));
        if (account.email && account.email !== account.label) name.append(el('span', 'account-email', account.email));
        const badge = el('span', 'auth', AUTH[account.auth_state] || account.auth_state);
        badge.dataset.state = account.auth_state;
        head.append(name, badge);
        card.append(head);
        if (account.error?.message) card.append(el('p', 'account-error', account.error.message));
        if (REVOCATION[account.remote_revocation]) card.append(el('p', 'revocation', REVOCATION[account.remote_revocation]));
        if (account.auth_state !== 'disconnected') {  // a disconnected account has no permission in use
            const services = el('ul', 'services');
            services.append(...SERVICES.map((service) => serviceRow(account, service)));
            card.append(services);
        }

        const active = account.auth_state === 'connected' || account.auth_state === 'partial';
        const actions = el('div', 'actions');
        if (active) {
            const running = Object.values(account.sync || {}).some((item) => item?.state === 'running');
            actions.append(button('sync', running ? 'Leyendo…' : 'Actualizar datos', `sync:${account.id}`, () => sync(account),
                { disabled: running || state.busy.has(`sync:${account.id}`) || !state.available }));
        }
        if (account.auth_state !== 'disconnected') {
            actions.append(button('edit', account.auth_state === 'reauth_required' ? 'Reconectar' : 'Cambiar permisos…',
                `edit:${account.id}`, () => {
                    state.editing = account.id;
                    state.disconnecting = '';
                    state.editChosen = new Set(SERVICES.map((item) => item.id)
                        .filter((id) => account.services?.[id]?.granted || account.services?.[id]?.availability === 'permission_missing'));
                    render();
                }, { disabled: sessionActive() || !state.available }));
            actions.append(button('disconnect', 'Desconectar…', `disconnect-open:${account.id}`, () => {
                state.disconnecting = account.id;
                state.editing = '';
                state.revokeRemote = false;
                state.clearCache = false;
                render();
            }, { tone: 'danger' }));
        } else {
            actions.append(button('forget', 'Quitar de la lista', `forget:${account.id}`, () => forget(account),
                { disabled: state.busy.has(`forget:${account.id}`) }));
        }
        card.append(actions);
        if (state.editing === account.id) card.append(editPanel(account));
        if (state.disconnecting === account.id) card.append(disconnectPanel(account));
        return card;
    }

    function chooser(chosen, prefix) {
        const list = el('div', 'chooser');
        for (const service of SERVICES) {
            list.append(checkbox('choice', `${service.name}: lectura`, `${prefix}:${service.id}`, chosen.has(service.id),
                (on) => { if (on) chosen.add(service.id); else chosen.delete(service.id); render(); },
                { hint: service.effect }));
        }
        return list;
    }

    function editPanel(account) {
        const panel = el('div', 'panel');
        panel.append(el('p', 'panel-text', 'Google pedirá de nuevo todos los permisos elegidos. La conexión actual sigue igual hasta que autorices.'));
        panel.append(chooser(state.editChosen, `edit-choice:${account.id}`));
        const row = el('div', 'panel-actions');
        row.append(button('authorize', 'Autorizar en Google', `authorize:${account.id}`,
            () => connect(SERVICES.map((item) => item.id).filter((id) => state.editChosen.has(id)), account.id),
            { disabled: !state.editChosen.size || state.busy.has('connect') }),
        button('back', 'Cancelar', `edit-back:${account.id}`, () => { state.editing = ''; render(); }));
        panel.append(row);
        return panel;
    }

    function disconnectPanel(account) {
        const panel = el('div', 'panel');
        panel.dataset.tone = 'danger';
        panel.append(checkbox('choice', 'Revocar también el acceso en Google', `revoke:${account.id}`, state.revokeRemote,
            (on) => { state.revokeRemote = on; render(); },
            { hint: 'Si no, Iris solo deja de usarla en este equipo y el permiso sigue en tu cuenta de Google.' }));
        panel.append(checkbox('choice', 'Borrar los datos que Iris guardó de esta cuenta', `clear:${account.id}`, state.clearCache,
            (on) => { state.clearCache = on; render(); }));
        const row = el('div', 'panel-actions');
        row.append(button('confirm-disconnect', 'Desconectar', `disconnect:${account.id}`, () => disconnect(account),
            { tone: 'danger', disabled: state.busy.has(`disconnect:${account.id}`) }),
        button('back', 'Cancelar', `disconnect-back:${account.id}`, () => { state.disconnecting = ''; render(); }));
        panel.append(row);
        return panel;
    }

    function sessionPanel() {
        const session = state.session;
        const panel = el('section', 'session');
        panel.setAttribute('aria-live', 'polite');
        panel.append(el('h3', 'session-title', session.state === 'validating' ? 'Comprobando la autorización…' : 'Esperando autorización'));
        const url = typeof session.authorization_url === 'string' && session.authorization_url.startsWith(GOOGLE_CONSENT)
            ? session.authorization_url : '';
        if (url) {
            panel.append(el('p', 'session-text', 'Abre Google en tu navegador, elige la cuenta y revisa los permisos. Esta ventana se actualiza sola.'));
            const link = el('a', 'consent', 'Abrir Google', 'consent');
            link.href = url;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            panel.append(link);
        } else if (session.state !== 'validating') {
            panel.append(el('p', 'session-text', 'Preparando la autorización…'));
        }
        if (session.expires_at) panel.append(el('p', 'session-expires', `Vence a las ${clock(session.expires_at)}.`));
        panel.append(button('cancel', 'Cancelar', 'cancel', cancel, { disabled: state.busy.has('cancel') }));
        return panel;
    }

    function newConnection() {
        const panel = el('section', 'new');
        panel.append(el('h3', 'new-title', state.accounts.some((item) => item.auth_state !== 'disconnected')
            ? 'Añadir otra cuenta' : 'Conectar Google'));
        panel.append(el('p', 'new-text', 'Elige qué podrá leer Iris. Google te mostrará exactamente estos permisos, y puedes concederlos solo en parte.'));
        panel.append(chooser(state.chosen, 'new-choice'));
        panel.append(button('connect', 'Conectar Google', 'connect',
            () => connect(SERVICES.map((item) => item.id).filter((id) => state.chosen.has(id))),
            { disabled: !state.chosen.size || state.busy.has('connect') }));
        return panel;
    }

    async function selectClientFile(file) {
        const mine = ++fileEpoch;
        state.notice = '';
        if (!file || file.size > MAX_CLIENT_BYTES || file.size <= 0) {
            state.notice = 'El archivo debe contener un JSON de hasta 64 KB.';
            render(); return;
        }
        state.fileReading = true; render();
        try {
            const text = await file.text();
            if (disposed || mine !== fileEpoch) return;
            if (new TextEncoder().encode(text).length > MAX_CLIENT_BYTES) throw new Error();
            state.clientDraft = text; state.clientFile = String(file.name || 'Archivo seleccionado').slice(0, 120);
        } catch {
            if (!disposed && mine === fileEpoch) state.notice = 'No se pudo leer ese archivo JSON.';
        } finally {
            if (!disposed && mine === fileEpoch) { state.fileReading = false; render(); }
        }
    }

    function saveClient() {
        if (!state.client?.can_change || state.fileReading) return;
        try {
            if (!state.clientDraft || new TextEncoder().encode(state.clientDraft).length > MAX_CLIENT_BYTES) throw new Error();
            const doc = JSON.parse(state.clientDraft.replace(/^\uFEFF/, ''));
            if (!doc || Object.keys(doc).length !== 1 || !doc.installed || typeof doc.installed !== 'object') throw new Error();
        } catch { state.notice = CLIENT_HELP; render(); return; }
        const text = state.clientDraft;
        return act('client', async () => {
            await call('/client', { method: 'PUT', body: { revision: state.client.revision, json_text: text } });
            fileEpoch++; state.clientDraft = ''; state.clientFile = ''; state.clientEditing = false;
            await load();
            state.notice = 'Cliente guardado en este perfil. Ahora puedes conectar tu cuenta de Google.';
        });
    }

    async function removeClient() {
        if (!state.client?.can_change || state.busy.has('client')) return;
        const revision = state.client.revision;
        const yes = await context.confirm?.({ title: 'Quitar configuración de Google',
            message: 'Iris dejará de usar este cliente OAuth. Los datos guardados de cuentas desconectadas se conservan.',
            confirmLabel: 'Quitar configuración', tone: 'danger' });
        if (!yes || disposed) return;
        return act('client', async () => {
            await call('/client', { method: 'DELETE', body: { revision } });
            fileEpoch++; state.clientDraft = ''; state.clientFile = ''; state.clientEditing = false;
            await load(); state.notice = 'Configuración retirada. Puedes importar otro cliente cuando quieras.';
        });
    }

    function clientPanel() {
        const config = state.client;
        const panel = el('section', 'client');
        const busy = state.busy.size > 0 || sessionActive();
        const blocked = config.can_change !== true || busy;
        panel.append(el('span', 'client-kicker', 'GOOGLE CLOUD · CONFIGURACIÓN PERSONAL'));
        panel.append(el('h3', 'client-title', config.configured ? 'Cliente de Google configurado' : 'Configura el acceso a Google'));
        panel.append(el('p', 'client-description', config.configured
            ? 'La configuración está lista. El acceso a tu cuenta se concede después en Google.'
            : 'Importa las credenciales de tu proyecto para conectar Calendario o Correo. Se guardan protegidas en tu perfil de Iris.'));
        if (!config.can_change) panel.append(el('p', 'client-blocked', 'Para cambiar el cliente, desconecta las cuentas activas y termina las autorizaciones o lecturas pendientes.'));
        if (config.error) panel.append(el('p', 'client-error', 'No se pudo abrir la configuración guardada. Puedes volver a importar el cliente.'));
        const editing = !config.configured || state.clientEditing;
        if (editing && config.can_change) {
            const form = el('div', 'client-form');
            const fileLabel = el('label', 'client-file-label', 'Importar archivo JSON');
            const file = el('input', 'client-file', '', 'client-file'); file.type = 'file'; file.accept = '.json,application/json';
            file.disabled = blocked || state.fileReading;
            file.addEventListener('change', () => { void selectClientFile(file.files?.[0]); });
            fileLabel.append(file); form.append(fileLabel);
            if (state.fileReading || state.clientFile) form.append(el('p', 'client-filename', state.fileReading ? 'Leyendo archivo…' : state.clientFile));
            const label = el('label', 'client-text-label', 'O pega el contenido del JSON');
            const input = el('textarea', 'client-text', '', 'client-text'); input.rows = 6; input.value = state.clientDraft;
            input.autocomplete = 'off'; input.spellcheck = false; input.disabled = blocked || state.fileReading;
            input.setAttribute('aria-label', 'Contenido JSON del cliente OAuth');
            input.addEventListener('input', () => { fileEpoch++; state.clientDraft = input.value; state.clientFile = ''; });
            label.append(input); form.append(label, el('p', 'client-hint', CLIENT_HELP));
            const actions = el('div', 'client-actions');
            actions.append(button('client-save', state.busy.has('client') ? 'Guardando…' : 'Guardar configuración', 'client-save', saveClient,
                { disabled: blocked || state.fileReading }));
            if (config.configured) actions.append(button('client-cancel', 'Cancelar', 'client-cancel', () => {
                fileEpoch++; state.clientDraft = ''; state.clientFile = ''; state.clientEditing = false; render();
            }, { disabled: busy }));
            form.append(actions); panel.append(form);
        } else if (config.configured) {
            const actions = el('div', 'client-actions');
            actions.append(button('client-edit', 'Cambiar configuración', 'client-edit', () => { state.clientEditing = true; render(); }, { disabled: blocked }));
            actions.append(button('client-remove', 'Quitar configuración', 'client-remove', removeClient, { disabled: blocked, tone: 'danger' }));
            panel.append(actions);
        }
        const help = el('details', 'client-help'); help.append(el('summary', 'client-help-title', 'Cómo obtener el archivo de Google'));
        const steps = el('ol', 'client-steps');
        for (const text of ['Crea o selecciona tu proyecto en Google Cloud.',
            'Habilita Google Calendar API y, si usarás Correo, Gmail API.',
            'Configura la pantalla de consentimiento. Si está en Pruebas, añade tu cuenta como usuario de prueba.',
            'Crea un cliente OAuth de tipo Aplicación de escritorio y descarga su JSON.']) steps.append(el('li', 'client-step', text));
        const link = el('a', 'client-console', 'Abrir Google Cloud'); link.href = 'https://console.cloud.google.com/apis/credentials';
        link.target = '_blank'; link.rel = 'noopener noreferrer';
        help.append(steps, link, el('p', 'client-testing', 'En modo Pruebas, Google puede pedirte reconectar después de 7 días. Importar el JSON no inicia sesión ni activa la IA.'));
        panel.append(help); return panel;
    }

    function render() {
        if (!root || disposed) return;
        const focused = document.activeElement?.dataset?.key || '';
        const app = el('section');
        const header = el('header', 'header');
        header.append(el('h2', 'title', 'Conexiones'),
            el('p', 'intro', 'Cuentas de Google que Calendario y Correo pueden leer. Conectar no lee nada hasta que lo pidas; nada se envía a la IA.'));
        app.append(header);
        const status = el('p', 'status', state.status === 'loading' ? 'Cargando…'
            : state.status === 'error' ? `${state.error} Pulsa «Reintentar».` : state.notice);
        status.setAttribute('role', 'status');
        app.append(status);
        if (state.status === 'error') {
            app.append(button('retry', 'Reintentar', 'retry', load));
        } else if (state.status === 'ready') {
            if (state.client) app.append(clientPanel());
            if (!state.available && (!state.client || state.client.configured)) app.append(el('p', 'unavailable', 'La conexión con Google no está disponible en esta instalación.'));
            if (sessionActive()) app.append(sessionPanel());
            if (state.accounts.length) {
                const list = el('ul', 'accounts');
                list.append(...state.accounts.map(accountCard));
                app.append(list);
            }
            if (state.available && !sessionActive() && !state.clientEditing && !state.busy.has('client')) app.append(newConnection());
        }
        root.replaceChildren(app);
        if (focused) {
            const again = (function find(node) {
                if (node?.dataset?.key === focused) return node;
                for (const child of node?.children || []) {
                    const found = find(child);
                    if (found) return found;
                }
                return null;
            })(app);
            again?.focus?.();
        }
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        fileEpoch++; state.clientDraft = ''; state.clientFile = '';
        generation += 1;
        clearTimeout(timer);
        timer = null;
        for (const controller of inFlight) controller.abort();
        inFlight.clear();
        root?.replaceChildren();
        root = null;
    }

    context.signal?.addEventListener?.('abort', dispose, { once: true });

    return {
        mount(target) {
            root = target;
            render();
        },
        /** Opening reads the state once; it never connects or reads Google. */
        refresh({ force = false, opening = false } = {}) {
            if (force || opening || state.status !== 'ready') return load();
            return undefined;
        },
        setVisibility({ visible: shown }) {
            visible = Boolean(shown);
            schedule();
        },
        dispose,
    };
}
