import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const text = fs.readFileSync(new URL('../../face/js/pose_face.js', import.meta.url), 'utf8');
const { PoseFace, NaturalBlink } = await import('data:text/javascript;base64,' + Buffer.from(text).toString('base64'));
const profiles = JSON.parse(fs.readFileSync(new URL('../../face/animations/manifest.json', import.meta.url))).facial_profiles;
function fixture() {
    const values = {}, face = new PoseFace();
    const avatar = { currentExpressions: { happy: 0, sad: 0.3, relaxed: 0.1 }, mouthOpenness: 0,
        vrm: { expressionManager: { setValue: (n, v) => values[n] = v, getValue: n => values[n] || 0 } } };
    const step = () => { values.blink = 0; face.apply(avatar, 1/60); };
    const advance = (seconds = 2) => { for (let i = 0; i < seconds * 60; i++) step(); };
    return { avatar, values, face, step, advance };
}
test('all 59 poses allow gaze and natural blinking without changing source clips', () => {
    assert.equal(Object.keys(profiles).length, 59);
    for (const [name, profile] of Object.entries(profiles)) {
        const held = name === 'desk_type_idle' ? 'screen' : /(read|tablet)_idle$/.test(name) ? 'down' : 'natural';
        assert.equal(profile.eyes.gaze, held, name);
        assert.ok(profile.eyes.blinkInterval >= 2 && profile.eyes.blinkInterval <= 8);
        assert.ok(profile.strength <= 0.7);
    }
});
test('a rest pose brings its own mood and gives way to the agent\'s', () => {
    const { avatar, values, advance } = fixture();
    avatar.currentExpressions = { happy: 0, sad: 0, relaxed: 0.3 };
    avatar.poseFacialProfile = profiles.idle;
    avatar.poseClipTime = 3;
    advance(5);
    assert.ok(values.happy > 0.15, 'she smiles on her own');
    avatar.currentExpressions = { happy: 0, sad: 0.6, relaxed: 0 };
    advance(1);
    assert.ok(values.happy < 0.01, 'the rest mood leaves at once');
    assert.ok(Math.abs(values.sad - 0.6) < 0.01, 'and the agent\'s stays whole');
    avatar.currentExpressions = { happy: 0, sad: 0, relaxed: 0.3 };
    avatar.poseFaceSpeaking = true;
    advance(5);
    assert.ok(values.happy < 0.01, 'nor does it smile over her speech');
});
test('a moment of a rest pose can lower her eyelids further than the clip does', () => {
    const { avatar, values, advance } = fixture();
    avatar.currentExpressions = { happy: 0, sad: 0, relaxed: 0.3 };
    avatar.poseFacialProfile = profiles.idle_lazy;
    avatar.poseClipTime = 0.5;
    advance(5);
    const awake = values.blink;
    avatar.poseClipTime = 3;
    advance(5);
    assert.ok(awake > 0.08 && values.blink > awake + 0.08, `${awake} then ${values.blink}`);
});
test('gesture face blends smoothly, leaves the base mood intact, then restores it', () => {
    const { avatar, values, advance, step } = fixture();
    avatar.poseFacialProfile = profiles.wave;
    step(); assert.ok(values.happy > 0 && values.happy < 0.05);
    advance(); assert.ok(values.happy > 0.4);
    assert.equal(avatar.currentExpressions.happy, 0);
    avatar.poseFacialProfile = profiles.idle;
    const before = values.happy;
    step(); assert.ok(values.happy < before && values.happy > before * 0.8);
    advance(8); assert.ok(values.happy < 0.001);   // the rest pose's own mood drifts slowly
    assert.ok(Math.abs(values.sad - 0.3) < 0.001);
});
test('a new chat emotion remains live while a gesture is playing', () => {
    const { avatar, values, advance } = fixture();
    avatar.poseFacialProfile = profiles.wave; advance();
    avatar.currentExpressions.sad = 0.8; advance();
    assert.ok(values.sad > 0.4);
    avatar.poseFacialProfile = null; advance();
    assert.ok(values.sad > 0.79);
});
test('rapid switching stays bounded and does not accumulate expression weight', () => {
    const { avatar, values, advance, step } = fixture();
    for (let i = 0; i < 300; i++) {
        avatar.poseFacialProfile = i % 2 ? profiles.celebrate : profiles.angry;
        step();
        assert.ok(Object.values(values).every(v => Number.isFinite(v) && v >= 0 && v <= 1));
    }
    avatar.poseFacialProfile = null; advance();
    assert.ok(values.happy < 0.001 && values.angry < 0.001);
});
test('surprise mouth yields immediately to real speech and stays closed between words', () => {
    const { avatar, values, advance, step } = fixture();
    avatar.poseFacialProfile = profiles.surprised; advance();
    assert.ok(values.aa > 0.15);
    avatar.poseFaceSpeaking = true;
    avatar.currentExpressions.aa = 0.65; step(); assert.equal(values.aa, 0.65);
    avatar.currentExpressions.aa = 0; step(); assert.equal(values.aa, 0);
});
test('clips carrying authored faces retain exclusive expression ownership', () => {
    const { avatar, values, advance, face } = fixture();
    avatar.poseFacialProfile = profiles.wave; advance();
    avatar.animationOwnsBlink = true;
    values.happy = 0.95; values.blink = 1; values.aa = 0.6;
    face.apply(avatar, 1/60);
    assert.equal(values.happy, 0.95); assert.equal(values.blink, 1); assert.equal(values.aa, 0.6);
});
test('resting eyelids never suppress a full blink and release on idle', () => {
    const { avatar, values, advance, face } = fixture();
    avatar.poseFacialProfile = profiles.think; advance();
    assert.ok(values.blink > 0.07 && values.blink < 0.1);
    values.blink = 1; face.apply(avatar, 1/60); assert.equal(values.blink, 1);
    avatar.poseFacialProfile = profiles.idle; advance(8); assert.ok(values.blink < 0.001);
});
for (const fps of [30, 60, 144]) test(`Companion blink closes and opens smoothly at ${fps} fps`, () => {
    const blink = new NaturalBlink(() => 0.5);
    let maximum = 0, intermediate = 0, cycles = 0, previous = 0;
    for (let i = 0; i < fps * 18; i++) {
        const value = blink.update(1/fps, profiles.listen.eyes);
        maximum = Math.max(maximum, value);
        if (value > 0 && value < 1) intermediate++;
        if (!value && previous) cycles++;
        assert.ok(value >= 0 && value <= 1);
        previous = value;
    }
    assert.equal(maximum, 1); assert.ok(intermediate > 6); assert.ok(cycles >= 3);
});
test('paused tabs cannot leave a timer reopening an authored eye pose', () => {
    const blink = new NaturalBlink(() => 0.5);
    blink.wait = 0; blink.update(0.08);
    blink.reset();
    assert.equal(blink.value, 0);
    assert.equal(blink.update(30), 0);
});

test('a pose change cannot speed up a blink that already started', () => {
    const a = new NaturalBlink(() => 0.5), b = new NaturalBlink(() => 0.5);
    a.wait = b.wait = 0;
    a.update(0.04, {blinkDuration:0.4}); b.update(0.04, {blinkDuration:0.4});
    for (let i=0;i<20;i++) {
        assert.equal(a.update(1/60,{blinkDuration:0.18}), b.update(1/60,{blinkDuration:0.4}));
    }
});
test('pose expressions preserve same-frame speech accents without feeding back', () => {
    const {avatar,values,face} = fixture();
    avatar.targetExpressions = {happy:0, sad:0.3, relaxed:0.1, surprised:0};
    avatar.poseFacialProfile = profiles.idle;
    values.surprised = 0.12;
    face.apply(avatar,1/60);
    assert.equal(values.surprised,0.12);
});
test('VRM fallback blink really delegates to the frame-clock curve', () => {
    const source = fs.readFileSync(new URL('../../face/js/avatar.js',import.meta.url),'utf8').split('export class VRMAvatar')[1];
    const method = source.match(/^    _updateBlink\(dt\) {[^]*?^    }/m)[0];
    const update = new Function('return ({'+method+'})')()._updateBlink;
    const values = {};
    const avatar = { _naturalBlink:new NaturalBlink(()=>0.5),
        vrm:{expressionManager:{getExpression:n=> n==='blink'?{binds:[{}]}:null}},
        _setExpression:(n,v)=>values[n]=v };
    avatar._naturalBlink.wait=0; update.call(avatar,.08);
    assert.ok(values.blink>0.9);
    avatar.animationOwnsBlink=true; values.blink=.35;
    update.call(avatar,.1); assert.equal(values.blink,.35);
});

test('a rest pose carries its own mood, and it changes along the clip', async () => {
    const { moodAt } = await import('../../face/js/pose_face.js');
    const profile = { emotion: 'neutral', strength: 0.35, moments: [{ from: 2.6, to: 6, emotion: 'feliz', strength: 0.24 }] };
    assert.equal(moodAt(profile, 1).emotion, 'neutral');
    assert.equal(moodAt(profile, 3).emotion, 'feliz');
    assert.equal(moodAt(profile, 6).emotion, 'neutral', 'and goes back');
    assert.equal(moodAt({ emotion: 'feliz', strength: 0.4 }, 3).emotion, 'feliz', 'a gesture keeps its one mood');
    assert.equal(moodAt(null, 3), null);
});
