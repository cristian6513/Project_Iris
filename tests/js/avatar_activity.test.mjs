import test from 'node:test';
import assert from 'node:assert/strict';
import { AvatarActivity } from '../../face/js/avatar_activity.js';
import { AnimationDirector } from '../../face/js/animation_director.js';

test('wake is idle while armed and holds listening only after detection', () => {
    const activity = new AvatarActivity();
    activity.handle('hermes_wake_state', { state: 'listening', active: true });
    assert.equal(activity.state, 'idle');
    activity.handle('hermes_wake_state', { state: 'detected' });
    assert.equal(activity.state, 'listening');
    activity.handle('hermes_wake_state', { state: 'recording' });
    assert.equal(activity.state, 'listening');
    activity.handle('hermes_wake_state', { state: 'processing' });
    assert.equal(activity.state, 'thinking');
    activity.handle('speech_start');
    assert.equal(activity.state, 'speaking');
    activity.handle('speech_end');
    assert.equal(activity.state, 'idle');
});

test('manual capture follows confirmed recording and ignores stale revisions', () => {
    const activity = new AvatarActivity();
    const manual = (state, revision) => activity.handle('hermes_voice_state', { state, revision });
    manual('arming', 1);
    assert.equal(activity.state, 'idle');
    manual('recording', 2);
    assert.equal(activity.state, 'listening');
    manual('off', 1);
    assert.equal(activity.state, 'listening');
    manual('transcribing', 3);
    assert.equal(activity.state, 'thinking');
    manual('processing', 4);
    activity.handle('speech_start');
    assert.equal(activity.state, 'speaking');
    activity.handle('speech_end');
    assert.equal(activity.state, 'idle');
});

test('cancelled manual capture and disconnected wake release listening', () => {
    const activity = new AvatarActivity();
    activity.handle('hermes_voice_state', { state: 'recording', revision: 1 });
    activity.handle('hermes_voice_state', { state: 'cancelling', revision: 2 });
    assert.equal(activity.state, 'idle');
    activity.handle('hermes_wake_state', { state: 'recording' });
    activity.reset();
    assert.equal(activity.state, 'idle');
    activity.handle('hermes_voice_state', { state: 'recording', revision: 0 });
    assert.equal(activity.state, 'listening');
});

test('wake resumption does not cancel a manual capture in another surface', () => {
    const activity = new AvatarActivity();
    activity.handle('hermes_voice_state', { state: 'recording', revision: 1 });
    activity.handle('hermes_wake_state', { state: 'listening' });
    activity.handle('status', { status: 'idle' });
    assert.equal(activity.state, 'listening');
});

test('runtime activity follows the new avatar after replacement and resets on disconnect', () => {
    const calls = [];
    let idle = { setActivity: state => calls.push(['old', state]) };
    const director = new AnimationDirector({ getIdle: () => idle });
    director.handleRuntimeEvent('hermes_wake_state', { state: 'recording' });
    director.updateActivity();
    idle = { setActivity: state => calls.push(['new', state]) };
    director.updateActivity();
    director.resetActivity();
    assert.deepEqual(calls, [['old', 'listening'], ['new', 'listening'], ['new', 'idle']]);
});
