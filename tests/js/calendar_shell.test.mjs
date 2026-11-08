/** F11 integration seams: execute the actual shell and Face handlers without WebGL/startup. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';

const shell = await readFile(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
const face = await readFile(new URL('../../face/js/app.js', import.meta.url), 'utf8');

test('the complete Face entry parses as a module before any startup or WebGL runs', () => {
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('NODE_TEST_')));
    const checked = spawnSync(process.execPath, ['--check', '--input-type=module'], {
        input: face, encoding: 'utf8', env,
    });
    assert.equal(checked.error, undefined);
    assert.equal(checked.status, 0, checked.stderr);
});
function method(source, name, dependencies = {}) {
    const body = source.match(new RegExp(`^    ${name}\\([^]*?^    \\}`, 'm'))?.[0];
    assert.ok(body, `${name} is present in the actual source`);
    return new Function(...Object.keys(dependencies), `return ({${body}})`)(...Object.values(dependencies))[name];
}

test('opening a Calendar reminder reveals the desktop before navigating, without reading its inbox item', () => {
    const calls = [];
    const destination = { date: '2026-10-16', event_id: `event_${'a'.repeat(32)}` };
    const canvas = { openCommandSurface: (id) => calls.push(['reveal', id]),
        _navigateApp: (id, payload) => calls.push(['navigate', id, payload]) };
    method(shell, 'openCalendar').call(canvas, destination);
    assert.deepEqual(calls, [['reveal', 'calendar'], ['navigate', 'calendar', destination]]);
    const object = face.match(/this\.calendarNotice = new CalendarNotice\(([^]*?)\n        \}\);/)?.[1];
    assert.ok(object);
    const handlers = new Function(`return (${object}\n});`).call({ activityCanvas: {
        openCalendar: (payload) => calls.push(['notice', payload]) } });
    handlers.onOpenCalendar(destination);
    assert.deepEqual(calls.at(-1), ['notice', destination]);
});

test('Face calendar delivery checks the current busy state, and the return card cannot overlap it', () => {
    const shown = [], inbox = [];
    const doc = { visibilityState: 'visible' };
    const handle = method(face, '_handleMessage', { document: doc });
    const view = { surfaceId: 'face-test', _visionStatus: 'idle',
        inboxNotice: { current: null, present: (payload, options) => inbox.push(options) },
        calendarNotice: { current: null, present: (payload, options) => shown.push(options) } };
    handle.call(view, 'calendar_reminder', { notice_id: 'a'.repeat(32) });
    assert.equal(shown[0].surfaceId, 'face-test');
    assert.equal(shown[0].documentLike, doc);
    assert.equal(Boolean(shown[0].busy()), false);
    view._visionStatus = 'tool';
    assert.equal(shown[0].busy(), true);
    view._visionStatus = 'idle'; view.calendarNotice.current = 'a'.repeat(32);
    assert.equal(shown[0].busy(), true);
    handle.call(view, 'inbox_presentation', {});
    assert.equal(inbox[0].chatBusy, true);
});
