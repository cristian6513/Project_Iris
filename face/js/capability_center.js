import { irisConfirm } from './iris_dialog.js?v=9';
import { osIconSvg } from './os_icons.js?v=2';
import {
    PERMISSION_MODES,
    groupCapabilities,
    normalizeCapabilitySnapshot,
} from './capability_center_state.js?v=2';

const PRESET_LABELS = Object.freeze({
    strict: 'Privado',
    balanced: 'Equilibrado',
    collaborative: 'Colaborativo',
});

function escapeHtml(value = '') {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function timeLabel(value = '') {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export class CapabilityCenterApp {
    constructor({ root, onSummary = null } = {}) {
        this.root = root || null;
        this.onSummary = onSummary;
        this.state = normalizeCapabilitySnapshot({});
        this.loading = false;
        this.loadedAt = 0;
        this.busy = '';
        this.error = '';
        this.notice = '';
        this.probe = null;
        this._bind();
        this.render();
    }

    _bind() {
        this.root?.addEventListener('click', (event) => this._onClick(event));
        this.root?.addEventListener('submit', (event) => this._onSubmit(event));
    }

    async _json(response, context) {
        if (!response?.ok) {
            let detail = '';
            try {
                const body = await response.json();
                detail = body?.detail || body?.message || body?.error || '';
            } catch {}
            throw new Error(detail || `${context}: HTTP ${response?.status || 'desconocido'}`);
        }
        return response.json();
    }

    async load(force = false) {
        if (this.loading) return;
        if (!force && this.loadedAt && Date.now() - this.loadedAt < 20000) return;
        this.loading = true;
        this.error = '';
        this.render();
        try {
            const response = await fetch(`/api/capability-center${force ? '?force=true' : ''}`, { cache: 'no-store' });
            this._snapshot = await this._json(response, 'Capability Center');
            this.state = normalizeCapabilitySnapshot(this._snapshot);
            this.loadedAt = Date.now();
            this.onSummary?.(`${this.state.counts.ask} requieren confirmación · ${this.state.toolSummary.enabled} tools activas`);
        } catch (error) {
            this.error = error?.message || String(error);
        } finally {
            this.loading = false;
            this.render();
        }
    }

    async _mutate(path, payload, busy) {
        if (this.busy) return null;
        this.busy = busy;
        this.error = '';
        this.notice = '';
        this.render();
        try {
            const response = await fetch(path, {
                method: 'POST',
                cache: 'no-store',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const result = await this._json(response, 'Capability Center');
            this.notice = result.message || 'Cambio aplicado.';
            if (result.policy && this._snapshot) {
                const previous = new Map((this._snapshot.policy?.capabilities || []).map(row => [row.id, row]));
                this._snapshot = { ...this._snapshot, policy: { ...result.policy,
                    capabilities: result.policy.capabilities.map(row => ({ ...previous.get(row.id), ...row })),
                } };
                this.state = normalizeCapabilitySnapshot(this._snapshot);
                this.loadedAt = Date.now();
            } else {
                await this.load(true);
            }
            return result;
        } catch (error) {
            this.error = error?.message || String(error);
            return null;
        } finally {
            this.busy = '';
            this.render();
        }
    }

    async _onClick(event) {
        const sectionButton = event.target?.closest?.('[data-permit-section]');
        if (sectionButton && !sectionButton.disabled) {
            const group = groupCapabilities(this.state.capabilities).find(row => row.id === sectionButton.dataset.permitSection);
            if (!group || this.busy || group.items.every(row => row.mode === 'allow')) return;
            if (!await irisConfirm({ title: `Permitir ${group.label}`, icon: 'shield',
                message: `Se permitirá esta sección: ${group.items.map(row => row.label).join(', ')}. No cambia otras secciones ni las confirmaciones propias de Hermes.`,
                question: '¿Permitir estas capacidades?', confirmLabel: 'Permitir sección',
            })) return;
            await this._mutate('/api/capability-center/section', { section: group.id, mode: 'allow', confirm: true }, `section:${group.id}`);
            return;
        }
        const forget = event.target?.closest?.('[data-forget-action]');
        if (forget && !forget.disabled) {
            await this._mutate('/api/capability-center/action-grant/remove', { id: forget.dataset.forgetAction, confirm: true }, 'forget-action');
            return;
        }
        const refresh = event.target?.closest?.('[data-cap-action="refresh"]');
        if (refresh && !refresh.disabled) {
            await this.load(true);
            return;
        }
        const mode = event.target?.closest?.('[data-capability-mode]');
        if (mode && !mode.disabled) {
            const capability = mode.dataset.capabilityId || '';
            const nextMode = mode.dataset.capabilityMode || '';
            const row = this.state.capabilities.find((item) => item.id === capability);
            if (!row || row.mode === nextMode) return;
            const modeLabel = PERMISSION_MODES[nextMode]?.label || nextMode;
            if (!await irisConfirm({
                title: `${row.label}: ${modeLabel}`, icon: 'shield',
                message: nextMode === 'allow'
                    ? `Permitir ${row.label} no omite Hermes ni activa sensores.`
                    : `${row.label} pasará a «${modeLabel}».`,
                question: nextMode === 'allow' ? '¿Guardar esta política?' : '¿Deseas continuar?',
                confirmLabel: 'Guardar política', confirmIcon: 'check',
            })) return;
            await this._mutate('/api/capability-center/policy', {
                capability,
                mode: nextMode,
                confirm: true,
            }, `policy:${capability}`);
            return;
        }
        const preset = event.target?.closest?.('[data-capability-preset]');
        if (preset && !preset.disabled) {
            const name = preset.dataset.capabilityPreset || '';
            if (!await irisConfirm({
                title: 'Perfil de confianza', icon: 'shield',
                message: `El perfil «${PRESET_LABELS[name] || name}» reemplaza la política de todas las capacidades. Puedes revertirlo después.`,
                question: '¿Aplicarlo?', confirmLabel: 'Aplicar perfil', confirmIcon: 'check',
            })) return;
            await this._mutate('/api/capability-center/preset', { preset: name, confirm: true }, `preset:${name}`);
            return;
        }
        const tool = event.target?.closest?.('[data-hermes-tool]');
        if (tool && !tool.disabled) {
            const name = tool.dataset.hermesTool || '';
            const enabled = tool.dataset.toolEnabled !== 'true';
            if (!await irisConfirm({
                title: `${enabled ? 'Habilitar' : 'Deshabilitar'} herramienta`, icon: 'gear',
                message: `${enabled ? 'Se habilitará' : 'Se deshabilitará'} «${name}» en Hermes CLI.`,
                question: '¿Deseas continuar?', confirmLabel: enabled ? 'Habilitar' : 'Deshabilitar',
            })) return;
            await this._mutate('/api/capability-center/tool', { name, enabled, confirm: true }, `tool:${name}`);
            return;
        }
        const mcp = event.target?.closest?.('[data-mcp-test]');
        if (mcp && !mcp.disabled) {
            const name = mcp.dataset.mcpTest || '';
            const result = await this._mutate('/api/capability-center/probe', {
                action: 'mcp_test',
                value: name,
                confirm: true,
            }, `mcp:${name}`);
            if (result?.probe) this.probe = result.probe;
            this.render();
            return;
        }
        const audit = event.target?.closest?.('[data-cap-action="security-audit"]');
        if (audit && !audit.disabled) {
            if (!await irisConfirm({
                title: 'Auditoría de seguridad', icon: 'shield',
                message: 'Hermes consultará OSV.dev para revisar sus dependencias. Esta consulta sale a internet.',
                question: '¿Deseas continuar?', confirmLabel: 'Revisar dependencias',
            })) return;
            const result = await this._mutate('/api/capability-center/probe', {
                action: 'security_audit',
                value: '',
                confirm: true,
            }, 'security-audit');
            if (result?.probe) this.probe = result.probe;
            this.render();
            return;
        }
        const clear = event.target?.closest?.('[data-cap-action="clear-audit"]');
        if (clear && !clear.disabled) {
            if (!await irisConfirm({
                title: 'Borrar historial', icon: 'trash', tone: 'danger',
                message: 'Se borrará el historial local de decisiones del Capability Center.',
                question: '¿Deseas continuar?', confirmLabel: 'Borrar', confirmIcon: 'trash',
            })) return;
            await this._mutate('/api/capability-center/audit/clear', { confirm: true }, 'clear-audit');
        }
    }

    async _onSubmit(event) {
        const form = event.target?.closest?.('[data-approval-lab]');
        if (!form) return;
        event.preventDefault();
        const command = form.querySelector('input')?.value?.trim() || '';
        if (!command) return;
        const result = await this._mutate('/api/capability-center/probe', {
            action: 'approval_test',
            value: command,
            confirm: true,
        }, 'approval-test');
        if (result?.probe) this.probe = result.probe;
        this.render();
    }

    _permissionCard(item) {
        const gate = item.toolGate || { mapped: 0, enabled: 0, status: 'local' };
        const runtime = gate.status === 'local'
            ? '<span class="cap-runtime neutral">IRIS local</span>'
            : gate.status === 'active'
                ? `<span class="cap-runtime online">CLI ${gate.enabled}/${gate.mapped}</span>`
                : gate.status === 'partial'
                    ? `<span class="cap-runtime partial">CLI ${gate.enabled}/${gate.mapped}</span>`
                    : gate.status === 'unknown'
                        ? `<span class="cap-runtime neutral">CLI ?/${gate.mapped}</span>`
                        : `<span class="cap-runtime offline">CLI 0/${gate.mapped}</span>`;
        return `
            <article class="cap-permission-card" data-mode="${escapeHtml(item.mode)}" data-risk="${escapeHtml(item.risk)}">
                <div class="cap-permission-copy">
                    <div><strong>${escapeHtml(item.label)}</strong>${runtime}</div>
                    <p>${escapeHtml(item.description)}</p>
                    <small>${escapeHtml(item.note)}</small>
                </div>
                <div class="cap-mode-switch" role="group" aria-label="Política para ${escapeHtml(item.label)}">
                    ${Object.entries(PERMISSION_MODES).map(([mode, meta]) => `
                        <button type="button" data-capability-id="${escapeHtml(item.id)}" data-capability-mode="${mode}"
                            class="${item.mode === mode ? 'active' : ''}" aria-pressed="${item.mode === mode ? 'true' : 'false'}"
                            ${this.busy || this.loading ? 'disabled' : ''}>${meta.label}</button>
                    `).join('')}
                </div>
            </article>
        `;
    }

    _toolCard(tool) {
        return `
            <article class="cap-tool-row" data-state="${tool.enabled ? 'enabled' : 'disabled'}">
                <span class="cap-tool-led"></span>
                <div><strong>${escapeHtml(tool.name)}</strong><small>${escapeHtml(tool.description)}</small></div>
                <em>${escapeHtml(tool.source)}</em>
                <button type="button" data-hermes-tool="${escapeHtml(tool.name)}" data-tool-enabled="${tool.enabled ? 'true' : 'false'}"
                    aria-label="${tool.enabled ? 'Deshabilitar' : 'Habilitar'} ${escapeHtml(tool.name)} en Hermes CLI"
                    ${this.busy || this.loading ? 'disabled' : ''}>${tool.enabled ? 'ON' : 'OFF'}</button>
            </article>
        `;
    }

    render() {
        if (!this.root) return;
        const scrolls = [];
        for (let node = this.root; node; node = node.parentElement) scrolls.push([node, node.scrollTop, node.scrollLeft]);
        const innerScrolls = [...(this.root.querySelectorAll?.('[class]') || [])]
            .filter(node => node.scrollTop || node.scrollLeft).map(node => ({ className: node.className, top: node.scrollTop, left: node.scrollLeft }));
        const active = globalThis.document?.activeElement;
        if (active && this.root.contains?.(active)) {
            const attrs = ['id', 'data-capability-id', 'data-capability-mode', 'data-permit-section', 'data-cap-action', 'data-forget-action', 'data-hermes-tool', 'data-mcp-test'];
            this._focusRestore = { tag: active.tagName, attrs: attrs.map(name => [name, active.getAttribute(name)]).filter(([, value]) => value !== null) };
        }
        const { counts, hermes, guards, tools, toolSummary, mcp, audit } = this.state;
        const groups = groupCapabilities(this.state.capabilities);
        const hermesState = hermes.reachable
            ? { key: 'online', label: 'GATEWAY ONLINE', detail: hermes.version }
            : hermes.installed && hermes.tools_available
                ? { key: 'ready', label: 'CLI READY', detail: `${hermes.version || 'Hermes instalado'} · Gateway detenido` }
                : hermes.installed
                    ? { key: 'limited', label: 'LIMITADO', detail: hermes.version || 'CLI detectado' }
                    : { key: 'missing', label: 'NO INSTALADO', detail: 'Hermes no detectado' };
        const status = this.loading
            ? 'Sincronizando políticas y enforcement...'
            : this.error || this.notice || 'IRIS aplica política local; Hermes conserva el control de ejecución.';
        const probe = this.probe ? `
            <div class="cap-probe-result" role="status" aria-live="polite" data-verdict="${escapeHtml(this.probe.verdict || (this.probe.success ? 'allow' : 'deny'))}">
                <strong>${escapeHtml(this.probe.verdict || (this.probe.success ? 'Completado' : 'Error'))}</strong>
                <p>${escapeHtml(this.probe.detail || this.probe.message || 'Sin detalle adicional.')}</p>
                ${this.probe.rule ? `<small>Regla: ${escapeHtml(this.probe.rule)}</small>` : ''}
            </div>
        ` : '';
        this.root.innerHTML = `
            <div class="capability-center" data-loading="${this.loading ? 'true' : 'false'}">
                <section class="cap-hero">
                    <div class="cap-hero-mark" aria-hidden="true"><span></span><i></i><b></b></div>
                    <div class="cap-hero-copy">
                        <span>LOCAL TRUST FABRIC · HERMES ENFORCEMENT</span>
                        <h2>Permisos</h2>
                        <p>Decide qué puede proponer o iniciar IRIS y comprueba los permisos de su motor de IA.</p>
                    </div>
                    <div class="cap-hero-state" data-state="${hermesState.key}">
                        <small>HERMES</small><strong>${hermesState.label}</strong>
                        <span>${escapeHtml(hermesState.detail)}</span>
                    </div>
                </section>
                <section class="cap-metrics" aria-label="Resumen de permisos">
                    <article data-tone="deny"><span>Bloqueadas</span><strong>${counts.deny}</strong></article>
                    <article data-tone="ask"><span>Preguntan</span><strong>${counts.ask}</strong></article>
                    <article data-tone="allow"><span>Permitidas</span><strong>${counts.allow}</strong></article>
                    <article data-tone="tools"><span>Herramientas de IA</span><strong>${toolSummary.enabled}/${toolSummary.total}</strong></article>
                </section>
                <div class="cap-status ${this.error ? 'error' : ''}" role="status" aria-live="polite"><span></span>${escapeHtml(status)}<button type="button" data-cap-action="refresh" ${this.loading || this.busy ? 'disabled' : ''}>Sincronizar</button></div>
                <section class="cap-presets">
                    <div><strong>Perfiles de confianza</strong><small>Reemplazan la política completa y pueden revertirse.</small></div>
                    <div>${this.state.presets.map((name) => `<button type="button" data-capability-preset="${escapeHtml(name)}" ${this.busy || this.loading ? 'disabled' : ''}>${escapeHtml(PRESET_LABELS[name] || name)}</button>`).join('')}</div>
                </section>
                <div class="cap-grid">
                    <main class="cap-policy-plane">
                        ${groups.map((group) => `
                            <section class="cap-group">
                                <header><span>${escapeHtml(group.label)}</span><small>${group.items.length} capacidades</small><button type="button" data-permit-section="${escapeHtml(group.id)}" aria-disabled="${group.items.every(row => row.mode === 'allow')}" ${this.busy || this.loading ? 'disabled' : ''}>${osIconSvg('shield', { className: 'cap-section-icon' })} Permitir sección</button></header>
                                <div>${group.items.map((item) => this._permissionCard(item)).join('')}</div>
                            </section>
                        `).join('') || '<div class="cap-empty">No se pudo cargar la política local.</div>'}
                    </main>
                    <aside class="cap-enforcement-plane">
                        <section class="cap-guard-stack">
                            <header><span>Hermes Guard Stack</span><strong>NATIVO</strong></header>
                            <article><i data-state="${guards.approvals?.available ? 'on' : 'off'}"></i><div><strong>Approvals</strong><small>${escapeHtml(guards.approvals?.detail || 'No detectado')}</small></div></article>
                            <article><i data-state="${guards.egress?.enabled ? 'on' : 'off'}"></i><div><strong>Egress firewall</strong><small>${guards.egress?.enabled ? 'Activo' : 'Desactivado por defecto'} · ${escapeHtml(guards.egress?.process || 'unknown')}</small></div></article>
                            <article><i data-state="${guards.security_audit?.available ? 'standby' : 'off'}"></i><div><strong>Supply-chain audit</strong><small>${escapeHtml(guards.security_audit?.detail || 'No disponible')}</small></div><button type="button" data-cap-action="security-audit" ${!guards.security_audit?.available || this.busy || this.loading ? 'disabled' : ''}>Ejecutar</button></article>
                        </section>
                        <section class="cap-tools-panel">
                            <header><div><span>Toolsets · CLI</span><small>Estado oficial de hermes tools list</small></div><strong>${toolSummary.enabled} ON</strong></header>
                            <div class="cap-tool-list">${tools.map((tool) => this._toolCard(tool)).join('') || '<div class="cap-empty">Hermes no entregó el inventario de tools.</div>'}</div>
                        </section>
                        <section class="cap-mcp-panel">
                            <header><span>MCP configurados</span><strong>${mcp.enabledCount}/${mcp.installedCount}</strong></header>
                            <div>${mcp.installed.map((item) => `
                                <article><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.transport || 'managed')} · ${escapeHtml(item.auth || 'unknown')}</small></div><button type="button" data-mcp-test="${escapeHtml(item.name)}" aria-label="Probar conexión MCP ${escapeHtml(item.name)}" ${this.busy || this.loading ? 'disabled' : ''}>Probar</button></article>
                            `).join('') || `<p>${mcp.available ? 'Sin servidores MCP configurados.' : 'No se pudo consultar el inventario MCP.'}</p>`}</div>
                        </section>
                    </aside>
                </div>
                <section class="cap-decision-lab">
                    <div class="cap-lab-copy"><span>DECISION LAB</span><strong>Prueba un comando sin ejecutarlo</strong><p>Usa el guard real de Hermes y muestra allow, ask o deny.</p></div>
                    <form data-approval-lab><input type="text" maxlength="320" placeholder="git status --short" aria-label="Comando para evaluar sin ejecutarlo" autocomplete="off"><button type="submit" ${this.busy || this.loading ? 'disabled' : ''}>Evaluar</button></form>
                    ${probe}
                </section>
                <section class="cap-audit-panel cap-remembered">
                    <header><div><span>Permisos recordados</span><small>Solo la operación y el alcance que aprobaste.</small></div></header>
                    <div>${this.state.actionGrants.map(row => `<article><strong>${escapeHtml(row.app)}</strong><span>${escapeHtml(row.label || row.action)}</span><button type="button" data-forget-action="${escapeHtml(row.id)}" ${this.busy ? 'disabled' : ''}>Volver a preguntar</button></article>`).join('') || '<p class="cap-empty">No hay operaciones recordadas.</p>'}</div>
                </section>
                <section class="cap-audit-panel">
                    <header><div><span>Ledger local</span><small>Sin prompts, comandos ni secretos persistidos.</small></div><button type="button" data-cap-action="clear-audit" ${!audit.length || this.busy || this.loading ? 'disabled' : ''}>Limpiar</button></header>
                    <div>${audit.slice(0, 24).map((item) => `
                        <article><time>${escapeHtml(timeLabel(item.at))}</time><strong>${escapeHtml(item.kind)}</strong><span>${escapeHtml(item.capability || item.verdict)}</span><p>${escapeHtml(item.detail)}</p></article>
                    `).join('') || '<p class="cap-empty">Aún no hay decisiones registradas.</p>'}</div>
                </section>
                <footer class="cap-invariants"><span>NO ACTIVA SENSORES</span><span>NO GUARDA SECRETOS</span><span>NO EVITA APPROVALS</span><span>ADMINISTRACIÓN SOLO LOCAL</span></footer>
            </div>
        `;
        for (const [node, top, left] of scrolls) { node.scrollTop = top; node.scrollLeft = left; }
        for (const saved of innerScrolls) {
            const node = [...(this.root.querySelectorAll?.('[class]') || [])].find(node => node.className === saved.className);
            if (node) { node.scrollTop = saved.top; node.scrollLeft = saved.left; }
        }
        if (!this.busy && !this.loading && this._focusRestore) {
            const saved = this._focusRestore;
            const node = [...(this.root.querySelectorAll?.('button,input,select') || [])].find(node =>
                node.tagName === saved.tag && saved.attrs.every(([name, value]) => node.getAttribute(name) === value));
            if (node && !node.disabled && (!globalThis.document?.activeElement || globalThis.document.activeElement === document.body || this.root.contains?.(document.activeElement))) node.focus?.({ preventScroll: true });
            this._focusRestore = null;
        }
    }
}
