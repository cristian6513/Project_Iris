// A framed Face can request navigation, never access a native command or shell.
function postToDesktop(message, host = window) {
    if (host.parent === host) return false;
    const raw = new URL(host.location.href).searchParams.get('desktop_origin');
    if (!raw) return false;
    try {
        const url = new URL(raw);
        if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port
            || url.username || url.password || url.origin !== raw) return false;
        host.parent.postMessage(message, url.origin);
        return true;
    } catch { return false; }
}

export function requestDesktopSetup(host = window, section = '') {
    const message = { type: 'iris.desktop.configure' };
    if (section === 'voice') message.section = section;
    return postToDesktop(message, host);
}

export function publishDesktopTheme(theme, host = window) {
    return postToDesktop({ type: 'iris.desktop.theme', theme }, host);
}

export function publishDesktopFaceState(status, host = window) {
    if (host.parent === host) return false;
    const id = new URL(host.location.href).searchParams.get('desktop_load');
    if (!id) return false;
    return postToDesktop({ type: 'iris.desktop.face-state', id, status }, host);
}
