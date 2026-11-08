import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
let playwright;
try { playwright = require(process.env.IRIS_SITE_PLAYWRIGHT || 'playwright'); } catch {}

test('a real app button that opens another window keeps the new window in front', { skip: !playwright }, async () => {
    const source = await readFile(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
    const binding = source.split('\n').find(line => line.includes("querySelector('.os-window-layer')?.addEventListener('click'"));
    const start = source.indexOf('    _handleWindowClick(ev) {');
    const end = source.indexOf('\n    _handleWindowPointerDown', start);
    const method = source.slice(start, end).trim();
    const browser = await playwright.chromium.launch({ headless: true, ...(process.env.IRIS_SITE_BROWSER_CHANNEL ? { channel: process.env.IRIS_SITE_BROWSER_CHANNEL } : {}) });
    try {
        const page = await browser.newPage();
        await page.setContent('<div class="os-window-layer"><section class="os-window" data-window-id="store"><button id="open">Abrir</button></section><section class="os-window" data-window-id="counter"></section></div>');
        const focused = await page.evaluate(({ binding, method }) => {
            const shell = { panel: document, _focusWindow: id => { shell.focused = id; } };
            shell._handleWindowClick = new Function(`return ({${method}})` )()._handleWindowClick.bind(shell);
            new Function(binding).call(shell);
            document.querySelector('#open').addEventListener('click', () => shell._focusWindow('counter'));
            document.querySelector('#open').click();
            return shell.focused;
        }, { binding, method });
        assert.equal(focused, 'counter');
    } finally { await browser.close(); }
});

test('an updated installed window refreshes its title, accent and accessible controls without changing geometry', { skip: !playwright }, async () => {
    const source = await readFile(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
    const start = source.indexOf('    _refreshWindowMetadata(id) {');
    const end = source.indexOf('\n    /** The empty view', start);
    const method = source.slice(start, end).trim();
    const browser = await playwright.chromium.launch({ headless: true, ...(process.env.IRIS_SITE_BROWSER_CHANNEL ? { channel: process.env.IRIS_SITE_BROWSER_CHANNEL } : {}) });
    try {
        const page = await browser.newPage();
        await page.setContent('<section class="os-window" style="width:440px;height:380px"><header class="os-window-titlebar"><div><svg></svg><strong>Anterior</strong></div><nav><button data-window-action="maximize" aria-label="Restaurar Anterior"></button></nav></header></section>');
        const state = await page.evaluate(method => {
            const OS_WINDOW_APPS = { demo: { title: 'Nueva "segura"', accent: 'magenta', icon: 'sparkle' } };
            const win = document.querySelector('section');
            const shell = { _osWindows: new Map([['demo', { el: win }]]) };
            const helper = new Function('OS_WINDOW_APPS', 'osIcon', `return ({${method}})`)(OS_WINDOW_APPS, () => '<svg data-new-icon="yes"></svg>');
            helper._refreshWindowMetadata.call(shell, 'demo');
            return { title: win.querySelector('strong').textContent, accent: win.dataset.accent,
                control: win.querySelector('button').getAttribute('aria-label'), width: win.style.width, height: win.style.height,
                icon: !!win.querySelector('[data-new-icon]') };
        }, method);
        assert.deepEqual(state, { title: 'Nueva "segura"', accent: 'magenta', control: 'Restaurar Nueva "segura"', width: '440px', height: '380px', icon: true });
    } finally { await browser.close(); }
});
