// Tema antes de pintar y recuperación independiente del controlador principal.
// Sin defer para evitar un destello; archivo propio para mantener la CSP estricta.
(function () {
    var root = document.documentElement;
    var initialized = false;
    var recovered = false;
    var watchdog;

    function cleanup() {
        window.clearTimeout(watchdog);
        window.removeEventListener('error', onError, true);
        document.removeEventListener('DOMContentLoaded', onLoaded);
    }

    function restoreContent() {
        var apps = document.getElementById('apps');
        if (apps) {
            document.querySelectorAll('article[data-app]').forEach(function (article) {
                article.hidden = false;
                apps.append(article);
            });
        }
        document.querySelectorAll('.tab-panel').forEach(function (panel) { panel.hidden = false; });
        var layer = document.getElementById('window-layer');
        if (layer) layer.replaceChildren();
        var boot = document.getElementById('boot');
        if (boot) boot.remove();
        var tour = document.getElementById('tour');
        if (tour) tour.hidden = true;
    }

    function recover() {
        if (initialized || recovered) return;
        recovered = true;
        cleanup();
        root.classList.remove('js');
        root.classList.add('no-js');
        restoreContent();
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', restoreContent, { once: true });
        }
    }

    function onError(event) {
        // Una imagen ausente no requiere abandonar el escritorio.
        if (event.target === window || (event.target && event.target.tagName === 'SCRIPT')) recover();
    }

    function onLoaded() {
        if (!initialized) recover();
    }

    window.irisSiteBoot = Object.freeze({
        get recovered() { return recovered; },
        ready: function () {
            if (recovered) return false;
            initialized = true;
            cleanup();
            return true;
        },
    });

    window.addEventListener('error', onError, true);
    document.addEventListener('DOMContentLoaded', onLoaded);
    // Si la descarga del script se estanca, la página sigue siendo legible.
    watchdog = window.setTimeout(recover, 5000);
    root.classList.remove('no-js');
    root.classList.add('js');
    try {
        var saved = localStorage.getItem('irisSite.theme');
        if (saved === 'iris' || saved === 'sunrise' || saved === 'verdant' || saved === 'rose') {
            root.setAttribute('data-theme', saved);
        }
    } catch (e) { /* almacenamiento no disponible */ }
})();
