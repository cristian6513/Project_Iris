/** Browser verification of the compiled Companion against an isolated Core fixture. */
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const option = name => { const i = process.argv.indexOf(name); return i < 0 ? '' : process.argv[i + 1]; };
const { chromium } = createRequire(import.meta.url)(option('--playwright-module') || 'playwright');
const vrm = path.resolve(option('--vrm'));
const vrm2 = path.resolve(option('--vrm2') || vrm);
assert.ok(fs.existsSync(vrm) && fs.existsSync(vrm2), 'Pass --vrm and optionally --vrm2');
const dist = path.join(root, 'companion/dist');
const output = path.resolve(root, option('--output') || 'artifacts/companion-avatar-qa');
fs.mkdirSync(output, { recursive: true });
let config = { avatar_vrm_path: '', assistant_name: 'IRIS', visual_theme: 'iris' };
let selectedFile = vrm;
const sockets = new Set(), assetRequests = [];
const frame = value => {
    const data = Buffer.from(JSON.stringify(value));
    const header = Buffer.alloc(data.length < 126 ? 2 : 4);
    header[0] = 0x81; header[1] = data.length < 126 ? data.length : 126;
    if (data.length >= 126) header.writeUInt16BE(data.length, 2);
    return Buffer.concat([header, data]);
};
const send = (type, payload = {}) => { for (const socket of sockets) socket.write(frame({ type, payload })); };
const broadcast = () => send('avatar_config_changed');
const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost'), p = url.pathname;
    if (p.startsWith('/api/')) {
        const data = p === '/api/config' ? config : {};
        res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data)); return;
    }
    if (p === '/avatars/current.vrm') {
        assetRequests.push(url.search);
        res.writeHead(200, { 'Content-Type': 'model/gltf-binary', 'Cache-Control': 'no-store' });
        fs.createReadStream(selectedFile).pipe(res); return;
    }
    const file = path.resolve(dist, '.' + p);
    if (!file.startsWith(dist + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
    const type = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.wasm': 'application/wasm' }[path.extname(file)] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': type }); fs.createReadStream(file).pipe(res);
});
server.on('upgrade', (req, socket) => {
    const accept = crypto.createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
    socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n');
    sockets.add(socket); socket.on('close', () => sockets.delete(socket)); socket.on('error', () => sockets.delete(socket)); socket.on('data', () => {});
    socket.write(frame({ type: 'hello', payload: {} }));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
let browser;
try {
    browser = await chromium.launch({ headless: true, args: ['--mute-audio'], ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
    const page = await browser.newPage({ viewport: { width: 420, height: 620 } });
    const errors = [], origin = 'http://127.0.0.1:' + server.address().port;
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin + '/face/companion/index.html');
    await page.waitForFunction(() => window.irisCompanion?.ttsAudioPlayer && !irisCompanion._avatarSync.pending);
    // No model of the person's own: IRIS, from the Companion's own bundle.
    assert.match(await page.evaluate(() => irisCompanion.avatarManager.vrmUrl), /\/animations\/iris\.vrm(\?|$)/);
    await page.waitForFunction(() => irisCompanion.ws.connected);
    config = { ...config, avatar_vrm_path: '/avatars/current.vrm', avatar_revision: 'first' };
    broadcast();
    await page.waitForFunction(() => irisCompanion.avatarManager?.avatar?.vrm && !irisCompanion._avatarSync.pending, null, { timeout: 60000 });
    await page.waitForFunction(() => irisCompanion.idle?._vrmaPlayer?.hasAnimation('talk'));
    const waitClip = name => page.waitForFunction(name =>
        irisCompanion.idle?._vrmaPlayer?.currentName === name, name);
    send('hermes_voice_state', { state: 'recording', revision: 1 });
    await waitClip('listen');
    await page.waitForFunction(() => irisCompanion.idle._vrmaPlayer.current.paused);
    const heldTime = await page.evaluate(() => irisCompanion.idle._vrmaPlayer.current.time);
    await page.waitForTimeout(400);
    assert.equal(await page.evaluate(() => irisCompanion.idle._vrmaPlayer.current.time), heldTime);
    assert.equal(await page.evaluate(() => irisCompanion.avatarManager.avatar.animationOwnsBlink), false);
    assert.equal(await page.evaluate(() => irisCompanion.avatarManager.avatar.animationOwnsRootMotion), true);
    assert.equal(await page.evaluate(() => irisCompanion.avatarManager.avatar.poseFacialProfile?.eyes?.gaze), 'natural');
    const supportsBlink = await page.evaluate(() => Boolean(
        irisCompanion.avatarManager.avatar.vrm.expressionManager.getExpression('blink')?.binds.length));
    if (supportsBlink) {
        await page.evaluate(() => { irisCompanion.avatarManager.avatar._naturalBlink.wait = 0.05; });
        await page.waitForFunction(() => {
            const avatar = irisCompanion.avatarManager.avatar;
            return avatar.vrm.expressionManager.getValue('blink') > 0.7 && avatar.vrm.lookAt.autoUpdate;
        });
    }
    await page.screenshot({ path: path.join(output, 'listening.png') });
    send('hermes_voice_state', { state: 'transcribing', revision: 2 });
    await waitClip('think');
    // Core releases its manual capture after transcription. Without that final
    // state the fixture's rail redraw restores "transcribing" after speech ends.
    send('hermes_voice_state', { state: 'off', revision: 3 });
    send('status', { status: 'thinking' });
    send('animation', { name: 'confirm' });
    await waitClip('confirm');
    send('speech_start', { text: 'Hola', emotion: 'neutral' });
    await waitClip('talk');
    send('speech_end');
    await waitClip('idle');
    send('hermes_wake_state', { state: 'listening', active: true });
    await page.waitForTimeout(200);
    assert.equal(await page.evaluate(() => irisCompanion.animationDirector.activity.state), 'idle');
    send('hermes_wake_state', { state: 'detected', active: true });
    await waitClip('listen');
    const first = await page.evaluate(() => {
        window.firstManager = irisCompanion.avatarManager;
        window.firstGroup = irisCompanion.avatarManager.avatar.group;
        return irisCompanion.avatarManager.avatar.group.uuid;
    });
    broadcast(); await page.waitForTimeout(200);
    await page.waitForFunction(() => !irisCompanion._avatarSync.pending);
    assert.equal(await page.evaluate(() => irisCompanion.avatarManager === window.firstManager), true);
    assert.equal(assetRequests.length, 1);
    // Exercise the blob URL path used by the native authenticated bridge.
    await page.evaluate(() => {
        const bridge = irisCompanion.bridge;
        bridge.native = true;
        bridge.coreAssetUrl = async path => URL.createObjectURL(await (await bridge.apiFetch(path, { cache: 'no-store' })).blob());
        bridge.releaseCoreAssetUrl = url => URL.revokeObjectURL(url);
    });
    selectedFile = vrm2; config.avatar_revision = 'replacement'; broadcast();
    await page.waitForFunction(() => irisCompanion._appliedAvatarKey.includes('replacement') && !irisCompanion._avatarSync.pending, null, { timeout: 60000 });
    await waitClip('listen');
    send('hermes_wake_state', { state: 'off', active: false });
    await waitClip('idle');
    const after = await page.evaluate(() => ({ uuid: irisCompanion.avatarManager.avatar.group.uuid,
        idle: irisCompanion.idle.avatar === irisCompanion.avatarManager,
        speech: irisCompanion.speechAnimator.avatarManager === irisCompanion.avatarManager,
        lip: irisCompanion.lipSync.avatar === irisCompanion.avatarManager,
        oldRemoved: window.firstGroup.parent === null && window.firstManager.avatar === null }));
    assert.notEqual(after.uuid, first); assert.ok(after.idle && after.speech && after.lip && after.oldRemoved);
    await page.screenshot({ path: path.join(output, 'vrm-replacement.png') });
    config = { ...config, avatar_vrm_path: '', avatar_revision: '' }; broadcast(); // «Volver a IRIS»
    await page.waitForFunction(() => /\/animations\/iris\.vrm(\?|$)/.test(irisCompanion.avatarManager?.vrmUrl || '')
        && irisCompanion.avatarManager.avatar?.vrm && !irisCompanion._avatarSync.pending, null, { timeout: 60000 });
    await page.screenshot({ path: path.join(output, 'iris.png') });
    config = { ...config, avatar_vrm_path: '/avatars/missing.vrm' }; broadcast();
    await page.waitForFunction(() => !!irisCompanion._avatarSyncError && !irisCompanion._avatarSync.pending);
    assert.match(await page.evaluate(() => irisCompanion.avatarManager.vrmUrl), /\/animations\/iris\.vrm(\?|$)/);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ status: 'passed', checks: ['manual listening held beyond duration', 'body-only blink ownership', 'thinking', 'response gesture then speech loop', 'return to base', 'wake activation', 'activity survives avatar replacement', 'live WebSocket event', 'real VRM load', 'no duplicate reload', 'same-name replacement', 'native blob URL path', 'animation references', 'back to IRIS', 'failed load preserves avatar'], pageErrors: errors, assetRequests, output }, null, 2));
} finally {
    if (browser) await browser.close();
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve));
}
