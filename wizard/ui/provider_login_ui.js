/* Uses the existing wizard's provider selection and checklist. */
let _irisProviderLogin = null;
let _lastProviderLoginView = '';

function _providerLoginController() {
    if (!_irisProviderLogin) {
        _irisProviderLogin = new IrisProviderAuth({
            getProvider: () => document.getElementById('use-oauth')?.checked ? selectedLLMProvider() : '',
            onChange: _renderProviderLogin,
            onAuthenticated: provider => {
                // Q04: a new session can change what the account may use (a Nous plan, for
                // example); the list read before signing in is stale either way.
                if (provider !== selectedLLMProvider() || !document.getElementById('use-oauth')?.checked) return;
                wizardState.oauth_authenticated = true;
                wizardState.oauth_authenticated_provider = provider;
                _refreshCatalogAfterLogin();
            },
        });
    }
    return _irisProviderLogin;
}

function _refreshCatalogAfterLogin() {
    // A refresh that started before the sign-in would still bring the old list.
    if (window._catalogRefreshState?.inFlight) cancelCatalogRefresh();
    void loadProviderModels();
}

function _closeOAuthStream() {
    // Retained name for the wizard's provider/mode-change hooks.
    _lastProviderLoginView = '';
    const connect = document.getElementById('btn-auth-oauth');
    if (connect) {
        connect.disabled = false;
        connect.textContent = 'Conectar cuenta';
    }
    const panel = document.getElementById('oauth-flow-panel');
    if (panel) panel.style.display = 'none';
    if (_irisProviderLogin) void _irisProviderLogin.cancel({ silent: true });
}

function _resumeProviderLogin(provider) {
    if (document.getElementById('use-oauth')?.checked) {
        void _providerLoginController().resume(provider);
    }
}

// «Conectar cuenta» next to the API key: one click switches to the sign-in and starts it.
function chooseOAuthLogin() {
    const toggle = document.getElementById('use-oauth');
    if (!toggle) return;
    toggle.checked = true;
    toggleOAuthMode();
    void authenticateProvider();
}

function chooseApiKey() {
    const toggle = document.getElementById('use-oauth');
    if (!toggle || toggle.disabled) return;
    toggle.checked = false;
    toggleOAuthMode();
    document.getElementById('llm-api-key')?.focus();
}

async function authenticateProvider(existing = false) {
    const provider = selectedLLMProvider();
    if (!provider || !['oauth', 'api+oauth'].includes(LLM_PROVIDERS[provider]?.auth)) return;
    invalidateProviderModels();
    wizardState.oauth_authenticated = false;
    wizardState.oauth_authenticated_provider = '';
    _lastProviderLoginView = '';
    await _providerLoginController().start(provider, existing);
}

function _renderProviderLogin(state) {
    if (state.provider !== selectedLLMProvider() || !document.getElementById('use-oauth')?.checked) return;
    const panel = document.getElementById('oauth-flow-panel');
    const box = document.getElementById('oauth-url-box');
    const status = document.getElementById('oauth-flow-log');
    const connect = document.getElementById('btn-auth-oauth');
    if (!panel || !box || !status || !connect) return;
    panel.style.display = 'block';
    connect.disabled = ['starting', 'pending'].includes(state.status);
    connect.textContent = connect.disabled ? 'Conectando…' : 'Conectar cuenta';
    const signature = JSON.stringify(state);
    if (_lastProviderLoginView === signature) {
        _providerLoginExpiry(state);
        return;
    }
    _lastProviderLoginView = signature;
    box.replaceChildren();
    box.style.display = 'block';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const element = (tag, text, className = '') => {
        const node = document.createElement(tag);
        node.textContent = text;
        node.className = className;
        return node;
    };
    const action = (text, callback) => {
        const button = element('button', text, 'btn-secondary btn-sm');
        button.type = 'button';
        button.addEventListener('click', callback);
        actions.appendChild(button);
        return button;
    };
    const actions = element('div', '', 'provider-login-actions');
    const providerName = LLM_PROVIDERS[state.provider]?.name || state.provider;
    box.appendChild(element('div', providerName, 'oauth-url-header'));
    if (state.status === 'pending') {
        box.appendChild(element('p', state.flow === 'device_code'
            ? 'Abre la página del proveedor y, si te lo pide, introduce este código. Iris detectará cuando termines.'
            : 'Autoriza el acceso en la página del proveedor. Al terminar, Iris recibirá la confirmación automáticamente.',
        'oauth-url-hint'));
        if (state.user_code) {
            const code = element('code', state.user_code, 'provider-login-code');
            code.id = 'oauth-device-code';
            box.appendChild(code);
            action('Copiar código', async event => {
                const button = event.currentTarget;
                try {
                    await navigator.clipboard.writeText(state.user_code);
                    button.textContent = 'Copiado';
                } catch { status.textContent = 'Puedes seleccionar y copiar el código que aparece arriba.'; }
            });
        }
        try {
            const url = new URL(state.url);
            if (url.protocol !== 'https:' || url.username || url.password) throw new Error('Invalid URL');
            const link = element('a', 'Abrir página de autorización', 'oauth-open-btn');
            link.id = 'oauth-open-link';
            link.href = url.href;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            actions.appendChild(link);
        } catch { status.textContent = 'El proveedor no devolvió un enlace válido. Cancela y vuelve a intentar.'; }
        const expiry = element('p', '', 'oauth-url-hint');
        expiry.id = 'oauth-expiry';
        box.appendChild(expiry);
        status.textContent = 'Esperando tu autorización…';
    } else if (state.status === 'starting') {
        status.textContent = 'Preparando el acceso con Hermes…';
    } else if (state.status === 'approved') {
        status.textContent = 'Sesión iniciada. Comprueba abajo los modelos disponibles para tu cuenta.';
    } else {
        status.textContent = state.message || 'No se pudo completar el acceso.';
        if (state.status === 'external') {
            action('Comprobar sesión existente', () => void authenticateProvider(true));
        }
        if (state.status === 'disconnected') {
            action('Volver a comprobar', () => void _providerLoginController().recheck());
        }
        if (LLM_PROVIDERS[state.provider]?.auth === 'api+oauth') {
            action('Usar API key', chooseApiKey);
        }
    }
    if (['starting', 'pending', 'disconnected'].includes(state.status)) {
        action('Cancelar', () => void _providerLoginController().cancel());
    }
    box.appendChild(actions);
    _providerLoginExpiry(state);
}

function _providerLoginExpiry(state) {
    const node = document.getElementById('oauth-expiry');
    if (node && state.expires_at) {
        const seconds = Math.max(0, Math.ceil(state.expires_at - Date.now() / 1000));
        node.textContent = `El enlace vence en ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}.`;
    }
}
