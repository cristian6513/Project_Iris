import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../../wizard/ui/wizard_runtime.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../../wizard/ui/index.html', import.meta.url), 'utf8');
const context = vm.createContext({});
vm.runInContext(source, context);
const R = context.IrisWizardRuntime;

const capabilities = (external) => ({
    external_hermes_install: external, external_hermes_update: external, source_update: external,
    external_autostart: external, profile_configuration: true, profile_extras: true, bundled_update: false,
});
const development = R.profile({ mode: 'development', capabilities: capabilities(true), iris_version: '1.0.0' });
const packaged = R.profile({ mode: 'packaged', capabilities: capabilities(false), iris_version: '1.0.0', hermes_version: '0.21.4' });

test('controls follow capabilities and an unknown runtime offers nothing it cannot do', () => {
    assert.equal(development.canInstallHermes, true);
    assert.equal(development.canAutostart, true);
    assert.equal(packaged.packaged, true);
    assert.equal(packaged.canInstallHermes, false);
    assert.equal(packaged.canAutostart, false);
    assert.equal(packaged.canInstallExtras, true);
    for (const broken of [null, {}, { mode: 7 }, { mode: 'packaged', capabilities: 'yes' }]) {
        const unknown = R.profile(broken);
        assert.equal(unknown.canInstallHermes, false);
        assert.equal(unknown.canAutostart, false);
    }
});

function fakeRoot(keys) {
    const nodes = keys.map(key => ({ dataset: { runtimeText: key }, textContent: `original ${key}` }));
    return { nodes, querySelectorAll: () => nodes };
}

test('development keeps the authored copy; packaged replaces every marked text', () => {
    const keys = [...html.matchAll(/data-runtime-text="([^"]+)"/g)].map(match => match[1]);
    assert.ok(keys.length >= 15);
    const dev = fakeRoot(keys);
    assert.equal(R.applyTexts(dev, development), 0);
    assert.ok(dev.nodes.every(node => node.textContent.startsWith('original')));
    const pkg = fakeRoot(keys);
    assert.equal(R.applyTexts(pkg, packaged), keys.length);
    assert.ok(pkg.nodes.every(node => !node.textContent.startsWith('original')));
});

test('packaged copy never offers installing, updating or a console', () => {
    const copy = Object.values(R.PACKAGED_TEXT).join('\n');
    for (const forbidden of [/Instalar Hermes/i, /instalador oficial/i, /\bBETA\b/, /Actualiza Hermes/i,
        /\bPATH\b/, /python -m/, /\bpip\b/, /\bgit\b/i, /\buv\b/]) {
        assert.doesNotMatch(copy, forbidden);
    }
    assert.match(R.PACKAGED_TEXT['hero-copy'], /motor de IA ya viene incluido/);
});

test('health rows name the included engine only in packaged', () => {
    assert.equal(R.healthRows(development)[0].label, '1. Hermes CLI instalado');
    assert.equal(R.healthRows(packaged)[0].label, '1. Motor Hermes Agent incluido');
    assert.deepEqual([...R.healthRows(packaged)].map(row => row.id),
        ['cli_in_path', 'config_valid', 'gateway_port', 'gateway_health', 'provider_credentials', 'iris_ws', 'voice_extra']);
});

test('a missing or damaged bundled engine offers no card: «Siguiente» already continues without AI', () => {
    for (const status of ['missing', 'damaged', 'unknown', undefined]) {
        const plan = R.packagedModePlan({ status });
        assert.equal(plan.tone, 'warn');
        assert.equal(plan.select, '');
        assert.ok(Object.values(plan.cards).every(card => !card.visible));
        assert.match(plan.detail, /motor se repara con el paquete de Iris/);
    }
});

test('QA12: diagnostics never repeat the provider form, «Iniciar IRIS» or «Siguiente»', () => {
    const setup = R.packagedModePlan({ status: 'needs_setup' });
    assert.ok(Object.values(setup.cards).every(card => !card.visible));
    assert.equal(setup.title, '');
    assert.equal(setup.detail, '');
    for (const status of ['installed', 'connected']) {
        const plan = R.packagedModePlan({ status });
        assert.equal(plan.cards.full.visible, false);
        assert.equal(plan.cards.skip.visible, false);
        assert.equal(plan.cards.repair.visible, true);
    }
    for (const status of ['needs_setup', 'installed', 'connected', 'missing']) {
        const plan = R.packagedModePlan({ status });
        assert.equal(plan.select, '');
        assert.doesNotMatch(JSON.stringify(plan), /Instalar|instalación|BETA|Modo Rápido/);
    }
});

test('channel availability defaults to available only when the backend says nothing', () => {
    assert.deepEqual({ ...R.channelState({ id: 'telegram' }) }, { available: true, detail: '' });
    const whatsapp = R.channelState({ availability: { available: false, code: 'missing_bridge', detail: 'Sin puente' } });
    assert.equal(whatsapp.available, false);
    assert.equal(whatsapp.code, 'missing_bridge');
    assert.equal(whatsapp.detail, 'Sin puente');
});
