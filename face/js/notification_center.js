/** Silent local inbox. It never opens a window, starts voice or sends a chat turn. */
import { irisConfirm } from './iris_dialog.js?v=9';
import { normalizeExperience, normalizeDiagnostics, renderVisionTuning, handleVisionTuning } from './vision_tuning_panel.js?v=3';
export function normalizeNotifications(value = {}) {
    value = value && typeof value === 'object' ? value : {};
    const seen = new Set();
    const items = (Array.isArray(value.items) ? value.items : []).filter(item => {
        if (item?.schema !== 'iris.notification/v1' || item.source !== 'vision'
            || !/^[a-f0-9]{32}$/.test(item.id || '') || seen.has(item.id)) return false;
        seen.add(item.id);
        return true;
    }).slice(0, 100).map(item => ({
        id: item.id, title: String(item.title || '').slice(0, 100),
        body: String(item.body || '').slice(0, 600),
        unread: item.unread === true, createdAt: Number(item.created_at) || 0,
        kind: ['low_head_motion', 'returned'].includes(item.kind) ? item.kind : 'long_session',
        status: ['active', 'snoozed', 'dismissed'].includes(item.status) ? item.status : 'active',
        snoozedUntil: Number(item.snoozed_until) || 0,
        feedback: ['useful', 'too_soon', 'not_applicable'].includes(item.feedback) ? item.feedback : null,
        relatedIds: Array.isArray(item.explanation?.related_ids)
            ? [...new Set(item.explanation.related_ids.filter(id => /^[a-f0-9]{32}$/.test(id)))].slice(0, 100) : [],
        evidence: {
            seconds: Math.max(0, Number(item.explanation?.observed_seconds) || 0),
            threshold: Math.max(0, Number(item.explanation?.threshold_seconds) || 0),
            confidence: Math.max(0, Math.min(1, Number(item.explanation?.confidence) || 0)),
            basis: item.kind === 'returned'
                ? 'Ausencia continua y avisos pendientes sin resumir; no reconocimiento de personas'
                : 'Movimiento de cabeza; no postura ni salud',
        },
    }));
    return {
        items, revision: Math.max(0, Number(value.revision) || 0),
        unread: items.filter(item => item.unread && item.status === 'active').length,
        serverNow: Number(value.server_now) || Date.now() / 1000,
        receivedAt: Date.now() / 1000,
        nextChangeAt: Number(value.next_change_at) || 0,
        preferences: {
            experience: normalizeExperience(value.preferences?.experience),
            returnSummary: value.preferences?.return_summary === true,
            mutedUntil: Number(value.preferences?.muted_until) || 0,
            disabledKinds: [...new Set((Array.isArray(value.preferences?.disabled_kinds)
                ? value.preferences.disabled_kinds : []).filter(kind =>
                ['long_session', 'low_head_motion'].includes(kind)))],
        },
        storageAvailable: value.storage_available === true,
        retentionDays: Math.max(0, Number(value.retention_days) || 0),
        availability: value.availability || { available: false },
        vision: value.vision || { enabled: false },
        diagnostics: normalizeDiagnostics(value.diagnostics),
    };
}
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const clock = seconds => {
    const date = new Date(seconds * 1000);
    return Number.isFinite(date.getTime()) ? date.toLocaleString([], {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    }) : 'Fecha no disponible';
};

export class NotificationCenterApp {
    constructor({ root, isVisible = () => false, onSummary = () => {}, onPrepareConversation = null } = {}) {
        this.root = root;
        this.isVisible = isVisible;
        this.onSummary = onSummary;
        this.onPrepareConversation = onPrepareConversation;
        this.wakeTimer = null;
        this.state = normalizeNotifications();
        this.loaded = false;
        this.busy = false;
        this.error = '';
        this.notice = '';
        this.diagnosticFilter = {};
        this.filter = 'unread';
        this.relatedIds = [];
        this.reloadPending = false;
        this.dirty = true;
        this.root?.addEventListener('click', event => this._onClick(event));
        this.render();
    }

    changed(summary = {}) {
        this.dirty = true;
        if (Number.isInteger(summary.unread) && summary.unread >= 0) {
            this.onSummary(summary.unread);
        }
        if (this.busy) this.reloadPending = true;
        else if (this.isVisible()) void this.load(true);
    }

    async load(force = false) {
        if (!force && this.loaded && !this.dirty) { this.render(); this._scheduleRefresh(); return; }
        return this._request('');
    }

    async _request(path, payload) {
        if (this.busy) { this.reloadPending = true; return; }
        this.busy = true;
        clearTimeout(this.wakeTimer);
        this.wakeTimer = null;
        let received = false;
        this.error = '';
        this.render();
        try {
            const response = await fetch('/api/notifications' + path, {
                cache: 'no-store',
                ...(payload ? {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                } : {}),
            });
            const body = await response.json();
            if (!response.ok) throw Error(typeof body.detail === 'string' ? body.detail : 'No se pudo guardar el cambio.');
            if (path === '/conversation') {
                if (!this.onPrepareConversation || await this.onPrepareConversation(body.draft) !== true) {
                    throw Error('No se pudo preparar el borrador. No se envió ningún mensaje.');
                }
                return;
            }
            this.state = normalizeNotifications(body);
            this.notice = body.calibration_requested
                ? 'Solicitud enviada. Pulsa Actualizar diagnóstico para comprobar el resultado; enviar no confirma la calibración.'
                : '';
            received = true;
            this.loaded = true;
            this.dirty = false;
            this.onSummary(this.state.unread);
        } catch (error) {
            this.error = error?.message || 'Core no responde. Puedes reintentar.';
        } finally {
            this.busy = false;
            this.render();
            const refresh = this.reloadPending;
            this.reloadPending = false;
            if (refresh && this.isVisible()) void this.load(true);
            else if (received) this._scheduleRefresh();
        }
    }

    _serverTime() {
        return this.state.serverNow + Math.max(0, Date.now() / 1000 - this.state.receivedAt);
    }

    _scheduleRefresh() {
        clearTimeout(this.wakeTimer);
        this.wakeTimer = null;
        if (!this.isVisible()) return;
        const deadlines = [this.state.nextChangeAt,
            this.state.availability.available ? this.state.availability.expires_at : 0]
            .filter(at => Number.isFinite(at) && at > this.state.serverNow);
        if (!deadlines.length) return;
        const ms = Math.min(2147483647, Math.max(100, (Math.min(...deadlines) - this._serverTime()) * 1000));
        this.wakeTimer = setTimeout(() => {
            this.wakeTimer = null;
            this.dirty = true;
            // One deadline, not polling: hidden windows never make this request.
            if (this.isVisible()) void this.load(true);
        }, ms);
    }

    _mutation(path, fields) {
        return this._request(path, { ...fields, confirm: true, expected_revision: this.state.revision });
    }

    observeVision(payload = {}) {
        if (!this.isVisible()) return;
        const available = Boolean(this.state.availability.available
            && this.state.availability.expires_at > this._serverTime());
        if (available !== this.renderedAvailability) this.render();
        const target = this.root?.querySelector('[data-notify-diagnostic]');
        if (target && payload.v2?.decision?.explanation) {
            target.textContent = payload.v2.decision.explanation;
        }
    }

    async _onClick(event) {
        const button = event.target?.closest?.('button');
        if (!button || button.disabled || this.busy) return;
        const action = button.dataset.notifyAction;
        if (action && await handleVisionTuning(action, button, this)) return;
        if (action === 'feedback') {
            const vote = button.dataset.feedback;
            if (await irisConfirm(vote === 'too_soon'
                ? { title: 'Espaciar avisos', icon: 'bolt', message: 'Este tipo de aviso esperará 15 minutos más. El intervalo por tipo tiene un máximo de 180 minutos.', question: '¿Espaciarlo?', confirmLabel: 'Espaciar' }
                : vote === 'not_applicable'
                    ? { title: 'Desactivar este aviso', icon: 'warning', message: 'Puedes habilitarlo de nuevo en Frecuencia y tipos.', question: '¿Desactivar este tipo de aviso?', confirmLabel: 'Desactivar' }
                    : { title: 'Aviso útil', icon: 'check', message: 'Se guardará que este aviso te sirvió. No cambia personalidad, permisos ni memoria.', question: '¿Guardarlo?', confirmLabel: 'Guardar' })) {
                return this._mutation('/feedback', { id: button.dataset.id, feedback: vote });
            }
            return;
        }
        if (action === 'refresh') return this.load(true);
        if (action === 'related') {
            const item = this.state.items.find(row => row.id === button.dataset.id);
            if (!item || item.kind !== 'returned') return;
            this.relatedIds = item.relatedIds;
            this.filter = 'related';
            this.render();
            return;
        }
        if (action === 'filter') {
            this.filter = ['all', 'snoozed', 'dismissed'].includes(button.dataset.filter) ? button.dataset.filter : 'unread';
            this.render();
        } else if (action === 'read') {
            const item = this.state.items.find(row => row.id === button.dataset.id);
            if (item) return this._request('/read', { id: item.id, read: item.unread });
        } else if (action === 'dismiss' || action === 'restore') {
            return this._mutation('/action', { id: button.dataset.id, action });
        } else if (action === 'snooze') {
            const minutes = Number(button.closest('article')?.querySelector('[data-notify-delay]')?.value || 30);
            return this._mutation('/action', { id: button.dataset.id, action, minutes });
        } else if (action === 'mute' || action === 'unmute') {
            return this._mutation('/preferences', { action, ...(action === 'mute' ? { minutes: 30 } : {}) });
        } else if (action === 'enable_return' || action === 'disable_return') {
            if (action === 'disable_return' || await irisConfirm({
                title: 'Resumen al regresar', icon: 'info',
                message: 'Si quedan avisos de Vision sin resumir, verás un resumen silencioso al regresar. Respeta disponibilidad y presupuesto, y puede acompañarse de un saludo en un solo avatar. No activa cámara, voz ni Hermes.',
                question: '¿Habilitarlo?', confirmLabel: 'Habilitar',
            })) {
                return this._mutation('/preferences', { action });
            }
        } else if (action === 'disable_kind' || action === 'enable_kind') {
            if (action === 'enable_kind' || await irisConfirm({
                title: 'Desactivar sugerencia', icon: 'warning',
                message: 'Puedes volver a habilitarla en los controles de esta bandeja. No se borra tu historial.',
                question: '¿Desactivar este tipo de sugerencia?', confirmLabel: 'Desactivar',
            })) {
                return this._mutation('/preferences', { action, kind: button.dataset.kind });
            }
        } else if (action === 'conversation') {
            if (await irisConfirm({
                title: 'Borrador para el chat', icon: 'notes',
                message: 'Se preparará un borrador editable en el chat, sin datos de la cámara. Solo se enviará a IRIS/Hermes cuando pulses Enviar.',
                question: '¿Prepararlo?', confirmLabel: 'Preparar borrador',
            })) {
                return this._request('/conversation', { id: button.dataset.id, confirm: true });
            }
        } else if (action === 'clear') {
            if (await irisConfirm({
                title: 'Vaciar bandeja de Vision', icon: 'trash', tone: 'danger',
                message: 'Se conservan los límites para no repetir avisos.',
                question: '¿Vaciar esta bandeja?', confirmLabel: 'Vaciar', confirmIcon: 'trash',
            })) {
                return this._request('/clear', { confirm: true });
            }
        } else if (action === 'availability') {
            const active = this.state.availability.available
                && this.state.availability.expires_at > this._serverTime();
            if (active || await irisConfirm({
                title: 'Disponible 15 minutos', icon: 'info',
                message: 'Declaras que estás disponible y no estás escribiendo, hablando, en Focus, reunión, juego o presentación. Las señales de actividad detectadas seguirán bloqueando los avisos. Esto no activa la cámara ni la voz.',
                question: '¿Deseas continuar?', confirmLabel: 'Estoy disponible',
            })) {
                return this._request('/availability', { available: !active, confirm: true });
            }
        }
    }

    render() {
        if (!this.root) return;
        const openDetails = new Set([...this.root.querySelectorAll('details[open]')].map(el => el.dataset.detailKey || el.dataset.id));
        const delays = new Map([...this.root.querySelectorAll('[data-notify-delay]')].map(el => [el.dataset.id, el.value]));
        const active = this.root.ownerDocument?.activeElement;
        const focusKey = el => [el.tagName, el.dataset.notifyAction || '', el.dataset.id || '',
            el.dataset.tuneField || '', el.dataset.diagnosticFilter || '', el.dataset.feedback || '',
            el.dataset.filter || '', el.dataset.kind || '', el.closest('article')?.dataset.id || '',
            el.tagName === 'SUMMARY' ? (el.parentElement?.dataset.detailKey || el.parentElement?.dataset.id || '') : ''].join('|');
        const focus = active && this.root.contains?.(active) ? focusKey(active) : null;
        const { items, unread, storageAvailable, availability, vision } = this.state;
        const available = Boolean(availability.available && availability.expires_at > this._serverTime());
        this.renderedAvailability = available;
        const shown = items.filter(item => this.filter === 'all'
            || (this.filter === 'related' ? this.relatedIds.includes(item.id)
                : this.filter === 'unread' ? item.unread && item.status === 'active' : item.status === this.filter));
        const muted = this.state.preferences.mutedUntil > this._serverTime();
        const disabled = this.busy ? 'disabled' : '';
        this.root.innerHTML = `
            <section class="notification-center">
                <header class="notify-hero">
                    <div><span class="notify-kicker">IRIS OS / SEÑALES LOCALES</span>
                        <h2>Notificaciones</h2><p>Lo importante, cuando tú decidas.</p></div>
                    <div class="notify-counter"><strong>${this.loaded ? String(unread).padStart(2, '0') : '—'}</strong><span>SIN LEER</span></div>
                </header>
                <div class="notify-status" role="status">${escape(this.error || (this.busy
                    ? 'Sincronizando bandeja local…' : !this.loaded ? 'Abre la bandeja para sincronizar.'
                    : !storageAvailable ? 'Estado local no verificable. Se bloquean nuevas entregas; no se sobrescriben datos.'
                    : this.notice || 'Entrega silenciosa · sin sonido, sin abrir chat ni robar foco.'))}</div>
                <div class="notify-layout">
                    <main class="notify-feed">
                        <nav aria-label="Filtrar notificaciones">
                            ${this.filter === 'related' ? '<button data-notify-action="filter" data-filter="all" aria-pressed="true">Avisos del resumen · Ver todos</button>' : ''}
                            <button data-notify-action="filter" data-filter="unread" aria-pressed="${this.filter === 'unread'}">Sin leer · ${unread}</button>
                            <button data-notify-action="filter" data-filter="all" aria-pressed="${this.filter === 'all'}">Todas · ${items.length}</button>
                            <button data-notify-action="filter" data-filter="snoozed" aria-pressed="${this.filter === 'snoozed'}">Pospuestas</button>
                            <button data-notify-action="filter" data-filter="dismissed" aria-pressed="${this.filter === 'dismissed'}">Descartadas</button>
                            <button data-notify-action="refresh" ${disabled}>Actualizar</button>
                        </nav>
                        <div class="notify-list">${shown.map(item => `
                            <article class="notify-item" data-id="${item.id}" data-unread="${item.unread && item.status === 'active'}" data-status="${item.status}">
                                <div class="notify-item-meta"><span>VISION · LOCAL</span><time>${escape(clock(item.createdAt))}</time></div>
                                <h3>${escape(item.title)}</h3><p>${escape(item.body)}</p>
                                ${item.kind === 'returned' ? `<button data-notify-action="related" data-id="${item.id}">Ver avisos relacionados</button>` : ''}
                                ${item.status === 'snoozed' ? `<p class="notify-state">Pospuesta hasta ${escape(clock(item.snoozedUntil))} · vuelve a esta bandeja, sin crear otra entrega.</p>` : ''}
                                ${item.status === 'dismissed' ? '<p class="notify-state">Descartada por ti. Puedes restaurarla sin reiniciar el presupuesto.</p>' : ''}
                                <details data-id="${item.id}" ${openDetails.has(item.id) ? 'open' : ''}>
                                    <summary>¿Por qué apareció?</summary>
                                    <p>${item.kind === 'returned'
                                        ? 'Ausencia observada: ' + Math.floor(item.evidence.seconds) + ' s · umbral: ' + Math.floor(item.evidence.threshold) + ' s'
                                        : 'Presencia observada: ' + Math.floor(item.evidence.seconds / 60) + ' min · umbral: ' + Math.floor(item.evidence.threshold / 60) + ' min'} · confianza de señal: ${Math.round(item.evidence.confidence * 100)}%.</p>
                                    <p>${escape(item.evidence.basis)}. Se guardó en la bandeja; no implica que ya lo hayas visto.</p>
                                </details>
                                <div class="notify-feedback" aria-label="Valorar este aviso">
                                    ${item.feedback ? `<small>Tu feedback: ${{ useful: 'Útil', too_soon: 'Muy pronto · intervalo ampliado', not_applicable: 'No aplica · tipo desactivado' }[item.feedback]}</small>`
                                    : Object.entries({ useful: 'Útil', too_soon: 'Muy pronto', not_applicable: 'No aplica' }).map(([vote, label]) => `<button data-notify-action="feedback" data-feedback="${vote}" data-id="${item.id}" ${disabled}>${label}</button>`).join('')}
                                </div>
                                ${item.status === 'active' ? `
                                    <div class="notify-actions">
                                        <button class="notify-primary" data-notify-action="conversation" data-id="${item.id}" ${this.busy || !this.onPrepareConversation ? 'disabled' : ''}>Preparar conversación</button>
                                        <button data-notify-action="dismiss" data-id="${item.id}" ${disabled}>Ahora no</button>
                                        <button data-notify-action="read" data-id="${item.id}" ${disabled}>${item.unread ? 'Marcar leído' : 'Marcar sin leer'}</button>
                                    </div>
                                    <details data-detail-key="actions-${item.id}" ${openDetails.has('actions-' + item.id) ? 'open' : ''}>
                                        <summary>Más opciones</summary>
                                        <div class="notify-actions">
                                            <select aria-label="Cuánto posponer este aviso" data-notify-delay data-id="${item.id}" ${disabled}>
                                                ${[10, 30, 60].map(minutes => `<option value="${minutes}" ${Number(delays.get(item.id) || 30) === minutes ? 'selected' : ''}>${minutes} min</option>`).join('')}
                                            </select>
                                            <button data-notify-action="snooze" data-id="${item.id}" ${disabled}>Posponer</button>
                                            <button data-notify-action="${item.kind === 'returned' ? 'disable_return' : 'disable_kind'}" data-kind="${item.kind}" ${this.busy || this.state.preferences.disabledKinds.includes(item.kind) ? 'disabled' : ''}>No sugerir este tipo</button>
                                        </div>
                                    </details>` : `<button data-notify-action="restore" data-id="${item.id}" ${disabled}>${item.status === 'snoozed' ? 'Mostrar ahora' : 'Restaurar aviso'}</button>`}
                            </article>`).join('') || `<div class="notify-empty"><span>○</span><h3>${this.loaded ? 'Todo en calma' : 'Bandeja local'}</h3><p>${this.loaded ? 'No hay avisos en este filtro. No hace falta llenar el silencio.' : 'Esperando la primera sincronización con Core.'}</p></div>`}
                        </div>
                    </main>
                    <aside class="notify-controls">
                        ${renderVisionTuning(this.state, { busy: this.busy || !this.loaded || !storageAvailable,
                            open: openDetails.has('vision-tuning'), diagnosticFilter: this.diagnosticFilter })}
                        <section><span class="notify-kicker">TU DISPONIBILIDAD</span>
                            <h3>${available ? 'Disponible temporalmente' : 'Sin disponibilidad declarada'}</h3>
                            <p>${available ? 'Caduca: ' + escape(clock(availability.expires_at)) : 'IRIS no asume que estás libre. Puedes declararlo por 15 minutos.'}</p>
                            <button data-notify-action="availability" ${this.busy || !this.loaded || (!available && (!vision.enabled || vision.paused || !storageAvailable || !this.state.retentionDays)) ? 'disabled' : ''}>${available ? 'Terminar disponibilidad' : 'Estoy disponible · 15 min'}</button>
                            <small>No activa Vision, cámara ni micrófono. La actividad detectada tiene prioridad.</small>
                        </section>
                        <section><span class="notify-kicker">VISION ASSISTANT</span>
                            <h3>Frecuencia y tipos</h3>
                            <p>${muted ? 'Nuevas sugerencias silenciadas hasta ' + escape(clock(this.state.preferences.mutedUntil)) + '.' : 'Las nuevas sugerencias respetan tu presupuesto y disponibilidad.'}</p>
                            <button data-notify-action="${muted ? 'unmute' : 'mute'}" ${this.busy || !this.loaded ? 'disabled' : ''}>${muted ? 'Quitar silencio temporal' : 'Silenciar nuevas sugerencias · 30 min'}</button>
                            ${this.state.preferences.disabledKinds.map(kind => `<button data-notify-action="enable_kind" data-kind="${kind}" ${disabled}>Habilitar: ${kind === 'long_session' ? 'sesión larga' : 'poco movimiento de cabeza'}</button>`).join('')}
                            <small>Silenciar no pausa la cámara ni borra avisos. Los pospuestos vuelven aquí al vencer el plazo; no crean tareas de Hermes.</small>
                        </section>
                        <section><span class="notify-kicker">AL REGRESAR</span>
                            <h3>Retoma a tu ritmo</h3>
                            <p>Resumen de avisos de Vision pendientes, solo tras una ausencia observada de al menos 30 segundos. Sin novedades, no hay saludo.</p>
                            <button data-notify-action="${this.state.preferences.returnSummary ? 'disable_return' : 'enable_return'}" aria-pressed="${this.state.preferences.returnSummary}" ${this.busy || !this.loaded ? 'disabled' : ''}>${this.state.preferences.returnSummary ? 'Desactivar resumen de regreso' : 'Habilitar resumen de regreso'}</button>
                            <small>Apagado por defecto. Face y Companion comparten presupuesto; solo un avatar puede saludar. No activa sensores ni cambia tus permisos.</small>
                        </section>
                        <section><span class="notify-kicker">OBSERVACIÓN LOCAL</span>
                            <h3>${vision.enabled ? vision.paused ? 'Pausado' : 'Habilitado' : 'Apagado'}</h3>
                            <p data-notify-diagnostic>${escape(vision.decision?.explanation || 'Consulta Vision en Configuración para activar la observación local.')}</p>
                            <small>Sesión larga, poco movimiento de cabeza y resumen opcional de regreso. Rutinas, runs y Gateway se conectarán después.</small>
                        </section>
                        <section><span class="notify-kicker">SOLO EN TU EQUIPO</span>
                            <p>Hasta 100 avisos · retención: ${this.state.retentionDays} días. Sin fotos, audio ni texto de conversaciones.</p>
                            ${this.loaded && !this.state.retentionDays ? '<small>La retención está apagada. No se guardarán nuevos avisos.</small>' : ''}
                            <button data-notify-action="clear" ${this.busy || !items.length ? 'disabled' : ''}>Vaciar bandeja</button>
                        </section>
                    </aside>
                </div>
            </section>`;
        if (focus) {
            const target = [...this.root.querySelectorAll('button, select, summary')].find(el => focusKey(el) === focus);
            (target || this.root.querySelector('nav button[aria-pressed=true]'))?.focus?.({ preventScroll: true });
        }
    }
}
