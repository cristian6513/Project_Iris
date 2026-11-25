/**
 * Iris's emotions, in one place (2026-11-24).
 *
 * The agent names her emotion with each reply (`emocion`). There were six; six more were added
 * for faces that can show them, MMD models above all (blush, heart or star eyes, a sweat drop,
 * ZZZ): avergonzada, cariñosa, emocionada, confundida, nerviosa and somnolienta. Each new one has
 * a `base` among the first six, which the body, gaze, breathing and particles fall back on where
 * they have no entry of their own, so their tuned behaviour carries over.
 *
 * The face itself (expression mixes, eyelids) is in pose_face.js; marks in avatar.js.
 */

export const EMOTIONS = Object.freeze({
    neutral:     Object.freeze({ base: 'neutral',     label: 'ÁNIMO',     energy: 40 }),
    feliz:       Object.freeze({ base: 'feliz',       label: 'ALEGRÍA',   energy: 75 }),
    triste:      Object.freeze({ base: 'triste',      label: 'TRISTEZA',  energy: 25 }),
    pensativa:   Object.freeze({ base: 'pensativa',   label: 'PENSANDO',  energy: 55 }),
    sorprendida: Object.freeze({ base: 'sorprendida', label: 'SORPRESA',  energy: 90 }),
    enojada:     Object.freeze({ base: 'enojada',     label: 'ENOJO',     energy: 95 }),
    // Shy: soft smile, eyes away (pensativa's wandering gaze), blush.
    avergonzada: Object.freeze({ base: 'pensativa',   label: 'VERGÜENZA', energy: 35 }),
    // Affectionate: warm smile, looking at you, heart eyes where the model has them.
    'cariñosa':  Object.freeze({ base: 'feliz',       label: 'CARIÑO',    energy: 60 }),
    // Excited: a big smile with surprise, star eyes.
    emocionada:  Object.freeze({ base: 'feliz',       label: 'EMOCIÓN',   energy: 95 }),
    // Confused: puzzled brows, a ？ mark.
    confundida:  Object.freeze({ base: 'pensativa',   label: 'CONFUSIÓN', energy: 50 }),
    // Nervous: an awkward smile, worried brows, a sweat drop.
    nerviosa:    Object.freeze({ base: 'pensativa',   label: 'NERVIOS',   energy: 70 }),
    // Sleepy: heavy eyelids, ZZZ.
    somnolienta: Object.freeze({ base: 'pensativa',   label: 'SUEÑO',     energy: 15 }),
});

export const EMOTION_NAMES = Object.freeze(Object.keys(EMOTIONS));

const plain = value => String(value ?? '').trim().toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
// Spellings an agent may use: without accents or ñ, in the masculine, or a close synonym.
const ALIASES = new Map([
    ...EMOTION_NAMES.map(name => [plain(name), name]),
    ['alegre', 'feliz'], ['contenta', 'feliz'], ['contento', 'feliz'],
    ['pensativo', 'pensativa'], ['sorprendido', 'sorprendida'], ['enojado', 'enojada'], ['molesta', 'enojada'],
    ['avergonzado', 'avergonzada'], ['timida', 'avergonzada'], ['timido', 'avergonzada'],
    ['carinoso', 'cariñosa'], ['tierna', 'cariñosa'], ['tierno', 'cariñosa'],
    ['emocionado', 'emocionada'], ['entusiasmada', 'emocionada'], ['entusiasmado', 'emocionada'],
    ['confundido', 'confundida'], ['confusa', 'confundida'], ['confuso', 'confundida'],
    ['nervioso', 'nerviosa'], ['ansiosa', 'nerviosa'], ['ansioso', 'nerviosa'],
    ['somnoliento', 'somnolienta'], ['dormida', 'somnolienta'], ['dormido', 'somnolienta'], ['con sueno', 'somnolienta'],
]);

/** The emotion's canonical name, or 'neutral' for anything unknown. */
export function canonicalEmotion(name) {
    return ALIASES.get(plain(name)) ?? 'neutral';
}

/** One of the first six: what the body, gaze, breathing and particles do for this emotion. */
export function emotionBase(name) {
    return EMOTIONS[canonicalEmotion(name)].base;
}

// What the avatar's face can show, in the user's words (the avatar panel lists it, 2026-11-24).
const GESTURE_LABELS = Object.freeze([
    ['blink', 'parpadeo'], ['happy', 'feliz'], ['sad', 'triste'], ['angry', 'enojo'], ['surprised', 'sorpresa'],
    ['relaxed', 'calma'], ['blush', 'rubor'], ['heartEyes', 'ojos de corazón'], ['starEyes', 'ojos de estrella'],
    ['sweat', 'sudor'], ['zzz', 'ZZZ'], ['dubious', 'duda'], ['smallPupils', 'pupilas chicas'],
    ['smirk', 'sonrisa de lado'],
    ['markSurprise', '«！»'], ['markThink', '«？»'], ['markDots', '«……»'],
]);
const VOWELS = ['aa', 'ih', 'ou', 'ee', 'oh'];

/** The avatar's expression names as labels: vowels together, then the rest in a fixed order. */
export function faceGestures(names) {
    const have = new Set(names ?? []);
    const vowels = VOWELS.filter(name => have.has(name)).length;
    const labels = [];
    if (have.has('blink')) labels.push('parpadeo');
    if (vowels === VOWELS.length) labels.push('vocales');
    else if (vowels) labels.push('boca');
    for (const [name, label] of GESTURE_LABELS) if (name !== 'blink' && have.has(name)) labels.push(label);
    // MMD's wink morph, or a VRM's separate eyelids.
    if (have.has('wink') || have.has('blinkRight') || have.has('blinkLeft')) labels.push('guiño');
    return labels;
}
