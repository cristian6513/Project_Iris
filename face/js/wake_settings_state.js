function clean(value = '') {
    return String(value ?? '').trim();
}

export function mergeWakeSettingsSnapshot(current = {}, incoming = {}) {
    const next = { ...(current || {}), ...(incoming || {}) };
    const incomingCapabilities = incoming?.capabilities;
    if (!incomingCapabilities || !Object.keys(incomingCapabilities).length) {
        next.capabilities = { ...(current?.capabilities || {}) };
    }
    return next;
}

export function wakeReadinessState(settings = {}, runtime = {}) {
    const capabilities = settings?.capabilities || {};
    const inputError = clean(runtime?.input_device?.error);
    const runtimeError = clean(runtime?.error || runtime?.hint);
    const blocked = !runtime?.active && Boolean(
        inputError
        || runtimeError
        || clean(runtime?.state).toLowerCase() === 'blocked'
    );
    const hint = clean(capabilities.hint)
        || (inputError
            ? 'No se encontró un micrófono utilizable. Conecta uno y vuelve a comprobar.'
            : runtimeError);
    return {
        blocked,
        ready: !blocked && capabilities.ready === true,
        hint,
    };
}
