/** Live Face QA. Requires a running local Core; does not start microphone or camera. */
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const option=name=>{const i=process.argv.indexOf(name);return i<0?null:process.argv[i+1];};
const {chromium}=createRequire(import.meta.url)(option('--playwright-module')||'playwright');
const url=option('--url')||'http://127.0.0.1:8765/';
const output=option('--output')||'.pose-qa.tmp';fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
 try {
 const page=await browser.newPage({viewport:{width:1280,height:800}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url,{waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>window.iris?.idle?._vrmaPlayer?.clips?.size===16,{},{timeout:60000});
 const result=await page.evaluate(async()=>{
  const app=window.iris, micro=app.microLife, player=app.idle._vrmaPlayer;
  const animate=app._animate.bind(app);app._animate=()=>{};
  await new Promise(requestAnimationFrame);
  const raf=window.requestAnimationFrame,render=app.composer.render.bind(app.composer);
  window.requestAnimationFrame=()=>0;app.clock.getDelta=()=>1/60;app.composer.render=()=>{};
  app.idle.nextAction=100000;
  const apply=micro._applyBoneDelta.bind(micro);
  let overlayFrames=0,maximumOverlayError=0;
  micro._applyBoneDelta=(bone,name,delta)=>{
   const expected=bone.quaternion.clone().multiply(delta).normalize();
   apply(bone,name,delta);overlayFrames++;
   maximumOverlayError=Math.max(maximumOverlayError,bone.quaternion.angleTo(expected));
  };
  const poses=[];
  for(const name of ['idle','wave','think','idle_casual','shy','dance']){
   if(name.startsWith('idle'))app.idle._startLoopingClip(name,{fadeIn:.5});else app.idle.playClipExternal(name);
   const total=Math.ceil((player.clips.get(name).duration+2)*60);
   let maximumRootStep=0;let prev=app.avatarManager.avatar.group.position.clone();
   for(let i=0;i<total;i++){
    animate();
    const group=app.avatarManager.avatar.group;
    maximumRootStep=Math.max(maximumRootStep,group.position.distanceTo(prev));prev.copy(group.position);
    for(const {node} of Object.values(app.avatarManager.avatar.vrm.humanoid.normalizedHumanBones)){
     if(!node.quaternion.toArray().every(Number.isFinite)||Math.abs(node.quaternion.lengthSq()-1)>.005)throw Error('Invalid bone '+node.name);
    }
   }
   poses.push({name,frames:total,maximumRootStep});
  }
  render();window.requestAnimationFrame=raf;
  return {overlayFrames,maximumOverlayError,poses,room:!!app.room,avatar:!!app.avatarManager.avatar.vrm};
 });
 await page.screenshot({path:path.join(output,'face-fixed.png')});
 assert.ok(result.overlayFrames>100);assert.ok(result.maximumOverlayError<1e-6);
 for(const pose of result.poses)assert.ok(pose.maximumRootStep<.01,JSON.stringify(pose));
 assert.deepEqual(errors,[]);
 fs.writeFileSync(path.join(output,'face-runtime-report.json'),JSON.stringify({result,errors},null,2));
 console.log(JSON.stringify({result,errors},null,2));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
