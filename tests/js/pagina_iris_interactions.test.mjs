/*
 * Browser regressions for the independent Iris presentation site.
 *
 * Uses the actual HTML, CSS and scripts with isolated browser contexts and a
 * localhost-only fixture. No browser package is installed by this test.
 * Resolve an existing `playwright` package through Node, or set
 * IRIS_SITE_PLAYWRIGHT to its package directory. IRIS_SITE_BROWSER_CHANNEL can
 * select an installed browser (for example `msedge`); omit it for Chromium.
 *
 * node --test tests/js/pagina_iris_interactions.test.mjs
 */
import test, { after, before } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { readFileSync, statSync } from 'node:fs';
import { extname, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const siteRoot = fileURLToPath(new URL('../../pagina-iris/', import.meta.url));
let playwright;
let skipReason = false;
try {
    playwright = require(process.env.IRIS_SITE_PLAYWRIGHT || 'playwright');
} catch (error) {
    // An explicit but broken runtime configuration must fail, not hide the QA.
    if (process.env.IRIS_SITE_PLAYWRIGHT || error.code !== 'MODULE_NOT_FOUND') throw error;
    skipReason = 'Playwright is unavailable; set IRIS_SITE_PLAYWRIGHT to an existing local package to run browser regressions.';
}

const mime = {
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.webp': 'image/webp',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.ico': 'image/x-icon',
    '.woff2': 'font/woff2',
    '.webmanifest': 'application/manifest+json',
};

// Keep the production CSP while serving a temporary HTTP origin. HSTS and
// upgrade-insecure-requests apply only to the deployed HTTPS site.
const headers = {};
const snippet = readFileSync(resolve(siteRoot, 'deploy/nginx/iris-os-security-headers.conf'), 'utf8');
for (const match of snippet.matchAll(/^add_header\s+(\S+)\s+"([^"]*)"\s+always;/gm)) {
    if (match[1] !== 'Strict-Transport-Security') {
        headers[match[1]] = match[2].replace(/\s*upgrade-insecure-requests;?/, '');
    }
}

let server;
let browser;
let origin;

before(async () => {
    if (!playwright) return;
    server = createServer((request, response) => {
        try {
            const path = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
            const file = resolve(siteRoot, path === '/' ? 'index.html' : `.${path}`);
            if (!file.startsWith(siteRoot.endsWith(sep) ? siteRoot : `${siteRoot}${sep}`) || !statSync(file).isFile()) {
                response.writeHead(404).end();
                return;
            }
            response.writeHead(200, {
                ...headers,
                'Content-Type': mime[extname(file)] || 'application/octet-stream',
                'Cache-Control': 'no-store',
            });
            response.end(readFileSync(file));
        } catch {
            response.writeHead(404).end();
        }
    });
    await new Promise((done, reject) => {
        server.once('error', reject);
        server.listen(0, '127.0.0.1', done);
    });
    origin = `http://127.0.0.1:${server.address().port}`;
    const options = { headless: true };
    if (process.env.IRIS_SITE_BROWSER_CHANNEL) options.channel = process.env.IRIS_SITE_BROWSER_CHANNEL;
    browser = await playwright.chromium.launch(options);
}, { timeout: 30000 });

after(async () => {
    try {
        if (browser) await browser.close();
    } finally {
        if (server) {
            server.closeAllConnections();
            await new Promise((done) => server.close(done));
        }
    }
});

async function freshPage(t, options = {}) {
    const { reducedMotion = 'reduce', viewport = { width: 1440, height: 900 }, allowPageErrors = false, ...rest } = options;
    const context = await browser.newContext({ viewport, reducedMotion, ...rest });
    const externalRequests = [];
    const pageErrors = [];
    context.on('request', (request) => {
        if (new URL(request.url()).origin !== origin) externalRequests.push(request.url());
    });
    await context.route('**/*', (route) => {
        if (new URL(route.request().url()).origin === origin) return route.continue();
        return route.abort();
    });
    await context.addInitScript(() => {
        localStorage.setItem('irisSite.tourDone', '1');
        sessionStorage.setItem('irisSite.booted', '1');
    });
    t.after(async () => {
        await context.close();
        assert.deepEqual(externalRequests, [], 'the site must not request external services during these checks');
        if (!allowPageErrors) assert.deepEqual(pageErrors, [], 'normal browsing must not raise an uncaught script error');
    });
    const page = await context.newPage();
    page.on('pageerror', (error) => pageErrors.push(error.message));
    return { page, context };
}

async function interactive(page) {
    await page.goto(origin, { waitUntil: 'load' });
    await page.waitForFunction(() => {
        const boot = document.getElementById('boot');
        return document.documentElement.classList.contains('js') && (!boot || getComputedStyle(boot).display === 'none');
    });
}

async function openApp(page, id) {
    // La ruta, Companion, Privacidad y Temas están en el dock; Estado, Preguntas,
    // Arquitectura e Historia, en los accesos bajo la tarjeta de IRIS.
    await page.locator(`.dock [data-open="${id}"], .home-tiles [data-open="${id}"]`).first().click();
    const window = page.locator(`.os-window[data-app="${id}"]`);
    await window.waitFor({ state: 'visible' });
    return window;
}

async function nextFrame(page) {
    await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
}

async function assertControlsInsideViewport(window) {
    const controls = await window.locator('[data-act]').evaluateAll((buttons) => buttons
        .filter((button) => getComputedStyle(button).display !== 'none')
        .map((button) => {
            const rect = button.getBoundingClientRect();
            return { action: button.dataset.act, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, viewportWidth: innerWidth, viewportHeight: innerHeight };
        }));
    assert.ok(controls.length > 0);
    for (const rect of controls) {
        assert.ok(rect.left >= 0 && rect.right <= rect.viewportWidth && rect.top >= 0 && rect.bottom <= rect.viewportHeight,
            `${rect.action} must remain accessible: ${JSON.stringify(rect)}`);
    }
}

async function assertOperationalFocus(page, app = null) {
    const focus = await page.evaluate(() => {
        const element = document.activeElement;
        const rect = element.getBoundingClientRect();
        return {
            tag: element.tagName,
            visible: Boolean(element.getClientRects().length) && getComputedStyle(element).visibility !== 'hidden',
            insideViewport: rect.right > 0 && rect.left < innerWidth && rect.bottom > 0 && rect.top < innerHeight,
            app: element.closest('.os-window')?.dataset.app || null,
        };
    });
    assert.notEqual(focus.tag, 'BODY', 'keyboard focus must not fall back to body');
    assert.ok(focus.visible && focus.insideViewport, `focus must remain usable: ${JSON.stringify(focus)}`);
    if (app) assert.equal(focus.app, app);
}

test('W01: a superseded wallpaper transition cannot overwrite the current theme', { skip: skipReason, timeout: 20000 }, async (t) => {
    const { page } = await freshPage(t, { reducedMotion: 'no-preference' });
    await interactive(page);
    const studio = await openApp(page, 'temas');
    for (const [first, last, expectedWall] of [['rose', 'iris', 'wall-city.webp'], ['verdant', 'sunrise', 'wall-classic.webp']]) {
        await studio.locator(`[data-theme-set="${first}"]`).click();
        await page.waitForFunction((name) => document.documentElement.dataset.theme === name, first);
        await page.waitForTimeout(80);
        await studio.locator(`[data-theme-set="${last}"]`).click();
        await page.waitForFunction((name) => document.documentElement.dataset.theme === name, last);
        // Both the detached layer's fallback and the current transition expire.
        await page.waitForTimeout(1750);
        const result = await page.evaluate(() => ({
            theme: document.documentElement.dataset.theme,
            wallpaper: document.querySelector('.wallpaper:not(.next)').style.backgroundImage,
            pending: document.querySelectorAll('.wallpaper.next').length,
            glitching: document.body.classList.contains('glitching'),
        }));
        assert.equal(result.theme, last);
        assert.ok(result.wallpaper.includes(expectedWall), JSON.stringify(result));
        assert.equal(result.pending, 0);
        assert.equal(result.glitching, false);
        assert.equal(await studio.locator(`[data-theme-set="${last}"]`).getAttribute('aria-pressed'), 'true');
    }
});

test('W01: reduced motion keeps theme controls and the final wallpaper synchronized', { skip: skipReason, timeout: 15000 }, async (t) => {
    const { page } = await freshPage(t);
    await interactive(page);
    const studio = await openApp(page, 'temas');
    for (const theme of ['rose', 'verdant', 'sunrise', 'iris']) {
        await studio.locator(`[data-theme-set="${theme}"]`).click();
        await page.waitForFunction((name) => document.documentElement.dataset.theme === name, theme);
    }
    assert.match(await page.locator('.wallpaper:not(.next)').evaluate((element) => element.style.backgroundImage), /wall-city\.webp/);
    assert.equal(await page.locator('.wallpaper.next').count(), 0);
    assert.equal(await studio.locator('[data-theme-set="iris"]').getAttribute('aria-pressed'), 'true');
});

test('W02: maximize, shrink and restore keeps the titlebar controls inside the desktop', { skip: skipReason, timeout: 15000 }, async (t) => {
    const { page } = await freshPage(t, { viewport: { width: 1920, height: 1080 } });
    await interactive(page);
    const window = await openApp(page, 'que-es');
    await window.locator('[data-act="max"]').click();
    await page.setViewportSize({ width: 820, height: 900 });
    await nextFrame(page);
    await window.locator('[data-act="max"]').click();
    await nextFrame(page);
    await assertControlsInsideViewport(window);
    const bounds = await window.boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 820, JSON.stringify(bounds));
});

test('W02: a maximized window remains usable across mobile and desktop layouts', { skip: skipReason, timeout: 15000 }, async (t) => {
    const { page } = await freshPage(t, { viewport: { width: 1920, height: 1080 } });
    await interactive(page);
    const window = await openApp(page, 'que-es');
    await window.locator('[data-act="max"]').click();
    await page.setViewportSize({ width: 390, height: 844 });
    await nextFrame(page);
    await assertControlsInsideViewport(window);
    await page.setViewportSize({ width: 820, height: 900 });
    await nextFrame(page);
    await window.locator('[data-act="max"]').click();
    await nextFrame(page);
    await assertControlsInsideViewport(window);
});

test('W02: resizing a minimized window preserves a useful size when restored', { skip: skipReason, timeout: 15000 }, async (t) => {
    const { page } = await freshPage(t, { viewport: { width: 1440, height: 900 } });
    await interactive(page);
    const window = await openApp(page, 'que-es');
    await window.locator('[data-act="min"]').click();
    await window.waitFor({ state: 'hidden' });
    await page.setViewportSize({ width: 820, height: 900 });
    await nextFrame(page);
    await page.locator('#tabstrip [data-tab="que-es"]').click();
    await window.waitFor({ state: 'visible' });
    await assertControlsInsideViewport(window);
    const bounds = await window.boundingBox();
    assert.ok(bounds.width >= 400 && bounds.height >= 300, `restored content must not collapse to zero dimensions: ${JSON.stringify(bounds)}`);
});

async function assertWindowAboveNavigation(page, window) {
    const bounds = await window.boundingBox();
    const tabs = await page.locator('#tabstrip').boundingBox();
    const dock = await page.locator('.dock').boundingBox();
    assert.ok(bounds.height > 80, `the window needs scrollable content: ${JSON.stringify(bounds)}`);
    assert.ok(bounds.y + bounds.height <= tabs.y, `window overlaps tabs: ${JSON.stringify({ bounds, tabs })}`);
    assert.ok(tabs.y + tabs.height <= dock.y, `tabs overlap dock: ${JSON.stringify({ tabs, dock })}`);
    await assertControlsInsideViewport(window);
}

test('W08: short viewports keep content above navigation and the footer reachable', { skip: skipReason, timeout: 20000 }, async (t) => {
    const { page } = await freshPage(t, { viewport: { width: 844, height: 390 } });
    await page.goto(`${origin}/#comunidad`, { waitUntil: 'load' });
    const window = page.locator('.os-window[data-app="comunidad"]');
    await window.waitFor({ state: 'visible' });
    for (const viewport of [{ width: 844, height: 390 }, { width: 1280, height: 500 }, { width: 390, height: 390 }]) {
        await page.setViewportSize(viewport);
        await nextFrame(page);
        await assertWindowAboveNavigation(page, window);
        assert.equal(await window.locator('[data-act="max"]').isVisible(), false);
        // A normal click scrolls the content to the footer; no forced click or
        // direct scroll mutation that could hide a covered control.
        await window.locator('.app-next [data-open="que-es"]').click();
        await page.locator('.os-window[data-app="que-es"]').waitFor({ state: 'visible' });
        await page.keyboard.press('Escape');
        await page.locator('.os-window[data-app="que-es"]').waitFor({ state: 'detached' });
        await assertWindowAboveNavigation(page, window);
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await nextFrame(page);
    await assertWindowAboveNavigation(page, window);
    const restored = await window.boundingBox();
    assert.ok(restored.width >= 400 && restored.height >= 300,
        `a window opened in landscape needs a useful desktop size: ${JSON.stringify(restored)}`);
});

test('W08: maximized and minimized windows recover after a landscape layout', { skip: skipReason, timeout: 20000 }, async (t) => {
    const { page } = await freshPage(t);
    await interactive(page);
    const window = await openApp(page, 'funciones');
    await window.locator('[data-act="max"]').click();
    await page.setViewportSize({ width: 844, height: 390 });
    await nextFrame(page);
    await assertWindowAboveNavigation(page, window);
    await window.locator('[data-act="min"]').click();
    await window.waitFor({ state: 'hidden' });
    await page.locator('#tabstrip [data-tab="funciones"]').click();
    await window.waitFor({ state: 'visible' });
    await page.setViewportSize({ width: 1440, height: 900 });
    await nextFrame(page);
    await assertWindowAboveNavigation(page, window);
    await window.locator('[data-act="max"]').click();
    await assertWindowAboveNavigation(page, window);
    const bounds = await window.boundingBox();
    assert.ok(bounds.width >= 400 && bounds.height >= 300, JSON.stringify(bounds));
});

test('W03: the complete Iris OS title fits narrow viewports after fonts load', { skip: skipReason, timeout: 20000 }, async (t) => {
    const { page } = await freshPage(t, { viewport: { width: 390, height: 844 } });
    await interactive(page);
    await page.evaluate(() => document.fonts.ready);
    for (const width of [320, 360, 390, 760]) {
        await page.setViewportSize({ width, height: 844 });
        await nextFrame(page);
        const title = await page.locator('.hero h1').evaluate((heading) => {
            const range = document.createRange();
            range.selectNodeContents(heading);
            const rect = range.getBoundingClientRect();
            return { text: heading.textContent.replace(/\s+/g, ''), left: rect.left, right: rect.right, viewport: innerWidth };
        });
        assert.equal(title.text, 'IRISOS');
        assert.ok(title.left >= 0 && title.right <= width, `title must fit ${width}px: ${JSON.stringify(title)}`);
    }
});

test('W04: keyboard minimization transfers focus and Escape still closes the visible window', { skip: skipReason, timeout: 15000 }, async (t) => {
    const { page } = await freshPage(t);
    await interactive(page);
    await openApp(page, 'que-es');
    const functions = await openApp(page, 'funciones');
    await functions.locator('[data-act="min"]').focus();
    await page.keyboard.press('Enter');
    await functions.waitFor({ state: 'hidden' });
    await assertOperationalFocus(page, 'que-es');
    await page.keyboard.press('Escape');
    await page.locator('.os-window[data-app="que-es"]').waitFor({ state: 'detached' });
    await assertOperationalFocus(page);
});

test('W04: keyboard closing transfers focus to the next visible window', { skip: skipReason, timeout: 15000 }, async (t) => {
    const { page } = await freshPage(t);
    await interactive(page);
    await openApp(page, 'que-es');
    const functions = await openApp(page, 'funciones');
    await functions.locator('[data-act="close"]').focus();
    await page.keyboard.press('Enter');
    await functions.waitFor({ state: 'detached' });
    await assertOperationalFocus(page, 'que-es');
    await page.keyboard.press('Escape');
    await page.locator('.os-window[data-app="que-es"]').waitFor({ state: 'detached' });
    await assertOperationalFocus(page);
});

test('W04: minimizing or closing the last window provides a keyboard return route', { skip: skipReason, timeout: 15000 }, async (t) => {
    const { page } = await freshPage(t, { viewport: { width: 1000, height: 900 } });
    await interactive(page);
    let window = await openApp(page, 'que-es');
    await window.locator('[data-act="min"]').focus();
    await page.keyboard.press('Enter');
    await window.waitFor({ state: 'hidden' });
    await assertOperationalFocus(page);
    await page.keyboard.press('Enter');
    await window.waitFor({ state: 'visible' });
    await window.locator('[data-act="close"]').focus();
    await page.keyboard.press('Enter');
    await window.waitFor({ state: 'detached' });
    await assertOperationalFocus(page);
    await page.keyboard.press('Enter');
    window = page.locator('.os-window[data-app="que-es"]');
    await window.waitFor({ state: 'visible' });
});

async function assertStaticFallback(page) {
    await page.waitForFunction(() => document.documentElement.classList.contains('no-js'));
    await page.locator('#apps').waitFor({ state: 'visible' });
    const result = await page.evaluate(() => ({
        bootVisible: Boolean(document.getElementById('boot')?.getClientRects().length),
        articles: [...document.querySelectorAll('article[data-app]')].map((article) => ({
            app: article.dataset.app,
            id: article.id,
            visible: !article.hidden && Boolean(article.getClientRects().length),
            inFallback: article.parentElement.id === 'apps',
        })),
        brokenLinks: [...document.querySelectorAll('a[href^="#"]')]
            .map((anchor) => anchor.getAttribute('href').slice(1))
            .filter((id) => id && !document.getElementById(id)),
        unreadablePanels: [...document.querySelectorAll('.tab-panel')]
            .filter((panel) => !panel.getClientRects().length)
            .map((panel) => panel.id),
    }));
    assert.equal(result.bootVisible, false, 'failed interactive initialization must not trap the visitor in boot');
    assert.ok(result.articles.length > 0);
    for (const article of result.articles) {
        assert.equal(article.id, article.app, `static navigation must identify ${article.app}`);
        assert.ok(article.visible && article.inFallback, `${article.app} must remain readable in #apps`);
    }
    assert.deepEqual(result.brokenLinks, []);
    assert.deepEqual(result.unreadablePanels, [], 'static content must expose each installation information panel');
    const link = page.locator('a[href="#que-es"]').first();
    assert.ok(await link.count(), 'the primary hero action must offer a static navigation route');
    await link.click();
    await page.waitForURL(`${origin}/#que-es`);
    const target = await page.locator('article#que-es').boundingBox();
    const viewport = page.viewportSize();
    assert.ok(target.y < viewport.height && target.y + target.height > 0, 'static anchor navigation must reach the requested article');
}

test('W05: blocking only the main script recovers readable and navigable content', { skip: skipReason, timeout: 15000 }, async (t) => {
    const { page, context } = await freshPage(t);
    await context.route('**/js/iris-site.js', (route) => route.abort());
    await page.goto(origin, { waitUntil: 'load' });
    await assertStaticFallback(page);
});

test('W05: an initialization exception recovers the static content independently', { skip: skipReason, timeout: 15000 }, async (t) => {
    const { page, context } = await freshPage(t, { allowPageErrors: true });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await context.addInitScript(() => {
        const original = document.createElement;
        document.createElement = function (...args) {
            document.createElement = original;
            throw new Error('intentional Iris initialization fixture failure');
        };
    });
    await page.goto(origin, { waitUntil: 'load' });
    await assertStaticFallback(page);
    assert.ok(errors.some((message) => message.includes('intentional Iris initialization fixture failure')), 'the intended initialization failure must execute');
});

test('W05: a main script arriving after recovery cannot reactivate the desktop', { skip: skipReason, timeout: 20000 }, async (t) => {
    const { page, context } = await freshPage(t);
    let release;
    const blocked = new Promise((done) => { release = done; });
    await context.route('**/js/iris-site.js', async (route) => {
        await blocked;
        await route.continue();
    });
    const navigation = page.goto(origin, { waitUntil: 'load' });
    try {
        // The defer script is still pending, so DOMContentLoaded cannot be the
        // trigger; the independent watchdog must provide readable content.
        await page.waitForFunction(() => document.documentElement.classList.contains('no-js'), undefined, { timeout: 10000 });
    } finally {
        release();
    }
    await navigation;
    await assertStaticFallback(page);
    assert.equal(await page.locator('.os-window').count(), 0, 'late initialization must not move the readable articles back into windows');
});

test('W05: a broken image does not abort otherwise successful initialization', { skip: skipReason, timeout: 15000 }, async (t) => {
    const { page, context } = await freshPage(t);
    await context.route('**/assets/*.webp', (route) => route.abort());
    await interactive(page);
    const window = await openApp(page, 'funciones');
    await window.waitFor({ state: 'visible' });
    assert.equal(await page.evaluate(() => document.documentElement.classList.contains('no-js')), false);
});

test('W05: disabling JavaScript preserves readable content and working anchor navigation', { skip: skipReason, timeout: 15000 }, async (t) => {
    const { page } = await freshPage(t, { javaScriptEnabled: false });
    await page.goto(origin, { waitUntil: 'load' });
    await assertStaticFallback(page);
});

test('W06: opening an app from a link keeps the desktop frame in place', { skip: skipReason, timeout: 15000 }, async (t) => {
    const { page } = await freshPage(t);
    await page.goto(`${origin}/#funciones`, { waitUntil: 'load' });
    await page.locator('.os-window[data-app="funciones"]').waitFor({ state: 'visible' });
    await nextFrame(page);
    // The wallpaper overflows 3 % for its drift; the anchor must not scroll the desktop.
    const frame = await page.evaluate(() => ({
        desktop: document.querySelector('.desktop').scrollTop,
        homeTop: document.querySelector('.home').getBoundingClientRect().top,
        topbarTop: document.querySelector('.topbar').getBoundingClientRect().top,
    }));
    assert.equal(frame.desktop, 0, JSON.stringify(frame));
    assert.ok(frame.homeTop >= 0 && frame.topbarTop >= 0, JSON.stringify(frame));
});

test('W06: the top bar status follows the focused window', { skip: skipReason, timeout: 15000 }, async (t) => {
    const { page } = await freshPage(t);
    await interactive(page);
    const status = page.locator('[data-status-readout]');
    const statusIs = (text) => page.waitForFunction((expected) =>
        document.querySelector('[data-status-readout]').textContent === expected, text);
    // A wide screen welcomes the visitor with the first app open.
    await statusIs('Abierta: Qué es IRIS');
    const window = await openApp(page, 'temas');
    assert.equal(await status.textContent(), 'Abierta: Theme Studio');

    await window.locator('[data-act="min"]').click();
    await statusIs('Abierta: Qué es IRIS');
    await page.locator('.os-window[data-app="que-es"] [data-act="min"]').click();
    await statusIs('En espera');
    assert.equal(await page.locator('.os-window:not(.minimized)').count(), 0);
    assert.equal(await page.locator('#tabstrip [data-tab="temas"]').count(), 1, 'minimized, not closed');
});

test('W07: the tiles under IRIS\'s card open their apps and hide only where they would collide', { skip: skipReason, timeout: 20000 }, async (t) => {
    const { page } = await freshPage(t, { viewport: { width: 1440, height: 900 } });
    await interactive(page);
    const tiles = page.locator('.home-tiles button');
    assert.deepEqual(await tiles.allTextContents(), ['Estado', 'Preguntas', 'Arquitectura', 'Historia']);
    const card = await page.locator('.profile').boundingBox();
    const grid = await page.locator('.home-tiles').boundingBox();
    assert.ok(grid.y >= card.y + card.height, `the tiles sit under the card: ${JSON.stringify({ card, grid })}`);
    // Windows open over the desktop: each one is minimized once it has opened.
    await page.locator('.os-window[data-app="que-es"] [data-act="min"]').click();
    for (const id of ['estado', 'preguntas', 'arquitectura', 'historia']) {
        const window = await openApp(page, id);
        await window.locator('[data-act="min"]').click();
        await window.waitFor({ state: 'hidden' });
    }

    // Narrower than the centred dock plus the right column, or too short: only in Apps.
    for (const viewport of [{ width: 1280, height: 900 }, { width: 1440, height: 600 }]) {
        await page.setViewportSize(viewport);
        await nextFrame(page);
        assert.equal(await page.locator('.home-tiles').isVisible(), false, JSON.stringify(viewport));
    }
});

test('W06: an unexpected saved route value does not drop the desktop', { skip: skipReason, timeout: 15000 }, async (t) => {
    const { page, context } = await freshPage(t);
    // Valid JSON that is not a list (an older or edited value) once crashed initialization.
    await context.addInitScript(() => localStorage.setItem('irisSite.visited', '1'));
    await page.goto(origin, { waitUntil: 'load' });
    await page.waitForFunction(() => document.documentElement.classList.contains('js') || document.documentElement.classList.contains('no-js'));
    await nextFrame(page);
    assert.equal(await page.evaluate(() => window.irisSiteBoot.recovered), false);
    assert.equal(await page.evaluate(() => document.documentElement.classList.contains('js')), true);
});
