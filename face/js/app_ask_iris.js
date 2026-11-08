/** «Avisar a Iris»: an app hands Iris a short message, only after a real click by the person.
 *
 * The shell, not the app, shows what will be sent and from which app, unless the person chose
 * to trust that app; even then each message needs its click. Core sends it to the current
 * conversation framed as the app's information, never as instructions (`app_message`).
 */
export const ASK_TEXT_CHARS = 300;
export const ASK_DATA_BYTES = 4096;
export const ASK_ACK_MS = 5000;
const TRUST_KEY = 'irisOs.askIris.trusted.v1';
const ESCAPED_PAIRS = new Set([0x22, 0x5c, 0x08, 0x0c, 0x0a, 0x0d, 0x09]);

const codePoints = (value) => [...value].length;

/** Bytes of a string in Python's `json.dumps(ensure_ascii=True)`: ASCII as is, `\"` and `\n`-style
 * pairs, everything else `\uXXXX` per UTF-16 unit (so `ñ` is 6 and an emoji 12). */
function stringBytes(value) {
    let bytes = 2;
    for (let index = 0; index < value.length; index += 1) {
        const unit = value.charCodeAt(index);
        bytes += ESCAPED_PAIRS.has(unit) ? 2 : unit >= 0x20 && unit <= 0x7e ? 1 : 6;
    }
    return bytes;
}

/**
 * Core's check of the data, in the shell (core/app_messages.py with bounded_json): the same shape
 * limits, and the size Core measures, `json.dumps` with its `", "`/`": "` separators and escaped
 * non-ASCII. The compact UTF-8 of `JSON.stringify` is smaller, and Core would refuse a message the
 * person had already approved.
 */
function coreJsonBytes(value, depth = 0) {
    if (depth > 8) throw new TypeError('data: como mucho 8 niveles.');
    if (value === null || typeof value === 'boolean') return String(value).length;
    if (typeof value === 'number') {
        if (Math.abs(value) > 1e15) throw new TypeError('data: números de hasta 1e15.');
        // Core reads the shell's JSON: without «.» or «e» it is an integer, written back the same.
        // A float below 1e-4 Python writes in scientific notation with two exponent digits
        // (0.000001 → 1e-06, 1.5e-7 → 1.5e-07); both sides keep the shortest digits.
        const wire = JSON.stringify(value);
        if (!/[.e]/.test(wire) || Math.abs(value) >= 1e-4) return wire.length;
        return value.toExponential().replace(/e([+-])(\d)$/, 'e$10$2').length;
    }
    if (typeof value === 'string') {
        if (codePoints(value) > 8192) throw new TypeError('data: textos de hasta 8192 caracteres.');
        return stringBytes(value);
    }
    const entries = Array.isArray(value) ? value.map((item) => [null, item]) : Object.entries(value);
    if (entries.length > (Array.isArray(value) ? 128 : 64)) throw new TypeError('data: hasta 128 elementos por lista y 64 claves por objeto.');
    let bytes = 2 + Math.max(0, entries.length - 1) * 2;
    for (const [key, item] of entries) {
        if (key !== null) {
            if (codePoints(key) > 120) throw new TypeError('data: claves de hasta 120 caracteres.');
            bytes += stringBytes(key) + 2;
        }
        bytes += coreJsonBytes(item, depth + 1);
    }
    return bytes;
}

/** What an app may send: a short line and, optionally, small plain JSON, checked as Core will. */
export function askIrisRequest({ text, data } = {}) {
    const line = typeof text === 'string' ? text.trim() : '';
    if (!line || codePoints(line) > ASK_TEXT_CHARS) throw new TypeError(`text: de 1 a ${ASK_TEXT_CHARS} caracteres.`);
    if (line.startsWith('/')) throw new TypeError('text: no puede empezar por «/».');
    if (/[\u0000-\u0009\u000b-\u001f]/.test(line)) throw new TypeError('text: sin caracteres de control, salvo saltos de línea.');
    if (data === undefined) return { text: line };
    const encoded = JSON.stringify(data); // a cycle or a BigInt throws here
    if (encoded === undefined) throw new TypeError('data: JSON simple.');
    const plain = JSON.parse(encoded);
    if (coreJsonBytes(plain) > ASK_DATA_BYTES) throw new TypeError(`data: JSON de hasta ${ASK_DATA_BYTES} bytes, medidos como Iris.`);
    return { text: line, data: plain };
}

/**
 * Sends approved messages to Core (`app_message`) and waits for its `app_message_ack`. The app
 * hears `{ sent: true }` only when Core took the message, and `onAccepted` puts it in the chat;
 * otherwise `{ sent: false, reason: 'refused', message }` with Core's reason, or `'offline'` when
 * Core did not answer in time. A late acceptance still reaches the chat: Iris got the message.
 */
export function askIrisSender({ send, onAccepted, timeoutMs = ASK_ACK_MS, setTimer = globalThis.setTimeout,
    clearTimer = globalThis.clearTimeout, newId = () => globalThis.crypto.randomUUID().replaceAll('-', '') } = {}) {
    const pending = new Map();
    const answer = (entry, outcome) => {
        if (entry.answered) return;
        entry.answered = true;
        clearTimer(entry.timer);
        entry.resolve(outcome);
    };
    return {
        send(appId, appName, request) {
            return new Promise((resolve) => {
                const id = newId();
                const entry = { resolve, appId, appName, request, answered: false };
                entry.timer = setTimer(() => answer(entry, { sent: false, reason: 'offline' }), timeoutMs);
                pending.set(id, entry);
                const message = { request_id: id, app_id: appId, text: request.text,
                    ...(request.data !== undefined ? { data: request.data } : {}) };
                if (send(message) !== true) {
                    pending.delete(id);
                    answer(entry, { sent: false, reason: 'offline' });
                }
            });
        },
        acknowledge(payload) {
            const entry = pending.get(payload?.request_id);
            if (!entry) return;
            pending.delete(payload.request_id);
            if (payload.accepted === true) {
                onAccepted?.(entry.appId, entry.appName, entry.request);
                answer(entry, { sent: true });
            } else {
                answer(entry, { sent: false, reason: 'refused', message: String(payload.message || 'Iris no aceptó el mensaje.') });
            }
        },
    };
}

/** The dialog: the app and the line in its message, and all the data (as Iris receives it) in its
 * details, which scroll: nothing is sent that the person could not read first (Codex SDK04). */
export function askIrisPreview(appName, request) {
    const data = request.data === undefined ? '' : JSON.stringify(request.data, null, 2);
    return {
        message: `${appName} enviará a Iris, en la conversación actual: «${request.text}»${data ? ', con datos de la app' : ''}. `
            + 'Iris lo recibe como información de la app, no como órdenes.',
        ...(data ? { details: { label: 'Ver los datos de la app', text: data } } : {}),
    };
}

/** Apps the person let send without the dialog, in this browser; revocable in Administrar aplicaciones. */
export const askIrisTrust = {
    list(storage = globalThis.localStorage) {
        try {
            const value = JSON.parse(storage?.getItem(TRUST_KEY) || '[]');
            return Array.isArray(value) ? value.filter((id) => /^[a-z][a-z0-9-]{1,31}$/.test(id)) : [];
        } catch { return []; }
    },
    has(appId, storage) { return this.list(storage).includes(appId); },
    set(appId, trusted, storage = globalThis.localStorage) {
        const next = new Set(this.list(storage));
        if (trusted) next.add(appId); else next.delete(appId);
        try { storage?.setItem(TRUST_KEY, JSON.stringify([...next])); } catch { /* the dialog keeps asking */ }
    },
};
