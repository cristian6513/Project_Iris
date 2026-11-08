/** A07 real VRM QA: --avatar <path> --playwright-module <module> [--output <dir>]. */
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const option = name => { const i = process.argv.indexOf(name); return i >= 0 ? process.argv[i + 1] : null; };
const avatar = option('--avatar'); if (!avatar || !fs.statSync(avatar).isFile()) throw Error('Pass --avatar');
const { chromium } = createRequire(import.meta.url)(option('--playwright-module') || 'playwright');
const output = path.resolve(root, option('--output') || 'artifacts/a07-validation/webgl'); fs.mkdirSync(output, { recursive: true });
const base = JSON.parse(fs.readFileSync(path.join(root, 'face/animations/manifest.json'), 'utf8'));
let revision = 1, offline = false, snapshotReads = 0, authenticatedAssets = 0;
const aliases = new Map(), bytes = new Map();
function snapshot() {
    const manifest = structuredClone(base); manifest.clips.wave = revision === 1 ? base.clips.wave : base.clips.nod;
    const resources = Object.fromEntries(Object.entries(manifest.clips).map(([name, filename]) => {
        if (!bytes.has(filename)) bytes.set(filename, fs.readFileSync(path.join(root, 'face/animations', filename)));
        const digest = crypto.createHash('sha256').update(bytes.get(filename)).digest('hex');
        const alias = name + '.' + digest + '.' + filename.split('.').pop(); aliases.set(alias, filename);
        manifest.clips[name] = alias;
        return [name, { filename: alias, format: filename.split('.').pop(), revision: digest, url: '/face/animations/' + alias + '?v=' + digest }];
    }));
    return { revision: 'qa-' + revision, manifest, resources };
}
const server = http.createServer((req, res) => {
    const p = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    if (p === '/api/animation-manifest') {
        snapshotReads++;
        if (offline) { res.writeHead(503); res.end(); return; }
        const value = snapshot(), etag = '"' + value.revision + '"';
        if (req.headers['if-none-match'] === etag) { res.writeHead(304, { ETag: etag }); res.end(); return; }
        res.writeHead(200, { 'Content-Type': 'application/json', ETag: etag }); res.end(JSON.stringify(value)); return;
    }
    let file;
    if (p === '/qa-avatar.vrm') file = avatar;
    else if (p.startsWith('/vendor/')) {
        const vendor = path.join(root, 'companion/node_modules'), candidate = path.resolve(vendor, p.slice(8));
        if (candidate.startsWith(vendor + path.sep)) file = candidate;
    } else if (/^\/face\/(js\/[^/]+\.js|animations\/[^/]+)$/.test(p) || p === '/tests/fixtures/animation_hot_reload.html') {
        const alias = aliases.get(p.split('/').pop());
        if (alias && req.headers['x-iris-companion-token'] === 'qa-only') authenticatedAssets++;
        file = alias ? path.join(root, 'face/animations', alias) : path.resolve(root, '.' + p);
    }
    if (!file) { res.writeHead(404); res.end(); return; }
    fs.readFile(file, (error, data) => {
        if (error) { res.writeHead(404); res.end(); return; }
        res.writeHead(200, { 'Content-Type': { '.js': 'text/javascript', '.html': 'text/html', '.json': 'application/json' }[path.extname(file)] || 'application/octet-stream' }); res.end(data);
    });
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
let browser;
const report = { status: 'failed', avatar: path.basename(avatar), surfaces: [], errors: [] };
try {
    browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
    const origin = 'http://127.0.0.1:' + server.address().port;
    for (const native of [false, true]) {
        revision = 1; offline = false;
        const page = await browser.newPage({ viewport: { width: 900, height: 900 } });
        page.on('pageerror', error => report.errors.push(error.message));
        await page.route('**/*', route => route.request().url().startsWith(origin + '/') || route.request().url().startsWith('blob:') ? route.continue() : route.abort());
        await page.goto(origin + '/tests/fixtures/animation_hot_reload.html' + (native ? '?native=1' : ''));
        await page.waitForFunction(() => window.reloadQA, {}, { timeout: 60000 });
        const initial = await page.evaluate(() => {
            const q = reloadQA; q.play('wave'); q.advance(0.8); q.capture();
            return { duration: q.idle._vrmaPlayer.clips.get('wave').duration, targetDuration: q.nodDuration, height: q.manager.avatar.group.position.y };
        });
        await page.screenshot({ path: path.join(output, (native ? 'companion' : 'face') + '-before.png') });
        revision = 2; // Simulates a commit by a separate process; no WebSocket event.
        try {
            await page.waitForFunction(() => reloadQA.idle._manifestRevision === 'qa-2', {}, { timeout: 12000 });
        } catch (error) {
            report.diagnostics = await page.evaluate(() => ({ snapshot: reloadQA.library.snapshot?.revision,
                applied: reloadQA.idle._manifestRevision, active: reloadQA.library.active, errors: reloadQA.errors,
                loaded: [...reloadQA.idle._vrmaPlayer._revisions], pending: reloadQA.idle._manifestRequest?.revision }));
            console.log(JSON.stringify(report.diagnostics, null, 2));
            throw error;
        }
        const changed = await page.evaluate(() => reloadQA.continuity());
        assert.ok(changed.position < 0.0001 && changed.angle < 0.001, JSON.stringify(changed));
        assert.equal(changed.rootHeight, initial.height); assert.equal(changed.speed, 1);
        assert.equal(changed.duration, initial.targetDuration); assert.notEqual(changed.duration, initial.duration);
        const progressed = await page.evaluate(() => {
            reloadQA.advance(0.75);
            document.querySelector('#status').textContent = 'A07 · misma pose wave · nueva revisión';
            return reloadQA.continuity();
        });
        assert.ok(progressed.angle > 0.05, 'The replacement must visibly change the sampled pose');
        assert.equal(progressed.rootHeight, initial.height);
        await page.screenshot({ path: path.join(output, (native ? 'companion' : 'face') + '-after.png') });
        offline = true;
        const failure = await page.evaluate(async () => {
            const q = reloadQA, action = q.idle._vrmaPlayer.current; await q.library.refresh();
            return { kept: q.idle._vrmaPlayer.current === action, errors: q.errors.length, outstandingBlobs: q.owned.size, revoked: q.revoked.length };
        });
        assert.equal(failure.kept, true); assert.equal(failure.errors, 1); assert.equal(failure.outstandingBlobs, 0);
        offline = false;
        await page.evaluate(async () => { await reloadQA.library.refresh(); reloadQA.advance(2); reloadQA.library.dispose(); });
        report.surfaces.push({ surface: native ? 'companion-bridge' : 'face', initial, changed, progressed, failure });
        await page.close();
    }
    assert.ok(authenticatedAssets > 0); assert.deepEqual(report.errors, []);
    Object.assign(report, { status: 'passed', snapshotReads, authenticatedAssets });
    console.log(JSON.stringify(report, null, 2));
} finally {
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    await browser?.close(); await new Promise(resolve => server.close(resolve));
}
