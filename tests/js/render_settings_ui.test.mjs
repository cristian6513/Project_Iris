import test from 'node:test';
import assert from 'node:assert/strict';
import { RenderSettings } from '../../face/js/render_settings.js';

function ui(fetchImpl) {
    const elements = Object.fromEntries(['[data-render-fps]','[data-render-quality]','[data-render-help]',
        'button','[role="status"]','[data-render-pill]'].map(key=>[key,{}]));
    const root={querySelector:key=>elements[key]};
    elements.button.disabled=true; // Mirrors the disabled attribute in the initial HTML.
    const settings=new RenderSettings(root,{fetchImpl});
    return {settings,elements};
}
const response = data=>({ok:true,json:async()=>data});

test('editing either choice leaves the saved pill unchanged until Core acknowledges both', async()=>{
    const calls=[];
    const h=ui(async(_,options)=>{calls.push(options);return response(options?.method==='PUT'?JSON.parse(options.body):{render_fps_limit:60,render_quality:'balanced'});});
    await h.settings.load();
    assert.equal(h.settings.pill.textContent,'Equilibrado · 60 FPS');
    h.settings.quality.value='saving';h.settings.quality.onchange();
    assert.equal(h.settings.pill.textContent,'Equilibrado · 60 FPS');
    assert.match(h.settings.help.textContent,/resolución/);
    await h.settings.save();
    assert.deepEqual(JSON.parse(calls[1].body),{render_fps_limit:60,render_quality:'saving'});
    assert.equal(h.settings.pill.textContent,'Ahorro · 60 FPS');
    assert.equal(h.settings.dirty,false);
});

test('failed or incomplete confirmations never claim that quality was applied', async()=>{
    for(const reply of [{ok:false},response({render_fps_limit:30}),response({render_fps_limit:30,render_quality:'ultra'})]){
        let calls=0;
        const h=ui(async()=>++calls===1?response({render_fps_limit:60,render_quality:'balanced'}):reply);
        await h.settings.load();h.settings.quality.value='high';h.settings.quality.onchange();
        await h.settings.save();
        assert.equal(h.settings.pill.textContent,'Equilibrado · 60 FPS');
        assert.equal(h.settings.dirty,true);assert.equal(h.settings.quality.disabled,false);
        assert.doesNotMatch(h.settings.status.textContent,/guardado y aplicado/);
    }
});

test('an invalid initial read keeps both selectors and the save button disabled', async()=>{
    const h=ui(async()=>response({render_quality:'balanced'}));await h.settings.load();
    assert.equal(h.settings.select.disabled,true);assert.equal(h.settings.quality.disabled,true);
    assert.equal(h.settings.button.disabled,true);
});
