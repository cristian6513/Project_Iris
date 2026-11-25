/**
 * Vowel shapes for her mouth while she speaks (2026-11-24).
 *
 * Lip sync used to give one number, how open the mouth is, and every avatar said «a» to everything
 * (with a little «o»). VRM and MMD models both have the five vowel shapes (aa ih ou ee oh; MMD's
 * あいうえお). The text she says is turned into a track of units: each vowel is a shape, m/b/p close
 * the lips, other consonants lean towards the next vowel half open, spaces and punctuation are
 * pauses. With word timings (Edge TTS) each word's units share its duration; without them the
 * whole sentence is laid over the audio's length. The mouth's openness still comes from the word
 * timing or the audio level; this only says which shape it takes.
 *
 * Spanish spelling first (á→a, the silent u of que/qui/gue/gui, y as a vowel), and any language
 * with Latin letters gets a reasonable approximation.
 */

export const VISEMES = Object.freeze(['aa', 'ih', 'ou', 'ee', 'oh']);

const SHAPES = Object.freeze({
    a: Object.freeze({ aa: 1 }),
    e: Object.freeze({ ee: 0.85, aa: 0.15 }),
    i: Object.freeze({ ih: 0.9 }),
    o: Object.freeze({ oh: 0.9, aa: 0.1 }),
    u: Object.freeze({ ou: 0.9 }),
    closed: Object.freeze({ aa: 0.15 }),
});
// How long each kind of unit lasts, relative to a vowel, and how open the mouth is in it.
const UNIT = Object.freeze({
    vowel: { weight: 1, open: 1 },
    consonant: { weight: 0.55, open: 0.45 },
    closed: { weight: 0.6, open: 0.08 },
    space: { weight: 0.35, open: 0 },
    pause: { weight: 1.6, open: 0 },
});
const BLEND = 0.35;   // the share of a unit over which it fades into the next one

// Accents go; the diaeresis stays a moment, so ü can keep its sound.
const strip = text => String(text ?? '').toLowerCase().normalize('NFD').replace(/[̀-̇̉-ͯ]/g, '');

/** The text as units: { kind, shape, weight, open }. */
export function vowelUnits(text) {
    // ü keeps its sound (pingüino); the u of que/qui/gue/gui is silent.
    const s = strip(text).replace(/ü/g, 'w').replace(/([qg])u(?=[ei])/g, '$1');
    const units = [];
    const push = (kind, shape) => units.push({ kind, shape, ...UNIT[kind] });
    for (let i = 0; i < s.length; i++) {
        const c = s[i];
        const next = s[i + 1] ?? '';
        if ('aeiou'.includes(c)) push('vowel', c);
        else if (c === 'w') push('vowel', 'u');
        else if (c === 'y') push(/[aeiou]/.test(next) ? 'consonant' : 'vowel', 'i');
        else if ('mbpv'.includes(c)) push('closed', 'closed');
        else if (/[a-zñç]/.test(c)) push('consonant', null);
        else if (/[\s\-_/]/.test(c)) { if (!['space', 'pause'].includes(units.at(-1)?.kind)) push('space', null); }
        else if (/[.,;:!?¡¿…()"«»]/.test(c)) {
            if (units.at(-1)?.kind === 'space') units.pop();      // «bien. ¿Qué»: one pause, not three
            if (units.at(-1)?.kind !== 'pause') push('pause', null);
        }
    }
    // A consonant takes the shape of the vowel after it (or before it, at a word's end).
    for (let i = 0; i < units.length; i++) {
        if (units[i].kind !== 'consonant' || units[i].shape) continue;
        const after = units.slice(i + 1).find(u => u.kind === 'vowel' || u.kind === 'space' || u.kind === 'pause');
        const before = [...units.slice(0, i)].reverse().find(u => u.kind === 'vowel');
        units[i].shape = after?.kind === 'vowel' ? after.shape : before?.shape ?? 'a';
    }
    while (units.length && (units[0].kind === 'space' || units[0].kind === 'pause')) units.shift();
    while (units.length && (units.at(-1).kind === 'space' || units.at(-1).kind === 'pause')) units.pop();
    return units;
}

/** Where each unit starts and ends, as a share (0..1) of the time the units take together. */
export function layout(units) {
    const total = units.reduce((sum, u) => sum + u.weight, 0) || 1;
    let at = 0;
    return units.map(u => {
        const start = at / total;
        at += u.weight;
        return { ...u, start, end: at / total };
    });
}

const shapeOf = unit => (unit?.shape ? SHAPES[unit.shape] : null);

/**
 * The mouth at `progress` (0..1) through a laid-out track: `shape` (viseme weights) and `open`
 * (0..1, how much of the speech's openness this moment lets through). Units fade into the next.
 */
export function mouthAt(track, progress) {
    if (!track?.length) return null;
    const p = Math.min(1, Math.max(0, progress));
    let i = track.findIndex(u => p < u.end);
    if (i < 0) i = track.length - 1;
    const unit = track[i], next = track[i + 1];
    const span = unit.end - unit.start || 1;
    const q = (p - unit.start) / span;
    const fade = next && q > 1 - BLEND ? (q - (1 - BLEND)) / BLEND : 0;
    const shape = {};
    for (const v of VISEMES) {
        const a = shapeOf(unit)?.[v] ?? 0, b = shapeOf(next)?.[v] ?? 0;
        const value = shapeOf(unit) && shapeOf(next) ? a + (b - a) * fade : shapeOf(unit) ? a : b * fade;
        if (value > 0) shape[v] = value;
    }
    const open = unit.open + ((next?.open ?? unit.open) - unit.open) * fade;
    return { shape, open };
}
