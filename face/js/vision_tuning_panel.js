import { irisConfirm } from './iris_dialog.js?v=9';
/** Vision V2 tuning stays in the local inbox; opening these controls starts no sensor. */
const esc = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const profiles = { custom: 'Personalizado', silent: 'Silencioso', company: 'Compañía', wellbeing: 'Bienestar', work: 'Trabajo' };
const kinds = { long_session: 'Sesión larga', low_head_motion: 'Poco movimiento', returned: 'Regreso', none: 'Sin candidato' };
const outcomes = { suppressed: 'Evitada', delivered: 'Entregada', eligible: 'Elegible', observe: 'Observando', deferred: 'Diferida' };
const calibration = { idle: 'Sin calibrar', collecting: 'Midiendo ruido', ready: 'Calibrada para esta captura',
    insufficient: 'Señal insuficiente: vuelve a intentar', noisy: 'Hubo demasiado movimiento: no se aplicó ajuste' };
export function normalizeExperience(raw = {}) {
    return { profile: Object.hasOwn(profiles, raw?.profile) ? raw.profile : 'custom',
        physical_suggestions: raw?.physical_suggestions !== false, greetings: raw?.greetings !== false,
        performance: ['economy', 'responsive'].includes(raw?.performance) ? raw.performance : 'balanced',
        feedback_delays: Object.fromEntries(Object.keys(kinds).filter(k => k !== 'none').map(k =>
            [k, Math.max(0, Math.min(180, Number(raw?.feedback_delays?.[k]) || 0))])) };
}
export function normalizeDiagnostics(raw = {}) {
    const rows = (Array.isArray(raw?.rows) ? raw.rows : []).filter(row =>
        Object.hasOwn(kinds, row?.kind) && Object.hasOwn(outcomes, row?.outcome)).slice(0, 100).map(row => ({
            at: Number(row.at) || 0, kind: row.kind, outcome: row.outcome,
            reason: String(row.reason || '').slice(0, 80), explanation: String(row.explanation || '').slice(0, 300),
            surface: ['face', 'desktop_companion', 'legacy_face'].includes(row.surface) ? row.surface : 'unknown',
        }));
    const sensor = raw?.sensor && raw.sensor_status === 'fresh' ? raw.sensor : null;
    return { rows, sensor, suppressed_transitions: rows.filter(r => r.outcome === 'suppressed').length };
}
export function filteredDiagnostics(rows, filter = {}) {
    return rows.filter(row => ['kind', 'outcome', 'surface'].every(k => !filter[k] || row[k] === filter[k]));
}
export function renderVisionTuning(state, { busy, open, diagnosticFilter = {} } = {}) {
    const p = state.preferences.experience, policy = state.vision.effective_policy;
    const disabled = busy ? 'disabled' : '';
    const selection = (field, choices, value) => `<select data-tune-field="${field}" aria-label="${field === 'profile' ? 'Perfil de Vision' : 'Rendimiento de Vision'}" ${disabled}>${Object.entries(choices).map(([k, v]) =>
        `<option value="${k}" ${value === k ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select>`;
    const rows = filteredDiagnostics(state.diagnostics.rows, diagnosticFilter);
    const sensor = state.diagnostics.sensor;
    return `<section class="vision-tuning"><details data-detail-key="vision-tuning" ${open ? 'open' : ''}>
        <summary>ADAPTA VISION A TI</summary>
        <h3>Tu ritmo, tus límites</h3>
        ${selection('profile', profiles, p.profile)}
        <button data-notify-action="tune-profile" ${disabled}>Aplicar perfil</button>
        <small>Silencioso: 0 avisos/h. Compañía: 1/h, sesión desde 60 min. Bienestar: 2/h, sesión 45 min y poco movimiento 40 min. Trabajo: 1/h, sesión 90 min. Compañía y Trabajo omiten sugerencias físicas. Personalizado conserva tu política original.</small>
        <p>${policy ? `Límites efectivos: ${policy.max_per_hour} aviso(s)/h · sesión desde ${Math.round(policy.long_session_seconds / 60)} min · separación global ${state.vision.effective_cooldown_minutes} min.` : 'Actualiza para consultar los límites efectivos.'}</p>
        <small>Intervalos por tipo: ${policy ? Object.entries(policy.kind_cooldown_minutes || {}).map(([k, v]) => esc(kinds[k]) + ': ' + Number(v) + ' min').join(' · ') : '—'}. Nunca elimina recibos ni ignora actividad o permisos.</small>
        <h3>Accesibilidad</h3>
        <button data-notify-action="tune-physical_suggestions" aria-pressed="${p.physical_suggestions}" ${disabled}>${p.physical_suggestions ? 'Desactivar sugerencias físicas' : 'Permitir sugerencias físicas'}</button>
        <small>Al desactivarlas, no se sugiere poco movimiento y la sesión larga propone organizar prioridades. No se infiere tu salud.</small>
        <button data-notify-action="tune-greetings" aria-pressed="${p.greetings}" ${disabled}>${p.greetings ? 'Desactivar gesto de saludo' : 'Permitir gesto de saludo'}</button>
        <small>El resumen sigue siendo opcional. La preferencia de movimiento reducido siempre tiene prioridad.</small>
        <h3>Rendimiento local</h3>
        ${selection('performance', { economy: 'Ahorro · hasta 5 inferencias/s', balanced: 'Equilibrado · hasta 15/s', responsive: 'Fluido · hasta 30/s' }, p.performance)}
        <button data-notify-action="tune-performance" ${disabled}>Aplicar rendimiento</button>
        <small>Limita el análisis de Vision, no la cámara física ni los FPS del avatar. Pausa la inferencia al ocultarse o tras sobrecarga sostenida. No mide batería.</small>
        <h3>Calibración voluntaria</h3>
        <p>Con la cámara ya autorizada, mantén una posición cómoda y tranquila durante 15 segundos. Ajusta solo el ruido del movimiento; no calibra postura ni identifica personas.</p>
        <button data-notify-action="calibration-start" ${disabled}>Calibrar captura actual</button>
        <button data-notify-action="calibration-reset" ${disabled}>Restablecer calibración</button>
        <small>Solo en RAM. Se pierde al cerrar la cámara. No abre ni transfiere sensores; necesita Face o Companion actualizado.</small>
        <p>${sensor ? esc(calibration[sensor.calibration] || 'Estado no disponible') + ' · ' + Number(sensor.progress) + '%' : 'No hay diagnóstico reciente de cámara. No se asume que esté funcionando.'}</p>
        <button data-notify-action="tune-reset_feedback" ${disabled}>Restablecer intervalos por feedback</button>
        <small>Conserva votos y tipos desactivados; no toca personalidad ni memoria.</small>
        <p>Feedback de los avisos retenidos: ${state.items.filter(r => r.feedback === 'useful').length} útiles · ${state.items.filter(r => r.feedback === 'too_soon').length} muy pronto · ${state.items.filter(r => r.feedback === 'not_applicable').length} no aplican. ${state.unread} pendientes sin leer; no se presupone que los ignoraste.</p>
        <h3>Diagnóstico de esta sesión de Core</h3>
        <p>${sensor ? esc(sensor.surface) + ' · ' + esc(sensor.state) + ' · última inferencia ' + Number(sensor.latency_ms).toFixed(1) + ' ms · intervalo ' + Number(sensor.interval_ms) + ' ms' : 'Cámara / motor sin informe reciente, o retención apagada.'}</p>
        <p>${state.diagnostics.suppressed_transitions} transiciones a decisiones evitadas entre las últimas 100. No representa precisión, salud ni un total histórico.</p>
        <div class="notify-diagnostic-filters">${[['kind', kinds, 'Tipo'], ['outcome', outcomes, 'Resultado'],
            ['surface', { face: 'Face', desktop_companion: 'Companion', legacy_face: 'Face antiguo', unknown: 'Sin superficie' }, 'Superficie']].map(([key, labels, title]) =>
            `<select data-diagnostic-filter="${key}" aria-label="Filtrar diagnóstico por ${title.toLowerCase()}"><option value="">${title}: todos</option>${Object.entries(labels).map(([k, v]) => `<option value="${k}" ${diagnosticFilter[key] === k ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select>`).join('')}</div>
        <button data-notify-action="diagnostic-filter" ${disabled}>Filtrar</button>
        <button data-notify-action="refresh" ${disabled}>Actualizar diagnóstico</button>
        <ol class="notify-diagnostic-list">${rows.slice(0, 12).map(row => `<li><strong>${esc(kinds[row.kind])} · ${esc(outcomes[row.outcome])}</strong><small>${esc(row.surface)} · ${esc(row.explanation)}</small></li>`).join('') || '<li>Sin decisiones en este filtro.</li>'}</ol>
        <small>Se muestran ${Math.min(12, rows.length)} de ${rows.length}. Historial de diagnóstico solo en RAM; los avisos y votos siguen tu retención local.</small>
        <button data-notify-action="diagnostic-export" ${disabled}>Exportar decisiones filtradas</button>
        <button data-notify-action="diagnostic-clear" ${disabled}>Borrar diagnóstico local</button>
    </details></section>`;
}
export async function handleVisionTuning(action, button, app) {
    const p = app.state.preferences.experience;
    if (action.startsWith('tune-')) {
        const key = action.slice(5);
        const fields = key === 'profile' || key === 'performance'
            ? { [key]: app.root.querySelector(`[data-tune-field="${key}"]`)?.value }
            : key === 'reset_feedback' ? {} : { enabled: !p[key] };
        if (await irisConfirm({
            title: 'Ajuste de Vision', icon: 'gear',
            message: 'Es un ajuste local. No activa sensores ni borra el presupuesto.',
            question: '¿Aplicarlo?', confirmLabel: 'Aplicar ajuste',
        })) {
            await app._mutation('/experience', { action: key, ...fields });
        }
        return true;
    }
    if (action.startsWith('calibration-')) {
        if (await irisConfirm({
            title: 'Calibrar Vision', icon: 'camera',
            message: 'La solicitud va a la cámara que ya está activa: no se abrirá ni transferirá otra. Para calibrar, permanece a tu ritmo durante 15 segundos.',
            question: '¿Enviarla?', confirmLabel: 'Calibrar',
        })) {
            await app._request('/calibration', { action: action.slice(12), confirm: true });
        }
        return true;
    }
    if (action === 'diagnostic-filter') {
        app.diagnosticFilter = Object.fromEntries([...app.root.querySelectorAll('[data-diagnostic-filter]')]
            .map(el => [el.dataset.diagnosticFilter, el.value]));
        app.render(); return true;
    }
    if (action === 'diagnostic-clear') {
        if (await irisConfirm({
            title: 'Borrar diagnóstico', icon: 'trash', tone: 'danger',
            message: 'Se borra el diagnóstico en memoria. Se conservan avisos, votos y límites; si Vision sigue activo, podrá registrar nuevas decisiones.',
            question: '¿Deseas continuar?', confirmLabel: 'Borrar', confirmIcon: 'trash',
        })) await app._request('/clear-diagnostics', { confirm: true });
        return true;
    }
    if (action === 'diagnostic-export') {
        const data = { schema: 'iris.vision-diagnostics/v1', scope: 'current_core_session',
            contains_media: false, rows: filteredDiagnostics(app.state.diagnostics.rows, app.diagnosticFilter) };
        const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
        const link = document.createElement('a');
        link.href = url; link.download = 'iris-vision-diagnostico.json';
        document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000); return true;
    }
    return false;
}
