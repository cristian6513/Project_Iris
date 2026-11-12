/**
 * IRIS Room Hotspots
 *
 * Declarative navigation points for simple room interactions. These are
 * intentionally data-first so future phases can add richer animation or IK
 * without changing the action names used by UI / LLM scene actions.
 */

// Her back to the bed's long side (x 2.66), level with the spot on the mattress her hips end
// up in when she lies down, her head on the left pillow. Along the bed's side, so she turns a
// quarter to sit and not all the way round. Sitting and lying share the view: one follows the other.
const BED_SIDE = Object.freeze({
    position: [2.52, 0, 1.72],
    rotation: [0, -Math.PI / 2, 0],
    approach: [[2.4, 0, 0.95]],
    view: { position: [1.0, 1.0, 2.75], target: [3.12, -0.3, 1.72], minimumAspect: 1.5 },
});

export const ROOM_HOTSPOTS = Object.freeze({
    centerSpot: {
        id: 'centerSpot',
        label: 'Return to center',
        position: [0, 0, 0],
        rotation: [0, 0, 0],
        cameraPreset: 'isometricView',
        defaultExpression: 'neutral',
        defaultPoseOrAnimation: 'idle',
        description: 'Neutral center position on the main avatar platform.',
        enabled: true,
    },
    pcSpot: {
        id: 'pcSpot',
        label: 'Go to PC',
        position: [1.52, 0, -1.66],
        rotation: [0, 2.48, 0],
        cameraPreset: 'deskView',
        defaultExpression: 'pensativa',
        defaultPoseOrAnimation: 'thinking_pose',
        description: 'Safe standing point near the futuristic desk and monitors.',
        enabled: true,
    },
    pcSeatSpot: {
        id: 'pcSeatSpot',
        label: 'Sit at PC',
        // On the chair, which room.js centres in the desk's knee space (desk x +0.1).
        position: [2.82, 0, -1.55],
        rotation: [0, 2.86, 0],
        cameraPreset: 'deskView',
        defaultExpression: 'neutral',
        defaultPoseOrAnimation: 'pc_seated_idle',
        description: 'Conservative seated workstation point aligned with the desk chair.',
        enabled: true,
    },
    deskSeatSpot: {
        id: 'deskSeatSpot',
        label: 'Sit at desk',
        // Where the chair stands at the desk. It rolls back to leave her this gap, she turns
        // her back to it here and rides it in (DESK_CHAIR in idle_system.js).
        position: [2.766, 0, -1.605],
        rotation: [0, Math.PI - 0.08, 0],
        approach: [[1.6, 0, -1.2], [1.95, 0, -1.84], [2.45, 0, -1.86]],
        rollsChair: true,
        // Close and from the side of the screens: it only fits her seated.
        // `via` takes the camera round her: in a straight line it would cross her head.
        view: { position: [4.02, 0.86, -2.78], target: [2.75, 0.08, -1.85], via: [5.6, 1.0, -2.1], minimumAspect: 1.5 },
        closeView: true,
        cameraPreset: 'deskView',
        defaultExpression: 'neutral',
        defaultPoseOrAnimation: 'desk_seated',
        description: 'Seated at the desk, on the chair.',
        enabled: true,
    },
    deskEdgeSpot: {
        id: 'deskEdgeSpot',
        label: 'Sit on the desk',
        // On the free left end of the desk top (y -0.16, 0.71 m), facing the room, her legs
        // hanging in front of it; the chair stays clear to her left. She can't walk up onto it:
        // a line of light takes her there and back (`teleport`).
        position: [1.75, 0, -2.25],
        rotation: [0, 0, 0],
        teleport: true,
        // Head (y 0.78) to boots on the floor (-1.2) in 16:9; the old view (target -0.35) cut
        // her head off, and the avatar zoom closed in on her legs (QA23).
        view: { position: [0.25, 0.45, 0.75], target: [1.75, -0.05, -2.25], minimumAspect: 1.5 },
        cameraPreset: 'deskView',
        defaultExpression: 'feliz',
        defaultPoseOrAnimation: 'desk_edge',
        description: 'Sitting on the edge of the desk, swinging her legs.',
        enabled: true,
    },
    sofaSpot: {
        id: 'sofaSpot',
        label: 'Go to sofa',
        // Beside the front-right corner of the sofa (room.js), reached in a straight
        // line from the platform without crossing its arm.
        position: [-1.65, 0, 2.3],
        rotation: [0, 0.08, 0],
        cameraPreset: 'sofaView',
        defaultExpression: 'feliz',
        defaultPoseOrAnimation: 'idle',
        description: 'Standing point at the front-right corner of the sofa.',
        enabled: true,
    },
    sofaSeatSpot: {
        id: 'sofaSeatSpot',
        label: 'Sit on sofa',
        // Where she stands before sitting: the stretch of seat free of cushions, with her
        // back to the sofa. This close, her toes clear the coffee table's base (z 2.28).
        position: [-2.6, 0, 1.95],
        rotation: [0, 0.06, 0],
        // Round the sofa's right arm and down the middle of the gap between sofa and table,
        // where her feet touch neither. The way out is the same one, reversed.
        approach: [[-1.65, 0, 2.3], [-2.1, 0, 2.05], [-2.45, 0, 2.04]],
        // The main view leaves her at the edge of the frame: the camera comes with her.
        view: { position: [-1.6, 0.6, 4.05], target: [-2.58, -0.34, 1.6], minimumAspect: 1.5 },
        cameraPreset: 'sofaView',
        defaultExpression: 'feliz',
        defaultPoseOrAnimation: 'sofa_seated',
        description: 'In front of the sofa seat, where the sit-down clip starts.',
        enabled: true,
    },
    bedSpot: {
        id: 'bedSpot',
        label: 'Go to bed area',
        // Beside the left long side of the bed (room.js, x ≥ 2.55), outside the platform.
        position: [2.15, 0, 1.6],
        rotation: [0, 1.18, 0],
        cameraPreset: 'bedView',
        defaultExpression: 'neutral',
        defaultPoseOrAnimation: 'idle',
        description: 'Safe bedside edge point for standing or a conservative seated pose.',
        enabled: true,
    },
    bedLieSpot: {
        id: 'bedLieSpot',
        label: 'Lie on bed',
        ...BED_SIDE,
        cameraPreset: 'bedView',
        defaultExpression: 'neutral',
        defaultPoseOrAnimation: 'bed_lying',
        description: 'Beside the bed, where the lie-down clip starts.',
        enabled: true,
    },
    bedProneSpot: {
        id: 'bedProneSpot',
        label: 'Lie face down on bed',
        ...BED_SIDE,
        cameraPreset: 'bedView',
        defaultExpression: 'neutral',
        defaultPoseOrAnimation: 'bed_prone',
        description: 'Beside the bed: she lies down there and rolls onto her front.',
        enabled: true,
    },
    bedSeatSpot: {
        id: 'bedSeatSpot',
        label: 'Sit on bed',
        ...BED_SIDE,
        cameraPreset: 'bedView',
        defaultExpression: 'neutral',
        defaultPoseOrAnimation: 'bed_seated',
        description: 'Beside the bed, where the sit-down clip starts.',
        enabled: true,
    },
    windowSpot: {
        id: 'windowSpot',
        label: 'Look out window',
        // A hand's reach from the glass, which stands level with the mullions (z -4.62). To the
        // side of the centre: a light hangs there (cityCyanWash), and it burns a pale head.
        position: [0.75, 0, -4.09],
        rotation: [0, Math.PI, 0],
        approach: [[0.45, 0, -2.4]],    // clear of the desk, coming from the bed
        backToRoom: true,
        view: { position: [1.05, 0.95, -0.5], target: [0.5, 0.02, -4.6], minimumAspect: 1.5 },
        cameraPreset: 'windowView',
        defaultExpression: 'pensativa',
        defaultPoseOrAnimation: 'window_looking',
        description: 'Window-facing point for looking over the cyberpunk skyline.',
        enabled: true,
    },
    bookReadSpot: {
        id: 'bookReadSpot',
        label: 'Read a book',
        // The bookshelf at her right, facing the room: she reaches sideways for the book and
        // reads with her face to the camera.
        position: [-3.7, 0, -2.25],
        rotation: [0, 0.25, 0],
        view: { position: [-3.05, 0.95, -0.15], target: [-3.66, 0.48, -2.25], minimumAspect: 1.5 },
        cameraPreset: 'leftView',
        defaultExpression: 'neutral',
        defaultPoseOrAnimation: 'book_reading',
        description: 'Beside the bookshelf, where she takes a book and reads it.',
        enabled: true,
    },
    bookshelfSpot: {
        id: 'bookshelfSpot',
        label: 'Go to bookshelf',
        position: [-3.64, 0, -1.86],
        rotation: [0, -1.82, 0],
        cameraPreset: 'leftView',
        defaultExpression: 'pensativa',
        defaultPoseOrAnimation: 'thinking_pose',
        description: 'Reading-side standing point near the warm bookshelf.',
        enabled: true,
    },
    platformSpot: {
        id: 'platformSpot',
        label: 'Stand on platform',
        position: [0, 0, 0],
        rotation: [0, 0, 0],
        cameraPreset: 'avatarCloseUp',
        defaultExpression: 'neutral',
        defaultPoseOrAnimation: 'idle',
        description: 'Hero position on the circular avatar platform.',
        enabled: true,
    },
});

export const ROOM_HOTSPOT_ACTIONS = Object.freeze({
    return_center: 'centerSpot',
    read_center: 'centerSpot',
    tablet_center: 'centerSpot',
    center: 'centerSpot',
    centerSpot: 'centerSpot',
    idle: 'centerSpot',
    respuesta_lista: 'centerSpot',
    recordar_memoria: 'centerSpot',
    pensar: 'centerSpot',

    go_pc: 'pcSpot',
    pc: 'pcSpot',
    use_pc: 'deskSeatSpot',
    sit_desk: 'deskSeatSpot',
    buscar_internet: 'pcSeatSpot',
    watch_screen: 'pcSpot',

    go_sofa: 'sofaSpot',
    sofa: 'sofaSpot',
    sit_sofa: 'sofaSeatSpot',
    read_sofa: 'sofaSeatSpot',
    tablet_sofa: 'sofaSeatSpot',

    go_bed: 'bedSpot',
    bed: 'bedSpot',
    bed_area: 'bedSpot',
    read_bed: 'bedSeatSpot',
    tablet_bed: 'bedSeatSpot',

    look_window: 'windowSpot',
    window: 'windowSpot',
    look_out_window: 'windowSpot',

    go_bookshelf: 'bookshelfSpot',
    bookshelf: 'bookshelfSpot',
    buscar_archivos: 'bookshelfSpot',
    read_book: 'bookshelfSpot',

    stand_platform: 'platformSpot',
    platform: 'platformSpot',
    platformSpot: 'platformSpot',
});

export function getHotspotForAction(actionName) {
    if (!actionName) return null;
    const direct = ROOM_HOTSPOT_ACTIONS[actionName] || actionName;
    if (ROOM_HOTSPOTS[direct]) return ROOM_HOTSPOTS[direct];

    const normalized = String(actionName)
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '_')
        .replace(/^_+|_+$/g, '');
    return ROOM_HOTSPOTS[ROOM_HOTSPOT_ACTIONS[normalized]] || null;
}

