/** Isolated visual/interaction QA: fake voice API, never Core or real audio. */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import assert from 'node:assert/strict';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const arg = process.argv.indexOf('--playwright-module');
const { chromium } = require(arg >= 0 ? process.argv[arg + 1] : 'playwright');
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'iris-manual-voice-'));
function html(companion) {
    const source = fs.readFileSync(path.join(root, companion ? 'face/companion/index.html' : 'face/index.html'), 'utf8');
    const controls = companion ? source.match(/<nav class="signal-rail"[^]*?<\/nav>/)[0]
        : source.match(/<div id="action-buttons">[^]*?<\/div>/)[0];
    return `<!doctype html><html lang="es"><head><meta charset="utf-8">
        <link rel="stylesheet" href="/face/${companion ? 'companion/companion.css' : 'css/styles.css'}">
        <link rel="stylesheet" href="/face/css/manual_voice.css"></head>
        <body data-controls="visible"><main id="companion-app">${controls}</main><script type="module">
        import { ManualVoiceController } from '/face/js/manual_voice.js';
        window.qaCalls = []; let revision = 0;
        let state = { state: 'off', busy: false, revision };
        window.ui = new ManualVoiceController({ button: document.getElementById('${companion ? 'voice-manual' : 'btn-hermes-voice'}'),
            fetchImpl: async (url, options) => {
                qaCalls.push({ url, method: options.method || 'GET' });
                if (options.method === 'POST') {
                    const data = JSON.parse(options.body);
                    state = { ...data, revision: ++revision, busy: data.action !== 'cancel',
                        state: data.action === 'start' ? 'recording' : data.action === 'stop' ? 'transcribing' : 'off' };
                }
                return { ok: true, json: async () => state };
            } }); ui.setConnected(true);
        </script></body></html>`;
}
const server = http.createServer((req, res) => {
    const p = new URL(req.url, 'http://localhost').pathname;
    if (p === '/' || p === '/companion') {
        res.writeHead(200, { 'Content-Type': 'text/html' }); res.end(html(p === '/companion')); return;
    }
    if (!/^\/face\/(css\/[^/]+\.css|js\/manual_voice\.js|companion\/companion\.css|companion\/fonts\/[\w.-]+\.woff2)$/.test(p)) {
        res.writeHead(404); res.end(); return;
    }
    const f = path.join(root, p);
    fs.readFile(f, (error, data) => {
        if (error) { res.writeHead(404); res.end(); return; }
        res.writeHead(200, { 'Content-Type': f.endsWith('.js') ? 'text/javascript' : f.endsWith('.css') ? 'text/css' : 'font/woff2' });
        res.end(data);
    });
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
let browser;
try {
    browser = await chromium.launch({ headless: true, args: ['--mute-audio'],
        ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
    const origin = 'http://127.0.0.1:' + server.address().port;
    const results = [];
    for (const companion of [false, true]) {
        const page = await browser.newPage({ viewport: companion ? { width: 420, height: 680 } : { width: 1440, height: 900 } });
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
        await page.goto(origin + (companion ? '/companion' : '/'));
        const button = page.locator(companion ? '#voice-manual' : '#btn-hermes-voice');
        await page.waitForFunction(() => window.ui && !ui.button.disabled);
        assert.equal(await page.evaluate(() => qaCalls.filter(c => c.method === 'POST').length), 0);
        await button.click();
        await page.locator('[data-confirm]').click();
        await page.waitForFunction(() => ui.state.state === 'recording');
        assert.equal(await button.getAttribute('aria-pressed'), 'true');
        const bounds = await page.locator('.manual-voice-panel').boundingBox();
        const size = page.viewportSize();
        assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= size.width && bounds.y + bounds.height <= size.height);
        await page.screenshot({ path: path.join(output, companion ? 'companion.jpg' : 'room.jpg'), type: 'jpeg', quality: 70 });
        await button.click();
        await page.waitForFunction(() => ui.state.state === 'transcribing');
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !ui.state.busy);
        assert.equal(await button.getAttribute('aria-pressed'), 'false');
        // A new utterance completes normally, including an idle notification
        // after the accepted transcript. The text must remain reviewable.
        await button.click();
        await page.locator('[data-confirm]').click();
        await page.waitForFunction(() => ui.state.state === 'recording');
        await button.click();
        await page.waitForFunction(() => ui.state.state === 'transcribing');
        const postsBefore = await page.evaluate(() => qaCalls.filter(c => c.method === 'POST').length);
        await page.evaluate(() => {
            ui.showTranscript({mode:'manual',accepted:true,text:'Hola Iris, esto es una prueba de voz en español.',capture_id:ui.state.capture_id});
            ui.apply({...ui.state,revision:100,state:'off',busy:false});
        });
        assert.match(await page.locator('.manual-voice-panel > p').textContent(), /Texto enviado:.*prueba de voz en español/);
        assert.equal(await page.evaluate(() => qaCalls.filter(c => c.method === 'POST').length), postsBefore);
        await page.screenshot({ path:path.join(output,companion?'companion-sent.jpg':'room-sent.jpg'),type:'jpeg',quality:80 });
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('.manual-voice-panel').isVisible(),false);
        assert.deepEqual(errors, []);
        results.push({ surface: companion ? 'companion' : 'room', consent: true, send: true, transcriptVisible: true, noDuplicateSend: true, cancel: true, bounds: true });
        await page.close();
    }
    console.log(JSON.stringify({ status: 'passed', output, results, realMicrophone: false }, null, 2));
} finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
}
