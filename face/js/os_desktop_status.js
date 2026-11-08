/** Short desktop copy from confirmed runtime state; no tool arguments or private details. */
export function desktopStatus({ state = 'idle', runtimeChecked = false, runtimeReady = false, wake = {}, manualVoice = {} } = {}) {
    if (runtimeChecked && !runtimeReady) return { text: 'Sin conexión local', tone: 'offline' };
    if (state === 'error') return { text: 'Necesito atención', tone: 'error' };
    if (manualVoice?.state === 'recording' || wake?.state === 'recording') return { text: 'Escuchando…', tone: 'busy' };
    if (manualVoice?.state === 'transcribing' || wake?.state === 'transcribing') return { text: 'Transcribiendo…', tone: 'busy' };
    const active = {
        thinking: 'Pensando…', speaking: 'Respondiendo…', tool: 'Usando una herramienta…',
        skill: 'Usando una habilidad…', subagent: 'Coordinando tareas…', job: 'Ejecutando una rutina…',
        waiting: 'Esperando tu respuesta', done: 'Trabajo terminado',
    };
    if (active[state]) return { text: active[state], tone: state === 'waiting' ? 'waiting' : state === 'done' ? 'ready' : 'busy' };
    if (!runtimeChecked) return { text: 'Comprobando conexión…', tone: 'checking' };
    // An armed wake detector is not a recording of the person's request.
    return { text: 'En espera', tone: 'ready' };
}
