/**
 * IRIS Room - Modular Cyberpunk AI Companion Apartment
 *
 * Phase 1 goal: replace the simple procedural set with a real modular
 * 3D room while preserving the public RoomEnvironment API used by app.js,
 * scene_actions.js, theme buttons, and inspect mode.
 */

import * as THREE from 'three';
import { conjuredBook, conjuredTablet } from './conjured_props.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { sharedKTX2, useKTX2 } from './ktx2_support.js?v=2';
import { softBox, createFabricTextures, createContactShadowTexture, createEdgeShadowTexture } from './room_surface_details.js?v=2';
import { RoomMediaDisplay } from './room_media_display.js?v=1';
import { RoomAtmosphere } from './room_atmosphere.js?v=3';
import { addFurnitureRim } from './room_furniture_lighting.js?v=1';
import { WindowBackdrop, WINDOW_BACKDROP_ASPECT, fitBackdropTexture } from './room_window_backdrop.js?v=2';

const GEOMETRY_CACHE = new Map();
// The floor, walls and window pictures are KTX2 (GPU-compressed); each keeps a PNG twin with
// the same name that loads if KTX2 cannot (_loadRoomTexture).
const ROOM_ASSETS = {
    cyberFloorPanels: '/face/public/assets/room/cyber-floor-panels-v1.ktx2',
    cyberLoungeRug: '/face/public/assets/room/cyber-lounge-rug-v1.png',
    cyberCircularRug: '/face/public/assets/room/cyber-circular-rug-muted-v2.png',
    cyberWallPanelsD: '/face/public/assets/room/cyber-wall-panels-d-v1.png',
    cyberWallHex: '/face/public/assets/room/cyber-wall-hex-v1.png',
    cyberWallComplete: '/face/public/assets/room/cyber-wall-complete-v1.ktx2',
    bookshelfModel: '/face/public/assets/room/bookshelf-v1.glb',
    sofaModel: '/face/public/assets/room/sofa-v1.glb',
    tableModel: '/face/public/assets/room/table-v1.glb',
    booksModel: '/face/public/assets/room/books-stack-v1.glb',
    bedModel: '/face/public/assets/room/bed-v1.glb',
    mugModel: '/face/public/assets/room/mug-pencils-v1.glb',
    chairModel: '/face/public/assets/room/chair-v1.glb',
    deskModel: '/face/public/assets/room/desk-v1.glb',
    monitorsModel: '/face/public/assets/room/monitors-v1.glb',
    tabletModel: '/face/public/assets/room/tablet-v2.glb',
    bookModel: '/face/public/assets/room/book-v1.glb',
    pencilsModel: '/face/public/assets/room/pencils-loose-v1.glb',
    speakerModel: '/face/public/assets/room/speaker-v1.glb',
    keyboardModel: '/face/public/assets/room/keyboard-mouse-v1.glb',
    towerLampModel: '/face/public/assets/room/tower-lamp-v1.glb',
    ceilingLampModel: '/face/public/assets/room/ceiling-lamp-v1.glb',
    shelfModel: '/face/public/assets/room/shelf-v1.glb',
    plantModel: '/face/public/assets/room/plant-v1.glb',
};

function cachedGeometry(key, factory) {
    let geometry = GEOMETRY_CACHE.get(key);
    if (!geometry) {
        geometry = factory();
        GEOMETRY_CACHE.set(key, geometry);
    }
    return geometry;
}

const ROOM_THEMES = {
    classic: {
        gold: 0xd4a017, accent: 0xa8d8ea, neon: 0xd4a017, cyan: 0xa8d8ea,
        magenta: 0xd46cff, amber: 0xd4a017, monitor: 0x3c72ff,
        ambient: 0x070912, wall: 0x10131d, wallPanel: 0x171b27,
        floor: 0x111018, ceiling: 0x0a0d14, fabric: 0x414957,
        linen: 0x8d9697, textileAccent: 0x7b6560, stitch: 0xb8ac98,
        wood: 0x22170f, glass: 0x6ea8ff,
        skyTop: '#07111f', skyMid: '#111523', skyBottom: '#23180f',
        cityA: '#d4a017', cityB: '#a8d8ea',
    },
    cyber: {
        gold: 0xffb86b, accent: 0xf36bce, neon: 0x8defff, cyan: 0x8defff,
        magenta: 0xf36bce, amber: 0xffb86b, monitor: 0x7fefff,
        ambient: 0x08111d, wall: 0x0b1524, wallPanel: 0x142035,
        floor: 0x0a1220, ceiling: 0x08101b, fabric: 0x354e62,
        linen: 0x83979e, textileAccent: 0x7d536c, stitch: 0x9bafb2,
        wood: 0x25170f, glass: 0x76e9ff,
        skyTop: '#071526', skyMid: '#13243a', skyBottom: '#27152b',
        cityA: '#8defff', cityB: '#f36bce',
    },
    hacker: {
        gold: 0x00ff41, accent: 0x39ff14, neon: 0x00ff41, cyan: 0x78ffaa,
        magenta: 0x28ff68, amber: 0x9eff7d, monitor: 0x00ff41,
        ambient: 0x010802, wall: 0x031007, wallPanel: 0x07180a,
        floor: 0x050c06, ceiling: 0x010601, fabric: 0x294637,
        linen: 0x849882, textileAccent: 0x596a43, stitch: 0xaab79a,
        wood: 0x10180a, glass: 0x39ff14,
        skyTop: '#001205', skyMid: '#031d08', skyBottom: '#000500',
        cityA: '#00ff41', cityB: '#78ffaa',
    },
    sakura: {
        gold: 0xff8ac8, accent: 0xffb6d8, neon: 0xff7ac8, cyan: 0x8be9ff,
        magenta: 0xff5fb7, amber: 0xffba83, monitor: 0xff78c8,
        ambient: 0x130615, wall: 0x130819, wallPanel: 0x211026,
        floor: 0x120917, ceiling: 0x09040d, fabric: 0x604255,
        linen: 0xb2a1ad, textileAccent: 0x946679, stitch: 0xccafba,
        wood: 0x261319, glass: 0xffb6d8,
        skyTop: '#17071c', skyMid: '#2a1027', skyBottom: '#16070f',
        cityA: '#ffb6d8', cityB: '#8be9ff',
    },
};

function box(w, h, d) {
    return cachedGeometry(`box:${w}:${h}:${d}`, () => new THREE.BoxGeometry(w, h, d));
}

function roundedCylinder(radiusTop, radiusBottom, height, segments = 48) {
    return cachedGeometry(`cyl:${radiusTop}:${radiusBottom}:${height}:${segments}`, () =>
        new THREE.CylinderGeometry(radiusTop, radiusBottom, height, segments));
}

function plane(w, h) {
    return cachedGeometry(`plane:${w}:${h}`, () => new THREE.PlaneGeometry(w, h));
}

function circle(radius, segments) {
    return cachedGeometry(`circle:${radius}:${segments}`, () => new THREE.CircleGeometry(radius, segments));
}

function torus(radius, tube, radialSegments, tubularSegments) {
    return cachedGeometry(`torus:${radius}:${tube}:${radialSegments}:${tubularSegments}`, () =>
        new THREE.TorusGeometry(radius, tube, radialSegments, tubularSegments));
}

function markTheme(material, binding) {
    material.userData.themeBinding = {
        color: binding.color,
        emissive: binding.emissive,
        rim: binding.rim,
    };
    return material;
}

// ---------------------------------------------------------------------
// Shared modular room section base
// ---------------------------------------------------------------------

class RoomSection {
    constructor(room, name) {
        this.room = room;
        this.group = new THREE.Group();
        this.group.name = name;
    }

    add(mesh) {
        this.group.add(mesh);
        return mesh;
    }

    mesh(geometry, material, position = null, rotation = null, scale = null) {
        const m = new THREE.Mesh(geometry, material);
        if (position) m.position.set(position[0], position[1], position[2]);
        if (rotation) m.rotation.set(rotation[0], rotation[1], rotation[2]);
        if (scale) m.scale.set(scale[0], scale[1], scale[2]);
        this.group.add(m);
        return m;
    }

    finish() {
        this.room.group.add(this.group);
        return this.group;
    }
}

// ---------------------------------------------------------------------
// AvatarPlatform
// ---------------------------------------------------------------------

// The circular rug lies on the floor, where avatars stand (y ≈ -1.185). The
// stage geometry was built this much higher; it drops instead of lifting
// every pose, seat and walk off the platform by the same amount.
const STAGE_FLOOR_DROP = 0.179;

class AvatarPlatform extends RoomSection {
    constructor(room) {
        super(room, 'AvatarPlatform');
    }

    // The visible stage is the circular rug (CyberRoom shell) over this base, top and
    // etched panels. Its rings, ticks, apron, marks and glows were all at opacity 0 and
    // still drawn every frame (84 draw calls), so they are gone.
    build() {
        const darkMetalMaterial = this.room.namedMaterial('darkMetalMaterial');
        const base = this.mesh(
            roundedCylinder(1.85, 1.98, 0.12, 96),
            darkMetalMaterial,
            [0, -1.14, 0],
        );
        base.name = 'avatar_platform_base';

        const top = this.mesh(
            roundedCylinder(1.62, 1.62, 0.025, 96),
            this.room.std({ color: 0x0b1724, emissive: 0x000000, emissiveIntensity: 0, transparent: true, opacity: 0.28, roughness: 0.28, metalness: 0.38 }),
            [0, -1.055, 0],
        );

        const etchedPanelMat = this.room.std({ color: 0x0d1a28, emissive: 'cyan', emissiveIntensity: 0.0, transparent: true, opacity: 0.08, roughness: 0.22, metalness: 0.55 });
        for (let i = 0; i < 10; i++) {
            const a = (i / 10) * Math.PI * 2;
            const panel = this.mesh(
                box(0.38, 0.012, 0.06),
                etchedPanelMat,
                [Math.sin(a) * 0.72, -1.003, Math.cos(a) * 0.72],
                [0, a, 0],
            );
            panel.name = 'platform_etched_panel';
        }
        // All of it is raised stage geometry built for the old platform height.
        for (const piece of this.group.children) piece.position.y -= STAGE_FLOOR_DROP;
        this.room.register('hero_stage', this.group);
        return this.finish();
    }
}

// ---------------------------------------------------------------------
// WindowCityBackdrop
// ---------------------------------------------------------------------

class WindowCityBackdrop extends RoomSection {
    constructor(room) {
        super(room, 'WindowCityBackdrop');
    }

    build() {
        const frameMat = this.room.namedMaterial('darkMetalMaterial');
        const city = new THREE.Group();
        city.name = 'city_skyline';
        this.group.add(city);

        const windowGroup = new THREE.Group();
        windowGroup.name = 'panoramic_window';
        this.group.add(windowGroup);

        // One recessed frame: jambs as deep as the sill and canopy, and two
        // mullions in front of the glass that split the opening into three
        // panes, leaving the centre clear behind the avatar.
        const framePieces = [
            [-3.95, 1.0, -4.58, 0.117, 3.1, 0.36],
            [3.95, 1.0, -4.58, 0.117, 3.1, 0.36],
            [-1.3, 0.99, -4.62, 0.06, 3.08, 0.2],
            [1.3, 0.99, -4.62, 0.06, 3.08, 0.2],
        ];
        framePieces.forEach(([x, y, z, w, h, d]) => {
            const frame = new THREE.Mesh(box(w, h, d), frameMat);
            frame.position.set(x, y, z);
            windowGroup.add(frame);
        });

        const sillMat = this.room.std({ color: 0x060a12, emissive: 'cyan', emissiveIntensity: 0.026, roughness: 0.36, metalness: 0.72 });
        const lowerSill = new THREE.Mesh(box(7.65, 0.18, 0.36), sillMat);
        lowerSill.name = 'window_deep_lower_sill';
        lowerSill.position.set(0, -0.64, -4.58);
        windowGroup.add(lowerSill);
        const upperCanopy = new THREE.Mesh(box(7.55, 0.14, 0.32), sillMat);
        upperCanopy.name = 'window_deep_upper_canopy';
        upperCanopy.position.set(0, 2.6, -4.58);
        windowGroup.add(upperCanopy);

        // The city photo stands about 5 m behind the glass so Explore cameras
        // get real parallax. It draws only where this invisible pane marks
        // the opening in the stencil buffer (after opaque geometry in front
        // has written depth), so the back wall needs no hole and the photo
        // never shows above the room as a billboard. App's render target must
        // keep its stencil buffer.
        const openingMask = new THREE.Mesh(
            plane(7.8, 3.08),
            new THREE.MeshBasicMaterial({
                colorWrite: false,
                depthWrite: false,
                stencilWrite: true,
                stencilRef: 1,
                stencilFunc: THREE.AlwaysStencilFunc,
                stencilZPass: THREE.ReplaceStencilOp,
            }),
        );
        openingMask.name = 'window_opening_stencil';
        openingMask.position.set(0, 0.99, -4.74);
        openingMask.renderOrder = 1;
        windowGroup.add(openingMask);

        // 24 units wide at the landscape's aspect covers the opening
        // from every preset camera.
        const cityBack = new THREE.Mesh(
            plane(24, 24 / WINDOW_BACKDROP_ASPECT),
            this.room.backdropMaterial(),
        );
        Object.assign(cityBack.material, {
            depthTest: false,
            depthWrite: false,
            stencilWrite: true,
            stencilRef: 1,
            stencilFunc: THREE.EqualStencilFunc,
        });
        cityBack.name = 'window_city_image_backdrop';
        cityBack.position.set(0, 0.79, -10);
        cityBack.renderOrder = 2;
        windowGroup.add(cityBack);

        // No hologram reticle or scan rings on the glass (they sat right above
        // the avatar's head like a halo) and no sill planters (flat cones that
        // covered the bottom of the view).
        this.room.register('window', windowGroup);
        this.room.register('city_skyline', city);
        this.room.register('window_city_glow', city);
        return this.finish();
    }
}

// ---------------------------------------------------------------------
// DeskArea
// ---------------------------------------------------------------------

class DeskArea extends RoomSection {
    constructor(room) {
        super(room, 'DeskArea');
    }

    build() {
        this.group.position.set(2.75, -0.40, -2.65);
        this.group.rotation.y = -0.08;

        // The user's desk (desk-v1.glb): frame and mat share one baked map, glass in the
        // right-hand compartment, neon strips. Its mat is 1.05 above the floor, the height
        // the props below and the sitting clips were laid out for. Base at its origin.
        const deskModel = this.room.models.desk;
        if (deskModel) {
            deskModel.traverse((child) => {
                if (!child.isMesh) return;
                const { name, map } = child.material;
                child.material.dispose();
                if (map) map.anisotropy = 4;
                if (name === 'desk_glass') {
                    child.material = this.room.std({ color: 'glass', emissive: 'cyan', emissiveIntensity: 0.06, transparent: true, opacity: 0.3, roughness: 0.18, metalness: 0.1, side: THREE.DoubleSide });
                } else if (name === 'desk_led_cyan' || name === 'desk_led_magenta') {
                    child.material = this.room.basic({ color: name === 'desk_led_cyan' ? 'cyan' : 'magenta', side: THREE.DoubleSide });
                } else {
                    const isMat = name === 'desk_mat';
                    child.material = this.room.std({ map, roughness: isMat ? 0.9 : 0.38, metalness: isMat ? 0 : 0.35, emissive: 'cyan', emissiveIntensity: 0.04 });
                    child.material.emissiveMap = map;
                }
            });
            deskModel.position.y = -0.8; // floor level: this group sits at y -0.40
            this.group.add(deskModel);
        }

        // The user's monitors (monitors-v1.glb): one wide screen and two portrait ones turned
        // towards the chair, their feet on the desk's riser (y 0.412, z -0.46 to -0.26 here).
        // The centre screen carries IRIS' activity canvas (hermes_activity.js binds it); the
        // portrait ones a tall decorative texture. Bodies are one mesh in the bezel material.
        const monitorGroup = new THREE.Group();
        monitorGroup.name = 'monitor';
        monitorGroup.position.set(0.1, 0.412, -0.36);
        const screens = {};
        const monitorModel = this.room.models.monitors;
        if (monitorModel) {
            const screenOpts = { transparent: true, roughness: 0.22, metalness: 0.04, side: THREE.DoubleSide };
            const materials = {
                monitor_body: () => this.room.std({ color: 0x05070c, emissive: 'cyan', emissiveIntensity: 0.035, roughness: 0.28, metalness: 0.72 }),
                screen_center: () => this.room.texturedMaterial('monitor', { ...screenOpts, color: 'monitor', emissive: 'monitor', emissiveIntensity: 0.46, opacity: 0.9 }),
                screen_left: () => this.room.texturedMaterial('monitorTall', { ...screenOpts, color: 'monitor', emissive: 'cyan', emissiveIntensity: 0.32, opacity: 0.82 }),
                screen_right: () => this.room.texturedMaterial('monitorTall', { ...screenOpts, color: 'monitor', emissive: 'monitor', emissiveIntensity: 0.26, opacity: 0.8 }),
            };
            monitorModel.traverse((child) => {
                if (!child.isMesh) return;
                const name = child.material.name;
                child.material.dispose();
                child.material = (materials[name] || materials.monitor_body)();
                if (name.startsWith('screen_')) screens[name] = child;
            });
            monitorGroup.add(monitorModel);
        }
        const screen = screens.screen_center;
        const sideScreenL = screens.screen_left;
        const sideScreenR = screens.screen_right;
        if (screen) screen.name = 'monitor_screen';
        if (sideScreenL) sideScreenL.name = 'monitor_screen_left';
        if (sideScreenR) sideScreenR.name = 'monitor_screen_right';
        this.group.add(monitorGroup);

        const wallTech = new THREE.Group();
        wallTech.name = 'desk_wall_tech_panels';
        wallTech.position.set(0.34, 1.06, -0.68);
        const panelGlass = this.room.basic({ color: 'cyan', transparent: true, opacity: 0.0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide });
        const panelLine = this.room.basic({ color: 'magenta', transparent: true, opacity: 0.0, depthWrite: false });
        const panelSpecs = [
            [-0.72, 0.18, 0.42, 0.32],
            [-0.2, 0.36, 0.36, 0.24],
            [0.42, 0.22, 0.52, 0.34],
            [0.9, 0.54, 0.28, 0.22],
        ];
        panelSpecs.forEach(([x, y, w, h], index) => {
            const panel = new THREE.Mesh(plane(w, h), panelGlass.clone());
            panel.position.set(x, y, 0);
            panel.userData.pcHologram = { phase: index * 0.55, baseOpacity: 0.0, activeOpacity: 0.16 };
            wallTech.add(panel);
            for (let i = 0; i < 3; i++) {
                const line = new THREE.Mesh(plane(w * (0.42 + i * 0.16), 0.006), panelLine.clone());
                line.position.set(x - w * 0.08, y + h * 0.23 - i * 0.06, 0.006);
                line.userData.holoLine = true;
                line.userData.phase = index + i * 0.3;
                line.userData.pcHologram = { phase: index + i * 0.3, baseOpacity: 0.0, activeOpacity: 0.22 };
                wallTech.add(line);
            }
        });
        this.group.add(wallTech);

        const deskHolograms = this._buildDeskHolograms();
        this.group.add(deskHolograms);

        // The user's keyboard and mouse (keyboard-mouse-v1.glb): 173 textured parts baked into
        // one mesh with a colour map and an RGB emission map. The origin is the keyboard's
        // centre; the mouse sits to its right. On the mat, in front of the chair.
        const keyboardModel = this.room.models.keyboard;
        if (keyboardModel) {
            keyboardModel.traverse((child) => {
                if (!child.isMesh) return;
                const { map, emissiveMap } = child.material;
                child.material.dispose();
                for (const texture of [map, emissiveMap]) if (texture) texture.anisotropy = 4;
                child.material = this.room.std({ map, roughness: 0.45, metalness: 0.25, emissive: 0xffffff, emissiveIntensity: 2.2 });
                child.material.emissiveMap = emissiveMap;
            });
            keyboardModel.name = 'keyboard';
            keyboardModel.position.set(0.1, 0.25, 0.03); // whole set on the mat (z -0.22 to 0.18)
            this.group.add(keyboardModel);
        }
        // The user's speaker (speaker-v1.glb, 20 cm), one copy each side outside the portrait
        // monitors, turned a little towards the chair. Body and smoked glass are shared; each
        // copy's cones glow in its own accent and pulse with the player (RoomMediaDisplay).
        const playerIndicators = [];
        const speakerModel = this.room.models.speaker;
        if (speakerModel) {
            const speakers = new THREE.Group();
            speakers.name = 'desk_speakers';
            const bodyMat = this.room.std({ color: 0x07090e, roughness: 0.4, metalness: 0.55, emissive: 'cyan', emissiveIntensity: 0.025 });
            const glassMat = this.room.std({ color: 0x0b1018, transparent: true, opacity: 0.38, roughness: 0.12, metalness: 0.3 });
            speakerModel.traverse((child) => {
                if (!child.isMesh) return;
                const name = child.material.name;
                child.material.dispose();
                child.material = name === 'speaker_glass' ? glassMat : bodyMat;
            });
            // Left on desk-v1.glb's raised back corner (y 0.31), right on the desk top.
            for (const [x, y, z, yaw, accent] of [[-1.04, 0.31, -0.35, 0.2, 'cyan'], [1.09, 0.236, -0.27, -0.2, 'magenta']]) {
                const speaker = speakerModel.clone();
                speaker.position.set(x, y, z);
                speaker.rotation.y = yaw;
                speaker.traverse((child) => {
                    if (!child.isMesh || child.name !== 'speaker_cones') return;
                    child.material = this.room.basic({ color: accent, transparent: true, opacity: 0.2, blending: THREE.AdditiveBlending, depthWrite: false });
                    playerIndicators.push(child);
                });
                speakers.add(speaker);
            }
            this.group.add(speakers);
        }

        const deskObjects = new THREE.Group();
        deskObjects.name = 'desk_lived_in_objects';
        // The user's tablet on a stand (tablet-v1.glb) shows the player state; its screen's UVs
        // keep the 16:10 player canvas in proportion. Turned towards the chair and the room camera.
        const tabletModel = this.room.models.tablet;
        if (tabletModel) {
            let tabletScreen = null;
            tabletModel.traverse((child) => {
                if (!child.isMesh) return;
                const isScreen = child.material.name === 'tablet_screen';
                child.material.dispose();
                child.material = isScreen
                    ? this.room.basic({ color: 0xffffff, side: THREE.DoubleSide })
                    : this.room.std({ color: 0x05070c, emissive: 'cyan', emissiveIntensity: 0.035, roughness: 0.3, metalness: 0.7 });
                if (isScreen) tabletScreen = child;
            });
            tabletModel.name = 'desk_tablet';
            tabletModel.position.set(0.94, 0.236, 0.17); // on the desk top, right of the mouse
            tabletModel.rotation.y = -0.45;
            deskObjects.add(tabletModel);
            // The panel leaves its stand when she takes the tablet: the base stays on the desk.
            const body = tabletModel.getObjectByName('tablet_body');
            if (body && tabletScreen) {
                const panel = new THREE.Group();
                panel.name = 'desk_tablet_panel';
                panel.add(body, tabletScreen);
                tabletModel.add(panel);
                this.room.register('desk_tablet_panel', panel);
                const held = conjuredTablet(body, tabletScreen, (params) => this.room.std(params));
                deskObjects.add(held);
                this.room.register('conjured_tablet', held);
            }
            if (tabletScreen) {
                tabletScreen.name = 'desk_tablet_glow';
                this.room.mediaDisplay = new RoomMediaDisplay(tabletScreen, playerIndicators);
                this.room.register('player_display', tabletScreen);
            }
        }
        // A copy of the user's pencil mug and a few of its pencils left lying on the mat.
        const mug = this.room.mugModel()?.clone();
        if (mug) {
            mug.name = 'desk_mug';
            mug.position.set(-0.88, 0.236, 0.18);
            mug.rotation.y = 0.5;
            deskObjects.add(mug);
        }
        const pencils = this.room.models.pencils;
        if (pencils) {
            pencils.traverse((child) => {
                if (!child.isMesh) return;
                const { map } = child.material;
                child.material.dispose();
                child.material = this.room.std({ map, roughness: 0.5, metalness: 0 });
            });
            pencils.name = 'desk_loose_pencils';
            // On the mat (x ≥ -0.6) left of the keyboard, turned lengthwise.
            pencils.position.set(-0.44, 0.25, -0.02);
            pencils.rotation.y = 1.25;
            deskObjects.add(pencils);
        }
        this.group.add(deskObjects);

        // The user's PC tower and lamp (tower-lamp-v1.glb). The tower stands in desk-v1.glb's
        // right-hand compartment (inside x 0.835-1.197, floor y -0.779, ceiling 0.067, back
        // z -0.459), its glass side towards the desk's glass.
        const towerLamp = this.room.towerLampModel();
        if (towerLamp?.tower) {
            towerLamp.tower.name = 'pc_tower';
            towerLamp.tower.position.set(1.016, -0.779, 0.0);
            this.group.add(towerLamp.tower);
        }

        const deskCableMat = this.room.basic({ color: 'cyan', transparent: true, opacity: 0.13, depthWrite: false });
        for (const [x, z, w] of [[-0.18, 0.34, 0.54], [0.48, 0.32, 0.42], [1.04, 0.31, 0.36]]) {
            const cable = new THREE.Mesh(box(w, 0.008, 0.012), deskCableMat);
            cable.position.set(x, 0.247, z);
            cable.rotation.y = 0.18;
            this.group.add(cable);
        }

        // The user's gamer chair (chair-v1.glb, about 4.7k triangles, its own colour, normal
        // and roughness/metal maps). Its seat is 0.675 above the floor, as on the old chair,
        // so the desk sitting clips still fit. The group keeps the old origin.
        const chair = new THREE.Group();
        chair.name = 'chair';
        // Centred on desk-v1.glb's knee space (x -0.64 to 0.84 here); pcSeatSpot follows.
        chair.position.set(0.1, -0.21, 0.92);
        const chairModel = this.room.models.chair;
        if (chairModel) {
            chairModel.traverse((child) => {
                if (!child.isMesh) return;
                const material = child.material;
                if (material.map) material.map.anisotropy = 4;
                material.side = THREE.DoubleSide; // the generated mesh has open seams
                // Its dark blue leather reads as a black shape from the room's camera;
                // a self-light from its own map keeps the leather and neon edges visible.
                material.emissive.setHex(0xffffff);
                material.emissiveMap = material.map;
                material.emissiveIntensity = 0.35;
            });
            chairModel.position.y = -0.59; // base on the floor (desk group y -0.40, chair -0.21)
            chairModel.rotation.y = Math.PI; // its front is +Z; the chair faces the desk
            chair.add(chairModel);
        }
        this.group.add(chair);

        // The desk lamp, left of the mat between the speaker and the mug, its shade turned
        // towards the keyboard. Its warm light sits where the user marked it under the shade.
        const lamp = towerLamp?.lamp || new THREE.Group();
        lamp.name = 'desk_lamp';
        lamp.position.set(-1.0, 0.236, 0.0);
        lamp.rotation.y = 1.2;
        const lampLight = new THREE.PointLight(ROOM_THEMES.cyber.amber, 0.82, 3.05);
        (lamp.getObjectByName('lamp_light') || lamp).add(lampLight);
        this.room.addLight(lampLight);
        this.group.add(lamp);

        const monitorGlow = new THREE.PointLight(ROOM_THEMES.cyber.monitor, 0.58, 2.9);
        monitorGlow.name = 'monitor_glow';
        monitorGlow.position.set(2.75, 0.40, -3.0);
        this.room.scene.add(monitorGlow);
        this.room.addLight(monitorGlow);
        this.room.register('monitor_glow', monitorGlow);

        this.room.register('desk', this.group);
        this.room.register('monitor', monitorGroup);
        this.room.register('monitor_screen', screen);
        this.room.register('monitor_screens', [screen, sideScreenL, sideScreenR].filter(Boolean));
        this.room.register('desk_holograms', deskHolograms);
        this.room.register('chair', chair);
        this.room.register('desk_lamp', lamp);
        this.room.lampLight = lampLight;
        return this.finish();
    }

    _buildDeskHolograms() {
        const group = new THREE.Group();
        group.name = 'desk_holograms';
        const panelMat = this.room.basic({
            color: 'cyan',
            transparent: true,
            opacity: 0.0,
            side: THREE.DoubleSide,
            depthWrite: false,
            blending: THREE.AdditiveBlending,
        });
        const lineMat = this.room.basic({
            color: 'magenta',
            transparent: true,
            opacity: 0.0,
            depthWrite: false,
            blending: THREE.AdditiveBlending,
        });
        const specs = [
            { x: -1.02, y: 0.98, z: -0.34, ry: 0.34, w: 0.46, h: 0.31 },
            { x: 1.04, y: 0.94, z: -0.34, ry: -0.32, w: 0.42, h: 0.28 },
        ];
        specs.forEach((spec, index) => {
            const panel = new THREE.Mesh(plane(spec.w, spec.h), panelMat.clone());
            panel.name = `desk_holo_panel_${index + 1}`;
            panel.position.set(spec.x, spec.y, spec.z);
            panel.rotation.y = spec.ry;
            panel.userData.pcHologram = { phase: index * 0.9, baseOpacity: 0.0, activeOpacity: 0.16 };
            group.add(panel);

            for (let i = 0; i < 4; i++) {
                const line = new THREE.Mesh(plane(spec.w * (0.52 + i * 0.08), 0.006), lineMat.clone());
                line.position.set(spec.x - spec.w * 0.04 + i * 0.012, spec.y + spec.h * 0.22 - i * 0.052, spec.z + 0.006);
                line.rotation.y = spec.ry;
                line.userData.holoLine = true;
                line.userData.pcHologramLine = true;
                line.userData.phase = index + i * 0.35;
                line.userData.pcHologram = { phase: index + i * 0.35, baseOpacity: 0.0, activeOpacity: 0.28 };
                line.userData.baseOpacity = 0.0;
                line.userData.activeOpacity = 0.28;
                group.add(line);
            }
        });
        return group;
    }

}

// ---------------------------------------------------------------------
// LoungeArea
// ---------------------------------------------------------------------

// The rug sits under the coffee table and the sofa's front edge.
const LOUNGE_RUG_Z = 2.4;

class LoungeArea extends RoomSection {
    constructor(room) {
        super(room, 'LoungeArea');
    }

    build() {
        // The user's model, textured in Blender (sofa-v1.glb, about 4.2k triangles):
        // leather and pillows share one baked map, the neon strips are flat. Its
        // front is +Z; the group keeps the old convention of facing along -Z.
        // Without it the lounge has no sofa.
        const sofa = new THREE.Group();
        sofa.name = 'sofa';
        sofa.position.set(-3.28, -1.2, 1.2);
        // The seating side faces the coffee table, not the back wall.
        sofa.rotation.y = Math.PI + 0.06;
        const model = this.room.models.sofa;
        if (model) {
            model.traverse((child) => {
                if (!child.isMesh) return;
                const { name, map } = child.material;
                child.material.dispose();
                if (map) map.anisotropy = 4;
                if (name === 'sofa_led_cyan' || name === 'sofa_led_magenta') {
                    child.material = this.room.basic({ color: name === 'sofa_led_cyan' ? 'cyan' : 'magenta', side: THREE.DoubleSide });
                } else if (name === 'sofa_pillow') {
                    // Light and dark covers baked in grey; the theme tints them.
                    child.material = this.room.std({ map, color: 'textileAccent', roughness: 0.85, metalness: 0 });
                } else {
                    child.material = this.room.std({ map, roughness: 0.48, metalness: 0, emissive: 0xffffff, emissiveIntensity: 0.10, rimColor: 'cyan', rimIntensity: 0.12 });
                    child.material.emissiveMap = map;
                }
            });
            model.rotation.y = Math.PI;
            sofa.add(model);
        }
        this.group.add(sofa);

        // An opaque dark rug in the room's shared fabric, like the user's reference. The
        // translucent printed rug read as loose pink and blue patches on the floor.
        const rug = new THREE.Mesh(plane(2.75, 1.62), this.room.texturedMaterial('fabric', { color: 0x2b2b32, roughness: 0.96, metalness: 0 }));
        rug.name = 'lounge_rug';
        rug.rotation.x = -Math.PI / 2;
        rug.position.set(-3.48, -1.176, LOUNGE_RUG_Z);
        this.group.add(rug);

        // The user's model (table-v1.glb): frame with a baked map, a glass panel in the
        // top's opening over a lower shelf, and neon strips. Base at its origin.
        const table = new THREE.Group();
        table.name = 'coffee_table';
        table.position.set(-3.3, -1.2, 2.66); // clear of the sofa front (z ≈ 1.81–2.01)
        const tableModel = this.room.models.table;
        if (tableModel) {
            tableModel.traverse((child) => {
                if (!child.isMesh) return;
                const { name, map } = child.material;
                child.material.dispose();
                if (map) map.anisotropy = 4;
                if (name === 'table_glass') {
                    child.material = this.room.std({ color: 'glass', emissive: 'cyan', emissiveIntensity: 0.08, transparent: true, opacity: 0.34, roughness: 0.18, metalness: 0.1, side: THREE.DoubleSide });
                } else if (name === 'table_led_cyan' || name === 'table_led_magenta') {
                    child.material = this.room.basic({ color: name === 'table_led_cyan' ? 'cyan' : 'magenta', side: THREE.DoubleSide });
                } else {
                    child.material = this.room.std({ map, roughness: 0.35, metalness: 0, emissive: 'cyan', emissiveIntensity: 0.03 });
                    child.material.emissiveMap = map;
                }
            });
            table.add(tableModel);
        }
        // The user's stack of three books (books-stack-v1.glb, own 512 map), on the glass
        // (0.444 above the floor in table-v1.glb), spines to the room.
        const books = this.room.models.books;
        if (books) {
            books.traverse((child) => {
                if (!child.isMesh) return;
                const { map } = child.material;
                child.material.dispose();
                child.material = this.room.std({ map, roughness: 0.8, metalness: 0 });
            });
            books.name = 'coffee_table_books';
            books.position.set(-0.35, 0.444, 0);
            books.rotation.y = 0.2;
            table.add(books);
        }
        // The user's mug of pencils (mug-pencils-v1.glb, 11 cm), handle towards the room.
        const mug = this.room.mugModel();
        if (mug) {
            mug.name = 'coffee_table_mug';
            mug.position.set(0.42, 0.444, 0.04);
            mug.rotation.y = -0.6;
            table.add(mug);
        }
        this.group.add(table);

        // The floor lamp is the user's desk lamp grown into one: 3.6x taller (about 1.55 m),
        // 1.8x wider so its base stays clear of the wall LED, the sofa arm and the table.
        // The desk is built first, so the clone drops the desk light it would copy.
        const floorLamp = this.room.towerLampModel()?.lamp?.clone() || new THREE.Group();
        floorLamp.traverse((child) => { if (child.isLight) child.removeFromParent(); });
        floorLamp.name = 'lounge_floor_lamp';
        floorLamp.position.set(-4.6, -1.2, 2.3); // beside the sofa's left arm
        floorLamp.rotation.y = 2.2; // shade towards the sofa
        floorLamp.scale.set(1.8, 3.6, 1.8);
        const warm = new THREE.PointLight(ROOM_THEMES.cyber.amber, 1.15, 3.8);
        (floorLamp.getObjectByName('lamp_light') || floorLamp).add(warm);
        this.room.addLight(warm);
        this.room.loungeWarm = warm;
        this.group.add(floorLamp);

        const wallFrames = new THREE.Group();
        wallFrames.name = 'lounge_wall_frames';
        wallFrames.position.set(-5.025, 1.52, 1.35);
        wallFrames.rotation.y = Math.PI / 2;
        for (let i = 0; i < 3; i++) {
            const frame = new THREE.Mesh(plane(0.36, 0.5), this.room.basic({ color: i === 1 ? 'magenta' : 'cyan', transparent: true, opacity: 0.0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
            frame.position.set(-0.46 + i * 0.46, i === 1 ? 0.1 : 0, 0);
            wallFrames.add(frame);
            const border = new THREE.Mesh(box(0.39, 0.012, 0.012), this.room.basic({ color: 'amber', transparent: true, opacity: 0.0, depthWrite: false }));
            border.position.set(frame.position.x, frame.position.y + 0.27, 0.006);
            wallFrames.add(border);
        }
        wallFrames.visible = false;
        this.group.add(wallFrames);

        this.room.register('sofa', sofa);
        return this.finish();
    }
}

// ---------------------------------------------------------------------
// BedArea
// ---------------------------------------------------------------------

class BedArea extends RoomSection {
    constructor(room) {
        super(room, 'BedArea');
    }

    build() {
        // The user's model (bed-v1.glb, about 5.7k triangles): frame, mattress and pillows
        // share one baked map with the blanket, which is grey so the theme can tint it.
        // Headboard at -Z, base at its origin, centred on the footprint (3.74 long with
        // the drape, 2.29 wide); the mattress is 2.85 long so IRIS fits lying down.
        const bed = new THREE.Group();
        bed.name = 'bed';
        // Headboard towards the back like the old bed, drape clear of the right wall's LED.
        bed.position.set(3.7, -1.2, 1.97);
        const model = this.room.models.bed;
        if (model) {
            model.traverse((child) => {
                if (!child.isMesh) return;
                const { name, map } = child.material;
                child.material.dispose();
                if (map) map.anisotropy = 4;
                // The blanket's light pixel squares glow in the theme's magenta, the
                // rest stays dark; the frame gets the faint self-light of the sofa.
                child.material = name === 'bed_blanket'
                    ? this.room.std({ map, color: 'textileAccent', roughness: 0.9, metalness: 0, emissive: 'magenta', emissiveIntensity: 0.50, rimColor: 'magenta', rimIntensity: 0.08 })
                    : this.room.std({ map, roughness: 0.85, metalness: 0, emissive: 0xffffff, emissiveIntensity: 0.12, rimColor: 'magenta', rimIntensity: 0.10 });
                child.material.emissiveMap = map;
            });
            bed.add(model);
        }
        this.group.add(bed);

        // The bedside light stays where the user's new nightstand will go, left of the headboard.
        const light = new THREE.PointLight(ROOM_THEMES.cyber.amber, 0.85, 2.8);
        light.position.set(2.2, -0.45, 0.35);
        this.room.scene.add(light);
        this.room.addLight(light);
        this.room.bedLight = light;

        this.room.register('bed', bed);
        return this.finish();
    }
}

// ---------------------------------------------------------------------
// BookshelfArea
// ---------------------------------------------------------------------

class BookshelfArea extends RoomSection {
    constructor(room) {
        super(room, 'BookshelfArea');
    }

    build() {
        // The user's model, textured in Blender: one mesh and one baked map (about 3k
        // triangles), front on +Z, base at its origin. Without it the wall stays bare.
        const model = this.room.models.bookshelf;
        if (model) {
            const shelf = new THREE.Group();
            shelf.name = 'bookshelf';
            // On the floor, its back (0.19 behind the origin) just clear of the baseboard.
            shelf.position.set(-4.765, -1.2, -2.15);
            shelf.rotation.y = Math.PI / 2;
            model.traverse((child) => {
                if (!child.isMesh) return;
                const map = child.material.map;
                map.anisotropy = 4;
                child.material.dispose();
                // A little self-light that follows the texture keeps the lower rows
                // readable in the dark room, tinted by the theme's amber.
                child.material = this.room.std({ map, roughness: 0.62, metalness: 0, emissive: 'amber', emissiveIntensity: 0.12 });
                child.material.emissiveMap = map;
            });
            // An amber LED under each board of bookshelf-v1.glb, merged into one mesh.
            const led = new THREE.Mesh(
                mergeGeometries([1.014, 1.489, 1.988].map((y) => new THREE.BoxGeometry(0.9, 0.014, 0.014).translate(0, y, 0.15))),
                this.room.namedMaterial('warmAmberLightMaterial'),
            );
            led.name = 'bookshelf_led';
            shelf.add(model, led);
            // A second of the user's pencil mugs on the arch's board (y 2.04), off-centre, and
            // a copy of the wall shelf's vase in the middle of the arch.
            const mug = this.room.mugModel()?.clone();
            if (mug) {
                mug.name = 'bookshelf_mug';
                mug.position.set(-0.3, 2.04, -0.01);
                mug.rotation.y = -0.8;
                shelf.add(mug);
            }
            const vase = this.room.shelfModel()?.vase?.clone();
            if (vase) {
                vase.name = 'bookshelf_vase';
                vase.position.set(0.08, 2.04, -0.01);
                vase.rotation.y = 0.7;
                shelf.add(vase);
            }
            // The user's book (book-v1.glb, 15 x 21 cm), the one she takes and reads: upright in
            // the top row, its spine out of the line. Hinged, so it opens in her hands.
            const book = this.room.models.book;
            if (book) {
                book.name = 'bookshelf_book';
                book.traverse((child) => {
                    if (!child.isMesh) return;
                    const { map } = child.material;
                    child.material.dispose();
                    child.material = this.room.std({ map, roughness: 0.75, metalness: 0 });
                });
                book.scale.setScalar(1.4594); // the avatar's own scale: it fits her hand
                book.position.set(-0.12, 1.713, 0.18);
                book.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(
                    new THREE.Vector3(0, 0, -1), new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, -1, 0),
                ));
                shelf.add(book);
                this.room.register('bookshelf_book', book);
                // The same book for reading away from the shelf, which appears in her hands.
                const conjured = conjuredBook(book, (params) => this.room.std(params));
                this.add(conjured);
                this.room.register('conjured_book', conjured);
            }
            this.add(shelf);
            this.room.register('bookshelf', shelf);
        }

        const warm = new THREE.PointLight(ROOM_THEMES.cyber.amber, 0.56, 2.95);
        // Mid-height in front of the shelf, not at its top, and to one side: where she reaches
        // for a book a light this close would flare on her hand.
        warm.position.set(-4.1, 0.55, -1.72);
        this.room.scene.add(warm);
        this.room.addLight(warm);
        this.room.shelfLight = warm;

        return this.finish();
    }
}

// ---------------------------------------------------------------------
// NeonTrim
// ---------------------------------------------------------------------

class NeonTrim extends RoomSection {
    constructor(room) {
        super(room, 'NeonTrim');
    }

    build() {
        const cyan = this.room.std({ color: 'cyan', emissive: 'cyan', emissiveIntensity: 0.74, transparent: true, opacity: 0.58 });
        const magenta = this.room.std({ color: 'magenta', emissive: 'magenta', emissiveIntensity: 0.78, transparent: true, opacity: 0.58 });
        const amber = this.room.std({ color: 'amber', emissive: 'amber', emissiveIntensity: 0.64, transparent: true, opacity: 0.54 });
        const strips = [];

        const addStrip = (name, dims, pos, rot, mat) => {
            const s = this.mesh(box(dims[0], dims[1], dims[2]), mat, pos, rot);
            s.name = name;
            strips.push(s);
            return s;
        };

        addStrip('back_floor_neon', [8.9, 0.024, 0.024], [0, -1.04, -4.62], null, cyan);
        addStrip('left_floor_neon', [0.024, 0.024, 7.5], [-4.92, -1.04, -0.45], null, magenta);
        addStrip('right_floor_neon', [0.024, 0.024, 7.5], [4.92, -1.04, -0.45], null, cyan);
        addStrip('back_ceiling_neon', [7.7, 0.024, 0.024], [0, 2.86, -4.58], null, magenta);
        addStrip('left_ceiling_neon', [0.024, 0.024, 7.0], [-4.85, 2.86, -0.55], null, amber);
        addStrip('right_wall_neon', [0.024, 1.9, 0.024], [4.88, 1.15, -1.0], null, magenta);
        addStrip('left_wall_neon', [0.024, 1.65, 0.024], [-4.88, 1.05, 0.85], null, cyan);

        const columns = new THREE.Group();
        columns.name = 'light_columns';
        for (const [x, z, mat] of [[-4.88, -4.45, cyan], [4.88, -4.45, magenta], [-4.88, 3.1, amber], [4.88, 3.1, cyan]]) {
            const col = new THREE.Mesh(box(0.08, 2.6, 0.08), this.room.std({ color: 0x0b1018, roughness: 0.35, metalness: 0.55 }));
            col.position.set(x, 0.4, z);
            columns.add(col);
            const strip = new THREE.Mesh(box(0.018, 2.24, 0.018), mat.clone());
            strip.position.set(x, 0.45, z + 0.055);
            strip.userData.pulse = 0.55;
            columns.add(strip);
        }
        this.group.add(columns);

        this.room.register('neon_skirts', strips);
        this.room.register('light_columns', columns);
        return this.finish();
    }
}

// ---------------------------------------------------------------------
// CyberRoom
// ---------------------------------------------------------------------

class CyberRoom {
    constructor(room) {
        this.room = room;
    }

    build() {
        this._buildShell();
        this._buildSeamShading();
        new AvatarPlatform(this.room).build();
        new WindowCityBackdrop(this.room).build();
        new DeskArea(this.room).build();
        new LoungeArea(this.room).build();
        new BedArea(this.room).build();
        new BookshelfArea(this.room).build();
        new NeonTrim(this.room).build();
        this._buildHolograms();
        this._buildDecorations();
        this._buildClockAndShelf();
        this._buildLighting();
        this._buildHazeVolumes();
        this._buildDustParticles();
    }

    // Soft shading where the shell meets itself: along the floor and ceiling
    // and beside the back corner columns. Black alpha ramps a few millimetres
    // off each surface (dark edge on local +y, at the seam) keep the room from
    // reading as thin planes meeting on hard lines. The strips merge into one
    // mesh per strength, so they cost two draw calls.
    _buildSeamShading() {
        const seams = new THREE.Group();
        seams.name = 'seam_shading';
        const ramp = createEdgeShadowTexture();
        const floorShade = [], wallShade = [];
        const placement = new THREE.Object3D();
        const add = (width, height, strips, position, rotation) => {
            placement.position.set(...position);
            placement.rotation.set(...rotation);
            placement.updateMatrix();
            strips.push(plane(width, height).clone().applyMatrix4(placement.matrix));
        };
        const HALF_PI = Math.PI / 2;
        const sideLength = 8.45, sideZ = -0.25; // the side wall texture panels
        // Floor along the walls, just above the floor layers.
        add(10.0, 0.6, floorShade, [0, -1.183, -4.57], [-HALF_PI, 0, 0]);
        add(sideLength, 0.6, floorShade, [-4.72, -1.183, sideZ], [-HALF_PI, 0, HALF_PI]);
        add(sideLength, 0.6, floorShade, [4.72, -1.183, sideZ], [-HALF_PI, 0, -HALF_PI]);
        // Bottom and top band of each wall; the z-rotation puts the dark edge down.
        for (const [y, height, edge] of [[-0.95, 0.5, Math.PI], [2.85, 0.4, 0]]) {
            add(10.0, height, wallShade, [0, y, -4.866], [0, 0, edge]);
            add(sideLength, height, wallShade, [-5.02, y, sideZ], [0, HALF_PI, edge]);
            add(sideLength, height, wallShade, [5.02, y, sideZ], [0, -HALF_PI, edge]);
        }
        // Ceiling along the walls.
        add(10.0, 0.45, wallShade, [0, 3.044, -4.645], [HALF_PI, 0, Math.PI]);
        add(sideLength, 0.45, wallShade, [-4.795, 3.044, sideZ], [HALF_PI, 0, HALF_PI]);
        add(sideLength, 0.45, wallShade, [4.795, 3.044, sideZ], [HALF_PI, 0, -HALF_PI]);
        // Beside each back corner column (x ±4.86..5.04, z -4.69..-4.35): on the
        // back wall and on the side wall.
        for (const side of [-1, 1]) {
            add(4.25, 0.45, wallShade, [side * 4.635, 0.925, -4.866], [0, 0, -side * HALF_PI]);
            add(4.25, 0.45, wallShade, [side * 5.02, 0.925, -4.125], [0, -side * HALF_PI, side * HALF_PI]);
        }
        for (const [strips, opacity] of [[floorShade, 0.6], [wallShade, 0.45]]) {
            const material = this.room.basic({ color: 0x000000, map: ramp, transparent: true, opacity, depthWrite: false });
            seams.add(new THREE.Mesh(mergeGeometries(strips), material));
            strips.forEach(strip => strip.dispose());
        }
        this.room.group.add(seams);
        this.room.register('seam_shading', seams);
    }

    _buildShell() {
        const floorMat = this.room.namedMaterial('floorReflectiveMaterial');
        const wallMat = this.room.std({ color: 'wall', emissive: 'cyan', emissiveIntensity: 0.018, roughness: 0.86, metalness: 0.08 });
        const panelMat = this.room.texturedMaterial('wallPanel', { color: 'wallPanel', emissive: 'magenta', emissiveIntensity: 0.032, roughness: 0.66, metalness: 0.16 });
        const ceilingMat = this.room.std({ color: 'ceiling', emissive: 'cyan', emissiveIntensity: 0.012, roughness: 0.8, metalness: 0.14 });

        const floor = new THREE.Mesh(plane(10.4, 8.8), floorMat);
        floor.name = 'floor';
        floor.rotation.x = -Math.PI / 2;
        floor.position.set(0, -1.2, -0.25);
        floor.receiveShadow = false;
        this.room.group.add(floor);
        this.room.register('floor', floor);

        // Real floor texture pass: this sits above the metal base and below
        // all glow/rug layers, so the generated panel artwork stays visible.
        const floorPhotoMat = this.room.basic({
            color: 0xffffff,
            transparent: true,
            opacity: 0.92,
            side: THREE.DoubleSide,
            depthWrite: true,
        });
        floorPhotoMat.name = 'floorGeneratedTextureMaterial';
        this.room._loadMaterialTextureAsset(floorPhotoMat, ROOM_ASSETS.cyberFloorPanels, {
            repeat: [1, 1],
            anisotropy: 8,
            textureType: 'floorSurface',
        });
        const floorPhoto = new THREE.Mesh(plane(10.34, 8.74), floorPhotoMat);
        floorPhoto.name = 'floor_generated_texture_surface';
        floorPhoto.rotation.x = -Math.PI / 2;
        floorPhoto.position.set(0, -1.197, -0.25);
        floorPhoto.renderOrder = -5;
        floorPhoto.receiveShadow = false;
        this.room.group.add(floorPhoto);
        this.room.register('floor_texture_surface', floorPhoto);

        // Circular rug image pass: this is the visible stage surface.
        // It lies on the floor, level with the avatar's soles, above the
        // lowered platform geometry and the floor texture layers.
        const centralRugMat = this.room.basic({
            color: 0xffffff,
            transparent: true,
            opacity: 0.82,
            side: THREE.DoubleSide,
            depthWrite: false,
        });
        centralRugMat.name = 'circularGeneratedRugMaterial';
        this.room._loadMaterialTextureAsset(centralRugMat, ROOM_ASSETS.cyberCircularRug, {
            repeat: [1, 1],
            anisotropy: 8,
            textureType: 'circularRugSurface',
            muteOuterMagentaRing: true,
        });
        const centralRug = new THREE.Mesh(
            circle(2.28, 144),
            centralRugMat,
        );
        centralRug.name = 'center_circular_rug_texture';
        centralRug.rotation.x = -Math.PI / 2;
        centralRug.position.set(0, -1.006 - STAGE_FLOOR_DROP, 0);
        centralRug.renderOrder = 12;
        this.room.group.add(centralRug);
        this.room.register('center_circular_rug', centralRug);

        const floorWallLed = new THREE.Group();
        floorWallLed.name = 'floor_wall_led_cove';
        const ledCyan = this.room.basic({ color: 'cyan', transparent: true, opacity: 0.22, blending: THREE.AdditiveBlending, depthWrite: false });
        const ledMagenta = this.room.basic({ color: 'magenta', transparent: true, opacity: 0.2, blending: THREE.AdditiveBlending, depthWrite: false });
        for (const [x, z, w, d, mat] of [
            [0, -4.59, 8.95, 0.018, ledCyan],
            [-4.86, -0.38, 0.018, 7.55, ledMagenta],
            [4.86, -0.38, 0.018, 7.55, ledCyan],
        ]) {
            const strip = new THREE.Mesh(box(w, 0.012, d), mat);
            strip.position.set(x, -1.075, z);
            strip.userData.pulse = 0.18;
            floorWallLed.add(strip);
        }
        const coveGlowMat = this.room.basic({ color: 'cyan', transparent: true, opacity: 0.032, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide });
        for (const [x, z, w, d, rot] of [
            [0, -4.52, 8.6, 0.28, 0],
            [-4.75, -0.32, 7.25, 0.24, Math.PI / 2],
            [4.75, -0.32, 7.25, 0.24, Math.PI / 2],
        ]) {
            const glow = new THREE.Mesh(plane(w, d), coveGlowMat.clone());
            glow.position.set(x, -1.145, z);
            glow.rotation.x = -Math.PI / 2;
            glow.rotation.z = rot;
            floorWallLed.add(glow);
        }
        this.room.group.add(floorWallLed);
        this.room.register('floor_wall_led_cove', floorWallLed);

        const floorPanels = new THREE.Group();
        floorPanels.name = 'premium_floor_panels';
        const panelA = this.room.std({ color: 0x0a1422, emissive: 'cyan', emissiveIntensity: 0.01, transparent: true, opacity: 0.018, roughness: 0.18, metalness: 0.74 });
        const panelB = this.room.std({ color: 0x0d1020, emissive: 'magenta', emissiveIntensity: 0.008, transparent: true, opacity: 0.014, roughness: 0.2, metalness: 0.7 });
        for (const [x, z, w, d, mat] of [
            [-2.7, -2.65, 2.8, 2.45, panelA],
            [2.6, -2.5, 2.6, 2.15, panelB],
            [-2.85, 1.45, 2.55, 2.45, panelB],
            [2.55, 1.45, 2.9, 2.5, panelA],
            [0, -0.15, 2.1, 2.1, panelA],
        ]) {
            const slab = new THREE.Mesh(box(w, 0.006, d), mat);
            slab.position.set(x, -1.193, z);
            floorPanels.add(slab);
        }
        const reflectionCyan = new THREE.Mesh(plane(3.4, 0.75), this.room.basic({ color: 'cyan', transparent: true, opacity: 0.025, blending: THREE.AdditiveBlending, depthWrite: false }));
        reflectionCyan.rotation.x = -Math.PI / 2;
        reflectionCyan.rotation.z = 0.18;
        reflectionCyan.position.set(-1.4, -1.186, -1.45);
        floorPanels.add(reflectionCyan);
        this.room.group.add(floorPanels);

        const contactShadows = new THREE.Group();
        contactShadows.name = 'fake_contact_shadows';
        const shadowMat = this.room.basic({ color: 0x000000, map: createContactShadowTexture(), transparent: true, opacity: 0.3, depthWrite: false, side: THREE.DoubleSide });
        for (const [x, z, w, d, opacity] of [
            [0, 0, 4.15, 3.4, 0.075],
            [2.8, -2.66, 3.0, 1.45, 0.30],
            [-3.65, 1.15, 2.85, 1.20, 0.32],
            [3.65, 1.78, 1.48, 2.45, 0.34],
            [-4.58, -2.0, 1.0, 2.3, 0.22],
        ]) {
            const mat = shadowMat.clone();
            mat.opacity = opacity;
            const s = new THREE.Mesh(plane(w, d), mat);
            s.rotation.x = -Math.PI / 2;
            s.position.set(x, -1.178, z);
            contactShadows.add(s);
        }
        this.room.group.add(contactShadows);

        const back = new THREE.Mesh(box(10.4, 4.4, 0.16), wallMat);
        back.name = 'back_wall';
        back.position.set(0, 0.95, -4.98);
        const left = new THREE.Mesh(box(0.16, 4.4, 8.8), wallMat);
        left.name = 'left_wall';
        left.position.set(-5.12, 0.95, -0.25);
        const right = new THREE.Mesh(box(0.16, 4.4, 8.8), wallMat);
        right.name = 'right_wall';
        right.position.set(5.12, 0.95, -0.25);
        const ceiling = new THREE.Mesh(plane(10.4, 8.8), ceilingMat);
        ceiling.name = 'ceiling';
        ceiling.rotation.x = Math.PI / 2;
        ceiling.position.set(0, 3.05, -0.25);

        this.room.group.add(back, left, right, ceiling);
        this.room.register('walls', [back, left, right]);
        this.room.register('ceiling', ceiling);

        const wallTexturePanels = new THREE.Group();
        wallTexturePanels.name = 'premium_wall_complete_texture_panels';
        const makeWallPanelMaterial = ({ flipX = false } = {}) => {
            const mat = this.room.basic({
                color: 0xffffff,
                transparent: false,
                opacity: 1,
                side: THREE.DoubleSide,
                depthWrite: true,
            });
            this.room._loadMaterialTextureAsset(mat, ROOM_ASSETS.cyberWallComplete, {
                repeat: [flipX ? -1 : 1, 1],
                offset: [flipX ? 1 : 0, 0],
                anisotropy: 8,
                textureType: 'wallCompleteSurface',
            });
            return mat;
        };
        const addWallPanel = (name, width, height, position, rotation, materialOpts = {}) => {
            const panel = new THREE.Mesh(plane(width, height), makeWallPanelMaterial(materialOpts));
            panel.name = name;
            panel.position.set(position[0], position[1], position[2]);
            panel.rotation.set(rotation[0], rotation[1], rotation[2]);
            panel.renderOrder = -4;
            wallTexturePanels.add(panel);
            return panel;
        };

        // Full-wall bitmap pass: one composed image per wall keeps the room
        // cohesive while the 3D neon trims stay available as separate accents.
        addWallPanel('back_wall_complete_surface', 10.05, 3.72, [0, 0.95, -4.872], [0, 0, 0]);
        addWallPanel('left_wall_complete_surface', 8.45, 3.72, [-5.026, 0.95, -0.25], [0, Math.PI / 2, 0]);
        addWallPanel('right_wall_complete_surface', 8.45, 3.72, [5.026, 0.95, -0.25], [0, -Math.PI / 2, 0], { flipX: true });
        this.room.group.add(wallTexturePanels);
        this.room.register('wall_texture_panels', wallTexturePanels);

        const ceilingPanels = new THREE.Group();
        ceilingPanels.name = 'ceiling_panels';
        const ceilingPanelMat = this.room.std({ color: 0x0c1420, emissive: 'cyan', emissiveIntensity: 0.014, transparent: true, opacity: 0.5, roughness: 0.62, metalness: 0.18 });
        for (const [x, z, w, d] of [
            [-3.1, -2.75, 1.8, 1.0],
            [0, -2.75, 2.1, 1.0],
            [3.1, -2.75, 1.8, 1.0],
            [-2.5, 0.45, 2.0, 1.1],
            [2.55, 0.45, 2.0, 1.1],
            [0, 2.35, 2.3, 0.8],
        ]) {
            const p = new THREE.Mesh(box(w, 0.012, d), ceilingPanelMat);
            p.position.set(x, 3.035, z);
            ceilingPanels.add(p);
        }
        this.room.group.add(ceilingPanels);

        const ceilingRibs = new THREE.Group();
        ceilingRibs.name = 'ceiling_architecture_ribs';
        const ribMat = this.room.std({ color: 0x050911, emissive: 'cyan', emissiveIntensity: 0.018, transparent: true, opacity: 0.58, roughness: 0.44, metalness: 0.68 });
        for (const z of [-4.06, -2.84, -1.62, -0.4, 0.82, 2.04, 3.26]) {
            const rib = new THREE.Mesh(box(9.55, 0.032, 0.052), ribMat);
            rib.position.set(0, 2.965, z);
            ceilingRibs.add(rib);
        }
        for (const x of [-4.36, -2.18, 2.18, 4.36]) {
            const rail = new THREE.Mesh(box(0.042, 0.032, 7.35), ribMat);
            rail.position.set(x, 2.975, -0.28);
            ceilingRibs.add(rail);
        }
        this.room.group.add(ceilingRibs);

        const depthColumns = new THREE.Group();
        depthColumns.name = 'room_depth_architecture_columns';
        const columnMat = this.room.std({ color: 0x07101a, emissive: 'cyan', emissiveIntensity: 0.018, roughness: 0.5, metalness: 0.58 });
        const darkInsetMat = this.room.std({ color: 0x04070d, emissive: 'magenta', emissiveIntensity: 0.012, roughness: 0.76, metalness: 0.16 });
        for (const [x, z, sx, sz] of [
            [-4.95, -4.52, 0.18, 0.34],
            [4.95, -4.52, 0.18, 0.34],
            [-4.95, 3.52, 0.18, 0.34],
            [4.95, 3.52, 0.18, 0.34],
        ]) {
            const column = new THREE.Group();
            column.name = 'room_corner_depth_column';
            column.position.set(x, 0.86, z);
            column.add(new THREE.Mesh(box(sx, 3.42, sz), columnMat));
            const groove = new THREE.Mesh(box(0.024, 2.72, 0.022), this.room.namedMaterial('cyanEmissiveMaterial'));
            groove.position.set(x < 0 ? 0.095 : -0.095, 0.0, z < 0 ? 0.18 : -0.18);
            groove.userData.pulse = 0.32;
            column.add(groove);
            depthColumns.add(column);
        }
        for (const [x, z, w, d, rotY] of [
            [-5.035, -1.85, 0.018, 1.54, Math.PI / 2],
            [-5.035, 1.74, 0.018, 1.42, Math.PI / 2],
            [5.035, -1.82, 0.018, 1.54, -Math.PI / 2],
            [5.035, 1.74, 0.018, 1.42, -Math.PI / 2],
        ]) {
            const recess = new THREE.Mesh(plane(w, d), darkInsetMat);
            recess.name = 'side_wall_recess_shadow';
            recess.position.set(x, 0.58, z);
            recess.rotation.y = rotY;
            recess.visible = false;
            depthColumns.add(recess);
        }
        this.room.group.add(depthColumns);

        const insetWallBands = new THREE.Group();
        insetWallBands.name = 'premium_wall_inset_bands';
        const darkBand = this.room.std({ color: 0x070d16, emissive: 'cyan', emissiveIntensity: 0.018, roughness: 0.7, metalness: 0.2 });
        for (const [x, y, z, w, h, rotY] of [
            [-5.026, 0.0, -2.95, 0.018, 1.42, Math.PI / 2],
            [-5.026, 0.0, 2.55, 0.018, 1.25, Math.PI / 2],
            [5.026, 0.02, -2.95, 0.018, 1.42, -Math.PI / 2],
            [5.026, 0.02, 2.55, 0.018, 1.25, -Math.PI / 2],
            [-3.1, 0.05, -4.872, 1.35, 1.32, 0],
            [3.12, 0.05, -4.872, 1.35, 1.32, 0],
        ]) {
            const band = new THREE.Mesh(plane(w, h), darkBand);
            band.position.set(x, y, z);
            band.rotation.y = rotY;
            insetWallBands.add(band);
        }
        this.room.group.add(insetWallBands);

        const baseboards = new THREE.Group();
        baseboards.name = 'wall_baseboard_trims';
        const baseMat = this.room.std({ color: 0x05080f, emissive: 'cyan', emissiveIntensity: 0.026, roughness: 0.42, metalness: 0.64 });
        for (const [x, y, z, w, h, d] of [
            [0, -1.0, -4.82, 9.75, 0.12, 0.08],
            [-5.0, -1.0, -0.25, 0.08, 0.12, 8.25],
            [5.0, -1.0, -0.25, 0.08, 0.12, 8.25],
            [0, 2.74, -4.82, 9.75, 0.08, 0.08],
            [-5.0, 2.74, -0.25, 0.08, 0.08, 8.25],
            [5.0, 2.74, -0.25, 0.08, 0.08, 8.25],
        ]) {
            const trim = new THREE.Mesh(box(w, h, d), baseMat);
            trim.position.set(x, y, z);
            baseboards.add(trim);
        }
        this.room.group.add(baseboards);

        const floorEdgeThickness = new THREE.Group();
        floorEdgeThickness.name = 'floor_visible_edge_depth';
        const edgeMat = this.room.std({ color: 0x03060c, emissive: 'cyan', emissiveIntensity: 0.012, roughness: 0.58, metalness: 0.35 });
        for (const [x, z, w, d] of [
            [0, 3.98, 10.2, 0.18],
            [-5.12, -0.25, 0.18, 8.62],
            [5.12, -0.25, 0.18, 8.62],
        ]) {
            const edge = new THREE.Mesh(box(w, 0.16, d), edgeMat);
            edge.position.set(x, -1.285, z);
            floorEdgeThickness.add(edge);
        }
        this.room.group.add(floorEdgeThickness);

        for (const [x, y, z, w, h] of [
            [-2.8, 1.2, -4.885, 1.0, 1.85],
            [2.95, 1.15, -4.885, 1.1, 1.65],
            [-5.025, 1.1, -1.0, 0.9, 1.75],
            [5.025, 1.28, 0.35, 0.82, 1.65],
            [-5.025, 2.08, 1.98, 0.72, 0.72],
            [5.025, 2.08, -2.18, 0.72, 0.72],
            [-3.75, 2.35, -4.875, 0.9, 0.32],
            [3.78, 2.35, -4.875, 0.9, 0.32],
        ]) {
            const p = new THREE.Mesh(plane(w, h), panelMat);
            p.position.set(x, y, z);
            if (Math.abs(x) > 4.9) p.rotation.y = x < 0 ? Math.PI / 2 : -Math.PI / 2;
            p.visible = false;
            this.room.group.add(p);
        }

        const seamMat = this.room.basic({ color: 'cyan', transparent: true, opacity: 0.026, depthWrite: false });
        const amberSeamMat = this.room.basic({ color: 'amber', transparent: true, opacity: 0.022, depthWrite: false });
        for (const [x, y, z, w, h, rotY, mat] of [
            [-5.018, 1.08, -2.7, 0.018, 2.8, Math.PI / 2, seamMat],
            [5.018, 1.08, 1.9, 0.018, 2.8, -Math.PI / 2, seamMat],
            [-1.8, 1.08, -4.872, 0.018, 2.8, 0, seamMat],
            [1.8, 1.08, -4.872, 0.018, 2.8, 0, seamMat],
            [-5.017, 2.34, 0.32, 0.012, 1.6, Math.PI / 2, amberSeamMat],
            [5.017, 2.18, -1.52, 0.012, 1.45, -Math.PI / 2, amberSeamMat],
            [-3.45, 2.72, -4.87, 0.012, 1.4, Math.PI / 2, seamMat],
            [3.42, 2.72, -4.87, 0.012, 1.4, Math.PI / 2, seamMat],
        ]) {
            const seam = new THREE.Mesh(plane(w, h), mat);
            seam.position.set(x, y, z);
            seam.rotation.y = rotY;
            this.room.group.add(seam);
        }

        const microPanels = new THREE.Group();
        microPanels.name = 'wall_micro_panels';
        const panelLineMat = this.room.basic({ color: 'cyan', transparent: true, opacity: 0.018, depthWrite: false });
        const panelWarmMat = this.room.basic({ color: 'amber', transparent: true, opacity: 0.016, depthWrite: false });
        for (let i = 0; i < 24; i++) {
            const leftSide = i < 12;
            const x = leftSide ? -5.016 : 5.016;
            const z = -3.55 + (i % 12) * 0.62;
            const y = 0.24 + ((i * 7) % 9) * 0.24;
            const line = new THREE.Mesh(plane(0.012, 0.32 + (i % 3) * 0.12), i % 5 === 0 ? panelWarmMat : panelLineMat);
            line.position.set(x, y, z);
            line.rotation.y = leftSide ? Math.PI / 2 : -Math.PI / 2;
            microPanels.add(line);
        }
        microPanels.visible = false;
        this.room.group.add(microPanels);

        const rearAlcoveGlow = new THREE.Group();
        rearAlcoveGlow.name = 'rear_wall_depth_alcove_glow';
        const alcoveMat = this.room.basic({ color: 'magenta', transparent: true, opacity: 0.0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide });
        for (const [x, w] of [[-3.22, 1.16], [3.22, 1.16]]) {
            const glow = new THREE.Mesh(plane(w, 2.42), alcoveMat.clone());
            glow.position.set(x, 0.88, -4.805);
            glow.userData.windowCinematic = true;
            rearAlcoveGlow.add(glow);
        }
        rearAlcoveGlow.visible = false;
        this.room.group.add(rearAlcoveGlow);
    }

    _buildHolograms() {
        const holo = new THREE.Group();
        holo.name = 'holo_consoles';
        const panels = [];
        panels.forEach((p) => holo.add(p));
        holo.visible = false;
        this.room.group.add(holo);
        this.room.register('holo_consoles', holo);
    }

    _buildDecorations() {
        // The user's floor plant (plant-v1.glb, 1.35 m, LED pot), in front of the window's
        // left mullion. Its origin is the centre of the pot's base; only the leaves sway, from
        // where the stems leave the pot.
        const plant = this.room.models.plant;
        if (plant) {
            plant.traverse((child) => {
                if (!child.isMesh) return;
                for (const key of ['map', 'emissiveMap', 'roughnessMap', 'metalnessMap']) {
                    if (child.material[key]) child.material[key].anisotropy = 4;
                }
            });
            plant.name = 'plant';
            plant.position.set(-1.55, -1.19, -3.72);
            plant.rotation.y = 0.2;
            this.room.group.add(plant);
            this.room.register('plant', plant);
            this.room.plantLeaves = plant.getObjectByName('plant_leaves');
        }

        const floorCables = new THREE.Group();
        floorCables.name = 'subtle_floor_cable_paths';
        const cableMat = this.room.basic({ color: 'cyan', transparent: true, opacity: 0.11, depthWrite: false });
        const warmCableMat = this.room.basic({ color: 'amber', transparent: true, opacity: 0.095, depthWrite: false });
        for (const [x, z, w, rot, mat] of [
            [2.3, -1.82, 1.0, 0.18, cableMat],
            [3.18, -1.42, 0.72, -0.32, warmCableMat],
        ]) {
            const cable = new THREE.Mesh(box(w, 0.006, 0.012), mat);
            cable.position.set(x, -1.164, z);
            cable.rotation.y = rot;
            floorCables.add(cable);
        }
        this.room.group.add(floorCables);

        // The user's flush ceiling lamp (ceiling-lamp-v1.glb, 62 cm, warm diffuser and RGB
        // rings), above the stage. Its origin is the top centre, against the ceiling. It
        // glows from its own emission map; it adds no light.
        const ceilingLamp = this.room.models.ceilingLamp;
        if (ceilingLamp) {
            ceilingLamp.traverse((child) => {
                if (!child.isMesh) return;
                for (const key of ['map', 'emissiveMap', 'roughnessMap', 'metalnessMap']) {
                    if (child.material[key]) child.material[key].anisotropy = 4;
                }
            });
            ceilingLamp.name = 'center_ceiling_lamp';
            ceilingLamp.position.set(0, 3.05, 0.28);
            this.room.group.add(ceilingLamp);
        }
    }

    _buildClockAndShelf() {
        // The user's wall shelf (76 × 20 cm, LED edge, vase and cube) on the right wall beside
        // the desk, its top 1.4 m above the floor and towards the corner, so the default view
        // shows all of it at 16:9.
        const shelf = this.room.shelfModel()?.shelf;
        if (shelf) {
            shelf.name = 'floating_shelf';
            shelf.position.set(5.022, 0.994, -3.3);
            shelf.rotation.y = -Math.PI / 2;
            this.room.group.add(shelf);
            this.room.register('floating_shelf', shelf);
        }

        const clock = new THREE.Group();
        clock.name = 'wall_clock';
        clock.position.set(-5.02, 1.92, 1.92);
        clock.rotation.y = Math.PI / 2;
        clock.add(new THREE.Mesh(torus(0.28, 0.012, 8, 48), this.room.std({ color: 'amber', emissive: 'amber', emissiveIntensity: 0.55 })));
        const hourPivot = new THREE.Group();
        const minPivot = new THREE.Group();
        const hourHand = new THREE.Mesh(box(0.012, 0.15, 0.012), this.room.std({ color: 'amber', emissive: 'amber', emissiveIntensity: 0.7 }));
        hourHand.position.y = 0.075;
        hourPivot.add(hourHand);
        const minHand = new THREE.Mesh(box(0.01, 0.22, 0.012), this.room.std({ color: 'cyan', emissive: 'cyan', emissiveIntensity: 0.7 }));
        minHand.position.y = 0.11;
        minPivot.add(minHand);
        clock.add(hourPivot, minPivot);
        this.room.group.add(clock);
        this.room.register('wall_clock', clock);
        this.room.register('clock_hour', hourPivot);
        this.room.register('clock_min', minPivot);
    }

    _buildLighting() {
        const hemi = new THREE.HemisphereLight(0xa6eaff, 0x241721, 0.44);
        this.room.scene.add(hemi);
        this.room.addLight(hemi);

        const avatarFill = new THREE.PointLight(ROOM_THEMES.cyber.cyan, 1.12, 5.6);
        avatarFill.position.set(-0.35, -0.22, 1.82);
        this.room.scene.add(avatarFill);
        this.room.addLight(avatarFill);
        this.room.avatarFill = avatarFill;

        const rim = new THREE.PointLight(ROOM_THEMES.cyber.magenta, 2.08, 6.3);
        rim.position.set(-0.28, 1.55, -2.35);
        this.room.scene.add(rim);
        this.room.addLight(rim);
        this.room.rimLight = rim;

        const silhouetteWash = new THREE.PointLight(ROOM_THEMES.cyber.cyan, 0.46, 3.8);
        silhouetteWash.position.set(0.72, 1.02, -1.86);
        this.room.scene.add(silhouetteWash);
        this.room.addLight(silhouetteWash);
        this.room.silhouetteWash = silhouetteWash;

        const windowWash = new THREE.PointLight(ROOM_THEMES.cyber.cyan, 0.42, 6.4);
        windowWash.position.set(0, 1.38, -4.0);
        this.room.scene.add(windowWash);
        this.room.addLight(windowWash);
        this.room.cityCyanWash = windowWash;

        const backWallUp = new THREE.PointLight(ROOM_THEMES.cyber.magenta, 0.5, 5.7);
        backWallUp.position.set(0, 2.45, -3.8);
        this.room.scene.add(backWallUp);
        this.room.addLight(backWallUp);
        this.room.backWallUp = backWallUp;

        const sideCyan = new THREE.PointLight(0xffeee2, 2.4, 4.0);
        sideCyan.position.set(-3.6, 1.15, 1.55);
        sideCyan.name = 'lounge_soft_fill';
        this.room.scene.add(sideCyan);
        this.room.addLight(sideCyan);
        this.room.sideCyan = sideCyan;

        const sideMagenta = new THREE.PointLight(0xffeee2, 2.0, 3.8);
        sideMagenta.position.set(3.75, 1.15, 1.45);
        sideMagenta.name = 'bed_soft_fill';
        this.room.scene.add(sideMagenta);
        this.room.addLight(sideMagenta);
        this.room.sideMagenta = sideMagenta;

        const bounce = new THREE.PointLight(ROOM_THEMES.cyber.amber, 0.3, 3.25);
        bounce.position.set(0.35, -0.82, 0.72);
        this.room.scene.add(bounce);
        this.room.addLight(bounce);
        this.room.bounceLight = bounce;

        const rimPanel = new THREE.Group();
        rimPanel.name = 'avatar_soft_rim_panel';
        rimPanel.position.set(0, 0.56, -1.94);
        const centerGlow = new THREE.Mesh(
            plane(0.56, 1.72),
            this.room.basic({ color: 'magenta', transparent: true, opacity: 0.012, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }),
        );
        centerGlow.name = 'avatar_center_rim_glow';
        centerGlow.userData.windowCinematic = true;
        centerGlow.userData.cinematicAmplitude = 0.006;
        rimPanel.add(centerGlow);
        for (const x of [-0.46, 0.46]) {
            const edgeGlow = new THREE.Mesh(
                plane(0.14, 1.88),
                this.room.basic({ color: x < 0 ? 'cyan' : 'magenta', transparent: true, opacity: 0.026, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }),
            );
            edgeGlow.name = 'avatar_edge_rim_glow';
            edgeGlow.position.x = x;
            edgeGlow.userData.windowCinematic = true;
            edgeGlow.userData.cinematicAmplitude = 0.008;
            rimPanel.add(edgeGlow);
        }
        this.room.group.add(rimPanel);
        this.room.register('avatar_soft_rim_panel', rimPanel);
    }

    _buildHazeVolumes() {
        const haze = new THREE.Group();
        haze.name = 'soft_room_haze';
        const cyanHaze = this.room.basic({
            color: 'cyan',
            transparent: true,
            opacity: 0.018,
            blending: THREE.AdditiveBlending,
            depthWrite: false,
            side: THREE.DoubleSide,
        });
        const magentaHaze = this.room.basic({
            color: 'magenta',
            transparent: true,
            opacity: 0.026,
            blending: THREE.AdditiveBlending,
            depthWrite: false,
            side: THREE.DoubleSide,
        });
        const windowMist = new THREE.Mesh(plane(7.18, 2.74), cyanHaze);
        windowMist.name = 'window_full_glass_sheen';
        windowMist.position.set(0, 1.0, -4.50);
        windowMist.userData.windowCinematic = true;
        windowMist.userData.cinematicAmplitude = 0.004;
        windowMist.visible = false;
        haze.add(windowMist);

        const sheenLineMat = this.room.basic({
            color: 'cyan',
            transparent: true,
            opacity: 0.024,
            blending: THREE.AdditiveBlending,
            depthWrite: false,
            side: THREE.DoubleSide,
        });
        for (const [x, y, w, rot] of [
            [-2.35, 2.08, 1.12, -0.07],
            [1.35, 0.16, 0.82, -0.07],
            [2.82, 1.82, 0.68, -0.07],
        ]) {
            const sheen = new THREE.Mesh(plane(w, 0.018), sheenLineMat.clone());
            sheen.name = 'window_subtle_glass_reflection';
            sheen.position.set(x, y, -4.49);
            sheen.rotation.z = rot;
            sheen.userData.holoLine = true;
            sheen.visible = false;
            haze.add(sheen);
        }

        const platformMist = new THREE.Mesh(circle(2.85, 96), magentaHaze.clone());
        platformMist.material.opacity = 0.0;
        platformMist.rotation.x = -Math.PI / 2;
        platformMist.position.set(0, -0.965, -0.15);
        platformMist.visible = false;
        haze.add(platformMist);

        this.room.group.add(haze);
        this.room.register('soft_room_haze', haze);
    }

    _buildDustParticles() {
        const count = 120;
        const geo = new THREE.BufferGeometry();
        const positions = new Float32Array(count * 3);
        for (let i = 0; i < count; i++) {
            positions[i * 3] = (Math.random() - 0.5) * 9.2;
            positions[i * 3 + 1] = -0.75 + Math.random() * 3.3;
            positions[i * 3 + 2] = -4.2 + Math.random() * 7.8;
        }
        geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        const mat = new THREE.PointsMaterial({
            color: ROOM_THEMES.cyber.cyan,
            size: 0.016,
            transparent: true,
            opacity: 0.16,
            blending: THREE.AdditiveBlending,
            depthWrite: false,
        });
        const dust = new THREE.Points(geo, mat);
        dust.name = 'dust_particles';
        this.room.group.add(dust);
        this.room.dustParticles = dust;
    }
}

// Areas of the room for the light atmosphere (room_atmosphere.js), by the builder's groups.
const ATMOSPHERE_AREAS = Object.freeze({
    DeskArea: 'desk', LoungeArea: 'lounge', BookshelfArea: 'lounge', floating_shelf: 'lounge', plant: 'lounge',
    BedArea: 'bed', WindowCityBackdrop: 'window', NeonTrim: 'accent', floor_wall_led_cove: 'accent',
    rear_wall_depth_alcove_glow: 'accent', AvatarPlatform: 'avatar', avatar_soft_rim_panel: 'avatar',
});

function atmosphereRole(object, root) {
    for (let node = object; node && node !== root; node = node.parent) {
        if (Object.hasOwn(ATMOSPHERE_AREAS, node.name)) return ATMOSPHERE_AREAS[node.name];
    }
    return 'ambient';
}

// ---------------------------------------------------------------------
// RoomEnvironment public API
// ---------------------------------------------------------------------

export class RoomEnvironment {
    constructor(scene) {
        this.scene = scene;
        this.group = new THREE.Group();
        this.group.name = 'room';
        this.objects = {};
        this.quality = 'high';
        this.roomLights = [];
        this.atmosphere = new RoomAtmosphere();
        this.themeListeners = new Set(); // e.g. the ambient sound follows the room
        this.themeBindings = [];
        this.currentTheme = 'cyber';
        try {
            const savedTheme = localStorage.getItem('IRIS.roomTheme');
            if (savedTheme && ROOM_THEMES[savedTheme]) this.currentTheme = savedTheme;
        } catch {}
        this.cyberRoom = null;
        this.animated = this._createAnimationBuckets();
        this.namedMaterials = {};
        this.models = {};
    }

    async init() {
        this.models = await this._loadModels();
        this.cyberRoom = new CyberRoom(this);
        this.cyberRoom.build();
        const lightRoles = new Map([
            [this.lampLight, 'desk'], [this.objects.monitor_glow, 'desk'],
            [this.loungeWarm, 'lounge'], [this.shelfLight, 'lounge'], [this.sideCyan, 'lounge'],
            [this.bedLight, 'bed'], [this.sideMagenta, 'bed'],
            [this.cityCyanWash, 'accent'], [this.backWallUp, 'accent'],
            [this.avatarFill, 'avatar'], [this.rimLight, 'avatar'], [this.silhouetteWash, 'avatar'],
        ]);
        for (const light of this.roomLights) this.atmosphere.register(light, lightRoles.get(light) || 'ambient');
        this.setTheme(this.currentTheme);
        this._optimizeStaticRoom();
        this._registerAtmosphereSurfaces();
        this._indexAnimatedObjects();
        this.scene.add(this.group);
        console.log('Cyberpunk room environment loaded');
    }

    /** GLB furniture loads before the build, so the passes after it (theme, atmosphere,
     * culling) treat it like the built-in pieces. A model that fails leaves its spot empty. */
    async _loadModels() {
        const loader = useKTX2(new GLTFLoader(), (window.iris || window.IRIS)?.renderer);
        const load = (url) => loader.loadAsync(url).then((gltf) => gltf.scene).catch((error) => {
            console.warn('[room] Model unavailable:', url, error);
            return null;
        });
        const [bookshelf, sofa, table, books, bed, mug, chair, desk, monitors, tablet, pencils, speaker, keyboard, towerLamp, ceilingLamp, shelf, plant, book] = await Promise.all([
            ROOM_ASSETS.bookshelfModel, ROOM_ASSETS.sofaModel, ROOM_ASSETS.tableModel,
            ROOM_ASSETS.booksModel, ROOM_ASSETS.bedModel, ROOM_ASSETS.mugModel, ROOM_ASSETS.chairModel,
            ROOM_ASSETS.deskModel, ROOM_ASSETS.monitorsModel, ROOM_ASSETS.tabletModel, ROOM_ASSETS.pencilsModel,
            ROOM_ASSETS.speakerModel, ROOM_ASSETS.keyboardModel, ROOM_ASSETS.towerLampModel, ROOM_ASSETS.ceilingLampModel,
            ROOM_ASSETS.shelfModel, ROOM_ASSETS.plantModel, ROOM_ASSETS.bookModel,
        ].map(load));
        return { bookshelf, sofa, table, books, bed, mug, chair, desk, monitors, tablet, pencils, speaker, keyboard, towerLamp, ceilingLamp, shelf, plant, book };
    }

    /** The user's PC tower and desk lamp: one GLB sharing one atlas (colour, ORM, emission).
     * Made once: the glass and the warm diffuser get room materials, and the parts are kept
     * here because placing them moves them out of the GLB scene. */
    towerLampModel() {
        const root = this.models.towerLamp;
        if (!root) return null;
        if (!this._towerLamp) {
            const glass = this.std({ color: 0x0b1018, transparent: true, opacity: 0.35, roughness: 0.1, metalness: 0.3, side: THREE.DoubleSide });
            const diffuser = this.basic({ color: 'amber' });
            root.traverse((child) => {
                if (!child.isMesh) return;
                const name = child.material.name;
                if (name === 'tower_glass' || name === 'lamp_diffuser') {
                    child.material.dispose();
                    child.material = name === 'tower_glass' ? glass : diffuser;
                    return;
                }
                for (const key of ['map', 'emissiveMap', 'roughnessMap', 'metalnessMap']) {
                    if (child.material[key]) child.material[key].anisotropy = 4;
                }
            });
            this._towerLamp = { tower: root.getObjectByName('tower'), lamp: root.getObjectByName('lamp') };
        }
        return this._towerLamp;
    }

    /** The user's wall shelf (shelf-v1.glb): board, cube and vase sharing one atlas (colour,
     * ORM, emission). Its origin is the back centre of the board's top. The vase has its origin
     * at its base so clones of it can stand elsewhere; clones share its geometry and material. */
    shelfModel() {
        const shelf = this.models.shelf;
        if (shelf && !this._shelfReady) {
            shelf.traverse((child) => {
                if (!child.isMesh) return;
                for (const key of ['map', 'emissiveMap', 'roughnessMap', 'metalnessMap']) {
                    if (child.material[key]) child.material[key].anisotropy = 4;
                }
            });
            this._shelfVase = shelf.getObjectByName('vase');
            this._shelfReady = true;
        }
        return shelf ? { shelf, vase: this._shelfVase } : null;
    }

    /** The user's pencil mug with its room material, made once so every clone shares it. */
    mugModel() {
        const mug = this.models.mug;
        if (mug && !this._mugMaterial) {
            mug.traverse((child) => {
                if (!child.isMesh) return;
                this._mugMaterial ??= this.std({ map: child.material.map, roughness: 0.4, metalness: 0 });
                child.material.dispose();
                child.material = this._mugMaterial;
            });
        }
        return mug || null;
    }

    register(name, object) {
        this.objects[name] = object;
        return object;
    }

    addLight(light) {
        if (light && 'castShadow' in light) light.castShadow = false;
        this.roomLights.push(light);
        return light;
    }

    getObject(name) {
        return this.objects[name] || null;
    }

    setVisible(visible) {
        this.group.visible = visible;
        this.roomLights.forEach((l) => { l.visible = visible; });
    }

    setPlayerPlayback(snapshot) {
        this.mediaDisplay?.setState(snapshot);
    }

    setLightIntensity(light, intensity) {
        return this.atmosphere.setBaseIntensity(light, intensity);
    }

    /** Transfer a decorative monitor map to its live UI owner. */
    bindMonitorTexture(material, texture) {
        if (!material || !texture) return false;
        const binding = this.themeBindings.find(entry => entry.material === material && entry.textureType === 'monitor');
        if (binding) {
            if (!binding.externalTexture && material.map !== texture) material.map?.dispose?.();
            binding.externalTexture = true;
        }
        material.map = texture;
        // A uniform emissive fill washes out the live text, especially after
        // a theme change. Light the same pixels without allocating another map.
        if (material.emissive) material.emissiveMap = texture;
        material.needsUpdate = true;
        return true;
    }

    namedMaterial(name) {
        if (this.namedMaterials[name]) return this.namedMaterials[name];

        const named = {
            darkMetalMaterial: () => this.std({
                color: 0x080d16,
                emissive: 'cyan',
                emissiveIntensity: 0.018,
                roughness: 0.34,
                metalness: 0.72,
                envMapIntensity: 1.0,
            }),
            softGlassMaterial: () => this.std({
                color: 'glass',
                emissive: 'cyan',
                emissiveIntensity: 0.045,
                transparent: true,
                opacity: 0.32,
                roughness: 0.12,
                metalness: 0.08,
                envMapIntensity: 1.25,
                side: THREE.DoubleSide,
            }),
            magentaEmissiveMaterial: () => this.std({
                color: 'magenta',
                emissive: 'magenta',
                emissiveIntensity: 0.88,
                transparent: true,
                opacity: 0.74,
                roughness: 0.26,
                metalness: 0.18,
            }),
            cyanEmissiveMaterial: () => this.std({
                color: 'cyan',
                emissive: 'cyan',
                emissiveIntensity: 0.82,
                transparent: true,
                opacity: 0.68,
                roughness: 0.24,
                metalness: 0.16,
            }),
            warmAmberLightMaterial: () => this.basic({
                color: 'amber',
                transparent: true,
                opacity: 0.72,
                blending: THREE.AdditiveBlending,
                depthWrite: false,
            }),
            floorReflectiveMaterial: () => this.std({
                color: 0xffffff,
                emissive: 0x162438,
                emissiveIntensity: 0.045,
                map: this._makeFloorTexture(ROOM_THEMES[this.currentTheme] || ROOM_THEMES.cyber),
                roughness: 0.18,
                metalness: 0.68,
                envMapIntensity: 0.9,
            }),
        };

        const material = named[name]?.();
        if (!material) return null;
        material.name = name;
        if (name === 'floorReflectiveMaterial') {
            material.userData.textureType = 'floor';
            this._loadMaterialTextureAsset(material, ROOM_ASSETS.cyberFloorPanels, {
                repeat: [1, 1],
                anisotropy: 8,
                textureType: 'floor',
                applyAsEmissive: true,
                emissiveIntensity: 0.075,
            });
            this.themeBindings.push({ material, textureType: 'floor' });
        }
        this.namedMaterials[name] = material;
        return material;
    }

    _createAnimationBuckets() {
        return {
            pulse: [],
            holoLine: [],
            holoRing: [],
            windowCinematic: [],
            pcHologram: [],
            float: [],
        };
    }

    _optimizeStaticRoom() {
        this.group.traverse((child) => {
            if (!child.isMesh && !child.isPoints && !child.isLine) return;
            child.frustumCulled = true;
            if (child.isMesh) {
                child.castShadow = false;
                child.receiveShadow = false;
            }
        });
    }

    /** The light atmosphere also scales what the lights do not reach: each area's unlit
     * surfaces and glow. Screens keep their own brightness, and the avatar is not in the room. */
    _registerAtmosphereSurfaces() {
        const screens = new Set(this.themeBindings
            .filter((binding) => binding.textureType === 'monitor' || binding.textureType === 'monitorTall')
            .map((binding) => binding.material));
        this.group.traverse((child) => {
            if (!child.isMesh || !child.material) return;
            const role = atmosphereRole(child, this.group);
            for (const material of [].concat(child.material)) {
                if (!material || screens.has(material) || material.toneMapped === false
                    || material.map?.isCanvasTexture || material.map?.isVideoTexture) continue;
                this.atmosphere.registerSurface(material, role);
            }
        });
    }

    _indexAnimatedObjects() {
        this.animated = this._createAnimationBuckets();
        this.group.traverse((child) => {
            const data = child.userData;
            if (!data) return;
            if (data.pulse && child.material?.emissiveIntensity !== undefined) {
                this.animated.pulse.push(child);
            }
            if (data.holoLine && child.material) {
                this.animated.holoLine.push(child);
            }
            if (data.holoRing) {
                this.animated.holoRing.push(child);
            }
            if (data.windowCinematic && child.material?.opacity !== undefined) {
                if (data.baseOpacity === undefined) data.baseOpacity = child.material.opacity;
                if (data.cinematicAmplitude === undefined) data.cinematicAmplitude = Math.min(0.06, Math.max(0.012, child.material.opacity * 0.28));
                this.animated.windowCinematic.push(child);
            }
            if (data.pcHologram && child.material) {
                if (data.baseY === undefined) data.baseY = child.position.y;
                this.animated.pcHologram.push(child);
            }
            if (data.float) {
                if (data.baseY === undefined) data.baseY = child.position.y;
                this.animated.float.push(child);
            }
        });
    }

    std(opts = {}) {
        const theme = ROOM_THEMES[this.currentTheme] || ROOM_THEMES.cyber;
        const material = new THREE.MeshStandardMaterial({
            color: this._resolveColor(opts.color ?? 0xffffff, theme),
            map: opts.map ?? null,
            roughness: opts.roughness ?? 0.65,
            metalness: opts.metalness ?? 0.05,
            envMapIntensity: opts.envMapIntensity ?? 1,
            emissive: this._resolveColor(opts.emissive ?? 0x000000, theme),
            emissiveIntensity: opts.emissiveIntensity ?? 0,
            transparent: opts.transparent ?? false,
            opacity: opts.opacity ?? 1,
            side: opts.side ?? THREE.FrontSide,
        });
        if (opts.rimColor) addFurnitureRim(material,
            new THREE.Color(this._resolveColor(opts.rimColor, theme)), opts.rimIntensity);
        this._bindTheme(material, opts);
        return material;
    }

    /** The theme's main light, for what isn't drawn with the room's materials. */
    accent() {
        return new THREE.Color(this._resolveColor('cyan', ROOM_THEMES[this.currentTheme] || ROOM_THEMES.cyber));
    }

    basic(opts = {}) {
        const theme = ROOM_THEMES[this.currentTheme] || ROOM_THEMES.cyber;
        const params = {
            color: this._resolveColor(opts.color ?? 0xffffff, theme),
            map: opts.map ?? null,
            transparent: opts.transparent ?? false,
            opacity: opts.opacity ?? 1,
            side: opts.side ?? THREE.FrontSide,
            depthWrite: opts.depthWrite ?? true,
        };
        if (opts.blending !== undefined) params.blending = opts.blending;
        const material = new THREE.MeshBasicMaterial(params);
        this._bindTheme(material, opts);
        return material;
    }

    backdropMaterial() {
        const texture = this._makeCityTexture(ROOM_THEMES[this.currentTheme] || ROOM_THEMES.cyber);
        const material = new THREE.MeshBasicMaterial({
            map: texture,
            side: THREE.DoubleSide,
            fog: false,
        });
        material.userData.isCityBackdrop = true;
        this.windowBackdrop = new WindowBackdrop({
            material,
            loadTexture: url => this._loadRoomTexture(url),
            createFallback: theme => this._makeCityTexture(ROOM_THEMES[theme]),
            prepareTexture: texture => {
                texture.colorSpace = THREE.SRGBColorSpace;
                texture.minFilter = THREE.LinearMipmapLinearFilter;
                texture.magFilter = THREE.LinearFilter;
                // A KTX2 picture brings its mip levels; WebGL cannot generate them for it.
                texture.generateMipmaps = !texture.isCompressedTexture;
                texture.anisotropy = 4;
                fitBackdropTexture(texture);
            },
        });
        void this.windowBackdrop.setTheme(this.currentTheme);
        this.themeBindings.push({ material, backdrop: true });
        return material;
    }

    /** A room picture. A .ktx2 goes through the page's KTX2 decoder and falls back to the
     * PNG of the same name, so the room keeps its look if the decoder cannot run. */
    _loadRoomTexture(url) {
        const picture = (path) => new THREE.TextureLoader().loadAsync(path);
        if (!url.endsWith('.ktx2')) return picture(url);
        const png = url.replace(/\.ktx2$/, '.png');
        const ktx2 = sharedKTX2((window.iris || window.IRIS)?.renderer);
        if (!ktx2) return picture(png);
        return ktx2.loadAsync(url).catch((error) => {
            console.warn('[room] KTX2 picture failed, using its PNG:', url, error);
            return picture(png);
        });
    }

    _loadMaterialTextureAsset(material, url, opts = {}) {
        if (!url || !material) return;
        // One download per URL. Each material gets a clone with its own repeat/offset;
        // clones share the image, so three.js uploads it to the GPU once (the walls used
        // to hold three copies of the same picture and the floor two).
        this._textureAssets ??= new Map();
        let pending = this._textureAssets.get(url);
        if (!pending) {
            pending = this._loadRoomTexture(url);
            this._textureAssets.set(url, pending);
        }
        pending.then(
            (loaded) => {
                // The magenta edit draws pixels on a canvas, so it needs a plain picture.
                const texture = opts.muteOuterMagentaRing && !loaded.isCompressedTexture
                    ? this._makeOuterMagentaMutedTexture(loaded.image) || loaded.clone()
                    : loaded.clone();
                texture.colorSpace = THREE.SRGBColorSpace;
                texture.minFilter = THREE.LinearMipmapLinearFilter;
                texture.magFilter = THREE.LinearFilter;
                texture.generateMipmaps = !texture.isCompressedTexture;
                if (opts.repeat) {
                    texture.wrapS = THREE.RepeatWrapping;
                    texture.wrapT = THREE.RepeatWrapping;
                    texture.repeat.set(opts.repeat[0], opts.repeat[1]);
                }
                if (opts.center) texture.center.set(opts.center[0], opts.center[1]);
                if (opts.offset) texture.offset.set(opts.offset[0], opts.offset[1]);
                if (opts.rotation !== undefined) texture.rotation = opts.rotation;
                if (opts.anisotropy) texture.anisotropy = opts.anisotropy;
                material.map?.dispose?.();
                material.map = texture;
                if (opts.applyAsEmissive && material.emissiveMap !== undefined) {
                    material.emissiveMap = texture;
                    if (opts.emissiveIntensity !== undefined) this.atmosphere.setSurfaceEmissive(material, opts.emissiveIntensity);
                    material.emissive?.setHex?.(0xffffff);
                }
                material.userData.assetUrl = url;
                if (opts.textureType) material.userData.textureType = opts.textureType;
                material.needsUpdate = true;
            },
            (error) => {
                console.warn('[room] Material texture asset failed, using procedural fallback:', url, error);
            },
        );
    }

    _makeOuterMagentaMutedTexture(image) {
        if (!image || typeof document === 'undefined') return null;
        const width = image.naturalWidth || image.width;
        const height = image.naturalHeight || image.height;
        if (!width || !height) return null;
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        if (!ctx) return null;
        ctx.drawImage(image, 0, 0, width, height);
        const img = ctx.getImageData(0, 0, width, height);
        const data = img.data;
        const cx = width * 0.5;
        const cy = height * 0.5;
        const maxRadius = Math.min(width, height) * 0.5;
        for (let y = 0; y < height; y++) {
            for (let x = 0; x < width; x++) {
                const dx = (x + 0.5 - cx) / maxRadius;
                const dy = (y + 0.5 - cy) / maxRadius;
                const radius = Math.sqrt(dx * dx + dy * dy);
                if (radius < 0.64 || radius > 1.02) continue;
                const i = (y * width + x) * 4;
                const red = data[i];
                const green = data[i + 1];
                const blue = data[i + 2];
                const alpha = data[i + 3];
                if (alpha < 8) continue;
                const isCyanAccent = blue > 105 && green > 90 && red < 95;
                const isAmberAccent = red > 130 && green > 82 && blue < 90;
                if (isCyanAccent || isAmberAccent) continue;
                const isPinkPurple = blue > green * 1.05 && red > green * 1.05;
                const isOuterBand = radius > 0.73;
                if (!isPinkPurple && !isOuterBand) continue;
                const edgeBlend = Math.min(1, Math.max(0, (radius - 0.64) / 0.22));
                const luminance = red * 0.2126 + green * 0.7152 + blue * 0.0722;
                const targetR = 3 + luminance * 0.035;
                const targetG = 7 + luminance * 0.045;
                const targetB = 15 + luminance * 0.08;
                const mix = isOuterBand ? 0.98 : 0.9 + edgeBlend * 0.08;
                data[i] = red + (targetR - red) * mix;
                data[i + 1] = green + (targetG - green) * mix;
                data[i + 2] = blue + (targetB - blue) * mix;
                if (isOuterBand && isPinkPurple) data[i + 3] = alpha * 0.18;
            }
        }
        ctx.putImageData(img, 0, 0);
        const texture = new THREE.CanvasTexture(canvas);
        texture.needsUpdate = true;
        return texture;
    }

    texturedMaterial(textureType, opts = {}) {
        if (textureType === 'fabric') {
            this.fabricTextures ??= createFabricTextures();
            const material = this.std({ ...opts, map: this.fabricTextures.map });
            material.bumpMap = this.fabricTextures.bumpMap;
            material.bumpScale = 0.008;
            return material;
        }
        const theme = ROOM_THEMES[this.currentTheme] || ROOM_THEMES.cyber;
        const texture = this._makeThemeTexture(textureType, theme);
        const material = this.std({ ...opts, map: texture });
        if (textureType === 'circularRug') {
            this._loadMaterialTextureAsset(material, ROOM_ASSETS.cyberCircularRug, {
                repeat: [1, 1],
                anisotropy: 8,
                textureType: 'circularRug',
                applyAsEmissive: true,
                emissiveIntensity: 0.16,
            });
        }
        if (textureType === 'rug') {
            this._loadMaterialTextureAsset(material, ROOM_ASSETS.cyberLoungeRug, {
                repeat: [1, 1],
                anisotropy: 4,
                textureType: 'rug',
            });
        }
        this.themeBindings.push({ material, textureType });
        return material;
    }

    _bindTheme(material, opts) {
        const binding = {
            material,
            color: typeof opts.color === 'string' ? opts.color : null,
            emissive: typeof opts.emissive === 'string' ? opts.emissive : null,
            rim: typeof opts.rimColor === 'string' ? opts.rimColor : null,
        };
        if (binding.color || binding.emissive || binding.rim) {
            markTheme(material, binding);
            this.themeBindings.push(binding);
        }
    }

    _resolveColor(value, theme) {
        if (typeof value === 'string') return theme[value] ?? 0xffffff;
        return value;
    }

    _makeCityTexture(theme) {
        const canvas = document.createElement('canvas');
        canvas.width = 1344;
        canvas.height = 512;
        const ctx = canvas.getContext('2d');
        const sky = ctx.createLinearGradient(0, 0, 0, canvas.height);
        sky.addColorStop(0, theme.skyTop);
        sky.addColorStop(0.55, theme.skyMid);
        sky.addColorStop(1, theme.skyBottom);
        ctx.fillStyle = sky;
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        const horizon = 330;
        ctx.globalAlpha = 0.32;
        ctx.strokeStyle = theme.cityB;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(0, horizon);
        ctx.lineTo(canvas.width, horizon);
        ctx.stroke();

        for (let i = 0; i < 92; i++) {
            const x = (i * 47) % canvas.width;
            const w = 12 + ((i * 13) % 42);
            const h = 60 + ((i * 29) % 230);
            const y = horizon - h + ((i % 5) * 8);
            ctx.globalAlpha = 0.36;
            ctx.fillStyle = i % 3 === 0 ? '#070b18' : '#091427';
            ctx.fillRect(x, y, w, h);
            ctx.globalAlpha = 0.42;
            ctx.fillStyle = i % 4 === 0 ? theme.cityB : theme.cityA;
            for (let wy = y + 12; wy < horizon - 8; wy += 22) {
                if ((wy + i) % 3 === 0) continue;
                ctx.fillRect(x + 4, wy, Math.max(4, w - 8), 2);
            }
        }

        const cityCenterX = canvas.width * 0.5;
        const glow = ctx.createRadialGradient(cityCenterX, 220, 20, cityCenterX, 220, 420);
        glow.addColorStop(0, 'rgba(130,239,255,0.22)');
        glow.addColorStop(0.36, 'rgba(243,107,206,0.12)');
        glow.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.globalAlpha = 1;
        ctx.fillStyle = glow;
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        ctx.globalAlpha = 0.22;
        for (let layer = 0; layer < 3; layer++) {
            ctx.fillStyle = layer === 0 ? '#050914' : layer === 1 ? '#071222' : '#0a1328';
            const baseY = horizon + 20 - layer * 34;
            for (let i = 0; i < 26; i++) {
                const x = ((i * 91) + layer * 29) % canvas.width;
                const w = 18 + ((i * 17 + layer * 11) % 52);
                const h = 48 + ((i * 31 + layer * 7) % 180);
                ctx.fillRect(x, baseY - h, w, h);
            }
        }

        ctx.globalAlpha = 0.52;
        for (let i = 0; i < 34; i++) {
            const x = (i * 83) % canvas.width;
            const y = 70 + ((i * 53) % 230);
            ctx.fillStyle = i % 2 ? theme.cityA : theme.cityB;
            ctx.fillRect(x, y, 3 + (i % 4) * 2, 26 + (i % 5) * 8);
        }

        ctx.globalAlpha = 0.28;
        ctx.lineWidth = 2;
        for (let i = 0; i < 12; i++) {
            const y = 106 + ((i * 43) % 250);
            ctx.strokeStyle = i % 2 ? theme.cityA : theme.cityB;
            ctx.beginPath();
            ctx.moveTo(-80 + i * 86, y);
            ctx.bezierCurveTo(180 + i * 24, y - 32, 520 + i * 12, y + 38, canvas.width + 120, y - 8);
            ctx.stroke();
        }

        ctx.globalAlpha = 0.16;
        ctx.strokeStyle = theme.cityB;
        ctx.lineWidth = 1;
        for (let i = 0; i < 42; i++) {
            const x = (i * 61) % canvas.width;
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x - 75, canvas.height);
            ctx.stroke();
        }

        ctx.globalAlpha = 0.2;
        ctx.strokeStyle = theme.cityB;
        for (let i = 0; i < 5; i++) {
            ctx.beginPath();
            ctx.ellipse(cityCenterX, 250, 95 + i * 52, 24 + i * 10, 0, 0, Math.PI * 2);
            ctx.stroke();
        }
        ctx.globalAlpha = 1;
        return new THREE.CanvasTexture(canvas);
    }

    _makeThemeTexture(textureType, theme) {
        if (textureType === 'floor') return this._makeFloorTexture(theme);
        if (textureType === 'rug') return this._makeRugTexture(theme);
        if (textureType === 'circularRug') return this._makeCircularRugFallbackTexture(theme);
        if (textureType === 'rugLight') return this._makeRugLightTexture(theme);
        if (textureType === 'monitor') return this._makeMonitorTexture(theme);
        if (textureType === 'monitorTall') return this._makeMonitorTallTexture(theme);
        if (textureType === 'deskSurface') return this._makeDeskSurfaceTexture(theme);
        if (textureType === 'wallPanel') return this._makeWallPanelTexture(theme);
        if (textureType === 'fabric') return this._makeFabricTexture(theme);
        return this._makeFloorTexture(theme);
    }

    _makeFloorTexture(theme) {
        const canvas = document.createElement('canvas');
        canvas.width = 768;
        canvas.height = 768;
        const ctx = canvas.getContext('2d');
        const base = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
        base.addColorStop(0, '#050912');
        base.addColorStop(0.45, '#0a1422');
        base.addColorStop(1, '#080812');
        ctx.fillStyle = base;
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        ctx.globalAlpha = 0.2;
        for (let y = 0; y < canvas.height; y += 128) {
            for (let x = 0; x < canvas.width; x += 128) {
                const tonal = ((x / 128 + y / 128) % 2) ? 'rgba(255,255,255,0.035)' : 'rgba(0,0,0,0.08)';
                ctx.fillStyle = tonal;
                ctx.fillRect(x + 2, y + 2, 124, 124);
            }
        }

        ctx.globalAlpha = 0.18;
        ctx.strokeStyle = theme.cityA;
        ctx.lineWidth = 1;
        for (let y = 0; y <= canvas.height; y += 128) {
            ctx.beginPath();
            ctx.moveTo(0, y + 0.5);
            ctx.lineTo(canvas.width, y + 8.5);
            ctx.stroke();
        }
        ctx.globalAlpha = 0.2;
        ctx.strokeStyle = theme.cityB;
        for (let x = 0; x <= canvas.width; x += 128) {
            ctx.beginPath();
            ctx.moveTo(x + 0.5, 0);
            ctx.lineTo(x + 14.5, canvas.height);
            ctx.stroke();
        }

        ctx.globalAlpha = 0.1;
        for (let i = 0; i < 46; i++) {
            const x = (i * 131) % canvas.width;
            const y = (i * 73) % canvas.height;
            ctx.strokeStyle = i % 2 ? theme.cityA : theme.cityB;
            ctx.strokeRect(x, y, 52 + (i % 3) * 28, 28 + (i % 4) * 18);
        }

        ctx.globalAlpha = 0.11;
        for (let i = 0; i < 28; i++) {
            const x = (i * 89) % canvas.width;
            const y = (i * 157) % canvas.height;
            const shine = ctx.createLinearGradient(x, y, x + 130, y + 24);
            shine.addColorStop(0, 'rgba(255,255,255,0)');
            shine.addColorStop(0.5, 'rgba(160,235,255,0.24)');
            shine.addColorStop(1, 'rgba(255,255,255,0)');
            ctx.fillStyle = shine;
            ctx.fillRect(x, y, 130, 3);
        }

        ctx.globalAlpha = 0.045;
        for (let i = 0; i < 1500; i++) {
            const v = 110 + (i * 37) % 120;
            ctx.fillStyle = `rgb(${v},${v},${v})`;
            ctx.fillRect((i * 29) % canvas.width, (i * 53) % canvas.height, 1, 1);
        }

        ctx.globalAlpha = 1;
        const texture = new THREE.CanvasTexture(canvas);
        texture.wrapS = THREE.RepeatWrapping;
        texture.wrapT = THREE.RepeatWrapping;
        texture.repeat.set(2.2, 1.8);
        texture.colorSpace = THREE.SRGBColorSpace;
        return texture;
    }

    _makeRugTexture(theme) {
        const canvas = document.createElement('canvas');
        canvas.width = 512;
        canvas.height = 384;
        const ctx = canvas.getContext('2d');
        const bg = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
        bg.addColorStop(0, '#101827');
        bg.addColorStop(0.5, '#171123');
        bg.addColorStop(1, '#0d1520');
        ctx.fillStyle = bg;
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        ctx.globalAlpha = 0.16;
        ctx.fillStyle = '#ffffff';
        for (let y = 0; y < canvas.height; y += 8) {
            ctx.fillRect(0, y, canvas.width, 1);
        }
        for (let x = 0; x < canvas.width; x += 10) {
            ctx.fillRect(x, 0, 1, canvas.height);
        }

        ctx.globalAlpha = 0.28;
        ctx.strokeStyle = theme.cityA;
        ctx.lineWidth = 2;
        for (let i = -canvas.height; i < canvas.width; i += 56) {
            ctx.beginPath();
            ctx.moveTo(i, 0);
            ctx.lineTo(i + canvas.height, canvas.height);
            ctx.stroke();
        }

        ctx.globalAlpha = 0.24;
        ctx.strokeStyle = theme.cityB;
        for (let i = 0; i < 9; i++) {
            ctx.strokeRect(28 + i * 22, 28 + i * 14, canvas.width - 56 - i * 44, canvas.height - 56 - i * 28);
        }

        ctx.globalAlpha = 0.2;
        ctx.strokeStyle = theme.cityA;
        ctx.lineWidth = 5;
        ctx.strokeRect(18, 18, canvas.width - 36, canvas.height - 36);

        ctx.globalAlpha = 0.06;
        for (let i = 0; i < 1200; i++) {
            ctx.fillStyle = i % 2 ? theme.cityA : theme.cityB;
            ctx.fillRect((i * 17) % canvas.width, (i * 47) % canvas.height, 1, 2);
        }

        ctx.globalAlpha = 1;
        const texture = new THREE.CanvasTexture(canvas);
        texture.colorSpace = THREE.SRGBColorSpace;
        return texture;
    }

    _makeCircularRugFallbackTexture(theme) {
        const canvas = document.createElement('canvas');
        canvas.width = 768;
        canvas.height = 768;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#02040a';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        const cx = canvas.width / 2;
        const cy = canvas.height / 2;

        const fabric = ctx.createRadialGradient(cx, cy, 80, cx, cy, 350);
        fabric.addColorStop(0, '#0a1422');
        fabric.addColorStop(0.68, '#0d1830');
        fabric.addColorStop(1, '#26142a');
        ctx.beginPath();
        ctx.arc(cx, cy, 352, 0, Math.PI * 2);
        ctx.fillStyle = fabric;
        ctx.fill();

        ctx.globalAlpha = 0.8;
        for (const [radius, color, width] of [
            [318, theme.cityB, 2],
            [286, theme.cityA, 4],
            [224, theme.cityB, 3],
            [164, theme.cityB, 2],
            [104, theme.cityA, 2],
        ]) {
            ctx.beginPath();
            ctx.arc(cx, cy, radius, 0, Math.PI * 2);
            ctx.strokeStyle = color;
            ctx.lineWidth = width;
            ctx.stroke();
        }

        ctx.globalAlpha = 0.7;
        for (let i = 0; i < 36; i++) {
            const a = (i / 36) * Math.PI * 2;
            const r1 = i % 3 === 0 ? 270 : 304;
            const r2 = 330;
            ctx.beginPath();
            ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
            ctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2);
            ctx.strokeStyle = i % 3 === 0 ? theme.amber ? `#${theme.amber.toString(16).padStart(6, '0')}` : theme.cityA : theme.cityB;
            ctx.lineWidth = i % 3 === 0 ? 4 : 2;
            ctx.stroke();
        }

        ctx.globalAlpha = 0.08;
        for (let i = 0; i < 1800; i++) {
            const x = (i * 29) % canvas.width;
            const y = (i * 47) % canvas.height;
            ctx.fillStyle = i % 2 ? theme.cityA : theme.cityB;
            ctx.fillRect(x, y, 1, 1);
        }

        ctx.globalAlpha = 1;
        const texture = new THREE.CanvasTexture(canvas);
        texture.colorSpace = THREE.SRGBColorSpace;
        return texture;
    }

    _makeRugLightTexture(theme) {
        const canvas = document.createElement('canvas');
        canvas.width = 512;
        canvas.height = 384;
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        const lampGlow = ctx.createRadialGradient(74, 80, 12, 74, 80, 210);
        lampGlow.addColorStop(0, 'rgba(255,184,107,0.34)');
        lampGlow.addColorStop(0.38, 'rgba(255,184,107,0.12)');
        lampGlow.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = lampGlow;
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        const edgeGlow = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
        edgeGlow.addColorStop(0, 'rgba(255,184,107,0.16)');
        edgeGlow.addColorStop(0.42, 'rgba(243,107,206,0.06)');
        edgeGlow.addColorStop(0.78, 'rgba(141,239,255,0.05)');
        edgeGlow.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = edgeGlow;
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        ctx.globalAlpha = 0.45;
        ctx.strokeStyle = theme.cityA;
        ctx.lineWidth = 2;
        ctx.strokeRect(24, 24, canvas.width - 48, canvas.height - 48);
        ctx.globalAlpha = 0.28;
        ctx.strokeStyle = theme.cityB;
        ctx.lineWidth = 1;
        for (let i = 0; i < 5; i++) {
            ctx.strokeRect(54 + i * 26, 54 + i * 18, canvas.width - 108 - i * 52, canvas.height - 108 - i * 36);
        }

        ctx.globalAlpha = 1;
        const texture = new THREE.CanvasTexture(canvas);
        texture.colorSpace = THREE.SRGBColorSpace;
        return texture;
    }

    _makeMonitorTexture(theme) {
        const canvas = document.createElement('canvas');
        canvas.width = 512;
        canvas.height = 320;
        const ctx = canvas.getContext('2d');
        const bg = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
        bg.addColorStop(0, '#061827');
        bg.addColorStop(1, '#14081f');
        ctx.fillStyle = bg;
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        ctx.globalAlpha = 0.68;
        ctx.strokeStyle = theme.cityB;
        ctx.lineWidth = 2;
        for (let i = 0; i < 6; i++) {
            ctx.strokeRect(24 + i * 38, 26 + i * 18, 160 - i * 8, 96 - i * 7);
        }
        ctx.strokeStyle = theme.cityA;
        for (let i = 0; i < 8; i++) {
            ctx.beginPath();
            ctx.moveTo(300, 42 + i * 24);
            ctx.lineTo(462 - (i % 3) * 32, 42 + i * 24);
            ctx.stroke();
        }
        ctx.globalAlpha = 0.32;
        ctx.beginPath();
        ctx.arc(248, 164, 62, 0, Math.PI * 2);
        ctx.strokeStyle = theme.cityB;
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(248, 164, 96, 0.4, Math.PI * 1.7);
        ctx.strokeStyle = theme.cityA;
        ctx.stroke();
        ctx.globalAlpha = 1;
        const texture = new THREE.CanvasTexture(canvas);
        texture.colorSpace = THREE.SRGBColorSpace;
        return texture;
    }

    /** Portrait side screens: a header and columns of indented "code" lines. */
    _makeMonitorTallTexture(theme) {
        const canvas = document.createElement('canvas');
        canvas.width = 256;
        canvas.height = 512;
        const ctx = canvas.getContext('2d');
        const bg = ctx.createLinearGradient(0, 0, 0, canvas.height);
        bg.addColorStop(0, '#061827');
        bg.addColorStop(1, '#14081f');
        ctx.fillStyle = bg;
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.globalAlpha = 0.7;
        ctx.fillStyle = theme.cityB;
        ctx.fillRect(18, 20, 220, 6);
        ctx.fillRect(18, 34, 90, 4);
        const indents = [0, 1, 2, 2, 1, 2, 3, 3, 2, 1, 0, 1, 2, 2, 1, 1, 2, 3, 2, 1, 0, 1, 1, 2];
        indents.forEach((indent, i) => {
            ctx.globalAlpha = i % 7 === 3 ? 0.75 : 0.5;
            ctx.fillStyle = i % 5 === 2 ? theme.cityB : theme.cityA;
            const length = 40 + ((i * 53) % 110);
            ctx.fillRect(22 + indent * 16, 62 + i * 18, length, 5);
        });
        ctx.globalAlpha = 0.32;
        ctx.strokeStyle = theme.cityB;
        ctx.lineWidth = 2;
        ctx.strokeRect(14, 14, canvas.width - 28, canvas.height - 28);
        ctx.globalAlpha = 1;
        const texture = new THREE.CanvasTexture(canvas);
        texture.colorSpace = THREE.SRGBColorSpace;
        return texture;
    }

    _makeDeskSurfaceTexture(theme) {
        const canvas = document.createElement('canvas');
        canvas.width = 768;
        canvas.height = 256;
        const ctx = canvas.getContext('2d');

        const base = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
        base.addColorStop(0, '#050811');
        base.addColorStop(0.52, '#0b1420');
        base.addColorStop(1, '#07070d');
        ctx.fillStyle = base;
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        ctx.globalAlpha = 0.22;
        ctx.strokeStyle = 'rgba(255,255,255,0.16)';
        ctx.lineWidth = 1;
        for (let x = 24; x < canvas.width; x += 96) {
            ctx.beginPath();
            ctx.moveTo(x, 18);
            ctx.lineTo(x + 28, 18);
            ctx.lineTo(x + 46, 36);
            ctx.lineTo(x + 46, canvas.height - 34);
            ctx.lineTo(x + 18, canvas.height - 18);
            ctx.stroke();
        }

        ctx.globalAlpha = 0.34;
        ctx.strokeStyle = `#${theme.cyan.toString(16).padStart(6, '0')}`;
        ctx.lineWidth = 2;
        for (const [x, y, w] of [
            [60, 54, 150],
            [275, 188, 120],
            [495, 48, 180],
        ]) {
            ctx.beginPath();
            ctx.moveTo(x, y);
            ctx.lineTo(x + w * 0.45, y);
            ctx.lineTo(x + w * 0.58, y + 18);
            ctx.lineTo(x + w, y + 18);
            ctx.stroke();
        }

        ctx.globalAlpha = 0.28;
        ctx.strokeStyle = `#${theme.amber.toString(16).padStart(6, '0')}`;
        ctx.lineWidth = 2;
        for (const [x, y, w] of [
            [90, 210, 84],
            [404, 32, 72],
            [615, 206, 68],
        ]) {
            ctx.beginPath();
            ctx.moveTo(x, y);
            ctx.lineTo(x + w, y);
            ctx.stroke();
        }

        ctx.globalAlpha = 0.12;
        ctx.strokeStyle = `#${theme.magenta.toString(16).padStart(6, '0')}`;
        ctx.lineWidth = 1.5;
        ctx.strokeRect(244, 62, 278, 118);

        ctx.globalAlpha = 0.1;
        for (let i = 0; i < 180; i++) {
            const x = (i * 97) % canvas.width;
            const y = (i * 43) % canvas.height;
            ctx.fillStyle = i % 3 === 0 ? 'rgba(255,184,107,0.7)' : 'rgba(141,239,255,0.5)';
            ctx.fillRect(x, y, 1, 1);
        }

        ctx.globalAlpha = 1;
        const texture = new THREE.CanvasTexture(canvas);
        texture.colorSpace = THREE.SRGBColorSpace;
        texture.wrapS = THREE.ClampToEdgeWrapping;
        texture.wrapT = THREE.ClampToEdgeWrapping;
        return texture;
    }

    _makeWallPanelTexture(theme) {
        const canvas = document.createElement('canvas');
        canvas.width = 512;
        canvas.height = 512;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#08111c';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.globalAlpha = 0.2;
        ctx.strokeStyle = theme.cityB;
        for (let i = 0; i < 10; i++) {
            const x = 18 + ((i * 83) % 450);
            const y = 20 + ((i * 57) % 450);
            ctx.strokeRect(x, y, 72 + (i % 4) * 26, 44 + (i % 3) * 30);
        }
        ctx.globalAlpha = 0.14;
        ctx.strokeStyle = theme.cityA;
        for (let i = 0; i < 18; i++) {
            ctx.beginPath();
            ctx.moveTo((i * 31) % canvas.width, 0);
            ctx.lineTo((i * 31 + 76) % canvas.width, canvas.height);
            ctx.stroke();
        }
        ctx.globalAlpha = 1;
        const texture = new THREE.CanvasTexture(canvas);
        texture.wrapS = THREE.RepeatWrapping;
        texture.wrapT = THREE.RepeatWrapping;
        texture.repeat.set(1.2, 1.2);
        texture.colorSpace = THREE.SRGBColorSpace;
        return texture;
    }

    _makeFabricTexture(theme) {
        const canvas = document.createElement('canvas');
        canvas.width = 384;
        canvas.height = 384;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#182235';
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        ctx.globalAlpha = 0.16;
        ctx.strokeStyle = theme.cityB;
        for (let y = 0; y < canvas.height; y += 12) {
            ctx.beginPath();
            ctx.moveTo(0, y + ((y / 12) % 2) * 2);
            ctx.lineTo(canvas.width, y);
            ctx.stroke();
        }
        ctx.globalAlpha = 0.1;
        ctx.strokeStyle = theme.cityA;
        for (let x = 0; x < canvas.width; x += 18) {
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x + 8, canvas.height);
            ctx.stroke();
        }
        ctx.globalAlpha = 0.08;
        for (let i = 0; i < 900; i++) {
            const v = 80 + (i * 19) % 100;
            ctx.fillStyle = `rgb(${v},${v},${v})`;
            ctx.fillRect((i * 23) % canvas.width, (i * 41) % canvas.height, 1, 1);
        }
        ctx.globalAlpha = 1;
        const texture = new THREE.CanvasTexture(canvas);
        texture.wrapS = THREE.RepeatWrapping;
        texture.wrapT = THREE.RepeatWrapping;
        texture.repeat.set(1.4, 1.2);
        texture.colorSpace = THREE.SRGBColorSpace;
        return texture;
    }

    setTheme(themeName) {
        if (!ROOM_THEMES[themeName]) return;
        // Theme colours are absolute: they go onto unscaled surfaces, then the light
        // atmosphere scales them again.
        this.atmosphere.rebaseSurfaces(() => this._applyTheme(themeName));
        for (const listener of this.themeListeners) {
            try { listener(themeName); } catch { /* A listener cannot stop the room. */ }
        }
    }

    _applyTheme(themeName) {
        const theme = ROOM_THEMES[themeName];
        this.currentTheme = themeName;
        try { localStorage.setItem('IRIS.roomTheme', themeName); } catch {}
        this.scene.background = new THREE.Color(theme.ambient);
        if (this.scene.fog) this.scene.fog.color.setHex(theme.ambient);

        const applyBinding = (material, binding) => {
            if (!material || !binding) return;
            if (binding.color && material.color) material.color.setHex(theme[binding.color] ?? 0xffffff);
            if (binding.emissive && material.emissive) material.emissive.setHex(theme[binding.emissive] ?? 0x000000);
            if (binding.rim) material.userData.furnitureRim?.color.value.setHex(theme[binding.rim] ?? 0xffffff);
            material.needsUpdate = true;
        };

        for (const binding of this.themeBindings) {
            const material = binding.material;
            if (!material) continue;
            if (binding.backdrop) {
                void this.windowBackdrop.setTheme(themeName);
                continue;
            }
            if (binding.textureType) {
                // Live canvas maps belong to their UI, not to the room theme.
                if (binding.externalTexture) continue;
                if (material.userData.assetUrl && (binding.textureType === 'floor' || binding.textureType === 'rug' || binding.textureType === 'circularRug')) {
                    material.needsUpdate = true;
                    continue;
                }
                material.map?.dispose?.();
                material.map = this._makeThemeTexture(binding.textureType, theme);
                material.needsUpdate = true;
                continue;
            }
            applyBinding(material, binding);
        }

        this.group.traverse((child) => {
            const materials = Array.isArray(child.material) ? child.material : [child.material];
            materials.forEach((material) => {
                const binding = material?.userData?.themeBinding;
                if (binding) applyBinding(material, binding);
            });
        });

        const colorLight = (light, key) => light?.color?.setHex?.(theme[key]);
        colorLight(this.lampLight, 'amber');
        colorLight(this.shelfLight, 'amber');
        colorLight(this.loungeWarm, 'amber');
        colorLight(this.bedLight, 'amber');
        colorLight(this.cityCyanWash, 'cyan');
        colorLight(this.backWallUp, 'magenta');
        colorLight(this.avatarFill, 'cyan');
        colorLight(this.silhouetteWash, 'cyan');
        // Neutral warm fill preserves black leather and grey linen in every theme.
        // Their coloured edge light follows the theme in the furniture material.
        colorLight(this.bounceLight, 'amber');
        colorLight(this.rimLight, 'magenta');
        colorLight(this.objects.monitor_glow, 'monitor');
        if (this.dustParticles?.material) this.dustParticles.material.color.setHex(theme.cyan);
        this.mediaDisplay?.setTheme(theme);

        try {
            const r = (theme.gold >> 16) & 0xff;
            const g = (theme.gold >> 8) & 0xff;
            const b = theme.gold & 0xff;
            document.documentElement.style.setProperty('--halo-color', `rgba(${r},${g},${b},0.03)`);
            const vignettes = {
                classic: 'rgba(0,0,0,0.35)',
                cyber: 'rgba(2,4,18,0.40)',
                hacker: 'rgba(0,18,4,0.40)',
                sakura: 'rgba(20,6,18,0.38)',
            };
            document.documentElement.style.setProperty('--vignette-color', vignettes[themeName] || vignettes.classic);
        } catch (e) {
            console.warn('[room] CSS theme var update failed:', e);
        }
    }

    update(delta, elapsed) {
        this.atmosphere.update(delta);
        this.mediaDisplay?.update(elapsed, this.group.visible && !globalThis.document?.hidden && this.quality !== 'low');
        const screen = this.objects.monitor_screen;
        if (screen?.material) {
            const active = !!screen.userData.monitorActive;
            const base = active ? 0.94 : 0.78;
            const amp = active ? 0.035 : 0.08;
            screen.material.opacity = base + Math.sin(elapsed * 4.2) * amp;
        }

        const hourPivot = this.objects.clock_hour;
        const minPivot = this.objects.clock_min;
        const now = new Date();
        if (hourPivot) hourPivot.rotation.z = -(((now.getHours() % 12) + now.getMinutes() / 60) / 12) * Math.PI * 2;
        if (minPivot) minPivot.rotation.z = -((now.getMinutes() + now.getSeconds() / 60) / 60) * Math.PI * 2;

        if (this.plantLeaves) {
            this.plantLeaves.rotation.z = Math.sin(elapsed * 0.55) * 0.012;
        }

        const pulseScalar = 0.5 + Math.sin(elapsed * 1.5) * 0.5;
        for (const child of this.animated.pulse) {
            child.material.emissiveIntensity = (child.userData.pulse + pulseScalar * 0.18)
                * this.atmosphere.surfaceWeight(child.material);
        }

        for (const child of this.animated.holoLine) {
            if (child.userData.pcHologramLine) {
                const active = !!child.userData.pcActive;
                const base = active ? child.userData.activeOpacity : child.userData.baseOpacity;
                const amp = active ? 0.16 : 0.05;
                child.material.opacity = Math.max(0, base + Math.sin(elapsed * 2.8 + (child.userData.phase || 0)) * amp);
            } else {
                child.material.opacity = 0.38 + Math.sin(elapsed * 1.6 + (child.userData.phase || 0)) * 0.22;
            }
        }

        for (const child of this.animated.holoRing) {
            const cinematicBoost = child.userData.windowCinematic ? 0.16 : 0;
            child.rotation.z += delta * (0.18 + cinematicBoost + child.userData.spin * 0.1);
        }

        for (const child of this.animated.windowCinematic) {
            const base = child.userData.baseOpacity ?? 0.12;
            const amp = child.userData.cinematicAmplitude ?? 0.035;
            child.material.opacity = Math.max(0, base + Math.sin(elapsed * 1.2 + (child.userData.phase || 0)) * amp);
        }

        for (const child of this.animated.pcHologram) {
            const active = !!child.userData.pcActive;
            const target = active ? child.userData.pcHologram.activeOpacity : child.userData.pcHologram.baseOpacity;
            child.material.opacity = Math.max(0, target + Math.sin(elapsed * 1.9 + child.userData.pcHologram.phase) * (active ? 0.055 : 0.018));
            child.position.y = child.userData.baseY + Math.sin(elapsed * 2.2 + child.userData.pcHologram.phase) * (active ? 0.018 : 0.005);
        }

        for (const child of this.animated.float) {
            child.position.y = child.userData.baseY + Math.sin(elapsed * 0.8 + child.position.x) * 0.045;
            child.rotation.y += delta * 0.35;
        }

        if (this.rimLight) {
            this.rimLight.intensity = 2.02 + Math.sin(elapsed * 0.6) * 0.08;
        }

        if (this.dustParticles) {
            this._dustFrame = (this._dustFrame || 0) + 1;
            if ((this._dustFrame & 1) === 0) {
                const attr = this.dustParticles.geometry.attributes.position;
                const arr = attr.array;
                for (let i = 0; i < arr.length; i += 3) {
                    arr[i + 1] += delta * 0.045;
                    if (arr[i + 1] > 2.7) arr[i + 1] = -0.82;
                    arr[i] += Math.sin(elapsed * 0.25 + i) * 0.0005;
                }
                attr.needsUpdate = true;
            }
            this.dustParticles.material.opacity = 0.12 + Math.sin(elapsed * 0.5) * 0.035;
        }
    }
}

