import assert from 'node:assert/strict';
import path from 'node:path';

export async function verifyRoomActivity(page,output) {
    await page.emulateMedia({reducedMotion:'no-preference'});
    const result=await page.evaluate(async()=>{
        const {room,THREE}=roomQA;
        const source=await (await fetch('/face/js/hermes_activity.js')).text();
        const meta=source.match(/const STATE_META = Object\.freeze\(\{[^]*?\n\}\);/)[0];
        const method=name=>{
            const block=source.match(new RegExp('^    '+name+'\\([^]*?^    \\}','m'))?.[0];
            if(!block)throw Error('Missing '+name);
            return new Function(meta+'return ({'+block.trim()+'})')()[name];
        };
        const canvas=document.createElement('canvas');canvas.width=768;canvas.height=456;
        const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;
        const activity={room,_texture:texture,_canvas:canvas,_ctx:canvas.getContext('2d'),
            _time:0,_monitorDrawElapsed:0,_monitorDirty:true,
            _monitorReducedMotion:matchMedia('(prefers-reduced-motion: reduce)'),
            state:'thinking',timeline:[],currentDetail:'Comprobando el monitor de Room'};
        for(const name of ['_bindMonitor','_drawMonitor','_applyMonitorMaterials','update'])activity[name]=method(name);
        const screen=room.getObject('monitor_screen');
        let oldDisposed=0,liveDisposed=0;
        screen.material.map.addEventListener('dispose',()=>oldDisposed++);
        texture.addEventListener('dispose',()=>liveDisposed++);
        activity._bindMonitor();activity._applyMonitorMaterials();activity._drawMonitor();
        let mapsKept=true;
        for(let i=0;i<8;i++)for(const theme of ['cyber','sakura','hacker','classic']) {
            room.setTheme(theme);mapsKept&&=screen.material.map===texture&&screen.material.emissiveMap===texture;
        }
        const before=texture.version;
        for(let i=0;i<600;i++)activity.update(1/60);
        const redraws=texture.version-before;
        room.setVisible(false);const hiddenStart=texture.version;
        for(let i=0;i<600;i++)activity.update(1/60);
        activity.currentDetail='El monitor sigue conectado al cambiar el tema';activity._drawMonitor();
        const hiddenDraws=texture.version-hiddenStart;
        room.setVisible(true);activity.update(1/60);
        const resumed=texture.version===hiddenStart+1;
        room.setTheme('cyber');roomQA.frame([3.8,1.1,-0.1],[2.75,0.7,-3.12],40);
        roomQA.activity=activity;
        return {mapsKept,oldDisposed,liveDisposed,frames:600,redraws,hiddenDraws,resumed};
    });
    assert.equal(result.mapsKept,true);assert.equal(result.oldDisposed,1);assert.equal(result.liveDisposed,0);
    assert.ok(result.redraws>=119&&result.redraws<=121);assert.equal(result.hiddenDraws,0);assert.equal(result.resumed,true);
    await page.setViewportSize({width:1440,height:960});
    await page.evaluate(()=>{document.getElementById('inspect-room-hud')?.remove();roomQA.frame([3.8,1.1,-0.1],[2.75,0.7,-3.12],40);});
    await page.screenshot({path:path.join(output,'monitor-activity-theme.jpg'),type:'jpeg',quality:85});
    await page.emulateMedia({reducedMotion:'reduce'});
    const reduced=await page.evaluate(()=>{
        const a=roomQA.activity;a.update(1/60);const before=a._texture.version;
        for(let i=0;i<600;i++)a.update(1/60);
        const staticCanvas=a._texture.version===before;
        a.currentDetail='Cambio inmediato de estado';a._drawMonitor();
        return staticCanvas&&a._texture.version===before+1;
    });
    assert.equal(reduced,true);
    return {...result,reducedMotionAndImmediateEvents:reduced};
}
