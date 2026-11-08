import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

function method(file,name) {
    const source=readFileSync(new URL(file,import.meta.url),'utf8');
    return source.match(new RegExp(`^    (?:async )?${name}\\([^]*?^    \\}`,'m'))[0];
}
for(const file of ['../../face/js/app.js','../../face/companion/companion.js']){
    test(`${file}: partial live changes keep the other rendering preference`,()=>{
        const fps=[],quality=[];
        const app={config:{render_fps_limit:30,render_quality:'balanced'},
            _frameLimiter:{setLimit:value=>fps.push(value)},graphicsQuality:{setQuality:value=>quality.push(value)}};
        const handle=new Function('return ({'+method(file,'_handleMessage')+'})._handleMessage')();
        handle.call(app,'render_preferences',{render_quality:'saving'});
        assert.equal(app.config.render_fps_limit,30);assert.deepEqual(fps,[]);assert.deepEqual(quality,['saving']);
        handle.call(app,'render_preferences',{render_fps_limit:60});
        assert.equal(app.config.render_quality,'saving');assert.deepEqual(fps,[60]);assert.deepEqual(quality,['saving']);
        assert.equal(app._renderRevision,2);
    });
    test(`${file}: a stale config response cannot undo a live FPS change`,async()=>{
        let resolve;const pending=new Promise(r=>{resolve=r;});const limits=[];
        const fetch=()=>pending;
        const qualities=[];
        const app={config:{render_fps_limit:30,render_quality:'saving'},bridge:{apiFetch:fetch},_frameLimiter:{setLimit:x=>limits.push(x)},
            graphicsQuality:{setQuality:q=>qualities.push(q)}};
        const load=new Function('fetch','return ({'+method(file,'_fetchConfig')+'})._fetchConfig')(fetch);
        const running=load.call(app);app._renderRevision=1;
        resolve({ok:true,json:async()=>({render_fps_limit:60,render_quality:'high'})});await running;
        assert.equal(app.config.render_fps_limit,30);assert.deepEqual(limits,[30]);
        assert.equal(app.config.render_quality,'saving');assert.deepEqual(qualities,['saving']);
    });
    test(`${file}: a skipped frame never updates animation or submits a render`,()=>{
        let scheduled=0, synced=0;
        const animate=new Function('requestAnimationFrame','performance','document',
            'return ({'+method(file,'_animate')+'})._animate')(()=>scheduled++,{now:()=>1},{visibilityState:'visible'});
        const app={inboxVoice:{sync:()=>synced++},_frameLimiter:{ready:()=>false},
            clock:{getDelta:()=>{throw Error('frame budget ignored');}}};
        animate.call(app);assert.equal(scheduled,1);assert.equal(synced,1);
    });
}

test('Companion: a stale avatar config cannot undo a live quality change before the model loads', async () => {
    const file = '../../face/companion/companion.js';
    let resolve; const pending = new Promise(r => { resolve = r; });
    const app = { bridge: { apiFetch: () => pending } };
    const read = new Function('return ({' + method(file, '_readAvatarConfig') + '})._readAvatarConfig')();
    const running = read.call(app); app._renderRevision = 1;
    resolve({ ok: true, json: async () => ({ avatar_vrm_path: 'mine.vrm', render_quality: 'balanced', render_fps_limit: 60 }) });
    assert.deepEqual(await running, { avatar_vrm_path: 'mine.vrm' });
    const fresh = { bridge: { apiFetch: async () => ({ ok: true, json: async () => ({ render_quality: 'saving' }) }) } };
    assert.deepEqual(await read.call(fresh), { render_quality: 'saving' });
    const failing = { bridge: { apiFetch: async () => ({ ok: false }) } };
    await assert.rejects(read.call(failing), /No pude consultar el avatar guardado/);
});
