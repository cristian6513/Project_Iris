/** F15 stays in Calendar's detail pane. Mounting/reading never sends context to a model. */
import { createAppModelPicker } from '../../../face/js/app_model_picker.js?v=1';
export function createPreparation({ document, call, confirm, changed, fetchImpl }) {
    let event = null, opened = false, disposed = false, visible = false, busy = false;
    let provider = '', model = '', notes = '', description = false, preview = null, message = '';
    let jobs = [], loaded = false, epoch = 0, timer = null, reading = null;
    const models = createAppModelPicker({ document, fetchImpl, prefix: 'prep', providerPart: 'prep-provider', modelPart: 'prep-model',
        fieldClass: 'iris-calendar-prep-model-choice', labelClass: 'iris-calendar-prep-field', buttonClass: 'iris-calendar-prep-refresh',
        getSelection: () => ({ provider, model }), onSelection: selection => { ({ provider, model } = selection); preview = null; epoch += 1; }, changed });
    const sourceText = value => typeof value === 'string' ? value : [
        `Título: ${value.title}`, `Inicio: ${value.start}`, `Fin: ${value.end}`,
        value.all_day ? 'Evento de todo el día' : 'Evento con horario',
        ...(value.description !== undefined ? [`Descripción: ${value.description}`] : []),
    ].join('\n');
    const node = (tag, part, text = '') => {
        const n = document.createElement(tag); n.className = `iris-calendar-${part}`;
        n.dataset.part = part; if (text) n.textContent = text; return n;
    };
    const button = (part, text, action, disabled = false) => {
        const n = node('button', part, text); n.type = 'button'; n.dataset.key = part;
        // A busy button stays focusable when Calendar redraws; its action is still blocked.
        n.setAttribute('aria-disabled', String(disabled));
        n.addEventListener('click', () => { if (!disabled) void action(); }); return n;
    };
    function schedule() {
        clearTimeout(timer); timer = null;
        if (!disposed && visible && opened && jobs.some(j => j.running)) timer = setTimeout(() => void read(), 2000);
    }
    async function read() {
        if (reading || disposed) return reading;
        reading = (async () => {
            try {
                const data = await call('/preparation');
                if (disposed) return;
                if (!Array.isArray(data.jobs)) throw new Error('No se pudo leer el historial de preparación.');
                jobs = data.jobs; loaded = true;
            } catch (error) { if (!disposed && error?.name !== 'AbortError') message = error.message; }
            finally { reading = null; if (!disposed) { changed(); schedule(); } }
        })();
        return reading;
    }
    function input(part, title, value, update, { area = false, limit = 200 } = {}) {
        const label = node('label', 'prep-field', title);
        const n = node(area ? 'textarea' : 'input', part);
        n.value = value; n.maxLength = limit; n.disabled = busy; n.dataset.key = part;
        n.setAttribute('aria-label', title);
        n.addEventListener('input', () => { update(n.value); preview = null; epoch += 1; changed(); });
        label.append(n); return label;
    }
    async function inspect() {
        if (busy || !event || disposed || !models.valid()) return;
        const current = event, version = epoch;
        busy = true; message = ''; changed();
        try {
            const data = await call('/preparation/preview', { method: 'POST', body: {
                event_id: current.id, date: String(current.start).slice(0, 10), provider: provider.trim(), model: model.trim(), description, notes } });
            if (disposed || event?.id !== current.id || version !== epoch) return;
            preview = data.preview;
        } catch (error) { if (!disposed && version === epoch && error?.name !== 'AbortError') message = error.message; }
        finally { if (!disposed) { busy = false; changed(); } }
    }
    async function send() {
        if (busy || !preview || disposed || !models.valid()) return;
        const selected = preview, version = epoch, eventId = event.id;
        busy = true; changed();
        try {
            const agreed = await confirm?.({ title: 'Preparar reunión con IA',
                message: `Se enviarán las fuentes mostradas a ${selected.route.provider} · ${selected.route.model}, en ${selected.route.endpoint}. Puede consumir cuota. Se hará una petición, sin herramientas ni acciones en Google.`,
                question: '¿Autorizar esta preparación?', confirmLabel: 'Enviar y generar notas', cancelLabel: 'Cancelar' });
            if (!agreed || disposed || epoch !== version || event?.id !== eventId) return;
            const result = await call(`/preparation/${selected.id}/start`, { method: 'POST', body: { confirmed: true } });
            if (disposed) return;
            preview = null;
            message = 'Preparación solicitada. Puedes cerrar esta ventana; los recordatorios siguen funcionando.';
            if (result.job) jobs = [result.job, ...jobs.filter(j => j.id !== result.job.id)];
            await read();
        } catch (error) { if (!disposed && error?.name !== 'AbortError') message = error.message; }
        finally { if (!disposed) { busy = false; changed(); schedule(); } }
    }
    async function cancel(job) {
        if (busy || disposed) return;
        busy = true; changed();
        try { await call(`/preparation/${job.id}/cancel`, { method: 'POST' }); await read(); }
        catch (error) { if (!disposed && error?.name !== 'AbortError') message = error.message; }
        finally { if (!disposed) { busy = false; changed(); } }
    }
    function render(current) {
        if (event?.id !== current?.id) {
            epoch += 1; event = current; preview = null; notes = ''; description = false; message = '';
        }
        const panel = node('section', 'preparation'); panel.setAttribute('aria-label', 'Preparación por IRIS');
        panel.append(node('span', 'eyebrow', 'PREPARACIÓN POR IRIS · OPCIONAL'),
            node('h3', 'prep-title', 'Llega con tus ideas claras'),
            node('p', 'prep-hint', 'Genera puntos y preguntas a partir de este evento y tus notas. Los avisos de horario son independientes.'));
        panel.append(button('prep-open', opened ? 'Ocultar preparación' : current ? 'Preparar con IRIS' : 'Ver preparaciones guardadas', () => {
            opened = !opened; changed(); if (opened) { void read(); void models.load(); } else schedule();
        }));
        if (!opened) return panel;
        if (current) panel.append(models.render({ disabled: busy }),
            input('prep-notes', 'Tus notas de contexto (opcional)', notes, v => notes = v, { area: true, limit: 4000 }));
        const label = node('label', 'prep-check');
        const check = node('input', 'prep-description'); check.type = 'checkbox'; check.checked = description; check.disabled = busy;
        check.dataset.key = 'prep-description'; check.setAttribute('aria-label', 'Incluir descripción del evento');
        check.addEventListener('change', () => { description = check.checked; preview = null; epoch += 1; changed(); });
        label.append(check, node('span', 'prep-hint', 'Incluir descripción del evento (hasta 12 000 caracteres)'));
        if (current) panel.append(label, node('small', 'prep-hint', 'Se incluye título y horario. No se envían correo, adjuntos, ubicación, asistentes ni enlace de reunión. Máximo 10 preparaciones al día por perfil.'),
            button('prep-preview', busy ? 'Procesando…' : 'Revisar fuentes y destino', inspect, busy || !models.valid()));
        if (preview) {
            const sources = node('div', 'prep-preview');
            sources.append(node('strong', 'prep-destination', `${preview.route.provider} · ${preview.route.model}`),
                node('small', 'prep-endpoint', preview.route.endpoint));
            for (const [id, value] of Object.entries(preview.sources)) {
                sources.append(node('h4', 'prep-source-name', id === 'event' ? 'Fuente: evento seleccionado' : 'Fuente: tus notas'),
                    node('pre', 'prep-source', sourceText(value)));
            }
            sources.append(button('prep-send', 'Generar notas con estas fuentes', send, busy || !models.valid())); panel.append(sources);
        }
        if (message) { const p = node('p', 'prep-status', message); p.setAttribute('role', 'status'); panel.append(p); }
        panel.append(button('prep-refresh', 'Actualizar notas de IA', read, busy || Boolean(reading)));
        if (!loaded) panel.append(node('p', 'prep-hint', reading ? 'Leyendo preparaciones guardadas…' : 'Actualiza para consultar el historial.'));
        for (const job of jobs.filter(j => !current || j.event_id === current.id)) {
            const card = node('article', 'prep-result');
            const states = { completed: 'Notas preparadas', failed: 'No se pudo preparar', cancelled: 'Preparación cancelada', unknown: job.running ? 'Preparando…' : 'Envío sin confirmar; no se repetirá solo' };
            card.append(node('strong', 'prep-event-title', job.title), node('h4', 'prep-result-title', states[job.state] || 'Estado no disponible'),
                node('small', 'prep-hint', `${job.route?.provider || ''} · ${job.route?.model || ''} · ${new Date(job.created_at * 1000).toLocaleString('es')}`));
            if (job.result) {
                card.append(node('p', 'prep-summary', job.result.summary));
                for (const [key, title] of [['points', 'Puntos a considerar'], ['questions', 'Preguntas para la reunión']]) {
                    card.append(node('h4', 'prep-list-title', title)); const list = node('ul', 'prep-list');
                    for (const value of job.result[key] || []) list.append(node('li', 'prep-point', value)); card.append(list);
                }
                card.append(node('small', 'prep-hint', `Fuentes usadas: ${job.result.sources.map(s => s === 'event' ? 'evento' : 'tus notas').join(', ')}. Sugerencia de IA: comprueba los datos.`));
            } else if (job.state === 'failed') card.append(node('p', 'prep-hint', 'El proveedor falló o las fuentes cambiaron. Revisa el evento antes de autorizar otra preparación.'));
            if (job.running || job.state === 'unknown') card.append(button(`prep-cancel-${job.id}`, 'Cancelar preparación', () => cancel(job), busy));
            const source = node('details', 'prep-saved-sources'); source.append(node('summary', 'prep-source-title', 'Ver fuentes guardadas'));
            for (const [id, value] of Object.entries(job.sources || {})) source.append(
                node('h4', 'prep-source-name', id === 'event' ? 'Evento seleccionado' : 'Tus notas'), node('pre', 'prep-source', sourceText(value)));
            card.append(source); panel.append(card);
        }
        return panel;
    }
    return { render, refresh: read, setVisibility(shown) { visible = shown; if (visible && opened) void read(); else schedule(); },
        dispose() { disposed = true; epoch += 1; models.dispose(); clearTimeout(timer); } };
}
