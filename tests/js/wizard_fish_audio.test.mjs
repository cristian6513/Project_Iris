import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../../wizard/ui/wizard.js', import.meta.url), 'utf8');
const between = (a,b) => source.slice(source.indexOf(a), source.indexOf(b, source.indexOf(a)+a.length));

function setup(fetch) {
    const nodes = new Map();
    const node = id => {
        if (!nodes.has(id)) nodes.set(id, { value:'', checked:false, disabled:false, textContent:'', style:{}, dataset:{}, children:[],
            querySelector(selector){return selector === 'audio' ? this.children.flatMap(child=>child.children||[]).find(child=>child.tag==='audio') || null : null;},
            replaceChildren(...children){this.children=children;}, append(...children){this.children.push(...children);}, appendChild(child){this.children.push(child);} });
        return nodes.get(id);
    };
    const blobs=[], revoked=[];
    let provider = 'fish-audio';
    const ctx = vm.createContext({fetch, AbortController, selectedVoice:'es-MX-DaliaNeural', wizardState:{}, URL:{createObjectURL(blob){blobs.push(blob);return `blob:${blobs.length}`;},revokeObjectURL(url){revoked.push(url);}},
        document:{getElementById:node, createElement:tag=>({tag,value:'',textContent:'',style:{},paused:true,pauseCalls:0,
            play(){this.paused=false;this.events?.play?.();return Promise.resolve();},pause(){this.paused=true;this.pauseCalls++;this.events?.pause?.();},
            setAttribute(name,value){this[name]=value;},addEventListener(name,fn){(this.events ||= {})[name]=fn;},append(...children){this.children=children;},removeAttribute(name){delete this[name];},load(){this.loaded=true;}}),querySelector:()=>({value:provider})}});
    vm.runInContext(between('let _ttsPreviewBlobUrl =', 'async function testTTS('),ctx);
    vm.runInContext(between('function prepareApiKeysStep()', 'function toggleKeyVisibility('),ctx);
    ctx.Audio = function(url) { const audio = ctx.document.createElement('audio'); audio.src = url; return audio; };
    ctx._escapeHtml = value => String(value);
    ctx.escapeHtml = value => String(value);
    vm.runInContext(between('async function testTTS(', 'function _escapeHtml('), ctx);
    return {ctx,node,blobs,revoked,setProvider(value){provider=value;ctx.prepareApiKeysStep();}};
}

const reply = data => ({ok:true,json:async()=>data});
const audioReply = bytes => ({ok:true,headers:{get:()=>'audio/mpeg'},blob:async()=>bytes});

test('generated voice uses the themed accessible player, replays its cache and stops when reset', async () => {
    let requests = 0;
    const w = setup(async () => { requests++; return audioReply({ size: 24000 }); });
    w.node('key-fish-audio-voice').value = 'voice-one';
    await w.ctx.testTTS('fish-audio');
    const player = w.node('tts-test-result').children[0];
    assert.equal(player.className, 'iris-audio-player');
    const audio = player.children.find(node => node.tag === 'audio');
    assert.equal(audio.hidden, true);
    assert.equal(player.children[0]['aria-label'], 'Pausar muestra');
    await w.ctx.testTTS('fish-audio');
    assert.equal(requests, 1);
    w.ctx.resetTTSPreview();
    assert.equal(audio.pauseCalls, 1);
    assert.equal(w.node('tts-test-result').children.length, 0);
});

test('Fish Audio opens its own credentials without contacting a service',()=>{
    let calls=0;
    const {ctx,node}=setup(()=>{calls++;throw Error('not a user action');});
    ctx.prepareApiKeysStep();
    assert.equal(node('fish-audio-key-section').style.display,'flex');
    assert.equal(node('elevenlabs-key-section').style.display,'none');
    assert.equal(node('minimax-key-section').style.display,'none');
    assert.equal(calls,0);
});

test('search uses only the Fish credential, treats names as text, and selection generates no audio',async()=>{
    const calls=[];
    const {ctx,node}=setup(async(url,options)=>{calls.push({url,options});return{ok:true,json:async()=>({voices:[{id:'one',name:'<img src=x onerror=bad()>'}]})};});
    node('key-fish-audio').value='fish-secret';node('key-elevenlabs').value='other-key';
    node('fish-audio-query').value='hola';node('fish-audio-self').checked=true;
    await ctx.searchFishAudioVoices();
    assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/fish-audio/voices');
    assert.equal(calls[0].options.headers['X-IRIS-TTS-Key'],'fish-secret');
    assert.deepEqual(JSON.parse(calls[0].options.body),{query:'hola',self_only:true,language:'',page:1});
    const option=node('fish-audio-voices').children[1];
    assert.equal(option.textContent,'<img src=x onerror=bad()>');assert.equal(option.innerHTML,undefined);
    node('fish-audio-voices').value='one';ctx.chooseFishAudioVoice();
    assert.equal(node('key-fish-audio-voice').value,'one');assert.equal(calls.length,1);
    assert.equal(node('key-fish-audio-voice').dataset.voiceName,'<img src=x onerror=bad()>');
    assert.equal(node('fish-audio-voice-name').textContent,'Voz elegida: <img src=x onerror=bad()>');
    assert.equal(node('voice-save-status').textContent,'Cambios de voz sin guardar.');
});

test('the chosen language travels with the search and each voice shows its languages and likes',async()=>{
    const calls=[];
    const {ctx,node}=setup(async(url,options)=>{calls.push(JSON.parse(options.body));
        return reply({voices:[{id:'v',name:'Valentino',languages:['es','en','pt','fr'],likes:12045}],has_more:false});});
    node('key-fish-audio').value='k';node('fish-audio-language').value='es';
    await ctx.searchFishAudioVoices();
    assert.equal(calls[0].language,'es');
    assert.equal(node('fish-audio-voices').children[1].textContent,'Valentino · es/en/pt · 12 mil ♥');
    assert.equal(node('fish-audio-more').style.display,'none');
});

test('«Ver más voces» appends the next page once and hides when Fish has no more',async()=>{
    const pages=[reply({voices:[{id:'a',name:'A'}],has_more:true}),reply({voices:[{id:'a',name:'A'},{id:'b',name:'B'}],has_more:false})];
    const bodies=[];
    const {ctx,node}=setup(async(url,options)=>{bodies.push(JSON.parse(options.body));return pages.shift();});
    node('key-fish-audio').value='k';
    await ctx.searchFishAudioVoices();
    assert.equal(node('fish-audio-more').style.display,'');
    await ctx.searchFishAudioVoices({more:true});
    assert.deepEqual(bodies.map(body=>body.page),[1,2]);
    assert.deepEqual(node('fish-audio-voices').children.map(option=>option.value),['','a','b']);
    assert.equal(node('fish-audio-more').style.display,'none');
});

test('a failed search leaves no results from the previous one',async()=>{
    const replies=[reply({voices:[{id:'old',name:'Old'}]}),{ok:false,json:async()=>({message:'La API key de Fish Audio no es válida o caducó.'})}];
    const {ctx,node}=setup(async()=>replies.shift());
    node('key-fish-audio').value='k';
    await ctx.searchFishAudioVoices();
    await ctx.searchFishAudioVoices();
    assert.deepEqual(node('fish-audio-voices').children,[]);
    assert.equal(node('fish-audio-voices').disabled,true);
    assert.equal(node('fish-audio-search-status').textContent,'La API key de Fish Audio no es válida o caducó.');
});

test('the free sample comes from the wizard, not a synthesis, and plays the published audio',async()=>{
    const calls=[];
    const {ctx,node,blobs}=setup(async(url,options)=>{calls.push({url,options});
        return url==='/api/fish-audio/sample'?audioReply('mp3-bytes'):reply({voices:[{id:'v',name:'V',has_sample:true}]});});
    node('key-fish-audio').value='fish-secret';
    await ctx.searchFishAudioVoices();
    node('fish-audio-voices').value='v';ctx.chooseFishAudioVoice();
    assert.equal(node('fish-audio-sample').disabled,false);
    await ctx.playFishAudioSample();
    const sample=calls[1];
    assert.equal(sample.url,'/api/fish-audio/sample');
    assert.deepEqual(JSON.parse(sample.options.body),{voice_id:'v'});
    assert.equal(sample.options.headers['X-IRIS-TTS-Key'],'fish-secret');
    assert.equal(calls.some(call=>call.url==='/api/test-tts-audio'),false);
    const audio=node('fish-audio-sample-player').children[0].children.at(-1);
    assert.equal(audio.tag,'audio');assert.equal(audio.src,'blob:1');assert.deepEqual(blobs,['mp3-bytes']);
});

test('a voice Fish published no sample for cannot request one, and a pasted id has no name',async()=>{
    const {ctx,node}=setup(async()=>reply({voices:[{id:'quiet',name:'Quiet',has_sample:false}]}));
    node('key-fish-audio').value='k';
    await ctx.searchFishAudioVoices();
    node('fish-audio-voices').value='quiet';ctx.chooseFishAudioVoice();
    assert.equal(node('fish-audio-sample').disabled,true);
    assert.equal(node('fish-audio-voice-name').textContent,'Voz elegida: Quiet · sin muestra publicada');
    node('key-fish-audio-voice').value='pasted-id';ctx.fishAudioVoiceTyped();
    assert.equal(node('key-fish-audio-voice').dataset.voiceName,'');
    assert.equal(node('fish-audio-voice-name').style.display,'none');
    assert.equal(node('fish-audio-sample').disabled,false);
});

test('a sample error is shown, and a late sample for a previous voice is discarded',async()=>{
    let finish;
    const replies=[{ok:false,headers:{get:()=>'application/json'},json:async()=>({message:'Esta voz no tiene una muestra publicada en Fish.'})},
        new Promise(resolve=>{finish=resolve;})];
    const {ctx,node,revoked}=setup(()=>replies.shift());
    node('key-fish-audio-voice').value='one';
    await ctx.playFishAudioSample();
    assert.equal(node('fish-audio-sample-player').textContent,'Esta voz no tiene una muestra publicada en Fish.');
    const late=ctx.playFishAudioSample();
    node('key-fish-audio-voice').value='two';ctx.fishAudioVoiceTyped();
    finish(audioReply('old-voice'));await late;
    assert.deepEqual(node('fish-audio-sample-player').children,[]);
    assert.equal(node('fish-audio-sample-player').style.display,'none');
    assert.deepEqual(revoked,[]);
});

test('a catalog from the previous account cannot replace a newer result',async()=>{
    const replies=[];
    const {ctx,node}=setup(()=>new Promise(resolve=>replies.push(resolve)));
    node('key-fish-audio').value='first';const first=ctx.searchFishAudioVoices();
    node('key-fish-audio').value='second';ctx.resetFishAudioAccount();
    const second=ctx.searchFishAudioVoices();
    replies[1]({ok:true,json:async()=>({voices:[{id:'second',name:'Second account'}]})});await second;
    replies[0]({ok:true,json:async()=>({voices:[{id:'first',name:'Old account'}]})});await first;
    assert.equal(node('fish-audio-voices').children[1].value,'second');
    assert.equal(node('fish-audio-search').disabled,false);
});

test('catalog errors stay visible and permit a retry',async()=>{
    const {ctx,node}=setup(async()=>({ok:false,json:async()=>({message:'Introduce tu API key'})}));
    await ctx.searchFishAudioVoices();
    assert.equal(node('fish-audio-search-status').textContent,'Introduce tu API key');
    assert.equal(node('fish-audio-search').disabled,false);
});


for (const [field, change] of [
    ['language', node => { node('fish-audio-language').value='en'; }],
    ['name', node => { node('fish-audio-query').value='narration'; }],
    ['own voices', node => { node('fish-audio-self').checked=true; }],
]) test('«Ver más» restarts on page 1 when the '+field+' filter changes', async()=>{
    const bodies=[];
    const {ctx,node}=setup(async(url,options)=>{
        bodies.push(JSON.parse(options.body));
        return reply({voices:[{id:bodies.length===1?'old':'new',name:'Voice'}],has_more:true});
    });
    node('fish-audio-language').value='es';
    await ctx.searchFishAudioVoices();
    change(node);
    await ctx.searchFishAudioVoices({more:true});
    assert.deepEqual(bodies.map(body=>body.page),[1,1]);
    assert.deepEqual(node('fish-audio-voices').children.map(option=>option.value),['','new']);
});

test('«Ver más» without a completed search starts at page 1',async()=>{
    const bodies=[];
    const {ctx}=setup(async(url,options)=>{bodies.push(JSON.parse(options.body));return reply({voices:[],has_more:false});});
    await ctx.searchFishAudioVoices({more:true});
    assert.equal(bodies[0].page,1);
});

test('a response for filters edited while waiting cannot display an obsolete catalog',async()=>{
    let finish;
    const {ctx,node}=setup(()=>new Promise(resolve=>{finish=resolve;}));
    node('fish-audio-language').value='es';
    const pending=ctx.searchFishAudioVoices();
    node('fish-audio-language').value='en';
    finish(reply({voices:[{id:'old-es',name:'Spanish'}],has_more:true}));
    await pending;
    assert.deepEqual(node('fish-audio-voices').children,[]);
    assert.equal(node('fish-audio-search').disabled,false);
});

test('switching away from Fish pauses audio, releases its URL and clears its controls',async()=>{
    const {ctx,node,revoked,setProvider}=setup(async()=>audioReply('sample'));
    node('key-fish-audio-voice').value='voice';
    await ctx.playFishAudioSample();
    const audio=node('fish-audio-sample-player').children[0].children.at(-1);
    assert.equal(audio.paused,false);
    ctx.prepareApiKeysStep();
    assert.equal(audio.paused,false,'refreshing Fish itself does not stop its sample');
    setProvider('minimax');
    assert.equal(audio.paused,true);
    assert.equal(audio.pauseCalls,1);
    assert.deepEqual(revoked,['blob:1']);
    assert.deepEqual(node('fish-audio-sample-player').children,[]);
    assert.equal(node('fish-audio-key-section').style.display,'none');
});

for (const phase of ['response','blob']) test('switching providers during the sample '+phase+' aborts and discards it',async()=>{
    let finish;
    let signal;
    const pending=new Promise(resolve=>{finish=resolve;});
    const {ctx,node,blobs,setProvider}=setup(async(url,options)=>{
        signal=options.signal;
        return phase==='response'?pending:{ok:true,headers:{get:()=>'audio/mpeg'},blob:()=>pending};
    });
    node('key-fish-audio-voice').value='voice';
    const playing=ctx.playFishAudioSample();
    await Promise.resolve();
    await Promise.resolve();
    setProvider('elevenlabs');
    assert.equal(signal?.aborted,true);
    assert.equal(node('fish-audio-sample').disabled,false,'a cancelled request must not strand the button');
    finish(phase==='response'?audioReply('old sample'):'old sample');
    await playing;
    assert.deepEqual(blobs,[]);
    assert.deepEqual(node('fish-audio-sample-player').children,[]);
    assert.equal(node('fish-audio-sample-player').style.display,'none');
});


test('voice save only submits the active provider and never the other credentials',async()=>{
    const calls=[];
    const {ctx,node}=setup(async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({status:'saved',message:'Voz guardada'})};});
    node('key-fish-audio').value='fish-only';node('key-fish-audio-voice').value='voice-id';node('fish-audio-model').value='s2.1-pro-free';
    node('key-elevenlabs').value='never-submit';node('key-minimax').value='never-submit';
    await ctx.saveVoiceSelection();
    assert.equal(calls[0].url,'/api/save-voice-selection');
    const payload=JSON.parse(calls[0].options.body);
    assert.equal(payload.tts_provider,'fish-audio');assert.equal(payload.fish_audio_api_key,'fish-only');
    assert.equal(calls[0].options.body.includes('never-submit'),false);
    assert.equal('assistant_name' in payload,false);assert.equal('llm_provider' in payload,false);
    assert.equal(node('voice-save-status').textContent,'Voz guardada');assert.equal(node('save-voice-selection').disabled,false);
});

test('voice save refusal stays visible and allows fixing the same inputs',async()=>{
    const {ctx,node}=setup(async()=>({ok:false,json:async()=>({message:'Falta elegir la voz'})}));
    node('key-fish-audio').value='keep-input';
    await ctx.saveVoiceSelection();
    assert.equal(node('voice-save-status').textContent,'Falta elegir la voz');
    assert.equal(node('save-voice-selection').disabled,false);assert.equal(node('key-fish-audio').value,'keep-input');
});

test('sample controls can pause and seek with accessible labels',async()=>{
    const {ctx,node}=setup(async()=>audioReply('sample'));
    node('key-fish-audio-voice').value='v';await ctx.playFishAudioSample();
    const [toggle,seek,time,audio]=node('fish-audio-sample-player').children[0].children;
    assert.equal(audio.hidden,true);assert.notEqual(audio.controls,true);
    audio.duration=120;audio.currentTime=30;audio.events.loadedmetadata();
    assert.equal(seek.disabled,false);assert.equal(seek.value,'250');
    assert.equal(time.textContent,'0:30 / 2:00');assert.ok(seek['aria-label']);
    toggle.events.click();audio.events.pause();assert.equal(audio.paused,true);
    assert.equal(toggle['aria-label'],'Reproducir muestra');
    seek.value='500';seek.events.input();assert.equal(audio.currentTime,60);
});
