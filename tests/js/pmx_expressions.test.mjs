// Iris's emotions on MMD models (2026-11-24): one brow morph per emotion barely showed under the
// fringe, so each emotion now merges brows, eyes and mouth from the morphs the model has.
import assert from 'node:assert/strict';
import test from 'node:test';
import { PMX_EYE_CLOSING, PMX_EXPRESSION_RECIPES, normalizeMorphName, selectExpressions } from '../../face/js/pmx_expressions.js';

// A vertex morph moving one vertex of its own by (1, 0, 0), so merged weights can be read back.
let next = 0;
const vertex = (name, index = next++) => ({ name, type: 1, elements: [{ index, position: [1, 0, 0] }] });
const model = (...morphs) => ({ morphs, metadata: { morphCount: morphs.length } });
const role = (data, name) => data.morphs.find((m) => m.name === name);
const delta = (morph, index) => morph.elements.find((e) => e.index === index)?.position[0];

test('happy merges brows, eyes and mouth at their own weights', () => {
    const brows = vertex('にこり'), eyes = vertex('笑い'), mouth = vertex('にやり');
    const data = model(brows, eyes, mouth, vertex('まばたき'));
    const used = selectExpressions(data).used;
    assert.deepEqual(used.happy, ['にこり', '笑い', 'にやり']);
    const happy = role(data, 'happy');
    assert.equal(happy.type, 1);
    assert.equal(happy.elementCount, 2);
    assert.ok(Math.abs(delta(happy, brows.elements[0].index) - 0.8) < 1e-9);
    assert.ok(Math.abs(delta(happy, eyes.elements[0].index) - 0.7) < 1e-9);
    // The mouth is a second morph, which gives way while she speaks (pmx_avatar.js).
    const happyMouth = role(data, 'happyMouth');
    assert.equal(happyMouth.elementCount, 1);
    assert.ok(Math.abs(delta(happyMouth, mouth.elements[0].index) - 0.9) < 1e-9);
    assert.equal(delta(happy, mouth.elements[0].index), undefined);
    assert.equal(data.metadata.morphCount, data.morphs.length);
});

test('a left/right pair counts as one part, and the first option the model has wins', () => {
    const data = model(vertex('怒り'), vertex('じと目'), vertex('口角上げ左'), vertex('口角上げ右'), vertex('にやり'), vertex('にこり'));
    const used = selectExpressions(data).used;
    assert.deepEqual(used.happy, ['にこり', '口角上げ左', '口角上げ右']);
    assert.deepEqual(used.angry, ['怒り', 'じと目']);
});

test('names match after NFKC: half-width katakana and full-width digits', () => {
    assert.equal(normalizeMorphName('ｷﾘｯ'), normalizeMorphName('キリッ'));
    const data = model(vertex('怒り'), vertex('ｷﾘｯ'), vertex('にこり２'));
    const used = selectExpressions(data).used;
    assert.deepEqual(used.angry, ['怒り', 'ｷﾘｯ']);
    assert.deepEqual(used.happy, ['にこり２']);
});

test('group morphs are flattened into their vertex deltas', () => {
    const child = vertex('child');
    const data = model(child, { name: 'びっくり', type: 0, elements: [{ index: 0, ratio: 0.5 }] });
    const used = selectExpressions(data).used;
    assert.deepEqual(used.surprised, ['びっくり']);
    assert.ok(Math.abs(delta(role(data, 'surprised'), child.elements[0].index) - 0.85 * 0.5) < 1e-9);
});

test('relaxed takes なごみ or soft smiling eyes, never both', () => {
    const withNagomi = model(vertex('なごみ'), vertex('笑い'));
    assert.deepEqual(selectExpressions(withNagomi).used.relaxed, ['なごみ']);
    const without = model(vertex('笑い'));
    assert.deepEqual(selectExpressions(without).used.relaxed, ['笑い']);
});

test('VRM-style English morphs still work, other morph kinds and unknown names are dropped', () => {
    const data = model(vertex('happy'), vertex('blink'), { name: '怒り', type: 8, elements: [] }, vertex('ぺろっ'));
    const used = selectExpressions(data).used;
    assert.deepEqual(used.happy, ['happy']);
    assert.deepEqual(used.blink, ['blink']);
    assert.equal(used.angry, undefined);
    assert.deepEqual(data.morphs.map((m) => m.name).sort(), ['blink', 'happy']);
    const empty = model(vertex('ぺろっ'));
    selectExpressions(empty);
    assert.equal(empty.metadata.morphCount, 0);
});

test('the recipes cover every channel Iris drives, and eye-closing ones yield the blink', () => {
    for (const name of ['blink', 'aa', 'ih', 'ou', 'ee', 'oh', 'happy', 'sad', 'angry', 'surprised', 'relaxed']) {
        assert.ok(PMX_EXPRESSION_RECIPES[name]?.length, name);
    }
    assert.ok(PMX_EYE_CLOSING.has('happy') && PMX_EYE_CLOSING.has('relaxed') && !PMX_EYE_CLOSING.has('blink'));
});

// 2026-11-24, the user's pick: MMD's accents where a model has them.
test('accents join their emotion: blush when happy, tears when sad, a pout when angry', () => {
    const data = model(vertex('にこり'), vertex('照れ'), vertex('困る'), vertex('涙'), vertex('怒り'), vertex('頬を膨'));
    const used = selectExpressions(data).used;
    assert.ok(used.happy.includes('照れ'));
    assert.ok(used.sad.includes('涙'));
    assert.ok(used.angry.includes('頬を膨'));
    const plain = model(vertex('にこり'), vertex('困る'));
    const bare = selectExpressions(plain).used;
    assert.deepEqual(bare.happy, ['にこり']);
    assert.deepEqual(bare.sad, ['困る']);
});

test('puffed cheeks win over pouting lips for the angry pout (もぐもぐ on Lily, 2026-11-24)', () => {
    const used = selectExpressions(model(vertex('怒り'), vertex('(○｀ε´○)'), vertex('もぐもぐ'))).used;
    assert.ok(used.angry.includes('もぐもぐ') && !used.angry.includes('(○｀ε´○)'));
});

test('star and heart eyes open the smile back up, and only exist with the eye shape itself', () => {
    const smile = vertex('笑い');
    const data = model(vertex('にこり'), smile, vertex('星目'), vertex('はぁと'));
    const used = selectExpressions(data).used;
    assert.deepEqual(used.starEyes, ['星目', '笑い']);
    assert.deepEqual(used.heartEyes, ['はぁと', '笑い']);
    const star = data.morphs.find((m) => m.name === 'starEyes');
    assert.ok(Math.abs(delta(star, smile.elements[0].index) + 0.7) < 1e-9, "the smile's eye closing is taken back");
    const noStars = selectExpressions(model(vertex('笑い'))).used;
    assert.equal(noStars.starEyes, undefined);
    assert.equal(noStars.heartEyes, undefined);
});

test('a model with Chinese names (Aplaybox) gets its emotions too', () => {
    const used = selectExpressions(model(vertex('眨眼'), vertex('啊'), vertex('咿'), vertex('呜'), vertex('诶'), vertex('哦'),
        vertex('微笑'), vertex('笑眼'), vertex('生氣'), vertex('难过'), vertex('惊讶'), vertex('脸红'), vertex('星星眼'), vertex('流汗'))).used;
    assert.deepEqual(used.blink, ['眨眼']);
    assert.deepEqual([used.aa, used.ih, used.ou, used.ee, used.oh].map((n) => n[0]), ['啊', '咿', '呜', '诶', '哦']);
    assert.deepEqual(used.happy, ['微笑', '笑眼', '脸红']);
    assert.deepEqual(used.angry, ['生氣']);
    assert.deepEqual(used.sad, ['难过']);
    assert.ok(used.surprised.includes('惊讶'));
    assert.deepEqual(used.blush, ['脸红']);
    assert.deepEqual(used.starEyes, ['星星眼', '笑眼']);
    assert.deepEqual(used.sweat, ['流汗']);
});

test('a model taken from a game (B_ brows, E_ eyes, M_ mouth) blinks and gets its emotions', () => {
    // 2026-11-25: only its vowels matched, so it never blinked and its emotions barely showed.
    const used = selectExpressions(model(vertex('E_Close'), vertex('あ'), vertex('B_Happy'), vertex('E_Blephar'),
        vertex('M_Smile_L'), vertex('M_Smile_R'), vertex('B_Sad'), vertex('E_Sad'), vertex('垂れ目'), vertex('M_Ennui_L'),
        vertex('M_Ennui_R'), vertex('B_Anger'), vertex('E_Anger'), vertex('M_Anger'), vertex('E_Stare'), vertex('B_Up_Add'),
        vertex('お'), vertex('B_AH_R'), vertex('Pupil_Scale'))).used;
    assert.deepEqual(used.blink, ['E_Close']);
    assert.deepEqual(used.happy, ['B_Happy', 'E_Blephar', 'M_Smile_L', 'M_Smile_R']);
    // Its own sad eyes before the generic droopy ones.
    assert.deepEqual(used.sad, ['B_Sad', 'E_Sad', 'M_Ennui_L', 'M_Ennui_R']);
    assert.deepEqual(used.angry, ['B_Anger', 'E_Anger', 'M_Anger']);
    assert.deepEqual(used.surprised, ['E_Stare', 'B_Up_Add', 'お']);
    assert.deepEqual(used.relaxed, ['E_Blephar', 'M_Smile_L', 'M_Smile_R']);
    assert.deepEqual(used.dubious, ['B_AH_R']);
    assert.deepEqual(used.smirk, ['M_Smile_R']);
    assert.deepEqual(used.smallPupils, ['Pupil_Scale']);
});

test('a model converted from VRoid (Fcl_… names) gets its emotions too', () => {
    const used = selectExpressions(model(vertex('Fcl_EYE_Close'), vertex('Fcl_MTH_A'), vertex('Fcl_MTH_I'), vertex('Fcl_ALL_Joy'),
        vertex('Fcl_ALL_Angry'), vertex('Fcl_ALL_Sorrow'), vertex('Fcl_ALL_Surprised'), vertex('Fcl_ALL_Fun'))).used;
    assert.deepEqual(used.blink, ['Fcl_EYE_Close']);
    assert.deepEqual(used.aa, ['Fcl_MTH_A']);
    assert.deepEqual(used.happy, ['Fcl_ALL_Joy']);
    assert.deepEqual(used.angry, ['Fcl_ALL_Angry']);
    assert.deepEqual(used.sad, ['Fcl_ALL_Sorrow']);
    assert.deepEqual(used.surprised, ['Fcl_ALL_Surprised']);
    assert.deepEqual(used.relaxed, ['Fcl_ALL_Fun']);
});

test('the ！ and ？ marks, full-width or not, become markSurprise and markThink', () => {
    const wide = selectExpressions(model(vertex('！'), vertex('？'))).used;
    assert.deepEqual(wide.markSurprise, ['！']);
    assert.deepEqual(wide.markThink, ['？']);
    const narrow = selectExpressions(model(vertex('!'), vertex('……'))).used;
    assert.deepEqual(narrow.markSurprise, ['!']);
    assert.deepEqual(narrow.markThink, ['……']);
});

const avatarSource = (await import('node:fs')).readFileSync(new URL('../../face/js/avatar.js', import.meta.url), 'utf8');

test('the marks avatar.js pops are the ones the PMX recipes build', () => {
    const block = avatarSource.match(/export const EMOTION_MARKS = Object\.freeze\(\{([^}]*)\}\)/)?.[1];
    assert.ok(block, 'EMOTION_MARKS found in avatar.js');
    const names = [...new Set([...block.matchAll(/:\s*'([A-Za-z]+)'/g)].map((m) => m[1]))];
    assert.deepEqual(names.sort(), ['markSurprise', 'markThink']);
    for (const name of names) assert.ok(PMX_EXPRESSION_RECIPES[name], name);
    assert.match(avatarSource, /const mark = EMOTION_MARKS\[name\];/);
});

test('a mark pops in fast, holds, fades and is gone after 1.6 s', async () => {
    const start = avatarSource.indexOf('const MARK_SECONDS');
    const end = avatarSource.indexOf('\n}\n', avatarSource.indexOf('export function markLevel')) + 3;
    const { markLevel } = await import('data:text/javascript;base64,' + Buffer.from(avatarSource.slice(start, end)).toString('base64'));
    assert.equal(markLevel(-0.1), 0);
    assert.ok(markLevel(0.06) > 0.4 && markLevel(0.06) < 0.6);
    assert.equal(markLevel(0.5), 1);
    assert.ok(markLevel(1.4) > 0 && markLevel(1.4) < 1);
    assert.equal(markLevel(1.6), 0);
    assert.equal(markLevel(Number.NaN), 0);
});

// 2026-11-24, per-model calibration: how much a morph shuts the eyes is measured against the blink.
function eyeModel(smileClosure) {
    // Two eyes at x = ±1, y = 10; per eye an upper-lid vertex (y 10.2) and a lower-lid one (y 9.8).
    const vertices = [[-1, 10.2], [-1, 9.8], [1, 10.2], [1, 9.8], [0, 5]].map(([x, y]) => ({ position: [x, y, 0] }));
    const lids = (down, up) => [0, 2].flatMap((v) => [{ index: v, position: [0, -down, 0] }, { index: v + 1, position: [0, up, 0] }]);
    return {
        bones: [{ name: '左目', position: [1, 10, 0] }, { name: '右目', position: [-1, 10, 0] }],
        vertices,
        morphs: [
            { name: 'まばたき', type: 1, elements: lids(0.3, 0.1) },               // a full blink: 0.4
            { name: '笑い', type: 1, elements: lids(smileClosure * 0.2, smileClosure * 0.2) },
            { name: '星目', type: 1, elements: [{ index: 4, position: [0, 0, 1] }] },
            { name: 'にこり', type: 1, elements: [{ index: 4, position: [1, 0, 0] }] },
        ],
        metadata: {},
    };
}

test('a smile that shuts the eyes more than the reference gets less weight, and its undoing follows', () => {
    const strong = eyeModel(1.8);          // 笑い closes 1.8 of a blink: twice the reference (0.9)
    const { calibration } = selectExpressions(strong);
    assert.equal(calibration['笑い'], 0.5);
    const happy = strong.morphs.find((m) => m.name === 'happy');
    const star = strong.morphs.find((m) => m.name === 'starEyes');
    const lidDown = (morph) => morph.elements.find((e) => e.index === 0).position[1];
    assert.ok(Math.abs(lidDown(happy) - (-0.36 * 0.7 * 0.5)) < 1e-9, 'happy eyes: 0.7 × 0.5');
    assert.ok(Math.abs(lidDown(star) - (0.36 * 0.7 * 0.5)) < 1e-9, 'star eyes take back the same amount');
    const weak = eyeModel(0.3);
    assert.equal(selectExpressions(weak).calibration['笑い'], 1.5, 'capped');
    const reference = eyeModel(0.9);
    assert.equal(selectExpressions(reference).calibration['笑い'], 1);
});

test('without eye bones there is nothing to measure, and nothing changes', () => {
    const data = eyeModel(1.8);
    data.bones = [];
    const { calibration } = selectExpressions(data);
    assert.deepEqual(calibration, {});
});

test('one-sided gestures: one doubting brow, a lopsided smile, a wink', () => {
    const used = selectExpressions(model(vertex('怪訝右'), vertex('怪訝左'), vertex('上右'), vertex('右口角上げ'),
        vertex('左口角上げ'), vertex('ウィンク'), vertex('ウィンク右'))).used;
    assert.deepEqual(used.dubious, ['怪訝右']);
    assert.deepEqual(used.smirk, ['右口角上げ']);
    assert.deepEqual(used.wink, ['ウィンク']);
    assert.ok(avatarSource.includes("export const EMOTION_PULSES = Object.freeze({ 'cariñosa'"));
    assert.match(avatarSource, /names: \['wink', 'blinkRight', 'blinkLeft'\]/);
});

test('while she speaks, an emotion\'s mouth gives way to the vowels, smoothly', async () => {
    const { emotionMouthWeight, MOUTH_KEPT_SPEAKING } = await import('../../face/js/pmx_expressions.js');
    assert.equal(emotionMouthWeight(0), 1);
    assert.ok(emotionMouthWeight(0.1) < 1 && emotionMouthWeight(0.1) > 0.7);
    assert.ok(Math.abs(emotionMouthWeight(0.35) - MOUTH_KEPT_SPEAKING) < 1e-9);
    assert.ok(Math.abs(emotionMouthWeight(1) - MOUTH_KEPT_SPEAKING) < 1e-9);
    assert.equal(emotionMouthWeight(Number.NaN), 1);
    const loader = (await import('node:fs')).readFileSync(new URL('../../face/js/pmx_avatar.js', import.meta.url), 'utf8');
    assert.match(loader, /for \(const bind of mouthBinds\) bind\.weight = weight;/);
});
