/* Channels are supplied by the installed Hermes runtime, including its plugins.
 * Each entry also carries whether that runtime can start it now (A08.6): a
 * channel whose components or programs are missing stays listed but cannot be
 * switched on, and the backend refuses it as well. */
class IrisMessagingPanel {
    constructor(root) {
        this.root = root;
        this.platforms = [];
        this.drafts = new Map();
        this.filter = '';
        this.activeOnly = false;
        this.pairing = null;
        this.pairingGeneration = 0;
        this.timer = null;
        this.whatsappDraft = { mode: 'self-chat', allowed_users: '' };
    }

    node(tag, text = '', className = '') {
        const node = document.createElement(tag);
        node.textContent = text;
        node.className = className;
        return node;
    }

    button(text, action, primary = false) {
        const button = this.node('button', text, primary ? 'btn-primary btn-sm' : 'btn-secondary btn-sm');
        button.type = 'button';
        button.onclick = async () => {
            button.disabled = true;
            try { await action(); } catch (error) { this.message(error.message, true); }
            finally { button.disabled = false; this.syncRestartGate(); }
        };
        return button;
    }

    async request(path, options = {}) {
        const response = await fetch(`/api/messaging${path}`, {
            ...options, cache: 'no-store', signal: AbortSignal.timeout(75000),
            headers: { 'Content-Type': 'application/json' },
        });
        const result = await response.json();
        if (!response.ok) {
            const error = new Error(typeof result.detail === 'string' ? result.detail : 'No se pudo completar la configuración.');
            error.status = response.status;
            throw error;
        }
        return result;
    }

    message(text, error = false) {
        const node = this.root.querySelector('.messaging-status');
        node.textContent = text;
        node.classList.toggle('error', error);
    }

    availability(platform) {
        return globalThis.IrisWizardRuntime
            ? IrisWizardRuntime.channelState(platform)
            : { available: platform?.availability?.available !== false, detail: platform?.availability?.detail || '' };
    }

    status(platform) {
        if (!this.availability(platform).available) return platform.enabled ? 'No disponible · activo' : 'No disponible';
        return ({ connected: 'Conectado', disabled: 'Desactivado', not_configured: 'Falta configurar',
            pending_restart: 'Pendiente de reiniciar', gateway_stopped: 'Servicio de IA detenido',
            startup_failed: 'No pudo iniciar', connecting: 'Conectando', fatal: 'Revisar conexión',
        })[platform.state] || (platform.enabled ? 'Pendiente de comprobar' : 'Desactivado');
    }

    async mount() {
        const toolbar = this.node('div', '', 'messaging-toolbar');
        const search = this.node('input');
        search.type = 'search';
        search.placeholder = 'Buscar canal o integración…';
        search.setAttribute('aria-label', 'Buscar canal');
        search.oninput = () => { this.filter = search.value.toLowerCase(); this.filterRows(); };
        const active = this.node('label', '', 'messaging-active-filter');
        const checkbox = this.node('input');
        checkbox.type = 'checkbox';
        checkbox.onchange = () => { this.activeOnly = checkbox.checked; this.filterRows(); };
        active.append(checkbox, this.node('span', 'Solo activos'));
        toolbar.append(search, active, this.button('Actualizar catálogo', () => this.load(true)));
        this.count = this.node('p', 'Leyendo los canales de mensajería…', 'dim');
        const actions = this.node('div', '', 'messaging-toolbar');
        actions.append(this.button('Preparar mensajes legibles', async () => {
            const result = await this.request('/prepare', { method: 'POST' });
            this.saved(result);
            await this.load();
        }), this.restartButton = this.button('Reiniciar IRIS para aplicar', () => this.restart()));
        this.syncRestartGate();
        const status = this.node('p', '', 'messaging-status');
        status.setAttribute('role', 'status');
        status.setAttribute('aria-live', 'polite');
        this.list = this.node('div', '', 'messaging-list');
        this.root.replaceChildren(toolbar, this.count, this.list, actions,
            this.node('p', 'Guardar conserva los campos vacíos. Reiniciar IRIS aplica los cambios sin cerrar tus ventanas; puede interrumpir una respuesta en curso.', 'dim'), status);
        await this.load();
        let id;
        try { id = sessionStorage.getItem('iris.whatsapp-pairing'); } catch { /* optional */ }
        if (id && /^[\w-]{12,100}$/.test(id)) {
            this.pairing = { pairing_id: id, status: 'starting' };
            await this.pollPairing(++this.pairingGeneration);
        }
    }

    // The Wizard's Gateway gate (llm_setup.js): Hermes is not restarted from here
    // before the main AI provider is ready, as with «Iniciar gateway» (R02).
    restartBlocked() {
        const gate = globalThis.irisGatewayBlockedReason;
        return typeof gate === 'function' ? gate() : '';
    }

    syncRestartGate() {
        if (!this.restartButton) return;
        const blocked = this.restartBlocked();
        this.restartButton.disabled = Boolean(blocked);
        this.restartButton.title = blocked || '';
    }

    async restart() {
        const blocked = this.restartBlocked();
        if (blocked) {
            this.message(blocked, true);
            return false;
        }
        // The Wizard asks once per profile before starting or restarting the
        // bundled engine (A08.6); development has no such hook requirement.
        const consent = globalThis.irisEnsureEngineConsent;
        if (consent && !await consent()) {
            const reason = globalThis.irisEngineConsentBlockedReason;
            this.message(reason ? reason('reiniciar el motor')
                : 'Acepta el aviso de seguridad de IRIS para reiniciar el asistente.', true);
            return false;
        }
        const result = await this.request('/restart', { method: 'POST' });
        this.message(result.restart_started
            ? 'IRIS se está reiniciando. Pulsa Actualizar catálogo para comprobar los canales.'
            : 'El motor de IA no confirmó el reinicio. Comprueba su instalación y vuelve a intentarlo.', !result.restart_started);
        return Boolean(result.restart_started);
    }

    saved(result) {
        this.message(result.warning || (result.needs_restart
            ? 'Guardado. Pulsa «Reiniciar IRIS» para aplicar los cambios.'
            : 'Guardado en este equipo.'), Boolean(result.warning));
    }

    async load(refresh = false) {
        const result = await this.request(`/platforms${refresh ? '?refresh=true' : ''}`);
        this.platforms = result.platforms;
        const open = new Set([...this.list.querySelectorAll('details[open]')].map(n => n.dataset.platform));
        this.list.replaceChildren(...this.platforms.map(p => this.card(p, open.has(p.id))));
        const unavailable = this.platforms.filter(p => !p.managed && !this.availability(p).available).length;
        this.count.textContent = `${this.platforms.length} canales de mensajería · `
            + (unavailable ? `${unavailable} no disponibles en esta instalación · ` : '')
            + (result.policy_ready ? 'Texto para chats preparado' : 'La salida de texto se preparará al guardar un canal');
        this.filterRows();
        this.renderPairing();
    }

    filterRows() {
        let visible = 0;
        for (const card of this.list.children) {
            const platform = this.platforms.find(p => p.id === card.dataset.platform);
            card.hidden = !`${platform.name} ${platform.id} ${platform.description}`.toLowerCase().includes(this.filter)
                || (this.activeOnly && !platform.enabled);
            if (!card.hidden) visible++;
        }
        this.list.dataset.empty = visible ? '' : 'No hay canales que coincidan.';
    }

    fieldCopy(platform, field) {
        const telegram = platform.id === 'telegram' ? {
            TELEGRAM_BOT_TOKEN: {
                label: 'Token del bot de Telegram',
                help: 'Lo entrega @BotFather al crear tu bot. Es necesario para que Iris se conecte con él.',
            },
            TELEGRAM_ALLOWED_USERS: {
                label: 'Usuarios autorizados de Telegram', recommended: true,
                help: 'Escribe el ID numérico de tu cuenta personal: puedes obtenerlo enviando /start a @userinfobot en Telegram. Separa varios con comas; no uses tu @usuario ni el ID del bot. Si no configuras esta lista y no hay usuarios ya aprobados, el bot puede pedir un código de vinculación. Iris todavía no permite aprobar ese código desde su interfaz.',
            },
            TELEGRAM_WEBHOOK_SECRET: {
                label: 'Secreto del webhook', conditional: true,
                help: 'No hace falta para la conexión normal de Iris. Solo es obligatorio si configuraste una URL de webhook. En ese caso, crea un secreto aleatorio propio; no es el token de @BotFather.',
            },
            TELEGRAM_WEBHOOK_URL: {
                label: 'URL del webhook',
                help: 'Opción avanzada para recibir mensajes en un servidor HTTPS público. Déjala sin configurar para la conexión normal; si la usas, necesitas también el secreto del webhook.',
            },
        } : {};
        const copy = telegram[field.key] || {};
        const requirement = field.required === true ? 'Obligatorio'
            : copy.recommended ? 'Recomendado'
                : field.required === false ? (copy.conditional ? 'Opcional · solo con webhook' : 'Opcional')
                : 'Según configuración';
        const help = copy.help || field.description || field.help ||
            (field.required === false
                ? 'No es un requisito general del canal. Consulta su guía para saber si la función que quieres usar lo necesita.'
                : 'Consulta la guía del canal para saber qué valor necesita esta opción.');
        return { label: copy.label || field.prompt || field.key, requirement, help };
    }

    card(platform, opened) {
        const availability = this.availability(platform);
        const card = this.node('details', '', availability.available ? 'messaging-card' : 'messaging-card unavailable');
        card.dataset.platform = platform.id;
        card.open = opened;
        const summary = this.node('summary');
        summary.append(this.node('span', platform.name.slice(0, 2).toUpperCase(), 'messaging-monogram'),
            this.node('strong', platform.name), this.node('span', this.status(platform),
                `messaging-badge ${availability.available ? platform.state || '' : 'unavailable'}`));
        const body = this.node('div', '', 'messaging-body');
        body.append(this.node('p', platform.description, 'dim'));
        if (!availability.available && !platform.managed) {
            const notice = this.node('p', availability.detail || 'Esta instalación no puede iniciar este canal.', 'messaging-unavailable');
            notice.setAttribute('role', 'note');
            body.append(notice);
        }
        if (platform.docs_url) {
            try {
                const url = new URL(platform.docs_url);
                if (url.protocol === 'https:' && !url.username && !url.password) {
                    const link = this.node('a', 'Guía oficial del canal', 'key-link');
                    Object.assign(link, { href: url.href, target: '_blank', rel: 'noopener noreferrer' });
                    body.append(link);
                }
            } catch { /* Ignore unusable catalog links. */ }
        }
        if (platform.managed) {
            body.append(this.node('p', 'Esta conexión permite que Iris hable con su motor de IA. Se administra desde la sección de conexión de Iris.', 'dim'));
            card.append(summary, body);
            return card;
        }
        const draft = this.drafts.get(platform.id) || { enabled: platform.enabled, env: {}, clear_env: [] };
        const enable = this.node('label', '', 'messaging-enable');
        const toggle = this.node('input');
        toggle.type = 'checkbox';
        toggle.checked = draft.enabled;
        // An unavailable channel can still be switched off, never on.
        toggle.disabled = !availability.available && !toggle.checked;
        toggle.onchange = () => {
            if (!availability.available && toggle.checked) { toggle.checked = false; return; }
            draft.enabled = toggle.checked;
            this.drafts.set(platform.id, draft);
            toggle.disabled = !availability.available && !toggle.checked;
        };
        enable.append(toggle, this.node('span', 'Activar este canal'));
        body.append(enable);
        const fields = this.node('div', '', 'messaging-fields');
        for (const field of platform.fields) {
            const row = this.node('div', '', 'messaging-field');
            const copy = this.fieldCopy(platform, field);
            const helpId = `messaging-help-${encodeURIComponent(platform.id)}-${encodeURIComponent(field.key)}`;
            const fieldLabel = this.node('label');
            const heading = this.node('span', '', 'messaging-field-heading');
            heading.append(this.node('span', copy.label),
                this.node('span', copy.requirement, 'messaging-field-requirement'));
            fieldLabel.append(heading);
            const input = this.node('input');
            input.type = field.is_password || /(?:TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE_KEY)/i.test(field.key) ? 'password' : 'text';
            input.autocomplete = 'off';
            input.setAttribute('aria-describedby', helpId);
            input.dataset.key = field.key;
            input.value = draft.env[field.key] || '';
            input.placeholder = field.is_set ? 'Credencial guardada · vacío conserva el valor' : field.key;
            // Unavailable channels keep their values readable and clearable only.
            input.disabled = !availability.available;
            input.oninput = () => { draft.env[field.key] = input.value; this.drafts.set(platform.id, draft); };
            fieldLabel.append(input);
            row.append(fieldLabel);
            const help = this.node('small', copy.help);
            help.id = helpId;
            row.append(help);
            if (field.is_set) {
                const clear = this.node('input');
                clear.type = 'checkbox';
                clear.checked = draft.clear_env.includes(field.key);
                input.disabled = clear.checked || !availability.available;
                clear.onchange = () => {
                    draft.clear_env = draft.clear_env.filter(k => k !== field.key);
                    if (clear.checked) { draft.clear_env.push(field.key); delete draft.env[field.key]; input.value = ''; }
                    input.disabled = clear.checked || !availability.available;
                    this.drafts.set(platform.id, draft);
                };
                const clearLabel = this.node('label', '', 'messaging-clear');
                clearLabel.append(clear, this.node('span', 'Borrar el valor guardado'));
                row.append(clearLabel);
            }
            fields.append(row);
        }
        body.append(fields);
        if (!platform.fields.length) body.append(this.node('p',
            'Esta integración no publica campos editables. Su instalación o vinculación puede requerir el procedimiento de su guía.', 'dim'));
        if (platform.id === 'whatsapp' && availability.available) body.append(this.whatsapp());
        const actions = this.node('div', '', 'messaging-toolbar');
        actions.append(this.button('Guardar canal', async () => {
            const result = await this.request(`/platforms/${encodeURIComponent(platform.id)}`, {
                method: 'PUT', body: JSON.stringify(draft),
            });
            this.drafts.delete(platform.id);
            this.saved(result);
            await this.load();
        }, true), this.button('Comprobar estado', async () => {
            const result = await this.request(`/platforms/${encodeURIComponent(platform.id)}/test`, { method: 'POST' });
            this.message(`${platform.name}: ${this.status({ ...platform, state: result.state })}. `
                + (result.ok ? 'El canal informa que está conectado.' : 'La comprobación no envía mensajes.'), false);
            await this.load();
        }));
        body.append(actions);
        card.append(summary, body);
        return card;
    }

    whatsapp() {
        const box = this.node('div', '', 'messaging-whatsapp');
        box.append(this.node('h4', 'Vincular WhatsApp con QR'));
        const mode = this.node('select');
        mode.setAttribute('aria-label', 'Modo de WhatsApp');
        for (const [value, title] of [['self-chat', 'Solo mi chat personal'], ['bot', 'Bot para usuarios autorizados']]) {
            const option = this.node('option', title); option.value = value; mode.append(option);
        }
        const users = this.node('input');
        users.placeholder = 'Usuarios autorizados, separados por coma';
        users.setAttribute('aria-label', 'Usuarios autorizados de WhatsApp');
        mode.value = this.whatsappDraft.mode;
        users.value = this.whatsappDraft.allowed_users;
        mode.onchange = () => { this.whatsappDraft.mode = mode.value; };
        users.oninput = () => { this.whatsappDraft.allowed_users = users.value; };
        const view = this.node('div', '', 'messaging-pairing');
        box.append(mode, users, this.button('Mostrar QR', async () => {
            if (this.pairing) throw new Error('Cancela o guarda la vinculación pendiente antes de empezar otra.');
            const generation = ++this.pairingGeneration;
            const result = await this.request('/whatsapp/pairing', {
                method: 'POST', body: JSON.stringify({ mode: mode.value, allowed_users: users.value }),
            });
            this.pairing = result;
            try { sessionStorage.setItem('iris.whatsapp-pairing', result.pairing_id); } catch { /* optional */ }
            this.renderPairing();
            this.timer = setTimeout(() => this.pollPairing(generation), 1500);
        }), view);
        return box;
    }

    async pollPairing(generation) {
        if (!this.pairing || generation !== this.pairingGeneration) return;
        try {
            const result = await this.request(`/whatsapp/pairing/${encodeURIComponent(this.pairing.pairing_id)}`);
            if (generation !== this.pairingGeneration) return;
            this.pairing = result;
            this.renderPairing();
            if (['installing', 'starting', 'waiting', 'waiting_qr', 'qr', 'pairing'].includes(result.status)) {
                this.timer = setTimeout(() => this.pollPairing(generation), 2000);
            }
        } catch (error) {
            if (generation !== this.pairingGeneration) return;
            this.message(error.message, true);
            if ([404, 410].includes(error.status)) this.forgetPairing();
            else this.renderPairing(true);
        }
    }

    forgetPairing() {
        ++this.pairingGeneration;
        clearTimeout(this.timer);
        this.pairing = null;
        try { sessionStorage.removeItem('iris.whatsapp-pairing'); } catch { /* optional */ }
        this.renderPairing();
    }

    renderPairing(disconnected = false) {
        const view = this.root.querySelector('.messaging-pairing');
        if (!view) return;
        view.replaceChildren();
        const state = this.pairing;
        if (!state) return;
        const label = state.status === 'connected' ? 'Cuenta vinculada. Guarda para activar el canal.'
            : state.error || ({ installing: 'IRIS está preparando los componentes de WhatsApp. La vinculación continuará aquí.',
                starting: 'Preparando la vinculación. El primer inicio puede descargar componentes de mensajería.',
                expired: 'El código QR venció. Cancela esta vinculación y solicita uno nuevo.',
                cancelled: 'Vinculación cancelada.' })[state.status] || 'Escanea el QR desde WhatsApp → Dispositivos vinculados. El primer inicio puede descargar componentes de mensajería.';
        view.append(this.node('p', label));
        if (['waiting', 'waiting_qr', 'qr', 'pairing'].includes(state.status)
            && state.qr_image?.startsWith('data:image/svg+xml;base64,')) {
            const image = this.node('img');
            image.src = state.qr_image; image.alt = 'Código QR para vincular WhatsApp'; image.width = 240;
            view.append(image);
        }
        if (state.status === 'connected') view.append(this.button('Guardar vinculación', async () => {
            const result = await this.request(`/whatsapp/pairing/${encodeURIComponent(state.pairing_id)}/apply`, { method: 'POST' });
            this.forgetPairing(); this.saved(result); await this.load();
        }, true));
        if (disconnected) view.append(this.button('Volver a comprobar', () => this.pollPairing(this.pairingGeneration)));
        view.append(this.button('Cancelar vinculación', async () => {
            await this.request(`/whatsapp/pairing/${encodeURIComponent(state.pairing_id)}`, { method: 'DELETE' });
            this.forgetPairing();
        }));
    }
}

globalThis.IrisMessagingPanel = IrisMessagingPanel;

function setupChannelHandlers() {
    const root = document.getElementById('messaging-panel');
    if (!root || root._messaging) return;
    root._messaging = new IrisMessagingPanel(root);
    root._messaging.mount().catch(error => root._messaging.message(error.message, true));
}
