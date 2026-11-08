/**
 * The book and the tablet that appear in her hands, built from their models. Room and Companion
 * share them. `std` makes a standard material: each surface has its own lights and colours.
 */
import * as THREE from 'three';

/** A copy of the book with its own materials: it glows as it appears, print and all. */
export function conjuredBook(book, std) {
    const conjured = book.clone();
    conjured.name = 'conjured_book';
    conjured.traverse((child) => {
        if (!child.isMesh) return;
        const { map } = child.material;
        child.material = std({ map, roughness: 0.75, metalness: 0, emissive: 'cyan', emissiveIntensity: 0 });
        child.material.emissiveMap = map;
    });
    return conjured;
}

/** The tablet's panel, its screen off, centred and laid flat: X across, Y up the screen, Z out of it. */
export function conjuredTablet(body, screen, std) {
    const flat = new THREE.Group();
    flat.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(
        new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0.958, -0.2868), new THREE.Vector3(0, 0.2868, 0.958),
    ).invert());
    flat.position.set(0, 0.184, 0.0501).applyQuaternion(flat.quaternion).negate();
    flat.add(
        new THREE.Mesh(body.geometry, std({ color: 0x1a2230, emissive: 'cyan', emissiveIntensity: 0, roughness: 0.3, metalness: 0.7 })),
        new THREE.Mesh(screen.geometry, std({ color: 0x0a0f18, emissive: 'cyan', emissiveIntensity: 0, roughness: 0.2, metalness: 0.3, side: THREE.DoubleSide })),
    );
    // A little light of its own, or it is lost against dark clothes.
    flat.children[0].material.userData.glow = 0.15;
    flat.children[1].material.userData.glow = 0.05;
    const held = new THREE.Group();
    held.name = 'conjured_tablet';
    held.add(flat);
    return held;
}
