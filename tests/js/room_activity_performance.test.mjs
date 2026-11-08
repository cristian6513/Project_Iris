import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

async function method(file,name,deps={},optional=false) {
    const source=await readFile(new URL('../../face/js/'+file,import.meta.url),'utf8');
    const block=source.match(new RegExp('^    '+name+'\\([^]*?^    \\}','m'))?.[0];
    if(!block&&optional)return undefined;
    assert.ok(block,name+' must exist');
    return new Function(...Object.keys(deps),'return ({'+block.trim()+'})')(...Object.values(deps))[name];
}

async function canvasHarness() {
    const document={hidden:false};
    const commands=[];
    const ctx=new Proxy({}, {get:(_,key)=>key==='createLinearGradient'
        ? ()=>({addColorStop(){}}) : (...args)=>commands.push([key,...args])});
    let uploads=0;
    const texture={set needsUpdate(value){if(value)uploads++;}};
    const meta={label:'Test',accent:'#fff',detail:'Test',glow:0};
    const deps={document,STATE_META:{idle:meta,thinking:meta,tool:meta}};
    const state={_ctx:ctx,_canvas:{width:768,height:456},_texture:texture,
        _monitorMeshes:[{visible:true}],_monitorReducedMotion:{matches:false},
        _monitorDrawElapsed:0,_monitorDirty:false,_time:0,state:'thinking',timeline:[],
        room:{group:{visible:true},quality:'high'}};
    state._drawMonitor=await method('hermes_activity.js','_drawMonitor',deps);
    const update=await method('hermes_activity.js','update',deps);
    return {state,document,commands,get uploads(){return uploads;},tick:(seconds,fps=60)=>{
        for(let i=0;i<seconds*fps;i++)update.call(state,1/fps);
    }};
}

test('theme changes preserve the live monitor map and release only its old decoration',async()=>{
    let decorativeDisposed=0,liveDisposed=0;
    const decoration={dispose(){decorativeDisposed++;}};
    const live={dispose(){liveDisposed++;}};
    const material={map:decoration,userData:{},color:{setHex(){}},emissive:{setHex(){}}};
    const screen={material,userData:{},traverse(){}};
    const room={getObject:()=>[screen],themeBindings:[{material,textureType:'monitor'}],
        group:{traverse(){}},scene:{},objects:{},_makeThemeTexture:()=>({dispose(){}})};
    room.bindMonitorTexture=await method('room.js','bindMonitorTexture',{},true);
    const bind=await method('hermes_activity.js','_bindMonitor');
    const themeDeps={
        ROOM_THEMES:{cyber:{ambient:0,gold:0,monitor:0},sakura:{ambient:1,gold:1,monitor:1}},
        THREE:{Color:class{}},localStorage:{setItem(){}},document:{documentElement:{style:{setProperty(){}}}},
    };
    const theme=await method('room.js','setTheme',themeDeps);
    // setTheme applies the theme on unscaled surfaces (the light atmosphere rescales them).
    room._applyTheme=await method('room.js','_applyTheme',themeDeps);
    room.atmosphere={rebaseSurfaces:(change)=>change()};
    room.themeListeners=new Set();
    const activity={room,_texture:live};
    bind.call(activity);
    theme.call(room,'sakura');theme.call(room,'cyber');
    assert.equal(material.map,live);
    assert.equal(material.emissiveMap,live);
    assert.equal(liveDisposed,0);
    assert.equal(decorativeDisposed,1);
    bind.call(activity);assert.equal(decorativeDisposed,1);assert.equal(liveDisposed,0);
});

test('steady activity caps texture uploads independently of display refresh rate',async()=>{
    for(const fps of [30,60,144]) {
        const h=await canvasHarness();h.tick(10,fps);
        assert.ok(h.uploads>=100&&h.uploads<=121,`${fps}Hz produced ${h.uploads} redraws`);
    }
});

test('hidden Room or page defers redraws and shows the latest state once visible',async()=>{
    const h=await canvasHarness();h.document.hidden=true;
    h.tick(10);h.state.currentDetail='Newest status';h.state._drawMonitor();
    assert.equal(h.uploads,0);
    h.document.hidden=false;h.tick(1/60);
    assert.equal(h.uploads,1);
    assert.ok(h.commands.some(c=>c[0]==='fillText'&&c[1]==='Newest status'));
    h.state.room.group.visible=false;h.tick(10);assert.equal(h.uploads,1);
    h.state.room.group.visible=true;h.tick(1/60);assert.equal(h.uploads,2);
});

test('reduced motion keeps the monitor static, but a new status is drawn immediately',async()=>{
    const h=await canvasHarness();h.state._monitorReducedMotion.matches=true;
    h.tick(10);assert.equal(h.uploads,1);
    h.state.currentDetail='New event';h.state._drawMonitor();assert.equal(h.uploads,2);
    h.tick(10);assert.equal(h.uploads,2);
    h.state._monitorReducedMotion.matches=false;h.tick(1/60);assert.equal(h.uploads,3);
});

test('low quality reduces monitor redraws without delaying explicit events',async()=>{
    const h=await canvasHarness();h.state.room.quality='low';h.tick(10);
    assert.ok(h.uploads>=39&&h.uploads<=41,`${h.uploads} low-quality redraws`);
    const before=h.uploads;h.state._drawMonitor();assert.equal(h.uploads,before+1);
});
