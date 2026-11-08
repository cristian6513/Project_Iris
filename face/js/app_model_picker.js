/** Read-only model choice for app-specific consent. Never changes the chat or sends app data. */
export function createAppModelPicker({ document, fetchImpl = (...args) => globalThis.fetch(...args),
    getSelection, onSelection, changed, prefix, providerPart, modelPart, fieldClass, labelClass, buttonClass }) {
    let catalog = null, loading = false, error = '', disposed = false, controller = null;
    const rows = () => (catalog?.inventory?.providers || []).filter(row => row.authenticated);
    const usable = (row, id) => row && !row.free_tier_pending && !row.pricing_pending
        && row.models.includes(id) && !(row.unavailable_models || []).includes(id);
    const valid = () => { const { provider, model } = getSelection(); return !loading && !error && usable(rows().find(row => row.id === provider), model); };
    const node = (tag, part, text = '') => {
        const n = document.createElement(tag); n.dataset.part = part; n.dataset.key = part;
        n.className = tag === 'label' ? labelClass : tag === 'select' ? fieldClass : tag === 'button' ? buttonClass || '' : '';
        n.textContent = text; return n;
    };
    async function load({ refresh = false } = {}) {
        if (disposed || loading || (catalog && !refresh)) return;
        if (refresh) onSelection({ ...getSelection() }); // invalidate consent even if the refresh fails
        loading = true; error = ''; changed();
        controller = new AbortController();
        try {
            const response = await fetchImpl(`/api/models/apps${refresh ? '?refresh=true' : ''}`, { cache: 'no-store', signal: controller.signal });
            const data = await response.json();
            if (!response.ok || data?.supported === false || data?.reason === 'hermes_offline'
                || !Array.isArray(data?.inventory?.providers) || data.inventory.error) throw new Error('No se pudo consultar el catálogo de IA. Comprueba que Iris esté iniciada y vuelve a intentarlo.');
            if (disposed) return;
            catalog = data;
            // Only a pristine choice inherits the configured default. Saved app settings and user edits win.
            const selected = getSelection(), fallback = data.default || {};
            if (!selected.provider && !selected.model && usable(rows().find(row => row.id === fallback.provider), fallback.model)) {
                onSelection({ provider: fallback.provider, model: fallback.model });
            }
        } catch (e) { if (!disposed && e?.name !== 'AbortError') error = 'No se pudo consultar el catálogo de IA. Comprueba que Iris esté iniciada y vuelve a intentarlo.'; }
        finally { if (!disposed) { loading = false; controller = null; changed(); } }
    }
    function render({ disabled = false } = {}) {
        const wrap = node('div', `${prefix}-picker`), selected = getSelection(), providers = rows();
        function select(part, title, value, choices, update) {
            const label = node('label', `${part}-label`, title), field = node('select', part);
            field.disabled = disabled || loading || !!error || !choices.length;
            field.setAttribute('aria-label', title);
            const placeholder = node('option', `${part}-placeholder`, loading ? 'Consultando modelos…' : 'Selecciona una opción');
            placeholder.value = ''; placeholder.disabled = true; field.append(placeholder);
            if (value && !choices.some(choice => choice.id === value)) {
                const missing = node('option', `${part}-missing`, `${value} · no disponible en el catálogo`);
                missing.value = value; missing.disabled = true; field.append(missing);
            }
            for (const choice of choices) {
                const option = node('option', `${part}-option`, choice.label || choice.id);
                option.value = choice.id; option.disabled = !!choice.disabled; field.append(option);
            }
            field.value = value;
            field.addEventListener('change', () => { if (!field.disabled && !disposed) update(field.value); });
            label.append(field); wrap.append(label);
        }
        select(providerPart, 'Proveedor de IA', selected.provider, providers.map(row => ({ id: row.id, label: row.label })), provider => {
            const row = providers.find(item => item.id === provider); if (!row) return;
            const fallback = catalog.default || {};
            onSelection({ provider, model: fallback.provider === provider && usable(row, fallback.model) ? fallback.model : '' }); changed();
        });
        const row = providers.find(item => item.id === selected.provider);
        select(modelPart, 'Modelo para esta función', selected.model, (row?.models || []).map(id => ({ id,
            label: `${id}${usable(row, id) ? '' : row.free_tier_pending || row.pricing_pending ? ' · pendiente de comprobar tu plan' : ' · no incluido en tu plan'}`,
            disabled: !usable(row, id) })), model => { if (usable(row, model)) { onSelection({ ...getSelection(), model }); changed(); } });
        const hint = node('p', `${prefix}-catalog-status`, loading ? 'Consultando los proveedores conectados…' : error
            || (!providers.length ? 'No hay proveedores conectados. Configura tu IA en Ajustes para elegirla aquí.'
                : 'Usa tus proveedores conectados. Esta elección solo se aplica a esta función; aún debes revisar y autorizar el envío.'));
        hint.setAttribute('role', 'status'); wrap.append(hint);
        const retry = node('button', `${prefix}-catalog-refresh`, error ? 'Reintentar catálogo' : 'Actualizar modelos'); retry.type = 'button';
        retry.disabled = disabled || loading; retry.addEventListener('click', () => { if (!retry.disabled) void load({ refresh: true }); }); wrap.append(retry);
        return wrap;
    }
    return { load, render, valid, dispose() { disposed = true; controller?.abort(); catalog = null; } };
}
