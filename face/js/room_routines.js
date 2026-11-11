/**
 * What IRIS does in the room when nobody needs her. Who owns her body, highest first:
 *   1. an order: a button, or a scene action in Hermes' answer. It holds until another order.
 *   2. the conversation: she stops what she is doing and answers from where she is.
 *   3. a routine: this file, only while nothing above wants her.
 *   4. the idle poses of IdleSystem.
 * The Companion has no room: it runs this on CompanionBody, which takes the place of the scenes.
 */
export const ROOM_LIFE_KEY = 'IRIS.roomLife.v1';

// Seconds without interaction before each kind of step, and how long a step lasts.
export const ROOM_LIFE = Object.freeze({
    off: Object.freeze({ label: 'Apagada' }),
    calm: Object.freeze({ label: 'Tranquila', near: 60, visit: 240, rest: 900, nearFor: [30, 60], visitFor: [120, 300], teleport: 0.15 }),
    active: Object.freeze({ label: 'Activa', near: 25, visit: 90, rest: 600, nearFor: [20, 40], visitFor: [60, 150], teleport: 0.4 }),
});
export const DEFAULT_ROOM_LIFE = 'calm';

const NEAR = Object.freeze(['read_center', 'tablet_center']);
// Each place, with what she can do there once seated.
const PLACES = Object.freeze([
    Object.freeze(['sit_sofa', 'read_sofa', 'tablet_sofa']),
    Object.freeze(['sit_bed', 'read_bed', 'tablet_bed']),
    Object.freeze(['sit_desk', 'use_pc']),
    Object.freeze(['look_window']),
    Object.freeze(['sit_desk_edge']),
]);
// Resting she lies on her back, or now and then on her front with her feet swinging.
const RESTS = Object.freeze(['lie_bed', 'lie_bed_prone']);
const PRONE_REST = 0.35;     // chance of the front when both can be played
const HOME = 'return_center';
const WORK = 'use_pc';
const WORK_AFTER = 20;       // seconds of tools running before she goes to the PC
const REPLY_WAIT = 90;       // nothing says a turn is alive until it ends: give it this long
const STAY_AT_PLACE = 0.6;   // chance of doing something else there instead of leaving
const NEW_TALK_AFTER = 300;  // seconds of silence that end one talk: she decides once in each
const TELEPORT_EVERY = 900;  // at most this often: an effect tires if it repeats

function roomLifeValue(value) {
    return Object.hasOwn(ROOM_LIFE, value) ? value : DEFAULT_ROOM_LIFE;
}

/** The person's choice, kept on this device like the avatar zoom. */
export class RoomLife {
    constructor({ storage = () => globalThis.localStorage } = {}) {
        this._storage = storage;
        this._listeners = new Set();
        this.value = DEFAULT_ROOM_LIFE;
        this.persisted = null;
        try { this.value = roomLifeValue(storage()?.getItem(ROOM_LIFE_KEY)); } catch { /* The default is fine. */ }
    }

    snapshot() {
        return { value: this.value, persisted: this.persisted };
    }

    subscribe(listener) {
        this._listeners.add(listener);
        listener(this.snapshot());
        return () => this._listeners.delete(listener);
    }

    setValue(value) {
        this.value = roomLifeValue(value);
        try {
            const storage = this._storage();
            if (!storage) throw Error('Storage unavailable');
            storage.setItem(ROOM_LIFE_KEY, this.value);
            this.persisted = true;
        } catch { this.persisted = false; }
        for (const listener of this._listeners) listener(this.snapshot());
    }
}

export function bindRoomLifeControls(select, getController) {
    if (!select) return { refresh() {}, dispose() {} };
    let controller = null, unsubscribe = null;
    const refresh = () => {
        const next = getController?.() || null;
        if (next === controller) return;
        unsubscribe?.();
        controller = next;
        select.disabled = !controller;
        unsubscribe = controller?.subscribe((state) => { select.value = state.value; }) || null;
    };
    const change = () => controller?.setValue(select.value);
    select.addEventListener('change', change);
    refresh();
    return { refresh, dispose() { unsubscribe?.(); select.removeEventListener('change', change); } };
}

/** May a routine take her body now? Same shape as the Companion's locomotion policy. */
export function routineDecision({
    life = DEFAULT_ROOM_LIFE,
    reducedMotion = false,
    visible = true,
    exploring = false,
    activity = 'idle',
    ordered = false,
} = {}) {
    if (life === 'off') return { allowed: false, reason: 'off' };
    if (reducedMotion) return { allowed: false, reason: 'reduced-motion' };
    if (ordered) return { allowed: false, reason: 'order' };
    // `activity` is AvatarActivity's state. Speaking and thinking mean someone is really
    // talking to her. Listening only keeps a routine from starting: a microphone may be nothing.
    if (activity === 'speaking' || activity === 'thinking') return { allowed: false, reason: 'conversation' };
    if (activity === 'listening') return { allowed: false, reason: 'attentive' };
    if (!visible) return { allowed: false, reason: 'hidden' };
    if (exploring) return { allowed: false, reason: 'exploring' };
    return { allowed: true, reason: 'ready' };
}

export class RoomRoutines {
    /**
     * @param {object} scenes SceneActionManager
     * @param {() => object} context what `routineDecision` reads, without `ordered`
     */
    constructor({ scenes, life, context, now = () => performance.now() / 1000, random = Math.random }) {
        this.scenes = scenes;
        this.life = life;
        this.context = context;
        this.now = now;
        this.random = random;
        this.quietSince = now();  // last time a person was involved; routines don't touch it
        this.nextAt = 0;          // a pause between routines
        this.step = null;
        this.last = null;
        this.orders = scenes.orders;
        this.stopped = false;     // she has already stopped for this conversation
        this.turn = null;         // Core's number for the turn she is waiting on
        this.toolSince = null;
        this.replyUntil = 0;
        this.teleportedAt = -Infinity;
        this._teleportStep = null;
    }

    /** Someone spoke to her: a message from any surface or a voice accepted, never an open mic. */
    turnStarted(turn) {
        const quiet = this.now() - this.quietSince;
        this.turn = turn;
        this.quietSince = this.now();
        this.replyUntil = this.now() + REPLY_WAIT;
        this.toolSince = null;    // whatever ran before belongs to the turn before
        if (!this._teleportHome(quiet)) this._stopForConversation();
    }

    /** A fast reply takes priority over the light; an unfinished departure stays in its seat. */
    responseStarted() {
        const cancelled = this.scenes.cancelTeleport?.();
        if (cancelled && !cancelled.landed && this._teleportStep) {
            this.step = this._teleportStep;
            this.stopped = false;
            this._stopForConversation();
            this._freeHands();
        }
        this._teleportStep = null;
    }

    /**
     * Hermes is using a tool. Core sends no status for it, only this progress. Only that of the
     * open turn: one stopped, the turn before or a task of its own moves nothing.
     */
    toolRunning(turn) {
        if (turn === null || turn === undefined || turn !== this.turn) return;
        this.toolSince ??= this.now();
        this.replyUntil = this.now() + REPLY_WAIT;
    }

    /** Answered, stopped or failed. The end of the turn before leaves a newer one open. */
    turnEnded(turn) {
        if (turn === this.turn) this._closeTurn();
    }

    connectionLost() {
        this._closeTurn();
    }

    _closeTurn() {
        this.turn = null;
        this.toolSince = null;
        this.replyUntil = 0;
    }

    update() {
        if (this.scenes.orders !== this.orders) {
            // An order takes her from any routine, and the routine does not come back to it.
            this.orders = this.scenes.orders;
            this.step = null;
            this._teleportStep = null;
            this.stopped = false;
            this.toolSince = null;
            this.quietSince = this.now();
        }
        const context = this.context();
        const waiting = this.now() < this.replyUntil;
        const decision = routineDecision({
            ...context,
            activity: waiting && context.activity === 'idle' ? 'thinking' : context.activity,
            ordered: this.scenes.heldByOrder(),
        });
        if (decision.reason === 'order') return;
        if (decision.reason === 'off' || decision.reason === 'reduced-motion') {
            this._endRoutine();
            return;
        }
        if (decision.reason === 'conversation') {
            this.quietSince = this.now();
            this._stopForConversation();
            // Going to work is a routine like any other: hidden or explored, she stays.
            const free = routineDecision({ ...context, activity: 'idle' }).allowed;
            if (context.activity === 'speaking' || !free || !this._goToWork(context.life)) this._freeHands();
            return;
        }
        this.stopped = false;
        if (!decision.allowed || this.scenes.isBusy()) return;

        const settings = ROOM_LIFE[context.life];
        if (this.step) {
            if (this.now() >= this.step.until) this._next(settings);
            return;
        }
        const quiet = this.now() - this.quietSince;
        if (quiet >= settings.near && this.now() >= this.nextAt) this._start(settings, quiet);
    }

    _start(settings, quiet) {
        if (quiet >= settings.rest && this._rest()) return;
        if (quiet >= settings.visit) {
            for (const place of this._shuffled(PLACES)) {
                for (const order of this._shuffled(place)) {
                    if (this._begin(order, 'visit', this._length(settings.visitFor), place)) return;
                }
            }
        }
        for (const order of this._shuffled(NEAR)) {
            if (this._begin(order, 'near', this._length(settings.nearFor))) return;
        }
    }

    /** Lie down to rest: on her back, or on her front; the other one if this one can't play. */
    _rest() {
        const first = this.random() < PRONE_REST ? 'lie_bed_prone' : 'lie_bed';
        return [first, ...RESTS.filter((order) => order !== first)]
            .some((order) => this._begin(order, 'rest', Infinity));
    }

    _next(settings) {
        const { kind, place } = this.step;
        const quiet = this.now() - this.quietSince;
        if (kind === 'visit' && this.random() < STAY_AT_PLACE) {
            const next = this._shuffled(place).find((order) => order !== this.step.order
                && this._begin(order, 'visit', this._length(settings.visitFor), place));
            if (next) return;
        }
        if (quiet >= settings.rest && this._rest()) return;
        this._goHome();
        this.nextAt = this.now() + settings.near;
    }

    _begin(order, kind, length, place = null) {
        if (order === this.last || !this.scenes.canRun(order)) return false;
        if (!this.scenes.execute(order, { origin: 'routine' })) return false;
        this.step = { order, kind, place, until: this.now() + length };
        this.last = order;
        return true;
    }

    _goHome() {
        this.step = null;
        this.scenes.execute(HOME, { origin: 'routine' });
    }

    /** Off, or Reducir movimiento: she stops wherever a routine left her and goes back. */
    _endRoutine() {
        if (this.step) this._goHome();
    }

    /**
     * Sometimes she comes to the middle of the room to answer. It has to be now and not when the
     * answer arrives, or she would vanish while talking. Never out of a place she was sent to.
     */
    _teleportHome(quiet) {
        const context = this.context();
        const settings = ROOM_LIFE[context.life];
        if (!this.step || this.step.kind === 'near' || !routineDecision({ ...context, activity: 'idle' }).allowed) return false;
        if (quiet < NEW_TALK_AFTER || this.now() - this.teleportedAt < TELEPORT_EVERY) return false;
        if (!this.scenes.canTeleport?.() || this.random() >= settings.teleport) return false;
        if (!this.scenes.teleportHome()) return false;
        this.teleportedAt = this.now();
        this._teleportStep = this.step;
        this.step = null;
        this.nextAt = this.now() + settings.near;
        return true;
    }

    /** On a walk of her own or at the window she comes back; with something in hand she puts it away. */
    _stopForConversation() {
        if (this.stopped) return;
        this.stopped = true;
        if (!this.step) return;
        if (this.scenes.isWalking() || this.scenes.hasBackToRoom?.()) {
            this._goHome();
            return;
        }
        // Afterwards she stays a while, as if the talk had interrupted her, then goes on.
        const settings = ROOM_LIFE[this.context().life];
        this.step.until = this.now() + (settings?.near ?? 0);
    }

    /** Asked every frame of a conversation: what she holds may still be on its way in. */
    _freeHands() {
        if (this.step && !this.scenes.isWalking()) this.scenes.freeHands();
    }

    /** A long task: the avatar shows that it is working, at the PC. True while she does. */
    _goToWork(life) {
        if (this.toolSince === null || this.now() - this.toolSince < WORK_AFTER) return false;
        if (this.step?.order === WORK) return true;
        if (!this.scenes.canRun(WORK) || this.scenes.isWalking()) return false;
        if (!this.scenes.execute(WORK, { origin: 'routine' })) return false;
        this.step = { order: WORK, kind: 'visit', place: PLACES[2], until: this.now() + (ROOM_LIFE[life]?.near ?? 0) };
        return true;
    }

    _length([least, most]) {
        return least + this.random() * (most - least);
    }

    _shuffled(list) {
        return [...list]
            .map((value) => [this.random(), value])
            .sort((a, b) => a[0] - b[0])
            .map(([, value]) => value);
    }
}
