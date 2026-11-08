/* Provider login state survives a wizard reload. No access tokens enter this UI. */
class IrisProviderAuth {
    constructor({ getProvider, onChange, onAuthenticated, fetchImpl = (...args) => fetch(...args),
        storage = sessionStorage, interval = 1000 }) {
        Object.assign(this, { getProvider, onChange, onAuthenticated, fetchImpl, storage, interval });
        this.generation = 0;
        this.session = null;
        this.startingProvider = '';
        this.timer = null;
        this.key = 'iris.provider-login.v1';
    }

    async request(path, options = {}) {
        const response = await this.fetchImpl(`/api/provider-auth/sessions${path}`, {
            ...options, cache: 'no-store', headers: { 'Content-Type': 'application/json' },
            signal: AbortSignal.timeout(15000),
        });
        const data = await response.json();
        if (!response.ok) {
            const error = new Error(typeof data.detail === 'string' ? data.detail : 'No se pudo comprobar el acceso.');
            error.status = response.status;
            throw error;
        }
        return data;
    }

    remember(session) {
        this.session = session;
        try {
            if (session) this.storage.setItem(this.key, JSON.stringify({
                session_id: session.session_id, provider: session.provider,
            }));
            else this.storage.removeItem(this.key);
        } catch { /* Authentication still works when storage is unavailable. */ }
    }

    stopPoll() {
        ++this.generation;
        clearTimeout(this.timer);
        this.timer = null;
    }

    savedSession() {
        try {
            const stored = JSON.parse(this.storage.getItem(this.key) || 'null');
            if (stored && typeof stored.provider === 'string' && /^[\w-]{20,80}$/.test(stored.session_id)) return stored;
        } catch { /* Ignore unavailable storage or an incomplete entry. */ }
        return null;
    }

    async cancel({ silent = false } = {}) {
        this.stopPoll();
        const generation = this.generation;
        const old = this.session || this.savedSession();
        const startingProvider = this.startingProvider;
        this.startingProvider = '';
        let result;
        if (old && ['starting', 'pending', 'disconnected', undefined].includes(old.status)) {
            try {
                result = await this.request(`/${encodeURIComponent(old.session_id)}`, { method: 'DELETE' });
            } catch (error) {
                if (generation !== this.generation) return false;
                if (error.status !== 404) {
                    this.remember(old);
                    this.onChange({ ...old, status: 'disconnected',
                        message: 'No se pudo confirmar la cancelación. Vuelve a comprobar la conexión.' });
                    return false;
                }
            }
        }
        if (generation !== this.generation) return false;
        this.remember(null);
        if (!silent && old) {
            // Approval may have completed before the cancellation reached Hermes.
            // Report the server's terminal result instead of claiming it was cancelled.
            this.accept(result || { ...old, status: 'cancelled',
                message: 'Inicio de sesión cancelado.' }, generation);
        } else if (!silent && startingProvider) {
            this.onChange({ provider: startingProvider, status: 'cancelled',
                message: 'Inicio de sesión cancelado.' });
        }
        return true;
    }

    async start(provider, existing = false) {
        if (!await this.cancel({ silent: true })) return;
        const generation = this.generation;
        this.startingProvider = provider;
        this.onChange({ provider, status: 'starting' });
        try {
            const data = await this.request('', { method: 'POST', body: JSON.stringify({ provider, existing }) });
            if (generation !== this.generation || provider !== this.getProvider()) {
                await this.request(`/${encodeURIComponent(data.session_id)}`, { method: 'DELETE' });
                return;
            }
            this.startingProvider = '';
            this.accept(data, generation);
        } catch (error) {
            if (generation === this.generation) {
                this.startingProvider = '';
                this.onChange({ provider, status: 'error', message: error.message });
            }
        }
    }

    async resume(provider) {
        if (this.session) return;
        const stored = this.savedSession();
        if (!stored || stored.provider !== provider) return;
        this.stopPoll();
        this.session = stored;
        await this.poll(this.generation);
    }

    async recheck() {
        this.stopPoll();
        if (this.session) await this.poll(this.generation);
    }

    accept(data, generation) {
        if (generation !== this.generation || data.provider !== this.getProvider()) return;
        this.remember(data);
        this.onChange(data);
        if (data.status === 'approved') {
            this.onAuthenticated(data.provider);
            this.remember(null);
        } else if (['starting', 'pending'].includes(data.status)) {
            this.timer = setTimeout(() => void this.poll(generation), this.interval);
        } else {
            this.remember(null);
        }
    }

    async poll(generation) {
        const session = this.session;
        if (!session || generation !== this.generation) return;
        try {
            const data = await this.request(`/${encodeURIComponent(session.session_id)}`);
            if (data.provider !== session.provider) throw new Error('El acceso no corresponde al proveedor seleccionado.');
            this.accept(data, generation);
        } catch (error) {
            if (generation !== this.generation) return;
            if (error.status === 404) this.remember(null);
            this.onChange({ ...session, status: 'disconnected', message: error.message });
        }
    }
}

globalThis.IrisProviderAuth = IrisProviderAuth;
