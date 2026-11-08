/** Optional, isolated WebGL QA. Requires local Three.js and Playwright. */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import assert from 'node:assert/strict';
import { verifyRoomMedia } from './room_media_qa.mjs';
import { verifyRoomAtmosphere } from './room_atmosphere_qa.mjs';
import { verifyRoomRuntime } from './room_runtime_qa.mjs';
import { verifyRoomActivity } from './room_activity_qa.mjs';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const require=createRequire(import.meta.url);
const moduleArg=process.argv.indexOf('--playwright-module');
const {chromium}=require(moduleArg>=0 ? process.argv[moduleArg+1] : 'playwright');
const vendor=path.join(root,'companion','node_modules','three');
if(!fs.existsSync(path.join(vendor,'build','three.module.js')))throw Error('Run npm --prefix companion ci first.');
const output=path.join(root,'.room-qa.tmp');
fs.mkdirSync(output,{recursive:true});
assert.ok(fs.realpathSync(output).startsWith(fs.realpathSync(root)+path.sep),'QA output must stay inside this workspace');
const server=http.createServer((req,res)=>{
    let p;try{p=decodeURIComponent(new URL(req.url,'http://localhost').pathname);}catch{res.writeHead(400);res.end();return;}
    if(p==='/qa-silence.wav'||p==='/qa-broken.wav'){
        const data=Buffer.alloc(44+8000*2*8);
        data.write('RIFF');data.writeUInt32LE(data.length-8,4);data.write('WAVEfmt ',8);
        data.writeUInt32LE(16,16);data.writeUInt16LE(1,20);data.writeUInt16LE(1,22);
        data.writeUInt32LE(8000,24);data.writeUInt32LE(16000,28);data.writeUInt16LE(2,32);data.writeUInt16LE(16,34);
        data.write('data',36);data.writeUInt32LE(data.length-44,40);
        res.writeHead(200,{'Content-Type':'audio/wav'});res.end(p==='/qa-broken.wav'?Buffer.from('invalid QA media'):data);return;
    }
    let file;
    if(p.startsWith('/vendor/three/')){
        const candidate=path.resolve(vendor,p.slice('/vendor/three/'.length));
        if(candidate.startsWith(vendor+path.sep))file=candidate;
    }else if(p==='/tests/fixtures/room_visual.html'||p==='/face/index.html'||/^\/face\/(js\/[^/]+\.js|css\/[^/]+\.css|public\/assets\/room\/[^/]+\.png)$/.test(p))file=path.resolve(root,'.'+p);
    if(!file){res.writeHead(404);res.end();return;}
    fs.readFile(file,(error,data)=>{
        if(error){res.writeHead(404);res.end();return;}
        res.writeHead(200,{'Content-Type':{'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png'}[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store'});res.end(data);
    });
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
let browser;
try{
    browser=await chromium.launch({headless:true,args:['--mute-audio'],...(process.platform==='win32'?{channel:'msedge'}:{})});
    const page=await browser.newPage({viewport:{width:1440,height:960}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
    const origin='http://127.0.0.1:'+server.address().port;
    await page.route('**/*',r=>r.request().url().startsWith(origin+'/')?r.continue():r.abort());
    await page.goto(origin+'/tests/fixtures/room_visual.html');
    await page.waitForFunction(()=>window.roomQA);await page.waitForLoadState('networkidle');
    await page.evaluate(()=>document.querySelector('#qa-start').hidden=true);
    for(const theme of ['cyber','sakura','classic','hacker']){
        await page.evaluate(t=>roomQA.room.setTheme(t),theme);
        for(const view of ['iso','sofa','bed']){
            await page.evaluate(v=>roomQA.preset(v),view);
            await page.screenshot({path:path.join(output,`${theme}-${view}.jpg`),type:'jpeg',quality:80});
        }
    }
    assert.equal((await page.evaluate(()=>roomQA.verify())).ok,true);
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>{roomQA.room.setTheme('sakura');roomQA.preset('sofa');});
    await page.screenshot({path:path.join(output,'compact-sofa.jpg'),type:'jpeg',quality:80});
    const result=await page.evaluate(()=>roomQA.verify());assert.equal(result.ok,true);
    const media=await verifyRoomMedia(page,output);
    const atmosphere=await verifyRoomAtmosphere(page,output);
    const runtime=await verifyRoomRuntime(page);
    const activity=await verifyRoomActivity(page,output);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({status:'passed',...result,media,atmosphere,runtime,activity,output},null,2));
}finally{
    if(browser)await browser.close();
    await new Promise(resolve=>server.close(resolve));
}
