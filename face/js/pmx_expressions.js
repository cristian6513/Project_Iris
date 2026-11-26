/**
 * Iris's expressions on MMD (PMX) models.
 *
 * VRM models ship whole-face expressions (happy, sad...). MMD models split the face into brows,
 * eyes and mouth morphs, named by a common Japanese convention. Taking one morph per emotion left
 * most emotions on the brows alone (にこり, 困る, 怒り), which the fringe often hides: on the test
 * models (2026-11-24) the emotions were set at full strength and barely showed. Each emotion is
 * now a recipe: brows, eyes and mouth, each part taking the first morph (or left/right pair) the
 * model has, at its own weight, merged into one vertex morph. The MMD loader keeps a dense copy of
 * the mesh for every morph, so Iris keeps only these.
 *
 * Where the model has them, emotions also get MMD's accents: a soft blush when happy, teary eyes
 * when sad, a pout when angry, and a ！ or ？ mark that pops up when she is surprised or thinking.
 *
 * Names are compared after NFKC and lower case: にこり２ matches にこり2 and ｷﾘｯ matches キリッ.
 * English names at the end of a part are for models authored with VRM-style morphs. Models taken
 * from games keep the game's own names (B_ brows, E_ eyes, M_ mouth: B_Happy, E_Close, M_Smile_L):
 * measured on one on 2026-11-25, where only the vowels matched and she never blinked.
 */

const one = (...names) => names.map(name => [name]);

// role → parts: { weight, options, overlay, key, closes, mouth }. `mouth` parts go into a second morph,
// `<role>Mouth`, which gives way while she speaks (pmx_avatar.js), so a frown or a pout does not fight
// the vowels; brows and eyes stay whole.
// An option is a list of morphs used
// together (a left/right pair); the first option whose morphs all exist wins. Without a `key` part the
// role is not built at all. Weights may be negative, to take back what another role adds. `closes`:
// the part closes the eyes, and its weight is calibrated to the model (eyeClosure below). `overlay` parts move pieces laid
// over the face (blush, tears, marks): their materials are checked for transparency (pmx_material.js).
export const PMX_EXPRESSION_RECIPES = Object.freeze({
    // Each list: the Japanese MMD name first, then Chinese (simplified and traditional, common on
    // Aplaybox), then English and VRoid's «Fcl_…» names (models converted from VRoid). A whole-face
    // morph (微笑, smile, Fcl_ALL_Joy) sits in one part only, so it is never added twice.
    blink: [{ weight: 1, options: one('まばたき', '眨眼', '闭眼', '閉眼', 'blink', 'fcl_eye_close', 'eye_close', 'e_close') }],
    aa: [{ weight: 1, options: one('あ', '啊', '阿', 'aa', 'a', 'fcl_mth_a') }],
    ih: [{ weight: 1, options: one('い', '咿', '伊', 'ih', 'i', 'fcl_mth_i') }],
    ou: [{ weight: 1, options: one('う', '呜', '嗚', '乌', 'ou', 'u', 'fcl_mth_u') }],
    ee: [{ weight: 1, options: one('え', '诶', '欸', 'ee', 'e', 'fcl_mth_e') }],
    oh: [{ weight: 1, options: one('お', '哦', '噢', 'oh', 'o', 'fcl_mth_o') }],
    happy: [
        { weight: 0.8, options: one('にこり', 'にこり2', '开心', '開心', '高兴', '高興', '微笑', 'happy', 'joy', 'smile', 'fcl_all_joy', 'b_happy') },
        { weight: 0.7, closes: 0.9, options: one('笑い', '笑い目', '喜び', '笑眼', '眯眼笑', 'fcl_eye_joy', 'e_blephar') },
        { weight: 0.9, mouth: true, options: [['口角上げ'], ['口角上げ左', '口角上げ右'], ['右口角上げ', '左口角上げ'], ['にやり'], ['嘴角上扬'], ['嘴角上揚'], ['fcl_mth_joy'], ['m_smile_l', 'm_smile_r']] },
        { weight: 0.35, overlay: true, options: one('照れ', '照れ2', 'facered', '脸红', '臉紅', '红晕', '紅暈') },   // a soft blush
    ],
    sad: [
        { weight: 0.9, options: one('困る', '悲し', '难过', '難過', '伤心', '傷心', '悲伤', '悲傷', 'sad', 'sorrow', 'fcl_all_sorrow', 'b_sad') },
        { weight: 0.6, options: one('悲しい目', 'e_sad', 'たれ目', '垂れ目', '垂眼', 'fcl_eye_sorrow') },
        { weight: 0.7, mouth: true, options: [['口角下げ'], ['口角下げ左', '口角下げ右'], ['右口角下げ', '左口角下げ'], ['∧'], ['嘴角下垂'], ['fcl_mth_sorrow'], ['m_ennui_l', 'm_ennui_r']] },
        { weight: 0.6, overlay: true, options: one('涙', '泪', '眼泪', '眼淚', '流泪', '流淚', 'tear', 'tears') },   // teary eyes
    ],
    angry: [
        { weight: 0.9, options: one('怒り', '生气', '生氣', '愤怒', '憤怒', '怒眉', 'angry', 'fcl_all_angry', 'b_anger') },
        { weight: 0.5, options: one('キリッ', 'じと目', '眯眼', 'fcl_eye_angry', 'e_anger') },
        { weight: 0.6, mouth: true, options: [['∧'], ['口角下げ'], ['口角下げ左', '口角下げ右'], ['右口角下げ', '左口角下げ'], ['嘴角下垂'], ['fcl_mth_angry'], ['m_anger'], ['m_ennui_l', 'm_ennui_r']] },
        // A pout: puffed cheeks (もぐもぐ, «chewing», puffs them too), or pouting lips.
        { weight: 0.6, mouth: true, options: one('頬を膨', 'ぷくー', 'もぐもぐ', '鼓腮', '鼓嘴', '(○｀ε´○)', '嘟嘴', 'pout', 'puff') },
    ],
    surprised: [
        { weight: 0.85, options: one('びっくり', '見開く', '惊讶', '驚訝', '吃惊', '吃驚', '瞪眼', 'surprised', 'surprise', 'fcl_all_surprised', 'e_stare') },
        { weight: 0.8, options: [['上'], ['上左', '上右'], ['眉毛上'], ['挑眉'], ['fcl_brw_surprised'], ['b_up_add']] },
        { weight: 0.4, mouth: true, options: one('お', 'ワ', 'あ', '哦', 'o') },
    ],
    relaxed: [
        { weight: 0.6, closes: 1, options: one('なごみ', '放松', '放鬆', 'relaxed', 'fcl_all_fun') },
        { weight: 0.35, closes: 0.9, options: one('笑い', '笑い目', '笑眼', 'fcl_eye_joy', 'e_blephar') },
        { weight: 0.25, mouth: true, options: [['口角上げ'], ['口角上げ左', '口角上げ右'], ['右口角上げ', '左口角上げ'], ['嘴角上扬'], ['嘴角上揚'], ['m_smile_l', 'm_smile_r']] },
    ],
    // Marks that float by the head for a moment when an emotion arrives (avatar.js EMOTION_MARKS):
    // ！ when she is surprised, ？ (or ……) when she thinks. NFKC turns ！？ into !?.
    markSurprise: [{ weight: 1, overlay: true, options: one('!', 'びっくりマーク', '感叹号', '感嘆號', '惊叹号') }],
    markThink: [{ weight: 1, overlay: true, options: one('?', '……', 'はてな', '问号', '問號', '疑问', '疑問') }],
    markDots: [{ weight: 1, overlay: true, options: one('……', '...', '…', '省略号', '無語', '无语') }],
    // Channels of the newer emotions (pose_face.js VRM_EMOTION_PRESETS): one gesture each, for the
    // mixes to dose. Faces without them simply skip them.
    blush: [{ weight: 1, overlay: true, options: one('照れ', '照れ2', 'facered', '頬染め', '脸红', '臉紅', '害羞', '红晕', '紅暈', 'blush') }],
    // Heart and star eyes need open eyes: they take back the smile's eye closing (笑い) that cariñosa
    // (happy 0.6) and emocionada (happy 1) bring, and exist only with the eye shape itself (`key`).
    heartEyes: [
        { weight: 1, key: true, overlay: true, options: one('はぁと', 'はぁと2', 'ハート', 'ハート目', '爱心眼', '愛心眼', '桃心眼', '心眼', 'heart', 'hearteyes') },
        { weight: -0.45, closes: 0.9, options: one('笑い', '笑い目', '笑眼', 'fcl_eye_joy') },
    ],
    starEyes: [
        { weight: 1, key: true, overlay: true, options: one('星目', '星目2', 'キラキラ', '星星眼', '星眼', '闪亮', '閃亮', 'star', 'stareyes') },
        { weight: -0.7, closes: 0.9, options: one('笑い', '笑い目', '笑眼', 'fcl_eye_joy') },
    ],
    sweat: [{ weight: 1, overlay: true, options: one('汗', '冷や汗', '汗1', '流汗', '冷汗', 'sweat') }],
    zzz: [{ weight: 1, overlay: true, options: one('zzz', 'すやすや', '睡觉', '睡覺', '睡眠', 'sleep') }],
    // One brow, not both (2026-11-24): a single raised or puzzled brow reads as doubt.
    dubious: [{ weight: 0.9, options: [['怪訝右'], ['上右'], ['右上'], ['怪訝左'], ['上左'], ['左上'], ['怪訝'], ['疑惑'], ['困惑'], ['困る'], ['fcl_brw_sorrow'], ['b_ah_r']] }],
    // One mouth corner up: the awkward, lopsided smile of nerviosa.
    smirk: [{ weight: 1, mouth: true, options: one('右口角上げ', '口角上げ右', '口角上げ2右', '左口角上げ', '口角上げ左', '口角上げ2左', 'にやり', '嘴角上扬右', '嘴角上扬左', 'm_smile_r') }],
    // One eye shut for a moment (avatar.js EMOTION_PULSES): cariñosa winks.
    wink: [{ weight: 1, options: one('ウィンク', 'ウィンク２', 'ウィンク右', 'ｳｨﾝｸ２右', '眨左眼', '眨右眼', '单眨眼', 'wink', 'fcl_eye_close_r', 'fcl_eye_close_l') }],
    smallPupils: [{ weight: 1, options: one('瞳小', '瞳小さ', '瞳孔缩小', '瞳孔縮小', '小瞳', 'fcl_eye_small', 'pupil_scale') }],
});

// The relaxed recipe takes なごみ or, without it, softly smiling eyes: one eye shape, not both.
const EXCLUSIVE = Object.freeze({ relaxed: [[0, 1]] });

// Expressions that close the eyes on their own: the blink fades while they show (VRM's
// overrideBlink 'blend'), or a blink on top of smiling eyes would push the lids through.
export const PMX_EYE_CLOSING = Object.freeze(new Set(['happy', 'relaxed']));

export const normalizeMorphName = name => String(name ?? '').normalize('NFKC').toLowerCase();
// The second morph of a role with mouth parts: `happyMouth` beside `happy`.
export const MOUTH_SUFFIX = 'Mouth';

// While she speaks, an emotion's mouth (frown, pout, smile corners) keeps this share of itself at
// full speech: the vowels lead, as VRM's overrideMouth 'blend' does. A frown on top of «a» and «e»
// stretched some mouths into a box (Pearl, 2026-11-24).
export const MOUTH_KEPT_SPEAKING = 0.3;
const SPEECH_FULL = 0.35;

/** The weight of an emotion's mouth morph when the vowels are at `speaking` (0..1, the largest). */
export function emotionMouthWeight(speaking) {
    const u = Math.min(1, Math.max(0, (Number.isFinite(speaking) ? speaking : 0) / SPEECH_FULL));
    return 1 - (1 - MOUTH_KEPT_SPEAKING) * u * u * (3 - 2 * u);
}

/** A morph as vertex deltas: vertex morphs as they are, group morphs flattened (one level). */
function vertexElements(morph, morphs) {
    if (morph.type === 1) return morph.elements;
    if (morph.type !== 0) return [];
    return morph.elements.flatMap(({ index, ratio }) => {
        const child = morphs[index];
        if (child?.type !== 1) return [];
        return child.elements.map(({ index: vertex, position }) => ({ index: vertex, position: position.map(v => v * ratio) }));
    });
}

/**
 * Replace the parsed model's morphs with Iris's roles, each one vertex morph. Returns `used` (per
 * role, the morph names it was made from, for logs and tests) and `overlayVertices` (the vertices
 * the overlay parts move).
 */
export function selectExpressions(data) {
    const morphs = data.morphs ?? [];
    const byName = new Map();
    for (const morph of morphs) {
        const key = normalizeMorphName(morph.name);
        if (!byName.has(key) && (morph.type === 0 || morph.type === 1)) byName.set(key, morph);
    }
    const selected = [];
    const used = {};
    const overlayVertices = new Set();
    const calibration = {};
    const closure = eyeClosure(data, byName, morphs);
    for (const [role, parts] of Object.entries(PMX_EXPRESSION_RECIPES)) {
        const sum = new Map(), mouthSum = new Map();
        const names = [];
        const chosen = parts.map(({ options }) => options.find(option => option.every(name => byName.has(normalizeMorphName(name)))));
        for (const [a, b] of EXCLUSIVE[role] ?? []) if (chosen[a] && chosen[b]) chosen[b] = undefined;
        if (parts.some(({ key }, i) => key && !chosen[i])) continue;
        parts.forEach(({ weight: authored, overlay, closes, mouth }, i) => {
            const into = mouth ? mouthSum : sum;
            const option = chosen[i];
            if (!option) return;
            for (const name of option) {
                const morph = byName.get(normalizeMorphName(name));
                names.push(morph.name);
                // How much this model's morph shuts the eyes, against the reference (Lily's 笑い
                // closes 0.9 of a blink): a stronger one is given less weight, a weaker one more.
                let weight = authored;
                const ratio = closes ? closure?.(morph) : null;
                if (ratio > 0.1) {
                    const scale = Math.min(CALIBRATION_MAX, Math.max(CALIBRATION_MIN, closes / ratio));
                    weight *= scale;
                    calibration[morph.name] = +scale.toFixed(2);
                }
                for (const { index, position } of vertexElements(morph, morphs)) {
                    if (overlay) overlayVertices.add(index);
                    const at = into.get(index) ?? [0, 0, 0];
                    at[0] += position[0] * weight; at[1] += position[1] * weight; at[2] += position[2] * weight;
                    into.set(index, at);
                }
            }
        });
        if (!sum.size && !mouthSum.size) continue;
        for (const [name, part] of [[role, sum], [role + MOUTH_SUFFIX, mouthSum]]) {
            if (!part.size) continue;
            const elements = [...part].map(([index, position]) => ({ index, position }));
            selected.push({ name, type: 1, panel: 4, elements, elementCount: elements.length });
        }
        used[role] = names;
    }
    data.morphs = selected;
    if (data.metadata) data.metadata.morphCount = selected.length;
    return { used, overlayVertices, calibration };
}

const CALIBRATION_MIN = 0.5, CALIBRATION_MAX = 1.5;
const EYE_BONES = Object.freeze({ left: ['左目', '左眼', 'eye_l', 'lefteye', 'left eye'], right: ['右目', '右眼', 'eye_r', 'righteye', 'right eye'] });

/**
 * Per-model calibration (2026-11-24): the same weight shut Feixue's eyes (her 笑い closes 1.05 of a
 * blink) and left Lily's half open (0.9). Around each eye bone, a morph's closure is how far it
 * brings the upper lid down plus the lower lid up; divided by the blink's, it says how much of the
 * eye it shuts at full weight. Returns that function, or null when the model has no eye bones or
 * no blink to measure against.
 */
export function eyeClosure(data, byName, morphs) {
    const bones = data.bones ?? [], vertices = data.vertices ?? [];
    const find = names => bones.find(bone => names.includes(normalizeMorphName(bone.name)))?.position;
    const eyes = [find(EYE_BONES.left), find(EYE_BONES.right)].filter(Boolean);
    const blink = byName.get(normalizeMorphName('まばたき')) ?? byName.get('blink') ?? byName.get('眨眼') ?? byName.get('fcl_eye_close')
        ?? byName.get('e_close');
    if (eyes.length !== 2 || !blink || !vertices.length) return null;
    const gap = Math.abs(eyes[0][0] - eyes[1][0]);
    if (!(gap > 0)) return null;
    const reach = gap * 0.45, height = gap * 0.4;
    const shut = morph => {
        let total = 0;
        for (const eye of eyes) {
            let down = 0, up = 0;
            for (const { index, position } of vertexElements(morph, morphs)) {
                const at = vertices[index]?.position;
                if (!at || Math.abs(at[0] - eye[0]) > reach || Math.abs(at[1] - eye[1]) > height) continue;
                if (at[1] >= eye[1]) down = Math.max(down, -position[1]);
                else up = Math.max(up, position[1]);
            }
            total += down + up;
        }
        return total / eyes.length;
    };
    const full = shut(blink);
    if (!(full > 0)) return null;
    const cache = new Map();
    return morph => {
        if (!cache.has(morph)) cache.set(morph, shut(morph) / full);
        return cache.get(morph);
    };
}
