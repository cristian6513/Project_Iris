// Generated from apps/store/ui/main.js by scripts/build_app_catalog.mjs: edit the original.
/**
 * Iris Store (F17): install IRIS OS apps from a .zip or from their folder.
 *
 * Core checks every package with the same rules as the build and keeps its files in the profile;
 * this window sends what the person chose, shows Core's review and asks before installing.
 * An installed app has the same access as Iris (the user's choice for this phase), and the window
 * says so before every installation. Uninstalling removes the app, its widgets and its shortcut.
 */
const CSS = 'store';
const API = '/api/app-packages';
export const MAX_FOLDER_FILES = 400;
// Sent as JSON with base64, a folder must stay under Core's 6 MB request limit.
export const MAX_FOLDER_BYTES = 4 * 1024 * 1024;
export const WARNING = 'Una app instalada tiene el mismo acceso que Iris: puede leer tu correo y tus conversaciones '
    + 'o solicitar acciones al motor de IA. Instala solo apps de personas en las que confíes. Si la descargaste de internet, '
    + 'revisa antes su código o pídele a una IA que lo revise.';

export function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 102.4) / 10} KB`;
    return `${Math.round(bytes / 104857.6) / 10} MB`;
}

function ignoredFolderPath(path) {
    return path.split('/').some((part) => part.startsWith('.') || part === 'node_modules' || part === '__MACOSX');
}

/** The files of a chosen folder worth sending: never hidden folders (.git), dependencies or macOS leftovers. */
export function folderEntries(items) {
    return [...items].map((item) => (item.file ? item : { file: item, path: item.webkitRelativePath || item.name }))
        .filter(({ path }) => path && !ignoredFolderPath(path));
}

export function toBase64(bytes) {
    let text = '';
    for (let index = 0; index < bytes.length; index += 0x8000) text += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
    return btoa(text);
}

/** A dropped folder, read with the browser's entries API ({ file, path } like a chosen folder). */
async function readEntry(entry, prefix = '', found = []) {
    const path = prefix + entry.name;
    if (found.length > MAX_FOLDER_FILES || ignoredFolderPath(path)) return found;
    if (entry.isFile) {
        found.push({ file: await new Promise((resolve, reject) => entry.file(resolve, reject)), path });
        return found;
    }
    const reader = entry.createReader();
    for (;;) {
        const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
        if (!batch.length || found.length > MAX_FOLDER_FILES) return found;
        for (const child of batch) await readEntry(child, `${path}/`, found);
    }
}

async function request(path, options = {}) {
    const response = await globalThis.fetch(`${API}${path}`, { cache: 'no-store', ...options });
    let data = null;
    try { data = await response.json(); } catch { /* the message below stands in */ }
    if (!response.ok) throw new Error(data?.error?.message || (typeof data?.detail === 'string' ? data.detail : '') || 'Iris Store no pudo completar la operación.');
    return data;
}

function el(tag, part, text) {
    const node = document.createElement(tag);
    node.className = `${CSS}-${part}`;
    node.dataset.part = part;
    if (text != null) node.textContent = text;
    return node;
}

function button(part, text, action, { primary = false } = {}) {
    const node = el('button', part, text);
    node.type = 'button';
    if (primary) node.classList.add(`${CSS}-primary`);
    node.addEventListener('click', action);
    return node;
}

export function createApp(context = {}) {
    let root = null, disposed = false, generation = 0, focusPart = '';
    // `desktopFailed`: the last change Core saved but the running desktop could not take yet.
    const state = { busy: '', error: '', notice: '', review: null, installed: [], loaded: false, changed: false, desktopFailed: null, removeData: new Set() };

    function set(changes, focus = '') {
        if (disposed) return;
        Object.assign(state, changes);
        focusPart = focus || focusPart;
        render();
    }

    async function loadInstalled() {
        try {
            const data = await request('');
            set({ installed: Array.isArray(data?.apps) ? data.apps : [], loaded: true });
        } catch (error) {
            set({ loaded: true, error: error.message });
        }
    }

    /** Send a .zip (Blob) or a folder ({ file, path } list) to Core and show its review. */
    async function inspect(source) {
        const mine = ++generation;
        set({ busy: 'Revisando la app…', error: '', notice: '', review: null });
        try {
            let init;
            if (Array.isArray(source)) {
                const entries = folderEntries(source);
                if (!entries.length) throw new Error('La carpeta está vacía.');
                if (entries.length > MAX_FOLDER_FILES) throw new Error(`La carpeta tiene más de ${MAX_FOLDER_FILES} archivos.`);
                const total = entries.reduce((sum, entry) => sum + entry.file.size, 0);
                if (total > MAX_FOLDER_BYTES) throw new Error('La carpeta supera 4 MB. Elige solo la carpeta de la app.');
                const files = [];
                for (const { file, path } of entries) files.push({ path, data: toBase64(new Uint8Array(await file.arrayBuffer())) });
                init = { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ files }) };
            } else {
                if (!/\.zip$/i.test(source?.name || '')) throw new Error('Elige un archivo .zip o la carpeta de la app.');
                init = { headers: { 'Content-Type': 'application/zip' }, body: source };
            }
            const data = await request('/inspect', { method: 'POST', ...init });
            if (mine === generation) set({ busy: '', review: data }, 'review-title');
        } catch (error) {
            if (mine === generation) set({ busy: '', error: error.message }, 'error');
        }
    }

    /**
     * F17: take an install, update or removal into the running desktop, without reloading it.
     * If the desktop cannot be updated, the Store says so and offers «Reintentar»; reloading the
     * page stays as the last resort. An older shell without that ability gets the reload button.
     */
    async function applyToDesktop(done, focus = '') {
        if (typeof context.refreshApps !== 'function') {
            await loadInstalled();
            set({ busy: '', changed: true, notice: `${done} Recarga el escritorio para aplicar el cambio.` }, 'reload');
            return;
        }
        set({ busy: 'Actualizando el escritorio…', desktopFailed: null });
        const result = await Promise.resolve().then(() => context.refreshApps()).catch((error) => ({ ok: false, error: error?.message }));
        if (disposed) return;
        await loadInstalled();
        if (result?.ok) set({ busy: '', desktopFailed: null, notice: done }, focus);
        else set({ busy: '', desktopFailed: { done, focus }, notice: `${done} Pero el escritorio no se pudo actualizar.` }, 'retry-desktop');
    }

    async function install() {
        const review = state.review;
        if (!review || state.busy) return;
        const update = !!review.app.current;
        const running = update && !!context.appInUse?.(review.app.id);
        const components = review.app.extension
            ? ` ${review.app.extension.python ? 'Incluye código Python que se ejecuta en tu equipo.' : ''}${review.app.extension.skill ? ' Añade una skill de uso para Hermes.' : ''}` : '';
        const ok = await context.confirm?.({
            title: `${update ? 'Actualizar' : 'Instalar'} «${review.app.name}»`,
            message: running ? `${WARNING}${components} «${review.app.name}» está abierta: se reiniciará con la nueva versión y perderás lo que no hayas guardado en ella.` : WARNING + components,
            question: update ? '¿Quieres actualizarla?' : '¿Quieres instalarla?',
            confirmLabel: update ? 'Actualizar' : 'Instalar', cancelLabel: 'Cancelar',
        });
        if (!ok || disposed || state.review !== review) return;
        set({ busy: update ? 'Actualizando…' : 'Instalando…', error: '' });
        let data;
        try {
            data = await request('/install', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token: review.token }) });
        } catch (error) {
            set({ busy: '', error: error.message }, 'error');
            return;
        }
        const { id, name, widgets = [] } = data.installed.manifest;
        state.removeData.delete(id);
        set({ review: null });
        await applyToDesktop(update
            ? `«${name}» se actualizó${running ? ' y se reinició con la nueva versión' : ''}.`
            : `«${name}» ya está en Aplicaciones.${widgets.length ? ' Sus widgets están en Personalizar → Widgets.' : ''}`, `open-${id}`);
    }

    async function uninstall(app) {
        if (state.busy) return;
        const keepData = !state.removeData.has(app.id);
        const ok = await context.confirm?.({
            title: `Desinstalar «${app.manifest.name}»`,
            message: 'Se cerrarán sus ventanas y se quitarán la app, sus widgets y su acceso del inicio.'
                + (app.manifest.extension ? ' También se detendrán sus funciones Python y se retirará su skill administrada, si la incluye.'
                    + (keepData ? ' Se conservarán sus datos locales.' : ' Se eliminarán también sus datos locales.') : ''),
            question: '¿Quieres desinstalarla?', confirmLabel: 'Desinstalar', cancelLabel: 'Cancelar',
        });
        if (!ok || disposed) return;
        set({ busy: 'Desinstalando…', error: '', notice: '' });
        let data;
        try {
            const installation = /\/files\/(ins_[a-f0-9]{32})\//.exec(app.entry || '')?.[1];
            data = await request(`/${encodeURIComponent(app.id)}${keepData ? '' : '?keep_data=false'}`, {
                method: 'DELETE', ...(installation ? { headers: { 'X-Iris-App-Installation': installation } } : {}),
            });
        } catch (error) {
            set({ busy: '', error: error.message }, 'error');
            return;
        }
        state.removeData.delete(app.id);
        await applyToDesktop(`«${app.manifest.name}» se desinstaló.`
            + (data.warnings?.length ? ' ' + data.warnings.join(' ') : '')
            + (data.desktop_cleaned ? '' : ' Si quedó un widget, aparece como no disponible y puedes quitarlo.'), 'pick-zip');
    }

    function picker(part, label, options) {
        const input = el('input', `${part}-input`);
        input.type = 'file';
        input.hidden = true;
        input.tabIndex = -1;
        Object.assign(input, options);
        input.addEventListener('change', () => {
            const files = [...(input.files || [])];
            input.value = '';
            if (files.length) void inspect(options.webkitdirectory ? files : files[0]);
        });
        return [button(part, label, () => input.click(), { primary: part === 'pick-zip' }), input];
    }

    function dropZone() {
        const zone = el('div', 'drop');
        zone.setAttribute('role', 'group');
        zone.setAttribute('aria-label', 'Elegir la app que quieres instalar');
        zone.append(el('p', 'drop-text', 'Arrastra aquí el .zip o la carpeta de la app'),
            ...picker('pick-zip', 'Elegir archivo .zip', { accept: '.zip,application/zip' }),
            ...picker('pick-folder', 'Elegir carpeta', { webkitdirectory: true }));
        zone.addEventListener('dragover', (event) => { event.preventDefault(); zone.classList.add(`${CSS}-dragging`); });
        zone.addEventListener('dragleave', () => zone.classList.remove(`${CSS}-dragging`));
        zone.addEventListener('drop', async (event) => {
            event.preventDefault();
            zone.classList.remove(`${CSS}-dragging`);
            const item = [...(event.dataTransfer?.items || [])].find((entry) => entry.kind === 'file');
            const entry = item?.webkitGetAsEntry?.();
            if (entry?.isDirectory) void inspect(await readEntry(entry).catch(() => []));
            else if (event.dataTransfer?.files?.[0]) void inspect(event.dataTransfer.files[0]);
        });
        return zone;
    }

    function icon(name) {
        const holder = el('span', 'icon');
        holder.setAttribute('aria-hidden', 'true');
        // The shell's own icon set (os_icons.js), passed in by the shell; never markup from a package.
        if (typeof context.icon === 'function') holder.innerHTML = context.icon(name);
        return holder;
    }

    function reviewCard(review) {
        const { app } = review;
        const card = el('section', 'review');
        const title = el('h3', 'review-title', app.name);
        title.tabIndex = -1;
        const head = el('header', 'review-head');
        const heading = el('div', 'review-heading');
        const replaces = !app.current ? '' : app.current.version === app.version ? ' · ya instalada: se reinstalará'
            : ` · reemplaza la ${app.current.version}`;
        heading.append(title, el('p', 'review-version', `Versión ${app.version}${replaces}`));
        head.append(icon(app.icon), heading);
        card.append(head, el('p', 'review-description', app.description));
        if (app.widgets.length) card.append(el('p', 'review-widgets', `Incluye ${app.widgets.length === 1 ? 'un widget' : `${app.widgets.length} widgets`}: ${app.widgets.map((widget) => widget.name).join(', ')}.`));
        if (app.extension) {
            const components = el('section', 'components');
            components.append(el('h4', 'components-title', 'Qué se añadirá'));
            if (app.extension.python) components.append(el('p', 'component-python', 'Funciones locales en Python, en un proceso propio de esta app.'));
            if (app.extension.skill) components.append(el('p', 'component-skill', 'Una skill para que Iris sepa usar la app mediante Hermes.'));
            // Operations only its own window uses never reach Iris, so they are not listed as hers.
            const forIris = app.extension.operations.filter(op => op.audience !== 'ui');
            if (forIris.length) components.append(el('p', 'component-actions', `Acciones que Iris podrá usar: ${forIris.map(op => op.label).join(', ')}.`));
            components.append(el('p', 'component-dependencies', 'No requiere descargar motores ni instalar librerías adicionales.'));
            card.append(components);
        }
        card.append(el('p', 'review-size', `${formatSize(app.size)} · ${app.files} ${app.files === 1 ? 'archivo' : 'archivos'}`));
        if (app.ignored.length) {
            const details = el('details', 'review-ignored');
            details.append(el('summary', 'review-ignored-summary', `No se instalan ${app.ignored.length} archivos (documentación y pruebas)`));
            const list = el('ul', 'review-ignored-list');
            for (const name of app.ignored.slice(0, 30)) list.append(el('li', 'review-ignored-item', name));
            details.append(list);
            card.append(details);
        }
        card.append(el('h4', 'risks-title', 'Lo que hace su código'));
        if (app.risks.length) {
            const list = el('ul', 'risks');
            for (const risk of app.risks) {
                const item = el('li', `risk-${risk.code}`, risk.label);
                item.classList.add(`${CSS}-risk`);
                item.append(el('small', 'risk-files', ` (${risk.files.slice(0, 4).join(', ')}${risk.files.length > 4 ? '…' : ''})`));
                list.append(item);
            }
            card.append(list);
        } else {
            card.append(el('p', 'risks-none', 'No encontramos conexiones a internet, llamadas a funciones de Iris ni código oculto.'));
        }
        card.append(el('p', 'risks-note', 'Esta revisión es automática y orientativa: no garantiza que la app sea segura.'));
        const warning = el('p', 'warning', WARNING);
        warning.setAttribute('role', 'note');
        const actions = el('div', 'review-actions');
        actions.append(button('install', app.current ? 'Actualizar' : 'Instalar', () => void install(), { primary: true }),
            button('cancel', 'Cancelar', () => { generation++; set({ review: null }, 'pick-zip'); }));
        card.append(warning, actions);
        return card;
    }

    function installedList() {
        const section = el('section', 'installed');
        section.append(el('h3', 'installed-title', 'Instaladas desde archivo'));
        if (!state.loaded) { section.append(el('p', 'installed-loading', 'Leyendo…')); return section; }
        if (!state.installed.length) { section.append(el('p', 'installed-empty', 'Todavía no has instalado apps desde un archivo.')); return section; }
        const list = el('ul', 'installed-list');
        for (const app of state.installed) {
            const row = el('li', `installed-${app.id}`);
            row.classList.add(`${CSS}-row`);
            const text = el('div', 'row-text');
            const date = Number.isNaN(Date.parse(app.installed_at)) ? '' : ` · ${new Date(app.installed_at).toLocaleDateString('es', { day: 'numeric', month: 'short', year: 'numeric' })}`;
            text.append(el('strong', 'row-name', app.manifest.name), el('small', 'row-meta', `Versión ${app.version} · ${formatSize(app.size)}${date}`));
            if (app.manifest.extension?.python) {
                const label = el('label', 'remove-data');
                const input = el('input', `remove-data-${app.id}`);
                input.type = 'checkbox'; input.checked = state.removeData.has(app.id);
                input.addEventListener('change', () => input.checked ? state.removeData.add(app.id) : state.removeData.delete(app.id));
                label.append(input, el('span', 'remove-data-label', 'Eliminar también sus datos al desinstalar'));
                text.append(label);
            }
            const actions = el('div', 'row-actions');
            if (context.installedApps?.has?.(app.id)) actions.append(button(`open-${app.id}`, 'Abrir', () => context.openApp?.(app.id)));
            else actions.append(el('small', 'row-reload', 'Recarga el escritorio para usarla'));
            actions.append(button(`uninstall-${app.id}`, 'Desinstalar', () => void uninstall(app)));
            row.append(icon(app.manifest.icon), text, actions);
            list.append(row);
        }
        section.append(list);
        return section;
    }

    function render() {
        if (!root || disposed) return;
        const wrap = el('div', 'root');
        wrap.append(el('h2', 'title', 'Iris Store'),
            el('p', 'intro', 'Instala apps de IRIS OS desde un archivo .zip o desde su carpeta. Iris comprueba que la app tenga el formato correcto antes de instalarla.'),
            dropZone());
        const status = el('p', 'status', state.busy || state.notice);
        status.setAttribute('role', 'status');
        wrap.append(status);
        if (state.error) {
            const error = el('p', 'error', state.error);
            error.setAttribute('role', 'alert');
            error.tabIndex = -1;
            wrap.append(error);
        }
        const reload = () => (context.reload || (() => globalThis.location?.reload()))();
        if (state.desktopFailed) {
            const { done, focus } = state.desktopFailed;
            const actions = el('div', 'desktop-actions');
            actions.append(button('retry-desktop', 'Reintentar', () => void applyToDesktop(done, focus), { primary: true }),
                button('reload', 'Recargar el escritorio', reload));
            wrap.append(actions);
        } else if (state.changed) wrap.append(button('reload', 'Recargar el escritorio', reload, { primary: true }));
        if (state.review) wrap.append(reviewCard(state.review));
        wrap.append(installedList());
        root.replaceChildren(wrap);
        for (const node of wrap.querySelectorAll?.('button') || []) node.disabled = !!state.busy && node.dataset.part !== 'cancel';
        if (focusPart) {
            const target = root.querySelector?.(`[data-part="${focusPart}"]`);
            focusPart = '';
            target?.focus?.();
        }
    }

    return {
        mount(node) { root = node; render(); },
        async refresh() { await loadInstalled(); },
        setVisibility() { /* nothing runs in the background */ },
        dispose() { disposed = true; generation++; root?.replaceChildren(); root = null; },
    };
}
