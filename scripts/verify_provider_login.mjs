/* Full wizard DOM and production JS, with deterministic auth HTTP fixtures. */
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = path.join(root, 'artifacts/provider-login');
await fs.mkdir(output, { recursive: true });
const sessions = new Map();
let posts = 0, deletes = 0;
const server = http.createServer(async (request, response) => {
    try {
        const url = new URL(request.url, 'http://localhost');
        if (url.pathname.startsWith('/api/provider-auth/sessions')) {
            response.setHeader('Content-Type', 'application/json');
            const id = url.pathname.split('/')[4];
            if (request.method === 'POST') {
                let body = '';
                for await (const chunk of request) body += chunk;
                const { provider } = JSON.parse(body);
                const session_id = `fixture-login-${String(++posts).padStart(12, '0')}`;
                const session = { session_id, provider, status: provider === 'anthropic' ? 'external' : 'pending',
                    flow: provider === 'openrouter' ? 'browser' : 'device_code',
                    url: 'https://example.test/authorize', user_code: provider === 'openrouter' ? '' : 'FAKE-1234',
                    expires_at: Date.now() / 1000 + 600,
                    message: provider === 'anthropic' ? 'Usa una sesión existente o una API key.' : undefined };
                sessions.set(session_id, session);
                response.end(JSON.stringify(session));
            } else if (!sessions.has(id)) {
                response.writeHead(404).end(JSON.stringify({ detail: 'Sesión no encontrada' }));
            } else {
                const session = sessions.get(id);
                if (request.method === 'DELETE') {
                    session.status = 'cancelled';
                    session.message = 'Inicio de sesión cancelado.';
                    deletes++;
                }
                response.end(JSON.stringify(session));
            }
            return;
        }
        if (!url.pathname.startsWith('/ui/')) { response.writeHead(404).end(); return; }
        const relative = url.pathname.slice(4) || 'index.html';
        const file = path.resolve(root, 'wizard/ui', relative);
        if (!file.startsWith(path.join(root, 'wizard/ui') + path.sep)) throw new Error('Invalid path');
        let content = await fs.readFile(file);
        if (relative === 'wizard.js') {
            // Skip environmental discovery only; exercise the real provider
            // selector, OAuth toggle, rendering and checklist functions below.
            content = Buffer.from(content.toString().replace(/initWizard\(\);\s*$/, ''));
        }
        const ext = path.extname(file);
        response.setHeader('Content-Type', ({ '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css' })[ext] || 'application/octet-stream');
        response.end(content);
    } catch (error) { response.writeHead(500).end(String(error)); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
let browser, page;
const errors = [];
try {
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    const context = await browser.newContext({ viewport: { width: 1200, height: 980 }, permissions: ['clipboard-read', 'clipboard-write'] });
    page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.request().url().startsWith(base) ? route.continue() : route.abort());
    async function prepare(provider = 'openai') {
        await page.evaluate(provider => {
            document.querySelectorAll('.wizard-step').forEach(el => el.classList.toggle('active', el.dataset.step === '3'));
            currentStep = 3;
            document.querySelectorAll('[id*="hermes-full"]').forEach(el => { el.style.display = 'block'; });
            wizardState.hermes_mode = 'full';
            LLM_PROVIDERS.openrouter.auth = 'api+oauth';
            renderProviderList();
            const radio = document.querySelector(`input[name="llm_provider"][value="${provider}"]`);
            radio.checked = true;
            wizardState.llm_provider = provider;
            document.getElementById('use-oauth').checked = true;
            updateLLMProviderUI(provider);
            toggleOAuthMode();
            // Provider setup is nested in installation-mode panels. Reveal
            // its ancestors without replacing any production form markup.
            for (let el = document.getElementById('llm-oauth-section'); el && el !== document.body; el = el.parentElement) {
                if (getComputedStyle(el).display === 'none') el.style.display = 'block';
            }
        }, provider);
    }
    await page.goto(`${base}/ui/index.html`);
    await prepare();
    await page.locator('#btn-auth-oauth').click();
    await page.locator('#oauth-device-code').waitFor();
    assert.equal(await page.locator('#oauth-device-code').textContent(), 'FAKE-1234');
    await page.getByRole('button', { name: 'Copiar código', exact: true }).click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'FAKE-1234');
    assert.equal(await page.locator('#oauth-open-link').getAttribute('target'), '_blank');
    await page.locator('#oauth-flow-panel').screenshot({ path: path.join(output, 'device-code.png') });
    await page.reload();
    await prepare();
    await page.locator('#oauth-device-code').waitFor();
    assert.equal(posts, 1, 'Reload must resume without starting a second login');
    sessions.get([...sessions.keys()][0]).status = 'approved';
    await page.waitForFunction(() => wizardState.oauth_authenticated === true);
    assert.equal(await page.evaluate(() => wizardState.oauth_authenticated_provider), 'openai');
    await page.locator('#oauth-flow-panel').screenshot({ path: path.join(output, 'connected.png') });

    await page.locator('#btn-auth-oauth').click();
    await page.locator('#oauth-device-code').waitFor();
    await page.getByRole('button', { name: 'Cancelar', exact: true }).click();
    await page.waitForFunction(() => document.getElementById('oauth-flow-log').textContent.includes('cancelado'));
    assert.equal(deletes, 1);
    assert.equal(await page.evaluate(() => wizardState.oauth_authenticated), false);

    async function switchProvider(provider) {
        await page.evaluate(provider => {
            document.querySelector(`input[name="llm_provider"][value="${provider}"]`).checked = true;
            updateLLMProviderUI(provider);
            document.getElementById('use-oauth').checked = true;
            toggleOAuthMode();
        }, provider);
        assert.equal(await page.locator('#btn-auth-oauth').isEnabled(), true,
            'Switching provider must release the previous login button');
    }
    await switchProvider('openrouter');
    await page.locator('#btn-auth-oauth').click();
    await page.locator('#oauth-open-link').waitFor();
    assert.equal(await page.locator('#oauth-device-code').count(), 0);
    // Switch while the OpenRouter operation is still pending. Cancelling its
    // worker must not leave the newly selected provider's button disabled.
    await switchProvider('anthropic');
    await page.locator('#btn-auth-oauth').click();
    await page.getByRole('button', { name: 'Comprobar sesión existente' }).waitFor();
    await page.locator('#oauth-flow-panel').screenshot({ path: path.join(output, 'external-provider.png') });
    await page.getByRole('button', { name: 'Usar API key', exact: true }).click();
    assert.equal(await page.locator('#use-oauth').isChecked(), false);
    assert.equal(await page.locator('#llm-key-section').isVisible(), true);
    assert.deepEqual(errors, []);
    await fs.writeFile(path.join(output, 'browser.json'), JSON.stringify({
        ok: true, fixtureProviders: true, posts, deletes,
        checked: ['device code', 'copy', 'safe link', 'reload', 'success', 'cancel', 'OpenRouter callback UI', 'switch while pending', 'external provider', 'API key switch'],
    }, null, 2));
    console.log(JSON.stringify({ ok: true, output, posts, deletes }));
} catch (error) {
    console.error(JSON.stringify({ error: String(error), posts, deletes, errors,
        ui: await page?.evaluate(() => ({
            status: document.getElementById('oauth-flow-log')?.textContent,
            checked: document.getElementById('use-oauth')?.checked,
            selected: selectedLLMProvider(), login: _irisProviderLogin?.session,
        })).catch(() => null) }));
    await page?.screenshot({ path: path.join(output, 'failure.png'), fullPage: true });
    throw error;
} finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
}
