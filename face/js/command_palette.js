/**
 * IRIS OS command palette + Hermes slash autocomplete.
 *
 * The catalogue is lazy-loaded from the installed Hermes registry the first
 * time the user types `/` or presses Ctrl/Cmd+K. Unsupported upstream
 * commands stay visible with a terminal-only badge; they are never forwarded
 * to the language model as ordinary chat.
 */

const FALLBACK_COMMANDS = [
    { name: 'help', command: '/help', description: 'Explorar comandos de Hermes e IRIS OS.', category: 'Info', source: 'Hermes', execution: 'client', action: 'palette', available: true, availability: 'Disponible', aliases: [], subcommands: [] },
    { name: 'apps', command: '/apps', description: 'Abrir el lanzador de aplicaciones.', category: 'IRIS OS', source: 'IRIS', execution: 'client', action: 'apps', available: true, availability: 'Disponible', aliases: [], subcommands: [] },
    { name: 'settings', command: '/settings', description: 'Abrir la configuración avanzada.', category: 'IRIS OS', source: 'IRIS', execution: 'client', action: 'settings', available: true, availability: 'Disponible', aliases: [], subcommands: [] },
    { name: 'theme', command: '/theme', description: 'Abrir Theme Studio.', category: 'IRIS OS', source: 'IRIS', execution: 'client', action: 'theme', available: true, availability: 'Disponible', aliases: [], subcommands: [] },
];

function commandSearchText(item) {
    return [
        item.name,
        ...(item.aliases || []),
        item.description,
        item.category,
        item.source,
        ...(item.subcommands || []),
    ].join(' ').toLocaleLowerCase('es');
}

function tokenMatch(item, rawQuery) {
    const tokens = String(rawQuery || '').toLocaleLowerCase('es').trim().split(/\s+/).filter(Boolean);
    if (!tokens.length) return true;
    const haystack = commandSearchText(item);
    return tokens.every((token) => haystack.includes(token.replace(/^\//, '')));
}

export class IrisCommandPalette {
    constructor({ input, composer, onExecute = null, onNotice = null } = {}) {
        this.input = input || null;
        this.composer = composer || null;
        this.onExecute = onExecute;
        this.onNotice = onNotice;
        this.commands = [...FALLBACK_COMMANDS];
        this._loaded = false;
        this._loading = null;
        this._loadedAt = 0;
        this._inlineSuggestions = [];
        this._globalSuggestions = [];
        this._inlineIndex = 0;
        this._globalIndex = 0;

        this._buildInlineMenu();
        this._buildGlobalPalette();
        this._bindGlobalShortcut();
        window.addEventListener('IRIS:skills-changed', () => {
            this._loadedAt = 0;
            if (this.isGlobalOpen() || this.input?.value?.startsWith('/')) {
                this.loadCatalogue({ force: true });
            }
        });
        if (this.input) {
            this.input.setAttribute('aria-autocomplete', 'list');
            this.input.setAttribute('aria-controls', 'slash-command-list');
            this.input.setAttribute('aria-expanded', 'false');
        }
    }

    _buildInlineMenu() {
        if (!this.composer) return;
        const menu = document.createElement('div');
        menu.id = 'slash-command-menu';
        menu.className = 'slash-command-menu';
        menu.hidden = true;
        menu.innerHTML = `
            <div class="command-menu-head">
                <span><b>/</b> Comandos</span>
                <span class="command-menu-key">Tab completa · Enter ejecuta</span>
            </div>
            <div id="slash-command-list" class="command-menu-list" role="listbox" aria-label="Comandos disponibles"></div>
            <div class="command-menu-foot">Registro de Hermes · los comandos de terminal están marcados</div>
        `;
        this.composer.prepend(menu);
        this.inlineMenu = menu;
        this.inlineList = menu.querySelector('#slash-command-list');
    }

    _buildGlobalPalette() {
        const overlay = document.createElement('div');
        overlay.className = 'iris-command-overlay';
        overlay.hidden = true;
        overlay.innerHTML = `
            <section class="iris-command-palette" role="dialog" aria-modal="true" aria-labelledby="iris-command-title">
                <header class="iris-command-palette-head">
                    <div>
                        <span class="command-palette-kicker">IRIS OS // COMMAND DECK</span>
                        <h2 id="iris-command-title">¿Qué quieres hacer?</h2>
                    </div>
                    <button type="button" class="command-palette-close" aria-label="Cerrar paleta">Esc</button>
                </header>
                <label class="command-palette-search">
                    <span aria-hidden="true">&gt;_</span>
                    <input type="search" autocomplete="off" spellcheck="false" placeholder="Busca una app, ajuste o comando de Hermes…" aria-label="Buscar comandos">
                    <kbd>Ctrl K</kbd>
                </label>
                <div class="command-palette-meta">
                    <span data-command-count>Cargando catálogo…</span>
                    <span><i class="command-dot is-ready"></i> Integrado <i class="command-dot is-terminal"></i> Solo terminal</span>
                </div>
                <div class="command-palette-list" role="listbox" aria-label="Paleta de comandos"></div>
                <footer class="command-palette-footer">↑↓ navegar <span>Enter ejecutar</span> <span>Esc cerrar</span></footer>
            </section>
        `;
        document.body.appendChild(overlay);
        this.globalOverlay = overlay;
        this.globalInput = overlay.querySelector('input');
        this.globalList = overlay.querySelector('.command-palette-list');
        this.globalList.id = 'iris-command-list';
        this.globalInput.setAttribute('aria-controls', this.globalList.id);
        this.globalInput.setAttribute('aria-autocomplete', 'list');
        this.globalCount = overlay.querySelector('[data-command-count]');
        overlay.querySelector('.command-palette-close')?.addEventListener('click', () => this.closeGlobal());
        overlay.addEventListener('mousedown', (event) => {
            if (event.target === overlay) this.closeGlobal();
        });
        this.globalInput?.addEventListener('input', () => {
            this._globalIndex = 0;
            this._renderGlobal();
        });
        this.globalInput?.addEventListener('keydown', (event) => this._handleGlobalKeydown(event));
        overlay.addEventListener('keydown', (event) => this._trapGlobalFocus(event));
    }

    _bindGlobalShortcut() {
        window.addEventListener('keydown', (event) => {
            if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || event.key.toLocaleLowerCase() !== 'k') return;
            event.preventDefault();
            this.isGlobalOpen() ? this.closeGlobal() : this.openGlobal();
        });
    }

    async loadCatalogue({ force = false } = {}) {
        const fresh = this._loaded && Date.now() - this._loadedAt < 60_000;
        if (!force && fresh) return this.commands;
        if (this._loading) return this._loading;
        this._loading = (async () => {
            try {
                const response = await fetch(`/api/hermes/commands${force ? '?force=true' : ''}`);
                const payload = await response.json().catch(() => ({}));
                if (!response.ok || !Array.isArray(payload.commands)) {
                    throw new Error(payload.detail || `HTTP ${response.status}`);
                }
                this.commands = payload.commands;
                this.catalogueSource = payload.source || 'Hermes';
            } catch (error) {
                this.commands = [...FALLBACK_COMMANDS];
                this.catalogueSource = 'respaldo local';
                this.onNotice?.(`No pude actualizar el catálogo de Hermes: ${error?.message || error}`);
            } finally {
                this._loaded = true;
                this._loadedAt = Date.now();
                this._loading = null;
                this.updateFromInput();
                if (this.isGlobalOpen()) this._renderGlobal();
            }
            return this.commands;
        })();
        return this._loading;
    }

    updateFromInput() {
        if (!this.input) return;
        const value = this.input.value || '';
        if (!value.startsWith('/') || value.includes('\n')) {
            this.closeInline();
            return;
        }
        this.loadCatalogue();
        this._inlineSuggestions = this._suggestForSlash(value).slice(0, 9);
        this._inlineIndex = Math.min(this._inlineIndex, Math.max(0, this._inlineSuggestions.length - 1));
        this._renderInline();
    }

    _suggestForSlash(value) {
        const body = String(value || '').slice(1);
        const firstSpace = body.search(/\s/);
        const commandTerm = (firstSpace === -1 ? body : body.slice(0, firstSpace)).toLocaleLowerCase('es');
        const argumentTerm = firstSpace === -1 ? null : body.slice(firstSpace).trim().toLocaleLowerCase('es');
        const exact = this._findCommand(commandTerm);

        if (exact && argumentTerm !== null && (exact.subcommands || []).length) {
            const canonical = exact.name;
            const variants = exact.subcommands
                .filter((subcommand) => !argumentTerm || String(subcommand).toLocaleLowerCase('es').includes(argumentTerm))
                .map((subcommand) => {
                    const sub = String(subcommand);
                    const supported = (exact.integrated_subcommands || []).includes(sub);
                    return {
                        ...exact,
                        variant: 'subcommand',
                        subcommand: sub,
                        completion: `/${canonical} ${sub}`,
                        available: exact.available && supported,
                        availability: supported ? exact.availability : 'No integrado',
                        reason: supported ? exact.reason : `/${canonical} ${sub} sigue disponible en Hermes CLI, pero IRIS OS no integra todavía ese subcomando.`,
                    };
                });
            if (variants.length) return variants;
        }

        let rows = this.commands;
        if (commandTerm) {
            const prefixRows = this.commands.filter((item) => item.name.startsWith(commandTerm)
                || (item.aliases || []).some((alias) => alias.startsWith(commandTerm)));
            // Keep slash completion predictable: prose descriptions belong to
            // Ctrl+K search, not to command-name completion. A contains match
            // is used only when there is no prefix match at all.
            rows = prefixRows.length ? prefixRows : this.commands.filter((item) => item.name.includes(commandTerm)
                || (item.aliases || []).some((alias) => alias.includes(commandTerm)));
        }
        return rows.map((item) => ({ ...item, completion: `/${item.name}` }));
    }

    _findCommand(name) {
        const clean = String(name || '').toLocaleLowerCase('es').replace(/^\//, '');
        return this.commands.find((item) => item.name === clean || (item.aliases || []).includes(clean)) || null;
    }

    _renderInline() {
        if (!this.inlineMenu || !this.inlineList) return;
        const show = this.input?.value?.startsWith('/') && this._inlineSuggestions.length > 0;
        this.inlineMenu.hidden = !show;
        this.input?.setAttribute('aria-expanded', show ? 'true' : 'false');
        if (!show) {
            this.inlineList.replaceChildren();
            return;
        }
        this._renderRows(this.inlineList, this._inlineSuggestions, this._inlineIndex, (index, item) => {
            this._inlineIndex = index;
            const current = (this.input?.value || '').trim();
            if (current === item.completion) this.executeLine(current);
            else this._applyCompletion(item, { keepOpen: true });
        }, 'inline');
    }

    _renderRows(host, rows, selectedIndex, onPick, mode) {
        host.replaceChildren();
        if (!rows.length) {
            const empty = document.createElement('div');
            empty.className = 'command-menu-empty';
            empty.textContent = 'No hay coincidencias. Prueba /help para explorar el catálogo.';
            host.appendChild(empty);
            return;
        }
        rows.forEach((item, index) => {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'command-menu-item';
            row.dataset.available = item.available ? 'true' : 'false';
            row.dataset.source = String(item.source || 'Hermes').toLocaleLowerCase('es');
            row.setAttribute('role', 'option');
            row.id = `${mode}-command-option-${index}`;
            row.setAttribute('aria-selected', index === selectedIndex ? 'true' : 'false');
            if (index === selectedIndex) row.classList.add('is-selected');

            const glyph = document.createElement('span');
            glyph.className = 'command-item-glyph';
            glyph.textContent = item.source === 'IRIS' ? 'OS' : item.source === 'Skill' ? 'SK' : '/';
            const copy = document.createElement('span');
            copy.className = 'command-item-copy';
            const command = document.createElement('strong');
            command.textContent = item.completion || item.command || `/${item.name}`;
            const description = document.createElement('small');
            description.textContent = item.variant === 'subcommand'
                ? `${item.description} · ${item.subcommand}`
                : item.description || 'Comando de Hermes';
            copy.append(command, description);
            const tags = document.createElement('span');
            tags.className = 'command-item-tags';
            const source = document.createElement('em');
            source.textContent = item.source || 'Hermes';
            const availability = document.createElement('em');
            availability.className = item.available ? 'is-ready' : 'is-terminal';
            availability.textContent = item.available ? 'Listo' : (item.availability || 'Solo terminal');
            tags.append(source, availability);
            row.append(glyph, copy, tags);
            row.addEventListener('mouseenter', () => {
                if (mode === 'inline') this._inlineIndex = index;
                else this._globalIndex = index;
                host.querySelectorAll('.command-menu-item').forEach((candidate, candidateIndex) => {
                    candidate.classList.toggle('is-selected', candidateIndex === index);
                    candidate.setAttribute('aria-selected', candidateIndex === index ? 'true' : 'false');
                });
                const owner = mode === 'inline' ? this.input : this.globalInput;
                owner?.setAttribute('aria-activedescendant', row.id);
            });
            row.addEventListener('click', () => onPick(index, item));
            host.appendChild(row);
        });
        const owner = mode === 'inline' ? this.input : this.globalInput;
        const selected = host.querySelector('.command-menu-item.is-selected');
        if (selected) owner?.setAttribute('aria-activedescendant', selected.id);
        else owner?.removeAttribute('aria-activedescendant');
    }

    handleComposerKeydown(event) {
        if (event.key === 'Escape' && !this.inlineMenu?.hidden) {
            event.preventDefault();
            event.stopPropagation();
            this.closeInline();
            return true;
        }
        if (this.inlineMenu?.hidden || !this._inlineSuggestions.length) return false;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            event.stopPropagation();
            const delta = event.key === 'ArrowDown' ? 1 : -1;
            this._inlineIndex = (this._inlineIndex + delta + this._inlineSuggestions.length) % this._inlineSuggestions.length;
            this._renderInline();
            this._scrollSelected(this.inlineList);
            return true;
        }
        if (event.key === 'Tab') {
            event.preventDefault();
            event.stopPropagation();
            this._applyCompletion(this._inlineSuggestions[this._inlineIndex], { keepOpen: true });
            return true;
        }
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
            const item = this._inlineSuggestions[this._inlineIndex];
            if (!item) return false;
            event.preventDefault();
            event.stopPropagation();
            const current = (this.input?.value || '').trim();
            if (current === item.completion || this._lineTargetsItem(current, item)) this.executeLine(current);
            else this._applyCompletion(item, { keepOpen: true });
            return true;
        }
        return false;
    }

    _lineTargetsItem(line, item) {
        const body = String(line || '').replace(/^\//, '').trim();
        const name = body.split(/\s+/, 1)[0].toLocaleLowerCase('es');
        return (item.name === name || (item.aliases || []).includes(name)) && body.includes(' ');
    }

    _applyCompletion(item, { keepOpen = false } = {}) {
        if (!item || !this.input) return;
        let completion = item.completion || `/${item.name}`;
        const supportsArguments = item.execution === 'skill'
            || item.execution !== 'client' && (item.args_hint || (item.subcommands || []).length)
            || item.execution === 'client' && (item.integrated_subcommands || []).length;
        if (!item.variant && supportsArguments) completion += ' ';
        this.input.value = completion;
        this.input.focus();
        this.input.setSelectionRange(completion.length, completion.length);
        this.input.dispatchEvent(new Event('input', { bubbles: true }));
        if (!keepOpen) this.closeInline();
    }

    executeLine(line) {
        const clean = String(line || '').trim();
        if (!clean.startsWith('/')) return false;
        if (!this._loaded) {
            this.loadCatalogue();
            this.onNotice?.('Actualizando el catálogo de comandos de Hermes…');
            return true;
        }
        const name = clean.slice(1).split(/\s+/, 1)[0].toLocaleLowerCase('es');
        const item = this._findCommand(name);
        if (!item) {
            this.onNotice?.(`No reconozco /${name}. Abre /help para ver los comandos disponibles.`);
            return true;
        }
        const argument = clean.slice(1).trim().split(/\s+/).slice(1)[0] || '';
        if (item.execution === 'client' && argument) {
            const supported = item.integrated_subcommands || [];
            if (!supported.includes(argument)) {
                this.onNotice?.(`/${item.name} ${argument} no está integrado en IRIS OS. Puedes usar /${item.name}${supported.length ? ` ${supported.join('|')}` : ' sin argumentos'} o abrir Hermes CLI.`);
                return true;
            }
        }
        const accepted = this.onExecute?.(clean, item);
        // A server-backed command may be rejected synchronously while IRIS
        // Core reconnects. Consume the slash command so it never leaks to the
        // LLM, but preserve the composer/palette for an explicit retry.
        if (accepted === false) return true;
        this.closeInline();
        this.closeGlobal();
        if (this.input) {
            this.input.value = '';
            this.input.dispatchEvent(new Event('input', { bubbles: true }));
        }
        return true;
    }

    openGlobal({ query = '' } = {}) {
        if (!this.globalOverlay) return;
        if (!this.isGlobalOpen()) {
            const active = document.activeElement;
            this._returnFocus = active instanceof HTMLElement ? active : this.input;
        }
        this.globalOverlay.hidden = false;
        document.body.classList.add('command-palette-open');
        this._inertedElements = [...document.body.children]
            .filter((element) => element !== this.globalOverlay)
            .map((element) => [element, Boolean(element.inert)]);
        this._inertedElements.forEach(([element]) => { element.inert = true; });
        this.globalInput.value = query;
        this._globalIndex = 0;
        this._renderGlobal();
        this.loadCatalogue();
        requestAnimationFrame(() => this.globalInput?.focus());
    }

    closeGlobal() {
        if (!this.globalOverlay || this.globalOverlay.hidden) return;
        this.globalOverlay.hidden = true;
        document.body.classList.remove('command-palette-open');
        (this._inertedElements || []).forEach(([element, wasInert]) => {
            element.inert = wasInert;
        });
        this._inertedElements = [];
        const focusTarget = this._returnFocus?.isConnected ? this._returnFocus : this.input;
        focusTarget?.focus?.();
        this._returnFocus = null;
    }

    isGlobalOpen() {
        return !!this.globalOverlay && !this.globalOverlay.hidden;
    }

    _renderGlobal() {
        if (!this.globalList) return;
        const query = this.globalInput?.value || '';
        const allMatches = this.commands.filter((item) => tokenMatch(item, query));
        this._globalSuggestions = allMatches.slice(0, 60);
        this._globalIndex = Math.min(this._globalIndex, Math.max(0, this._globalSuggestions.length - 1));
        const available = this._globalSuggestions.filter((item) => item.available).length;
        if (this.globalCount) {
            const shown = allMatches.length > this._globalSuggestions.length
                ? `${this._globalSuggestions.length} de ${allMatches.length} resultados`
                : `${allMatches.length} resultados`;
            this.globalCount.textContent = this._loaded
                ? `${shown} · ${available} integrados ahora`
                : 'Actualizando registro de Hermes…';
        }
        this._renderRows(this.globalList, this._globalSuggestions, this._globalIndex, (_index, item) => this._pickGlobal(item), 'global');
    }

    _pickGlobal(item) {
        if (!item) return;
        if (!item.available) {
            this.onNotice?.(item.reason || `/${item.name} solo está disponible en Hermes CLI.`);
            return;
        }
        const needsInput = item.execution === 'skill';
        if (needsInput && this.input) {
            this.closeGlobal();
            this._applyCompletion({ ...item, completion: `/${item.name}` }, { keepOpen: true });
            return;
        }
        this.executeLine(`/${item.name}`);
    }

    _handleGlobalKeydown(event) {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            this.closeGlobal();
            return;
        }
        if (!this._globalSuggestions.length) return;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            const delta = event.key === 'ArrowDown' ? 1 : -1;
            this._globalIndex = (this._globalIndex + delta + this._globalSuggestions.length) % this._globalSuggestions.length;
            this._renderGlobal();
            this._scrollSelected(this.globalList);
        } else if (event.key === 'Enter') {
            event.preventDefault();
            this._pickGlobal(this._globalSuggestions[this._globalIndex]);
        }
    }

    _trapGlobalFocus(event) {
        if (event.key !== 'Tab' || !this.isGlobalOpen()) return;
        const focusable = [...this.globalOverlay.querySelectorAll(
            'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
        )].filter((element) => !element.hidden && element.offsetParent !== null);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    _scrollSelected(host) {
        host?.querySelector('.command-menu-item.is-selected')?.scrollIntoView?.({ block: 'nearest' });
    }

    closeInline() {
        if (this.inlineMenu) this.inlineMenu.hidden = true;
        this.input?.setAttribute('aria-expanded', 'false');
    }
}
