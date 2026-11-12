import assert from 'node:assert/strict';
import test from 'node:test';
import { ROOM_OBJECT_ACTIONS } from '../../face/js/room_actions.js';
import { ROOM_HOTSPOTS } from '../../face/js/room_hotspots.js';
import { ROOM_DETAIL_PRESETS } from '../../face/js/room_framing.js';

// The camera that shows IRIS at each spot: the main view (app.js) for the bookshelf, and the
// Explorar presets for the sofa and bed, whose spots sit at the main view's edge.
const MAIN_VIEW = [0.34, 1.38, 4.95];
const CASES = [
    ['bookshelf', 'standNearBookshelf', MAIN_VIEW],
    ['sofa', 'standNearSofa', ROOM_DETAIL_PRESETS.sofaView.position],
    ['bed', 'standNearBed', ROOM_DETAIL_PRESETS.bedView.position],
];
const yaw = (from, to) => Math.atan2(to[0] - from[0], to[2] - from[2]);
const wrap = (a) => Math.atan2(Math.sin(a), Math.cos(a));

for (const [object, id, camera] of CASES) {
    test(`standing near the ${object}, IRIS looks at it in three-quarters and keeps that yaw`, () => {
        const action = ROOM_OBJECT_ACTIONS[object].actions.find((a) => a.id === id);
        assert.ok(action?.enabled);
        assert.equal(action.endMode, 'stay');
        assert.equal(action.faceTarget, true);
        assert.ok(action.lookAtWeight > 0 && action.lookAtWeight <= 0.3, 'the head leans to the object, the face stays visible');
        const spot = ROOM_HOTSPOTS[action.targetHotspot].position;
        const toObject = yaw(spot, action.lookAtTarget);
        const toCamera = yaw(spot, camera);
        // goToHotspot: the yaw toward lookAtTarget plus facingYawOffset.
        const body = toObject + action.facingYawOffset;
        const bodyToCamera = Math.abs(wrap(toCamera - body)) * 180 / Math.PI;
        const bodyToObject = Math.abs(wrap(toObject - body)) * 180 / Math.PI;
        assert.ok(Math.abs(bodyToCamera - 45) < 2, `body ${bodyToCamera.toFixed(1)}° from its camera`);
        assert.ok(bodyToObject < 100, `the ${object} stays to her side (${bodyToObject.toFixed(0)}°)`);
        // Turned from the object toward the camera, never past it.
        assert.ok(Math.sign(action.facingYawOffset) === Math.sign(wrap(toCamera - toObject)));
    });
}

test('«Usar PC 2», an old draft at another chair spot, is gone; «Usar PC» stays (QA23)', async () => {
    const { getRoomAction, getRoomActionCommand } = await import('../../face/js/room_actions.js');
    const { getHotspotForAction } = await import('../../face/js/room_hotspots.js');
    const { readFileSync } = await import('node:fs');
    const ids = Object.values(ROOM_OBJECT_ACTIONS).flatMap((object) => object.actions.map((action) => action.id));
    assert.ok(!ids.includes('usePC_blenderDraft'));
    for (const name of ['use_pc_2', 'use_pc_blender', 'usePC_blenderDraft']) {
        assert.equal(getRoomAction(name), null, name);
        assert.equal(getRoomActionCommand(name), null, name);
        assert.equal(getHotspotForAction(name), null, name);
    }
    assert.equal(getRoomAction('use_pc')?.id, 'usePC_idle');
    const html = readFileSync(new URL('../../face/index.html', import.meta.url), 'utf8');
    assert.ok(!html.includes('data-room-action="use_pc_2"') && !html.includes('Usar PC 2'));
    assert.ok(html.includes('data-room-action="use_pc"'));
});
