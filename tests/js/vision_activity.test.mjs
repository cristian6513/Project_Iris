import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildVisionActivity } from '../../face/js/vision_activity.js';

const body = { tagName: 'BODY' };
const documentLike = { visibilityState: 'visible', activeElement: body };

test('missing context is unknown, never a made-up free activity mode', () => {
    assert.ok(Object.values(buildVisionActivity()).every(value => value === null));
    const value = buildVisionActivity({ documentLike, status: 'idle' });
    assert.equal(value.visible, true);
    assert.equal(value.typing, false);
    assert.equal(value.speaking, false);
    assert.equal(value.listening, false);
    for (const flag of ['quiet', 'focus', 'gaming', 'presenting', 'meeting']) {
        assert.equal(value[flag], null);
    }
});

test('focused editable fields defer without reading their contents', () => {
    for (const element of [
        { tagName: 'TEXTAREA' },
        { tagName: 'INPUT', type: 'password' },
        { tagName: 'INPUT', type: 'search' },
        { tagName: 'DIV', isContentEditable: true },
    ]) {
        Object.defineProperty(element, 'value', { get() { throw Error('private'); } });
        Object.defineProperty(element, 'textContent', { get() { throw Error('private'); } });
        const value = buildVisionActivity({
            documentLike: { ...documentLike, activeElement: element }, status: 'idle',
        });
        assert.equal(value.typing, true);
        assert.ok(Object.values(value).every(v => v === null || typeof v === 'boolean'));
    }
    assert.equal(buildVisionActivity({
        documentLike: { ...documentLike, activeElement: { tagName: 'INPUT', type: 'checkbox' } },
    }).typing, false);
});

test('known voice and quiet states are preserved, disconnected states are not idle', () => {
    assert.equal(buildVisionActivity({ documentLike, status: 'speaking' }).speaking, true);
    assert.equal(buildVisionActivity({ documentLike, status: 'listening' }).listening, true);
    assert.equal(buildVisionActivity({ documentLike, status: 'offline' }).speaking, null);
    assert.equal(buildVisionActivity({ documentLike, status: 'connected' }).listening, null);
    assert.equal(buildVisionActivity({ documentLike, quiet: true }).quiet, true);
    assert.equal(buildVisionActivity({ documentLike, quiet: false }).quiet, false);
    assert.equal(buildVisionActivity({ documentLike, quiet: 'false' }).quiet, null);
    assert.equal(buildVisionActivity({
        documentLike: { ...documentLike, visibilityState: 'hidden' },
    }).visible, false);
});

test('both surfaces report activity at the existing opt-in cadence before camera-only work', () => {
    const app = readFileSync(new URL('../../face/js/app.js', import.meta.url), 'utf8');
    const companion = readFileSync(new URL('../../face/companion/companion.js', import.meta.url), 'utf8');
    for (const source of [app, companion]) {
        const start = source.indexOf('const visionPaused =', source.indexOf('_visionEmitAccum = 0;'));
        const code = source.slice(start, source.indexOf('const signal =', start));
        assert.match(code, /this\.visionAssistant\.enabled && !visionPaused/);
        assert.match(code, /this\._visionEmitAccum >= 1\.5/);
        assert.match(code, /'vision_activity', buildVisionActivity/);
    }
    const method = companion.slice(companion.indexOf('    _updateLocalPerception(delta) {'));
    assert.ok(method.indexOf("'vision_activity'") < method.indexOf('if (!this.cameraStream'));
});

test('Companion reports its activity without camera and sends nothing when off or paused', () => {
    const source = readFileSync(new URL('../../face/companion/companion.js', import.meta.url), 'utf8');
    const body = source.match(/_updateLocalPerception\(delta\) \{([\s\S]*?)\n    \}\n\n    _onResize/)[1];
    const doc = { ...documentLike, body: { dataset: { operationalState: 'idle' } } };
    const tick = new Function('buildVisionActivity', 'buildVisionSignal', 'document',
        'return function(delta) {' + body + '}')(buildVisionActivity,
        () => { throw Error('Camera analysis must not be called'); }, doc);
    const sent = [];
    const app = {
        visionAssistant: { enabled: true, paused_until: 0 }, _visionEmitAccum: 0,
        quietMode: false, ws: { send: (...args) => sent.push(args) },
    };
    tick.call(app, 1);
    assert.equal(sent.length, 0);
    tick.call(app, 0.5);
    assert.equal(sent.length, 1);
    assert.equal(sent[0][0], 'vision_activity');
    app.visionAssistant.enabled = false;
    tick.call(app, 2);
    app.visionAssistant.enabled = true;
    app.visionAssistant.paused_until = Date.now() / 1000 + 60;
    tick.call(app, 2);
    assert.equal(sent.length, 1);
    assert.equal(app._visionEmitAccum, 0);
});
