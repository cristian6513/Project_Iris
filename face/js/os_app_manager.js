/** Built-in apps are protected; only their icons and home shortcuts are managed here.
 * Apps installed from a file (F17) are uninstalled in Iris Store, which asks first.
 * Each installed app's notices and «Avisar a Iris» without asking are the person's choice, here;
 * the shell gives the means (`appNotices`, `askIrisTrust`), this view makes no requests itself. */
const NOTICE_MODES = [['show', 'Mostrar y guardar en la Bandeja'], ['inbox', 'Solo en la Bandeja'], ['off', 'Desactivados']];

export function createApp(context) {
    const { desktopHome: model, appRegistry: registry, openApp } = context;
    const { appNotices = null, askIrisTrust = null } = context;
    let root, visible = false, unsubscribe, signature = '', pendingFocus = '';
    let noticeModes = null, noticeError = '';
    async function loadNoticeModes() {
        if (!appNotices) return;
        try { noticeModes = await appNotices.load(); noticeError = ''; } catch { noticeError = 'No se pudo leer qué apps pueden avisarte.'; }
        render();
    }
    async function setNoticeMode(id, mode) {
        try { await appNotices.set(id, mode); noticeModes = { ...noticeModes, [id]: mode }; noticeError = ''; } catch { noticeError = 'No se guardó el cambio de avisos; inténtalo de nuevo.'; }
        render();
    }
    const doc = globalThis.document;
    const node = (tag, text = '', className = '') => { const value = doc.createElement(tag); value.textContent = text; value.className = className; return value; };
    const button = (text, key, action, disabled = false) => {
        const value = node('button', text, 'os-home-button'); value.type = 'button'; value.dataset.managerKey = key;
        value.disabled = disabled; value.addEventListener('click', action); return value;
    };
    function render() {
        if (!root || !visible) return;
        // The registry revision changes when Iris Store installs or removes an app (F17).
        const trusted = askIrisTrust?.list() || [];
        const stamp = JSON.stringify([model.layout, model.busy, !!model.loading, model.error, registry.revision ?? 0, noticeModes, noticeError, trusted]); if (stamp === signature) return; signature = stamp;
        const focus = (root.contains(doc.activeElement) ? doc.activeElement?.dataset.managerKey : '') || pendingFocus;
        const disabled = model.busy || !!model.loading || !model.layout;
        pendingFocus = disabled ? focus : '';
        root.replaceChildren(node('h2', 'Tus aplicaciones'), node('p', 'Ocultar solo quita el icono del cajón. Las funciones y los accesos del inicio se conservan.', 'os-home-manager-hint'));
        for (const message of [model.error, noticeError].filter(Boolean)) { const error = node('p', message); error.setAttribute('role', 'alert'); root.append(error); }
        root.append(button(model.loading ? 'Leyendo…' : 'Actualizar lista', 'reload', () => void model.load(), model.busy || !!model.loading));
        const list = node('div', '', 'os-home-manager-list'); root.append(list);
        for (const app of registry.list()) {
            const hidden = model.layout?.hidden_apps.includes(app.id), pinned = model.layout?.shortcuts.includes(app.id);
            const row = node('section', '', 'os-home-manager-row');
            // F17: Core's package store says which apps came from a file; only those can be uninstalled, in Iris Store.
            const installed = context.installedApps?.get?.(app.id);
            row.append(node('h3', app.name), node('p', installed ? `Instalada desde archivo · versión ${installed.version}`
                : 'Incluida con Iris · No se desinstala desde aquí', 'os-home-manager-origin'));
            if (installed) {
                row.append(button('Desinstalar en Iris Store', `store:${app.id}`, () => openApp('store')));
                if (appNotices) {
                    const notices = node('label', '', 'os-home-manager-notices'); notices.append(node('span', 'Avisos de la app'));
                    const select = node('select'); select.dataset.managerKey = `notices:${app.id}`;
                    for (const [value, text] of NOTICE_MODES) { const option = node('option', text); option.value = value; select.append(option); }
                    select.value = noticeModes?.[app.id] || 'show'; select.disabled = !noticeModes;
                    select.addEventListener('change', () => void setNoticeMode(app.id, select.value));
                    notices.append(select); row.append(notices);
                }
                if (trusted.includes(app.id)) {
                    row.append(node('p', 'Puede avisar a Iris sin preguntarte; cada aviso sigue necesitando tu clic.'),
                        button('Volver a preguntar antes de avisar a Iris', `ask:${app.id}`, () => { askIrisTrust.set(app.id, false); signature = ''; render(); }));
                }
            }
            const status = node('span', hidden ? 'Oculta del cajón' : app.launcher.visible ? 'Visible en Apps' : 'Acceso desde el sistema'); row.append(status);
            if (app.launcher.visible) row.append(button(hidden ? 'Mostrar en el cajón' : 'Ocultar del cajón', `hide:${app.id}`, () => void model.hide(app.id, !hidden), disabled));
            row.append(button(pinned ? 'Quitar de inicio' : 'Agregar a inicio', `pin:${app.id}`, () => void (pinned ? model.remove(app.id) : model.pin(app.id)), disabled || (!pinned && model.layout?.shortcuts.length >= 64)));
            if (['mail', 'calendar'].includes(app.id)) {
                row.append(node('p', 'La sincronización y los avisos que tengas activados continuarán. Para detener la sincronización de una cuenta, desconéctala en Conexiones.'),
                    button('Gestionar cuentas en Conexiones', `accounts:${app.id}`, () => openApp('conexiones')));
            }
            list.append(row);
        }
        for (const id of new Set([...(model.layout?.shortcuts || []), ...(model.layout?.hidden_apps || [])].filter((id) => !registry.get(id)))) {
            const row = node('section', '', 'os-home-manager-row'); row.append(node('h3', 'Aplicación no disponible'),
                button('Quitar preferencia guardada', `remove:${id}`, () => void model.change((value) => {
                    value.shortcuts = value.shortcuts.filter((key) => key !== id); value.hidden_apps = value.hidden_apps.filter((key) => key !== id);
                    value.order = value.order.filter((key) => key !== `app:${id}`);
                }), disabled)); list.append(row);
        }
        if (!disabled && focus) [...root.querySelectorAll('[data-manager-key]')].find((entry) => entry.dataset.managerKey === focus && !entry.disabled)?.focus();
    }
    return {
        mount(target) { root = target; root.classList.add('os-home-manager'); unsubscribe = model.subscribe(render); },
        setVisibility({ visible: value }) { visible = value; if (value) { render(); void model.load(); void loadNoticeModes(); } },
        refresh() { render(); },
        dispose() { unsubscribe?.(); root?.replaceChildren(); root = null; },
    };
}
