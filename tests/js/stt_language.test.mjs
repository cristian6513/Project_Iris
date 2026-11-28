// «Idioma al hablar» (face/js/stt_language.js): one list, notes and saving shared by the voice
// dialog (IRIS OS and Companion) and IRIS OS Ajustes. Core accepts the same codes
// (tests/test_hermes_stt_settings.py checks both lists match).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
    STT_DEFAULT_LANGUAGE, STT_LANGUAGES, SttLanguageField, liveTextNote, saveSttSettings,
    sttLanguageLabel, sttLanguageNote, sttLanguageOptions, sttSettingsReady,
} from '../../face/js/stt_language.js';

const ready = { streaming: false, language: 'es', language_saved: false, live_text: false, language_override: '' };

test('the offered languages, in order, with Spanish as the default', () => {
    assert.deepEqual(STT_LANGUAGES.map((option) => option.code), ['es', 'en', 'pt', 'fr', 'de', 'it', 'ja', '']);
    assert.deepEqual(STT_LANGUAGES.map((option) => option.label),
        ['Español', 'English', 'Português', 'Français', 'Deutsch', 'Italiano', '日本語', 'Detección automática']);
    assert.equal(STT_DEFAULT_LANGUAGE, 'es');
    assert.ok(Object.isFrozen(STT_LANGUAGES) && STT_LANGUAGES.every(Object.isFrozen));
    assert.equal(sttLanguageLabel(''), 'Detección automática');
    assert.equal(sttLanguageLabel('ko'), 'ko');
});

test('a profile value outside the list is offered first, as is; known values are not repeated', () => {
    assert.equal(sttLanguageOptions('fr').length, 8);
    assert.equal(sttLanguageOptions('').length, 8);
    assert.deepEqual(sttLanguageOptions('ko')[0], { code: 'ko', label: 'ko (de tu perfil)' });
    assert.equal(sttLanguageOptions(undefined).length, 8);
});

test('settings are usable only when Core sent a language and no error', () => {
    assert.equal(sttSettingsReady(ready), true);
    assert.equal(sttSettingsReady({ ...ready, language: '' }), true);
    assert.equal(sttSettingsReady({ streaming: false }), false); // an older Core
    assert.equal(sttSettingsReady({ ...ready, error: 'config.yaml ilegible' }), false);
    assert.equal(sttSettingsReady(null), false);
});

test('notes say when the language applies and when the profile overrides it', () => {
    assert.match(sttLanguageNote(ready), /próxima grabación, sin reiniciar/);
    assert.match(sttLanguageNote({ ...ready, language_override: 'en' }), /fija «en» para este transcriptor/);
    assert.equal(sttLanguageNote({ streaming: false }), '');
});

test('the live-text note appears only for a provider without live STT', () => {
    assert.match(liveTextNote(ready), /transcriptor local no hay texto en vivo: solo con OpenAI, xAI o ElevenLabs/);
    assert.equal(liveTextNote({ ...ready, live_text: true }), '');
    assert.equal(liveTextNote({ streaming: false }), '');               // older Core: no claim
    assert.equal(liveTextNote({ ...ready, error: 'x' }), '');
});

test('saving posts exactly the change and reports Core\'s own message', async () => {
    const calls = [];
    const reply = (ok, body, json = async () => body) => async (url, options) => (calls.push([url, options]), { ok, json });
    assert.deepEqual(await saveSttSettings(reply(true, { ...ready, language: 'fr' }), { language: 'fr' }),
        { ...ready, language: 'fr' });
    assert.equal(calls[0][0], '/api/hermes-voice/settings');
    assert.equal(calls[0][1].method, 'POST');
    assert.equal(calls[0][1].body, '{"language":"fr"}');
    await assert.rejects(saveSttSettings(reply(false, { detail: 'Termina o cancela la intervención antes.' }), { language: 'en' }),
        /Termina o cancela/);
    await assert.rejects(saveSttSettings(reply(false, { detail: [{ msg: 'Input should be…' }] }), { language: 'xx' }),
        /No se pudo guardar la opción de transcripción/);
    await assert.rejects(saveSttSettings(reply(true, { language: 'fr' }), { language: 'fr' }), /No se pudo guardar/);
    await assert.rejects(saveSttSettings(reply(true, null, async () => { throw new SyntaxError('html'); }), { language: 'fr' }),
        /No se pudo guardar/);
});

function fakeSelect() {
    return { innerHTML: '', value: '', disabled: false, listeners: {},
        addEventListener(type, fn) { this.listeners[type] = fn; } };
}

test('the field renders the list, the value and the note, and reports a choice', () => {
    const select = fakeSelect(), note = { textContent: '' }, chosen = [];
    const field = new SttLanguageField({ select, note, onChoose: (code) => chosen.push(code) });
    field.render(null);
    assert.equal(select.value, 'es');
    assert.equal(select.disabled, true);
    assert.equal(note.textContent, '');
    field.render({ ...ready, language: '' });
    assert.equal(select.value, '');
    assert.equal(select.disabled, false);
    assert.match(note.textContent, /próxima grabación/);
    field.render({ ...ready, language: 'de' }, { disabled: true });
    assert.equal(select.disabled, true);
    select.value = 'ja'; select.listeners.change();
    assert.deepEqual(chosen, ['ja']);
    field.render({ ...ready, language: '"><img src=x onerror=alert(1)>' });
    assert.doesNotMatch(select.innerHTML, /<img/);
    assert.match(select.innerHTML, /&quot;&gt;&lt;img/);
});

test('IRIS OS Ajustes has the card next to voice activation and loads it with the settings', () => {
    const shell = readFileSync(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
    assert.match(shell, /import \{ SttLanguageField, saveSttSettings, sttLanguageLabel, sttSettingsReady \} from '\.\/stt_language\.js\?v=1';/);
    assert.match(shell, /data-settings-jump="settings-stt">Idioma al hablar</);
    assert.ok(shell.indexOf('id="settings-voice"') < shell.indexOf('id="settings-stt"'));
    assert.match(shell, /<select data-stt-field="language" aria-describedby="settings-stt-note"><\/select>/);
    assert.match(shell, /this\._loadWakeSettings\(\);\n\s+this\._loadSttSettings\(\);/);
    assert.match(shell, /fetch\('\/api\/hermes-voice\/status', \{ cache: 'no-store' \}\)/);
    assert.match(shell, /saveSttSettings\(\(\.\.\.args\) => fetch\(\.\.\.args\), \{ language \}\)/);
});
