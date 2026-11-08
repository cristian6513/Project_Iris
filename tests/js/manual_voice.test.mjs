import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';

const source = readFileSync(new URL('../../face/js/manual_voice.js', import.meta.url), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
function element() {
    const children = new Map();
    return { hidden: false, dataset: {}, listeners: {}, attributes: {}, textContent: '',
        classList: { toggle() {} }, style: { setProperty() {} }, focus() {},
        addEventListener(key, fn) { this.listeners[key] = fn; },
        setAttribute(key, value) { this.attributes[key] = value; },
        querySelector(key) { if (!children.has(key)) children.set(key, element()); return children.get(key); },
    };
}
function harness() {
    const document = { ...element(), hidden: false, body: { append() {} },
        createElement: element, querySelector: () => null, documentElement: element() };
    const window = element(), button = element(), calls = [], notices = [];
    const Class = new Function('document', 'window', 'crypto',
        source.replace('export class', 'class') + '\nreturn ManualVoiceController;')(document, window, { randomUUID });
    let revision = 0;
    const h = { document, window, button, calls, notices, replies: [], server: { state: 'off', busy: false, revision, settings: { streaming: false } } };
    h.ui = new Class({ button, notice: text => notices.push(text), fetchImpl: async (url, options) => {
        calls.push({ url, options });
        if (options.method === 'POST') {
            if (h.replies.length) return h.replies.shift()();
            const body = JSON.parse(options.body);
            if (url.endsWith('/settings')) {
                h.server.settings = { streaming: body.streaming };
                return { ok: true, json: async () => h.server.settings };
            }
            h.server = { ...body, settings: h.server.settings, revision: ++revision,
                state: body.action === 'start' ? 'recording' : body.action === 'stop' ? 'transcribing' : 'off',
                busy: body.action !== 'cancel' };
        }
        return { ok: true, json: async () => h.server };
    } });
    h.posts = () => calls.filter(call => call.options.method === 'POST');
    return h;
}

test('initial state and reconnection never start the microphone or Wake', async () => {
    const h = harness(); assert.equal(h.calls.length, 0); assert.equal(h.button.disabled, true);
    h.ui.setConnected(true); await flush();
    assert.equal(h.posts().length, 0); assert.equal(h.button.disabled, false);
    assert.equal(h.ui.panel.hidden, true);
});

test('consent explains local/remote STT and can be dismissed without activation', async () => {
    const h = harness(); h.ui.setConnected(true); await flush(); h.ui.toggle();
    assert.match(h.ui.message.textContent, /proveedor STT/);
    assert.equal(h.ui.panel.hidden, false); assert.equal(h.posts().length, 0);
    await h.ui.cancel(); assert.equal(h.posts().length, 0); assert.equal(h.ui.panel.hidden, true);
});

test('single button starts then sends; Escape cancels instead of transcribing', async () => {
    const h = harness(); h.ui.setConnected(true); await flush(); h.ui.toggle();
    h.ui.confirmButton.listeners.click(); await flush();
    assert.equal(h.ui.state.state, 'recording');
    assert.equal(h.button.querySelector('[data-voice-label]').textContent, 'Enviar');
    h.ui.toggle(); await flush(); assert.equal(h.ui.state.state, 'transcribing');
    h.document.listeners.keydown({ key: 'Escape' }); await flush();
    assert.deepEqual(h.posts().map(c => JSON.parse(c.options.body).action), ['start', 'stop', 'cancel']);
    assert.equal(h.ui.state.busy, false);
    assert.ok(h.posts().every(c => c.url === '/api/hermes-voice/runtime'));
});

test('permission rejection shows actionable feedback and never retries start', async () => {
    const h = harness(); h.ui.setConnected(true); await flush();
    h.replies.push(() => ({ ok: false, json: async () => ({ detail: 'Micrófono bloqueado en Capability Center' }) }));
    h.ui.consented = true; await h.ui.start(); await flush();
    assert.equal(h.ui.state.busy, false); assert.equal(h.posts().length, 1);
    assert.match(h.ui.message.textContent, /Capability Center/);
});

test('another window owns capture; obsolete response cannot resurrect recording', async () => {
    const h = harness(); h.ui.setConnected(true); await flush();
    h.ui.apply({ revision: 20, client_id: 'other-window', capture_id: 'two', state: 'recording', busy: true });
    assert.equal(h.button.disabled, true); h.ui.toggle(); await h.ui.cancel();
    assert.equal(h.posts().length, 0);
    h.ui.apply({ revision: 21, state: 'off', busy: false });
    h.ui.apply({ revision: 20, state: 'recording', busy: true });
    assert.equal(h.ui.state.state, 'off');
});

test('hidden window cancels only its own capture with keepalive', async () => {
    const h = harness(); h.ui.setConnected(true); await flush();
    h.ui.consented = true; await h.ui.start();
    h.document.hidden = true; h.document.listeners.visibilitychange(); await flush();
    assert.equal(JSON.parse(h.posts().at(-1).options.body).action, 'cancel');
    assert.equal(h.posts().at(-1).options.keepalive, true);
    assert.equal(h.ui.state.busy, false);
});

test('uncertain request preserves ownership and allows cancellation, no retry', async () => {
    const h = harness(); h.ui.setConnected(true); await flush();
    h.replies.push(() => { throw Error('network error'); });
    // Do not allow a status response to falsely stand in for the missing POST.
    h.ui.refresh = async () => {};
    h.ui.consented = true; await h.ui.start();
    assert.equal(h.ui.state.busy, true); assert.equal(h.ui.ownsCapture, true);
    assert.equal(h.posts().length, 1); assert.match(h.ui.message.textContent, /network/);
    await h.ui.cancel(); assert.equal(h.ui.state.busy, false);
});

test('a delivered transcript stays visible after capture ends and never sends again', async () => {
    const h = harness(); h.ui.setConnected(true); await flush();
    h.ui.consented = true; await h.ui.start(); await h.ui.command('stop');
    h.ui.showTranscript({mode:'manual',accepted:true,text:'Hola, ¿cómo estás?',capture_id:'older'});
    assert.equal(h.ui.transcript,'');
    const payload={mode:'manual',accepted:true,text:'Hola, ¿cómo estás?',capture_id:h.ui.state.capture_id};
    h.ui.showTranscript(payload); h.ui.showTranscript(payload);
    h.ui.apply({...h.ui.state,revision:100,state:'off',busy:false});
    assert.equal(h.ui.panel.hidden,false);
    assert.match(h.ui.message.textContent,/Texto enviado: «Hola, ¿cómo estás\?»/);
    assert.equal(h.posts().length,2);
    await h.ui.cancel();
    assert.equal(h.ui.panel.hidden,true);
    assert.equal(h.posts().length,2);
});

test('Face displays successful voice input once without re-submitting it', () => {
    const app=readFileSync(new URL('../../face/js/app.js',import.meta.url),'utf8');
    const handler=app.match(/case 'voice_transcript':([^]*?)break;/)[1];
    const messages=[],transcripts=[];
    const receive=new Function('payload',handler).bind({
        panel:{addMessage:(...args)=>messages.push(args)},
        manualVoice:{showTranscript:p=>transcripts.push(p)},
    });
    const payload={accepted:true,text:' hola ',mode:'manual'};
    receive(payload);
    assert.deepEqual(messages,[['user','hola']]);
    assert.deepEqual(transcripts,[payload]);
    receive({accepted:false,text:'rejected'});
    assert.equal(messages.length,1);
});

test('quick surfaces replace Wake with mic while settings retain Wake', () => {
    const root = new URL('../../', import.meta.url);
    const room = readFileSync(new URL('face/index.html', root), 'utf8');
    const companion = readFileSync(new URL('face/companion/index.html', root), 'utf8');
    const app = readFileSync(new URL('face/js/app.js', root), 'utf8');
    assert.match(room, /id="btn-hermes-voice"/); assert.doesNotMatch(room, /id="btn-hermes-wake"/);
    assert.match(companion, /id="voice-manual"/); assert.doesNotMatch(companion, /id="wake-toggle"/);
    assert.match(app, /IRIS:hermes-wake-toggle/);
    assert.doesNotMatch(source, /getUserMedia|MediaRecorder|wake\.start/);
    const activity = readFileSync(new URL('face/js/hermes_activity.js', root), 'utf8');
    assert.match(activity, /const microphoneActive = !!this\._wakeRuntime\?\.active \|\| !!this\._manualVoiceRuntime\?\.active/);
    assert.match(app, /status: this\.manualVoice\?\.state\.busy \? 'listening'/);
    const compact = readFileSync(new URL('face/companion/companion.js', root), 'utf8');
    assert.match(compact, /status: this\.manualVoice\?\.state\.busy \? 'listening'/);
});

test('streaming opt-in saves only its setting, never a capture or chat request', async () => {
    const h = harness(); h.ui.setConnected(true); await flush(); h.ui.toggle();
    assert.equal(h.ui.streamingInput.checked, false);
    await h.ui.saveStreaming(true);
    assert.equal(h.ui.streamingInput.checked, true);
    assert.deepEqual(h.posts().map(c => c.url), ['/api/hermes-voice/settings']);
    assert.deepEqual(JSON.parse(h.posts()[0].options.body), { streaming: true });
    assert.match(source, /también para la activación por voz/);
    assert.match(source, /Si no admite directo o falla/);
});

test('partial text replaces its preview, remains plain text and never sends', async () => {
    const h = harness(); h.ui.setConnected(true); await flush(); await h.ui.start();
    const base = { mode:'manual', client_id:h.ui.clientId, capture_id:h.ui.state.capture_id };
    h.ui.showPartial({ ...base, text:'hola', sequence:1 });
    h.ui.showPartial({ ...base, text:'<img onerror=attack()>', sequence:2 });
    h.ui.showPartial({ ...base, text:'atrasado', sequence:1 });
    assert.equal(h.ui.partialMessage.textContent, '<img onerror=attack()>');
    assert.equal(h.ui.partialBox.hidden, false);
    assert.equal(h.posts().length, 1);
    h.ui.showTranscript({ ...base, accepted:true, text:'hola Iris' });
    h.ui.showPartial({ ...base, text:'tardío', sequence:3 });
    assert.equal(h.ui.partialBox.hidden, true);
    assert.equal(h.ui.transcript, 'hola Iris');
    assert.equal(h.posts().length, 1);
});

test('partial owner, capture, phase, visibility and sequence are checked', async () => {
    const h = harness(); h.ui.setConnected(true); await flush(); await h.ui.start();
    const base={mode:'manual',client_id:h.ui.clientId,capture_id:h.ui.state.capture_id,text:'invisible',sequence:1};
    for (const change of [{client_id:'other'}, {capture_id:'old'}, {sequence:0}, {sequence:1.5}, {mode:'unknown'}]) h.ui.showPartial({...base,...change});
    assert.equal(h.ui.partial, '');
    h.document.hidden=true; h.ui.showPartial(base); assert.equal(h.ui.partial, '');
    h.document.hidden=false; h.ui.apply({...h.ui.state,state:'processing',revision:20});
    h.ui.showPartial(base); assert.equal(h.ui.partial, '');
});

test('cancel, reload and disconnect clear provisional text without submitting it', async () => {
    const h = harness(); h.ui.setConnected(true); await flush(); await h.ui.start();
    h.ui.showPartial({mode:'manual',client_id:h.ui.clientId,capture_id:h.ui.state.capture_id,text:'borrador',sequence:1});
    await h.ui.cancel(); assert.equal(h.ui.partial, '');
    const fresh = harness(); fresh.ui.setConnected(true); await flush();
    fresh.ui.showPartial({mode:'manual',client_id:h.ui.clientId,capture_id:h.ui.state.capture_id,text:'viejo',sequence:2});
    assert.equal(fresh.ui.partial, ''); assert.equal(fresh.posts().length, 0);
    h.ui.setConnected(false); assert.equal(h.ui.partialBox.hidden, true);
    assert.deepEqual(h.posts().map(c=>JSON.parse(c.options.body).action), ['start','cancel']);
});

test('Wake previews only its detected take and clears at final or status end', async () => {
    const h = harness(); h.ui.setConnected(true); await flush();
    const p={mode:'wake',capture_id:'wake-one',text:'mi agenda',sequence:1};
    h.ui.showPartial(p); assert.equal(h.ui.partial, '');
    h.ui.showWakeState({state:'detected',capture_id:'wake-one'}); h.ui.showPartial(p);
    assert.equal(h.ui.partial, 'mi agenda'); assert.match(h.ui.message.textContent, /Hey Hermes/);
    h.ui.showTranscript({accepted:true,text:'mi agenda'}); assert.equal(h.ui.partial, '');
    h.ui.showPartial({...p,sequence:2}); assert.equal(h.ui.partial, '');
    h.ui.showWakeState({state:'detected',capture_id:'wake-two'});
    h.ui.showPartial({...p,capture_id:'wake-two'}); h.ui.showWakeState({state:'listening'});
    assert.equal(h.ui.partial, ''); assert.equal(h.posts().length, 0);
});

test('saving streaming blocks capture until acknowledged and failure is not retried', async () => {
    const h = harness(); h.ui.setConnected(true); await flush(); h.ui.toggle();
    let done;
    h.replies.push(()=>new Promise(resolve=>{done=resolve;}));
    const save=h.ui.saveStreaming(true);
    assert.equal(h.ui.confirmButton.disabled,true); await h.ui.start();
    assert.equal(h.posts().length,1);
    done({ok:false,json:async()=>({detail:'STT inválido'})}); await save;
    assert.equal(h.ui.streaming,false); assert.match(h.ui.failure,/STT/);
    assert.equal(h.posts().length,1);
});

test('partial surface handlers never add a message or submit a turn', () => {
    for (const file of ['face/js/app.js','face/companion/companion.js']) {
        const code=readFileSync(new URL('../../'+file,import.meta.url),'utf8');
        const handler=code.match(/case 'voice_partial':([^]*?)break;/)[1];
        const partials=[];
        new Function('payload',handler).call({manualVoice:{showPartial:p=>partials.push(p)}},{text:'solo visual'});
        assert.equal(partials.length,1);
        assert.doesNotMatch(handler,/addMessage|sendMessage|sendChat|\.send\(/);
    }
});

test('cancel suppresses partials immediately while the server is still closing', async () => {
    const h=harness(); h.ui.setConnected(true); await flush(); await h.ui.start();
    const p={mode:'manual',client_id:h.ui.clientId,capture_id:h.ui.state.capture_id,text:'late',sequence:1};
    let done; h.replies.push(()=>new Promise(resolve=>{done=resolve;}));
    const cancelling=h.ui.cancel(); h.ui.showPartial(p);
    assert.equal(h.ui.partial,'');
    done({ok:true,json:async()=>({...h.ui.state,state:'off',busy:false,revision:100})}); await cancelling;
    assert.equal(h.ui.partial,'');
});
