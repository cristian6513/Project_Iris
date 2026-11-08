/** Offline browser QA for an explicitly staged candidate, never the current preview. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { verifyLocalVision } from './verify_local_vision.mjs';
import { verifyLocalDesktop } from './verify_local_desktop.mjs';

export async function verifyLocalWeb({ playwright, origin, output }) {
    assert.equal(new URL(origin).hostname, '127.0.0.1');
    await fs.mkdir(output, { recursive: true });
    const browser = await playwright.chromium.launch({ channel: 'msedge', headless: true,
        args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const evidence = { origin, externalRequests: [], failedResources: [], pageErrors: [], requests: [], console: [] };
    await context.route('**/*', async route => {
        const url = route.request().url();
        if (url.startsWith('data:') || url.startsWith('blob:') || new URL(url).origin === origin) return route.continue();
        evidence.externalRequests.push(url);
        await route.abort('blockedbyclient');
    });
    context.on('request', request => evidence.requests.push({ url: request.url(), type: request.resourceType() }));
    context.on('requestfailed', request => evidence.failedResources.push({ url: request.url(), failure: request.failure()?.errorText }));
    context.on('response', response => {
        if (response.status() >= 400) evidence.failedResources.push({ url: response.url(), status: response.status() });
    });
    context.on('page', page => {
        page.on('pageerror', error => evidence.pageErrors.push(error.message));
        page.on('console', message => evidence.console.push({ type: message.type(), text: message.text() }));
    });
    let completed = false;
    let activePage;
    try {
        const face = await context.newPage();
        activePage = face;
        await face.goto(origin + '/face/', { waitUntil: 'domcontentloaded' });
        await face.waitForFunction(() => window.iris?.panel && window.iris?.idle?._vrmaPlayer?.clips?.size >= 18, null, { timeout: 120000 });
        await face.waitForFunction(() => window.iris?.manualVoice
            && document.querySelector('#loading-screen')?.classList.contains('hidden'), null, { timeout: 30000 });
        if (!await face.locator('#side-panel').evaluate(el => el.classList.contains('open'))) {
            await face.locator('#panel-toggle').click();
        }
        await face.locator('#panel-lan .panel-section-header').click();
        await face.waitForFunction(() => document.querySelector('#lan-qr img')?.src.startsWith('data:image/'), null, { timeout: 30000 });
        evidence.face = await face.evaluate(async () => {
            await document.fonts.ready;
            const app = window.iris;
            const before = app.renderer.info.render.frame;
            const played = await app.idle.playClipExternal('wave');
            // Software WebGL can take more than 600 ms per frame with a full VRM.
            // Wait for actual progress; the assertion below still fails if rendering stops.
            const deadline = performance.now() + 30000;
            while (app.renderer.info.render.frame <= before && performance.now() < deadline) {
                await new Promise(resolve => setTimeout(resolve, 100));
            }
            return { avatarUrl: app.avatarManager.vrmUrl, clips: [...app.idle._vrmaPlayer.clips.keys()],
                played, frameAdvanced: app.renderer.info.render.frame > before,
                fonts: { outfit: document.fonts.check('400 16px Outfit'), syne: document.fonts.check('700 16px Syne') },
                fontFaces: [...document.fonts].map(font => ({ family: font.family, status: font.status })),
                qr: !!document.querySelector('#lan-qr img'), shaderPrograms: app.renderer.info.programs.length };
        });
        assert.equal(evidence.face.avatarUrl, '/qa/avatar.vrm');
        assert.ok(evidence.face.frameAdvanced);
        assert.ok(evidence.face.fonts.outfit && evidence.face.fonts.syne);
        assert.ok(evidence.face.clips.includes('wave'));
        console.log('Offline Face: VRM, 18 poses, fonts and QR ready.');
        await face.locator('#panel-toggle').click();
        await face.screenshot({ path: path.join(output, 'face-cyber.png') });
        await face.locator('.room-btn[data-room="sakura"]').click();
        await face.waitForFunction(() => window.iris?.config.room_theme === 'sakura'
            && document.querySelector('.room-btn.active')?.dataset.room === 'sakura');
        await face.waitForTimeout(900);
        await face.screenshot({ path: path.join(output, 'face-sakura.png') });
        evidence.vision = await verifyLocalVision(face, origin);
        console.log('Offline Face Vision: real inference and cached restart ready.');
        evidence.desktop = await verifyLocalDesktop(face, output);
        console.log('Offline IRIS OS desktop: dock usable, «Aplicaciones» and «Chat» open.');

        const companion = await context.newPage();
        activePage = companion;
        await companion.goto(origin + '/face/companion/', { waitUntil: 'domcontentloaded' });
        await companion.waitForFunction(() => window.irisCompanion?.ttsAudioPlayer
            && window.irisCompanion?.loading?.classList.contains('is-ready')
            && window.irisCompanion?.idle?._vrmaPlayer?.clips?.size >= 18, null, { timeout: 120000 });
        evidence.companion = await companion.evaluate(() => ({ native: window.irisCompanion.bridge.native,
            avatarUrl: window.irisCompanion.avatarManager.vrmUrl,
            clips: [...window.irisCompanion.idle._vrmaPlayer.clips.keys()] }));
        assert.equal(evidence.companion.native, false);
        assert.match(evidence.companion.avatarUrl, /\/qa\/avatar\.vrm(\?|$)|^blob:/);
        await companion.screenshot({ path: path.join(output, 'companion-browser.png') });
        evidence.companionVision = await verifyLocalVision(companion, origin, { companion: true });
        console.log('Offline browser Companion: avatar, poses and real Vision inference ready.');

        const wizard = await context.newPage();
        activePage = wizard;
        await wizard.goto(origin + '/ui/', { waitUntil: 'domcontentloaded' });
        await wizard.waitForFunction(() => typeof window.nextStep === 'function' && document.querySelector('#assistant-name')?.value === 'IRIS', null, { timeout: 30000 });
        await wizard.waitForFunction(() => document.querySelector('[data-identity-preview-bound="true"]') !== null, null, { timeout: 30000 });
        evidence.wizard = await wizard.evaluate(async () => {
            await document.fonts.load('400 16px "JetBrains Mono"');
            await document.fonts.ready;
            return { classicFunctions: ['nextStep', 'prevStep', 'applyInstallerTheme'].every(key => typeof window[key] === 'function'),
                fonts: { syne: document.fonts.check('600 16px Syne'), mono: document.fonts.check('400 16px "JetBrains Mono"') },
                fontFaces: [...document.fonts].map(font => ({ family: font.family, status: font.status })) };
        });
        assert.ok(evidence.wizard.classicFunctions);
        assert.ok(evidence.wizard.fonts.syne && evidence.wizard.fonts.mono);
        await wizard.screenshot({ path: path.join(output, 'wizard-1440.png') });
        await wizard.setViewportSize({ width: 860, height: 720 });
        await wizard.screenshot({ path: path.join(output, 'wizard-compact.png') });
        evidence.assets = await (await fetch(origin + '/qa/hashes')).json();
        assert.ok(evidence.assets.unchanged);
        assert.equal(evidence.externalRequests.length, 0, 'External bootstrap requests');
        assert.equal(evidence.failedResources.length, 0, 'Missing/failed local resource requests');
        assert.equal(evidence.pageErrors.length, 0, 'Uncaught browser errors');
        completed = true;
        return evidence;
    } finally {
        evidence.completed = completed;
        if (!completed && activePage && !activePage.isClosed()) {
            evidence.diagnostics = await activePage.evaluate(() => ({ url: location.href,
                qr: document.querySelector('#lan-qr')?.textContent,
                loading: document.querySelector('#loading-screen')?.textContent,
                faceClips: window.iris?.idle?._vrmaPlayer?.clips?.size,
                companionClips: window.irisCompanion?.idle?._vrmaPlayer?.clips?.size })).catch(() => null);
            await activePage.screenshot({ path: path.join(output, 'failure.png') }).catch(() => {});
        }
        await fs.writeFile(path.join(output, 'browser-evidence.json'), JSON.stringify(evidence, null, 2) + '\n');
        await browser.close();
    }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    const [origin, output, runtime] = process.argv.slice(2);
    assert.ok(origin && output && runtime, 'Usage: node verify_local_web.mjs ORIGIN OUTPUT PLAYWRIGHT_MODULE');
    const playwright = createRequire(import.meta.url)(runtime);
    const evidence = await verifyLocalWeb({ playwright, origin, output });
    console.log(JSON.stringify({ completed: evidence.completed, face: evidence.face, vision: evidence.vision,
        desktop: evidence.desktop, companion: evidence.companion, companionVision: evidence.companionVision,
        wizard: evidence.wizard, assets: evidence.assets, requests: evidence.requests.length }, null, 2));
}
