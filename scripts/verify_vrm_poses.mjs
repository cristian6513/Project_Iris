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
const output=path.resolve(root,option('--output')||'.pose-qa.tmp');fs.mkdirSync(output,{recursive:true});
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
    const clips=await page.evaluate(()=>[...poseQA.idle._vrmaPlayer.clips.keys()]);
    const results=[];
    for(const name of clips.filter(n=>!n.startsWith('pc_')&&n!=='walk_in_place')) {
        const result=await page.evaluate(name=>{
            const {idle,play,advance}=poseQA,player=idle._vrmaPlayer;
            idle.nextAction=0; // force the race that formerly interrupted gestures
            if(!play(name))throw Error('Clip failed: '+name);
            const duration=player.clips.get(name).duration;
            advance(Math.min(duration*0.55,2));
            if(player.currentName!==name)throw Error('Gesture was interrupted: '+name);
            return {name,duration,head:player.clipAnimatesHead(name)};
        },name);
        await page.screenshot({path:path.join(output,name+'.png')});
        const completed=await page.evaluate(duration=>{
            const {idle,advance}=poseQA;advance(duration+1);
            return {released:!idle.isExplicitAnimationActive(),activeActions:idle._vrmaPlayer.mixer.stats.actions.inUse};
        },result.duration);
        assert.equal(completed.released,true,name+' should release ownership');
        assert.equal(completed.activeActions,1,name+' should leave only the calm idle');
        results.push({...result,...completed});
    }
    const stress=await page.evaluate(()=>{
        const {idle,manager,play,advance,step,speech}=poseQA;
        const player=idle._vrmaPlayer;
        const bones=Object.values(manager.avatar.vrm.humanoid.normalizedHumanBones).map(b=>b.node);
        let maximumBoundaryAngle=0;
        speech.start();
        for(let i=0;i<60;i++) {
            const before=bones.map(b=>b.quaternion.clone());
            play(['wave','think','nod','celebrate','idle_casual','confused'][i%6]);
            step(1e-6);
            bones.forEach((bone,j)=>maximumBoundaryAngle=Math.max(maximumBoundaryAngle,before[j].angleTo(bone.quaternion)));
            advance(0.075);
        }
        // Keep random ambient scheduling out of the subsequent loop-seam test.
        speech.stop();idle.pause();advance(12);
        return {maximumBoundaryAngle,activeActions:player.mixer.stats.actions.inUse};
    });
    assert.ok(stress.maximumBoundaryAngle<0.01,JSON.stringify(stress));
    assert.equal(stress.activeActions,1);
    const loops=await page.evaluate(()=>{
        const {idle,manager,advance,step}=poseQA, player=idle._vrmaPlayer;
        const bones=Object.values(manager.avatar.vrm.humanoid.normalizedHumanBones).map(b=>b.node);
        idle.nextAction=100000;
        return [...player.clips].map(([name,clip])=>{
            idle._startLoopingClip(name,{fadeIn:0});
            let maximumPositionJump=0,maximumAngle=0;
            for(let cycle=0;cycle<3;cycle++){
                if (!player.current) throw Error('Missing action for loop '+name+' duration '+clip.duration+' cycle '+cycle);
                advance(clip.duration-player.current.time-1e-5);
                const before=bones.map(b=>({p:b.position.clone(),q:b.quaternion.clone().normalize()}));
                step(2e-5);
                bones.forEach((b,i)=>{
                    maximumPositionJump=Math.max(maximumPositionJump,b.position.distanceTo(before[i].p));
                    maximumAngle=Math.max(maximumAngle,b.quaternion.clone().normalize().angleTo(before[i].q));
                });
                advance(0.6);
            }
            return {name,maximumPositionJump,maximumAngle};
        });
    });
    for(const loop of loops){
        assert.ok(loop.maximumPositionJump<0.001,JSON.stringify(loop));
        assert.ok(loop.maximumAngle<0.005,JSON.stringify(loop));
    }
    assert.deepEqual(errors,[]);
    const report={avatar,loops,status:'passed',clips:results,stress,errors,warnings};
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report,null,2));
} finally {
    await browser?.close();await new Promise(resolve=>server.close(resolve));
}
