import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../face/js/room_media_display.js', import.meta.url), 'utf8');
const stub = `const THREE={CanvasTexture:class{set needsUpdate(v){if(v)this.version=(this.version||0)+1;}},SRGBColorSpace:'srgb',LinearFilter:'linear'};`;
const { RoomMediaDisplay } = await import('data:text/javascript;base64,' + Buffer.from(stub + source.replace(/^import .*;$/gm, '')).toString('base64'));
const theme = { cyan: 0x8defff, amber: 0xffb86b, ambient: 0x08111d, wallPanel: 0x142035, linen: 0x83979e };

function fixture(t) {
    const text = [], query = { matches: false };
    const ctx = { fillRect(){}, strokeRect(){}, beginPath(){}, moveTo(){}, lineTo(){}, stroke(){},
        fillText(value){text.push(value);}, measureText(value){return {width: value.length * 20};} };
    t.mock.method(globalThis, 'setTimeout', () => { throw Error('No animation timers'); });
    const previous = { document: globalThis.document, matchMedia: globalThis.matchMedia };
    globalThis.document = { createElement: () => ({ getContext: () => ctx }) };
    globalThis.matchMedia = () => query;
    t.after(() => Object.assign(globalThis, previous));
    const screen = { material: {} }, light = { material: { opacity: 0.2 } };
    const display = new RoomMediaDisplay(screen, [light]); display.setTheme(theme);
    return { display, screen, light, query, text };
}

test('tablet keeps one texture across repeated snapshots and theme changes', t => {
    const { display, screen } = fixture(t), texture = display.texture;
    const state = { status: 'playing', title: 'Track', artist: 'Artist' };
    display.setState(state); const version = texture.version;
    display.setState(state); display.update(20);
    assert.equal(texture.version, version);
    display.setTheme({ ...theme, cyan: 0xffb6d8 });
    assert.equal(screen.material.map, texture); assert.ok(texture.version > version);
});

test('room activity is subdued and stops on pause, mute, reduced motion or hidden room', t => {
    const { display, light, query } = fixture(t);
    display.setState({ status: 'playing' });
    display.update(0); const base = light.material.opacity;
    display.update(1); assert.notEqual(light.material.opacity, base);
    query.matches = true; display.update(2); assert.equal(light.material.opacity, base);
    query.matches = false; display.update(2, false); assert.equal(light.material.opacity, base);
    display.setState({ status: 'playing', muted: true }); display.update(1); assert.equal(light.material.opacity, 0.2);
    display.setState({ status: 'paused' }); display.update(1); assert.equal(light.material.opacity, 0.2);
});

test('display bounds text, uses explicit states, and clears stale titles on idle', t => {
    const { display, text } = fixture(t);
    display.setState({ status: 'playing', muted: true, title: 'x'.repeat(1000), artist: 'a\nb' });
    assert.ok(text.includes('REPRODUCIENDO / SILENCIO'));
    assert.ok(display.state.title.length <= 96); assert.equal(display.state.artist, 'a b');
    display.setState({ status: 'idle', title: 'stale' }); assert.equal(display.state.title, '');
    display.setState({ status: 'error' }); assert.ok(text.includes('ERROR DE AUDIO'));
    display.setState(null); assert.equal(display.state.status, 'idle');
});
