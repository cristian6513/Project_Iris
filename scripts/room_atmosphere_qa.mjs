import assert from 'node:assert/strict';
import path from 'node:path';

async function attachControls(page) {
    await page.evaluate(async()=>{
        for(const file of ['styles.css','iris_os_responsive.css','room_atmosphere.css']) {
            const link=document.createElement('link');link.rel='stylesheet';link.href='/face/css/'+file;
            await new Promise((resolve,reject)=>{link.onload=resolve;link.onerror=reject;document.head.append(link);});
        }
        // Read the real app markup but never execute its scripts or start Core.
        const parsed=new DOMParser().parseFromString(await (await fetch('/face/index.html')).text(),'text/html');
        const hud=parsed.querySelector('#inspect-room-hud');
        if(!hud)throw Error('Missing app inspection HUD');
        document.body.append(hud);hud.hidden=false;hud.classList.add('active');
        roomQA.controls=roomQA.bindRoomAtmosphereControls(hud,()=>roomQA.room.atmosphere);
        const copy=hud.querySelector('.room-atmosphere-controls').cloneNode(true);
        copy.id='qa-other-controls';copy.hidden=true;
        for(const node of copy.querySelectorAll('[id]'))node.removeAttribute('id');
        document.body.append(copy);
        roomQA.otherControls=roomQA.bindRoomAtmosphereControls(copy,()=>roomQA.room.atmosphere);
        document.querySelector('#qa-start').hidden=true;
    });
}

export async function verifyRoomAtmosphere(page, output) {
    await page.setViewportSize({width:1440,height:960});
    await attachControls(page);
    const select=page.locator('#inspect-room-atmosphere');
    assert.equal(await select.inputValue(),'original');
    await page.evaluate(()=>{roomQA.room.setTheme('cyber');roomQA.preset('iso');});
    const base=await page.evaluate(()=>roomQA.room.lampLight.intensity);
    for(const mode of ['original','work','relax','night']) {
        await select.selectOption(mode);
        await page.waitForFunction(()=>!roomQA.room.atmosphere._transition);
        assert.equal(await page.locator('#qa-other-controls select').inputValue(),mode);
        const actual=await page.evaluate(()=>{
            const r=roomQA.room,a=r.atmosphere;
            return {mode:a.mode,intensity:r.lampLight.intensity,weight:a.snapshot().weights.desk,lights:r.roomLights.length,avatar:r.avatarFill.intensity};
        });
        assert.equal(actual.mode,mode);assert.equal(actual.intensity,base*actual.weight);
        assert.equal(actual.lights,14);assert.equal(actual.avatar,1.12);
        await page.screenshot({path:path.join(output,`atmosphere-${mode}.jpg`),type:'jpeg',quality:85});
    }
    const composed=await page.evaluate(()=>{
        const r=roomQA.room;
        roomQA.sceneActions._setMonitor(true);roomQA.sceneActions._setWindowCinematic(true);
        const night=r.getObject('monitor_glow').intensity===1.35*0.3&&r.cityCyanWash.intensity===1.04*0.25;
        r.setTheme('sakura');const kept=r.atmosphere.mode==='night';
        r.atmosphere.setMode('original',{immediate:true});
        return night&&kept&&r.getObject('monitor_glow').intensity===1.35&&r.cityCyanWash.intensity===1.04;
    });
    assert.equal(composed,true);
    await page.emulateMedia({reducedMotion:'reduce'});
    await select.selectOption('night');
    assert.equal(await page.evaluate(()=>roomQA.room.atmosphere._transition),null);
    // A fresh page must restore the manual choice, not activate audio or timers.
    await page.reload();await page.waitForFunction(()=>window.roomQA);
    assert.equal(await page.evaluate(()=>roomQA.room.atmosphere.mode),'night');
    assert.equal(await page.evaluate(()=>roomQA.player.audio.paused),true);
    await attachControls(page);
    for(const [width,height] of [[1200,540],[390,844]]) {
        await page.setViewportSize({width,height});
        await page.evaluate(()=>roomQA.preset('iso'));
        await select.scrollIntoViewIfNeeded();
        const box=await select.boundingBox();assert.ok(box&&box.x>=0&&box.y>=0&&box.x+box.width<=width&&box.y+box.height<=height);
        await select.focus();assert.equal(await select.evaluate(el=>document.activeElement===el),true);
        await page.screenshot({path:path.join(output,`atmosphere-controls-${width}.jpg`),type:'jpeg',quality:85});
    }
    await page.evaluate(()=>{roomQA.room.atmosphere._storage=()=>{throw Error('Blocked QA storage');};});
    await select.selectOption('relax');
    assert.match(await page.locator('#inspect-room-atmosphere-note').textContent(),/Solo esta sesión/);
    return {manualPresets:true,controlsSynchronized:true,sceneActionsCompose:true,themeIndependent:true,reducedMotion:true,reloadPersistence:true,responsive:true,storageFailureVisible:true};
}
