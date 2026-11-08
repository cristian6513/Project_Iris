/** Real WebGL A06 QA: --avatar <VRM> [--playwright-module <module>] [--output <directory>]. */
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const option = name => { const i = process.argv.indexOf(name); return i >= 0 ? process.argv[i + 1] : null; };
const avatar = option('--avatar');
if (!avatar || !fs.statSync(avatar).isFile()) throw Error('Pass --avatar with a local VRM file');
const { chromium } = createRequire(import.meta.url)(option('--playwright-module') || 'playwright');
const output = path.resolve(root, option('--output') || '.avatar-lifecycle-qa.tmp'); fs.mkdirSync(output, { recursive: true });
const server = http.createServer((req, res) => {
    const p = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    let file;
    if (p === '/qa-avatar.vrm') file = avatar;
    else if (p.startsWith('/vendor/')) {
        const vendor = path.join(root, 'companion', 'node_modules'), candidate = path.resolve(vendor, p.slice(8));
        if (candidate.startsWith(vendor + path.sep)) file = candidate;
    } else if (/^\/face\/(js\/[^/]+\.js|animations\/[^/]+)$/.test(p) || p === '/tests/fixtures/avatar_lifecycle.html') file = path.resolve(root, '.' + p);
    if (!file) { res.writeHead(404); res.end(); return; }
    fs.readFile(file, (error, data) => {
        if (error) { res.writeHead(404); res.end(); return; }
        res.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json' }[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
        res.end(data);
    });
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
let browser;
const report = { avatar: path.basename(avatar), status: 'failed', samples: [], errors: [], warnings: [] };
try {
    browser = await chromium.launch({ headless: true, args: ['--autoplay-policy=no-user-gesture-required'], ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
    const page = await browser.newPage({ viewport: { width: 900, height: 900 } });
    page.on('pageerror', error => report.errors.push(error.message));
    page.on('console', message => { if (message.type() === 'warning') report.warnings.push(message.text()); });
    const origin = 'http://127.0.0.1:' + server.address().port;
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') || route.request().url().startsWith('blob:') ? route.continue() : route.abort());
    await page.goto(origin + '/tests/fixtures/avatar_lifecycle.html');
    await page.waitForFunction(() => window.lifecycleQA, {}, { timeout: 60000 });
    const cdp = await page.context().newCDPSession(page); await cdp.send('Performance.enable');
    // Always a VRM (2026-10-28): warm each model once, then compare ten complete
    // swaps between the given VRM and IRIS' own.
    const models = { qa: '/qa-avatar.vrm', iris: '/face/animations/iris.vrm' };
    for (let i = 0; i < 22; i++) {
        const mode = ['qa', 'iris'][i % 2];
        const sample = await page.evaluate(async ([mode, url]) => lifecycleQA.swap(mode, url), [mode, models[mode]]);
        await cdp.send('HeapProfiler.collectGarbage');
        const { metrics } = await cdp.send('Performance.getMetrics');
        sample.heapBytes = metrics.find(m => m.name === 'JSHeapUsedSize').value;
        sample.cycle = Math.floor(i / 2); report.samples.push(sample);
        console.log(JSON.stringify(sample));
    }
    for (const mode of Object.keys(models)) {
        const samples = report.samples.filter(s => s.mode === mode && s.cycle > 0);
        for (const key of ['geometries', 'textures', 'programs']) {
            assert.equal(new Set(samples.map(s => s[key])).size, 1, `${mode}: growing GPU ${key}`);
        }
        const early = samples.slice(0, 3).reduce((n, s) => n + s.heapBytes, 0) / 3;
        const late = samples.slice(-3).reduce((n, s) => n + s.heapBytes, 0) / 3;
        // Allows browser bookkeeping/JIT variance, not another retained full VRM per swap.
        assert.ok(late - early < Math.max(4 * 1024 * 1024, early * 0.15), `${mode}: retained heap growth ${late - early}`);
    }
    assert.deepEqual(report.errors, []);
    await page.screenshot({ path: path.join(output, 'final-vrm.png') });
    report.status = 'passed';
} finally {
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    await browser?.close(); await new Promise(resolve => server.close(resolve));
}
