/* Wizard texts and controls derived from the runtime contract (A08.6).
 *
 * Development keeps the external Hermes installer, its precheck and autostart.
 * The packaged runtime already includes Hermes Agent: the same steps lead to
 * provider, messaging and diagnostics, and never offer to install, update or
 * autostart another Hermes. Controls follow `capabilities`; texts follow `mode`.
 * Pure logic so it can be tested without a browser.
 */
(function () {
    const PACKAGED_TEXT = Object.freeze({
        'document-title': 'IRIS OS — Configuración',
        'hero-kicker': 'CONFIGURACIÓN',
        'hero-copy': 'Conecta tu proveedor de IA, la mensajería, la voz y el avatar. El motor de IA ya viene incluido en Iris.',
        'readout-note': 'Iris incluye su motor de IA: solo falta conectar tu proveedor. No se instala nada aparte.',
        'mode-first-title': 'Primera configuración',
        'mode-first-desc': 'Configura IRIS OS paso a paso: proveedor de IA, voz, avatar y dispositivos.',
        'mode-basic-desc': 'Ajusta identidad, voz, avatar y dispositivos sin cambiar el proveedor de IA.',
        'mode-repair-title': 'Diagnóstico y reparación',
        'mode-repair-desc': 'Reinicia el motor incluido, valida tu proveedor y prueba la conexión.',
        'mode-advanced-desc': 'Proveedor y modelo, OAuth o API keys, canales y diagnóstico.',
        'card-full-title': 'Configurar proveedor de IA',
        'card-full-desc': 'Elige proveedor y modelo para el motor incluido. No se instala nada.',
        'card-skip-desc': 'Deja la IA para después y ve directo a voz y avatar.',
        'card-repair-title': 'Reparar el motor',
        'card-repair-desc': 'El motor incluido no responde. Reinícialo con tu configuración guardada.',
        'brain-title': 'Inteligencia artificial',
        'brain-tip': 'Iris incluye su motor de IA: se conecta a modelos de IA (Claude, GPT, Gemini…) y a canales de mensajería. Aquí eliges qué IA piensa por tu asistente; no hace falta instalar nada ni abrir la consola.',
        'setup-install': 'Motor de IA incluido',
        'setup-setup': 'Perfil del motor preparado',
        'setup-model': 'Elegir proveedor + modelo de IA',
        'setup-gateway': 'Iniciar IRIS',
        'gateway-title': 'Iniciar IRIS',
        'gateway-copy': 'El motor incluido conecta a Iris con tu proveedor de IA y con los canales que hayas activado. Inícialo cuando el proveedor esté guardado.',
        'refresh-hint': 'Consultando el inventario de proveedores y modelos del motor incluido.',
        'health-tip': 'Comprueba la ruta completa sin consumir tokens: motor incluido, su configuración, el servicio local en el puerto configurado (8642 por defecto), /health, las credenciales del proveedor, el WebSocket de Iris (8765) y la voz local. Si falla un requisito, solo se omiten las comprobaciones que dependen de él.',
        'launch-copy': 'Abre IRIS OS para empezar. La primera respuesta puede tardar un poco más mientras Iris se prepara.',
        'catalog-fallback': 'Sigue usando el catálogo incluido con esta versión de IRIS. El motor y su catálogo se actualizan junto con Iris.',
    });

    const HEALTH_ROWS = Object.freeze([
        ['cli_in_path', '1. Hermes CLI instalado', '1. Motor Hermes Agent incluido'],
        ['config_valid', '2. Config hermes válida', '2. Configuración del motor válida'],
        ['gateway_port', '3. API server escuchando (puerto 8642)', '3. Servicio local escuchando (puerto 8642)'],
        ['gateway_health', '4. Gateway responde JSON (/health)', '4. El motor responde (/health)'],
        ['provider_credentials', '5. Credenciales del proveedor listas', '5. Credenciales del proveedor listas'],
        ['iris_ws', '6. IRIS WebSocket activo (puerto 8765)', '6. IRIS WebSocket activo (puerto 8765)'],
        ['voice_extra', '7. Voz local en su entorno aislado', '7. Voz local en su entorno aislado'],
    ]);

    function profile(info) {
        const valid = info && typeof info === 'object' && typeof info.mode === 'string';
        const capabilities = valid && info.capabilities && typeof info.capabilities === 'object'
            ? info.capabilities : {};
        const allowed = name => capabilities[name] === true;
        return Object.freeze({
            known: Boolean(valid),
            packaged: valid && info.mode === 'packaged',
            mode: valid ? info.mode : 'unknown',
            irisVersion: valid ? String(info.iris_version || '') : '',
            hermesVersion: valid ? String(info.hermes_version || '') : '',
            // Unknown runtime: offer nothing that the backend might refuse.
            canInstallHermes: allowed('external_hermes_install'),
            canAutostart: allowed('external_autostart'),
            canInstallExtras: allowed('profile_extras'),
        });
    }

    function text(key, runtime, fallback = '') {
        return runtime && runtime.packaged && PACKAGED_TEXT[key] ? PACKAGED_TEXT[key] : fallback;
    }

    // Development keeps the markup as authored; packaged replaces only the
    // product texts that would offer an installation it does not have.
    function applyTexts(root, runtime) {
        if (!runtime || !runtime.packaged || !root) return 0;
        let changed = 0;
        for (const node of root.querySelectorAll('[data-runtime-text]')) {
            const value = PACKAGED_TEXT[node.dataset.runtimeText];
            if (value !== undefined) {
                node.textContent = value;
                changed++;
            }
        }
        return changed;
    }

    function healthRows(runtime) {
        return HEALTH_ROWS.map(([id, development, packaged]) => ({
            id, label: runtime && runtime.packaged ? packaged : development,
        }));
    }

    // Diagnostics cards for the bundled engine. Development keeps its picker.
    // The same step already has the provider form, «Iniciar IRIS» and «Siguiente», so the
    // cards only offer what those don't: restarting an engine that has a provider (QA12).
    function packagedModePlan(status) {
        const state = status && typeof status.status === 'string' ? status.status : 'unknown';
        const card = (visible, title = '', desc = '', mode = '') => ({ visible, title, desc, mode });
        const only = repair => ({ full: card(false), skip: card(false), repair: card(repair) });
        if (state === 'connected') {
            return {
                tone: 'ok', title: 'Motor incluido activo',
                detail: 'Si deja de responder, puedes reiniciarlo con tu configuración guardada.',
                select: '', cards: only(true),
            };
        }
        if (state === 'installed') {
            return {
                tone: 'info', title: 'Motor incluido detenido',
                detail: 'Ya tiene proveedor y modelo. Inícialo con «Iniciar IRIS», más arriba.',
                select: '', cards: only(true),
            };
        }
        if (state === 'needs_setup') {
            return {
                tone: 'info', title: '', detail: '',
                select: '', cards: only(false),
            };
        }
        // missing / damaged / unknown: the package itself needs repair.
        return {
            tone: 'warn', title: 'El motor incluido no está disponible',
            detail: 'Repara o reinstala Iris desde su instalador; el motor se repara con el paquete de Iris. Mientras tanto puedes seguir con «Siguiente» y configurar voz y avatar.',
            select: '', cards: only(false),
        };
    }

    function channelState(platform) {
        const availability = platform && platform.availability;
        if (!availability || typeof availability !== 'object') return { available: true, detail: '' };
        return {
            available: availability.available !== false,
            detail: typeof availability.detail === 'string' ? availability.detail : '',
            code: typeof availability.code === 'string' ? availability.code : '',
        };
    }

    globalThis.IrisWizardRuntime = Object.freeze({
        PACKAGED_TEXT, profile, text, applyTexts, healthRows, packagedModePlan, channelState,
    });
})();
