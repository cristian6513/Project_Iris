/**
 * IRIS Contextual Room Actions
 *
 * Data-driven map from room objects to avatar actions. Keep this file as the
 * single source of truth for object interactions; scene_actions.js only
 * executes the config.
 */

const ACTION = ({
    id,
    label,
    targetHotspot,
    cameraPreset,
    expression = 'neutral',
    animationName = 'idle',
    duration = null,
    canInterrupt = true,
    enabled = true,
    fallbackAnimation = 'idle',
    activate = null,
    effect = null,
    lookAtTarget = null,
    lookAtWeight = 0.45,
    faceTarget = false,
    facingYawOffset = 0,
    endMode = 'returnCenter',
    returnExpression = null,
}) => Object.freeze({
    id,
    label,
    targetHotspot,
    cameraPreset,
    expression,
    animationName,
    duration,
    canInterrupt,
    enabled,
    fallbackAnimation,
    activate,
    effect,
    lookAtTarget,
    lookAtWeight,
    faceTarget,
    facingYawOffset,
    endMode,
    returnExpression,
});

// "Stand near" the bookshelf, sofa and bed: IRIS starts from facing the object (faceTarget) and
// turns toward the camera that shows her there (facingYawOffset) until her body is at 45° to
// it, a true three-quarters, while her head leans to the object (lookAtWeight 0.25). She keeps
// that yaw (endMode 'stay') instead of turning back whenever no idle clip is playing. That
// camera is the main view for the bookshelf; the sofa and bed spots sit at the main view's
// edge, so theirs are sofaView and bedView.
export const ROOM_OBJECT_ACTIONS = Object.freeze({
    PC: Object.freeze({
        id: 'PC',
        label: 'PC',
        actions: Object.freeze([
            ACTION({
                id: 'standNearPC',
                label: 'Stand near PC',
                targetHotspot: 'pcSpot',
                cameraPreset: 'deskView',
                expression: 'neutral',
                animationName: 'idle',
                fallbackAnimation: 'idle',
                activate: 'monitor',
                lookAtTarget: [2.75, 0.68, -3.12],
                lookAtWeight: 0.28,
                faceTarget: true,
            }),
            ACTION({
                id: 'lookAtScreen',
                label: 'Look at screen',
                targetHotspot: 'pcSpot',
                cameraPreset: 'deskView',
                expression: 'neutral',
                animationName: 'pc_focus_idle',
                duration: 4000,
                fallbackAnimation: 'idle',
                activate: 'monitor',
                lookAtTarget: [2.75, 0.68, -3.12],
                lookAtWeight: 0.36,
                faceTarget: true,
            }),
            ACTION({
                id: 'usePC_idle',
                label: 'Use PC',
                targetHotspot: 'deskSeatSpot',
                cameraPreset: 'deskView',
                expression: 'neutral',
                animationName: 'desk_typing',
                duration: null,
                fallbackAnimation: 'idle',
                activate: 'pc_workstation',
                effect: null,
                endMode: 'stay',
            }),
            ACTION({
                id: 'sitAtDesk_idle',
                label: 'Sit at desk',
                targetHotspot: 'deskSeatSpot',
                cameraPreset: 'deskView',
                expression: 'neutral',
                animationName: 'desk_seated',
                duration: null,
                canInterrupt: true,
                enabled: true,
                fallbackAnimation: 'idle',
                endMode: 'stay',
            }),
            ACTION({
                id: 'usePC_blenderDraft',
                label: 'Use PC 2',
                targetHotspot: 'pcSeatSpot',
                cameraPreset: 'deskView',
                expression: 'neutral',
                animationName: 'pc_seated_idle_v2',
                duration: null,
                fallbackAnimation: 'pc_seated_idle',
                activate: 'pc_workstation',
                effect: null,
                lookAtTarget: [2.75, 0.68, -3.12],
                lookAtWeight: 0,
                faceTarget: true,
                endMode: 'stay',
            }),
        ]),
    }),

    sofa: Object.freeze({
        id: 'sofa',
        label: 'Sofa',
        actions: Object.freeze([
            ACTION({
                id: 'standNearSofa',
                label: 'Stand near sofa',
                targetHotspot: 'sofaSpot',
                cameraPreset: 'sofaView',
                expression: 'feliz',
                animationName: 'idle',
                fallbackAnimation: 'idle',
                lookAtTarget: [-3.3, -0.6, 1.2],
                lookAtWeight: 0.25,
                faceTarget: true,
                facingYawOffset: 1.04,
                endMode: 'stay',
            }),
            ACTION({
                id: 'readOnSofa',
                label: 'Read on sofa',
                targetHotspot: 'sofaSeatSpot',
                cameraPreset: 'sofaView',
                expression: 'feliz',
                animationName: 'sofa_reading',
                duration: null,
                canInterrupt: true,
                enabled: true,
                fallbackAnimation: 'idle',
                endMode: 'stay',
            }),
            ACTION({
                id: 'tabletOnSofa',
                label: 'Tablet on sofa',
                targetHotspot: 'sofaSeatSpot',
                cameraPreset: 'sofaView',
                expression: 'feliz',
                animationName: 'sofa_tablet',
                duration: null,
                canInterrupt: true,
                enabled: true,
                fallbackAnimation: 'idle',
                endMode: 'stay',
            }),
            ACTION({
                id: 'sitOnSofa_idle',
                label: 'Sit on sofa',
                targetHotspot: 'sofaSeatSpot',
                cameraPreset: 'sofaView',
                expression: 'feliz',
                animationName: 'sofa_seated',
                duration: null,
                canInterrupt: true,
                enabled: true,
                fallbackAnimation: 'idle',
                endMode: 'stay',
            }),
        ]),
    }),

    bed: Object.freeze({
        id: 'bed',
        label: 'Bed',
        actions: Object.freeze([
            ACTION({
                id: 'standNearBed',
                label: 'Stand near bed',
                targetHotspot: 'bedSpot',
                cameraPreset: 'bedView',
                expression: 'neutral',
                animationName: 'idle',
                fallbackAnimation: 'idle',
                lookAtTarget: [3.65, 0.34, 1.78],
                lookAtWeight: 0.25,
                faceTarget: true,
                facingYawOffset: -1.67,
                endMode: 'stay',
            }),
            ACTION({
                id: 'readOnBed',
                label: 'Read on bed',
                targetHotspot: 'bedSeatSpot',
                cameraPreset: 'bedView',
                expression: 'neutral',
                animationName: 'bed_reading',
                duration: null,
                canInterrupt: true,
                enabled: true,
                fallbackAnimation: 'idle',
                endMode: 'stay',
            }),
            ACTION({
                id: 'tabletOnBed',
                label: 'Tablet on bed',
                targetHotspot: 'bedSeatSpot',
                cameraPreset: 'bedView',
                expression: 'neutral',
                animationName: 'bed_tablet',
                duration: null,
                canInterrupt: true,
                enabled: true,
                fallbackAnimation: 'idle',
                endMode: 'stay',
            }),
            ACTION({
                id: 'sitOnBed_idle',
                label: 'Sit on bed',
                targetHotspot: 'bedSeatSpot',
                cameraPreset: 'bedView',
                expression: 'neutral',
                animationName: 'bed_seated',
                duration: null,
                canInterrupt: true,
                enabled: true,
                fallbackAnimation: 'idle',
                endMode: 'stay',
            }),
            ACTION({
                id: 'lieOnBed',
                label: 'Lie on bed',
                targetHotspot: 'bedLieSpot',
                cameraPreset: 'bedView',
                expression: 'neutral',
                animationName: 'bed_lying',
                duration: null,
                canInterrupt: true,
                enabled: true,
                fallbackAnimation: 'idle',
                endMode: 'stay',
            }),
        ]),
    }),

    window: Object.freeze({
        id: 'window',
        label: 'Window',
        actions: Object.freeze([
            ACTION({
                id: 'lookOutWindow',
                label: 'Look out window',
                targetHotspot: 'windowSpot',
                cameraPreset: 'windowView',
                expression: 'pensativa',
                animationName: 'window_looking',
                duration: 7000,
                fallbackAnimation: 'pensar',
                activate: 'window_cinematic',
                lookAtTarget: [0.1, 1.18, -5.25],
                lookAtWeight: 0.42,
                endMode: 'stay',
                returnExpression: 'neutral',
            }),
            ACTION({
                id: 'thoughtfulWindowIdle',
                label: 'Thoughtful window idle',
                targetHotspot: 'windowSpot',
                cameraPreset: 'windowView',
                expression: 'pensativa',
                animationName: 'window_looking',
                duration: null,
                activate: 'window_cinematic',
                lookAtTarget: [0.1, 1.18, -5.25],
                lookAtWeight: 0.38,
                fallbackAnimation: 'pensar',
            }),
        ]),
    }),

    bookshelf: Object.freeze({
        id: 'bookshelf',
        label: 'Bookshelf',
        actions: Object.freeze([
            ACTION({
                id: 'standNearBookshelf',
                label: 'Stand near bookshelf',
                targetHotspot: 'bookshelfSpot',
                cameraPreset: 'leftView',
                expression: 'pensativa',
                animationName: 'idle',
                fallbackAnimation: 'idle',
                activate: 'bookshelf',
                lookAtTarget: [-4.6, 0.55, -2.15],
                lookAtWeight: 0.25,
                faceTarget: true,
                facingYawOffset: 1.61,
                endMode: 'stay',
            }),
            ACTION({
                id: 'readBook',
                label: 'Read a book',
                targetHotspot: 'bookReadSpot',
                cameraPreset: 'leftView',
                expression: 'neutral',
                animationName: 'book_reading',
                duration: null,
                canInterrupt: true,
                enabled: true,
                fallbackAnimation: 'pensar',
                endMode: 'stay',
            }),
        ]),
    }),

    platform: Object.freeze({
        id: 'platform',
        label: 'Platform',
        actions: Object.freeze([
            ACTION({
                id: 'readStanding',
                label: 'Read standing',
                targetHotspot: 'centerSpot',
                cameraPreset: 'avatarCloseUp',
                expression: 'neutral',
                animationName: 'stand_reading',
                duration: null,
                canInterrupt: true,
                enabled: true,
                fallbackAnimation: 'idle',
                endMode: 'stay',
            }),
            ACTION({
                id: 'tabletStanding',
                label: 'Tablet standing',
                targetHotspot: 'centerSpot',
                cameraPreset: 'avatarCloseUp',
                expression: 'neutral',
                animationName: 'stand_tablet',
                duration: null,
                canInterrupt: true,
                enabled: true,
                fallbackAnimation: 'idle',
                endMode: 'stay',
            }),
            ACTION({
                id: 'centerIdle',
                label: 'Center idle',
                targetHotspot: 'platformSpot',
                cameraPreset: 'avatarCloseUp',
                expression: 'neutral',
                animationName: 'idle',
                fallbackAnimation: 'idle',
            }),
            ACTION({
                id: 'presentationPose',
                label: 'Presentation pose',
                targetHotspot: 'platformSpot',
                cameraPreset: 'avatarCloseUp',
                expression: 'feliz',
                animationName: 'pose_noble',
                duration: 3000,
                fallbackAnimation: 'saludar_feliz',
            }),
            ACTION({
                id: 'listeningPose',
                label: 'Listening pose',
                targetHotspot: 'platformSpot',
                cameraPreset: 'avatarCloseUp',
                expression: 'neutral',
                animationName: 'subtle_nod',
                duration: 1400,
                fallbackAnimation: 'idle',
            }),
        ]),
    }),
});

export const ROOM_ACTIONS_BY_ID = Object.freeze(
    Object.fromEntries(
        Object.values(ROOM_OBJECT_ACTIONS)
            .flatMap(object => object.actions)
            .map(action => [action.id, action]),
    ),
);

export const ROOM_ACTION_ALIASES = Object.freeze({
    return_center: 'centerIdle',
    read_center: 'readStanding',
    tablet_center: 'tabletStanding',
    center: 'centerIdle',
    centerspot: 'centerIdle',
    idle: 'centerIdle',
    respuesta_lista: 'centerIdle',
    recordar_memoria: 'centerIdle',
    pensar: 'listeningPose',

    go_pc: 'standNearPC',
    pc: 'standNearPC',
    use_pc: 'usePC_idle',
    sit_desk: 'sitAtDesk_idle',
    use_pc_2: 'usePC_blenderDraft',
    use_pc_blender: 'usePC_blenderDraft',
    buscar_internet: 'usePC_idle',
    watch_screen: 'lookAtScreen',

    go_sofa: 'standNearSofa',
    sofa: 'standNearSofa',
    sit_sofa: 'sitOnSofa_idle',
    read_sofa: 'readOnSofa',
    tablet_sofa: 'tabletOnSofa',

    go_bed: 'standNearBed',
    bed: 'standNearBed',
    bed_area: 'standNearBed',
    sit_bed: 'sitOnBed_idle',
    read_bed: 'readOnBed',
    tablet_bed: 'tabletOnBed',
    sit_on_bed: 'sitOnBed_idle',
    lie_bed: 'lieOnBed',
    lie_on_bed: 'lieOnBed',

    look_window: 'lookOutWindow',
    window: 'lookOutWindow',
    look_out_window: 'lookOutWindow',

    go_bookshelf: 'standNearBookshelf',
    bookshelf: 'standNearBookshelf',
    buscar_archivos: 'standNearBookshelf',
    read_book: 'readBook',

    stand_platform: 'centerIdle',
    platform: 'centerIdle',
    platformspot: 'centerIdle',
});

export const ROOM_ACTION_COMMANDS = Object.freeze({
    buscar_internet: Object.freeze({ actionId: 'usePC_idle', activate: 'pc_workstation', effect: null }),
    use_pc_2: Object.freeze({ actionId: 'usePC_blenderDraft', activate: 'pc_workstation', effect: null }),
    buscar_archivos: Object.freeze({ actionId: 'standNearBookshelf', activate: 'bookshelf', effect: 'floatingBook' }),
    recordar_memoria: Object.freeze({ actionId: 'centerIdle', effect: 'floatingBook' }),
    pensar: Object.freeze({ actionId: 'listeningPose', effect: 'thinkingCloud' }),
    respuesta_lista: Object.freeze({ actionId: 'centerIdle' }),
});

function normalizeActionName(value) {
    return String(value || '')
        .trim()
        .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '_')
        .replace(/^_+|_+$/g, '');
}

export function getRoomAction(actionName) {
    if (!actionName) return null;
    if (ROOM_ACTIONS_BY_ID[actionName]) return ROOM_ACTIONS_BY_ID[actionName];
    const normalized = normalizeActionName(actionName);
    const actionId = ROOM_ACTIONS_BY_ID[normalized]
        ? normalized
        : ROOM_ACTION_ALIASES[normalized];
    return ROOM_ACTIONS_BY_ID[actionId] || null;
}

export function getRoomActionCommand(commandName) {
    const action = getRoomAction(commandName);
    if (!action) return null;
    const normalized = normalizeActionName(commandName);
    const command = ROOM_ACTION_COMMANDS[normalized] || {};
    return Object.freeze({
        action,
        actionId: action.id,
        activate: command.activate ?? action.activate ?? null,
        effect: command.effect ?? action.effect ?? null,
    });
}

