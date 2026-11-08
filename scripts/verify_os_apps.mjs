/** Optional isolated app-window QA. No Core, Hermes, user profile or devices. */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import assert from 'node:assert/strict';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const arg = process.argv.indexOf('--playwright-module');
const { chromium } = require(arg >= 0 ? process.argv[arg + 1] : 'playwright');
const vendor = path.join(root, 'companion', 'node_modules', 'three', 'build');
const calls = [], writes = [];
const html = `<!doctype html><html><head><link rel="stylesheet" href="/face/css/styles.css">
<script type="importmap">{"imports":{"three":"/vendor/three.module.js"}}</script></head>
<body><script type="module">
import { HermesActivityCanvas } from '/face/js/hermes_activity.js';
window.osQA = new HermesActivityCanvas(); osQA.open();
</script></body></html>`;
const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost'), p = url.pathname;
    if (req.method !== 'GET') { writes.push(req.method + ' ' + p); res.writeHead(405); res.end(); return; }
    if (p === '/') { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end(html); return; }
    if (p.startsWith('/api/')) {
        calls.push(p);
        const data = p === '/api/cron/jobs' ? { jobs: [{ id: 'qa-routine', name: 'QA fixture' }] }
            : p === '/api/hermes/capabilities' ? { success: true, supports: { runs: true } }
            : { success: true, runs: [], outputs: [], lines: ['QA fixture'], items: [] };
        res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data)); return;
    }
    const file = /^\/vendor\/three\.(module|core)\.js$/.test(p) ? path.join(vendor, path.basename(p))
        : /^\/face\/(js\/[^/]+\.js|css\/[^/]+\.css)$/.test(p) ? path.resolve(root, '.' + p) : null;
    if (!file) { res.writeHead(404); res.end(); return; }
    fs.readFile(file, (error, data) => {
        if (error) { res.writeHead(404); res.end(); return; }
        res.writeHead(200, { 'Content-Type': file.endsWith('.js') ? 'text/javascript' : 'text/css' }); res.end(data);
    });
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
let browser;
try {
    browser = await chromium.launch({ headless: true, args: ['--mute-audio'],
        ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    const errors = [], origin = 'http://127.0.0.1:' + server.address().port;
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin + '/');
    await page.waitForFunction(() => window.osQA);
    // Lifecycle assertions use the real class, its generated windows and production CSS.
    const result = await page.evaluate(() => {
        const app = window.osQA, checks = [];
        function check(label, expected, timer) {
            const el = app._osWindows.get('missions')?.el;
            const visible = !!el && el.checkVisibility({ checkVisibilityCSS: true });
            if (visible !== expected || app._hasVisibleAppSurface('missions') !== expected
                || !!app._missionPollTimer !== timer) throw Error(label + ' visibility/poll mismatch');
            checks.push(label);
        }
        app._openWindow('missions'); check('open', true, true);
        app._minimizeWindow('missions'); check('minimize', false, false);
        app._openWindow('missions'); check('reopen existing', true, true);
        app._setDesktopMode('dashboard'); check('dashboard hides windows', false, false);
        app._setDesktopMode('home'); check('home restores windows', true, true);
        app._setTab('settings'); check('other tab', false, false);
        app._restoreWindow('missions'); check('restore', true, true);
        app.close(); check('close OS', false, false);
        app.open(); check('reopen OS', true, true);
        app._closeWindow('missions'); check('close window', false, false);
        app._openWindow('doctor'); app._openWindow('routines');
        return checks;
    });
    await page.waitForFunction(() => osQA._gatewayLogsLoadedAt && osQA._cronJobOutputsLoadedAt);
    assert.ok(calls.includes('/api/gateway-logs'));
    assert.ok(calls.includes('/api/cron/jobs/qa-routine/outputs'));
    assert.deepEqual(writes, []); assert.deepEqual(errors, []);
    console.log(JSON.stringify({ status: 'passed', lifecycle: result, logs: true, history: true,
        backendWrites: writes.length, pageErrors: errors }, null, 2));
} finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
}
