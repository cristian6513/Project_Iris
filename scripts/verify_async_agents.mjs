/** Isolated browser QA of the real Async Agents UI; all API data is synthetic. */
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
const output = path.join(root, 'artifacts', 'async-agents-qa');
fs.mkdirSync(output, { recursive: true });
const manual = [{ id: 'manual-1', title: 'Preparar un resumen del proyecto', source: 'manual', status: 'running', created_at: 1789804800 }];
const answer = 'Revisión completada.\n\n1. Las tareas manuales conservan su historial.\n2. El chat muestra las delegaciones de esta identidad.\n3. El resultado se puede consultar mientras continúa la conversación.\n\nTexto literal: <img src=x onerror="window.injected=true">';
const chat = [
    { id: 'chat-1', title: 'Revisar el flujo de agentes en segundo plano', source: 'chat', status: 'completed', created_at: 1789805000, summary: 'Revisión completada. Estados y resultados disponibles.', result: answer, child_count: 2 },
    { id: 'chat-2', title: 'Comprobar las dependencias del proyecto', source: 'chat', status: 'running', created_at: 1789804900 },
];
const calls = [];
const html = `<!doctype html><html lang="es"><head><meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/face/css/styles.css"><link rel="stylesheet" href="/face/css/iris_os_responsive.css"><link rel="stylesheet" href="/face/css/async_agents.css">
<script type="importmap">{"imports":{"three":"/vendor/three.module.js"}}</script></head>
<body><script type="module">import { HermesActivityCanvas } from '/face/js/hermes_activity.js';
window.osQA = new HermesActivityCanvas(); osQA.open(); osQA._openWindow('agents');</script></body></html>`;
const server = http.createServer(async (req, res) => {
    const p = new URL(req.url, 'http://localhost').pathname;
    if (p === '/') { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end(html); return; }
    if (p.startsWith('/api/')) {
        calls.push(req.method + ' ' + p);
        let data = { success: true, runs: [], outputs: [], items: [] };
        if (p === '/api/hermes/capabilities') data.supports = { run_submission: true, run_status: true, run_stop: true };
        if (p === '/api/hermes/async-agents') data = { success: true, scope: 'qa', manual: { success: true, runs: manual }, chat: { success: true, runs: chat.map(({ result, ...r }) => r) } };
        if (p.startsWith('/api/hermes/delegations/')) data = { success: true, run: chat.find(r => p.endsWith('/' + r.id)), events: [] };
        if (p === '/api/hermes/runs' && req.method === 'POST') {
            let body = ''; for await (const chunk of req) body += chunk;
            const run = { id: 'manual-2', title: JSON.parse(body).prompt, source: 'manual', status: 'started' };
            manual.unshift(run); data = { success: true, run };
        } else if (p.startsWith('/api/hermes/runs/')) {
            const id = p.split('/')[4], run = manual.find(r => r.id === id);
            if (p.endsWith('/stop') && run) run.status = 'cancelled';
            data = { success: true, run, events: [] };
        }
        res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data)); return;
    }
    const file = /^\/vendor\/three\.(module|core)\.js$/.test(p) ? path.join(root, 'companion/node_modules/three/build', path.basename(p))
        : /^\/face\/(js\/[^/]+\.js|css\/[^/]+\.css)$/.test(p) ? path.resolve(root, '.' + p) : null;
    if (!file) { res.writeHead(404); res.end(); return; }
    fs.readFile(file, (error, content) => {
        if (error) { res.writeHead(404); res.end(); return; }
        res.writeHead(200, { 'Content-Type': file.endsWith('.js') ? 'text/javascript' : 'text/css' }); res.end(content);
    });
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
let browser;
try {
    browser = await chromium.launch({ headless: true, args: ['--mute-audio'], ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], origin = 'http://127.0.0.1:' + server.address().port;
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    const settled = () => page.waitForFunction(() => window.osQA?._asyncRunsLoadedAt && !osQA._asyncRunsLoading && !osQA._asyncRunDetailLoading);
    const sourceTab = source => page.locator(`[data-agent-source="${source}"]`);
    await page.goto(origin); await settled();
    await page.evaluate(() => osQA._toggleMaximizeWindow('agents'));
    assert.equal(await page.locator('[data-source-count="manual"]').textContent(), '1');
    assert.equal(await page.locator('[data-source-count="chat"]').textContent(), '2');
    assert.equal(await page.locator('.os-agents-active').textContent(), '1');
    await page.locator('.os-agents-section').screenshot({ path: path.join(output, 'manual.png') });
    await sourceTab('manual').focus(); await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => osQA._asyncRunDetail?.run?.id === 'chat-1');
    assert.equal(await sourceTab('chat').getAttribute('aria-selected'), 'true');
    assert.equal(await page.locator('.os-agents-create').isVisible(), false);
    assert.equal(await page.locator('[data-agent-action="stop"]').count(), 0);
    assert.equal(await page.locator('.os-agent-result-text').textContent(), answer);
    assert.equal(await page.evaluate(() => !!window.injected), false);
    await page.locator('.os-agent-result-text').scrollIntoViewIfNeeded();
    await page.locator('.os-agents-section').evaluate(el => el.closest('.os-window').scrollIntoView({ block: 'nearest' }));
    await page.locator('.os-agents-section').locator('xpath=ancestor::*[contains(@class,"os-window")][1]').screenshot({ path: path.join(output, 'chat.png') });
    chat[1].status = 'completed'; chat[1].result = 'Dependencias verificadas.';
    await page.locator('[data-os-refresh="agents"]').click(); await settled();
    assert.equal(await page.locator('.os-agents-done').textContent(), '2');
    assert.equal(await page.locator('.os-agents-active').textContent(), '0');
    await sourceTab('manual').click();
    await page.locator('.os-agents-prompt').fill('Crear una guía de instalación');
    await page.locator('.os-agents-create button').click();
    await page.waitForFunction(() => osQA._asyncRuns.length === 2 && !osQA._asyncRunAction && !osQA._asyncRunsLoading);
    assert.equal(await page.locator('[data-source-count="chat"]').textContent(), '2');
    await page.reload(); await settled();
    assert.equal(await page.locator('[data-source-count="manual"]').textContent(), '2');
    await page.setViewportSize({ width: 390, height: 844 });
    await sourceTab('chat').click();
    await page.waitForFunction(() => osQA._asyncRunDetail?.run?.id === 'chat-1');
    await page.locator('.os-agent-result-text').scrollIntoViewIfNeeded();
    await page.locator('.os-agents-section').locator('xpath=ancestor::*[contains(@class,"os-window")][1]').screenshot({ path: path.join(output, 'chat-mobile.png') });
    const overflow = await page.locator('.os-agents-section').evaluate(el => el.scrollWidth - el.clientWidth);
    assert.ok(overflow <= 1, 'Panel horizontal overflow: ' + overflow);
    assert.deepEqual(errors, []);
    assert.equal(calls.filter(c => c === 'GET /api/hermes/runs').length, 0);
    assert.equal(calls.filter(c => c === 'POST /api/hermes/runs').length, 1);
    console.log(JSON.stringify({ status: 'passed', checks: ['two sources', 'keyboard navigation', 'full escaped results', 'completion counters', 'manual creation', 'reload', 'mobile layout'], pageErrors: errors, screenshots: output }, null, 2));
} finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
}
