/* Production wizard DOM/JS with HTTP fixtures. No external accounts or messages. */
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = path.join(root, 'artifacts/messaging-review');
await fs.mkdir(output, { recursive: true });
const field = (key, is_set = false) => ({ key, prompt: key, is_set, required: true, is_password: true });
const platform = (id, name, fields = []) => ({ id, name, fields, enabled: false, configured: false,
    state: 'not_configured', description: `Configura ${name} desde Hermes.`, docs_url: 'https://example.test/guide' });
const platforms = [platform('telegram', 'Telegram', [field('TELEGRAM_BOT_TOKEN', true)]),
    platform('discord', 'Discord', [field('DISCORD_BOT_TOKEN')]), platform('whatsapp', 'WhatsApp'),
    platform('community_plugin', 'Canal de una extensión', [field('COMMUNITY_SECRET')]),
    { ...platform('api_server', 'Conexión de Iris'), managed: true, enabled: true, state: 'connected' }];
const writes = [], pairings = new Map();
let starts = 0, cancels = 0, applies = 0, restarts = 0, checks = 0, ready = false, rejectSave = false;
const qr = 'data:image/svg+xml;base64,' + Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="white"/><path d="M10 10h25v25H10zm55 0h25v25H65zM10 65h25v25H10zm35-20h10v10H45z"/></svg>').toString('base64');
const server = http.createServer(async (req, res) => {
    try {
        const url = new URL(req.url, 'http://localhost');
        if (url.pathname.startsWith('/api/messaging/')) {
            res.setHeader('Content-Type', 'application/json');
            let raw = ''; for await (const part of req) raw += part;
            const body = raw ? JSON.parse(raw) : {};
            const route = url.pathname.slice('/api/messaging'.length);
            const send = value => res.end(JSON.stringify(value));
            if (route === '/platforms') return send({ platforms, hermes_version: 'fixture', policy_ready: ready });
            if (route === '/prepare') { ready = true; return send({ ok: true, needs_restart: true }); }
            if (route === '/restart') { restarts++; return send({ restart_started: true }); }
            if (route.startsWith('/platforms/')) {
                if (route.endsWith('/test')) { checks++; return send({ ok: false, state: 'gateway_stopped' }); }
                if (rejectSave) { res.statusCode = 400; return send({ detail: 'Credencial de prueba inválida' }); }
                writes.push({ id: route.split('/')[2], body }); ready = true;
                return send({ ok: true, needs_restart: true });
            }
            if (route === '/whatsapp/pairing') {
                const pairing_id = `fixture-pairing-${String(++starts).padStart(12, '0')}`;
                const value = { pairing_id, status: 'waiting', qr_image: qr, ...body };
                pairings.set(pairing_id, value); return send({ ...value, status: 'installing', qr_image: undefined });
            }
            const id = route.split('/')[3];
            if (route.endsWith('/apply')) { applies++; pairings.delete(id); return send({ ok: true, needs_restart: true }); }
            if (req.method === 'DELETE') { cancels++; pairings.delete(id); return send({ ok: true }); }
            if (pairings.has(id)) return send(pairings.get(id));
            res.statusCode = 404; return send({ detail: 'La vinculación venció' });
        }
        if (!url.pathname.startsWith('/ui/')) { res.writeHead(404).end(); return; }
        const relative = url.pathname.slice(4) || 'index.html';
        const file = path.resolve(root, 'wizard/ui', relative);
        if (!file.startsWith(path.join(root, 'wizard/ui') + path.sep)) throw new Error('Invalid path');
        let content = await fs.readFile(file);
        if (relative === 'wizard.js') content = Buffer.from(content.toString().replace(/initWizard\(\);\s*$/, ''));
        res.setHeader('Content-Type', ({ '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css' })[path.extname(file)] || 'application/octet-stream');
        res.end(content);
    } catch (error) { res.writeHead(500).end(String(error)); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
let browser, page;
const errors = [];
try {
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    page = await browser.newPage({ viewport: { width: 1200, height: 1000 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.request().url().startsWith(base) ? route.continue() : route.abort());
    async function mount() {
        await page.evaluate(() => {
            for (let el = document.getElementById('messaging-panel'); el && el !== document.body; el = el.parentElement) {
                if (getComputedStyle(el).display === 'none') el.style.display = 'block';
            }
            setupChannelHandlers();
        });
        await page.locator('.messaging-card').first().waitFor();
    }
    const card = id => page.locator(`.messaging-card[data-platform="${id}"]`);
    async function open(id) { if (!await card(id).getAttribute('open').then(v => v !== null)) await card(id).locator('summary').click(); }
    const search = () => page.getByRole('searchbox', { name: 'Buscar canal' });
    await page.goto(`${base}/ui/index.html`); await mount();
    assert.equal(await page.locator('.messaging-card').count(), 5);
    await search().fill('extensión'); assert.equal(await card('community_plugin').isVisible(), true);
    assert.equal(await card('telegram').isVisible(), false); await search().fill('');
    await page.getByLabel('Solo activos').check(); assert.equal(await card('api_server').isVisible(), true);
    assert.equal(await card('telegram').isVisible(), false); await page.getByLabel('Solo activos').uncheck();
    await open('api_server'); assert.equal(await card('api_server').getByRole('button', { name: 'Guardar canal' }).count(), 0);
    await open('telegram'); await open('discord');
    assert.equal(await card('telegram').locator('input[data-key]').inputValue(), '');
    assert.equal(await card('telegram').locator('input[data-key]').getAttribute('type'), 'password');
    await card('discord').locator('input[data-key]').fill('draft-discord-secret');
    await card('telegram').getByRole('button', { name: 'Guardar canal' }).click();
    await page.waitForFunction(() => document.querySelector('.messaging-status').textContent.includes('Guardado'));
    assert.deepEqual(writes[0].body.env, {}); assert.equal(restarts, 0);
    assert.equal(await card('discord').locator('input[data-key]').inputValue(), 'draft-discord-secret');
    await card('telegram').getByLabel('Borrar el valor guardado').check();
    assert.equal(await card('telegram').locator('input[data-key]').isDisabled(), true);
    await card('telegram').getByRole('button', { name: 'Guardar canal' }).click();
    await page.waitForFunction(() => !document.querySelector('[data-platform="telegram"] .messaging-clear input').checked);
    assert.deepEqual(writes[1].body.clear_env, ['TELEGRAM_BOT_TOKEN']);
    rejectSave = true;
    await card('discord').getByRole('button', { name: 'Guardar canal' }).click();
    await page.getByText('Credencial de prueba inválida', { exact: true }).waitFor();
    assert.equal(await card('discord').locator('input[data-key]').inputValue(), 'draft-discord-secret');
    rejectSave = false;
    await card('telegram').getByRole('button', { name: 'Comprobar estado' }).click();
    await page.waitForFunction(() => document.querySelector('.messaging-status').textContent.includes('Hermes detenido'));
    assert.equal(checks, 1);
    await search().fill('telegram');
    await page.locator('#messaging-panel').screenshot({ path: path.join(output, 'desktop.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('#messaging-panel').screenshot({ path: path.join(output, 'mobile.png') });
    assert.equal(await page.locator('#messaging-panel').evaluate(el => el.scrollWidth <= el.clientWidth), true);
    await page.setViewportSize({ width: 1200, height: 1000 }); await search().fill('whatsapp'); await open('whatsapp');
    await card('whatsapp').getByRole('button', { name: 'Mostrar QR' }).click();
    await page.getByAltText('Código QR para vincular WhatsApp').waitFor();
    await page.reload(); await mount(); await search().fill('whatsapp'); await open('whatsapp');
    await page.getByAltText('Código QR para vincular WhatsApp').waitFor(); assert.equal(starts, 1);
    await page.locator('#messaging-panel').screenshot({ path: path.join(output, 'whatsapp.png') });
    const id = [...pairings.keys()][0]; pairings.get(id).status = 'connected';
    await page.getByRole('button', { name: 'Guardar vinculación', exact: true }).click();
    await page.waitForFunction(() => sessionStorage.getItem('iris.whatsapp-pairing') === null);
    assert.equal(applies, 1); assert.equal(restarts, 0);
    await page.getByRole('button', { name: 'Mostrar QR' }).click();
    await page.getByAltText('Código QR para vincular WhatsApp').waitFor();
    await page.getByRole('button', { name: 'Cancelar vinculación' }).click();
    await page.waitForFunction(() => sessionStorage.getItem('iris.whatsapp-pairing') === null); assert.equal(cancels, 1);
    await page.getByRole('button', { name: 'Reiniciar Hermes para aplicar' }).click();
    await page.getByText('Hermes se está reiniciando.', { exact: false }).waitFor(); assert.equal(restarts, 1);
    const storage = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
    assert(!storage.includes('secret')); assert.deepEqual(errors, []);
    const report = { ok: true, writes: writes.length, starts, cancels, applies, restarts,
        checks: ['dynamic plugin', 'search', 'active filter', 'managed API', 'secret preservation', 'explicit clear',
            'validation errors', 'independent drafts', 'read-only status', 'mobile layout', 'QR', 'resume', 'apply', 'cancel'],
        fixtureAccounts: true, realMessagesSent: 0 };
    await fs.writeFile(path.join(output, 'browser.json'), JSON.stringify(report, null, 2)); console.log(JSON.stringify(report));
} catch (error) {
    console.error(JSON.stringify({ error: String(error), errors, writes: writes.length, starts, cancels, applies }));
    await page?.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }); throw error;
} finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
