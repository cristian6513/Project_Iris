import * as THREE from 'three';
import { MMDLoader } from 'three/addons/loaders/MMDLoader.js';
import { zipSync } from 'three/addons/libs/fflate.module.js';
import { Parser } from 'three/addons/libs/mmdparser.module.js';
import { VRMExpression, VRMExpressionManager, VRMExpressionMorphTargetBind, VRMHumanoid } from '@pixiv/three-vrm';
import { disposeAvatarTree } from './avatar_lifecycle.js?v=4';
import { irisForm } from './iris_dialog.js?v=9';
import { archiveKey, extensionOf, openPmxPackage, PMX_AVATAR_LIMIT, PMX_AVATAR_EXPANDED_LIMIT, PMX_PACKAGE_LIMIT } from './pmx_archive.js';
import { useMmdAmbient, useOverlayAlpha } from './pmx_material.js?v=2';
// Brows, eyes and mouth per emotion, merged into one morph each (the loader copies the mesh per morph).
import { emotionMouthWeight, MOUTH_SUFFIX, normalizeMorphName, PMX_EYE_CLOSING, selectExpressions } from './pmx_expressions.js?v=10';

const VIRTUAL_ROOT = '/iris-mmd-assets/';
// .sph and .spa are MMD sphere maps: BMP images, sometimes PNG ones, by another name. The
// browser reads the image by its content.
const MIME = { pmx: 'application/octet-stream', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', bmp: 'image/bmp',
    sph: 'image/bmp', spa: 'image/bmp', tga: 'image/x-tga' };
const BODY_CHAINS = [
    ['hips', 'spine', 'head'],
    ['spine', 'leftUpperArm', 'leftLowerArm', 'leftHand'],
    ['spine', 'rightUpperArm', 'rightLowerArm', 'rightHand'],
    ['hips', 'leftUpperLeg', 'leftLowerLeg', 'leftFoot'],
    ['hips', 'rightUpperLeg', 'rightLowerLeg', 'rightFoot'],
];
const normalizeName = normalizeMorphName;

function parsePmx(content) {
    return new Parser().parsePmx(content.buffer.slice(content.byteOffset, content.byteOffset + content.byteLength), true);
}

/** The textures a parsed PMX draws with, as paths relative to the model. */
function usedTextures(data) {
    const indices = new Set();
    for (const material of data.materials) {
        indices.add(material.textureIndex);
        if (material.envFlag === 1 || material.envFlag === 2) indices.add(material.envTextureIndex);
        if (material.toonFlag === 0) indices.add(material.toonIndex);
    }
    indices.delete(-1);
    return [...indices].map(index => data.textures[index]);
}

const openPackage = (bytes, options) => openPmxPackage(bytes, { ...options, texturesOf: content => usedTextures(parsePmx(content)) });

async function loadMesh(bytes) {
    const { entries, models, read } = openPackage(bytes);
    if (models.length !== 1) throw new Error('El ZIP debe contener un solo modelo PMX con sus texturas.');
    const [model] = models;
    const header = read(model);
    if (header.length < 17 || String.fromCharCode(...header.subarray(0, 4)) !== 'PMX ') {
        throw new Error('El archivo PMX del ZIP no es válido.');
    }
    const urls = new Map();
    let mesh;
    try {
        for (const entry of entries.values()) {
            const extension = extensionOf(entry.name);
            if (entry.directory || !Object.hasOwn(MIME, extension)) continue;
            const content = entry === model ? header : read(entry);
            urls.set(entry.key, URL.createObjectURL(new Blob([content], { type: MIME[extension] })));
        }
        const manager = new THREE.LoadingManager();
        manager.setURLModifier(url => {
            if (url.startsWith('data:')) return url; // Three.js' built-in MMD toon ramps.
            if (!url.startsWith(VIRTUAL_ROOT)) throw new Error('Las texturas del PMX deben estar dentro del ZIP.');
            const key = archiveKey(url.slice(VIRTUAL_ROOT.length), { texture: true });
            const resource = urls.get(key);
            if (!resource) throw new Error(`Falta una textura del PMX en el ZIP: ${key}`);
            return resource;
        });
        const loader = new MMDLoader(manager);
        const url = VIRTUAL_ROOT + model.name;
        return await new Promise((resolve, reject) => {
            let overlayVertices = new Set();
            manager.onLoad = () => {
                if (!mesh) return reject(new Error('No se pudo cargar el PMX.'));
                // Blush, tears and marks: see-through pieces the loader may have left opaque.
                useOverlayAlpha(mesh, overlayVertices);
                resolve(mesh);
            };
            manager.onError = () => reject(new Error('No se pudo leer una textura del PMX.'));
            loader.loadPMX(url, data => {
                if (data.metadata.vertexCount > 250_000 || data.metadata.boneCount > 2_000) {
                    throw new Error('El PMX supera el límite de vértices o huesos.');
                }
                const selection = selectExpressions(data);
                const used = selection.used;
                overlayVertices = selection.overlayVertices;
                console.log('[PMX] expresiones:', Object.entries(used).map(([role, names]) => `${role}=${names.join('+')}`).join(' '));
                if (Object.keys(selection.calibration ?? {}).length) console.log('[PMX] calibración de ojos:', JSON.stringify(selection.calibration));
                mesh = loader.meshBuilder.build(data, THREE.LoaderUtils.extractUrlBase(url), undefined, reject);
                // Ambient times texture, as in MMD: MMDLoader's flat glow greyed every dark colour.
                useMmdAmbient(mesh.material, data.materials);
                // An empty position morph array enables shader code with no morph targets.
                if (data.metadata.morphCount === 0) delete mesh.geometry.morphAttributes.position;
            }, undefined, reject);
        });
    } catch (error) {
        if (mesh) disposeAvatarTree(mesh);
        if (error instanceof RangeError) throw new Error('El archivo PMX del ZIP está incompleto.');
        throw error;
    } finally {
        for (const url of urls.values()) URL.revokeObjectURL(url);
    }
}

function isAncestor(ancestor, node) {
    for (let parent = node?.parent; parent; parent = parent.parent) {
        if (ancestor === parent) return true;
    }
    return false;
}

/**
 * VRM animations turn each bone away from a T-pose, and MMD models are modelled in an A-pose.
 * With the A-pose as the humanoid's rest, every clip lowers the arms some 35° too far, behind
 * the body. The arms are raised to the T-pose before the humanoid takes its rest: the skin
 * follows the bones, nothing is rebound. Her left is +X here, as in a VRM.
 */
function standInTPose(raw) {
    const start = new THREE.Vector3(), end = new THREE.Vector3(), along = new THREE.Vector3(), out = new THREE.Vector3();
    const own = new THREE.Quaternion(), parent = new THREE.Quaternion(), turn = new THREE.Quaternion();
    for (const [side, sign] of [['left', 1], ['right', -1]]) {
        out.set(sign, 0, 0);
        const joints = [raw[side + 'UpperArm'], raw[side + 'LowerArm'], raw[side + 'Hand'], raw[side + 'MiddleProximal']];
        for (let i = 0; i < 3; i++) {
            const [bone, next] = [joints[i], joints[i + 1]];
            if (!bone || !next) continue;
            bone.updateWorldMatrix(true, true);
            along.subVectors(next.getWorldPosition(end), bone.getWorldPosition(start)).normalize();
            turn.setFromUnitVectors(along, out);
            bone.parent.getWorldQuaternion(parent).invert();
            bone.quaternion.copy(parent.multiply(turn.multiply(bone.getWorldQuaternion(own))));
        }
        joints[0]?.updateWorldMatrix(false, true);
    }
}

function mapHumanoid(mesh) {
    const bones = new Map(mesh.skeleton.bones.map(bone => [normalizeName(bone.name), bone]));
    const find = (...names) => names.map(name => bones.get(normalizeName(name))).find(Boolean);
    const skinWeight = mesh.geometry.getAttribute('skinWeight');
    const skinIndex = mesh.geometry.getAttribute('skinIndex');
    const weightTotals = new Float64Array(mesh.skeleton.bones.length);
    for (let vertex = 0; vertex < skinWeight.count; vertex++) {
        for (let component = 0; component < 4; component++) {
            weightTotals[skinIndex.getComponent(vertex, component)] += skinWeight.getComponent(vertex, component);
        }
    }
    const weights = new Map(mesh.skeleton.bones.map((bone, index) => [bone, weightTotals[index]]));
    // MMD legs come twice: the bones the leg is moved by (足, ひざ, 足首) and those the skin follows
    // (足D...), kept together by the model's grant rotation. Iris moves whichever carries the skin;
    // its twin is turned with it, or the rigid bodies on it (the thighs a skirt rests on) would stay
    // standing while she sits, and the skirt would go through her thighs.
    const twins = [];
    const raw = { spine: find('上半身', 'spine'), chest: find('上半身2', 'chest'), neck: find('首', 'neck'), head: find('頭', 'head') };
    for (const [side, prefix] of [['left', '左'], ['right', '右']]) {
        for (const [part, name] of Object.entries({ Eye: '目', Shoulder: '肩', UpperArm: '腕', LowerArm: 'ひじ', Hand: '手首' })) {
            raw[side + part] = find(prefix + name, side + part);
        }
        // One chain per leg, all control bones or all «D» bones, so each bone hangs from the one before
        // it. A model taken from a game (2026-11-25) keeps its calf skin on its own bones under the
        // control knee: both knees weighed 0, and choosing bone by bone mixed 左足D with 左ひざ, which
        // hangs from 左足, and the model was refused. The chain with more skin is moved; its twins
        // turn with it, so skin on either chain follows.
        const legs = Object.entries({ UpperLeg: '足', LowerLeg: 'ひざ', Foot: '足首' }).map(([part, name]) =>
            ({ part, control: find(prefix + name, side + part), deform: find(prefix + name + 'D') }));
        const skin = key => legs.reduce((sum, leg) => sum + (weights.get(leg[key]) ?? 0), 0);
        const useDeform = legs.every(leg => leg.deform) && skin('deform') > skin('control');
        for (const { part, control, deform } of legs) {
            raw[side + part] = useDeform ? deform : (control ?? deform);
            if (control && deform) twins.push(raw[side + part] === deform ? [deform, control] : [control, deform]);
        }
        for (const [finger, mmd] of Object.entries({ Index: '人指', Middle: '中指', Ring: '薬指', Little: '小指' })) {
            ['Proximal', 'Intermediate', 'Distal'].forEach((segment, index) => {
                raw[side + finger + segment] = find(prefix + mmd + (index + 1), side + finger + segment);
            });
        }
        ['Metacarpal', 'Proximal', 'Distal'].forEach((segment, index) => {
            raw[side + 'Thumb' + segment] = find(prefix + '親指' + index, side + 'Thumb' + segment);
        });
    }
    const trunk = [raw.spine, raw.leftUpperLeg, raw.rightUpperLeg];
    raw.hips = ['腰', '下半身', 'センター', 'hips', 'pelvis'].map(name => find(name))
        .find(candidate => candidate && trunk.every(bone => isAncestor(candidate, bone)));
    const missing = [...new Set(BODY_CHAINS.flat().filter(role => !raw[role]))];
    if (missing.length) throw new Error(`El PMX no tiene huesos humanoides reconocibles: ${missing.join(', ')}.`);
    if (BODY_CHAINS.some(chain => chain.slice(1).some((role, index) => !isAncestor(raw[chain[index]], raw[role])))) {
        throw new Error('El PMX usa un esqueleto que Iris aún no puede adaptar.');
    }
    standInTPose(raw);
    const humanoid = new VRMHumanoid(Object.fromEntries(Object.entries(raw).filter(([, node]) => node).map(([role, node]) => [role, { node }])));
    return { humanoid, twins };
}

const _twinTurn = new THREE.Quaternion();
const _parentTurn = new THREE.Quaternion();

/** Turn each twin leg bone as the bone that moves (world turn, from the hip down). */
export function followTwins(twins) {
    for (const [moved, twin] of twins) {
        if (!twin.parent) continue;
        moved.updateWorldMatrix(true, false);
        twin.parent.updateWorldMatrix(true, false);
        moved.getWorldQuaternion(_twinTurn);
        twin.parent.getWorldQuaternion(_parentTurn).invert();
        twin.quaternion.copy(_parentTurn.multiply(_twinTurn));
        twin.updateMatrixWorld(false);
    }
}

const _grantTurn = new THREE.Quaternion();

/**
 * The model's grant bones (付与) IRIS can follow: a bone that copies part of another's turn, as
 * MMD does every frame. A shoulder pad turns with half the arm, a necklace with the neck, a game
 * dancer's waist ring with the invisible physics skirt that bends it (2026-11-25): left rigid, the
 * ring stood up in front of her legs as she sat and stood on end as she lay. Left out: what holds
 * the humanoid (MMD's waist cancel undoes the hips' turn under the legs, which the clips need), the
 * leg twins (followTwins), bones the physics moves, local and position-only grants. MMDLoader
 * sorts them parents first.
 */
export function grantedBones(mesh, humanoid, twins = []) {
    const { grants = [], rigidBodies = [] } = mesh.geometry.userData.MMD ?? {};
    const bones = mesh.skeleton.bones;
    const human = Object.values(humanoid.humanBones).map(bone => bone?.node).filter(Boolean);
    const twinned = new Set(twins.flat());
    const simulated = new Set(rigidBodies.filter(body => body.type !== 0).map(body => bones[body.boneIndex]).filter(Boolean));
    const holdsHuman = bone => human.some(node => {
        for (let up = node; up; up = up.parent) if (up === bone) return true;
        return false;
    });
    return grants.flatMap(({ index, parentIndex, ratio, affectRotation, isLocal }) => {
        const target = bones[index], source = bones[parentIndex];
        if (!target || !source || target === source || !affectRotation || isLocal || !Number.isFinite(ratio)) return [];
        if (twinned.has(target) || simulated.has(target) || holdsHuman(target)) return [];
        return [{ target, source, ratio, rest: target.quaternion.clone() }];
    });
}

/** Each granted bone: its rest turn, then its source's turn with the angle times the ratio. */
export function applyGrants(granted) {
    for (const { target, source, ratio, rest } of granted) {
        const q = source.quaternion;
        const sign = q.w < 0 ? -1 : 1;
        const w = Math.min(1, sign * q.w);
        const s = Math.sqrt(Math.max(0, 1 - w * w));
        target.quaternion.copy(rest);
        if (s < 1e-6) continue;
        const half = Math.acos(w) * ratio;
        const k = sign * Math.sin(half) / s;
        target.quaternion.multiply(_grantTurn.set(q.x * k, q.y * k, q.z * k, Math.cos(half)));
    }
}

export async function loadPmxAvatar(bytes, { simulate = false } = {}) {
    const mesh = await loadMesh(bytes);
    const scene = new THREE.Group();
    scene.add(mesh);
    try {
        // Keep the PMX rest pose for retargeting. MMDLoader already converts handedness.
        mesh.scale.setScalar(0.08);
        mesh.frustumCulled = false;
        scene.updateMatrixWorld(true);
        // The rigid bodies were placed on the bones as modelled: physics starts from that pose.
        const modelled = mesh.skeleton.bones.map(bone => [bone.position.clone(), bone.quaternion.clone()]);
        const { humanoid, twins } = mapHumanoid(mesh);
        const granted = grantedBones(mesh, humanoid, twins);
        scene.add(humanoid.normalizedHumanBonesRoot);
        const expressionManager = new VRMExpressionManager();
        const expressions = new Map();
        const expressionFor = (role) => {
            let expression = expressions.get(role);
            if (!expression) {
                expression = new VRMExpression(role);
                // Smiling eyes are already half closed: the blink gives way, as in VRM presets.
                if (PMX_EYE_CLOSING.has(role)) expression.overrideBlink = 'blend';
                expressionManager.registerExpression(expression);
                scene.add(expression);
                expressions.set(role, expression);
            }
            return expression;
        };
        // An emotion's mouth (`happyMouth`…) is a second bind of the same expression, eased while
        // she speaks (emotionMouthWeight).
        const mouthBinds = [];
        for (const [name, index] of Object.entries(mesh.morphTargetDictionary ?? {})) {
            const mouth = name.length > MOUTH_SUFFIX.length && name.endsWith(MOUTH_SUFFIX);
            const bind = new VRMExpressionMorphTargetBind({ primitives: [mesh], index, weight: 1 });
            expressionFor(mouth ? name.slice(0, -MOUTH_SUFFIX.length) : name).addBind(bind);
            if (mouth) mouthBinds.push(bind);
        }
        const speaking = () => Math.max(...['aa', 'ih', 'ou', 'ee', 'oh'].map(n => expressionManager.getValue(n) || 0));
        let physics = null;
        if (simulate && mesh.geometry.userData.MMD.rigidBodies.some(body => body.type !== 0)) {
            try {
                const { createPmxPhysics } = await import('./pmx_physics.js?v=5');
                physics = await createPmxPhysics(mesh, humanoid, modelled, twins);
            } catch (error) {
                console.warn('[PMX] No se pudieron iniciar las físicas.', error);
            }
        }
        return {
            scene, humanoid, expressionManager, meta: { metaVersion: '1' },
            get pmxPhysics() { return physics?.active ? physics : null; },
            resetPhysics() { physics?.reset(); },
            holdHair(on) { physics?.holdHair(on); },
            disposePhysics() { physics?.dispose(); physics = null; },
            update(delta) {
                humanoid.update();
                followTwins(twins);
                applyGrants(granted);
                if (mouthBinds.length) {
                    const weight = emotionMouthWeight(speaking());
                    for (const bind of mouthBinds) bind.weight = weight;
                }
                expressionManager.update();
                physics?.update(delta);
            },
        };
    } catch (error) {
        disposeAvatarTree(scene);
        throw error;
    }
}

function selectedZip(pkg, model) {
    const content = pkg.read(model);
    let data;
    try {
        data = parsePmx(content);
    } catch {
        throw new Error('No se pudo leer el modelo PMX elegido.');
    }
    const selected = new Set();
    for (const texture of usedTextures(data)) {
        if (!texture) throw new Error('El PMX elegido tiene una referencia de textura inválida.');
        const entry = pkg.texture(model, texture);
        if (!entry || entry.directory || !Object.hasOwn(MIME, extensionOf(entry.name)) || extensionOf(entry.name) === 'pmx') {
            throw new Error('Falta una textura del PMX en el ZIP. ' + texture);
        }
        selected.add(entry);
    }
    const expanded = [...selected].reduce((sum, entry) => sum + entry.size, model.size);
    if (expanded > PMX_AVATAR_EXPANDED_LIMIT) throw new Error('El contenido del avatar elegido supera el límite permitido.');
    const files = [[model.name, [content, { level: 1 }]]];
    for (const entry of selected) files.push([entry.name, [pkg.read(entry), { level: 0 }]]);
    const bytes = zipSync(Object.fromEntries(files));
    if (bytes.length > PMX_AVATAR_LIMIT) throw new Error('El avatar elegido supera el límite de 64 MiB.');
    return bytes;
}

export async function preparePmxUpload(file, signal) {
    if (signal?.aborted) return null;
    if (file.size > PMX_PACKAGE_LIMIT) throw new Error('El ZIP MMD supera el límite de 128 MiB.');
    let bytes = new Uint8Array(await file.arrayBuffer());
    if (signal?.aborted) return null;
    let pkg = openPackage(bytes, { forImport: true });
    const { models } = pkg;
    let index = 0;
    if (models.length > 1) {
        const labels = models.map(model => model.name.split('/').at(-1).slice(0, -4));
        const answer = await irisForm({
            title: 'Elige una variante',
            message: 'El ZIP contiene varios modelos PMX. Iris guardará la variante que elijas con sus texturas.',
            confirmLabel: 'Usar variante',
            signal,
            fields: [{ name: 'model', label: 'Modelo', required: true, placeholder: 'Selecciona un modelo',
                choices: labels.map((label, i) => ({ value: String(i),
                    label: labels.indexOf(label) === labels.lastIndexOf(label) ? label : models[i].name })) }],
        });
        if (!answer || signal?.aborted) return null;
        index = Number(answer.model);
        if (!models[index]) throw new Error('Selecciona una variante del ZIP.');
    }
    const label = models[index].name.split('/').at(-1).slice(0, -4);
    // A ZIP rebuilt here names its files in UTF-8: the saved avatar never needs guessing again.
    if (models.length > 1 || pkg.encoding !== 'utf-8' || file.size > PMX_AVATAR_LIMIT || pkg.expanded > PMX_AVATAR_EXPANDED_LIMIT) {
        bytes = selectedZip(pkg, models[index]);
        const name = models.length > 1 ? file.name.slice(0, -4).slice(0, 52) + '-' + (index + 1) + '.zip' : file.name;
        file = new File([bytes], name, { type: 'application/zip' });
    }
    pkg = null;
    const avatar = await loadPmxAvatar(bytes);
    disposeAvatarTree(avatar.scene);
    return signal?.aborted ? null : { file, label };
}
