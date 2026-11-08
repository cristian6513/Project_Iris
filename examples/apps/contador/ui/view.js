/** Window and widget share the backend value, never independent counters. */
export function createCounter({ call, openApp, signal, widget = false }) {
    let root, value, status, increment, timer, visible = false, disposed = false, saving = false, generation = 0;
    const eventName = 'iris-contador-updated';
    function node(tag, text, part) {
        const element = document.createElement(tag);
        element.textContent = text;
        if (part) element.dataset.part = part;
        return element;
    }
    async function update(amount = 0) {
        if (disposed || !visible || saving || typeof call !== 'function') return;
        const mine = ++generation;
        if (amount) { saving = true; increment.disabled = true; status.textContent = 'Guardando…'; }
        try {
            const data = await call(amount ? 'increment' : 'status', amount ? { amount } : {});
            if (disposed || mine !== generation) return;
            value.textContent = String(data.count);
            status.textContent = amount ? 'Guardado en tu equipo' : 'Compartido con tu app e Iris';
            if (amount) document.dispatchEvent(new CustomEvent(eventName));
        } catch (error) {
            if (!disposed && mine === generation && !signal?.aborted) status.textContent = error.message;
        } finally {
            if (amount) { saving = false; if (increment) increment.disabled = false; }
        }
    }
    const changed = () => void update();
    function setVisibility({ visible: next }) {
        visible = !!next; clearInterval(timer);
        if (visible && !disposed) { void update(); timer = setInterval(() => void update(), 2000); }
    }
    return {
        mount(target) {
            root = target;
            const wrap = node('section', '', 'counter'); wrap.className = `contador-root${widget ? ' contador-widget' : ''}`;
            const heading = node('h2', 'Contador');
            const hint = node('p', widget ? 'Tu valor local' : 'Un ejemplo pequeño de lo que puedes crear con Iris');
            value = node('output', '—', 'value'); value.setAttribute('aria-label', 'Valor del contador');
            status = node('p', 'Abre la app para consultar su valor', 'status'); status.setAttribute('role', 'status');
            increment = node('button', '+1', 'increment'); increment.type = 'button';
            increment.setAttribute('aria-label', 'Sumar uno al contador'); increment.addEventListener('click', () => void update(1));
            const actions = node('div', ''); actions.className = 'contador-actions'; actions.append(increment);
            if (widget) {
                const open = node('button', 'Abrir app', 'open'); open.type = 'button';
                open.addEventListener('click', () => openApp?.('contador')); actions.append(open);
            }
            wrap.append(heading, hint, value, actions, status); root.replaceChildren(wrap);
            document.addEventListener(eventName, changed);
            if (typeof call !== 'function') { status.textContent = 'Esta versión de Iris no admite el backend de la app.'; increment.disabled = true; }
        },
        refresh: () => update(), setVisibility,
        dispose() { disposed = true; generation++; clearInterval(timer); document.removeEventListener(eventName, changed); root?.replaceChildren(); },
    };
}
