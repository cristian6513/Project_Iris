/** Real IRIS UI. Synthetic by default; real Hermes mutation requires the explicit flag. */
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const option = name => { const i = process.argv.indexOf(name); return i < 0 ? '' : process.argv[i + 1]; };
const real = process.argv.includes('--apply-real-hermes-update');
const fail = process.argv.includes('--simulate-failure');
const { chromium } = createRequire(import.meta.url)(option('--playwright-module') || 'playwright');
const output = path.join(root, 'artifacts/hermes-update', real ? 'browser-real' : fail ? 'browser-failure' : 'browser-fixture');
fs.mkdirSync(output, { recursive: true });
let backend, backendOrigin, browser, mutationStarted = false;
const writes = [], errors = [];
let fixture = { active: false, phase: 'idle' };
const snapshot = { can_update: true, summary: 'updates_available', items: [
    { id: 'iris-os', label: 'IRIS OS', can_update: false, status: 'current_with_local_changes', version: '1.0.0' },
    { id: 'hermes-agent', label: 'Hermes Agent', can_update: true, status: 'update_available', version: 'Test' },
] };
const html = `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/face/css/styles.css">
<script type="importmap">{"imports":{"three":"/vendor/three.module.js"}}</script></head><body>
<script type="module">import { HermesActivityCanvas } from '/face/js/hermes_activity.js';
window.osQA=new HermesActivityCanvas();osQA.open();osQA._setTab('settings');</script></body></html>`;
const json = (res, body, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
const server = http.createServer(async (req, res) => {
    try {
        const p = new URL(req.url, 'http://localhost').pathname;
        if (p === '/') { res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); res.end(html); return; }
        if (p.startsWith('/api/system-updates/')) {
            if (req.method === 'POST') writes.push(p);
            if (real) {
                const response = await fetch(backendOrigin + req.url, { method: req.method });
                const body = await response.text();
                res.writeHead(response.status, { 'Content-Type': 'application/json' }); res.end(body); return;
            }
            if (p.endsWith('/check')) { json(res, snapshot); return; }
            if (p.endsWith('/apply')) {
                fixture = { active: true, phase: 'updating' };
                setTimeout(() => { fixture = { active: false, phase: fail ? 'failed' : 'completed', ok: !fail,
                    message: fail ? 'No se pudieron actualizar las dependencias.' : 'Actualización completada.', snapshot }; }, 5500);
                json(res, fixture, 202); return;
            }
            json(res, fixture); return;
        }
        if (p.startsWith('/api/')) {
            json(res, { success: true, supports: {}, runs: [], outputs: [], items: [], lines: [] }); return;
        }
        const file = /^\/vendor\/three\.(module|core)\.js$/.test(p)
            ? path.join(root, 'companion/node_modules/three/build', path.basename(p))
            : /^\/face\/(js\/[^/]+\.js|css\/[^/]+\.css)$/.test(p) ? path.resolve(root, '.' + p) : null;
        if (!file || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
        res.writeHead(200, { 'Content-Type': file.endsWith('.js') ? 'text/javascript' : 'text/css' });
        fs.createReadStream(file).pipe(res);
    } catch (error) { json(res, { detail: error.message }, 500); }
});

async function status() {
    if (!real) return fixture;
    return (await fetch(backendOrigin + '/api/system-updates/status')).json();
}

try {
    if (real) {
        backend = spawn(path.join(root, '.venv/Scripts/python.exe'), ['scripts/serve_system_update_test.py'],
            { cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
        const log = fs.createWriteStream(path.join(output, 'backend.log'));
        backend.stderr.pipe(log);
        backendOrigin = await new Promise((resolve, reject) => {
            let pending = '';
            backend.stdout.on('data', chunk => { pending += chunk; const m = pending.match(/IRIS_UPDATE_PORT=(\d+)/); if (m) resolve('http://127.0.0.1:' + m[1]); });
            backend.once('exit', code => reject(Error('Update test server exited: ' + code)));
        });
        console.log('Production IRIS update backend:', backendOrigin);
    }
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = 'http://127.0.0.1:' + server.address().port;
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    console.log('Opening the production settings UI.');
    await page.goto(origin);
    await page.locator('[data-system-update-action="check"]').click();
    await page.waitForFunction(() => osQA?._systemUpdates && !osQA._systemUpdateLoading, null, { timeout: 240000 });
    const before = await page.evaluate(() => osQA._systemUpdates);
    fs.writeFileSync(path.join(output, 'before.json'), JSON.stringify(before, null, 2));
    assert.deepEqual(before.items.filter(item => item.can_update).map(item => item.id), ['hermes-agent'],
        'The UI test must update only Hermes; IRIS source must remain untouched.');
    await page.locator('[data-system-update-action="apply"]').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, 'before.png') });
    console.log(real ? 'Clicking Actualizar: this runs the official Hermes updater.' : 'Clicking Actualizar with a synthetic updater.');
    mutationStarted = true;
    await page.locator('[data-system-update-action="apply"]').click();
    await page.waitForFunction(() => osQA._systemUpdateApplying);
    while (!(await status()).active) await new Promise(resolve => setTimeout(resolve, 200));
    await page.reload();
    await page.waitForFunction(() => window.osQA?._systemUpdateApplying, null, { timeout: 30000 });
    console.log('Reloaded the page; it recovered the existing update job.');
    let last = '', job;
    do {
        job = await status();
        if (job.phase !== last) { last = job.phase; console.log('Update phase:', last); }
        if (job.active) await new Promise(resolve => setTimeout(resolve, 2000));
    } while (job.active);
    await page.waitForFunction(() => !osQA._systemUpdateApplying && !osQA._systemUpdateLoading, null, { timeout: 30000 });
    const ui = await page.locator('.os-system-update-zone').innerText();
    await page.locator('[data-system-update-action="apply"]').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, 'after.png') });
    await page.reload();
    await page.waitForFunction(() => window.osQA?._systemUpdates && !osQA._systemUpdateLoading, null, { timeout: 30000 });
    const reopened = await page.locator('.os-system-update-zone').innerText();
    assert.equal(reopened, ui, 'A completed or failed job must remain visible after reopening.');
    const report = { mode: real ? 'real-hermes-update' : 'fixture', job, ui, reopened, writes, errors };
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    assert.equal(writes.length, 1, 'Reload must not launch a second updater.');
    assert.deepEqual(errors, []);
    if (fail) assert.match(ui, /No se pudieron actualizar las dependencias/);
    else assert.equal(job.ok, true, job.message);
    console.log(JSON.stringify({ status: 'passed', real, writes: writes.length, phase: job.phase, output }));
} finally {
    if (browser) await browser.close();
    // Never kill the supervised backend while a real installer is in progress,
    // even if a visual assertion failed. It must finish its native transaction.
    if (backend && mutationStarted) {
        while ((await status()).active) await new Promise(resolve => setTimeout(resolve, 3000));
    }
    if (backend) backend.kill();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
}
