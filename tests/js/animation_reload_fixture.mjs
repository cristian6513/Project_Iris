import { readFileSync } from 'node:fs';
import * as THREE from '../../companion/node_modules/three/build/three.module.js';
import { disposeAvatarTree } from '../../face/js/avatar_lifecycle.js';
export { THREE };
export const tick = () => new Promise(setImmediate);
export const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const source = name => readFileSync(new URL('../../face/js/' + name, import.meta.url), 'utf8');

export function fixture() {
    const loads = [], gates = new Map(), clips = new Map();
    async function read(url, format) { loads.push({ url, format }); if (gates.has(url)) await gates.get(url).promise; if (!clips.has(url)) throw Error('missing ' + url); return clips.get(url); }
    class Loader {
        register() {}
        load(url, done, progress, fail) { read(url, 'vrma').then(clip => done({ scene: new THREE.Group(), userData: { vrmAnimations: [clip] } }), fail); }
    }
    let code = source('vrm_animation_loader.js').replace(/^import .*;\r?$/gm, '').replace('export class', 'class')
        .replace('let _vrmAnimPlugin = null;', 'let _vrmAnimPlugin = class {};')
        .replace('let _createClip = null;', 'let _createClip = clip => clip.clone();');
    for (const format of ['BVH', 'FBX', 'GLB']) code = code.replace(
        new RegExp("import\\('\\./" + format.toLowerCase() + "_retargeter\\.js[^']*'\\)"),
        `Promise.resolve({loadAndRetarget${format}: async url => ({clip: (await read(url, '${format.toLowerCase()}')).clone()})})`);
    const Player = new Function('THREE', 'GLTFLoader', 'disposeAvatarTree', 'read', code + '\nreturn VRMAnimationPlayer;')(THREE, Loader, disposeAvatarTree, read);
    const Idle = new Function('THREE', 'VRMAnimationPlayer', 'fetch', source('idle_system.js').replace(/^import .*;\r?$/gm, '').replace('export class', 'class') + '\nreturn IdleSystem;')(
        THREE, Player, () => { throw Error('Unexpected local fetch'); });
    const root = new THREE.Group(), hips = new THREE.Bone(); hips.name = 'hips'; root.add(hips); root.position.y = 0.7;
    const vrm = { scene: root, humanoid: { getNormalizedBoneNode: name => name === 'hips' ? hips : null } };
    const avatar = { avatar: { group: root, vrm, restYaw: 0 } };
    const idle = new Idle(avatar); idle.nextAction = 100000; idle._vrmaInitAttempted = true;
    const player = new Player(vrm); idle._vrmaPlayer = player;
    function clip(url, x, duration = 2) {
        const times = duration ? [0, duration] : [0];
        const values = duration ? [x, 1, 0, x + 0.1, 1, 0] : [x, 1, 0];
        const clip = new THREE.AnimationClip(url, duration, [new THREE.VectorKeyframeTrack('hips.position', times, values)]);
        clips.set(url, clip); return clip;
    }
    function snapshot(revision, entries, extra = {}) {
        return { revision, manifest: { clips: Object.fromEntries(Object.entries(entries).map(([n, d]) => [n, d.filename])), return_idle_pool: ['idle'], ...extra }, resources: entries };
    }
    function resource(filename, revision = filename) { return { filename, revision, format: filename.split('.').pop(), url: '/face/animations/' + filename }; }
    function advance(seconds) { for (let t = 0; t < seconds; t += 1 / 60) idle.update(Math.min(1 / 60, seconds - t), t); }
    return { Player, idle, player, avatar, hips, root, clips, loads, gates, clip, snapshot, resource, advance };
}
