/*
 * IRIS OS — Sitio de presentación
 *
 * Gestor de ventanas ligero que imita el escritorio de IRIS OS:
 * cada <article data-app> se abre como una ventana con titlebar,
 * arrastre, redimensión, minimizar/maximizar y su pestaña en el
 * tabstrip. Incluye la ruta recomendada (1 → 2 → 3), el pie
 * «Qué hago después» y un mini tutorial. Sin dependencias.
 */
(() => {
    'use strict';

    const root = document.documentElement;
    // Una carga tardía no debe reactivar un escritorio que ya pasó a lectura estática.
    if (window.irisSiteBoot && window.irisSiteBoot.recovered) return;
    const $ = (selector, scope = document) => scope.querySelector(selector);
    const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));
    const clamp = (value, min, max) => Math.min(Math.max(value, min), Math.max(min, max));

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    // También en horizontal: una ventana de escritorio no cabe entre la barra
    // superior y el dock cuando el navegador tiene poca altura.
    const compactLayout = window.matchMedia('(max-width: 760px), (max-height: 600px)');

    const storage = {
        get(key) {
            try { return window.localStorage.getItem(key); } catch { return null; }
        },
        set(key, value) {
            try { window.localStorage.setItem(key, value); } catch { /* sin almacenamiento */ }
        },
    };

    const session = {
        get(key) {
            try { return window.sessionStorage.getItem(key); } catch { return null; }
        },
        set(key, value) {
            try { window.sessionStorage.setItem(key, value); } catch { /* sin almacenamiento */ }
        },
    };

    // Presets de face/js/os_theme.js. Las claves son los ids internos de IRIS OS;
    // las etiquetas son los nombres que muestra su Theme Studio.
    const THEMES = Object.freeze({
        iris: { label: 'Cyber', wall: 'assets/wall-city.webp', transition: 'none' },
        rose: { label: 'Sakura', wall: 'assets/wall-sakura.webp', transition: 'iris' },
        verdant: { label: 'Hacker', wall: 'assets/wall-hacker.webp', transition: 'glitch' },
        sunrise: { label: 'Classic', wall: 'assets/wall-classic.webp', transition: 'slide' },
    });
    const THEME_KEY = 'irisSite.theme';
    const BOOT_KEY = 'irisSite.booted';
    const TOUR_KEY = 'irisSite.tourDone';
    const VISITED_KEY = 'irisSite.visited';

    const WINDOW_SIZES = Object.freeze({
        s: { w: 560, h: 480 },
        m: { w: 780, h: 600 },
        l: { w: 980, h: 660 },
    });

    const LAUNCHER_GROUPS = Object.freeze([
        { id: 'start', label: 'Empieza aquí' },
        { id: 'discover', label: 'Descubre' },
        { id: 'deep', label: 'Profundiza' },
        { id: 'community', label: 'Comunidad' },
    ]);
    const ROUTE_LENGTH = 3;

    const isApple = /Mac|iPhone|iPad/i.test(
        (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || navigator.userAgent,
    );
    const LAUNCHER_SHORTCUT = isApple ? '⌘ + K' : 'Ctrl + K';

    const home = $('#home');
    const hero = $('.hero');
    const heroTitle = $('.hero h1');
    const layer = $('#window-layer');
    const tabstrip = $('#tabstrip');
    const dock = $('.dock');
    const launcher = $('#launcher');
    const launcherBody = $('[data-launcher-body]');
    const launcherSearch = $('[data-launcher-search]');
    const launcherEmpty = $('[data-launcher-empty]');
    const fallback = $('#apps');

    /* ------------------------------------------------------------
       Registro de apps (desde el HTML)
       ------------------------------------------------------------ */

    const apps = new Map();
    for (const article of $$('article[data-app]', fallback)) {
        const data = article.dataset;
        apps.set(data.app, {
            id: data.app,
            el: article,
            title: data.title || data.app,
            subtitle: data.subtitle || '',
            icon: data.icon || 'core',
            accent: data.accent || 'cyan',
            size: WINDOW_SIZES[data.size] || WINDOW_SIZES.m,
            group: data.group || 'discover',
            route: Number(data.route) || 0,
            next: data.next || '',
            tag: data.tag || '',
        });
    }

    const routeApps = [...apps.values()].filter((app) => app.route).sort((a, b) => a.route - b.route);

    function svgIcon(name, className = '') {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        if (className) svg.setAttribute('class', className);
        svg.setAttribute('aria-hidden', 'true');
        const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
        use.setAttribute('href', `#i-${name}`);
        svg.append(use);
        return svg;
    }

    function osIcon(name) {
        const span = document.createElement('span');
        span.className = 'os-icon';
        span.append(svgIcon(name));
        return span;
    }

    function element(tag, className = '', text = '') {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text) node.textContent = text;
        return node;
    }

    // animationend burbujea: ignora las animaciones de los hijos. El temporizador
    // cubre pestañas en segundo plano, donde las animaciones pueden no avanzar.
    function onOwnAnimationEnd(target, callback, timeout = 700) {
        let done = false;
        const cancel = () => {
            done = true;
            target.removeEventListener('animationend', handler);
            window.clearTimeout(timer);
        };
        const finish = () => {
            if (done) return;
            cancel();
            callback();
        };
        const handler = (event) => {
            if (event.target === target) finish();
        };
        const timer = window.setTimeout(finish, timeout);
        target.addEventListener('animationend', handler);
        return cancel;
    }

    /* ------------------------------------------------------------
       Ruta recomendada: progreso guardado por visitante
       ------------------------------------------------------------ */

    const visited = new Set((() => {
        try {
            // Valid JSON that is not a list (an older or edited value) once crashed initialization.
            const saved = JSON.parse(storage.get(VISITED_KEY) || '[]');
            return Array.isArray(saved) ? saved : [];
        } catch { return []; }
    })());

    function markVisited(id) {
        if (visited.has(id)) return;
        visited.add(id);
        storage.set(VISITED_KEY, JSON.stringify([...visited]));
    }

    function renderRoute() {
        for (const badge of $$('[data-route-badge]')) {
            const done = visited.has(badge.dataset.routeBadge);
            badge.classList.toggle('done', done);
            badge.setAttribute('aria-label', done ? 'visto' : `paso ${badge.textContent}`);
        }
        const nextUp = routeApps.find((app) => !visited.has(app.id));
        for (const button of $$('.dock-route [data-open]', dock)) {
            button.classList.toggle('next-up', Boolean(nextUp) && button.dataset.open === nextUp.id);
        }
    }

    /* ------------------------------------------------------------
       Gestor de ventanas
       ------------------------------------------------------------ */

    const windows = new Map(); // id -> registro de ventana
    let zIndex = 20;
    let cascade = 0;
    let focusedId = null;

    function deskBounds() {
        const rect = layer.getBoundingClientRect();
        const dockRect = dock.getBoundingClientRect();
        // Deja sitio para el tabstrip que aparece sobre el dock.
        const bottom = Math.min(rect.height, dockRect.top - rect.top - 58);
        return { width: rect.width, height: rect.height, bottom: Math.max(bottom, 0) };
    }

    function initialRect(app) {
        // Las hojas usan su tamaño por CSS. Conserva debajo un rectángulo de
        // escritorio útil para cuando se rote el dispositivo o crezca la vista.
        if (compactLayout.matches) return { left: 16, top: 14, width: app.size.w, height: app.size.h };
        const desk = deskBounds();
        const height = Math.min(app.size.h, desk.bottom - 20);
        const offset = cascade * 28;
        cascade = (cascade + 1) % 6;

        // En pantallas amplias las ventanas se abren a la derecha del hero
        // para no tapar el titular; si no cabe, se centran.
        // El titular puede ser más ancho que su caja: mide el texto real.
        const titleRange = document.createRange();
        titleRange.selectNodeContents(heroTitle);
        const heroEdge = Math.max(hero.getBoundingClientRect().right, titleRange.getBoundingClientRect().right);
        const heroRight = heroEdge - layer.getBoundingClientRect().left + 28;
        let width;
        let left;
        if (desk.width >= 1180 && desk.width - heroRight - 24 >= 540) {
            left = heroRight + offset;
            width = Math.min(app.size.w, desk.width - left - 24);
        } else {
            width = Math.min(app.size.w, desk.width - 32);
            left = (desk.width - width) / 2 + offset;
        }
        left = clamp(left, 16, desk.width - width - 16);
        const top = clamp(14 + offset, 10, desk.bottom - height);
        return { left, top, width, height };
    }

    function applyRect(record, rect) {
        const { win } = record;
        win.style.left = `${Math.round(rect.left)}px`;
        win.style.top = `${Math.round(rect.top)}px`;
        win.style.width = `${Math.round(rect.width)}px`;
        win.style.height = `${Math.round(rect.height)}px`;
    }

    function currentRect(record) {
        const { win } = record;
        return { left: win.offsetLeft, top: win.offsetTop, width: win.offsetWidth, height: win.offsetHeight };
    }

    function maximizedRect() {
        const desk = deskBounds();
        return { left: 12, top: 10, width: desk.width - 24, height: Math.max(0, desk.bottom - 20) };
    }

    function fitRect(rect) {
        const desk = deskBounds();
        const width = Math.min(rect.width, desk.width - 24);
        const height = Math.min(rect.height, desk.bottom - 10);
        return {
            width,
            height,
            left: clamp(rect.left, 8, desk.width - width - 8),
            top: clamp(rect.top, 8, desk.bottom - height),
        };
    }

    // Pie «Qué hago después»: siguiente paso de la ruta o app sugerida.
    function buildNextStep(app) {
        const next = apps.get(app.next);
        if (!next) return null;

        const footer = element('footer', 'app-next');
        footer.setAttribute('aria-label', 'Qué hacer después');
        const info = element('div');
        const kicker = element('p', 'kicker');

        if (app.route) {
            const label = app.route === ROUTE_LENGTH
                ? `Paso ${app.route} de ${ROUTE_LENGTH} · Ya conoces lo esencial`
                : `Paso ${app.route} de ${ROUTE_LENGTH} · Ruta recomendada`;
            const dots = element('span', 'route-dots');
            dots.setAttribute('aria-hidden', 'true');
            for (let step = 1; step <= ROUTE_LENGTH; step += 1) {
                dots.append(element('i', step <= app.route ? 'on' : ''));
            }
            kicker.append(dots, document.createTextNode(label));
        } else if (next.route === 1) {
            kicker.textContent = '¿Primera vez? Empieza por lo esencial';
        } else {
            kicker.textContent = 'Qué hacer después';
        }

        let heading = `Sigue explorando: ${next.title}`;
        if (app.route && app.route < ROUTE_LENGTH) heading = `Siguiente: ${next.title}`;
        else if (next.route === 1) heading = next.title;
        info.append(kicker, element('strong', '', heading), element('small', '', next.subtitle));

        const actions = element('div', 'app-next-actions');
        const go = element('button', 'btn primary');
        go.type = 'button';
        go.dataset.open = next.id;
        go.append(document.createTextNode(`Abrir ${next.title}`), svgIcon('arrow', 'ico'));
        const all = element('button', 'btn', 'Ver todas las apps');
        all.type = 'button';
        all.dataset.launcher = '';
        all.setAttribute('aria-controls', 'launcher');
        all.setAttribute('aria-expanded', 'false');
        actions.append(go, all);

        footer.append(info, actions);
        return footer;
    }

    function buildWindow(app) {
        const win = element('section', 'os-window');
        win.dataset.app = app.id;
        win.dataset.accent = app.accent;
        win.tabIndex = -1;
        win.setAttribute('role', 'dialog');
        win.setAttribute('aria-modal', 'false');
        win.setAttribute('aria-labelledby', `win-title-${app.id}`);

        const titlebar = element('header', 'os-window-titlebar');
        const label = element('div');
        const title = element('h2', '', app.title);
        title.id = `win-title-${app.id}`;
        label.append(osIcon(app.icon), title);
        if (app.tag) label.append(element('span', 'win-tag', app.tag));
        label.append(element('em', '', `iris://${app.id}`));

        const controls = element('nav');
        controls.setAttribute('aria-label', `Controles de ${app.title}`);
        for (const [act, icon, text] of [
            ['min', 'min', 'Minimizar'],
            ['max', 'max', 'Maximizar'],
            ['close', 'close', 'Cerrar'],
        ]) {
            const button = element('button');
            button.type = 'button';
            button.dataset.act = act;
            button.setAttribute('aria-label', `${text} ${app.title}`);
            button.title = text;
            button.append(svgIcon(icon, 'ico'));
            controls.append(button);
        }
        titlebar.append(label, controls);

        const body = element('div', 'os-window-body');
        const resize = element('div', 'os-window-resize');
        resize.setAttribute('aria-hidden', 'true');

        win.append(titlebar, body, resize);
        return { win, titlebar, body, resize };
    }

    function openApp(id, { invoker = null, silent = false } = {}) {
        if (root.classList.contains('no-js')) return;
        const app = apps.get(id);
        if (!app) return;
        closeLauncher();

        let existing = windows.get(id);
        if (existing && existing.closing) {
            // Reabrir mientras se cierra: termina el cierre y crea una ventana nueva.
            existing.finishClose();
            existing = null;
        }
        if (existing) {
            if (existing.minimized) restoreWindow(existing);
            focusWindow(existing, { moveFocus: !silent });
            if (!silent) setHash(id);
            return;
        }

        const parts = buildWindow(app);
        const record = {
            id,
            app,
            ...parts,
            minimized: false,
            maximized: false,
            restoreRect: null,
            invoker: invoker instanceof HTMLElement ? invoker : null,
        };

        app.el.hidden = false;
        parts.body.append(app.el);
        const nextStep = buildNextStep(app);
        if (nextStep) parts.body.append(nextStep);
        layer.append(parts.win);
        applyRect(record, initialRect(app));
        windows.set(id, record);
        wireWindow(record);

        if (!reduceMotion.matches) {
            parts.win.classList.add('opening');
            onOwnAnimationEnd(parts.win, () => parts.win.classList.remove('opening'));
        }
        if (id === 'instalar') parts.body.classList.add('term-ready');

        markVisited(id);
        focusWindow(record, { moveFocus: !silent });
        if (!silent) setHash(id);
        renderChrome();
    }

    function focusWindow(record, { moveFocus = false } = {}) {
        zIndex += 1;
        record.win.style.zIndex = String(zIndex);
        for (const other of windows.values()) {
            other.win.classList.toggle('focused', other === record);
        }
        focusedId = record.id;
        if (moveFocus) record.win.focus({ preventScroll: true });
        renderChrome();
    }

    function topVisibleWindow() {
        let best = null;
        for (const record of windows.values()) {
            if (record.minimized || record.closing) continue;
            if (!best || Number(record.win.style.zIndex) > Number(best.win.style.zIndex)) best = record;
        }
        return best;
    }

    function refocusTop({ moveFocus = false, returnTo = null } = {}) {
        const next = topVisibleWindow();
        if (next) {
            focusWindow(next, { moveFocus });
            setHash(next.id);
        } else {
            focusedId = null;
            for (const record of windows.values()) record.win.classList.remove('focused');
            clearHash();
            renderChrome();
            if (moveFocus) {
                const usableInvoker = returnTo && returnTo.isConnected
                    && returnTo.getClientRects().length
                    && getComputedStyle(returnTo).visibility !== 'hidden';
                const target = usableInvoker ? returnTo : $('[data-launcher]', dock);
                if (target) target.focus({ preventScroll: true });
            }
        }
    }

    function closeWindow(record) {
        if (record.closing) return;
        record.closing = true;
        record.animToken = null;
        const finish = () => {
            if (record.closed) return;
            const moveFocus = record.win.contains(document.activeElement);
            record.closed = true;
            record.app.el.hidden = true;
            fallback.append(record.app.el);
            record.win.remove();
            windows.delete(record.id);
            if (windows.size === 0) cascade = 0;
            refocusTop({ moveFocus, returnTo: record.invoker });
        };
        record.finishClose = finish;
        if (reduceMotion.matches) {
            finish();
            return;
        }
        record.win.classList.remove('attention');
        record.win.classList.add('closing');
        onOwnAnimationEnd(record.win, finish);
    }

    function minimizeWindow(record) {
        if (record.minimized || record.closing) return;
        record.minimized = true;
        // Si la ventana se restaura o se cierra antes de terminar la animación,
        // este final queda invalidado.
        const token = {};
        record.animToken = token;
        const finish = () => {
            if (record.animToken !== token || !record.minimized) return;
            const moveFocus = record.win.contains(document.activeElement);
            record.win.classList.remove('minimizing');
            record.win.classList.add('minimized');
            refocusTop({ moveFocus, returnTo: record.invoker });
        };
        if (reduceMotion.matches) {
            finish();
        } else {
            record.win.classList.remove('attention');
            record.win.classList.add('minimizing');
            onOwnAnimationEnd(record.win, finish);
        }
        renderChrome();
    }

    function restoreWindow(record) {
        record.animToken = null;
        record.minimized = false;
        record.win.classList.remove('minimized', 'minimizing');
        if (!reduceMotion.matches) {
            record.win.classList.add('opening');
            onOwnAnimationEnd(record.win, () => record.win.classList.remove('opening'));
        }
    }

    function toggleMaximize(record) {
        if (compactLayout.matches) return;
        const animate = !reduceMotion.matches;
        if (animate) record.win.classList.add('animate-rect');
        if (record.maximized) {
            record.maximized = false;
            record.win.classList.remove('maximized');
            applyRect(record, fitRect(record.restoreRect || initialRect(record.app)));
        } else {
            record.restoreRect = currentRect(record);
            record.maximized = true;
            record.win.classList.add('maximized');
            applyRect(record, maximizedRect());
        }
        const maxButton = $('[data-act="max"]', record.titlebar);
        const verb = record.maximized ? 'Restaurar' : 'Maximizar';
        maxButton.setAttribute('aria-label', `${verb} ${record.app.title}`);
        maxButton.title = verb;
        if (animate) window.setTimeout(() => record.win.classList.remove('animate-rect'), 260);
    }

    function wireWindow(record) {
        const { win, titlebar, resize } = record;

        win.addEventListener('pointerdown', () => {
            if (focusedId !== record.id) {
                focusWindow(record);
                setHash(record.id);
            }
        });

        titlebar.addEventListener('click', (event) => {
            const button = event.target.closest('button[data-act]');
            if (!button) return;
            const act = button.dataset.act;
            if (act === 'close') closeWindow(record);
            else if (act === 'min') minimizeWindow(record);
            else if (act === 'max') toggleMaximize(record);
        });

        titlebar.addEventListener('dblclick', (event) => {
            if (!event.target.closest('button')) toggleMaximize(record);
        });

        // Arrastre por la titlebar.
        titlebar.addEventListener('pointerdown', (event) => {
            if (event.button !== 0 || event.target.closest('button') || compactLayout.matches) return;
            const start = { x: event.clientX, y: event.clientY, rect: currentRect(record) };
            let moved = false;
            titlebar.setPointerCapture(event.pointerId);

            const onMove = (moveEvent) => {
                const dx = moveEvent.clientX - start.x;
                const dy = moveEvent.clientY - start.y;
                if (!moved && Math.hypot(dx, dy) < 4) return;
                if (!moved) {
                    moved = true;
                    win.classList.add('dragging');
                    if (record.maximized) {
                        // Al arrastrar una ventana maximizada, vuelve a su tamaño
                        // anterior bajo el cursor.
                        const restore = fitRect(record.restoreRect || initialRect(record.app));
                        const ratio = (start.x - layer.getBoundingClientRect().left - start.rect.left) / start.rect.width;
                        record.maximized = false;
                        win.classList.remove('maximized');
                        start.rect = {
                            ...restore,
                            left: start.x - layer.getBoundingClientRect().left - restore.width * ratio,
                            top: start.rect.top,
                        };
                        applyRect(record, start.rect);
                    }
                }
                const desk = deskBounds();
                const left = clamp(start.rect.left + dx, 120 - start.rect.width, desk.width - 120);
                const top = clamp(start.rect.top + dy, 0, desk.height - 120);
                win.style.left = `${Math.round(left)}px`;
                win.style.top = `${Math.round(top)}px`;
            };

            const onUp = () => {
                win.classList.remove('dragging');
                titlebar.removeEventListener('pointermove', onMove);
                titlebar.removeEventListener('pointerup', onUp);
                titlebar.removeEventListener('pointercancel', onUp);
            };

            titlebar.addEventListener('pointermove', onMove);
            titlebar.addEventListener('pointerup', onUp);
            titlebar.addEventListener('pointercancel', onUp);
        });

        // Redimensión desde la esquina.
        resize.addEventListener('pointerdown', (event) => {
            if (event.button !== 0 || record.maximized) return;
            event.preventDefault();
            const start = { x: event.clientX, y: event.clientY, rect: currentRect(record) };
            resize.setPointerCapture(event.pointerId);
            win.classList.add('dragging');

            const onMove = (moveEvent) => {
                const desk = deskBounds();
                const width = clamp(start.rect.width + moveEvent.clientX - start.x, 340, desk.width - start.rect.left - 8);
                const height = clamp(start.rect.height + moveEvent.clientY - start.y, 260, desk.height - start.rect.top - 8);
                win.style.width = `${Math.round(width)}px`;
                win.style.height = `${Math.round(height)}px`;
            };

            const onUp = () => {
                win.classList.remove('dragging');
                resize.removeEventListener('pointermove', onMove);
                resize.removeEventListener('pointerup', onUp);
                resize.removeEventListener('pointercancel', onUp);
            };

            resize.addEventListener('pointermove', onMove);
            resize.addEventListener('pointerup', onUp);
            resize.addEventListener('pointercancel', onUp);
        });
    }

    // Mantiene las ventanas dentro del escritorio al cambiar el tamaño.
    let resizeFrame = 0;
    window.addEventListener('resize', () => {
        cancelAnimationFrame(resizeFrame);
        resizeFrame = requestAnimationFrame(() => {
            if (tourIndex >= 0) renderTourStep();
            if (compactLayout.matches) return;
            for (const record of windows.values()) {
                if (record.maximized) {
                    applyRect(record, maximizedRect());
                    continue;
                }
                // Una minimizada no tiene dimensiones de layout; conserva sus estilos.
                const rect = record.minimized ? {
                    left: parseFloat(record.win.style.left),
                    top: parseFloat(record.win.style.top),
                    width: parseFloat(record.win.style.width),
                    height: parseFloat(record.win.style.height),
                } : currentRect(record);
                applyRect(record, fitRect(rect));
            }
        });
    });

    /* ------------------------------------------------------------
       Tabstrip, dock y barra de estado
       ------------------------------------------------------------ */

    const statusReadout = $('[data-status-readout]');
    const pulse = $('[data-pulse]');

    function renderChrome() {
        tabstrip.replaceChildren();
        for (const record of windows.values()) {
            const tab = element('button');
            tab.type = 'button';
            tab.dataset.tab = record.id;
            tab.dataset.accent = record.app.accent;
            const isFocused = record.id === focusedId && !record.minimized;
            tab.setAttribute('aria-pressed', String(isFocused));
            tab.classList.toggle('is-min', record.minimized);
            tab.title = record.minimized ? `Restaurar ${record.app.title}` : record.app.title;
            tab.append(osIcon(record.app.icon), element('span', '', record.app.title));
            tabstrip.append(tab);
        }
        tabstrip.classList.toggle('visible', windows.size > 0);

        for (const button of $$('[data-open]', dock)) {
            button.classList.toggle('running', windows.has(button.dataset.open));
        }

        const focused = focusedId ? windows.get(focusedId) : null;
        if (statusReadout) statusReadout.textContent = focused ? `Abierta: ${focused.app.title}` : 'En espera';
        if (pulse) pulse.textContent = `${windows.size} ${windows.size === 1 ? 'app' : 'apps'}`;
        renderRoute();
    }

    tabstrip.addEventListener('click', (event) => {
        const tab = event.target.closest('button[data-tab]');
        if (!tab) return;
        const record = windows.get(tab.dataset.tab);
        if (!record) return;
        if (record.minimized) {
            restoreWindow(record);
            focusWindow(record, { moveFocus: true });
            setHash(record.id);
        } else if (record.id === focusedId) {
            minimizeWindow(record);
        } else {
            focusWindow(record, { moveFocus: true });
            setHash(record.id);
        }
    });

    /* ------------------------------------------------------------
       Launcher (agrupado: Empieza aquí, Descubre, Profundiza, Comunidad)
       ------------------------------------------------------------ */

    function normalize(text) {
        return text.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    }

    for (const group of LAUNCHER_GROUPS) {
        const members = [...apps.values()]
            .filter((app) => app.group === group.id)
            .sort((a, b) => (a.route || 99) - (b.route || 99));
        if (!members.length) continue;

        const section = element('section', 'launcher-group');
        section.dataset.group = group.id;
        const heading = element('h3', '', group.label);
        heading.id = `launcher-group-${group.id}`;
        section.setAttribute('aria-labelledby', heading.id);
        const grid = element('div', 'launcher-grid');

        for (const app of members) {
            const button = element('button');
            button.type = 'button';
            button.dataset.open = app.id;
            button.dataset.accent = app.accent;
            const name = element('strong');
            if (app.route) name.append(element('span', 'lt-step', String(app.route)));
            name.append(document.createTextNode(app.title));
            if (app.tag) name.append(element('span', 'lt-tag', app.tag));
            button.append(osIcon(app.icon), name, element('span', '', app.subtitle));
            button.dataset.search = normalize(`${app.title} ${app.subtitle} ${app.id} ${app.tag} ${group.label}`);
            grid.append(button);
        }

        section.append(heading, grid);
        launcherBody.append(section);
    }

    for (const toggle of $$('.topbar [data-launcher]')) toggle.title = `Apps (${LAUNCHER_SHORTCUT})`;

    function isLauncherOpen() {
        return launcher.classList.contains('open');
    }

    function setLauncherExpanded(open) {
        for (const toggle of $$('[data-launcher]')) toggle.setAttribute('aria-expanded', String(open));
        launcher.setAttribute('aria-hidden', String(!open));
    }

    let launcherReturnFocus = null;

    function openLauncher(invoker) {
        launcherReturnFocus = invoker instanceof HTMLElement ? invoker : null;
        launcher.classList.add('open');
        setLauncherExpanded(true);
        launcherSearch.value = '';
        filterLauncher('');
        launcherBody.scrollTop = 0;
        window.setTimeout(() => launcherSearch.focus({ preventScroll: true }), 30);
    }

    function closeLauncher({ restoreFocus = false } = {}) {
        if (!isLauncherOpen()) return;
        launcher.classList.remove('open');
        setLauncherExpanded(false);
        if (restoreFocus && launcherReturnFocus && launcherReturnFocus.isConnected) {
            launcherReturnFocus.focus({ preventScroll: true });
        }
    }

    function filterLauncher(query) {
        const needle = normalize(query.trim());
        let visible = 0;
        for (const section of $$('.launcher-group', launcherBody)) {
            let inGroup = 0;
            for (const button of $$('button', section)) {
                const match = !needle || button.dataset.search.includes(needle);
                button.hidden = !match;
                if (match) inGroup += 1;
            }
            section.hidden = inGroup === 0;
            visible += inGroup;
        }
        launcherEmpty.hidden = visible > 0;
    }

    launcherSearch.addEventListener('input', () => filterLauncher(launcherSearch.value));
    launcherSearch.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            const first = $('.launcher-group:not([hidden]) button:not([hidden])', launcherBody);
            if (first) openApp(first.dataset.open, { invoker: launcherReturnFocus });
        }
    });

    document.addEventListener('pointerdown', (event) => {
        if (!isLauncherOpen()) return;
        if (event.target.closest('#launcher, [data-launcher]')) return;
        closeLauncher();
    });

    /* ------------------------------------------------------------
       Pestañas dentro de una app (Instalar: normal / desarrollador)
       ------------------------------------------------------------ */

    function selectTab(tab, { focus = false } = {}) {
        const list = tab.closest('[data-tabs]');
        if (!list) return;
        for (const other of $$('[role="tab"]', list)) {
            const selected = other === tab;
            other.setAttribute('aria-selected', String(selected));
            other.tabIndex = selected ? 0 : -1;
            const panel = document.getElementById(other.getAttribute('aria-controls'));
            if (panel) panel.hidden = !selected;
        }
        if (focus) tab.focus();
    }

    for (const list of $$('[data-tabs]')) {
        const initial = $('[role="tab"][aria-selected="true"]', list) || $('[role="tab"]', list);
        if (initial) selectTab(initial);
        list.addEventListener('keydown', (event) => {
            if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
            const tabs = $$('[role="tab"]', list);
            const index = tabs.indexOf(document.activeElement);
            if (index < 0) return;
            const step = event.key === 'ArrowRight' ? 1 : -1;
            selectTab(tabs[(index + step + tabs.length) % tabs.length], { focus: true });
            event.preventDefault();
        });
    }

    /* ------------------------------------------------------------
       Temas
       ------------------------------------------------------------ */

    const wallHost = $('.wall-host');
    const baseWall = $('[data-wallpaper]');
    let currentTheme = THEMES[root.dataset.theme] ? root.dataset.theme : 'iris';
    let themeRevision = 0;
    let cancelThemeTransition = null;
    let glitchTimer = 0;

    baseWall.style.backgroundImage = `url("${THEMES[currentTheme].wall}")`;

    function syncThemeControls() {
        for (const card of $$('[data-theme-set]')) {
            card.setAttribute('aria-pressed', String(card.dataset.themeSet === currentTheme));
        }
    }

    function applyTheme(name, origin = null) {
        const theme = THEMES[name];
        if (!theme || name === currentTheme) return;
        currentTheme = name;
        const revision = ++themeRevision;
        storage.set(THEME_KEY, name);
        if (cancelThemeTransition) cancelThemeTransition();
        cancelThemeTransition = null;
        for (const pending of $$('.wallpaper.next', wallHost)) pending.remove();
        window.clearTimeout(glitchTimer);
        document.body.classList.remove('glitching');

        const swap = () => {
            if (revision !== themeRevision) return;
            root.dataset.theme = name;
            syncThemeControls();

            const url = `url("${theme.wall}")`;
            if (reduceMotion.matches) {
                baseWall.style.backgroundImage = url;
                return;
            }

            const next = element('div', `wallpaper next t-${theme.transition}`);
            next.style.backgroundImage = url;
            if (origin) {
                const hostRect = wallHost.getBoundingClientRect();
                next.style.setProperty('--ox', `${origin.x - hostRect.left}px`);
                next.style.setProperty('--oy', `${origin.y - hostRect.top}px`);
            }
            wallHost.append(next);
            cancelThemeTransition = onOwnAnimationEnd(next, () => {
                if (revision !== themeRevision) return;
                baseWall.style.backgroundImage = url;
                next.remove();
                cancelThemeTransition = null;
            }, 1400);

            if (theme.transition === 'glitch') {
                document.body.classList.add('glitching');
                glitchTimer = window.setTimeout(() => document.body.classList.remove('glitching'), 650);
            }
        };

        const image = new Image();
        image.src = theme.wall;
        const ready = typeof image.decode === 'function' ? image.decode() : Promise.resolve();
        ready.then(swap, swap);
    }

    syncThemeControls();

    // Precarga los demás fondos cuando el navegador esté libre, salvo en modo
    // de ahorro de datos: entonces cada fondo se descarga solo al elegirlo.
    const saveData = Boolean(navigator.connection && navigator.connection.saveData);
    const idle = window.requestIdleCallback || ((callback) => window.setTimeout(callback, 1500));
    if (!saveData) {
        idle(() => {
            for (const theme of Object.values(THEMES)) {
                const image = new Image();
                image.src = theme.wall;
            }
        });
    }

    /* ------------------------------------------------------------
       Delegación de clics
       ------------------------------------------------------------ */

    document.addEventListener('click', (event) => {
        if (root.classList.contains('no-js')) return;
        if (event.target.closest('[data-tour-start]')) {
            startTour(event.target.closest('[data-tour-start]'));
            return;
        }

        const launcherToggle = event.target.closest('[data-launcher]');
        if (launcherToggle) {
            if (isLauncherOpen()) closeLauncher();
            else openLauncher(launcherToggle);
            return;
        }

        if (event.target.closest('[data-launcher-close]')) {
            closeLauncher({ restoreFocus: true });
            return;
        }

        const opener = event.target.closest('[data-open]');
        if (opener) {
            if (opener.tagName === 'A') event.preventDefault();
            openApp(opener.dataset.open, { invoker: opener });
            return;
        }

        const tab = event.target.closest('[data-tabs] [role="tab"]');
        if (tab) {
            selectTab(tab);
            return;
        }

        const tabLink = event.target.closest('[data-tab-go]');
        if (tabLink) {
            if (tabLink.tagName === 'A') event.preventDefault();
            const target = document.getElementById(tabLink.dataset.tabGo);
            if (target) selectTab(target, { focus: true });
            return;
        }

        const themeButton = event.target.closest('[data-theme-set]');
        if (themeButton) {
            const rect = themeButton.getBoundingClientRect();
            const origin = event.clientX || event.clientY
                ? { x: event.clientX, y: event.clientY }
                : { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
            applyTheme(themeButton.dataset.themeSet, origin);
            return;
        }

        const copyButton = event.target.closest('[data-copy]');
        if (copyButton) {
            copyText(copyButton.dataset.copy).then((ok) => flashLabel(copyButton, ok ? 'Copiado' : 'Selecciónalo', ok));
            return;
        }

        const shareButton = event.target.closest('[data-share]');
        if (shareButton) shareSite(shareButton);
    });

    async function copyText(text) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch {
            const area = element('textarea');
            area.value = text;
            area.setAttribute('readonly', '');
            area.style.position = 'fixed';
            area.style.opacity = '0';
            document.body.append(area);
            area.select();
            let ok = false;
            try { ok = document.execCommand('copy'); } catch { ok = false; }
            area.remove();
            return ok;
        }
    }

    // Cambia el texto de un botón un momento y lo restaura.
    function flashLabel(button, text, ok) {
        const label = button.querySelector('span') || button.firstChild;
        if (!label) return;
        const original = button.dataset.label || label.textContent;
        button.dataset.label = original;
        label.textContent = text;
        button.classList.toggle('copied', ok);
        window.setTimeout(() => {
            label.textContent = original;
            button.classList.remove('copied');
        }, 1600);
    }

    async function shareSite(button) {
        const url = window.location.href.split('#')[0];
        const data = {
            title: 'IRIS OS',
            text: 'Una IA que puedes ver, escuchar y hacer tuya.',
            url,
        };
        if (navigator.share) {
            try {
                await navigator.share(data);
                return;
            } catch (error) {
                if (error && error.name === 'AbortError') return;
            }
        }
        const ok = await copyText(url);
        flashLabel(button, ok ? 'Enlace copiado' : 'No se pudo copiar', ok);
    }

    /* ------------------------------------------------------------
       Mini tutorial
       ------------------------------------------------------------ */

    const tour = $('#tour');
    const tourSpot = $('.tour-spot', tour);
    const tourCard = $('.tour-card', tour);
    const tourStepLabel = $('[data-tour-step]', tour);
    const tourTitle = $('[data-tour-title]', tour);
    const tourText = $('[data-tour-text]', tour);
    const tourDots = $('[data-tour-dots]', tour);
    const tourNext = $('[data-tour-next]', tour);
    const tourSkip = $('[data-tour-skip]', tour);

    const TOUR_STEPS = [
        {
            title: 'Bienvenido a IRIS OS',
            text: () => 'Esta web funciona como el escritorio de IRIS: cada sección se abre como una app en su propia ventana.',
            target: () => null,
        },
        {
            title: 'Empieza por aquí',
            text: () => 'Sigue la ruta 1 → 2 → 3 para empezar: qué es IRIS, qué puede hacer y cómo probarla. Cada paso se marcará cuando lo abras.',
            target: () => $('.dock-route'),
        },
        {
            title: 'Así es una ventana',
            text: () => (compactLayout.matches
                ? 'Minimízala o ciérrala con los botones de arriba. Dentro de cada ventana puedes desplazarte y, al final, ver qué abrir después.'
                : 'Muévela desde la barra superior, minimízala o ciérrala. Dentro de cada ventana puedes desplazarte y, al final, ver qué abrir después.'),
            before: () => openApp('que-es', { silent: true }),
            target: () => (windows.get('que-es') || {}).win || null,
        },
        {
            title: 'Todo a mano',
            text: () => (compactLayout.matches
                ? 'Tus ventanas abiertas aparecen sobre el dock. En Apps encontrarás todas las secciones. Y si te pierdes, el botón ? vuelve a mostrar este tutorial.'
                : `Tus ventanas abiertas aparecen sobre el dock. En Apps encontrarás todas las secciones (también con ${LAUNCHER_SHORTCUT}). Y si te pierdes, el botón ? vuelve a mostrar este tutorial.`),
            // Pestañas abiertas + dock, iluminados juntos.
            target: () => [tabstrip, dock],
        },
    ];

    let tourIndex = -1;
    let tourReturnFocus = null;

    // Rectángulo final de un elemento (o la unión de varios visibles); las ventanas
    // se miden sin su animación de entrada.
    function targetRect(target) {
        if (Array.isArray(target)) {
            const rects = target
                .filter((item) => item && item.offsetParent !== null)
                .map((item) => targetRect(item));
            const left = Math.min(...rects.map((r) => r.left));
            const top = Math.min(...rects.map((r) => r.top));
            const right = Math.max(...rects.map((r) => r.left + r.width));
            const bottom = Math.max(...rects.map((r) => r.top + r.height));
            return { left, top, width: right - left, height: bottom - top };
        }
        if (target.classList.contains('os-window')) {
            const base = layer.getBoundingClientRect();
            return {
                left: base.left + target.offsetLeft,
                top: base.top + target.offsetTop,
                width: target.offsetWidth,
                height: target.offsetHeight,
            };
        }
        const rect = target.getBoundingClientRect();
        return { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
    }

    function placeTourCard(rect) {
        const margin = 16;
        const gap = 14;
        const viewW = window.innerWidth;
        const viewH = window.innerHeight;
        const cardW = tourCard.offsetWidth;
        const cardH = tourCard.offsetHeight;
        let left = (viewW - cardW) / 2;
        let top = (viewH - cardH) / 2;

        if (rect) {
            const bottom = rect.top + rect.height;
            const right = rect.left + rect.width;
            const centeredLeft = clamp(rect.left + rect.width / 2 - cardW / 2, margin, viewW - cardW - margin);
            const centeredTop = clamp(rect.top + rect.height / 2 - cardH / 2, margin, viewH - cardH - margin);
            if (rect.top - margin >= cardH + gap) {
                top = rect.top - cardH - gap;
                left = centeredLeft;
            } else if (viewH - bottom - margin >= cardH + gap) {
                top = bottom + gap;
                left = centeredLeft;
            } else if (rect.left - margin >= cardW + gap) {
                left = rect.left - cardW - gap;
                top = centeredTop;
            } else if (viewW - right - margin >= cardW + gap) {
                left = right + gap;
                top = centeredTop;
            } else {
                // El objetivo ocupa casi toda la pantalla: la tarjeta va sobre su parte baja.
                left = centeredLeft;
                top = clamp(bottom - cardH - 24, margin, viewH - cardH - margin);
            }
        }
        tourCard.style.left = `${Math.round(left)}px`;
        tourCard.style.top = `${Math.round(top)}px`;
    }

    function renderTourStep() {
        const step = TOUR_STEPS[tourIndex];
        if (!step) return;
        tourStepLabel.textContent = `Paso ${tourIndex + 1} de ${TOUR_STEPS.length}`;
        tourTitle.textContent = step.title;
        tourText.textContent = step.text();
        tourNext.textContent = tourIndex === TOUR_STEPS.length - 1 ? 'Empezar' : 'Siguiente';
        tourDots.replaceChildren(...TOUR_STEPS.map((_, index) => element('i', index === tourIndex ? 'on' : '')));

        const target = step.target();
        if (target) {
            const rect = targetRect(target);
            const pad = 8;
            tourSpot.classList.remove('is-center');
            tourSpot.style.left = `${rect.left - pad}px`;
            tourSpot.style.top = `${rect.top - pad}px`;
            tourSpot.style.width = `${rect.width + pad * 2}px`;
            tourSpot.style.height = `${rect.height + pad * 2}px`;
            placeTourCard(rect);
        } else {
            tourSpot.classList.add('is-center');
            tourSpot.style.left = '50%';
            tourSpot.style.top = '50%';
            tourSpot.style.width = '0px';
            tourSpot.style.height = '0px';
            placeTourCard(null);
        }
    }

    function goToTourStep(index) {
        tourIndex = index;
        const step = TOUR_STEPS[index];
        if (step.before) step.before();
        renderTourStep();
        tourNext.focus({ preventScroll: true });
    }

    function startTour(invoker = null) {
        closeLauncher();
        tourReturnFocus = invoker instanceof HTMLElement ? invoker : null;
        tour.hidden = false;
        goToTourStep(0);
    }

    function endTour() {
        if (tourIndex < 0) return;
        tourIndex = -1;
        tour.hidden = true;
        storage.set(TOUR_KEY, '1');
        const focused = focusedId ? windows.get(focusedId) : null;
        if (focused) {
            focused.win.focus({ preventScroll: true });
            highlight(focused.win);
            return;
        }
        // Sin ventanas abiertas (tutorial saltado pronto): el siguiente paso de la ruta.
        const nextUp = $('.dock-route .next-up');
        if (nextUp) {
            nextUp.focus({ preventScroll: true });
            highlight(nextUp);
        } else if (tourReturnFocus && tourReturnFocus.isConnected) {
            tourReturnFocus.focus({ preventScroll: true });
        }
    }

    function highlight(target) {
        if (reduceMotion.matches) return;
        target.classList.remove('attention');
        void target.offsetWidth; // reinicia la animación si ya estaba
        target.classList.add('attention');
        onOwnAnimationEnd(target, () => target.classList.remove('attention'), 1800);
    }

    tourNext.addEventListener('click', () => {
        if (tourIndex >= TOUR_STEPS.length - 1) endTour();
        else goToTourStep(tourIndex + 1);
    });
    tourSkip.addEventListener('click', endTour);

    function handleTourKey(event) {
        if (event.key === 'Escape') {
            event.preventDefault();
            endTour();
        } else if (event.key === 'Tab') {
            // Mantiene el foco dentro de la tarjeta mientras el tutorial está abierto.
            event.preventDefault();
            (document.activeElement === tourNext ? tourSkip : tourNext).focus();
        } else if (event.key === 'ArrowRight' && tourIndex < TOUR_STEPS.length - 1) {
            goToTourStep(tourIndex + 1);
        } else if (event.key === 'ArrowLeft' && tourIndex > 0) {
            goToTourStep(tourIndex - 1);
        }
    }

    /* ------------------------------------------------------------
       Teclado
       ------------------------------------------------------------ */

    document.addEventListener('keydown', (event) => {
        if (root.classList.contains('no-js')) return;
        if (tourIndex >= 0) {
            handleTourKey(event);
            return;
        }

        const typing = event.target.closest('input, textarea, [contenteditable="true"]');

        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
            event.preventDefault();
            if (isLauncherOpen()) closeLauncher({ restoreFocus: true });
            else openLauncher(document.activeElement);
            return;
        }

        if (event.key === '/' && !typing && !isLauncherOpen()) {
            event.preventDefault();
            openLauncher(document.activeElement);
            return;
        }

        if (event.key === 'Escape') {
            if (isLauncherOpen()) {
                closeLauncher({ restoreFocus: true });
                return;
            }
            const inside = event.target.closest('.os-window');
            if (inside) {
                const record = windows.get(inside.dataset.app);
                if (record) closeWindow(record);
            }
        }
    });

    /* ------------------------------------------------------------
       URL (#app) para compartir una ventana concreta
       ------------------------------------------------------------ */

    function setHash(id) {
        if (window.location.hash.slice(1) === id) return;
        history.replaceState(null, '', `#${id}`);
    }

    function clearHash() {
        if (!window.location.hash) return;
        history.replaceState(null, '', window.location.pathname + window.location.search);
    }

    window.addEventListener('hashchange', () => {
        const id = decodeURIComponent(window.location.hash.slice(1));
        if (apps.has(id)) openApp(id);
    });

    /* ------------------------------------------------------------
       Reloj de la barra superior
       ------------------------------------------------------------ */

    const clockTime = $('[data-clock-time]');
    const clockDate = $('[data-clock-date]');
    // Mismo formato que la barra de IRIS OS.
    const timeFormat = new Intl.DateTimeFormat('es-CO', { hour: '2-digit', minute: '2-digit' });
    const dateFormat = new Intl.DateTimeFormat('es-CO', { weekday: 'short', day: '2-digit', month: 'short' });

    function tick() {
        const now = new Date();
        if (clockTime) clockTime.textContent = timeFormat.format(now);
        if (clockDate) clockDate.textContent = dateFormat.format(now).replace(/\./g, '');
    }
    tick();
    window.setInterval(tick, 15000);

    /* ------------------------------------------------------------
       Companion: recorre espera → escucha → piensa → habla
       mientras su ventana es visible
       ------------------------------------------------------------ */

    const COMPANION_STATES = [
        { id: 'idle', label: 'En espera' },
        { id: 'listening', label: 'Escuchando' },
        { id: 'thinking', label: 'Pensando' },
        { id: 'speaking', label: 'Hablando' },
    ];
    let companionStep = 0;

    function renderCompanion(scope) {
        const state = COMPANION_STATES[companionStep];
        const widget = $('[data-companion-widget]', scope);
        const label = $('[data-companion-state]', scope);
        if (widget) widget.dataset.state = state.id;
        if (label) label.textContent = state.label;
        for (const step of $$('[data-c-step]', scope)) {
            if (step.dataset.cStep === state.id) step.setAttribute('aria-current', 'step');
            else step.removeAttribute('aria-current');
        }
    }

    renderCompanion(fallback);

    window.setInterval(() => {
        const record = windows.get('companion');
        if (!record || record.minimized || document.hidden) return;
        companionStep = (companionStep + 1) % COMPANION_STATES.length;
        renderCompanion(record.win);
    }, 2000);

    /* ------------------------------------------------------------
       Arranque
       ------------------------------------------------------------ */

    function runBoot() {
        const boot = $('#boot');
        if (!boot) return Promise.resolve();
        if (reduceMotion.matches || session.get(BOOT_KEY)) {
            boot.remove();
            return Promise.resolve();
        }
        session.set(BOOT_KEY, '1');
        return new Promise((resolve) => {
            let finished = false;
            const done = () => {
                if (finished) return;
                finished = true;
                boot.classList.add('done');
                window.setTimeout(() => {
                    boot.remove();
                    resolve();
                }, 450);
            };
            const timer = window.setTimeout(done, 1900);
            boot.addEventListener('click', () => {
                window.clearTimeout(timer);
                done();
            }, { once: true });
        });
    }

    renderChrome();

    if (window.irisSiteBoot && !window.irisSiteBoot.ready()) return;
    root.classList.remove('no-js');
    root.classList.add('js');

    runBoot().then(() => {
        const requested = decodeURIComponent(window.location.hash.slice(1));
        if (apps.has(requested)) {
            // Quien llega con un enlace directo va a esa app; el tutorial queda en «?».
            openApp(requested);
        } else if (!storage.get(TOUR_KEY)) {
            startTour();
        } else if (window.innerWidth >= 1180) {
            // En pantallas amplias, recibe al visitante con la primera app abierta.
            openApp('que-es', { silent: true });
        }
    });

    // Doble clic en el fondo del escritorio: minimiza todas las ventanas.
    home.addEventListener('dblclick', (event) => {
        if (event.target === home || event.target === layer) {
            for (const record of windows.values()) if (!record.minimized) minimizeWindow(record);
        }
    });
})();
