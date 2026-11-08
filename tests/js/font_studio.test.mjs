import test from 'node:test';
import assert from 'node:assert/strict';
import { FontStudio } from '../../face/js/os_font_studio.js';

const ID='font_'+'a'.repeat(64),OTHER='font_'+'b'.repeat(64);
const bytes=()=>{const v=new Uint8Array(60);v.set([119,79,70,50]);return v.buffer};
const file=()=>({name:'<test>.woff2',size:60,arrayBuffer:async()=>bytes()});
const flush=()=>new Promise(r=>setTimeout(r,0));
class Node {
    constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.style={};this.textContent='';}
    append(...nodes){this.children.push(...nodes)} replaceChildren(...nodes){this.children=nodes}
    setAttribute(){} all(){return [this,...this.children.flatMap(n=>n.all())]}
    querySelectorAll(){return this.all().filter(n=>n.dataset.fontFocus)}
    text(){return this.all().map(n=>n.textContent).join(' ')}
}
function setup({route,decode,commit=true,confirm=async()=>true}={}){
    let theme={textFont:'outfit',titleFont:'syne'},saved=0;const calls=[],faces=new Set();
    const root=new Node('section');const runtime={fonts:faces,preview:null,clearPreview(){faces.delete(this.preview);this.preview=null},decode:decode|| (async family=>({family}))};
    const studio=new FontStudio({root,runtime,documentLike:{createElement:t=>new Node(t)},getTheme:()=>theme,confirm,
        commit:value=>{saved++;if(commit)theme=value;return commit},fetchImpl:async(url,init={})=>{calls.push({url,...init});return route?route(url,init):{ok:true,json:async()=>init.method==='POST'?{id:ID,label:'Prueba',bytes:60}:{fonts:[]}}}});
    return {studio,root,runtime,calls,faces,theme:()=>theme,saved:()=>saved,replace:value=>theme=value};
}
test('opening only lists local fonts; file preview decodes but neither uploads nor applies the choice',async()=>{
    const h=setup();assert.equal(h.calls.length,0);h.studio.setVisibility(true);await flush();
    await h.studio.prepare(file());assert.equal(h.calls.length,1);assert.equal(h.saved(),0);assert.equal(h.faces.size,1);
    assert.match(h.root.text(),/<test>/);assert.ok(h.root.all().every(n=>n.tag!=='img'));
    h.studio.setVisibility(false);assert.equal(h.faces.size,0);assert.equal(h.studio.staged,null);
});
test('invalid data or an obsolete decoder never creates a preview face',async()=>{
    let finish;const h=setup({decode:()=>new Promise(r=>finish=r)});h.studio.visible=true;
    const reading=h.studio.prepare(file());await flush();h.studio.setVisibility(false);finish({family:'old'});await reading;
    assert.equal(h.faces.size,0);assert.equal(h.studio.staged,null);
    h.studio.visible=true;await h.studio.prepare({...file(),size:2000001});assert.match(h.root.text(),/2 MB/);assert.equal(h.saved(),0);
});
test('explicit save uploads exact bytes once, and a failed preference commit leaves selection unchanged',async()=>{
    const h=setup({commit:false});h.studio.visible=true;await h.studio.prepare(file());const buffer=h.studio.staged.buffer;
    await h.studio.save('textFont');assert.equal(h.calls.length,1);assert.equal(h.calls[0].method,'POST');assert.equal(h.calls[0].body,buffer);
    assert.equal(h.theme().textFont,'outfit');assert.match(h.root.text(),/biblioteca, pero no se aplicó/);assert.equal(h.faces.size,0);
});
test('a later selection or closing the window wins over a pending upload',async()=>{
    for(const mode of ['new-choice','hidden']){
        let reply;const h=setup({route:()=>new Promise(r=>reply=r)});h.studio.visible=true;await h.studio.prepare(file());
        const saving=h.studio.save('textFont');if(mode==='new-choice')h.replace({textFont:OTHER,titleFont:'syne'});else h.studio.setVisibility(false);
        reply({ok:true,json:async()=>({id:ID,label:'Saved'})});await saving;
        assert.equal(h.saved(),0);assert.equal(h.theme().textFont,mode==='hidden'?'outfit':OTHER);assert.equal(h.faces.size,0);
    }
});
test('removing requires confirmation and a saved fallback before sending DELETE',async()=>{
    const no=setup({confirm:async()=>false});no.studio.visible=true;await no.studio.remove(ID);assert.equal(no.calls.length,0);
    const failed=setup({commit:false});failed.studio.visible=true;await failed.studio.remove(ID);assert.equal(failed.calls.length,0);
    const yes=setup();yes.replace({textFont:ID,titleFont:'system'});yes.studio.visible=true;yes.studio.fonts=[{id:ID,label:'Sample'}];
    await yes.studio.remove(ID);assert.deepEqual(yes.theme(),{textFont:'outfit',titleFont:'system'});assert.equal(yes.calls[0].method,'DELETE');assert.equal(yes.studio.fonts.length,0);
});

test('a loading failure takes priority over an earlier saved notice; refresh remembers keyboard focus while busy',async()=>{
    const h=setup();h.studio.visible=true;h.studio.notice='Fuente guardada y aplicada.';h.studio.status('Fuente ausente. Se usa la incluida.');
    assert.match(h.root.text(),/Fuente ausente/);assert.doesNotMatch(h.root.text(),/guardada y aplicada/);
    const refresh=h.root.all().find(n=>n.dataset.fontFocus==='refresh');h.studio.document.activeElement=refresh;
    h.studio.busy=true;h.studio.render();assert.equal(h.studio.pendingFocus,'refresh');
    h.studio.document.activeElement=null;h.studio.busy=false;
    const create=h.studio.document.createElement;let focused='';h.studio.document.createElement=t=>{const n=create(t);n.focus=()=>focused=n.dataset.fontFocus;return n};
    h.studio.render();assert.equal(focused,'refresh');assert.equal(h.studio.pendingFocus,'');
});
