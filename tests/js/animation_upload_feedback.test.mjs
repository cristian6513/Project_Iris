import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';

// wizard.js icon helpers (setIconText, uiIcon, iconLabelHtml): text-only stand-ins for the VM.
const iconStubs = {
    setIconText: (el, label) => { if (el) el.textContent = String(label ?? ''); },
    uiIcon: (token) => `<wz-icon>${token}</wz-icon>`,
    iconLabelHtml: (label) => String(label ?? ''),
};

const source = fs.readFileSync(new URL('../../wizard/ui/wizard.js', import.meta.url), 'utf8');
const uploadSource = source.slice(
    source.indexOf('async function uploadAnimationFile('), source.indexOf('async function rigModel('),
);

async function exercise(response) {
    const oldMessage = {};
    const newMessage = {};
    const classes = new Set();
    const classList = { add: value => classes.add(value), remove: value => classes.delete(value) };
    const oldRow = { classList, querySelector: () => oldMessage };
    const newRow = { classList, querySelector: () => newMessage };
    const input = { files: [{ name: 'wave.glb' }], value: 'wave.glb', closest: () => oldRow };
    let refreshed = false;
    const context = vm.createContext({ ...iconStubs,
        _ACCEPTED_ANIM_EXTS: ['glb'],
        FormData: class { append() {} },
        document: { querySelector: () => refreshed ? newRow : oldRow },
        fetch: async () => response,
        refreshAnimationSlots: async () => { refreshed = true; },
    });
    vm.runInContext(uploadSource, context);
    await context.uploadAnimationFile(input, 'wave');
    return { oldMessage, newMessage, refreshed, input, classes };
}

test('a committed animation keeps the Hermes warning visible after rows refresh', async () => {
    const result = await exercise({
        ok: true, json: async () => ({
            filename: 'wave.digest.glb', size_bytes: 1024, saved: true,
            warnings: ['Animación guardada; la sincronización con Hermes sigue pendiente.'],
        }),
    });
    assert.equal(result.refreshed, true);
    assert.match(result.newMessage.textContent, /Guardado como wave\.digest\.glb/);
    assert.match(result.newMessage.textContent, /Hermes sigue pendiente/);
    assert.equal(result.newMessage.className, 'anim-slot-msg warn');
    assert.equal(result.classes.has('error'), false);
    assert.equal(result.input.value, '');
});

test('an uncommitted upload displays the server error without claiming it was saved', async () => {
    const result = await exercise({
        ok: false, status: 409, json: async () => ({ detail: 'El manifest no es válido.' }),
    });
    assert.equal(result.refreshed, false);
    assert.match(result.oldMessage.textContent, /Error: El manifest no es válido/);
    assert.doesNotMatch(result.oldMessage.textContent, /Guardado/);
    assert.equal(result.classes.has('error'), true);
    assert.equal(result.input.value, '');
});
