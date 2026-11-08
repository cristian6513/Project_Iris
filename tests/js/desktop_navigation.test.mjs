import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { homeRecovery } from '../../face/js/os_recovery.js';
const source = readFileSync(new URL('../../face/js/hermes_activity.js',import.meta.url),'utf8');
test('the real navigation distinguishes Home and Panel, including sidebar and dock, and keeps Settings separate',()=>{
    const code=source.match(/^    _renderDesktopNavigation\([^]*?^    \}/m)[0];const render=new Function(`return ({${code}})._renderDesktopNavigation`)();
    const node=dataset=>({dataset,selected:false,current:false,classList:{toggle(){}},setAttribute(){this.current=true},removeAttribute(){this.current=false}});
    const nodes=[node({workbenchTab:'desktop'}),node({workbenchTab:'settings'}),node({osDesktopMode:'home'}),node({osDesktopMode:'dashboard'})];
    const view={_activeTab:'desktop',_desktopMode:'dashboard',panel:{querySelectorAll:()=>nodes}};
    render.call(view);assert.deepEqual(nodes.map(n=>n.current),[false,false,false,true]);
    view._desktopMode='home';render.call(view);assert.deepEqual(nodes.map(n=>n.current),[true,false,true,false]);
    view._activeTab='settings';render.call(view);assert.deepEqual(nodes.map(n=>n.current),[false,true,false,false]);
});
test('home failures offer recovery separately from technical codes and never assert empty/default data',()=>{
    const reply=homeRecovery({status:503,message:'HTTP 503 / internal_code'});assert.doesNotMatch(reply.message,/HTTP|internal_code/);assert.match(reply.detail,/HTTP/);
    assert.match(reply.message,/no se restablecieron/);assert.equal(reply.action,'Volver a cargar');
    assert.match(homeRecovery({status:409,message:'conflict'}).message,/otra ventana/);
    assert.match(homeRecovery({status:422,message:'bad'}).message,/anterior se conserva/);
});
