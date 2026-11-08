import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { ROOM_LIFE, ROOM_LIFE_KEY, RoomLife, RoomRoutines, routineDecision } from '../../face/js/room_routines.js';

function fixture({ life = 'calm', can = () => true, random = () => 0.5 } = {}) {
    const clock = { t: 0 };
    const calls = [];
    const scenes = {
        orders: 0,
        held: false, busy: false, walking: false,
        heldByOrder() { return this.held; },
        isBusy() { return this.busy || this.walking; },
        isWalking() { return this.walking; },
        canRun: can,
        holding: true,
        back: false,
        hasBackToRoom() { return this.back; },
        canTeleport() { return true; },
        teleportHome() { calls.push('teleport'); return true; },
        freeHands() { if (!this.holding) return false; this.holding = false; calls.push('freeHands'); return true; },
        execute(order, { origin } = {}) { calls.push(`${order}:${origin}`); return true; },
    };
    const context = { life, reducedMotion: false, visible: true, exploring: false, activity: 'idle' };
    const routines = new RoomRoutines({ scenes, context: () => context, now: () => clock.t, random });
    const at = (seconds) => { clock.t = seconds; routines.update(); };
    return { clock, calls, scenes, context, routines, at };
}

test('the body has one order of priority', () => {
    const allowed = (over) => routineDecision({ life: 'calm', ...over });
    assert.deepEqual(allowed(), { allowed: true, reason: 'ready' });
    assert.equal(allowed({ life: 'off' }).reason, 'off');
    assert.equal(allowed({ reducedMotion: true }).reason, 'reduced-motion');
    assert.equal(allowed({ ordered: true }).reason, 'order');
    assert.equal(allowed({ activity: 'speaking' }).reason, 'conversation');
    assert.equal(allowed({ activity: 'thinking' }).reason, 'conversation');
    assert.equal(allowed({ activity: 'listening' }).reason, 'attentive');
    assert.equal(allowed({ visible: false }).reason, 'hidden');
    assert.equal(allowed({ exploring: true }).reason, 'exploring');
    assert.equal(allowed({ ordered: true, activity: 'speaking' }).reason, 'order', 'an order outranks the conversation');
});

test('she waits, does something near, goes back, and waits again', () => {
    const { calls, routines, at } = fixture();
    at(59);
    assert.deepEqual(calls, []);
    at(60);
    assert.equal(calls.length, 1);
    assert.match(calls[0], /^(read|tablet)_center:routine$/);
    assert.equal(routines.step.kind, 'near');
    at(106);
    assert.equal(calls.at(-1), 'return_center:routine');
    assert.equal(routines.step, null);
    at(165);
    assert.equal(calls.length, 2, 'a pause between routines');
    at(166);
    assert.equal(calls.length, 3);
});

test('after longer she goes somewhere, and after much longer she lies down until spoken to', () => {
    const { calls, routines, at, clock } = fixture();
    clock.t = 0;
    routines.quietSince = -240;
    at(0);
    assert.equal(routines.step.kind, 'visit');
    assert.match(calls[0], /^(sit|read|tablet)_(sofa|bed|desk)|use_pc/);
    routines.step = null;
    routines.quietSince = -900;
    at(1);
    assert.equal(calls.at(-1), 'lie_bed:routine');
    assert.equal(routines.step.until, Infinity);
    at(100000);
    assert.equal(calls.at(-1), 'lie_bed:routine', 'she stays');
});

test('the routines themselves do not count as quiet ending: near leads to visits, and those to rest', () => {
    const { calls, routines, at } = fixture({ random: () => 0.9 });
    const kinds = [];
    for (let t = 0; t < 1200; t += 1) {
        at(t);
        if (routines.step && kinds.at(-1) !== routines.step.kind) kinds.push(routines.step.kind);
    }
    assert.equal(kinds[0], 'near');
    assert.ok(kinds.includes('visit'));
    assert.equal(kinds.at(-1), 'rest');
    assert.ok(calls.includes('lie_bed:routine'));
});

test('at a place she may change what she does there before leaving', () => {
    const { calls, routines, at } = fixture({ random: () => 0.1 });
    routines.quietSince = -240;
    at(0);
    const first = calls[0];
    at(routines.step.until + 1);
    assert.notEqual(calls[1], first);
    assert.notEqual(calls[1], 'return_center:routine');
    assert.equal(routines.step.kind, 'visit');
});

test('she does not repeat what she has just done', () => {
    const { calls, routines, at } = fixture();
    let t = 0;
    for (let i = 0; i < 6; i++) {
        routines.quietSince = t - 60;
        routines.nextAt = 0;
        at(t);
        t = routines.step.until + 1;
        at(t);
    }
    const picks = calls.filter((c) => c !== 'return_center:routine');
    picks.forEach((pick, i) => assert.notEqual(pick, picks[i - 1]));
});

test('an order takes her from a routine, and keeps her until the next order', () => {
    const { calls, scenes, routines, at } = fixture();
    at(60);
    assert.ok(routines.step);
    scenes.orders += 1;
    scenes.held = true;
    at(61);
    assert.equal(routines.step, null);
    const count = calls.length;
    at(100000);
    assert.equal(calls.length, count, 'she stays where she was told');
    scenes.orders += 1;
    scenes.held = false;
    at(100001);
    at(100060);
    assert.equal(calls.length, count, 'the wait starts at the order');
    at(100061);
    assert.equal(calls.length, count + 1);
});

test('spoken to in a seat she frees her hands once and stays there', () => {
    const { calls, routines, context, at } = fixture({ random: () => 0.9 });
    routines.quietSince = -240;
    at(0);
    context.activity = 'thinking';
    at(1);
    at(2);
    context.activity = 'speaking';
    at(3);
    assert.equal(calls.filter((c) => c === 'freeHands').length, 1);
    assert.equal(calls.filter((c) => c === 'return_center:routine').length, 0, 'she answers from there');
    context.activity = 'idle';
    at(4);
    assert.ok(routines.step, 'the routine waits');
    at(70);
    assert.equal(calls.at(-1), 'return_center:routine');
});

test('spoken to on a walk of her own she turns back', () => {
    const { calls, scenes, routines, context, at } = fixture();
    routines.quietSince = -240;
    at(0);
    scenes.walking = true;
    context.activity = 'thinking';
    at(1);
    assert.equal(calls.at(-1), 'return_center:routine');
    assert.ok(!calls.includes('freeHands'));
    assert.equal(routines.step, null);
});

test('the window is one more place to visit, on its own', () => {
    const { calls, routines, at } = fixture({ random: () => 0.1 });
    const visited = new Set();
    for (let t = 0; t < 400; t += 1) {
        routines.quietSince = t - 250;
        routines.step = null;
        routines.last = null;
        routines.random = () => Math.random();
        at(t);
    }
    calls.forEach((call) => visited.add(call));
    assert.ok(visited.has('look_window:routine'));
});

test('spoken to at the window she comes back: she cannot answer with her back turned', () => {
    const { calls, scenes, routines, clock, at } = fixture({ random: () => 0.9 });
    routines.quietSince = -240;
    at(0);
    routines.step.order = 'look_window';
    scenes.back = true;
    clock.t = 1;
    routines.turnStarted(1);
    assert.equal(calls.at(-1), 'return_center:routine');
    assert.equal(routines.step, null);
});

test('she may also jump from the window', () => {
    const { calls, scenes, routines, clock, at } = fixture({ random: () => 0 });
    routines.quietSince = -400;
    at(0);
    routines.step.order = 'look_window';
    scenes.back = true;
    calls.length = 0;
    clock.t = 1;
    routines.turnStarted(1);
    assert.deepEqual(calls, ['teleport']);
});

test('an open microphone stops nothing and starts nothing', () => {
    const { calls, routines, context, at } = fixture();
    at(60);
    context.activity = 'listening';
    at(61);
    assert.ok(!calls.includes('freeHands'));
    const count = calls.length;
    routines.step = null;
    routines.quietSince = -1000;
    at(62);
    assert.equal(calls.length, count);
});

test('a sent message holds her until the answer, which gives no signal before it starts', () => {
    const { calls, routines, context, at } = fixture();
    at(30);
    routines.turnStarted(1);
    at(89);
    at(95);
    assert.equal(calls.length, 0, 'no routine while the answer is on its way');
    context.activity = 'speaking';
    at(100);
    context.activity = 'idle';
    routines.turnEnded(1);
    at(105);
    assert.equal(routines.replyUntil, 0);
    at(159);
    assert.equal(calls.length, 0, 'a minute of quiet after she spoke');
    at(161);
    assert.equal(calls.length, 1);
});

test('a message that gets no answer does not hold her forever', () => {
    const { calls, routines, at } = fixture();
    at(10);
    routines.turnStarted(1);
    at(99);
    assert.equal(calls.length, 0);
    at(101);
    at(171);
    assert.equal(calls.length, 1);
});

test('a long task sends her to the PC, a quick one does not', () => {
    const { calls, routines, context, at } = fixture();
    at(1);
    routines.turnStarted(1);
    routines.toolRunning(1);
    at(20);
    assert.ok(!calls.some((c) => c.startsWith('use_pc')));
    at(22);
    assert.equal(calls.at(-1), 'use_pc:routine');
    at(23);
    assert.equal(calls.filter((c) => c === 'use_pc:routine').length, 1);
    assert.ok(!calls.includes('freeHands'), 'she works while the task runs');
    context.activity = 'speaking';
    at(24);
    assert.ok(calls.includes('freeHands'), 'and stops typing to answer');
});

test('what she holds is put away as soon as it can be, however long that takes', () => {
    const { calls, scenes, routines, context, at } = fixture();
    routines.quietSince = -240;
    at(0);
    let ready = false;
    const free = scenes.freeHands.bind(scenes);
    scenes.freeHands = () => (ready ? free() : false);   // still on its way in
    context.activity = 'thinking';
    at(1);
    at(2);
    assert.ok(!calls.includes('freeHands'));
    ready = true;
    at(3);
    assert.ok(calls.includes('freeHands'));
});

for (const [where, change] of [['exploring', { exploring: true }], ['with the room hidden', { visible: false }]]) {
    test(`a long task does not send her to the PC while ${where}, and does once that ends`, () => {
        const { calls, routines, context, at } = fixture();
        at(1);
        routines.turnStarted(1);
        routines.toolRunning(1);
        Object.assign(context, change);
        at(22);
        at(30);
        assert.ok(!calls.some((c) => c.startsWith('use_pc')));
        context.exploring = false;
        context.visible = true;
        at(31);
        assert.equal(calls.at(-1), 'use_pc:routine');
    });
}

test('stopped before she spoke, the task is over: no PC, and no wait for an answer', () => {
    const { calls, routines, at } = fixture();
    at(1);
    routines.turnStarted(1);
    routines.toolRunning(1);
    at(3);
    routines.turnEnded(1);
    assert.equal(routines.toolSince, null);
    assert.equal(routines.replyUntil, 0);
    at(22);
    at(40);
    assert.deepEqual(calls, []);
    at(64);
    assert.equal(calls.length, 1, 'the usual minute after the last time someone spoke');
    assert.match(calls[0], /_center:routine$/);
});

test('a tool event that arrives after the stop moves nothing', () => {
    const { calls, routines, at } = fixture();
    at(1);
    routines.turnStarted(1);
    at(3);
    routines.turnEnded(1);
    routines.toolRunning(1);
    assert.equal(routines.toolSince, null);
    at(30);
    assert.ok(!calls.some((c) => c.startsWith('use_pc')));
});

test('a task that is no turn of hers does not send her to the PC', () => {
    const { calls, routines, at } = fixture();
    at(1);
    routines.toolRunning(1);
    routines.toolRunning(null);       // a background result being presented
    routines.toolRunning();
    at(30);
    assert.deepEqual(calls, []);
});

test('stopped in its first second, a late tool event still moves nothing', () => {
    const { calls, routines, clock, at } = fixture();
    at(1);
    routines.turnStarted(1);
    routines.toolRunning(1);
    clock.t = 1.2;
    routines.turnEnded(1);
    clock.t = 1.3;
    routines.toolRunning(1);
    assert.equal(routines.toolSince, null);
    assert.equal(routines.replyUntil, 0);
    at(22);
    assert.ok(!calls.some((c) => c.startsWith('use_pc')));
});

test('a new message starts its own count', () => {
    const { calls, routines, at } = fixture();
    at(1);
    routines.turnStarted(1);
    routines.toolRunning(1);
    at(15);
    routines.turnStarted(1);
    routines.toolRunning(1);
    at(25);
    assert.ok(!calls.some((c) => c.startsWith('use_pc')), 'not ten seconds in');
    at(36);
    assert.equal(calls.at(-1), 'use_pc:routine');
});

test('the end of the turn before does not close the new one, however late it comes', () => {
    const { calls, routines, clock, at } = fixture();
    at(1);
    routines.turnStarted(1);
    routines.toolRunning(1);
    at(5);
    routines.turnStarted(2);
    clock.t = 6.1;
    routines.turnEnded(1);
    assert.ok(routines.replyUntil > 6.1, 'still waiting for the answer');
    at(7);
    routines.toolRunning(2);
    assert.equal(routines.toolSince, 7);
    at(28);
    assert.equal(calls.at(-1), 'use_pc:routine');
});

test('the tools of the turn before do not count for the new one', () => {
    const { routines, at } = fixture();
    at(1);
    routines.turnStarted(1);
    at(5);
    routines.turnStarted(2);
    routines.toolRunning(1);
    assert.equal(routines.toolSince, null);
});

test('a lost connection closes the turn at once', () => {
    const { routines, at } = fixture();
    at(1);
    routines.turnStarted(1);
    routines.toolRunning(1);
    routines.connectionLost();
    assert.equal(routines.replyUntil, 0);
    assert.equal(routines.toolSince, null);
    routines.toolRunning(1);
    assert.equal(routines.toolSince, null, 'what was still on its way moves nothing');
});

test('an order during a task leaves no pending trip to the PC', () => {
    const { calls, scenes, routines, at } = fixture();
    at(1);
    routines.turnStarted(1);
    routines.toolRunning(1);
    at(10);
    scenes.orders += 1;
    at(11);
    assert.equal(routines.toolSince, null);
    at(40);
    assert.ok(!calls.some((c) => c.startsWith('use_pc')));
});

test('the room hears turns by number, and the end of speech closes none', () => {
    const source = readFileSync(new URL('../../face/js/app.js', import.meta.url), 'utf8');
    const block = source.match(/^    _handleMessage\([^]*?^    \}/m)?.[0];
    assert.ok(block);
    const handle = new Function(`return ({${block.trim()}})._handleMessage;`)();
    const seen = [];
    const app = {
        roomRoutines: {
            turnStarted: (turn) => seen.push(['started', turn]),
            toolRunning: (turn) => seen.push(['tool', turn]),
            turnEnded: (turn) => seen.push(['ended', turn]),
        },
        speechPresentation: { end() {} },
        subtitles: { hide() {} },
    };
    globalThis.window = { dispatchEvent() {} };
    try {
        handle.call(app, 'turn_started', { turn: 7 });
        handle.call(app, 'tool_progress', { turn: 7 });
        handle.call(app, 'speech_end', {});
        handle.call(app, 'turn_ended', { turn: 7 });
    } finally {
        delete globalThis.window;
    }
    assert.deepEqual(seen, [['started', 7], ['tool', 7], ['ended', 7]]);
});

/** She is at a place a routine took her to, and five minutes have gone by since anyone spoke. */
function atPlace({ quiet = 400, ...options } = {}) {
    const dice = { value: 0 };
    const f = fixture({ random: () => dice.value, ...options });
    f.routines.quietSince = -quiet;
    f.at(0);
    assert.equal(f.routines.step.kind, quiet >= 900 ? 'rest' : 'visit');
    f.calls.length = 0;
    return { ...f, dice };
}

test('spoken to at a place she sometimes comes to the middle of the room, at once', () => {
    const { calls, routines, clock } = atPlace();
    clock.t = 1;
    routines.turnStarted(1);
    assert.deepEqual(calls, ['teleport']);
    assert.equal(routines.step, null);
    assert.equal(routines.nextAt, 61, 'no routine straight after');
});

test('she may jump from lying down', () => {
    const { calls, routines, clock } = atPlace({ quiet: 1000 });
    clock.t = 1;
    routines.turnStarted(1);
    assert.deepEqual(calls, ['teleport']);
});

test('a reply before landing leaves her visible in her seat and keeps clearing her hands', () => {
    const { routines, scenes, calls, clock, context, at } = atPlace();
    const seated = routines.step;
    clock.t = 1;
    routines.turnStarted(1);
    scenes.cancelTeleport = () => ({ landed: false });
    let ready = false;
    const free = scenes.freeHands.bind(scenes);
    scenes.freeHands = () => ready && free();
    context.activity = 'speaking';
    routines.responseStarted();
    assert.equal(routines.step, seated, 'the interrupted visit still owns its seat');
    assert.ok(!calls.includes('freeHands'), 'an entry clip may still be finishing');
    ready = true;
    at(2);
    assert.ok(calls.includes('freeHands'), 'she puts it away as soon as she can');
    assert.ok(!calls.includes('return_center:routine'), 'speech never waits for a walk');
});

test('a reply after landing keeps her at the centre without resurrecting the old visit', () => {
    const { routines, scenes, clock } = atPlace();
    clock.t = 1;
    routines.turnStarted(1);
    scenes.cancelTeleport = () => ({ landed: true });
    routines.responseStarted();
    assert.equal(routines.step, null);
});

test('a reply after the effect finished does not restore the old seat', () => {
    const { routines, scenes, clock } = atPlace();
    clock.t = 1;
    routines.turnStarted(1);
    scenes.cancelTeleport = () => null;
    routines.responseStarted();
    assert.equal(routines.step, null);
});

test('most of the time she stays and answers from there', () => {
    const { calls, routines, dice, clock } = atPlace();
    dice.value = 0.15;
    clock.t = 1;
    routines.turnStarted(1);
    assert.deepEqual(calls, []);
    assert.ok(routines.step, 'the routine waits');
    assert.equal(routines.teleportedAt, -Infinity);
});

test('Activa jumps more often than Tranquila', () => {
    assert.equal(ROOM_LIFE.calm.teleport, 0.15);
    assert.ok(ROOM_LIFE.active.teleport > ROOM_LIFE.calm.teleport);
    const { calls, routines, dice, clock } = atPlace({ life: 'active' });
    dice.value = 0.3;
    clock.t = 1;
    routines.turnStarted(1);
    assert.deepEqual(calls, ['teleport']);
});

test('she decides at the start of a talk, not at each message', () => {
    const { calls, routines, clock } = atPlace({ quiet: 290 });
    clock.t = 1;
    routines.turnStarted(1);
    assert.deepEqual(calls, []);
});

test('not twice in fifteen minutes, however many talks there are', () => {
    const { calls, routines, at, clock } = atPlace();
    clock.t = 1;
    routines.turnStarted(1);
    routines.turnEnded(1);
    const again = (t) => {
        routines.step = { order: 'read_sofa', kind: 'visit', place: null, until: t + 100 };
        routines.quietSince = t - 400;
        routines.stopped = false;
        clock.t = t;
        routines.turnStarted(2);
        routines.turnEnded(2);
    };
    again(800);
    assert.equal(calls.filter((c) => c === 'teleport').length, 1);
    again(901);
    assert.equal(calls.filter((c) => c === 'teleport').length, 2);
});

test('from a routine near the middle she has nowhere to jump to', () => {
    const { calls, routines, clock, at } = fixture({ random: () => 0 });
    at(60);
    assert.equal(routines.step.kind, 'near');
    clock.t = 400;
    calls.length = 0;
    routines.turnStarted(1);
    assert.ok(!calls.includes('teleport'));
});

test('on a walk, or with no routine at all, there is no jump', () => {
    const walking = atPlace();
    walking.scenes.isWalking = () => true;
    walking.scenes.canTeleport = () => false;
    walking.clock.t = 1;
    walking.routines.turnStarted(1);
    assert.ok(!walking.calls.includes('teleport'));
    const { calls, routines, clock } = fixture({ random: () => 0 });
    routines.quietSince = -400;
    clock.t = 1;
    routines.turnStarted(1);
    assert.deepEqual(calls, []);
});

for (const [why, change] of [
    ['hidden', { visible: false }], ['exploring', { exploring: true }],
    ['turned off', { life: 'off' }], ['with reduced motion', { reducedMotion: true }],
]) {
    test(`${why} she does not jump`, () => {
        const { calls, routines, context, clock } = atPlace();
        Object.assign(context, change);
        clock.t = 1;
        routines.turnStarted(1);
        assert.ok(!calls.includes('teleport'));
    });
}

test('where a body cannot jump, as in the Companion, she does as usual', () => {
    const { calls, routines, scenes, clock } = atPlace();
    delete scenes.canTeleport;
    clock.t = 1;
    routines.turnStarted(1);
    assert.ok(!calls.includes('teleport'));
    assert.ok(routines.step);
});

test('a jump that does not start leaves her where she was', () => {
    const { routines, scenes, clock } = atPlace();
    scenes.teleportHome = () => false;
    clock.t = 1;
    routines.turnStarted(1);
    assert.ok(routines.step);
    assert.equal(routines.teleportedAt, -Infinity);
});

test('in the room a reply that could not be spoken says so', () => {
    const source = readFileSync(new URL('../../face/js/app.js', import.meta.url), 'utf8');
    const block = source.match(/^    _handleMessage\([^]*?^    \}/m)?.[0];
    const handle = new Function(`return ({${block.trim()}})._handleMessage;`)();
    const shown = [];
    handle.call({ subtitles: { show: (...args) => shown.push(args) } }, 'voice_unavailable', { message: 'Iris no pudo hablar.' });
    assert.deepEqual(shown, [['Iris no pudo hablar.', 7000]]);
});

test('nothing happens if the clips are not there', () => {
    const { calls, routines, at } = fixture({ can: () => false });
    routines.quietSince = -1000;
    at(0);
    at(5000);
    assert.deepEqual(calls, []);
    assert.equal(routines.step, null);
});

test('turned off or with reduced motion she goes back and stays', () => {
    for (const change of [{ life: 'off' }, { reducedMotion: true }]) {
        const { calls, routines, context, at } = fixture();
        routines.quietSince = -240;
        at(0);
        Object.assign(context, change);
        at(1);
        assert.equal(calls.at(-1), 'return_center:routine');
        const count = calls.length;
        at(10000);
        assert.equal(calls.length, count);
    }
});

test('hidden or being explored, a routine neither starts nor moves on', () => {
    for (const change of [{ visible: false }, { exploring: true }]) {
        const { calls, routines, context, at } = fixture();
        Object.assign(context, change);
        routines.quietSince = -1000;
        at(0);
        assert.deepEqual(calls, []);
    }
});

test('the choice stays on this device and bad values fall back', () => {
    const values = new Map();
    const storage = { getItem: (k) => values.get(k) ?? null, setItem: (k, v) => values.set(k, v) };
    const life = new RoomLife({ storage: () => storage });
    assert.equal(life.value, 'calm');
    const seen = [];
    life.subscribe((state) => seen.push(state.value));
    life.setValue('active');
    assert.deepEqual(seen, ['calm', 'active']);
    assert.equal(values.get(ROOM_LIFE_KEY), 'active');
    assert.equal(new RoomLife({ storage: () => storage }).value, 'active');
    life.setValue('nonsense');
    assert.equal(life.value, 'calm');
    const failing = new RoomLife({ storage: () => { throw Error('blocked'); } });
    failing.setValue('off');
    assert.equal(failing.value, 'off');
    assert.equal(failing.persisted, false);
    assert.deepEqual(Object.keys(ROOM_LIFE), ['off', 'calm', 'active']);
});
