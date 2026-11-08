/** Real desktop controller + production UI, isolated homes, no external accounts. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import fs from 'node:fs/promises';
import http from 'node:http';
import net from 'node:net';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.join(root, 'artifacts/desktop-review');
await fs.mkdir(output, { recursive: true });
const profile = await fs.mkdtemp(path.join(output, 'profile-'));
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const native = process.argv.includes('--native');
const env = { ...process.env, IRIS_HOME: path.join(profile, 'iris'),
    HERMES_HOME: path.join(profile, 'hermes'), PYTHONIOENCODING: 'utf-8' };
await fs.mkdir(env.IRIS_HOME);
await fs.mkdir(env.HERMES_HOME);
const executable = path.join(root, 'dist/desktop/Iris.exe');
let child, browser, page, controllerUrl, logs = '', debugPort;
const errors = [];
const result = { native, isolatedProfile: true, checks: [] };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function unusedPort() {
    const server = net.createServer();
    server.listen(0, '127.0.0.1'); await once(server, 'listening');
    const port = server.address().port;
    server.close(); await once(server, 'close');
    return port;
}
async function waitUntil(probe, label, timeout = 30000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
        const value = await probe(); if (value) return value;
        if (child?.exitCode !== null) throw new Error(`Desktop closed early: ${child.exitCode}; ${logs}`);
        await sleep(150);
    }
    throw new Error(`Timed out: ${label}; ${logs}`);
}
// A different-origin fixture checks the browser permission delegation used by Face.
const faceServer = http.createServer(async (req, res) => {
    if (req.url.startsWith('/desktop_host.js')) {
        res.setHeader('Content-Type', 'text/javascript');
        res.end(await fs.readFile(path.join(root, 'face/js/desktop_host.js'))); return;
    }
    res.setHeader('Content-Type', 'text/html');
    res.end('<!doctype html><html><body><h1>Face integration fixture</h1><button id="settings">Configuración</button><script type="module">import { requestDesktopSetup } from "/desktop_host.js";document.getElementById("settings").onclick=()=>requestDesktopSetup();</script></body></html>');
});
faceServer.listen(0, '127.0.0.1'); await once(faceServer, 'listening');
const faceUrl = `http://127.0.0.1:${faceServer.address().port}/face/`;

try {
    if (native) {
        debugPort = await unusedPort();
        env.WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = `--remote-debugging-port=${debugPort} --use-fake-ui-for-media-stream --use-fake-device-for-media-stream`;
        env.WEBVIEW2_USER_DATA_FOLDER = path.join(profile, 'webview');
        child = spawn(executable, [], { cwd: root, env, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
        await waitUntil(async () => {
            try { return (await fetch(`http://127.0.0.1:${debugPort}/json/version`)).ok; } catch { return false; }
        }, 'native WebView2 debug endpoint', 45000);
        browser = await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`);
        page = await waitUntil(async () => browser.contexts()[0]?.pages().find(p => p.url().includes('/desktop/')), 'native controller page');
        controllerUrl = page.url();
        // The second instance must focus the same window and exit, without another controller.
        const second = spawn(executable, [], { cwd: root, env, windowsHide: true });
        const [code] = await once(second, 'exit');
        assert.equal(code, 0);
        assert.equal(browser.contexts()[0].pages().length, 1);
        result.checks.push('native WebView2 startup and single instance');
    } else {
        const python = process.env.IRIS_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
        child = spawn(python, ['-u', '-m', 'core.desktop'], { cwd: root, env, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
        let stdout = '';
        child.stdout.on('data', chunk => { stdout += chunk; });
        await waitUntil(async () => stdout.includes('\n'), 'controller handshake');
        const hello = JSON.parse(stdout.split('\n')[0]);
        assert.equal(hello.type, 'desktop_server'); controllerUrl = hello.url;
        browser = await chromium.launch({ channel: 'msedge', headless: true,
            args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] });
        page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
        await page.goto(controllerUrl);
    }
    child.stderr.on('data', chunk => { logs += chunk; });
    page.on('pageerror', error => errors.push(error.message));
    const origin = new URL(controllerUrl).origin;
    await page.locator('#setup').waitFor({ state: 'visible' });
    assert.match(await page.locator('#message').innerText(), /configura/i);
    await page.screenshot({ path: path.join(output, native ? 'native-startup.png' : 'startup.png') });
    assert.equal(await page.evaluate(() => Boolean(window.__TAURI__)), false);
    const forbidden = await fetch(`${origin}/desktop/start`, { method: 'POST', headers: { Origin: 'https://untrusted.test' } });
    assert.equal(forbidden.status, 403);
    result.checks.push('first-run setup; no native API exposed; cross-origin launch rejected');
    for (const [preset, accent] of [['iris', '#73f7ff'], ['rose', '#ff8ed7'], ['verdant', '#5cffad'], ['sunrise', '#9ed8ff']]) {
        const saved = await fetch(origin + '/api/save-visual-theme', { method: 'POST',
            headers: { Origin: origin, 'Content-Type': 'application/json' }, body: JSON.stringify({ theme: preset }) });
        assert.equal(saved.status, 200);
        await page.waitForFunction(accent => getComputedStyle(document.documentElement).getPropertyValue('--iris-os-accent').trim() === accent, accent);
        await page.evaluate(async () => {
            const style = getComputedStyle(document.documentElement);
            await Promise.all(['--iris-os-bg-image', '--iris-profile-bg-image'].map(key => {
                const url = style.getPropertyValue(key).trim().slice(4, -1).replaceAll('"', '');
                return new Promise((resolve, reject) => {
                    const image = new Image(); image.onload = resolve; image.onerror = () => reject(new Error('Missing theme artwork')); image.src = url;
                });
            }));
        });
        await page.screenshot({ path: path.join(output, (native ? 'native-' : '') + 'theme-' + preset + '.png') });
        await page.screenshot({ path: path.join(output, (native ? 'native-' : '') + 'theme-' + preset + '.jpg'), type: 'jpeg', quality: 55 });
    }
    result.checks.push('four existing Iris OS presets, shared colors and wallpapers');
    await page.locator('#setup').click();
    await page.waitForFunction(() => { try { return document.querySelector('#workspace').contentWindow.location.pathname === '/ui/'; } catch { return false; } });
    const wizard = page.frameLocator('#workspace');
    await wizard.locator('body').waitFor();
    assert.equal(new URL(await page.locator('#workspace').getAttribute('src'), origin).searchParams.get('desktop'), '1');
    result.checks.push('real wizard embedded inside desktop');
    // Only backend responses are fixtures for the ready/error states; production shell JS runs unchanged.
    let state = { phase: 'ready', face_url: faceUrl, message: 'Iris está conectado.', reused: true };
    await page.route('**/desktop/state', route => route.fulfill({ json: state }));
    await page.route('**/desktop/start', route => route.fulfill({ status: 202, json: state }));
    await page.locator('#return').click();
    await page.waitForFunction(() => !document.querySelector('#workspace').hidden && document.querySelector('#workspace').src.includes('/face/'));
    await page.frameLocator('#workspace').locator('#settings').waitFor();
    const face = page.frames().find(frame => frame.url().startsWith(faceUrl));
    assert.ok(face);
    const media = await face.evaluate(async () => {
        const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
        const result = { secure: isSecureContext, kinds: stream.getTracks().map(t => t.kind).sort() };
        stream.getTracks().forEach(track => track.stop()); return result;
    });
    assert.deepEqual(media, { secure: true, kinds: ['audio', 'video'] });
    await face.evaluate(async () => {
        const { publishDesktopTheme } = await import('/desktop_host.js');
        publishDesktopTheme({ accent: '#abc123', accent2: '#123abc', background: 'hacker', reducedMotion: true });
    });
    await page.waitForFunction(() => getComputedStyle(document.documentElement).getPropertyValue('--iris-os-accent').trim() === '#abc123');
    await waitUntil(async () => (await (await fetch(origin + '/desktop/state')).json()).visual?.theme?.accent === '#abc123', 'theme cache saved');
    await page.evaluate(() => window.postMessage({ type: 'iris.desktop.theme', theme: { accent: '#ffffff' } }, location.origin));
    assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--iris-os-accent').trim()), '#abc123');
    assert.equal(await page.getAttribute('html', 'data-reduced-motion'), 'true');
    result.checks.push('live Theme Studio customization, reduced motion, cache persistence and rejection of foreign theme messages');
    await face.locator('#settings').click();
    await page.waitForFunction(() => document.querySelector('#workspace').src.includes('/ui/'));
    result.checks.push('Face iframe delegates microphone/camera and routes configuration internally');
    // Exercise the production wizard's launch handoff, without saving any real account.
    await page.waitForFunction(() => { try { return document.querySelector('#workspace').contentWindow.location.pathname === '/ui/'; } catch { return false; } });
    const setupFrame = page.frames().find(frame => frame.url().includes('/ui/'));
    await setupFrame.waitForFunction(() => typeof launchIris === 'function');
    await setupFrame.evaluate(() => {
        _lastConfigSaveOk = true;
        commitInstallerTheme = async () => {};
        void launchIris();
    });
    await page.waitForFunction(() => document.querySelector('#workspace').src.includes('/face/'));
    result.checks.push('wizard launch returns to Face without a separate browser or wrong TLS port');
    state = { phase: 'error', face_url: '', message: 'El puerto está ocupado. Revisa Configuración.' };
    await page.locator('#home').click();
    await page.locator('#retry').waitFor({ state: 'visible' });
    await page.screenshot({ path: path.join(output, native ? 'native-error.png' : 'error.png') });
    assert.ok(!(await page.locator('#workspace').getAttribute('src')));
    assert.deepEqual(errors, []);
    result.checks.push('readable error, retry action and disposal of the previous surface');
} finally {
    if (browser) await browser.close();
    if (child && child.exitCode === null) {
        if (native) {
            const exit = spawn(executable, ['--iris-exit'], { cwd: root, env, windowsHide: true });
            await once(exit, 'exit');
        } else child.stdin.end();
        await Promise.race([once(child, 'exit'), sleep(10000).then(() => {
            if (child.exitCode === null) { child.kill(); throw new Error('Controller did not close after parent exit'); }
        })]);
    }
    faceServer.close();
    await fs.writeFile(path.join(output, native ? 'native.json' : 'browser.json'), JSON.stringify({ ...result, errors }, null, 2));
    await fs.writeFile(path.join(profile, 'controller.log'), logs);
}
console.log(JSON.stringify(result, null, 2));
