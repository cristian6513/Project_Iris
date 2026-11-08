// QA12: signing in with a provider account was a small «Usar OAuth» checkbox under the API key;
// the «Conectar cuenta» button appeared only after ticking it. Now the sign-in is a visible button
// that switches the method and starts the login in one click, with «Usar API key» to go back.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const read = path => readFileSync(new URL(`../../wizard/ui/${path}`, import.meta.url), 'utf8');
const html = read('index.html');
const wizardSource = read('wizard.js');
const loginSource = read('provider_login_ui.js');

function element(id) {
    return { id, hidden: false, checked: false, disabled: false, focused: false, focus() { this.focused = true; } };
}

function page(auth, checked = false) {
    const nodes = Object.fromEntries(['use-oauth', 'oauth-offer', 'btn-oauth-use-key', 'llm-api-key'].map(id => [id, element(id)]));
    nodes['use-oauth'].checked = checked;
    const calls = [];
    const context = vm.createContext({
        document: { getElementById: id => nodes[id] || null },
        LLM_PROVIDERS: { chosen: { auth } },
        selectedLLMProvider: () => 'chosen',
    });
    const start = wizardSource.indexOf('function _syncOAuthChoice()');
    const end = wizardSource.indexOf('\n}\n', start) + 3;
    vm.runInContext(wizardSource.slice(start, end), context);
    vm.runInContext(loginSource, context);
    context.toggleOAuthMode = () => { calls.push(`toggle:${nodes['use-oauth'].checked}`); context._syncOAuthChoice(); };
    context.authenticateProvider = async () => { calls.push('login'); };
    context._syncOAuthChoice();
    return { nodes, calls, context };
}

test('the sign-in is a visible button, not a checkbox to tick first', () => {
    assert.match(html, /<input type="checkbox" id="use-oauth" hidden onchange="toggleOAuthMode\(\)">/);
    assert.match(html, /<button type="button" id="btn-oauth-offer" class="btn-primary btn-sm" onclick="chooseOAuthLogin\(\)">Conectar cuenta<\/button>/);
    assert.doesNotMatch(html, /Usar OAuth \(inicio de sesión con navegador\)/);
});

test('a dual provider offers «Conectar cuenta» beside its API key and starts the login in one click', () => {
    const { nodes, calls, context } = page('api+oauth');
    assert.equal(nodes['oauth-offer'].hidden, false);
    assert.equal(nodes['btn-oauth-use-key'].hidden, true);
    context.chooseOAuthLogin();
    assert.deepEqual(calls, ['toggle:true', 'login']);
    assert.equal(nodes['oauth-offer'].hidden, true);
    assert.equal(nodes['btn-oauth-use-key'].hidden, false);
    context.chooseApiKey();
    assert.deepEqual(calls.slice(2), ['toggle:false']);
    assert.equal(nodes['oauth-offer'].hidden, false);
    assert.equal(nodes['llm-api-key'].focused, true);
});

test('Q04: a completed sign-in reads only the chosen access, cancelling an older general refresh', () => {
    const { context } = page('oauth', true);
    const calls = [];
    context.window = { _catalogRefreshState: { inFlight: true } };
    context.cancelCatalogRefresh = () => { calls.push('cancel'); context.window._catalogRefreshState = null; };
    context.loadProviderModels = async () => { calls.push('models'); };
    context._refreshCatalogAfterLogin();
    context._refreshCatalogAfterLogin();
    assert.deepEqual(calls, ['cancel', 'models', 'models']);
    let completed;
    context.IrisProviderAuth = class { constructor(options) { completed = options.onAuthenticated; } };
    context.wizardState = {};
    context._providerLoginController();
    completed('another-provider');
    assert.deepEqual(calls, ['cancel', 'models', 'models'], 'a late old login never loads models into the new provider');
    completed('chosen');
    assert.equal(calls.at(-1), 'models');
});

test('OAuth-only and API-only providers show no method switch', () => {
    const oauthOnly = page('oauth', true);
    assert.equal(oauthOnly.nodes['oauth-offer'].hidden, true);
    assert.equal(oauthOnly.nodes['btn-oauth-use-key'].hidden, true);
    oauthOnly.nodes['use-oauth'].disabled = true;
    oauthOnly.context.chooseApiKey();
    assert.deepEqual(oauthOnly.calls, []);
    const apiOnly = page('api');
    assert.equal(apiOnly.nodes['oauth-offer'].hidden, true);
    assert.equal(apiOnly.nodes['btn-oauth-use-key'].hidden, true);
});
