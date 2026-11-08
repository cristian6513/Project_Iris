import {
    continuityDefaultProfile,
    continuityModeCopy,
    continuityTimeLabel,
    filterContinuitySessions,
    normalizeContinuitySnapshot,
} from './continuity_state.js?v=4';

const escapeHtml = (value = '') => String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');

export class ContinuityCenterApp {
    constructor({ root = null, fetchImpl = globalThis.fetch?.bind(globalThis) } = {}) {
        this.root = root;
        this.fetchImpl = fetchImpl;
        this.snapshot = normalizeContinuitySnapshot();
        this.loading = false;
        this.notice = '';
        this.error = '';
        this.query = '';
        this.profile = 'all';
        this.workspace = 'all';
        this.selectedSessionId = '';
        this.createOpen = false;
        this.busyAction = '';
        this._loaded = false;
        this._bound = false;
        this._bind();
        this.render();
    }

    _bind() {
        if (!this.root || this._bound) return;
        this._bound = true;
        this.root.addEventListener('click', (event) => this._handleClick(event));
        this.root.addEventListener('input', (event) => {
            if (!event.target?.matches?.('[data-continuity-search]')) return;
            this.query = event.target.value || '';
            this._ensureSelectedSession();
            this._renderSessionRegion();
        });
        this.root.addEventListener('submit', (event) => {
            const form = event.target?.closest?.('[data-continuity-create-form]');
            if (!form) return;
            event.preventDefault();
            this._createProfile(new FormData(form));
        });
    }

    async _request(path, options = {}) {
        if (!this.fetchImpl) throw new Error('La red local de IRIS no está disponible.');
        const response = await this.fetchImpl(path, {
            cache: 'no-store',
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
            ...options,
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = payload?.detail;
            const message = Array.isArray(detail)
                ? detail.map((item) => item?.msg || '').filter(Boolean).join(' · ')
                : detail || payload?.error || payload?.message;
            throw new Error(message || `IRIS respondió ${response.status}.`);
        }
        return payload;
    }

    async load(force = false) {
        if (this.loading || (this._loaded && !force)) return;
        this.loading = true;
        this.error = '';
        this.render();
        try {
            const payload = await this._request(`/api/hermes/continuity?limit=200${force ? '&force=true' : ''}`);
            this.snapshot = normalizeContinuitySnapshot(payload);
            this.profile = continuityDefaultProfile(this.snapshot, this.profile);
            this.workspace = this.snapshot.workspaces.some(({ id }) => id === this.workspace)
                ? this.workspace
                : 'all';
            this.selectedSessionId = this.snapshot.sessions.some(({ id }) => id === this.selectedSessionId)
                ? this.selectedSessionId
                : this.snapshot.sessions.find(({ isCurrent }) => isCurrent)?.id
                    || this.snapshot.sessions[0]?.id
                    || '';
            this._ensureSelectedSession();
            this._loaded = true;
        } catch (error) {
            this.error = error?.message || 'No pude leer la continuidad de Hermes.';
        } finally {
            this.loading = false;
            this.render();
        }
    }

    _modeMarkup() {
        if (this.loading && !this._loaded) {
            return '<span class="continuity-mode" data-tone="partial"><i></i>Consultando perfiles</span>';
        }
        const mode = continuityModeCopy(this.snapshot.mode);
        return `<span class="continuity-mode" data-tone="${mode.tone}"><i></i>${escapeHtml(mode.label)}</span>`;
    }

    _profileMarkup() {
        if (!this.snapshot.profiles.length) {
            const reason = this.snapshot.profileScope === 'remote'
                ? 'Este Gateway es remoto; IRIS no mostrará perfiles del CLI local.'
                : 'Hermes CLI no respondió o todavía no está instalado.';
            return `<div class="continuity-empty"><strong>Sin perfiles visibles</strong><span>${reason}</span></div>`;
        }
        return this.snapshot.profiles.map((profile) => {
            const selected = this.profile === profile.name;
            const model = profile.model || 'Modelo pendiente';
            const activate = profile.active ? '' : `
                <button type="button" data-continuity-activate="${escapeHtml(profile.name)}">
                    Usar al reiniciar
                </button>`;
            return `
                <article class="continuity-profile ${selected ? 'selected' : ''}" data-profile="${escapeHtml(profile.name)}">
                    <button class="continuity-profile-main" type="button" data-continuity-profile="${escapeHtml(profile.name)}">
                        <span class="continuity-profile-sigil">${escapeHtml(profile.name.slice(0, 2).toUpperCase())}</span>
                        <span>
                            <strong>${escapeHtml(profile.name)}</strong>
                            <small>${escapeHtml(model)}</small>
                        </span>
                        ${profile.active ? '<em>ACTIVO</em>' : ''}
                    </button>
                    <div class="continuity-profile-meta">
                        <span>${escapeHtml(profile.gateway || 'unknown')}</span>
                        ${profile.distribution ? `<span>${escapeHtml(profile.distribution)}</span>` : ''}
                        ${activate}
                    </div>
                </article>`;
        }).join('');
    }

    _workspaceMarkup() {
        const profileWorkspaces = this.snapshot.workspaces.filter((workspace) => (
            this.profile === 'all' || workspace.profile === this.profile
        ));
        if (!profileWorkspaces.length) {
            return `
                <button type="button" class="active" data-continuity-workspace="all">
                    <strong>Conversaciones</strong>
                    <span>Esta versión no expone carpetas por la API segura.</span>
                </button>`;
        }
        return [
            `<button type="button" class="${this.workspace === 'all' ? 'active' : ''}" data-continuity-workspace="all"><strong>Todos</strong><span>${profileWorkspaces.length} contextos</span></button>`,
            ...profileWorkspaces.map((workspace) => `
                <button type="button" class="${this.workspace === workspace.id ? 'active' : ''}" data-continuity-workspace="${escapeHtml(workspace.id)}">
                    <strong>${escapeHtml(workspace.label)}</strong>
                    <span>${workspace.sessionCount} sesiones · ${escapeHtml(workspace.profile)}</span>
                </button>`),
        ].join('');
    }

    _filteredSessions() {
        return filterContinuitySessions(this.snapshot.sessions, {
            query: this.query,
            profile: this.profile,
            workspace: this.workspace,
            includeArchived: true,
        });
    }

    _ensureSelectedSession() {
        const visible = this._filteredSessions();
        if (!visible.some(({ id }) => id === this.selectedSessionId)) {
            this.selectedSessionId = visible[0]?.id || '';
        }
    }

    _sessionsMarkup() {
        const sessions = this._filteredSessions();
        if (!this.snapshot.supports.sessions) {
            const recovery = this.snapshot.apiReachable
                ? 'Actualiza Hermes si necesitas una versión que anuncie esta capacidad.'
                : 'Pulsa «Iniciar IRIS» para habilitar esta vista.';
            return `<div class="continuity-empty"><strong>Gateway sin Session API</strong><span>Continuity Center no leerá state.db como atajo. ${recovery}</span></div>`;
        }
        if (!sessions.length) {
            const selectedProfile = this.snapshot.profiles.find(({ name }) => name === this.profile);
            if (
                selectedProfile
                && this.snapshot.gatewayProfile
                && selectedProfile.name !== this.snapshot.gatewayProfile
            ) {
                return '<div class="continuity-empty"><strong>Perfil fuera del Gateway activo</strong><span>Actívalo y reinicia el Gateway para consultar sus sesiones sin cruzar credenciales ni memoria.</span></div>';
            }
            return '<div class="continuity-empty"><strong>No hay coincidencias</strong><span>Cambia los filtros o inicia una conversación con IRIS.</span></div>';
        }
        const rows = sessions.map((session) => `
            <button type="button" class="continuity-session ${session.id === this.selectedSessionId ? 'selected' : ''}" data-continuity-session="${escapeHtml(session.id)}">
                <span class="continuity-session-line"><i></i></span>
                <span class="continuity-session-copy">
                    <span>
                        <strong>${escapeHtml(session.title)}</strong>
                        ${session.isCurrent ? '<em>AHORA</em>' : ''}
                        ${session.pinned ? '<em>FIJADA</em>' : ''}
                        ${session.archived ? '<em>ARCHIVADA EN HERMES</em>' : ''}
                    </span>
                    <small>${escapeHtml(session.preview || 'Sin vista previa guardada')}</small>
                    <span class="continuity-session-meta">
                        <b>${escapeHtml(continuityTimeLabel(session.lastActive))}</b>
                        <b>${session.messageCount} mensajes</b>
                        <b>${escapeHtml(session.source)}</b>
                    </span>
                </span>
            </button>`).join('');
        const more = this.snapshot.hasMore
            ? '<div class="continuity-more">Hermes tiene más sesiones. Se muestran las 200 más recientes y las fijadas.</div>'
            : '';
        return rows + more;
    }

    _detailMarkup() {
        const session = this.snapshot.sessions.find(({ id }) => id === this.selectedSessionId);
        if (!session) {
            return `
                <div class="continuity-detail-empty">
                    <span class="continuity-orbit-mini"><i></i><i></i><i></i></span>
                    <strong>Selecciona una sesión</strong>
                    <p>IRIS mostrará únicamente metadatos seguros entregados por Hermes.</p>
                </div>`;
        }
        const canUpdate = this.snapshot.supports.sessionUpdate;
        const canFork = this.snapshot.supports.sessionFork
            && this.snapshot.supports.resumeInIris
            && !session.archived;
        const canResume = this.snapshot.supports.resumeInIris && !session.archived;
        const isCurrent = session.isCurrent;
        const resumeCopy = session.archived
            ? 'Hermes conserva esta sesión archivada. Restáurala en una superficie compatible antes de continuar.'
            : canResume
                ? 'IRIS reemplazará el chat visible y dirigirá los próximos turnos al transcript que conserva Hermes.'
                : 'El Gateway debe anunciar historial de mensajes y continuidad autenticada para evitar mezclar conversaciones.';
        return `
            <div class="continuity-detail-head">
                <span>${escapeHtml(session.profile)} / ${escapeHtml(session.source)}</span>
                <strong>${escapeHtml(session.title)}</strong>
                <p>${escapeHtml(session.preview || 'Hermes no guardó una vista previa para esta sesión.')}</p>
            </div>
            <dl class="continuity-ledger">
                <div><dt>Modelo</dt><dd>${escapeHtml(session.model || 'No registrado')}</dd></div>
                <div><dt>Mensajes</dt><dd>${session.messageCount}</dd></div>
                <div><dt>Tokens</dt><dd>${session.inputTokens + session.outputTokens}</dd></div>
                <div><dt>Contexto</dt><dd>${escapeHtml(session.workspaceLabel || 'No expuesto')}</dd></div>
            </dl>
            <div class="continuity-detail-actions">
                <button type="button" data-continuity-copy-id="${escapeHtml(session.id)}">Copiar ID</button>
                <button type="button" data-continuity-session-action="rename" ${canUpdate ? '' : 'disabled'}>Renombrar</button>
                <button type="button" data-continuity-session-action="pin" ${canUpdate ? '' : 'disabled'}>${session.pinned ? 'Desfijar' : 'Fijar'}</button>
                <button type="button" data-continuity-fork ${canFork ? '' : 'disabled'}>Crear rama</button>
            </div>
            <div class="continuity-resume-boundary">
                <strong>Continuar dentro de IRIS</strong>
                <p>${escapeHtml(resumeCopy)}</p>
                <button type="button" data-continuity-resume ${canResume && !isCurrent && !this.busyAction ? '' : 'disabled'}>${isCurrent ? 'Conversación activa' : this.busyAction === 'resume' ? 'Cambiando contexto…' : 'Reanudar de forma segura'}</button>
            </div>`;
    }

    _createMarkup() {
        const sources = this.snapshot.profiles.map(({ name }) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
        return `
            <form class="continuity-create ${this.createOpen ? 'open' : ''}" data-continuity-create-form>
                <header><strong>Nuevo perfil Hermes</strong><button type="button" data-continuity-create-close aria-label="Cerrar creación">x</button></header>
                <label>Nombre<input name="name" required maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,63}" placeholder="trabajo"></label>
                <label>Descripción<input name="description" maxlength="280" placeholder="Qué debe hacer este perfil"></label>
                <label>Base<select name="clone_mode"><option value="blank">Perfil nuevo — requiere setup</option><option value="config">Config + identidad + skills + memoria + .env</option><option value="all">Estado completo (sin historial)</option></select></label>
                <label>Perfil de origen<select name="clone_from">${sources}</select></label>
                <p>Los modos clonados pueden copiar API keys, identidad y memoria. Hermes realiza la copia y excluye sesiones, state.db y backups; IRIS no abre esos archivos.</p>
                <button type="submit">Crear con Hermes</button>
            </form>`;
    }

    render() {
        if (!this.root) return;
        const counts = this.snapshot.counts || {
            profiles: this.snapshot.profiles.length,
            sessions: this.snapshot.sessions.length,
            workspaces: this.snapshot.workspaces.length,
        };
        this.root.innerHTML = `
            <section class="continuity-surface" data-mode="${escapeHtml(this.snapshot.mode)}">
                <header class="continuity-hero">
                    <div class="continuity-orbit" aria-hidden="true"><i></i><i></i><i></i><b></b></div>
                    <div class="continuity-title">
                        <span>IRIS / HERMES CONTEXT FABRIC</span>
                        <strong>Perfiles y sesiones</strong>
                        <p>Cambia de propósito sin mezclar memoria, sesiones ni personalidad.</p>
                    </div>
                    <div class="continuity-stats">
                        <article><span>Perfiles</span><strong>${counts.profiles ?? this.snapshot.profiles.length}</strong></article>
                        <article><span>Sesiones</span><strong>${counts.sessions ?? this.snapshot.sessions.length}</strong></article>
                        <article><span>Workspaces</span><strong>${counts.workspaces ?? this.snapshot.workspaces.length}</strong></article>
                    </div>
                    <div class="continuity-hero-actions">
                        ${this._modeMarkup()}
                        <button type="button" data-continuity-refresh ${this.loading ? 'disabled' : ''}>${this.loading ? 'Leyendo perfiles…' : 'Sincronizar'}</button>
                        <button type="button" data-continuity-session-create ${this.snapshot.supports.sessionCreate && this.snapshot.supports.resumeInIris && !this.busyAction ? '' : 'disabled'}>Nueva conversación</button>
                        <button type="button" data-continuity-create-open ${this.snapshot.supports.profiles ? '' : 'disabled'}>Nuevo perfil</button>
                    </div>
                </header>
                <div class="continuity-notice ${this.error || this.snapshot.error ? 'error' : ''}">
                    <strong>${escapeHtml(this.notice || this.error || this.snapshot.error || this.snapshot.notice || 'Preparando Continuity Center…')}</strong>
                    <span>${escapeHtml(this.snapshot.hermesVersion || 'Hermes sin versión detectada')} · Perfil CLI: ${escapeHtml(this.snapshot.activeProfile)}${this.snapshot.gatewayProfile ? ` · Gateway: ${escapeHtml(this.snapshot.gatewayProfile)}` : ''}</span>
                </div>
                <div class="continuity-grid">
                    <aside class="continuity-profiles">
                        <header><span>01</span><div><strong>Perfiles</strong><small>Aislamiento nativo de Hermes</small></div></header>
                        <div class="continuity-profile-list">${this._profileMarkup()}</div>
                    </aside>
                    <main class="continuity-journey">
                        <header class="continuity-journey-tools">
                            <label><span>Buscar continuidad</span><input type="search" data-continuity-search value="${escapeHtml(this.query)}" placeholder="Título, modelo o actividad"></label>
                        </header>
                        <nav class="continuity-workspaces" aria-label="Contextos de trabajo">${this._workspaceMarkup()}</nav>
                        <div class="continuity-session-list">${this._sessionsMarkup()}</div>
                    </main>
                    <aside class="continuity-detail">${this._detailMarkup()}</aside>
                </div>
                <footer class="continuity-doctrine">
                    <span>FUENTE DE VERDAD</span>
                    <strong>Hermes conserva perfiles, memoria y sesiones.</strong>
                    <p>IRIS organiza la experiencia y aplica el tema; no abre ni replica la base de datos de Hermes.</p>
                </footer>
                ${this._createMarkup()}
            </section>`;
    }

    _renderSessionRegion() {
        const list = this.root?.querySelector('.continuity-session-list');
        const detail = this.root?.querySelector('.continuity-detail');
        if (list) list.innerHTML = this._sessionsMarkup();
        if (detail) detail.innerHTML = this._detailMarkup();
    }

    async _handleClick(event) {
        const refresh = event.target?.closest?.('[data-continuity-refresh]');
        if (refresh) return this.load(true);
        if (event.target?.closest?.('[data-continuity-create-open]')) {
            this.createOpen = true;
            this.render();
            this.root?.querySelector('[data-continuity-create-form] input')?.focus();
            return;
        }
        if (event.target?.closest?.('[data-continuity-create-close]')) {
            this.createOpen = false;
            this.render();
            return;
        }
        const profileButton = event.target?.closest?.('[data-continuity-profile]');
        if (profileButton) {
            this.profile = profileButton.dataset.continuityProfile || 'all';
            this.workspace = 'all';
            const first = this._filteredSessions()[0];
            this.selectedSessionId = first?.id || '';
            this.render();
            return;
        }
        const workspaceButton = event.target?.closest?.('[data-continuity-workspace]');
        if (workspaceButton) {
            this.workspace = workspaceButton.dataset.continuityWorkspace || 'all';
            const first = this._filteredSessions()[0];
            this.selectedSessionId = first?.id || '';
            this.render();
            return;
        }
        const sessionButton = event.target?.closest?.('[data-continuity-session]');
        if (sessionButton) {
            this.selectedSessionId = sessionButton.dataset.continuitySession || '';
            this._renderSessionRegion();
            return;
        }
        const activate = event.target?.closest?.('[data-continuity-activate]');
        if (activate) return this._activateProfile(activate.dataset.continuityActivate || '');
        const copy = event.target?.closest?.('[data-continuity-copy-id]');
        if (copy) return this._copySessionId(copy.dataset.continuityCopyId || '');
        const action = event.target?.closest?.('[data-continuity-session-action]');
        if (action && !action.disabled) return this._updateSession(action.dataset.continuitySessionAction || '');
        const resume = event.target?.closest?.('[data-continuity-resume]');
        if (resume && !resume.disabled) return this._resumeSession();
        const createSession = event.target?.closest?.('[data-continuity-session-create]');
        if (createSession && !createSession.disabled) return this._createSession();
        const fork = event.target?.closest?.('[data-continuity-fork]');
        if (fork && !fork.disabled) return this._forkSession();
    }

    async _activateProfile(name) {
        if (!name || !globalThis.confirm?.(`Hermes usará “${name}” en próximos procesos. El Gateway actual deberá reiniciarse. ¿Continuar?`)) return;
        this.notice = `Activando ${name} en Hermes…`;
        this.render();
        try {
            const result = await this._request('/api/hermes/continuity/profiles/activate', {
                method: 'POST',
                body: JSON.stringify({ name, confirm: true }),
            });
            this.notice = result.message || `Perfil ${name} activado.`;
            await this.load(true);
        } catch (error) {
            this.error = error?.message || 'No pude activar el perfil.';
            this.render();
        }
    }

    async _createProfile(formData) {
        const payload = {
            name: String(formData.get('name') || ''),
            description: String(formData.get('description') || ''),
            clone_mode: String(formData.get('clone_mode') || 'blank'),
            clone_from: String(formData.get('clone_from') || 'default'),
        };
        this.notice = `Hermes está creando ${payload.name || 'el perfil'}…`;
        this.error = '';
        this.render();
        try {
            const result = await this._request('/api/hermes/continuity/profiles', {
                method: 'POST',
                body: JSON.stringify(payload),
            });
            this.createOpen = false;
            this.notice = result.message || 'Perfil creado por Hermes.';
            await this.load(true);
        } catch (error) {
            this.error = error?.message || 'No pude crear el perfil.';
            this.createOpen = true;
            this.render();
        }
    }

    async _copySessionId(id) {
        try {
            await navigator.clipboard.writeText(id);
            this.notice = 'ID de sesión copiado. Hermes puede reanudarlo con --resume.';
        } catch {
            this.notice = `ID de sesión: ${id}`;
        }
        this.render();
    }

    _publishResumedSession(result) {
        const requestedId = result?.requested_session_id || result?.session_id || '';
        if (requestedId) this.selectedSessionId = requestedId;
        globalThis.dispatchEvent?.(new CustomEvent('IRIS:continuity-session-resumed', {
            detail: {
                sessionId: result?.session_id || requestedId,
                requestedSessionId: requestedId,
                title: result?.title || 'Sesión de Hermes',
                messages: Array.isArray(result?.messages) ? result.messages : [],
            },
        }));
    }

    async _resumeSession() {
        const session = this.snapshot.sessions.find(({ id }) => id === this.selectedSessionId);
        if (!session || session.isCurrent || this.busyAction) return;
        if (!globalThis.confirm?.(`IRIS reemplazará el chat visible por “${session.title}”. La conversación actual seguirá guardada en Hermes. ¿Continuar?`)) return;
        this.busyAction = 'resume';
        this.error = '';
        this.notice = `Hermes está abriendo ${session.title}…`;
        this.render();
        try {
            const result = await this._request(`/api/hermes/continuity/sessions/${encodeURIComponent(session.id)}/resume`, {
                method: 'POST',
                body: JSON.stringify({ confirm: true }),
            });
            this._publishResumedSession(result);
            this.notice = result.message || 'Conversación reanudada por Hermes.';
            await this.load(true);
        } catch (error) {
            this.error = error?.message || 'No pude reanudar la conversación.';
        } finally {
            this.busyAction = '';
            this.render();
        }
    }

    async _createSession() {
        if (this.busyAction) return;
        const title = globalThis.prompt?.('Nombre opcional para la nueva conversación:', '');
        if (title == null) return;
        this.busyAction = 'create';
        this.error = '';
        this.notice = 'Hermes está creando una conversación limpia…';
        this.render();
        try {
            const result = await this._request('/api/hermes/continuity/sessions', {
                method: 'POST',
                body: JSON.stringify({ title: title.trim(), confirm: true }),
            });
            this._publishResumedSession(result);
            this.notice = result.message || 'Nueva conversación abierta.';
            await this.load(true);
        } catch (error) {
            this.error = error?.message || 'No pude crear la conversación.';
        } finally {
            this.busyAction = '';
            this.render();
        }
    }

    async _forkSession() {
        const session = this.snapshot.sessions.find(({ id }) => id === this.selectedSessionId);
        if (!session || this.busyAction) return;
        const title = globalThis.prompt?.('Nombre opcional para la nueva rama:', '');
        if (title == null) return;
        if (!globalThis.confirm?.('Hermes copiará el transcript a una rama nueva. La conversación original se conserva. ¿Crear y abrir la rama?')) return;
        this.busyAction = 'fork';
        this.error = '';
        this.notice = `Hermes está ramificando ${session.title}…`;
        this.render();
        try {
            const result = await this._request(`/api/hermes/continuity/sessions/${encodeURIComponent(session.id)}/fork`, {
                method: 'POST',
                body: JSON.stringify({ title: title.trim(), confirm: true }),
            });
            this._publishResumedSession(result);
            this.notice = result.message || 'Rama creada y abierta.';
            await this.load(true);
        } catch (error) {
            this.error = error?.message || 'No pude crear la rama.';
        } finally {
            this.busyAction = '';
            this.render();
        }
    }

    async _updateSession(action) {
        const session = this.snapshot.sessions.find(({ id }) => id === this.selectedSessionId);
        if (!session) return;
        let body = {};
        if (action === 'rename') {
            const title = globalThis.prompt?.('Nuevo título de la sesión:', session.title);
            if (title == null || !title.trim() || title.trim() === session.title) return;
            body = { title: title.trim() };
        } else if (action === 'pin') {
            body = { pinned: !session.pinned };
        } else {
            return;
        }
        this.notice = 'Hermes está actualizando la sesión…';
        this.error = '';
        this.render();
        try {
            const result = await this._request(`/api/hermes/continuity/sessions/${encodeURIComponent(session.id)}`, {
                method: 'PATCH',
                body: JSON.stringify(body),
            });
            this.notice = result.message || 'Sesión actualizada por Hermes.';
            await this.load(true);
        } catch (error) {
            this.error = error?.message || 'No pude actualizar la sesión.';
            this.render();
        }
    }
}
