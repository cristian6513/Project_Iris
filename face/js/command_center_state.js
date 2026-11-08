/**
 * Pure state adapters for the IRIS OS Command Center.
 *
 * Keeping these decisions outside panel.js makes the UI honest and testable:
 * an open TCP port is not treated as a healthy Hermes Gateway, and the global
 * badge never says "Live" before the IRIS WebSocket is actually connected.
 */

export function gatewayViewFromProbe(report = {}) {
    if (report?.managed === false) {
        const healthy = report?.healthy === true;
        return {
            state: healthy ? 'online' : 'attention',
            panelState: healthy ? 'running' : 'degraded',
            label: healthy ? 'El servicio de IA remoto está listo' : 'El servicio de IA remoto no responde',
            detail: report.detail || (healthy
                ? 'La conexión remota respondió correctamente.'
                : 'Revisa la URL, el token y la disponibilidad del servidor remoto.'),
            action: 'probe',
            actionLabel: 'Actualizar estado',
        };
    }

    if (report?.healthy === true) {
        return {
            state: 'online',
            panelState: 'running',
            label: 'IRIS puede responder',
            detail: report.detail || 'El servicio de IA respondió correctamente.',
            action: 'restart',
            actionLabel: 'Reiniciar IRIS',
        };
    }

    if (report?.running === true) {
        return {
            state: 'attention',
            panelState: 'degraded',
            label: 'La conexión de IA necesita revisión',
            detail: report.detail || report.health_error || 'El puerto está abierto, pero /health no confirmó Hermes.',
            action: 'probe',
            actionLabel: 'Volver a comprobar',
        };
    }

    return {
        state: 'offline',
        panelState: 'stopped',
        label: 'El servicio de IA está detenido',
        detail: report.detail || '',
        action: 'start',
        actionLabel: 'Iniciar IRIS',
    };
}

export function commandCenterSummary({ runtimeConnected = false, gatewayState = 'checking' } = {}) {
    if (!runtimeConnected) {
        return { state: 'offline', label: 'Reconectando' };
    }
    if (gatewayState === 'attention' || gatewayState === 'error') {
        return { state: 'attention', label: 'La conexión de IA requiere atención' };
    }
    if (gatewayState === 'online') {
        return { state: 'online', label: 'Todo listo' };
    }
    if (gatewayState === 'offline') {
        return { state: 'ready', label: 'IRIS OS abierto · IA sin conexión' };
    }
    return { state: 'ready', label: 'IRIS lista' };
}
