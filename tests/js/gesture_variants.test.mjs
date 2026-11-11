// A gesture with variants (manifest `variants`) plays one of them, never the same twice in a row.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const threeUrl = new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href;
const gltfUrl = new URL('../../companion/node_modules/three/examples/jsm/loaders/GLTFLoader.js', import.meta.url).href;
const faceUrl = name => new URL('../../face/js/' + name, import.meta.url);
const source = name => readFile(faceUrl(name), 'utf8');
const moduleUrl = text => 'data:text/javascript;base64,' + Buffer.from(text).toString('base64');
const lifecycleUrl = faceUrl('avatar_lifecycle.js').href;
const playerUrl = moduleUrl((await source('vrm_animation_loader.js'))
    .replace("from './avatar_lifecycle.js'", `from '${lifecycleUrl}'`)
    .replace("from 'three'", `from '${threeUrl}'`)
    .replace("from 'three/addons/loaders/GLTFLoader.js'", `from '${gltfUrl}'`));
const { IdleSystem } = await import(moduleUrl((await source('idle_system.js'))
    .replace("from './avatar_lifecycle.js'", `from '${lifecycleUrl}'`)
    .replace("from 'three'", `from '${threeUrl}'`)
    .replace(/from '\.\/vrm_animation_loader\.js[^']*'/, `from '${playerUrl}'`)));
const manifest = JSON.parse(await readFile(new URL('../../face/animations/manifest.json', import.meta.url), 'utf8'));

function fixture(loaded) {
    const idle = new IdleSystem({ avatar: null });
    const played = [];
    idle._vrmaManifest = manifest;
    idle._vrmaPlayer = {
        currentName: null, current: null, mixer: null,
        hasAnimation: name => loaded.has(name),
        play(name) { played.push(name); this.currentName = name; return true; },
    };
    return { idle, played };
}

test('every variant in the manifest is a clip with its own face, and only gestures have them', () => {
    for (const [gesture, clips] of Object.entries(manifest.variants)) {
        assert.ok(clips.includes(gesture), gesture);
        for (const clip of clips) {
            assert.ok(manifest.clips[clip], clip);
            assert.ok(manifest.facial_profiles[clip], clip);
            assert.ok(manifest.body_only_clips.includes(clip), clip);
            if (clip !== gesture) assert.ok(manifest.runtime_only.includes(clip), clip);
        }
    }
    assert.deepEqual(manifest.variants.talk, ['talk', 'talk_2', 'talk_3']);
});

// A repeatable stream instead of Math.random, so the counts below are exact.
function seeded(run) {
    const random = Math.random;
    let seed = 7;
    Math.random = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
    try { return run(); } finally { Math.random = random; }
}

test('a gesture plays all its variants, its first choice most, and seldom the same twice', () => {
    const { idle } = fixture(new Set(['wave', 'wave_2', 'angry', 'angry_2', 'angry_3']));
    const waves = seeded(() => Array.from({ length: 600 }, () => idle._variantFor('wave')));
    const share = waves.filter(clip => clip === 'wave').length / waves.length;
    assert.ok(share > 0.5 && share < 0.65, `her own wave first (weight 2): ${share}`);
    const angry = seeded(() => Array.from({ length: 600 }, () => idle._variantFor('angry')));
    assert.deepEqual(new Set(angry), new Set(manifest.variants.angry));
    const repeats = angry.slice(1).filter((clip, index) => clip === angry[index]).length / (angry.length - 1);
    assert.ok(repeats < 0.2, `a repeat weighs a quarter: ${repeats}`);
    assert.equal(idle._variantFor('nod'), 'nod', 'no variants: its own clip');
});

test('the jump left Celebrate: Poses only, never the agent', () => {
    assert.deepEqual(manifest.variants.celebrate, ['celebrate', 'celebrate_2']);
    assert.ok(manifest.menu_poses.includes('jump') && manifest.runtime_only.includes('jump'));
    assert.equal(manifest.clips.jump, 'iris_jump.vrma');
});

test('a variant that did not load is skipped, and a gesture left with one plays it every time', () => {
    const { idle } = fixture(new Set(['wave']));
    assert.deepEqual(new Set(Array.from({ length: 10 }, () => idle._variantFor('wave'))), new Set(['wave']));
});

test('each response speaks with one variant, kept for the whole response, the favourite most', () => {
    const { idle, played } = fixture(new Set(['talk', 'talk_2', 'talk_3', 'idle']));
    idle._activityState = 'speaking';
    assert.equal(idle._startActivityClip('speaking'), true);
    const first = played.at(-1);
    assert.ok(manifest.variants.talk.includes(first));
    assert.equal(idle._activityClip, first);
    assert.equal(idle.setActivity('speaking'), true, 'the same response keeps its clip');
    assert.equal(played.length, 1);
    seeded(() => { for (let i = 0; i < 600; i++) idle._startActivityClip('speaking'); });
    const share = clip => played.filter(name => name === clip).length / played.length;
    assert.ok(share('talk_2') > 0.43 && share('talk_2') < 0.57, `talk_2 speaks most (3 of 6): ${share('talk_2')}`);
    assert.ok(share('talk_3') > 0.1 && share('talk_3') < 0.23, `talk_3 least (1 of 6): ${share('talk_3')}`);
});

test('her walk is the plain one most trips and the catwalk now and then, each at its own pace', () => {
    const { idle, played } = fixture(new Set(['walk', 'walk_2', 'idle']));
    idle._vrmaPlayer.current = { scale: 1, setEffectiveTimeScale(v) { this.scale = v; } };
    assert.deepEqual(manifest.variants.walk, ['walk', 'walk_2']);
    const picks = seeded(() => Array.from({ length: 600 }, () => idle.chooseWalk()));
    const slow = picks.filter(({ clip }) => clip === 'walk_2').length / picks.length;
    assert.ok(slow > 0.18 && slow < 0.32, `the catwalk one trip in four: ${slow}`);
    const catwalk = picks.find(({ clip }) => clip === 'walk_2');
    assert.ok(Math.abs(catwalk.pace - 0.847 / 1.0945) < 1e-9);
    assert.equal(picks.find(({ clip }) => clip === 'walk').pace, 1);

    // The room walks her at its speed times the pace: the clip then plays at its own speed.
    const speed = 1.1 * catwalk.pace;
    assert.equal(idle.setWalking(true, speed, catwalk.clip), true);
    assert.equal(played.at(-1), 'walk_2');
    assert.ok(idle.isWalking());
    assert.ok(Math.abs(idle._vrmaPlayer.current.scale - 1.1 / 1.0945) < 1e-9, 'feet on the floor');
    idle.setWalking(true, speed, catwalk.clip);
    assert.equal(played.filter(name => name === 'walk_2').length, 1, 'one clip for the whole trip');
    idle.setWalking(false);
    assert.equal(idle.isWalking(), false);
});

test('rest clips space out and answer the moment: night yawns, unanswered questions, a happy mood', () => {
    const { idle } = fixture(new Set());
    idle.params = { ...(idle.params || {}), energy: 0.5 };
    const gaps = seeded(() => Array.from({ length: 200 }, () => idle._randomInterval()));
    assert.ok(Math.min(...gaps) >= 8 && Math.max(...gaps) <= 20, 'every 8 to 20 s at the default energy');

    let context = { quiet: 90, asked: null, hour: 15 };
    idle.restContext = () => context;
    assert.equal(idle._restBiasFor('idle_yawn', 0), 1);
    context = { quiet: 400, asked: null, hour: 1 };
    assert.equal(idle._restBiasFor('idle_yawn', 0), 6, 'late at night after a long quiet');
    context = { quiet: 20, asked: 30, hour: 15 };
    assert.equal(idle._restBiasFor('idle_waiting', 0), 6, 'her question waits for an answer');
    context = { quiet: 20, asked: null, hour: 15 };
    assert.ok(idle._restBiasFor('idle_waiting', 0) < 1, 'not while the talk is fresh');
    assert.equal(idle._restBiasFor('hair_touch', 0.6), 2);
    assert.equal(idle._restBiasFor('stretch', 0.6), 1);
    idle.restContext = null;
    assert.doesNotThrow(() => idle._restBiasFor('idle_yawn', 0));
});
