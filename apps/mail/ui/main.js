/** F13. Three-pane mail reader from the user's mockup, inside the existing app host. */
import { osIconSvg } from '../../../face/js/os_icons.js?v=3';
import { createMailClient } from './adapter.js?v=3';
import { FOLDERS, FILTERS, createMailState, eligible, accountStatus, messageDate, initials } from './state.js?v=2';
import { createAppModelPicker } from '../../../face/js/app_model_picker.js?v=1';

export function createApp(context = {}, { fetchImpl, timers = globalThis } = {}) {
    const doc = globalThis.document, state = createMailState();
    const client = createMailClient({ fetchImpl });
    let draftDirty = false;
    let classification = null, draft = null, classifyOpen = false, classifyBusy = false, classifyError = '', reviewedRoute = null, authorized = false;
    let root, disposed = false, visible = false, interrupted = false, hasOpened = false, readEpoch = 0, detailEpoch = 0, timer = null, searchTimer = null, opening = null;
    const models = createAppModelPicker({ document: doc, fetchImpl, prefix: 'classification', providerPart: 'classification-provider', modelPart: 'classification-model',
        fieldClass: 'iris-mail-classification-input', labelClass: 'iris-mail-classification-label', buttonClass: 'iris-mail-classification-refresh',
        getSelection: () => ({ provider: draft?.provider || '', model: draft?.model || '' }),
        onSelection: selection => { if (draft) { Object.assign(draft, selection); draftDirty = true; reviewedRoute = null; authorized = false; } }, changed: () => render() });
    const el = (tag, part, text = '', key = '') => {
        const node = doc.createElement(tag); node.className = `iris-mail-${part}`;
        node.dataset.part = part; if (key) node.dataset.key = key; if (text) node.textContent = text; return node;
    };
    const icon = (name) => { const node = el('span', 'icon'); node.innerHTML = osIconSvg(name); return node; };
    function button(part, label, key, action, { disabled = false, pressed, mark } = {}) {
        const node = el('button', part, '', key); node.type = 'button'; node.disabled = disabled; node.title = label;
        if (pressed !== undefined) node.setAttribute('aria-pressed', String(pressed));
        if (mark) node.append(icon(mark)); node.append(el('span', 'button-label', label));
        node.addEventListener('click', () => { if (!node.disabled && !disposed) void action(); }); return node;
    }
    const params = () => ({ account_id: state.account, folder: state.folder, filter: state.filter, query: state.query, cursor: state.cursor, ...(state.focusId ? { focus_id: state.focusId } : {}) });
    const syncTargets = () => (state.data?.accounts || []).filter((account) => eligible(account) && (!state.account || state.account === account.id)).map((account) => account.id);
    function clearDetail() {
        detailEpoch += 1; client.abortDetail(); state.selected = ''; state.detail = null;
        state.detailLoading = false; state.detailError = ''; state.body = null; state.bodyLoading = false; state.bodyError = '';
    }
    async function readClassification({ resetDraft = false } = {}) {
        if (disposed || classifyBusy) return;
        classifyBusy = true; classifyError = ''; render();
        try {
            const value = await client.classification();
            if (disposed) return;
            if (value.schema !== 'iris.mail-classification/v1' || !value.options || !Array.isArray(value.results)) throw new Error('La respuesta de clasificación no es válida.');
            classification = value; if (!draft || !draftDirty || resetDraft) { draft = structuredClone(value.options); draftDirty = false; reviewedRoute = null; authorized = false; }
        } catch (error) { if (!disposed && error?.name !== 'AbortError') classifyError = error.message; }
        finally { if (!disposed) { classifyBusy = false; render(); } }
    }
    async function classificationAction(action) {
        if (classifyBusy || !draft) return;
        if (action !== 'pause' && !models.valid()) return;
        classifyBusy = true; classifyError = ''; render();
        try {
            if (action === 'probe') {
                reviewedRoute = await client.probe(draft.provider.trim(), draft.model.trim()); authorized = false;
            } else {
                const options = action === 'pause' ? { ...classification.options, enabled: false }
                    : { ...draft, provider: draft.provider.trim(), model: draft.model.trim(), enabled: true };
                await client.configure(options, action === 'pause' ? null : reviewedRoute, action !== 'pause' && authorized);
                classifyBusy = false; await readClassification({ resetDraft: true });
            }
        } catch (error) { if (!disposed && error?.name !== 'AbortError') classifyError = error.message; }
        finally { if (!disposed) { classifyBusy = false; render(); } }
    }
    function classificationPanel() {
        const section = el('aside', 'classification'); section.setAttribute('aria-label', 'Clasificación de Correo');
        section.append(icon('brain'), el('h3', 'privacy-title', 'Relevancia con IA · opcional'),
            el('p', 'privacy-copy', 'Apagada por defecto. Solo clasifica correos nuevos después de activarla; nunca cambia tu buzón de Gmail.'),
            button('classification-toggle', classifyOpen ? 'Cerrar configuración de IA' : 'Configurar clasificación', 'classification-toggle', () => {
                classifyOpen = !classifyOpen; render(); if (classifyOpen) void readClassification().then(() => models.load());
            }, { pressed: classifyOpen }));
        if (!classifyOpen) return section;
        const status = el('p', 'classification-status', classifyBusy ? 'Comprobando…' : classifyError || (classification
            ? `${classification.options.enabled ? 'Activada' : 'Pausada'} · ${({off:'apagada',waiting:'esperando correos nuevos',awaiting_baseline:'actualiza Gmail para establecer la base inicial',running:'clasificando',connection_changed:'revisa la conexión de Google',daily_limit:'límite diario alcanzado',unavailable:'no disponible',request_unknown:'resultado sin confirmar',capacity:'almacén lleno',profile_changed:'revisa el perfil de Hermes',busy:'otra clasificación en curso',paused:'Correo desactivado'})[classification.state] || 'revisa el último resultado'} · ${classification.daily_used}/${classification.options.daily_limit} trabajos hoy (UTC)` : 'Consulta la configuración para continuar.'));
        status.setAttribute('role', 'status'); section.append(status);
        if (!draft) { if (!classifyBusy) section.append(button('classification-retry', 'Reintentar', 'classification-retry', readClassification)); return section; }
        const form = el('fieldset', 'classification-fields'); form.disabled = classifyBusy;
        form.append(el('legend', 'section-title', 'QUÉ Y DÓNDE CLASIFICAR'));
        const invalidate = () => { draftDirty = true; reviewedRoute = null; authorized = false; };
        function field(name, label, { type = 'text', min, max } = {}) {
            const wrap = el('label', 'classification-label', label), input = el(name === 'criteria' ? 'textarea' : 'input', 'classification-input', '', `classification:${name}`);
            input.value = draft[name]; if (name !== 'criteria') input.type = type;
            if (min) input.min = min; if (max) input.max = max;
            if (type !== 'number') input.maxLength = name === 'criteria' ? 2000 : name === 'model' ? 200 : 100;
            input.addEventListener('input', () => { draft[name] = type === 'number' ? Number(input.value) : input.value; invalidate();
                // Disable existing consent controls immediately without replacing a typing field.
                const activate = walk(root, 'classification-enable'); if (activate) activate.disabled = true;
                const consent = walk(root, 'classification-consent'); if (consent) { consent.checked = false; consent.disabled = true; }
            });
            wrap.append(input); form.append(wrap);
        }
        const accounts = el('div', 'classification-accounts'); accounts.append(el('strong', 'section-title', 'CUENTAS AUTORIZADAS'));
        for (const account of (state.data?.accounts || []).filter(eligible)) {
            const label = el('label', 'classification-check', account.email), input = el('input', 'classification-checkbox', '', `classification:account:${account.id}`);
            input.type = 'checkbox'; input.checked = draft.account_ids.includes(account.id);
            input.addEventListener('change', () => { draft.account_ids = input.checked ? [...draft.account_ids, account.id] : draft.account_ids.filter((key) => key !== account.id); invalidate(); render(); });
            label.prepend(input); accounts.append(label);
        }
        form.append(accounts);
        for (const [labelId, title] of [['INBOX', 'Bandeja de entrada'], ['STARRED', 'Destacados'], ['IMPORTANT', 'Importantes en Gmail']]) {
            const label = el('label', 'classification-check', title), input = el('input', 'classification-checkbox', '', `classification:folder:${labelId}`);
            input.type = 'checkbox'; input.checked = draft.folders.includes(labelId);
            input.addEventListener('change', () => { draft.folders = input.checked ? [...draft.folders, labelId] : draft.folders.filter((key) => key !== labelId); invalidate(); render(); }); label.prepend(input); form.append(label);
        }
        form.append(models.render({ disabled: classifyBusy })); field('criteria', 'Qué es importante para ti');
        const contentLabel = el('label', 'classification-label', 'Contenido que autorizas enviar'), content = el('select', 'classification-input', '', 'classification:content');
        for (const [value, label] of [['snippet', 'Asunto, remitente, destinatario y extracto'], ['body', 'Lo anterior + hasta 20 000 caracteres del texto']]) {
            const option = el('option', 'option', label); option.value = value; option.selected = draft.content === value; content.append(option);
        }
        content.value = draft.content; content.addEventListener('change', () => { draft.content = content.value; invalidate(); render(); }); contentLabel.append(content); form.append(contentLabel);
        field('interval_minutes', 'Intervalo mínimo entre trabajos (minutos)', { type: 'number', min: 1, max: 1440 });
        field('daily_limit', 'Máximo de intentos diarios (UTC)', { type: 'number', min: 1, max: 100 });
        form.append(button('classification-probe', 'Revisar destino', 'classification-probe', () => classificationAction('probe'), { disabled: classifyBusy || !models.valid() || !draft.account_ids.length || !draft.folders.length }));
        if (reviewedRoute) {
            form.append(el('p', 'classification-destination', `${reviewedRoute.provider} · ${reviewedRoute.model}\n${reviewedRoute.endpoint}`));
            const label = el('label', 'classification-check', `Autorizo enviar los campos elegidos de estas ${draft.account_ids.length} cuentas a este destino, usando estos criterios.`);
            const consent = el('input', 'classification-checkbox', '', 'classification-consent'); consent.type = 'checkbox'; consent.checked = authorized;
            consent.addEventListener('change', () => { authorized = consent.checked; render(); }); label.prepend(consent); form.append(label);
        }
        form.append(el('p', 'privacy-copy', 'La primera actualización de Gmail tras activar establece una base y no se clasifica. Después solo se procesan altas nuevas de Gmail. El proveedor puede cobrar o consumir tu cuota. Este límite cuenta intentos, incluidos los fallidos; no es un precio ni un límite exacto de tokens. Sin herramientas, adjuntos ni imágenes. El historial anterior no se procesa.'),
            button('classification-enable', 'Autorizar y activar desde ahora', 'classification-enable', () => classificationAction('enable'), { disabled: classifyBusy || !models.valid() || !reviewedRoute || !authorized }));
        section.append(form);
        if (classification?.options.enabled) section.append(button('classification-pause', 'Pausar clasificación', 'classification-pause', () => classificationAction('pause'), { disabled: classifyBusy }));
        section.append(button('classification-refresh', 'Actualizar resultados locales', 'classification-refresh', readClassification, { disabled: classifyBusy }));
        for (const result of classification?.results || []) {
            const item = el('div', 'classification-result'); const value = result.correction || result.result?.label;
            item.append(el('strong', 'classification-subject', result.subject), el('p', 'classification-label', value ? `${value}${result.correction ? ' · corregido por ti' : ' · sugerencia de IA'}` : result.state === 'running' ? 'Clasificando…' : 'Sin resultado confirmado · no se reintentará solo'),
                el('p', 'privacy-copy', result.result ? `${result.result.summary}\n${result.result.reason}` : result.state === 'running' ? 'La petición está en curso. Puedes pausarla.' : 'La solicitud no dejó un resultado válido. Puede haberse enviado y consumir cuota; no se repetirá automáticamente.'));
            if (result.result) {
                item.append(el('small', 'privacy-copy', `Modelo solicitado: ${result.route?.model || 'no disponible'} · informado: ${result.reported_model || 'no disponible'}`));
                for (const label of ['importante', 'normal', 'incierto']) item.append(button('classification-correct', `Marcar ${label} en Iris`, `correct:${result.id}:${label}`, async () => {
                    if (classifyBusy) return; classifyBusy = true; render();
                    try { await client.correct(result.id, label, result.revision); classifyBusy = false; await readClassification(); }
                    catch (error) { if (!disposed) classifyError = error.message; }
                    finally { if (!disposed) { classifyBusy = false; render(); } }
                }, { disabled: classifyBusy || value === label }));
            }
            section.append(item);
        }
        return section;
    }
    function resetPage() { state.cursor = ''; state.history = []; state.focusId = ''; clearDetail(); }

    async function read({ reset = false, quiet = false } = {}) {
        if (disposed) return;
        if (reset) resetPage();
        const epoch = ++readEpoch;
        let redraw = !quiet;
        state.loading = true; state.error = ''; if (!quiet) render();
        try {
            const data = await client.read(params());
            if (disposed || epoch !== readEpoch) return;
            if (data.schema !== 'iris.mail/v1' || !Array.isArray(data.messages) || !Array.isArray(data.accounts)) throw new Error('La respuesta de Correo no es válida.');
            const old = state.data;
            state.data = data;
            if (state.selected) {
                const current = data.messages.find((row) => row.id === state.selected);
                if (!current) clearDetail();
                else if (state.detail && !state.detailLoading) state.detail = current;
            }
            context.setBrief?.(`${data.total} ${data.total === 1 ? 'correo guardado' : 'correos guardados'}`);
            redraw = redraw || JSON.stringify(old) !== JSON.stringify(data);
            if (classifyOpen && !classifyBusy) {
                try {
                    const current = await client.classification();
                    if (disposed || epoch !== readEpoch) return;
                    if (current.schema === 'iris.mail-classification/v1' && Array.isArray(current.results)) {
                        redraw = redraw || JSON.stringify(classification) !== JSON.stringify(current);
                        classification = current;
                    }
                } catch (error) { if (error?.name !== 'AbortError') { classifyError = error.message; redraw = true; } }
            }
        } catch (error) {
            if (disposed || epoch !== readEpoch || error?.name === 'AbortError') return;
            if (error.code === 'page_changed' || error.code === 'account_changed') {
                if (error.code === 'account_changed') state.account = '';
                state.notice = error.message; resetPage(); return read();
            }
            state.error = error.message || 'No se pudo leer el correo guardado.';
            redraw = true;
            // A previous page is not the result for a new account/search/page.
            state.data = null; clearDetail();
        } finally {
            if (!disposed && epoch === readEpoch) { state.loading = false; if (redraw) render(); schedule(); }
        }
    }
    async function select(key) {
        clearDetail();
        const epoch = ++detailEpoch; state.selected = key; state.detail = null; state.detailLoading = true; state.detailError = ''; render();
        try {
            const result = await client.detail(key);
            if (disposed || epoch !== detailEpoch) return;
            if (result.message?.id !== key || !(state.data?.messages || []).some((row) => row.id === key && row.account_id === result.message.account_id)) throw new Error('El mensaje cambió. Actualiza la vista.');
            state.detail = result.message;
            state.detailLoading = false; render();
            await loadBody(key, epoch);
        } catch (error) {
            if (!disposed && epoch === detailEpoch && error?.name !== 'AbortError') state.detailError = error.message || 'No se pudo abrir el mensaje.';
        } finally { if (!disposed && epoch === detailEpoch) { state.detailLoading = false; render(); } }
    }
    async function loadBody(key, epoch = detailEpoch) {
        if (!state.detail || state.bodyLoading || disposed) return;
        state.bodyLoading = true; state.bodyError = ''; render();
        try {
            const result = await client.body(key);
            if (disposed || epoch !== detailEpoch) return;
            if (result.message_id !== key || result.account_id !== state.detail.account_id || result.body?.schema !== 'iris.mail-body/v1'
                || typeof result.body.text !== 'string' || result.body.text.length > 200000) throw new Error('El contenido del mensaje no es válido.');
            state.body = result.body;
        } catch (error) {
            if (!disposed && epoch === detailEpoch && error?.name !== 'AbortError') state.bodyError = error.message || 'No se pudo cargar el texto de Gmail. El extracto sigue disponible.';
        } finally { if (!disposed && epoch === detailEpoch) { state.bodyLoading = false; render(); } }
    }
    async function sync() {
        const targets = syncTargets(); if (disposed || state.syncing || !targets.length) return;
        state.syncing = true; state.notice = ''; render();
        try {
            const result = await client.sync(targets);
            if (disposed) return;
            state.notice = result.accepted ? `Actualización solicitada${result.errors?.length ? '; algunas cuentas no pudieron iniciarla' : ''}. La copia cambiará cuando termine.`
                : 'No se pudo solicitar la actualización. Revisa el estado de las cuentas en Conexiones.';
            await read({ reset: true });
        } catch (error) { if (!disposed && error?.name !== 'AbortError') state.notice = error.message || 'No se pudo solicitar la actualización.'; }
        finally { if (!disposed) { state.syncing = false; render(); } }
    }
    function schedule() {
        if (timer !== null) timers.clearTimeout(timer); timer = null;
        if (!visible || disposed) return;
        const running = state.data?.accounts.some((account) => account.sync?.state === 'running') || (classifyOpen && ['running', 'awaiting_baseline'].includes(classification?.state));
        timer = timers.setTimeout(() => { timer = null; void read({ quiet: true }); }, running ? 2000 : 30000);
    }
    function change(key, value) { state[key] = value; state.notice = ''; resetPage(); void read(); }
    function sidebar() {
        const rail = el('aside', 'rail'); rail.setAttribute('aria-label', 'Carpetas y cuentas');
        const identity = el('div', 'identity'); const portrait = el('img', 'portrait');
        portrait.src = '/face/public/assets/os/iris-profile-icon-v1.png'; portrait.alt = ''; portrait.width = 60; portrait.height = 60;
        identity.append(portrait, el('strong', 'brand', 'IRIS'), el('small', 'read-only', 'CORREO · SOLO LECTURA')); rail.append(identity);
        const folders = el('nav', 'folders'); folders.setAttribute('aria-label', 'Carpetas');
        const marks = { inbox: 'mail', starred: 'skills', sent: 'launch', drafts: 'notes', all: 'folder' };
        for (const [key, label] of Object.entries(FOLDERS)) {
            const node = button('folder', label, `folder:${key}`, () => change('folder', key), { pressed: state.folder === key, mark: marks[key] });
            if (state.data?.counts?.[key] !== undefined) node.append(el('span', 'count', String(state.data.counts[key])));
            folders.append(node);
        }
        rail.append(folders, el('h3', 'section-title', 'CUENTAS'));
        rail.append(button('account', 'Todas las cuentas', 'account:all', () => change('account', ''), { pressed: !state.account, mark: 'database' }));
        for (const account of state.data?.accounts || []) {
            const node = button('account', account.email || 'Cuenta de Google', `account:${account.id}`, () => change('account', account.id), { pressed: state.account === account.id, mark: 'mail' });
            node.append(el('small', 'account-state', accountStatus(account))); rail.append(node);
        }
        rail.append(button('connections', 'Gestionar cuentas', 'connections', () => context.openApp?.('conexiones'), { mark: 'plus' }));
        const privacy = el('div', 'privacy'); privacy.append(icon('lock'), el('strong', 'privacy-title', 'Lectura local y segura'),
            el('p', 'privacy-copy', 'Sin imágenes remotas. Abrir un correo aquí no lo marca como leído.')); rail.append(privacy); return rail;
    }
    function toolbar() {
        const bar = el('form', 'toolbar'); bar.setAttribute('role', 'search');
        const search = el('input', 'search', '', 'search'); search.type = 'search'; search.value = state.query; search.maxLength = 200;
        search.placeholder = 'Buscar en el correo guardado…'; search.setAttribute('aria-label', 'Buscar por remitente, asunto o extracto');
        search.addEventListener('input', () => {
            state.query = search.value; readEpoch += 1; resetPage(); state.loading = true; state.error = ''; state.data = null;
            if (searchTimer !== null) timers.clearTimeout(searchTimer);
            searchTimer = timers.setTimeout(() => { searchTimer = null; void read(); }, 250);
        });
        bar.addEventListener('submit', (event) => { event.preventDefault(); if (searchTimer !== null) timers.clearTimeout(searchTimer); searchTimer = null; void read({ reset: true }); });
        bar.append(search, button('sync', state.syncing ? 'Solicitando…' : 'Actualizar Gmail', 'sync', sync, { disabled: state.syncing || !syncTargets().length, mark: 'update' }));
        const filters = el('div', 'filters'); filters.setAttribute('aria-label', 'Filtros de mensajes');
        for (const [key, label] of Object.entries(FILTERS)) filters.append(button('filter', label, `filter:${key}`, () => change('filter', key), { pressed: state.filter === key }));
        bar.append(filters); return bar;
    }
    function mobileControls() {
        const controls = el('div', 'mobile');
        function chooser(part, label, choices, current, action) {
            const wrapper = el('label', 'mobile-label', label);
            const select = el('select', part, '', part); select.setAttribute('aria-label', label);
            for (const [key, name] of choices) { const option = el('option', 'option', name); option.value = key; option.selected = key === current; select.append(option); }
            select.value = current; select.addEventListener('change', () => action(select.value)); wrapper.append(select); controls.append(wrapper);
        }
        chooser('mobile-account', 'Cuenta de Correo', [['', 'Todas las cuentas'], ...(state.data?.accounts || []).map((account) => [account.id, `${account.email} · ${accountStatus(account)}`])], state.account, (value) => change('account', value));
        chooser('mobile-folder', 'Carpeta de Correo', Object.entries(FOLDERS), state.folder, (value) => change('folder', value));
        controls.append(button('mobile-connections', 'Gestionar cuentas', 'mobile-connections', () => context.openApp?.('conexiones')));
        return controls;
    }
    function empty() {
        if (state.loading) return 'Leyendo el correo guardado…';
        if (state.error) return 'No se pudo consultar esta vista.';
        if (!state.data?.available) return 'La conexión de Google no está disponible.';
        if (!state.data.accounts.some(eligible)) return 'Conecta una cuenta con permiso de Correo en Conexiones.';
        if (state.data.warnings?.length) return 'No hay mensajes mostrables en esta vista incompleta.';
        return 'No hay mensajes guardados que coincidan. Puedes actualizar Gmail para consultar cambios.';
    }
    function listing() {
        const center = el('section', 'center'); center.setAttribute('aria-label', 'Lista de mensajes'); center.append(mobileControls(), toolbar());
        const status = el('div', 'status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
        status.append(el('p', 'status-text', state.error || (state.loading ? 'Leyendo la copia local…' : state.notice || 'Copia local · no es todo tu buzón')));
        const accounts = (state.data?.accounts || []).filter(eligible).filter(account => !state.account || account.id === state.account);
        if (accounts.length) {
            const automatic = accounts.filter(account => account.sync_enabled === true).length;
            const known = accounts.every(account => typeof account.sync_enabled === 'boolean');
            const copy = !known ? 'Consulta la actualización automática en Conexiones.'
                : automatic === accounts.length ? 'Actualización automática cada 15 minutos mientras Iris está abierto.'
                : automatic ? `Actualización automática: ${automatic} de ${accounts.length} cuentas. Las demás se actualizan manualmente.`
                : 'Actualización automática apagada. Pulsa «Actualizar Gmail» o actívala en Conexiones.';
            status.append(el('p', 'sync-policy', copy),
                button('sync-settings', 'Configurar actualización automática', 'sync-settings', () => context.openApp?.('conexiones')));
            for (const account of accounts) {
                const readAt = Date.parse(account.sync?.last_success_at || '');
                if (Number.isFinite(readAt)) status.append(el('small', 'last-read', `${account.email || 'Cuenta de Google'} · Última lectura: ${messageDate(readAt)}`));
            }
        }
        if (state.error) status.append(button('retry', 'Reintentar', 'retry', () => read({ reset: true })));
        for (const warning of state.data?.warnings || []) status.append(el('p', 'warning', warning.message));
        center.append(status);
        const list = el('div', 'list'); list.dataset.scroll = 'list';
        if (state.loading || !state.data?.messages.length) list.append(el('p', 'empty', empty()));
        else for (const message of state.data.messages) {
            const row = button('message', '', `message:${message.id}`, () => select(message.id), { pressed: state.selected === message.id });
            row.dataset.unread = String(message.unread);
            row.append(el('span', 'avatar', initials(message.sender)));
            const copy = el('span', 'message-copy');
            copy.append(el('strong', 'sender', message.sender), el('span', 'subject', message.subject), el('span', 'snippet', message.snippet || 'Sin extracto disponible'));
            const meta = el('span', 'message-meta'); meta.append(el('span', 'date', messageDate(message.date_ms, { compact: true })));
            if (message.unread) meta.append(el('span', 'unread', 'No leído'));
            if (message.starred) { const star = icon('skills'); star.setAttribute('aria-label', 'Destacado'); meta.append(star); }
            row.append(copy, meta); list.append(row);
        }
        center.append(list);
        const pages = el('nav', 'pages'); pages.setAttribute('aria-label', 'Páginas de correo');
        pages.append(button('previous', 'Anterior', 'previous', () => { state.cursor = state.history.pop() || ''; clearDetail(); void read(); }, { disabled: state.loading || !state.history.length }),
            el('span', 'page-count', state.data?.total ? `${state.data.offset + 1}–${state.data.offset + state.data.messages.length} de ${state.data.total}` : '0 mensajes'),
            button('next', 'Siguiente', 'next', () => { state.history.push(state.cursor); state.cursor = state.data.next_cursor; clearDetail(); void read(); }, { disabled: state.loading || !state.data?.next_cursor }));
        center.append(pages); return center;
    }
    function detailPanel() {
        const panel = el('section', 'detail'); panel.setAttribute('aria-label', 'Detalle del mensaje'); panel.dataset.scroll = 'detail';
        if (state.detailLoading) panel.append(el('p', 'detail-empty', 'Leyendo el detalle guardado…'));
        else if (state.detailError) panel.append(el('p', 'detail-error', state.detailError), button('detail-retry', 'Reintentar', 'detail-retry', () => select(state.selected)));
        else if (!state.detail) {
            panel.append(icon('mail'), el('h2', 'detail-title', 'Tu correo, en tu espacio'),
                el('p', 'detail-empty', state.loading || state.error ? empty() : 'Selecciona un mensaje para leerlo. Iris consultará su texto en Gmail.'));
        } else {
            const message = state.detail;
            const head = el('div', 'detail-head'); head.append(el('h2', 'detail-title', message.subject), el('span', 'mode', 'Solo lectura'));
            const sender = el('div', 'detail-sender'); sender.append(el('span', 'avatar', initials(message.sender)));
            const who = el('div', 'who'); who.append(el('strong', 'from', message.sender), el('p', 'to', `Para: ${message.to || 'No indicado'}`), el('p', 'sent-at', messageDate(message.date_ms)));
            sender.append(who); panel.append(head, sender);
            const account = state.data?.accounts.find((item) => item.id === message.account_id);
            panel.append(el('p', 'detail-account', `Cuenta: ${account?.email || 'Google'}`));
            const body = el('article', 'body');
            body.setAttribute('aria-busy', String(state.bodyLoading));
            body.append(el('h3', 'section-title', state.body ? 'MENSAJE' : 'EXTRACTO GUARDADO'));
            body.append(el('p', state.body ? 'message-text' : 'excerpt', state.body ? state.body.text || 'Este mensaje tiene el cuerpo vacío.'
                : message.snippet || 'Gmail no guardó un extracto de este mensaje.'));
            if (state.bodyLoading) { const status = el('p', 'body-status', 'Cargando el texto de Gmail…'); status.setAttribute('role', 'status'); body.append(status); }
            if (state.bodyError) body.append(el('p', 'body-error', state.bodyError), button('body-retry', 'Reintentar lectura', 'body-retry', () => loadBody(message.id)));
            if (state.body?.source_format === 'html_text') body.append(el('p', 'excerpt-note', 'Versión de texto del correo HTML; se omiten su diseño e imágenes.'));
            if (state.body?.lossy) body.append(el('p', 'excerpt-note', 'Algunos caracteres no se pudieron decodificar y se muestran sustituidos.'));
            body.append(el('p', 'excerpt-note', 'Sin imágenes remotas ni apertura de archivos adjuntos. El texto consultado permanece en esta vista; no se guarda para leerlo sin conexión.'));
            panel.append(body);
            const flags = el('div', 'flags');
            flags.append(el('span', 'flag', message.unread ? 'Sigue sin leer en Gmail' : 'Leído en Gmail'));
            if (message.important) flags.append(el('span', 'flag', 'Importante en Gmail'));
            if (message.starred) flags.append(el('span', 'flag', 'Destacado en Gmail'));
            panel.append(flags);
        }
        panel.append(classificationPanel()); return panel;
    }
    function walk(node, key) { if (node?.dataset?.key === key) return node; for (const child of node?.children || []) { const found = walk(child, key); if (found) return found; } return null; }
    function render() {
        if (!root || disposed) return;
        const focus = root.contains?.(doc.activeElement) ? doc.activeElement : null, key = focus?.dataset?.key;
        const range = typeof focus?.selectionStart === 'number' ? [focus.selectionStart, focus.selectionEnd] : null;
        const scroll = [...(root.querySelectorAll?.('[data-scroll]') || [])].map((node) => [node.dataset.scroll, node.scrollTop]);
        const host = el('div', 'host'), app = el('div', 'app'); app.append(sidebar(), listing(), detailPanel()); host.append(app); root.replaceChildren(host);
        for (const [name, top] of scroll) { const node = root.querySelector?.(`[data-scroll="${name}"]`); if (node) node.scrollTop = top; }
        if (key) { const next = walk(root, key); next?.focus?.(); if (range) next?.setSelectionRange?.(...range); }
    }
    function dispose() {
        if (disposed) return; disposed = true; readEpoch += 1; detailEpoch += 1;
        if (timer !== null) timers.clearTimeout(timer); if (searchTimer !== null) timers.clearTimeout(searchTimer);
        models.dispose(); client.dispose(); root?.replaceChildren(); if (root) delete root.dataset.mailRoot; root = null;
    }
    context.signal?.addEventListener('abort', dispose, { once: true });
    return {
        mount(target) { root = target; root.dataset.mailRoot = 'true'; render(); },
        refresh({ force = false, opening: isOpening = false } = {}) {
            hasOpened = true;
            if (opening) return opening;
            if (force || isOpening || !state.data) { opening = read().finally(() => { opening = null; }); return opening; }
        },
        setVisibility({ visible: shown }) {
            const returning = shown && !visible; visible = Boolean(shown);
            if (!visible) {
                interrupted = interrupted || state.loading || state.detailLoading || state.bodyLoading || Boolean(state.selected) || searchTimer !== null;
                if (searchTimer !== null) timers.clearTimeout(searchTimer); searchTimer = null;
                readEpoch += 1; detailEpoch += 1; client.abort();
                clearDetail();
                state.loading = false;
            }
            schedule();
            if (returning && hasOpened) { const quiet = !interrupted; interrupted = false; void read({ quiet }); }
        },
        navigate(payload = {}) {
            if (typeof payload.account_id === 'string' && /^gacc_[a-f0-9]{32}$/.test(payload.account_id)) {
                state.account = payload.account_id; state.folder = 'all'; state.query = ''; state.filter = 'all'; resetPage();
                const key = payload.message_id, navigation = detailEpoch;
                if (/^mail_[a-f0-9]{40}$/.test(key || '')) state.focusId = key;
                void read().then(async () => {
                    if (state.data && state.focusId === key && detailEpoch === navigation) {
                        const detail = await client.detail(key).catch(() => null);
                        if (!disposed && detailEpoch === navigation && state.account === payload.account_id && detail?.message?.id === key && detail.message.account_id === payload.account_id) {
                            state.selected = key; state.detail = detail.message; render();
                            // Navigation is local only; read full body after explicit selection.
                        }
                    }
                });
            }
        },
        dispose,
    };
}
