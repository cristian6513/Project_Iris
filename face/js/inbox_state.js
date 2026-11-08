/**
 * F07: the general inbox (`/api/inbox`, items `iris.inbox-item/v1`) as Face reads it.
 *
 * Mirrors core/inbox_contracts.py and core/inbox_api.py. Identity is composite
 * (origin, profile and source id), so a Vision notice and an automation result
 * sharing an id stay two items. Execution outcome and delivery status are
 * separate facts: opening the inbox or showing a notice never marks anything
 * read. Unknown origins and Gmail rows without an explicit local destination are left
 * out, a source that could not be read is reported (never shown as empty), and
 * every text is a plain string for textContent.
 */

const ORIGINS = new Set(['automation', 'vision', 'calendar', 'gmail', 'meeting', 'background', 'app']);
const APP_ID = /^[a-z][a-z0-9-]{1,31}$/;
const OUTCOMES = new Set(['completed', 'failed', 'running', 'unknown', 'notice']);
const READABLE = new Set(['completed', 'failed', 'notice']);
const DELIVERY = new Set(['pending', 'presented', 'read', 'snoozed', 'dismissed', 'expired']);
const ACTIONS = new Set(['read', 'unread', 'snooze', 'dismiss', 'restore']);
const ID = /^[0-9a-f]{32}$/;
const PROFILE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const CALENDAR_EVENT = /^event_[0-9a-f]{32}$/;
const CALENDAR_DATE = /^\d{4}-\d{2}-\d{2}$/;

export const SNOOZE_MINUTES = Object.freeze([10, 30, 60, 240]);
export const QUIET_MINUTES = Object.freeze([60, 240, 720]);

const text = (value, limit) => String(value ?? '').slice(0, limit);
const time = (value) => (Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) : 0);
const count = (value) => Math.max(0, Math.trunc(time(value)));

export const inboxKey = (origin, profile, sourceId) => `${origin}:${profile}:${sourceId}`;

export const OUTCOME_LABELS = Object.freeze({
    completed: { label: 'Completada', tone: 'ok' },
    failed: { label: 'Fallida', tone: 'error' },
    running: { label: 'En curso', tone: 'running' },
    unknown: { label: 'Sin confirmar', tone: 'warning' },
    notice: { label: 'Aviso', tone: 'muted' },
});

export const ORIGIN_LABELS = Object.freeze({ automation: 'AUTOMATIZACIÓN', vision: 'VISION', calendar: 'CALENDARIO', gmail: 'CORREO', meeting: 'PREPARACIÓN IA', background: 'TAREA', app: 'APP' });

function validCalendarDate(value) {
    if (typeof value !== 'string' || !CALENDAR_DATE.test(value) || value.startsWith('0000-')) return '';
    const date = new Date(`${value}T00:00:00Z`);
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? value : '';
}

/** Explicit source field only: public HTTPS domain, no credentials, IPs or local names. */
export function safeCalendarJoinUrl(value) {
    if (typeof value !== 'string' || !value || value.length > 2000 || /[\x00-\x20\\]/.test(value)) return '';
    try {
        const url = new URL(value);
        const host = url.hostname.toLowerCase().replace(/\.$/, '');
        if (url.protocol !== 'https:' || url.username || url.password || (url.port && url.port !== '443')) return '';
        const suffix = host.split('.').at(-1);
        if (!host.includes('.') || !/^(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})$/.test(suffix)) return '';
        if (host.split('.').some((part) => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(part))) return '';
        const local = ['localhost', 'local', 'localdomain', 'internal', 'lan', 'home', 'test', 'invalid', 'example'];
        if (local.some((name) => host === name || host.endsWith(`.${name}`))) return '';
        url.hostname = host;
        return url.href;
    } catch { return ''; }
}

/** A calendar destination is an opaque event plus a civil day, never parsed from notice text. */
export function calendarOpenPayload(item) {
    if (!['calendar', 'meeting'].includes(item?.origin) || !CALENDAR_EVENT.test(item.calendarEventId || '')
        || !validCalendarDate(item.calendarDate)) return null;
    return { event_id: item.calendarEventId, date: item.calendarDate };
}

export function mailOpenPayload(item) {
    if (item?.origin !== 'gmail' || !/^gacc_[a-f0-9]{32}$/.test(item.mailAccountId || '')
        || !/^mail_[a-f0-9]{40}$/.test(item.mailMessageId || '')) return null;
    return { account_id: item.mailAccountId, message_id: item.mailMessageId };
}

function normalizeItem(row) {
    if (!row || row.schema !== 'iris.inbox-item/v1' || !ORIGINS.has(row.origin)) return null;
    if (!ID.test(row.source_id || '') || !PROFILE.test(row.profile || '')) return null;
    const key = inboxKey(row.origin, row.profile, row.source_id);
    if (row.key !== key || !OUTCOMES.has(row.outcome)) return null;
    if (row.origin === 'calendar' && row.outcome !== 'notice') return null;
    if (row.origin === 'gmail' && (row.outcome !== 'notice' || !/^gacc_[a-f0-9]{32}$/.test(row.mail_account_id || '')
        || !/^mail_[a-f0-9]{40}$/.test(row.mail_message_id || ''))) return null;
    if (row.origin === 'meeting' && (!['completed', 'failed'].includes(row.outcome) || !CALENDAR_EVENT.test(row.calendar_event_id || '') || !validCalendarDate(row.calendar_date) || row.join_url)) return null;
    if (row.origin === 'app' && (row.outcome !== 'notice' || !APP_ID.test(row.app_id || ''))) return null;
    const delivery = row.delivery && typeof row.delivery === 'object' ? row.delivery : {};
    return {
        key,
        origin: row.origin,
        profile: row.profile,
        sourceId: row.source_id,
        sourceRevision: count(row.source_revision),
        ruleId: ID.test(row.rule_id || '') ? row.rule_id : '',
        calendarEventId: ['calendar', 'meeting'].includes(row.origin) && CALENDAR_EVENT.test(row.calendar_event_id || '')
            ? row.calendar_event_id : '',
        calendarDate: ['calendar', 'meeting'].includes(row.origin) ? validCalendarDate(row.calendar_date) : '',
        joinUrl: row.origin === 'calendar' ? safeCalendarJoinUrl(row.join_url) : '',
        mailAccountId: row.origin === 'gmail' && /^gacc_[a-f0-9]{32}$/.test(row.mail_account_id || '') ? row.mail_account_id : '',
        mailMessageId: row.origin === 'gmail' && /^mail_[a-f0-9]{40}$/.test(row.mail_message_id || '') ? row.mail_message_id : '',
        // The app that posted it: Core names it from Iris Store, never from the notice.
        appId: row.origin === 'app' ? row.app_id : '',
        appName: row.origin === 'app' ? text(row.app_name, 80) || row.app_id : '',
        title: text(row.title, 120) || 'Sin título',
        text: text(row.text, 20000),
        outcome: row.outcome,
        // Vision keeps its actions in its own view; only listed actions are ever offered.
        actions: ['automation', 'calendar', 'gmail', 'meeting', 'background', 'app'].includes(row.origin) && Array.isArray(row.actions)
            ? [...new Set(row.actions.filter((action) => ACTIONS.has(action)))] : [],
        delivery: {
            status: DELIVERY.has(delivery.status) ? delivery.status : 'pending',
            presentedAt: time(delivery.presented_at),
            readAt: time(delivery.read_at),
            snoozedUntil: time(delivery.snoozed_until),
            dismissedAt: time(delivery.dismissed_at),
            expiresAt: time(delivery.expires_at),
            revision: count(delivery.revision),
            uncertain: delivery.uncertain === true,
            voiceReceipt: ['spoken', 'failed', 'ambiguous'].includes(delivery.voice_receipt) ? delivery.voice_receipt : '',
        },
        createdAt: time(row.created_at),
        updatedAt: time(row.updated_at),
    };
}

function normalizeSource(value) {
    const status = ['ok', 'none', 'unavailable'].includes(value?.status) ? value.status : 'unavailable';
    return { status, message: text(value?.message, 600), revision: count(value?.revision) };
}

/** Core's answer: unknown, malformed and duplicate items are dropped. */
export function normalizeInbox(payload = {}) {
    payload = payload && typeof payload === 'object' ? payload : {};
    const seen = new Set();
    const items = (Array.isArray(payload.items) ? payload.items : []).map(normalizeItem)
        .filter((item) => item && !seen.has(item.key) && seen.add(item.key))
        .sort((a, b) => b.updatedAt - a.updatedAt)
        .slice(0, 300);
    const sources = payload.sources && typeof payload.sources === 'object' ? payload.sources : {};
    const prefs = payload.preferences && typeof payload.preferences === 'object' ? payload.preferences : {};
    return {
        items,
        unread: items.filter(isUnread).length,
        serverNow: time(payload.server_now) || Date.now() / 1000,
        receivedAt: Date.now() / 1000,
        sources: {
            automation: normalizeSource(sources.automation ?? { status: 'none' }),
            vision: normalizeSource(sources.vision ?? { status: 'none' }),
            calendar: normalizeSource(sources.calendar ?? { status: 'none' }),
            gmail: normalizeSource(sources.gmail ?? { status: 'none' }),
            meeting: normalizeSource(sources.meeting ?? { status: 'none' }),
            background: normalizeSource(sources.background ?? { status: 'none' }),
            app: normalizeSource(sources.app ?? { status: 'none' }),
        },
        preferences: {
            available: prefs.available === true,
            revision: count(prefs.revision),
            returnNotice: prefs.return_notice !== false,
            quietUntil: time(prefs.quiet_until),
        },
    };
}

/** Something to read that the user has not read (presented is not read; work in progress is not unread). */
export function isUnread(item) {
    return READABLE.has(item.outcome) && ['pending', 'presented'].includes(item.delivery.status);
}

/** Whether an item still waits for the user, including a snooze that came back. */
export function isPending(item, now = Date.now() / 1000) {
    if (item.delivery.status === 'snoozed') return item.delivery.snoozedUntil <= now;
    return item.delivery.status === 'pending' || item.delivery.status === 'presented';
}

export const VIEWS = Object.freeze(['unread', 'all', 'snoozed', 'dismissed']);

/** The items one filter shows; "Sin leer" also keeps work in progress, which may still bring a result. */
export function filterItems(state, { view = 'unread', origin = 'all' } = {}) {
    return state.items.filter((item) => (origin === 'all' || item.origin === origin) && (
        view === 'all' ? true
            : view === 'unread' ? isUnread(item) || ['running', 'unknown'].includes(item.outcome)
                : item.delivery.status === view));
}

export function statusText(item, when) {
    const d = item.delivery;
    if (item.outcome === 'running') return 'Hermes la está ejecutando; su resultado aparecerá aquí.';
    if (item.outcome === 'unknown') return 'Iris todavía no sabe cómo terminó; no se cuenta como hecha.';
    const lines = [];
    if (d.voiceReceipt === 'spoken') lines.push('Resumen reproducido por voz; no implica que lo hayas leído.');
    if (d.voiceReceipt === 'ambiguous') lines.push('La reproducción por voz quedó sin confirmar y no se repetirá sola.');
    if (d.voiceReceipt === 'failed') lines.push('El resumen no se reprodujo por voz. El texto sigue disponible.');
    if (d.status === 'snoozed') lines.push(`Pospuesta hasta ${when(d.snoozedUntil)}. `
        + (item.origin === 'calendar' ? 'Solo se pospone el aviso; el evento no cambia.' : 'Volverá sola; no se repite la tarea.'));
    else if (d.status === 'dismissed') lines.push('Descartada por ti. Puedes restaurarla.');
    else if (d.status === 'read') lines.push('Leída.');
    else if (d.status === 'expired') lines.push(item.origin === 'calendar'
        ? 'Sin leer. El aviso venció; el evento no cambia.' : 'Sin leer. Ya no se avisará al volver; sigue aquí.');
    else if (d.status === 'presented') lines.push(item.origin === 'calendar'
        ? 'Sin leer. Ya se mostró el recordatorio.' : 'Sin leer. Ya se mostró en un aviso al volver.');
    else lines.push('Sin leer.');
    if (d.uncertain) lines.push(item.origin === 'calendar'
        ? 'Puede que el recordatorio ya se haya mostrado; no se repetirá solo.'
        : 'Puede que el aviso al volver ya se haya mostrado; no se repetirá solo.');
    return lines.join(' ');
}

export function actionLabel(item, action) {
    if (action === 'restore') return item.delivery.status === 'snoozed' ? 'Mostrar ahora' : 'Restaurar';
    return { read: 'Marcar leída', unread: 'Marcar sin leer', snooze: 'Posponer', dismiss: 'Descartar' }[action] || action;
}

export function actionBody(item, action, minutes = 60) {
    const body = { key: item.key, action, expected_revision: item.delivery.revision };
    if (action === 'snooze') body.minutes = SNOOZE_MINUTES.includes(Number(minutes)) ? Number(minutes) : 60;
    return body;
}

/** Problems reading a source, said as such: never "no results". */
export function sourceProblems(state) {
    const out = [];
    if (state.sources.meeting?.status === 'unavailable') out.push(state.sources.meeting.message || 'No se pudieron leer las preparaciones de reuniones.');
    if (state.sources.gmail?.status === 'unavailable') out.push(state.sources.gmail.message || 'No se pudieron leer los avisos de Correo.');
    if (state.sources.background?.status === 'unavailable') out.push(state.sources.background.message || 'No se pudieron leer las tareas en segundo plano.');
    if (state.sources.app?.status === 'unavailable') out.push(state.sources.app.message || 'No se pudieron leer los avisos de las apps.');
    if (state.sources.automation.status === 'unavailable') {
        out.push(state.sources.automation.message || 'No se pudieron leer los resultados de automatizaciones.');
    }
    if (state.sources.vision.status === 'unavailable') {
        out.push(state.sources.vision.message || 'No se pudieron leer los avisos de Vision.');
    }
    if (state.sources.calendar?.status === 'unavailable') {
        out.push(state.sources.calendar.message || 'No se pudieron leer los avisos del calendario.');
    }
    return out;
}

export function inboxErrorText(body, fallback) {
    const detail = body?.detail;
    if (detail?.code === 'revision_conflict') return 'Cambió en otro lugar mientras tanto. Se muestra su estado actual.';
    if (typeof detail?.message === 'string' && detail.message) return detail.message.slice(0, 300);
    if (typeof detail === 'string' && detail) return detail.slice(0, 300);
    return fallback;
}

/** When the next snooze comes back or the next notice expires, to refresh once then (never polling). */
export function nextChangeAt(state) {
    const now = state.serverNow;
    const times = state.items.flatMap((item) => [
        item.delivery.status === 'snoozed' ? item.delivery.snoozedUntil : 0,
        ['pending', 'presented'].includes(item.delivery.status) ? item.delivery.expiresAt : 0,
    ]).filter((at) => at > now);
    if (state.preferences.quietUntil > now) times.push(state.preferences.quietUntil);
    return times.length ? Math.min(...times) : 0;
}

// ── The return notice (layer 3) ──────────────────────────────────────────

/** A notice from Core, checked; null when it is not one for this surface. */
export function normalizeNotice(payload, surfaceId) {
    if (!payload || payload.schema !== 'iris.inbox-presentation/v1') return null;
    if (!ID.test(payload.id || '') || !ID.test(payload.token || '') || !Number.isInteger(payload.generation)) return null;
    if (payload.surface_id !== surfaceId) return null;
    const row = (value, withOutcome) => {
        const key = String(value?.key || '');
        if (!/^automation:[a-z0-9][a-z0-9_-]{0,63}:[0-9a-f]{32}$/.test(key)) return null;
        const outcome = withOutcome ? (['completed', 'failed'].includes(value.outcome) ? value.outcome : null) : null;
        if (withOutcome && !outcome) return null;
        return { key, title: text(value.title, 120) || 'Automatización', outcome,
            excerpt: text(value.excerpt, 280), state: value.state === 'unknown' ? 'unknown' : 'running' };
    };
    const results = (Array.isArray(payload.results) ? payload.results : []).map((v) => row(v, true)).filter(Boolean);
    const running = (Array.isArray(payload.running) ? payload.running : []).map((v) => row(v, false)).filter(Boolean);
    if (!results.length && !running.length) return null;
    return { id: payload.id, token: payload.token, generation: payload.generation, results, running };
}

/** The notice's headline, grouped and honest: a failure is said as a failure, work in progress as such. */
export function noticeHeadline(notice) {
    const done = notice.results.filter((r) => r.outcome === 'completed').length;
    const failed = notice.results.length - done;
    const parts = [];
    if (done) parts.push(`${done} ${done === 1 ? 'tarea terminó' : 'tareas terminaron'}`);
    if (failed) parts.push(`${failed} ${failed === 1 ? 'falló' : 'fallaron'}`);
    if (notice.running.length) parts.push(`${notice.running.length} ${notice.running.length === 1 ? 'sigue' : 'siguen'} en curso`);
    return parts.length ? `Mientras no estabas: ${parts.join(', ')}.` : '';
}

/**
 * Whether this surface must defer the notice now (it is then released, never marked shown):
 * a hidden page, the chat being written in, Iris speaking or listening, or silence.
 */
export function noticeDeferral({ documentLike, status = '', quiet = false, chatBusy = false } = {}) {
    if (documentLike?.visibilityState === 'hidden') return 'hidden';
    if (quiet) return 'quiet';
    if (chatBusy || ['speaking', 'listening', 'thinking', 'tool'].includes(status)) return 'busy';
    const element = documentLike?.activeElement;
    if (element && (element.isContentEditable || element.tagName === 'TEXTAREA'
        || (element.tagName === 'INPUT' && !['button', 'checkbox', 'radio', 'range', 'submit', 'reset', 'color',
            'file', 'hidden'].includes(element.type)))) return 'typing';
    return '';
}
