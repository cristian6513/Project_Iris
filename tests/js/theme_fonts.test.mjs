import test from 'node:test';
import assert from 'node:assert/strict';
import { ThemeFonts, validFont, fontSettings } from '../../face/js/os_fonts.js';
import { decodeThemeFile, encodeThemeFile, normalizeStoredTheme } from '../../face/js/os_theme_file.js';

const ID='font_'+'a'.repeat(64), OTHER='font_'+'b'.repeat(64);
const buffer=()=>{const b=new Uint8Array(60);b.set([119,79,70,50]);return b.buffer};
const flush=()=>new Promise(r=>setTimeout(r,0));
function setup(fetchImpl=async()=>({ok:true,arrayBuffer:async()=>buffer()}),Face=class{constructor(family){this.family=family}async load(){return this}}){
    const values={},overlays={},faces=new Set(),notices=[];
    const runtime=new ThemeFonts({root:{style:{setProperty:(k,v)=>values[k]=v}},overlayRoot:{style:{setProperty:(k,v)=>overlays[k]=v}},
        fonts:faces,FontFaceImpl:Face,fetchImpl,onStatus:s=>notices.push(s)});
    return {runtime,values,overlays,faces,notices};
}
test('only fixed names and exact profile IDs are accepted; theme files omit custom font references',()=>{
    for(const id of ['url(https://x)','https://x/font.woff2','../font','font_'+ 'x'.repeat(64),'Outfit; color:red'])assert.equal(validFont(id),false);
    assert.equal(validFont(ID),true);assert.deepEqual(fontSettings({}),{textFont:'outfit',titleFont:'syne'});
    assert.equal(normalizeStoredTheme({textFont:ID}).textFont,ID);
    assert.throws(()=>decodeThemeFile(JSON.stringify({textFont:'url(https://x)'})));
    assert.equal(normalizeStoredTheme({textFont:'https://x'}).textFont,'outfit');
    const encoded=encodeThemeFile({textFont:ID,titleFont:'system'});assert.deepEqual(encoded.omittedFonts,['textFont']);assert.equal(encoded.payload.theme.textFont,'outfit');
    const decoded=decodeThemeFile(JSON.stringify({textFont:ID}));assert.deepEqual(decoded.omittedFonts,['textFont']);assert.equal(decoded.theme.textFont,'outfit');
});
test('selected roles share one local load, retain faces across color edits and release on replacement',async()=>{
    const calls=[];const h=setup(async(url)=>{calls.push(url);return {ok:true,arrayBuffer:async()=>buffer()}});
    h.runtime.apply({textFont:ID,titleFont:ID});await flush();assert.equal(calls.length,1);assert.equal(h.faces.size,1);
    assert.match(h.values['--font-main'],/IrisFont_/);assert.equal(h.overlays['--iris-ui-font'],h.values['--font-main']);
    h.runtime.apply({textFont:ID,titleFont:ID,accent:'#ffffff'});assert.equal(calls.length,1);
    h.runtime.apply({textFont:'system',titleFont:'syne'});assert.equal(h.faces.size,0);assert.equal(h.runtime.cache.size,0);h.runtime.dispose();
});
test('late loads cannot replace newer selections or survive disposal',async()=>{
    let release;const h=setup(()=>new Promise(r=>release=r));h.runtime.apply({textFont:ID});
    h.runtime.apply({textFont:'system'});release({ok:true,arrayBuffer:async()=>buffer()});await flush();
    assert.equal(h.values['--font-main'],'system-ui, sans-serif');assert.equal(h.faces.size,0);
    h.runtime.apply({textFont:OTHER});h.runtime.dispose();release({ok:true,arrayBuffer:async()=>buffer()});await flush();assert.equal(h.faces.size,0);
});
test('missing or undecodable fonts keep an included fallback and can be retried explicitly',async()=>{
    let okay=false;const h=setup(async()=>({ok:okay,arrayBuffer:async()=>buffer()}));h.runtime.apply({textFont:ID});await flush();
    assert.match(h.values['--font-main'],/IrisOutfit/);assert.match(h.notices.at(-1),/no está disponible/);
    okay=true;h.runtime.retry({textFont:ID});await flush();assert.match(h.values['--font-main'],/IrisFont_/);h.runtime.dispose();
    const bad=setup(undefined,class{async load(){throw Error('decoding failed')}});bad.runtime.apply({textFont:ID});await flush();
    assert.match(bad.values['--font-main'],/IrisOutfit/);assert.equal(bad.faces.size,0);bad.runtime.dispose();
});
