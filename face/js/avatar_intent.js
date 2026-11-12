/**
 * Quick Configuration's avatar actions: the latest one wins (Codex qa10 review, R5).
 *
 * "Volver a IRIS" waits for Core to forget the person's model; meanwhile the
 * user can upload another model or drop one on the page.
 * Each action takes a token before its first await and only changes the scene
 * if no later action started: a slow answer never replaces a newer model.
 *
 * The page's drag and drop is another surface (Face's AvatarSwapCoordinator),
 * so the token is shared with it through `reserve()`: a reservation taken from
 * Face before the first request, and handed to Face with the result, so a late
 * answer can neither win over a later drop nor start a new selection when it
 * arrives (Codex qa10 closure, C2).
 */
export function createIntentGate() {
    let current = 0;
    return {
        begin() {
            current += 1;
            return current;
        },
        invalidate() {
            current += 1;
        },
        isCurrent(token) {
            return token === current;
        },
    };
}

/**
 * `useIris()` clears the person's model, so IRIS' own VRM shows; `upload(file)`
 * stores a model and shows it. Both call `dispatch({vrmUrl, isCurrent})` (an empty
 * `vrmUrl` is IRIS') only while they are still the latest action, and say whether
 * they were superseded.
 * `reserve()` returns Face's selection reservation (a function telling whether
 * it is still the latest, with `finish()`), or null while Face has none.
 */
export function createAvatarActions({ fetchImpl, dispatch, reserve = () => null, FormDataImpl = globalThis.FormData } = {}) {
    const gate = createIntentGate();

    function begin() {
        const token = gate.begin();
        const shared = typeof reserve === 'function' ? reserve() || null : null;
        return {
            isCurrent: () => gate.isCurrent(token) && (!shared || shared()),
            detail: shared ? { isCurrent: shared } : {},
            signal: shared?.signal,
            // An action that dispatches nothing ends its reservation.
            release: () => shared?.finish?.(),
        };
    }

    return {
        invalidate: () => gate.invalidate(),

        async useIris() {
            const intent = begin();
            let ok = false;
            try {
                const res = await fetchImpl('/api/update-identity', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ avatar_vrm_path: '' }),
                });
                const data = await res.json().catch(() => ({}));
                ok = res.ok && ['saved', 'noop'].includes(data.status);
            } catch {
                ok = false;
            }
            const superseded = !intent.isCurrent();
            if (ok && !superseded) dispatch({ vrmUrl: '', ...intent.detail });
            else intent.release();
            return { ok, superseded };
        },

        async upload(file) {
            const intent = begin();
            const pmx = /\.zip$/i.test(file.name);
            let data, modelLabel;
            try {
                if (pmx) {
                    const { preparePmxUpload } = await import('./pmx_avatar.js');
                    const prepared = await preparePmxUpload(file, intent.signal);
                    if (!intent.isCurrent()) {
                        intent.release();
                        return { url: '', superseded: true };
                    }
                    if (!prepared) {
                        intent.release();
                        return { url: '', superseded: false, cancelled: true };
                    }
                    ({ file, label: modelLabel } = prepared);
                }
                const form = new FormDataImpl();
                form.append('file', file, file.name);
                let response;
                try {
                    response = await fetchImpl('/api/avatar/upload', { method: 'POST', body: form });
                } catch (error) {
                    throw new Error('No pude conectar con IRIS para guardar el avatar. Comprueba la conexión y vuelve a intentarlo.', { cause: error });
                }
                data = await response.json().catch(() => ({}));
                if (!response.ok || data.status !== 'saved' || !data.avatar_url) {
                    throw new Error(data.detail || 'IRIS no pudo guardar el avatar.');
                }
            } catch (error) {
                const superseded = !intent.isCurrent();
                intent.release();
                if (superseded) return { url: '', superseded: true };
                throw error;
            }
            const superseded = !intent.isCurrent();
            if (!superseded) dispatch({ vrmUrl: data.avatar_url, ...intent.detail, ...(pmx ? { reload: true } : {}) });
            else intent.release();
            return { url: data.avatar_url, superseded, ...(modelLabel ? { modelLabel } : {}) };
        },
    };
}
