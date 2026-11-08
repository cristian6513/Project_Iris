/** Isolated browser QA with a generated MediaStream; never opens user devices. */
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const option = n => { const i = process.argv.indexOf(n); return i < 0 ? '' : process.argv[i + 1]; };
const { chromium } = createRequire(import.meta.url)(option('--playwright-module') || 'playwright');
const output = path.join(root, 'artifacts/camera-lifecycle'); fs.mkdirSync(output, { recursive: true });
let vision = { enabled: false, paused_until: 0, state: 'off', permissions: { camera: { verdict: 'allow' } } };
const assets = [], writes = [];
const sourceHtml = fs.readFileSync(path.join(root, 'face/index.html'), 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
const setup = `<script type="importmap">{"imports":{"three":"/vendor/three.module.js"}}</script>
<script type="module">
import { SidePanel } from '/face/js/panel.js';
import { HermesActivityCanvas } from '/face/js/hermes_activity.js';
import { CameraMocap } from '/face/js/camera_mocap.js';
document.querySelectorAll('#loading-screen, .loading-screen').forEach(e=>e.remove());
const panel=new SidePanel(), mocap=new CameraMocap();
const qa=window.cameraQA={panel,mocap,starts:0,stops:0,kills:0,ready:false};
const canvas=document.createElement('canvas');canvas.width=640;canvas.height=480;
const ctx=canvas.getContext('2d');let frame=0;
function draw(){ctx.fillStyle=frame++%2?'#193d52':'#1c4259';ctx.fillRect(0,0,640,480);ctx.fillStyle='#79eed5';ctx.fillRect(frame%540,210,100,60);ctx.font='28px sans-serif';ctx.fillText('Cámara de prueba · señal local',75,100);}
draw();setInterval(draw,100);
navigator.mediaDevices.getUserMedia=async opts=>{qa.starts++;return canvas.captureStream(15);};
panel.requestCameraLease=async()=>true;panel.releaseCameraLease=()=>qa.stops++;
const app=qa.app=new HermesActivityCanvas({acquireCamera:(...args)=>panel.acquireCamera(...args),
 releaseCamera:key=>panel.releaseCamera(key),getCameraStream:()=>panel.camStream,
 onVisionSettingsChanged:state=>{mocap.configureVision(state);if(state.enabled)panel.acquireCamera('vision-assistant');else panel.releaseCamera('vision-assistant');}});
panel.onCameraKillSwitch=()=>{qa.kills++;app.applyVisionAssistantState({enabled:false});};
window.addEventListener('IRIS:camera-started',async e=>{app.setSharedCameraStream(e.detail.stream);try{await mocap.start(e.detail.videoEl);qa.ready=mocap.running;}catch(e){qa.error=String(e);}});
window.addEventListener('IRIS:camera-stopped',()=>{mocap.stop();qa.ready=false;app.setSharedCameraStream(null);});
app.open();app._setTab('camera');
</script>`;
const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost'), p = decodeURIComponent(url.pathname);
    if (p === '/face/index.html') { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end(sourceHtml.replace('</body>', setup + '</body>')); return; }
    if (p.startsWith('/api/')) {
        if (req.method === 'POST') {
            let body = ''; for await (const b of req) body += b;
            writes.push(p);
            if (p === '/api/vision-assistant/settings') {
                const update = JSON.parse(body); vision = { ...vision, ...update, state: update.enabled ? 'observing' : 'off' };
            }
        }
        const data = p === '/api/vision-assistant/settings' ? { ...vision, camera_confirmation_granted: req.method === 'POST' && vision.enabled }
            : { success: true, events: [], jobs: [], runs: [], outputs: [], lines: [], items: [] };
        res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data)); return;
    }
    const file = /^\/vendor\/three\.(core|module)\.js$/.test(p)
        ? path.join(root, 'companion/node_modules/three/build', path.basename(p)) : path.resolve(root, '.' + p);
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
    if (p.includes('/mediapipe/')) assets.push(p);
    const mime = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.wasm': 'application/wasm', '.png': 'image/png' };
    res.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream' }); fs.createReadStream(file).pipe(res);
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
let browser;
try {
    browser = await chromium.launch({ headless: true, args: ['--mute-audio'], ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
    const page = await browser.newPage({ viewport: { width: 1440, height: 980 }, permissions: ['camera'] });
    const errors = [], origin = 'http://127.0.0.1:' + server.address().port;
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', r => r.request().url().startsWith(origin + '/') || r.request().url().startsWith('blob:') ? r.continue() : r.abort());
    await page.goto(origin + '/face/index.html');
    await page.waitForFunction(() => window.cameraQA?.app);
    assert.deepEqual(errors, []);
    await page.screenshot({path:path.join(output,'startup.png')});
    const started = Date.now();
    await page.getByRole('button', { name: 'Abrir cámara', exact: true }).click();
    const visibleVideo = () => page.waitForFunction(() => { const v = document.querySelector('.os-camera-video'); return v?.checkVisibility() && !v.paused && v.readyState >= 2 && v.videoWidth > 0; });
    await visibleVideo().catch(async error => {
        console.log('Preview failure', await page.evaluate(() => ({ errors: cameraQA.error, starts: cameraQA.starts, stops: cameraQA.stops, consumers: [...cameraQA.panel._cameraConsumers], active: cameraQA.panel.camActive, starting: cameraQA.panel.camStarting, stream: cameraQA.panel.camStream?.id, preview: cameraQA.app._cameraStream?.id, messages: cameraQA.panel.messages, status: document.querySelector('.os-camera-status')?.textContent, video: [...document.querySelectorAll('video')].map(v => ({ class: v.className, src: v.srcObject?.id, paused: v.paused, ready: v.readyState, width: v.videoWidth, visible: v.checkVisibility() })) })), errors);
        await page.screenshot({path:path.join(output,'failed-preview.png')});
        throw error;
    }); const firstPreviewMs = Date.now() - started;
    await page.evaluate(() => { window.originalStream = cameraQA.panel.camStream; });
    await page.waitForFunction(() => cameraQA.ready || cameraQA.error, null, { timeout: 60000 });
    assert.equal(await page.evaluate(() => cameraQA.error || ''), '');
    const cycles = [];
    for (const action of ['other-section', 'desktop', 'close-desktop']) {
        const started = Date.now();
        await page.evaluate(action => {
            const app = cameraQA.app;
            if (action === 'other-section') { app._setTab('settings'); app._setTab('camera'); }
            if (action === 'desktop') { app._setDesktopMode('home'); app._setTab('camera'); }
            if (action === 'close-desktop') { app.close(); app.open(); }
        }, action);
        await visibleVideo();
        const state = await page.evaluate(() => ({ same: cameraQA.panel.camStream === originalStream, starts: cameraQA.starts, kills: cameraQA.kills }));
        assert.deepEqual(state, { same: true, starts: 1, kills: 0 });
        cycles.push({ action, restoredMs: Date.now() - started });
    }
    await page.locator('[data-vision-field="enabled"]').check();
    await page.waitForFunction(() => cameraQA.app._visionSettings.enabled && !cameraQA.app._visionLoading);
    await page.evaluate(() => { cameraQA.app.close(); document.querySelector('#btn-cam').click(); });
    assert.equal(await page.evaluate(() => cameraQA.app._visionSettings.enabled), true);
    assert.equal(await page.evaluate(() => cameraQA.starts), 1);
    assert.equal(await page.locator('#mirror-toggle').count(), 0);
    await page.evaluate(() => { cameraQA.panel.close(); cameraQA.app.open(); cameraQA.app._setTab('camera'); });
    await visibleVideo();
    await page.screenshot({ path: path.join(output, 'camera-preview.png') });
    assert.ok(assets.some(p => p.endsWith('face_landmarker.task')), 'analysis must load the local model');
    assert.ok(assets.some(p => p.endsWith('.wasm')), 'analysis must load local WASM');
    await page.evaluate(() => cameraQA.panel.toggleCamera());
    assert.equal(await page.evaluate(() => cameraQA.panel.camActive), false);
    assert.equal(await page.evaluate(() => cameraQA.app._visionSettings.enabled), false);
    assert.deepEqual(errors, []);
    const report = { status: 'passed', device: 'generated canvas MediaStream; no physical camera', firstPreviewMs, cycles, starts: 1, analysis: 'real local MediaPipe', assets, writes, errors };
    fs.writeFileSync(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2)); console.log(JSON.stringify(report, null, 2));
} finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
