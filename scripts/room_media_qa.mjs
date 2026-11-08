import assert from 'node:assert/strict';
import path from 'node:path';

/** Exercises real HTMLAudioElement events through IrisPlayerApp, with silent QA media. */
export async function verifyRoomMedia(page, output) {
    await page.setViewportSize({width:1440,height:960});
    const state=()=>page.evaluate(()=>({...roomQA.room.mediaDisplay.state}));
    assert.equal((await state()).status,'idle');
    assert.equal(await page.evaluate(()=>roomQA.player.audio.paused),true);
    await page.evaluate(()=>document.querySelector('#qa-start').hidden=false);
    await page.locator('#qa-start').click();
    await page.waitForFunction(()=>roomQA.room.mediaDisplay.state.status==='playing');
    assert.equal((await state()).title,'Neon Horizon · QA');
    assert.equal((await state()).muted,true);
    await page.evaluate(()=>{
        document.querySelector('#qa-start').hidden=true;
        roomQA.player.audio.muted=false; // The WAV is silence and Chromium output is muted.
        roomQA.room.setTheme('cyber');
        roomQA.frame([3.65,0.85,-1.35],[3.63,-0.12,-2.36],35);
    });
    await page.waitForFunction(()=>roomQA.room.mediaDisplay.state.muted===false);
    await page.screenshot({path:path.join(output,'player-tablet-playing.jpg'),type:'jpeg',quality:85});
    await page.emulateMedia({reducedMotion:'reduce'});
    const staticMotion=await page.evaluate(()=>{
        const display=roomQA.room.mediaDisplay;
        display.update(1);const before=display.indicators[0].material.opacity;
        display.update(3);return before===display.indicators[0].material.opacity;
    });
    assert.equal(staticMotion,true);
    await page.emulateMedia({reducedMotion:'no-preference'});
    await page.evaluate(()=>roomQA.player.audio.pause());
    await page.waitForFunction(()=>roomQA.room.mediaDisplay.state.status==='paused');
    await page.screenshot({path:path.join(output,'player-tablet-paused.jpg'),type:'jpeg',quality:85});
    const stable=await page.evaluate(()=>{
        const d=roomQA.room.mediaDisplay,t=d.texture;
        for(const theme of ['classic','cyber','hacker','sakura'])roomQA.room.setTheme(theme);
        const version=t.version;d.update(20);d.update(30);
        return d.texture===t&&t.version===version;
    });
    assert.equal(stable,true);
    await page.evaluate(()=>{roomQA.player.audio.src='/qa-broken.wav';roomQA.player.audio.load();});
    await page.waitForFunction(()=>roomQA.room.mediaDisplay.state.status==='error');
    await page.screenshot({path:path.join(output,'player-tablet-error.jpg'),type:'jpeg',quality:85});
    await page.evaluate(()=>roomQA.player._stopAudioForLibraryChange());
    await page.waitForFunction(()=>roomQA.room.mediaDisplay.state.status==='idle');
    assert.equal((await state()).title,'');
    return {realMediaEvents:true,noAutoplay:true,pause:true,decodeError:true,sourceRemoval:true,reducedMotion:true,textureReuse:true};
}
