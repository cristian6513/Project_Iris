/** Optional local WebGL QA. Pass --avatar <VRM> and optionally --playwright-module <module>. */
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const option=name=>{const i=process.argv.indexOf(name);return i>=0?process.argv[i+1]:null;};
const avatar=option('--avatar');
if(!avatar || !fs.statSync(avatar).isFile())throw Error('Pass --avatar with a local VRM file');
const {chromium}=createRequire(import.meta.url)(option('--playwright-module')||'playwright');
const output=path.resolve(root,option('--output')||'artifacts/pose-faces/local');fs.mkdirSync(output,{recursive:true});
const server=http.createServer((req,res)=>{
    const p=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
    let file;
    if(p==='/qa-avatar.vrm')file=avatar;
    else if(p.startsWith('/vendor/')) {
        const vendor=path.join(root,'companion','node_modules');
        const candidate=path.resolve(vendor,p.slice(8));
        if(candidate.startsWith(vendor+path.sep))file=candidate;
    } else if(/^\/face\/(js\/[^/]+\.js|animations\/[^/]+)$/.test(p)||p==='/tests/fixtures/vrm_poses.html')file=path.resolve(root,'.'+p);
    if(!file){res.writeHead(404);res.end();return;}
    fs.readFile(file,(err,data)=>{
        if(err){res.writeHead(404);res.end();return;}
        res.writeHead(200,{'Content-Type':{'.html':'text/html','.js':'text/javascript','.json':'application/json'}[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store'});res.end(data);
    });
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
let browser;
try {
    browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
    const page=await browser.newPage({viewport:{width:1000,height:1000}}),errors=[],warnings=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('console',message=>{if(message.type()==='warning')warnings.push(message.text());});
    const origin='http://127.0.0.1:'+server.address().port;
    await page.route('**/*',r=>r.request().url().startsWith(origin+'/')||r.request().url().startsWith('blob:')?r.continue():r.abort());
    await page.goto(origin+'/tests/fixtures/vrm_poses.html');
    await page.waitForFunction(()=>window.poseQA,{},{timeout:60000});
    const result = await page.evaluate(() => {
        const { idle, manager, micro, advance, step, camera } = poseQA;
        const avatar = manager.avatar, em = avatar.vrm.expressionManager;
        const bindings = Object.fromEntries(em.expressions.map(e => [e.expressionName, e.binds.length]));
        const profiles = idle._vrmaManifest.facial_profiles;
        const surfaces = [];
        const tracks = name => (em.getExpression(name)?.binds || []).flatMap(b =>
            (b.primitives || []).map(p => ({ p, index: b.index })));
        for (const surface of ['face', 'companion']) {
            if (surface === 'companion') {
                micro.update = () => {}; micro.applyPost = () => {};
                avatar.microLifeOwnsBlink = false; avatar.microLifeOwnsBreathing = false;
                for (const name of em.blinkExpressionNames) em.setValue(name, 0);
                if (avatar.vrm.lookAt) {
                    avatar.vrm.lookAt.target = camera;
                    avatar.vrm.lookAt.autoUpdate = true;
                }
            }
            const poses = [];
            for (const name of Object.keys(profiles)) {
                idle.nextAction = 100000;
                idle._startLoopingClip(name, { fadeIn: 0.4 });
                const action = idle._vrmaPlayer.current;
                advance(0.2);
                const playback = { duration:action.getClip().duration,
                    actionSpeed:action.getEffectiveTimeScale(), mixerSpeed:idle._vrmaPlayer.mixer.timeScale,
                    elapsed:action.time };
                if (playback.duration > 0.2 && Math.abs(playback.elapsed - 0.2) > 1e-6)
                    throw Error('Pose clock changed speed: ' + JSON.stringify({name,...playback}));
                if (playback.actionSpeed !== 1 || playback.mixerSpeed !== 1) throw Error('Accelerated pose: '+name);
                advance(0.3);
                const moodBefore = JSON.stringify(avatar.targetExpressions);
                micro._timeToNextBlink = 0.05;
                avatar._naturalBlink.wait = 0.05;
                const blinkTracks = ['blink', 'blinkLeft', 'blinkRight'].flatMap(tracks);
                let peak = 0, morphPeak = 0;
                const open = blinkTracks.map(b => b.p.morphTargetInfluences[b.index]);
                for (let i = 0; i < 72; i++) {
                    step();
                    peak = Math.max(peak, ...em.blinkExpressionNames.map(n => em.getValue(n) || 0));
                    blinkTracks.forEach((b, j) => {
                        morphPeak = Math.max(morphPeak, Math.abs(b.p.morphTargetInfluences[b.index] - open[j]));
                    });
                }
                if (avatar.animationOwnsBlink) throw Error('Blocked blinking: ' + name);
                if (avatar.vrm.lookAt && !avatar.vrm.lookAt.autoUpdate) throw Error('Blocked gaze: ' + name);
                if (JSON.stringify(avatar.targetExpressions) !== moodBefore) throw Error('Pose replaced chat mood');
                const supported = blinkTracks.length > 0;
                if (supported && (peak < 0.85 || morphPeak < 0.1)) throw Error('Invisible blink: ' + JSON.stringify({surface,name,peak,morphPeak}));
                poses.push({name, peak, morphPeak, playback, blinkSupported:supported});
            }
            idle._startLoopingClip('surprised'); advance(1);
            avatar.setMouthOpen(0.65); advance(0.5);
            if (em.getValue('aa') < 0.5) throw Error('Lip sync suppressed');
            avatar.setMouthOpen(0); idle._startLoopingClip('idle'); advance(3);
            if (em.getValue('aa') > 0.01 || em.getValue('oh') > 0.01) throw Error('Mouth stuck open');
            surfaces.push({surface,poses});
        }
        return {bindings,surfaces};
    });
    await page.evaluate(() => {
        const { manager, camera, THREE } = poseQA;
        const head = manager.avatar.vrm.humanoid.getNormalizedBoneNode('head');
        const target = head.getWorldPosition(new THREE.Vector3());
        camera.position.set(target.x, target.y, target.z + 1.5);
        camera.lookAt(target); camera.updateProjectionMatrix();
    });
    for (const pose of ['idle','wave','think','surprised','angry']) {
        await page.evaluate(name => {
            poseQA.idle._startLoopingClip(name); poseQA.advance(1.2);
            document.querySelector('#status').textContent = name;
        }, pose);
        await page.screenshot({path:path.join(output, pose+'.jpg'),type:'jpeg',quality:75});
    }
    assert.deepEqual(errors, []);
    const report = { avatar, status:'passed', ...result, errors, warnings };
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({avatar,status:report.status,poses:result.surfaces.map(s=>({surface:s.surface,count:s.poses.length,visibleBlink:s.poses.every(p=>p.blinkSupported)})),errors}));
} finally {
    await browser?.close(); await new Promise(resolve=>server.close(resolve));
}
