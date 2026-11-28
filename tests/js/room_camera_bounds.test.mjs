// The conversation camera stays inside the room at any screen shape (QA30, 2026-11-27: on a
// portrait monitor the desk view backed off behind the walls and the screen went empty).
import test from 'node:test';
import assert from 'node:assert/strict';
import { ROOM_HOTSPOTS } from '../../face/js/room_hotspots.js';
import { ConversationCamera, ROOM_CAMERA_BOUNDS, ROOM_DETAIL_PRESETS, clampToRoom, frameRoomPreset } from '../../face/js/room_framing.js';

const THREE = await import(new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href);

const ASPECTS = [16 / 9, 4 / 3, 1, 9 / 16, 0.35];
const HOME_FOV = 45;
const HOME = [0.34, 1.38, 4.95];
const SPOT_VIEWS = Object.values(ROOM_HOTSPOTS).filter((spot) => spot.view);
// The room's walls (room.js): side walls x ±5.04, window glass z -4.62, ceiling 3.05, floor -1.2.
const WALLS = { min: [-5.04, -1.2, -4.62], max: [5.04, 3.05, 4.95] };
const within = (point, { min, max }, slack = 1e-9) => point.every((v, i) => v >= min[i] - slack && v <= max[i] + slack);
const label = (aspect) => aspect.toFixed(2);

// Her bones at each spot, measured in the room in Edge once she settled there (2026-11-27): what
// the 16:9 view shows of her. At the desk and the bookshelf that stops at her hands: the close
// views leave her boots out, by design. Desk edge and sofa: the points of room_sofa_seat.test.mjs.
const MEASURED = {
    deskSeatSpot: [[2.796, 0.456, -2], [2.786, -0.4, -1.877], [2.683, -0.019, -2.421], [2.974, -0.024, -2.398]],
    deskEdgeSpot: [[1.74, 0.96, -2.44], [2.05, -0.75, -1.45], [1.56, -0.75, -1.8]],
    sofaSeatSpot: [[-2.65, 0.45, 1.05], [-2.63, -0.49, 1.36], [-2.6, -1.15, 2.2]],
    bedSeatSpot: [[3.107, 0.41, 1.718], [3.021, -0.452, 1.722], [2.521, -1.072, 1.863], [2.521, -1.072, 1.58]],
    windowSpot: [[0.787, 0.993, -4.187], [0.748, 0.134, -4.09], [0.607, -1.072, -4.089], [0.89, -1.072, -4.089]],
    bookReadSpot: [[-3.681, 0.993, -2.183], [-3.698, 0.131, -2.25], [-3.287, 0.457, -1.905], [-3.893, 0.457, -1.75]],
};

function settledCamera(view, aspect, { zoom = 1, focus = null, seconds = 3.2 } = {}) {
    const camera = new THREE.PerspectiveCamera(HOME_FOV, aspect, 0.1, 1000);
    camera.zoom = zoom;
    camera.updateProjectionMatrix();
    const conversation = new ConversationCamera(camera);
    for (let t = 0; t < seconds; t += 1 / 30) conversation.update(1 / 30, t, view, focus);
    camera.updateMatrixWorld(true);
    return { camera, conversation };
}

function onScreen(camera, point) {
    const ndc = new THREE.Vector3(...point).project(camera);
    return { ndc, visible: Math.abs(ndc.x) <= 1 && Math.abs(ndc.y) <= 1 && ndc.z > -1 && ndc.z < 1 };
}

test('every spot view and inside preset is placed inside the camera bounds', () => {
    assert.ok(within(HOME, ROOM_CAMERA_BOUNDS), 'home');
    for (const spot of SPOT_VIEWS) assert.ok(within(spot.view.position, ROOM_CAMERA_BOUNDS), spot.id);
    assert.ok(within(ROOM_DETAIL_PRESETS.sofaView.position, ROOM_CAMERA_BOUNDS));
    assert.ok(within(ROOM_DETAIL_PRESETS.bedView.position, ROOM_CAMERA_BOUNDS));
    // The bounds themselves sit inside the walls.
    assert.ok(within(ROOM_CAMERA_BOUNDS.min, WALLS) && within(ROOM_CAMERA_BOUNDS.max, WALLS));
});

test('16:9 and wider frame every view exactly as placed', () => {
    for (const spot of SPOT_VIEWS) {
        for (const aspect of [1.5, 16 / 9, 21 / 9]) {
            const framed = frameRoomPreset(spot.view, aspect, { fov: HOME_FOV });
            assert.deepEqual(framed.position, spot.view.position, `${spot.id} ${label(aspect)}`);
            assert.equal(framed.fov, HOME_FOV);
        }
    }
});

test('at any aspect a spot view keeps the camera in the room and her in front of it', () => {
    for (const spot of SPOT_VIEWS) {
        for (const aspect of ASPECTS) {
            const framed = frameRoomPreset(spot.view, aspect, { fov: HOME_FOV });
            const name = `${spot.id} ${label(aspect)}`;
            assert.ok(within(framed.position, ROOM_CAMERA_BOUNDS), `${name}: camera at ${framed.position}`);
            assert.deepEqual(framed.target, spot.view.target);
            assert.ok(framed.fov >= HOME_FOV && framed.fov < 90, `${name}: fov ${framed.fov}`);
            const camera = new THREE.PerspectiveCamera(framed.fov, aspect, 0.1, 1000);
            camera.position.set(...framed.position);
            camera.lookAt(...framed.target);
            camera.updateMatrixWorld(true);
            const { ndc, visible } = onScreen(camera, framed.target);
            assert.ok(visible && Math.hypot(ndc.x, ndc.y) < 1e-6, `${name}: target at ${ndc.x}, ${ndc.y}`);
        }
    }
});

test('where the walls stop the camera short, a wider lens keeps the same width around her', () => {
    const halfWidth = (framed, aspect) => {
        const distance = Math.hypot(...framed.position.map((v, i) => v - framed.target[i]));
        return distance * Math.tan(framed.fov * Math.PI / 360) * aspect;
    };
    let widened = 0;
    for (const spot of SPOT_VIEWS) {
        const design = halfWidth(frameRoomPreset(spot.view, 1.5, { fov: HOME_FOV }), 1.5);
        for (const aspect of ASPECTS.filter((a) => a < spot.view.minimumAspect)) {
            const framed = frameRoomPreset(spot.view, aspect, { fov: HOME_FOV });
            const width = halfWidth(framed, aspect);
            // Equal, unless the lens hit its cap: the frame's corners 45° off its axis. Only at
            // 0.35 among the spots (desk 86 %, sofa 73 %); her measured body still fits there.
            const corner = Math.atan(Math.tan(framed.fov * Math.PI / 360) * Math.hypot(1, Math.max(aspect, 0.35)));
            const capped = Math.abs(corner - Math.PI / 4) < 1e-9;
            if (framed.fov > HOME_FOV) widened++;
            assert.ok(capped ? width < design && width > design * 0.7 : Math.abs(width - design) < 1e-9,
                `${spot.id} ${label(aspect)}: ${width.toFixed(3)} for ${design.toFixed(3)}`);
        }
    }
    assert.ok(widened > 0);
});

test('the desk seat on a 9:16 screen: the camera stops at the right wall instead of behind it (QA30)', () => {
    const view = ROOM_HOTSPOTS.deskSeatSpot.view;
    const old = view.position.map((v, i) => view.target[i] + (v - view.target[i]) * (1.5 / (9 / 16)));
    assert.ok(old[0] > WALLS.max[0], 'unclamped it stood behind the right wall');
    const framed = frameRoomPreset(view, 9 / 16, { fov: HOME_FOV });
    assert.equal(framed.position[0], ROOM_CAMERA_BOUNDS.max[0]);
    assert.ok(framed.fov > 60 && framed.fov < 75, `fov ${framed.fov}`);
});

test('her measured head, hips and feet stay in frame at every aspect', () => {
    for (const [id, points] of Object.entries(MEASURED)) {
        for (const aspect of ASPECTS) {
            const { camera } = settledCamera(ROOM_HOTSPOTS[id].view, aspect);
            for (const point of points) {
                const { ndc, visible } = onScreen(camera, point);
                assert.ok(visible, `${id} ${label(aspect)}: ${point} at ${ndc.x.toFixed(2)}, ${ndc.y.toFixed(2)}`);
            }
        }
    }
});

test('the conversation camera never leaves the room on its way to a spot and back, arcs included', () => {
    for (const spot of SPOT_VIEWS) {
        for (const aspect of ASPECTS) {
            const camera = new THREE.PerspectiveCamera(HOME_FOV, aspect, 0.1, 1000);
            const conversation = new ConversationCamera(camera);
            let t = 0;
            const step = (seconds, view) => {
                for (let s = 0; s < seconds; s += 1 / 30, t += 1 / 30) {
                    conversation.update(1 / 30, t, view);
                    const at = camera.position.toArray();
                    assert.ok(within(at, ROOM_CAMERA_BOUNDS), `${spot.id} ${label(aspect)} at ${at.map((v) => v.toFixed(2))}`);
                    assert.ok(camera.fov >= HOME_FOV - 1e-9 && camera.fov < 90);
                }
            };
            step(3.2, spot.view);
            const framed = frameRoomPreset(spot.view, aspect, { fov: HOME_FOV });
            assert.ok(Math.abs(camera.fov - framed.fov) < 1e-9, `${spot.id} ${label(aspect)} fov`);
            step(3.2, undefined);
            assert.equal(conversation.blend, 0);
            assert.equal(camera.fov, HOME_FOV, 'the lens closes again at home');
            assert.equal(camera.position.z, HOME[2]);
        }
    }
});

test('the desk arc bends round her head and still stops at the walls on a narrow screen', () => {
    const { via } = ROOM_HOTSPOTS.deskSeatSpot.view;
    assert.ok(via[0] > WALLS.max[0], 'its control point stands beyond the right wall');
    const camera = new THREE.PerspectiveCamera(HOME_FOV, 9 / 16, 0.1, 1000);
    const conversation = new ConversationCamera(camera);
    let widest = -Infinity;
    for (let t = 0; t < 3.2; t += 1 / 30) {
        conversation.update(1 / 30, t, ROOM_HOTSPOTS.deskSeatSpot.view);
        widest = Math.max(widest, camera.position.x);
    }
    assert.equal(widest, ROOM_CAMERA_BOUNDS.max[0]);
});

test('home frames her at any aspect and keeps its normal lens', () => {
    for (const aspect of ASPECTS) {
        const camera = new THREE.PerspectiveCamera(HOME_FOV, aspect, 0.1, 1000);
        new ConversationCamera(camera).update(1 / 30, 0, undefined);
        camera.updateMatrixWorld(true);
        assert.equal(camera.fov, HOME_FOV);
        // Her head, hips and feet standing on the platform (measured like MEASURED).
        for (const point of [[0.005, 1.007, -0.003], [0.002, 0.14, 0], [0.143, -1.066, -0.001], [-0.14, -1.066, -0.001]]) {
            const { ndc, visible } = onScreen(camera, point);
            assert.ok(visible, `${label(aspect)}: ${point} at ${ndc.x.toFixed(2)}, ${ndc.y.toFixed(2)}`);
        }
    }
});

test('the avatar zoom still closes in on her head at the desk on a portrait screen', () => {
    const head = MEASURED.deskSeatSpot[0];
    const { camera } = settledCamera(ROOM_HOTSPOTS.deskSeatSpot.view, 9 / 16, { zoom: 2.5, focus: head });
    const { ndc, visible } = onScreen(camera, head);
    assert.ok(visible && Math.hypot(ndc.x, ndc.y) < 1e-6, `head at ${ndc.x}, ${ndc.y}`);
    assert.ok(camera.fov > HOME_FOV, 'with the wider lens');
});

test('«Mimos» closes in with the normal lens and gives the wider one back afterwards', () => {
    const view = ROOM_HOTSPOTS.deskSeatSpot.view;
    const { camera, conversation } = settledCamera(view, 9 / 16);
    const wide = camera.fov;
    assert.ok(wide > HOME_FOV + 10);
    conversation.update(1 / 30, 4, view, null, 1);
    assert.equal(camera.fov, HOME_FOV, 'at the close-up');
    conversation.update(1 / 30, 4, view, null, 0.5);
    assert.ok(camera.fov > HOME_FOV && camera.fov < wide, 'on the way');
    conversation.update(1 / 30, 4, view, null, 0);
    assert.equal(camera.fov, wide, 'back at the desk view');
});

test('reset brings the normal lens back with the home view', () => {
    const { camera, conversation } = settledCamera(ROOM_HOTSPOTS.deskSeatSpot.view, 9 / 16);
    assert.ok(camera.fov > HOME_FOV);
    camera.fov = 44;   // Explorar's last preset
    conversation.reset();
    conversation.update(1 / 30, 0, undefined);
    assert.equal(camera.fov, HOME_FOV);
    assert.deepEqual(camera.position.toArray(), HOME);
});

test('Explorar: the sofa and bed views stay in the room; the Iso overview keeps looking in from outside', () => {
    for (const aspect of ASPECTS) {
        for (const name of ['sofaView', 'bedView']) {
            const preset = ROOM_DETAIL_PRESETS[name];
            const framed = frameRoomPreset(preset, aspect);
            assert.ok(within(framed.position, ROOM_CAMERA_BOUNDS), `${name} ${label(aspect)} at ${framed.position}`);
            assert.ok(framed.fov >= preset.fov && framed.fov < 90);
        }
        // Placed in front of the open room on purpose: no wall behind it, it backs off freely.
        const iso = ROOM_DETAIL_PRESETS.isometricView;
        const framed = frameRoomPreset(iso, aspect);
        const distance = Math.max(1, 1.5 / aspect);
        framed.position.forEach((v, i) => assert.ok(Math.abs(v - (iso.target[i] + (iso.position[i] - iso.target[i]) * distance)) < 1e-9));
        assert.equal(framed.fov, iso.fov);
    }
});

test('clampToRoom moves only what is outside', () => {
    assert.deepEqual(clampToRoom([0, 0, 0]), [0, 0, 0]);
    assert.deepEqual(clampToRoom([9, -9, -9]), [ROOM_CAMERA_BOUNDS.max[0], ROOM_CAMERA_BOUNDS.min[1], ROOM_CAMERA_BOUNDS.min[2]]);
});
