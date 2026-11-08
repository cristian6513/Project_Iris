export const FOLDERS = Object.freeze({ inbox: 'Bandeja de entrada', starred: 'Destacados', sent: 'Enviados', drafts: 'Borradores', all: 'Todo el correo' });
export const FILTERS = Object.freeze({ all: 'Todos', unread: 'No leídos', important: 'Importantes' });
export function createMailState() {
    return { account: '', folder: 'inbox', filter: 'all', query: '', cursor: '', history: [], data: null,
        loading: true, error: '', notice: '', selected: '', detail: null, detailLoading: false, detailError: '',
        body: null, bodyLoading: false, bodyError: '', syncing: false };
}
export function eligible(account) {
    return account?.granted === true && ['connected', 'partial'].includes(account.auth_state);
}
export function accountStatus(account) {
    if (account.auth_state === 'disconnected') return 'Desconectada';
    if (account.auth_state === 'reauth_required') return 'Vuelve a conectar';
    if (!account.granted) return 'Sin permiso de Correo';
    if (account.sync?.state === 'running') return 'Actualizando…';
    if (account.sync?.error) return 'Sin actualizar · copia guardada';
    if (!account.sync?.last_success_at) return 'Pendiente de primera lectura';
    return 'Copia guardada';
}
export function messageDate(value, { compact = false } = {}) {
    if (!Number.isFinite(value)) return 'Fecha no disponible';
    try { return new Intl.DateTimeFormat('es', compact ? { day: 'numeric', month: 'short' }
        : { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value)); }
    catch { return 'Fecha no disponible'; }
}
export function initials(value) {
    const clean = String(value || '').replace(/<[^>]*>/g, '').replace(/["<>]/g, '').trim();
    return clean.split(/\s+/).slice(0, 2).map((part) => [...part][0] || '').join('').toLocaleUpperCase() || '?';
}
