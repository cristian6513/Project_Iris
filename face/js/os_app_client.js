import { appNoticesFor } from './app_notice.js?v=2';

/** The installation an installed module was served from: an update or reinstall is another one. */
export const installationOf = (entry) => /^\/api\/app-packages\/files\/(ins_[a-f0-9]{32})\//.exec(entry || '')?.[1];

/** Calls from one installed window/widget, pinned to that exact installation. */
export function createAppCaller(id, entry, signal, fetchImpl = globalThis.fetch) {
    const installation = installationOf(entry);
    if (!/^[a-z][a-z0-9-]{1,31}$/.test(id) || !installation) throw new Error('La app no tiene una instalación válida.');
    return async (action, args = {}) => {
        if (!/^[a-z][a-z0-9_]{0,47}$/.test(action)) throw new Error('Operación no válida.');
        const body = JSON.stringify(args);
        if (new TextEncoder().encode(body).length > 32768) throw new Error('Los argumentos superan 32 KiB.');
        const response = await fetchImpl(`/api/app-packages/${id}/call/${action}`, {
            method: 'POST', cache: 'no-store', signal,
            headers: { 'Content-Type': 'application/json', 'X-Iris-App-Installation': installation }, body,
        });
        const answer = await response.json();
        if (!response.ok || answer?.status !== 'ok') throw new Error(answer?.error?.message || answer?.message || 'La app no pudo completar la operación.');
        return answer.data;
    };
}

/** Installed-package binding belongs to its module loader, not the general shell: `call` for an
 * app with Python, and notices (core/app_notices.py) for every app installed from Iris Store. */
export function withAppCaller(module, factory, id, entry, { python = true } = {}) {
    if (typeof module?.[factory] !== 'function') return module;
    return { ...module, [factory]: context => module[factory](Object.freeze({
        ...context, ...appNoticesFor(id, installationOf(entry)), ...(python ? { call: createAppCaller(id, entry, context.signal) } : {}),
    })) };
}
