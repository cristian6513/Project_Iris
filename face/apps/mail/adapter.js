// Generated from apps/mail/ui/adapter.js by scripts/build_app_catalog.mjs: edit the original.
/** Local list/detail; explicit body reads and Gmail-only sync through Core. */
export function createMailClient({ fetchImpl = (...args) => globalThis.fetch(...args) } = {}) {
    const controllers = new Map();
    let closed = false;
    async function call(path, body, detail = false, method) {
        if (closed) throw new DOMException('Cerrado', 'AbortError');
        const controller = new AbortController(); controllers.set(controller, detail);
        try {
            const response = await fetchImpl(`/api/mail${path}`, { method: method || (body ? 'POST' : 'GET'),
                cache: 'no-store', signal: controller.signal,
                headers: body ? { 'Content-Type': 'application/json' } : undefined,
                body: body ? JSON.stringify(body) : undefined });
            const data = await response.json();
            if (!response.ok) {
                const error = new Error(data?.error?.message || 'No se pudo consultar Correo.');
                error.code = data?.error?.code; error.status = response.status; throw error;
            }
            if (closed || controller.signal.aborted) throw new DOMException('Cerrado', 'AbortError');
            return data;
        } finally { controllers.delete(controller); }
    }
    return {
        read(params) { return call(`?${new URLSearchParams(params)}`); },
        detail(key) { return call(`/messages/${encodeURIComponent(key)}`, undefined, true); },
        body(key) { return call(`/messages/${encodeURIComponent(key)}/body`, {}, true); },
        sync(account_ids) { return call('/sync', { account_ids }); },
        classification() { return call('/classification'); },
        probe(provider, model) { return call('/classification/probe', { provider, model }); },
        configure(options, route, confirmed) { return call('/classification', { options, route, confirmed }, false, 'PUT'); },
        correct(identity, label, revision) { return call(`/classification/${encodeURIComponent(identity)}/correction`, { label, revision }); },
        abortDetail() { for (const [controller, detail] of controllers) if (detail) controller.abort(); },
        abort() { for (const controller of controllers.keys()) controller.abort(); },
        dispose() { closed = true; for (const controller of controllers.keys()) controller.abort(); },
    };
}
