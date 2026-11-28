// While her lids close, her eyes turn back to the centre (2026-11-26).
//
// A VRM's gaze turns the eye bones (three-vrm's look-at applier; IRIS's file allows 10 degrees).
// Turned, the iris slid out from under a closed lid at the outer corner (the «3 a. m.» video):
// a lid only covers the iris as far as an open eye can turn when it is half closed or less.
// Measured on IRIS v14 (eyes 5 mm further back), the turn that stays under the lid:
//   lids 0.5 closed: about 7 degrees; 0.65 to 0.8: about 5; 0.9 to 1: about 3.
// The turn is scaled by (1 - closure), the most closed of blink / blinkLeft / blinkRight, which
// stays inside all of those at her full 10 degrees. A closed eye is covered anyway.
//
// The scale wraps the applier, the one place every gaze goes through (micro_life's target, held
// gazes, clips through the look-at proxy). When a clip holds the head, nothing turns the eyes
// each frame: if only the lids moved, the held turn is applied again with the new closure.

const LID_EXPRESSIONS = ['blink', 'blinkLeft', 'blinkRight'];

/** How closed her lids are, 0 open to 1 closed: the most closed of the blink expressions. */
export function lidClosure(expressionManager) {
    let closure = 0;
    for (const name of LID_EXPRESSIONS) {
        const value = Number(expressionManager?.getValue?.(name));
        if (Number.isFinite(value) && value > closure) closure = value;
    }
    return Math.min(1, closure);
}

/**
 * Makes `vrm`'s gaze ease to the centre as the lids close. Returns false when the VRM has no
 * look-at applier or expressions (nothing to do), true once wrapped; calling it again is harmless.
 */
export function closeGazeWithLids(vrm) {
    const look = vrm?.lookAt;
    const applier = look?.applier;
    const expressions = vrm?.expressionManager;
    if (!applier || typeof applier.applyYawPitch !== 'function' || typeof look.update !== 'function' || !expressions) {
        return false;
    }
    if (applier.irisLidGaze) return true;
    const apply = applier.applyYawPitch.bind(applier);
    let used = 0;
    applier.applyYawPitch = (yaw, pitch) => {
        used = lidClosure(expressions);
        const open = 1 - used;
        apply(yaw * open, pitch * open);
    };
    const update = look.update.bind(look);
    look.update = (delta) => {
        update(delta);
        if (Math.abs(lidClosure(expressions) - used) > 1e-3) applier.applyYawPitch(look.yaw, look.pitch);
    };
    applier.irisLidGaze = true;
    return true;
}
