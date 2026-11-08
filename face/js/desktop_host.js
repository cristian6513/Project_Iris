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

export function requestDesktopSetup(host = window) {
    return postToDesktop({ type: 'iris.desktop.configure' }, host);
}

export function publishDesktopTheme(theme, host = window) {
    return postToDesktop({ type: 'iris.desktop.theme', theme }, host);
}
