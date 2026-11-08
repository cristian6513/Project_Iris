/* IRIS OS dialogs: confirmations, notices and short forms in Iris' own style.
 *
 * They replace the browser's confirm/alert/prompt, which the embedded WebView
 * shows as «Una página incrustada en 127.0.0.1 dice…». One dialog at a time
 * (later ones wait their turn); Escape cancels, Enter confirms, focus stays
 * inside and returns to where it was. Colours follow the active theme through CSS
 * (iris_dialog.css), so a theme change shows at once, even with a dialog open.
 *
 * Without a DOM (unit tests) they fall back to the browser functions, so code
 * that stubs window.confirm keeps working.
 */

import { osIconSvg } from './os_icons.js?v=2';

// The same line icons as the IRIS OS desktop (the badge is the «IRIS OS» mark).
function icon(name, className = 'iris-dialog-svg') {
    return osIconSvg(name, { className });
}

const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const hasDom = () => typeof document !== 'undefined' && Boolean(document.body)
    && typeof document.createElement === 'function' && typeof HTMLElement !== 'undefined';

let queue = Promise.resolve();
let sequence = 0;

function plainText(options) {
    return [options.message, options.question].filter(Boolean).join(' ');
}

function buildDialog(kind, options, id) {
    const tone = options.tone === 'danger' ? 'danger' : 'default';
    const versions = options.versions;
    const details = options.details && (options.details.text || options.details.items?.length) ? options.details : null;
    const fields = kind === 'form' ? (options.fields || []) : [];
    const backdrop = document.createElement('div');
    backdrop.className = 'iris-dialog-backdrop';
    const describedBy = [options.message || options.question ? `${id}-message` : '', versions ? `${id}-versions` : '']
        .filter(Boolean).join(' ');
    backdrop.innerHTML = `
        <section class="iris-dialog" data-tone="${tone}" role="${kind === 'form' ? 'dialog' : 'alertdialog'}"
                 aria-modal="true" aria-labelledby="${id}-title"${describedBy ? ` aria-describedby="${describedBy}"` : ''}>
            <header class="iris-dialog-head">
                <span class="iris-dialog-badge">${icon(options.icon || (tone === 'danger' ? 'warning' : 'iris'))}</span>
                <h2 class="iris-dialog-title" id="${id}-title">${escapeHtml(options.title || 'IRIS OS')}</h2>
                <button class="iris-dialog-close" type="button" data-dialog="cancel" aria-label="Cerrar">${icon('close')}</button>
            </header>
            <div class="iris-dialog-body">
                ${options.message || options.question ? `<p class="iris-dialog-message" id="${id}-message">
                    ${escapeHtml(options.message || '')}
                    ${options.question ? `<strong class="iris-dialog-question">${escapeHtml(options.question)}</strong>` : ''}
                </p>` : ''}
                ${versions ? `<div class="iris-dialog-versions" id="${id}-versions">
                    <div class="iris-dialog-version" data-side="from">
                        <span class="iris-dialog-version-icon">${icon(versions.fromIcon || 'gear')}</span>
                        <span><small>${escapeHtml(versions.fromLabel || 'Versión actual:')}</small>
                            <b>${escapeHtml(versions.from || '—')}</b></span>
                    </div>
                    <span class="iris-dialog-arrow">${icon('chevron')}</span>
                    <div class="iris-dialog-version" data-side="to">
                        <span class="iris-dialog-version-icon">${icon(versions.toIcon || 'download')}</span>
                        <span><small>${escapeHtml(versions.toLabel || 'Destino:')}</small>
                            <b>${escapeHtml(versions.to || '—')}</b></span>
                    </div>
                </div>` : ''}
                ${details ? `<div class="iris-dialog-details" id="${id}-details" hidden>
                    ${details.text ? `<p>${escapeHtml(details.text)}</p>` : ''}
                    ${details.items?.length ? `<ul>${details.items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : ''}
                </div>` : ''}
                ${fields.length ? `<form class="iris-dialog-form" novalidate>${fields.map((field, index) => `
                    <label class="iris-dialog-field">
                        <span>${escapeHtml(field.label || field.name)}</span>
                        ${field.multiline
                            ? `<textarea name="${escapeHtml(field.name)}" rows="${Number(field.rows) || 4}" placeholder="${escapeHtml(field.placeholder || '')}"${field.required ? ' required' : ''}>${escapeHtml(field.value || '')}</textarea>`
                            : `<input name="${escapeHtml(field.name)}" type="text" value="${escapeHtml(field.value || '')}" placeholder="${escapeHtml(field.placeholder || '')}" autocomplete="off"${field.required ? ' required' : ''}>`}
                        ${field.hint ? `<small>${escapeHtml(field.hint)}</small>` : ''}
                    </label>`).join('')}
                    <p class="iris-dialog-error" role="alert" hidden></p>
                </form>` : ''}
                ${kind === 'consent' && options.rememberLabel ? `<label class="iris-dialog-remember">
                    <input type="checkbox" data-dialog-remember><span>${escapeHtml(options.rememberLabel)}</span>
                </label>` : ''}
                ${kind === 'image' ? `<img class="iris-dialog-preview" src="${escapeHtml(options.image)}" alt="Captura que se enviará al modelo indicado">` : ''}
            </div>
            <footer class="iris-dialog-actions">
                ${details ? `<button class="iris-dialog-btn" type="button" data-dialog="details" aria-expanded="false" aria-controls="${id}-details">${icon('notes')}<span>${escapeHtml(details.label || 'Ver cambios')}</span></button>` : ''}
                ${kind === 'alert' ? '' : `<button class="iris-dialog-btn" type="button" data-dialog="cancel">${escapeHtml(options.cancelLabel || 'Cancelar')}</button>`}
                ${kind === 'choice' ? (options.choices || []).map((choice, index) => `<button class="iris-dialog-btn ${index === 0 ? 'primary' : ''}" type="button" data-dialog="choice" data-choice="${escapeHtml(choice.value)}">${escapeHtml(choice.label)}</button>`).join('')
                    : `<button class="iris-dialog-btn primary" type="button" data-dialog="confirm">${options.confirmIcon ? icon(options.confirmIcon) : ''}<span>${escapeHtml(options.confirmLabel || (kind === 'alert' ? 'Entendido' : 'Aceptar'))}</span></button>`}
            </footer>
        </section>`;
    return backdrop;
}

function show(kind, options) {
    const run = () => new Promise((resolve) => {
        const cancelledValue = kind === 'consent' ? { confirmed: false, remember: false }
            : kind === 'form' || kind === 'choice' ? null : kind === 'alert' ? undefined : false;
        if (options.signal?.aborted) { resolve(cancelledValue); return; }
        const id = `iris-dialog-${++sequence}`;
        const backdrop = buildDialog(kind, options, id);
        const dialog = backdrop.querySelector('.iris-dialog');
        const previous = document.activeElement;
        const form = backdrop.querySelector('.iris-dialog-form');
        const error = backdrop.querySelector('.iris-dialog-error');
        let settled = false;

        const values = () => Object.fromEntries((options.fields || []).map((field) => {
            const input = form.elements.namedItem(field.name);
            return [field.name, String(input?.value ?? '').trim()];
        }));
        const finish = (answer) => {
            if (settled) return;
            settled = true;
            options.signal?.removeEventListener('abort', onAbort);
            document.removeEventListener('keydown', onKey, true);
            backdrop.classList.add('closing');
            const remove = () => backdrop.remove();
            if (globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches) remove();
            else setTimeout(remove, 140);
            if (previous && typeof previous.focus === 'function' && document.contains(previous)) previous.focus();
            resolve(answer);
        };
        const confirm = () => {
            if (kind === 'consent') {
                finish({ confirmed: true, remember: backdrop.querySelector('[data-dialog-remember]')?.checked === true });
                return;
            }
            if (kind === 'choice') { finish(options.choices?.[0]?.value ?? null); return; }
            if (kind === 'form') {
                const answer = values();
                const missing = (options.fields || []).find((field) => field.required && !answer[field.name]);
                if (missing) {
                    error.hidden = false;
                    error.textContent = `Completa «${missing.label || missing.name}».`;
                    form.elements.namedItem(missing.name)?.focus();
                    return;
                }
                finish(answer);
                return;
            }
            finish(kind === 'alert' ? undefined : true);
        };
        const cancel = () => finish(cancelledValue);
        const onAbort = () => finish(cancelledValue);
        const focusables = () => [...dialog.querySelectorAll('button, input, textarea, select, [tabindex]:not([tabindex="-1"])')]
            .filter((node) => !node.disabled && !node.closest('[hidden]'));
        function onKey(event) {
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                cancel();
            } else if (event.key === 'Enter' && event.target?.tagName !== 'TEXTAREA'
                       && !event.target?.closest?.('[data-dialog="cancel"], [data-dialog="details"], [data-dialog="choice"]')) {
                event.preventDefault();
                confirm();
            } else if (event.key === 'Tab') {
                const items = focusables();
                if (!items.length) return;
                const first = items[0];
                const last = items[items.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                    event.preventDefault();
                    last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault();
                    first.focus();
                } else if (!dialog.contains(document.activeElement)) {
                    event.preventDefault();
                    first.focus();
                }
            }
        }

        backdrop.addEventListener('click', (event) => {
            const action = event.target.closest?.('[data-dialog]')?.dataset.dialog;
            if (action === 'confirm') confirm();
            else if (action === 'choice') finish(event.target.closest('[data-choice]')?.dataset.choice ?? null);
            else if (action === 'cancel') cancel();
            else if (action === 'details') {
                const panel = backdrop.querySelector('.iris-dialog-details');
                const button = event.target.closest('[data-dialog="details"]');
                panel.hidden = !panel.hidden;
                button.setAttribute('aria-expanded', String(!panel.hidden));
            } else if (event.target === backdrop && options.dismissible !== false) cancel();
        });
        form?.addEventListener('submit', (event) => { event.preventDefault(); confirm(); });
        document.addEventListener('keydown', onKey, true);
        options.signal?.addEventListener('abort', onAbort, { once: true });
        document.body.appendChild(backdrop);
        void backdrop.offsetWidth;  // start the fade from the closed state, even without animation frames
        backdrop.classList.add('open');
        const start = kind === 'form'
            ? form.querySelector('input, textarea')
            : backdrop.querySelector(options.tone === 'danger' ? '[data-dialog="cancel"]:not(.iris-dialog-close)' : kind === 'choice' ? '[data-dialog="choice"]' : '[data-dialog="confirm"]');
        (start || dialog.querySelector('[data-dialog="confirm"]'))?.focus();
    });
    const turn = queue.then(run, run);
    queue = turn.catch(() => {});
    return turn;
}

/** Ask a yes/no question; resolves true only when the user confirms. */
export function irisConfirm(options = {}) {
    if (options.signal?.aborted) return Promise.resolve(false);
    if (!hasDom()) return Promise.resolve(globalThis.window?.confirm?.(plainText(options)) === true);
    return show('confirm', options);
}

/** A local image review: remote URLs and DOM-less approval are deliberately absent. */
export function irisImageConfirm(options = {}) {
    if (!hasDom() || options.signal?.aborted || typeof options.image !== 'string'
        || options.image.length > 6 * 1024 * 1024
        || !/^data:image\/(?:jpeg|png);base64,[A-Za-z0-9+/]+=*$/.test(options.image)) return Promise.resolve(false);
    return show('image', options);
}

/** Confirmation with an optional, initially unchecked remembered destination. */
export function irisConsent(options = {}) {
    if (options.signal?.aborted) return Promise.resolve({ confirmed: false, remember: false });
    if (!hasDom()) return Promise.resolve({
        confirmed: globalThis.window?.confirm?.(plainText(options)) === true, remember: false,
    });
    return show('consent', options);
}

/** Explicit choices; the no-DOM fallback can approve only the first, one-time choice. */
export function irisChoice(options = {}) {
    if (options.signal?.aborted) return Promise.resolve(null);
    if (!hasDom()) return Promise.resolve(globalThis.window?.confirm?.(plainText(options)) === true
        ? options.choices?.[0]?.value ?? null : null);
    return show('choice', options);
}

/** Tell the user something; resolves when it is acknowledged. */
export function irisAlert(options = {}) {
    if (!hasDom()) {
        globalThis.window?.alert?.(plainText(options));
        return Promise.resolve();
    }
    return show('alert', options);
}

/** Ask for a few text fields; resolves {name: value} or null when cancelled. */
export function irisForm(options = {}) {
    if (!hasDom()) {
        const answer = {};
        for (const field of options.fields || []) {
            const value = globalThis.window?.prompt?.(field.label || field.name, field.value || '');
            if (value === null || value === undefined) return Promise.resolve(null);
            answer[field.name] = String(value).trim();
        }
        return Promise.resolve(answer);
    }
    return show('form', options);
}

/** The «Actualización del sistema» dialog: current version → target, with what changes. */
export function systemUpdateDialog(snapshot = {}, rollback = false) {
    const current = (snapshot?.items || []).find((item) => item?.id === 'iris-os')?.version || '';
    const target = rollback ? snapshot?.previous : snapshot?.available;
    const version = target?.version || (rollback ? 'anterior' : 'nueva');
    const items = [];
    if (target?.hermes_version) items.push(`Iris ${target.version} incluye Hermes Agent ${target.hermes_version}.`);
    if (!rollback && Number(target?.size) > 0) items.push(`Descarga de ${(Number(target.size) / 1048576).toFixed(0)} MB.`);
    items.push('Tu perfil, tus conversaciones y tu configuración se conservan.');
    items.push('Iris se reinicia: termina lo que estés haciendo antes de continuar.');
    return {
        title: 'Actualización del sistema',
        icon: 'iris',
        message: `IRIS se cerrará y volverá a abrirse con la versión ${version}.`,
        question: '¿Deseas continuar?',
        versions: current || target?.version ? { from: current, to: target?.version } : null,
        details: {
            label: 'Ver cambios',
            text: rollback ? `Vuelves a la versión ${version} que tenías instalada.` : (target?.notes || 'Esta versión no trae notas.'),
            items,
        },
        confirmLabel: rollback ? `Volver a ${version}` : 'Confirmar actualización',
        confirmIcon: 'update',
    };
}

globalThis.IrisDialog = Object.freeze({ confirm: irisConfirm, alert: irisAlert, form: irisForm });
