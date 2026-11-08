/** A08.2 real backend/WebGL smoke: --avatar <vrm> --playwright-module <module>. */
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const option = name => { const i = process.argv.indexOf(name); return i < 0 ? null : process.argv[i + 1]; };
const avatar = option('--avatar');
if (!avatar || !fs.statSync(avatar).isFile()) throw Error('Pass --avatar');
const { chromium } = createRequire(import.meta.url)(option('--playwright-module') || 'playwright');
const output = path.resolve(root, option('--output') || 'artifacts/a08-2-validation/webgl');
if (!output.startsWith(path.join(root, 'artifacts') + path.sep)) throw Error('Output must be under artifacts');
fs.mkdirSync(output, { recursive: true });
const work = fs.mkdtempSync(path.join(output, 'isolated-'));
const python = option('--python') || path.join(root, '.venv/Scripts/python.exe');
const child = spawn(python, ['-E', '-s', '-B', '-X', 'utf8', '-u', path.join(root, 'scripts/verify_animation_profile_server.py'),
    '--work-root', work, '--avatar', path.resolve(avatar)], { cwd: root, windowsHide: true });
let serverLog = '', browser;
child.stderr.on('data', data => { serverLog += data; });
const report = { status: 'failed', surfaces: [], errors: [] };
try {
    const origin = await new Promise((resolve, reject) => {
        let stdout = '';
        const deadline = setTimeout(() => reject(Error('Fixture startup timed out')), 30000);
        child.once('error', error => { clearTimeout(deadline); reject(error); });
        child.stdout.on('data', data => {
            stdout += data; serverLog += data;
            for (const line of stdout.split(/\r?\n/)) {
                try { const value = JSON.parse(line); if (value.origin) { clearTimeout(deadline); resolve(value.origin); } } catch {}
            }
        });
        child.once('exit', code => { clearTimeout(deadline); reject(Error(`Fixture exited ${code}: ${serverLog}`)); });
    });
    for (let i = 0; ; i++) {
        try { if ((await fetch(origin + '/api/animation-manifest')).ok) break; } catch {}
        if (i > 100) throw Error('Fixture readiness timed out');
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
    for (const native of [false, true]) {
        assert.equal((await fetch(origin + '/qa/reset', { method: 'POST' })).status, 200);
        const page = await browser.newPage({ viewport: { width: 900, height: 900 } });
        page.on('pageerror', error => report.errors.push(error.message));
        await page.route('**/*', route => route.request().url().startsWith(origin + '/') || route.request().url().startsWith('blob:') ? route.continue() : route.abort());
        await page.goto(origin + '/tests/fixtures/animation_hot_reload.html' + (native ? '?native=1' : ''));
        await page.waitForFunction(() => window.reloadQA, {}, { timeout: 60000 });
        const initial = await page.evaluate(() => {
            const q = reloadQA; q.play('wave'); q.advance(0.8); q.capture();
            return { revision: q.library.snapshot.revision, resource: q.library.snapshot.resources.wave,
                duration: q.idle._vrmaPlayer.clips.get('wave').duration, targetDuration: q.nodDuration,
                height: q.manager.avatar.group.position.y };
        });
        assert.equal(initial.resource.source, 'factory');
        await page.screenshot({ path: path.join(output, (native ? 'companion' : 'face') + '-before.png') });
        const data = new FormData(); data.set('slot', 'wave');
        data.set('file', new Blob([fs.readFileSync(path.join(root, 'face/animations/iris_nod.vrma'))]), 'replacement.vrma');
        const response = await fetch(origin + '/wizard/api/upload-animation', { method: 'POST', body: data });
        assert.equal(response.status, 200, await response.clone().text());
        const commit = await response.json();
        await page.waitForFunction(({ previous, filename }) => {
            const q = reloadQA, resource = q.library.snapshot?.resources.wave;
            return q.idle._manifestRevision != null && q.idle._manifestRevision !== previous
                && resource?.filename === filename && q.idle._vrmaPlayer._revisions.get('wave') === resource.revision;
        }, { previous: initial.revision, filename: commit.filename }, { timeout: 12000 });
        const changed = await page.evaluate(() => ({ ...reloadQA.continuity(), resource: reloadQA.library.snapshot.resources.wave }));
        assert.equal(changed.resource.source, 'user'); assert.equal(changed.resource.filename, commit.filename);
        assert.ok(changed.position < 0.0001 && changed.angle < 0.001, JSON.stringify(changed));
        assert.equal(changed.rootHeight, initial.height); assert.equal(changed.speed, 1);
        assert.equal(changed.duration, initial.targetDuration); assert.notEqual(changed.duration, initial.duration);
        const oldBytes = await fetch(origin + initial.resource.url);
        assert.equal(oldBytes.status, 200, 'Old factory URLs stay available');
        assert.equal(crypto.createHash('sha256').update(Buffer.from(await oldBytes.arrayBuffer())).digest('hex'), initial.resource.revision);
        const stable = (await fetch(origin + '/api/animation-manifest')).headers.get('etag');
        const invalid = new FormData(); invalid.set('name', 'wave'); invalid.set('file', new Blob(['invalid']), 'invalid.vrma');
        assert.equal((await fetch(origin + '/api/upload-vrma', { method: 'POST', body: invalid })).status, 400);
        assert.equal((await fetch(origin + '/api/animation-manifest', { headers: { 'If-None-Match': stable } })).status, 304);
        const kept = await page.evaluate(async () => {
            const q = reloadQA, action = q.idle._vrmaPlayer.current; await q.library.refresh(); q.advance(0.75);
            return { ...q.continuity(), kept: q.idle._vrmaPlayer.current === action, errors: q.errors, outstandingBlobs: q.owned.size, revoked: q.revoked.length };
        });
        assert.equal(kept.kept, true); assert.ok(kept.angle > 0.05); assert.equal(kept.outstandingBlobs, 0);
        assert.equal(kept.rootHeight, initial.height); assert.deepEqual(kept.errors, []);
        if (native) assert.ok(kept.revoked > 0);
        await page.screenshot({ path: path.join(output, (native ? 'companion' : 'face') + '-after.png') });
        await page.reload();
        await page.waitForFunction(() => window.reloadQA, {}, { timeout: 60000 });
        assert.equal(await page.evaluate(() => reloadQA.library.snapshot.resources.wave.filename), commit.filename);
        report.surfaces.push({ surface: native ? 'companion-browser-bridge' : 'face', initial, changed, kept });
        await page.close();
    }
    report.factory = await (await fetch(origin + '/qa/factory-hashes')).json();
    assert.equal(report.factory.unchanged, true); assert.deepEqual(report.errors, []);
    report.status = 'passed';
} catch (error) {
    report.status = 'failed'; report.errors.push(error.message); throw error;
} finally {
    try {
        await browser?.close();
    } catch (error) {
        report.status = 'failed'; report.errors.push(`Browser cleanup: ${error.message}`); throw error;
    } finally {
        try {
            if (child.exitCode === null && child.signalCode === null && child.pid) {
                const stopped = new Promise(resolve => child.once('exit', resolve));
                child.kill(); await stopped;
            }
        } catch (error) {
            report.status = 'failed'; report.errors.push(`Fixture cleanup: ${error.message}`); throw error;
        } finally {
            fs.writeFileSync(path.join(output, 'server.txt'), serverLog);
            fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
        }
    }
}
console.log(JSON.stringify(report, null, 2));
