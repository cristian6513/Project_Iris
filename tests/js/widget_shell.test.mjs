import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source=await readFile(new URL('../../face/js/hermes_activity.js',import.meta.url),'utf8');
function method(name,deps={}) {
    const body=source.match(new RegExp(`^    ${name}\\([^]*?^    \\}`,'m'))?.[0]; assert.ok(body);
    return new Function(...Object.keys(deps),`return ({${body}})`)(...Object.values(deps))[name];
}
test('widgets have independent desktop visibility, never pretend the Calendar window is open',()=>{
    const document={visibilityState:'visible'};
    const visible=method('_widgetsDesktopVisible',{document});
    const view={_open:true,_activeTab:'desktop',_desktopMode:'home',_desktopLauncherOpen:false};
    assert.equal(visible.call(view),true);
    for(const change of [{_open:false},{_activeTab:'settings'},{_desktopMode:'dashboard'},{_desktopLauncherOpen:true}]) assert.equal(visible.call({...view,...change}),false);
    document.visibilityState='hidden'; assert.equal(visible.call(view),false);
});
test('covered and off-scroll widgets stop, while exposed widgets keep their consumer',()=>{
    const exposed=method('_widgetExposed');
    const rect={left:600,right:900,top:100,bottom:500,width:300,height:400};
    const list={top:50,bottom:450,height:400};
    const root={getBoundingClientRect:()=>rect};
    const app={minimized:false,el:{isConnected:true,getBoundingClientRect:()=>({left:0,right:1000,top:0,bottom:600})}};
    const view={panel:{querySelector:()=>({getBoundingClientRect:()=>list})},_osWindows:new Map()};
    assert.equal(exposed.call(view,root),true);
    view._osWindows.set('calendar',app); assert.equal(exposed.call(view,root),false);
    app.minimized=true; assert.equal(exposed.call(view,root),true);
    rect.top=500;rect.bottom=900;assert.equal(exposed.call(view,root),false);
});
test('widget bounds reuse the dock/window bar clearance and shell hide/show updates their visibility',()=>{
    const properties=[],calls=[];
    const view={panel:{querySelector:(selector)=>selector==='.os-widgets'?{style:{setProperty:(...args)=>properties.push(args)}}:{getBoundingClientRect:()=>({height:650})}},
        _widgetsDesktopVisible:()=>true,
        _windowBottomClearance:(height)=>{assert.equal(height,650);return 180;},_widgets:{syncVisibility:()=>calls.push('sync')}};
    method('_syncDesktopWidgets').call(view);
    assert.deepEqual(properties,[['--widgets-bottom','180px']]);assert.equal(calls.length,1);
    const sync=method('_syncModuleVisibility',{OS_APPS:{list:()=>[]}});sync.call({_syncDesktopWidgets:()=>calls.push('shell'),_hasVisibleAppSurface:()=>false});
    assert.deepEqual(calls,['sync','shell']);
});
