import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../../wizard/ui/wizard.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../../wizard/ui/index.html', import.meta.url), 'utf8');
const between = (a,b) => source.slice(source.indexOf(a), source.indexOf(b, source.indexOf(a)+a.length));
const routes = between('const FLOW_LABELS =', 'const VISUAL_THEMES');
const navigation = between('function updateUI()', '// ── Radio Card Selection');

function setup() {
    const calls=[];
    const nodes=new Map();
    const headings=new Map();
    const node=id => {
        if (!nodes.has(id)) nodes.set(id,{style:{},textContent:'',open:false});
        return nodes.get(id);
    };
    const steps=[...html.matchAll(/class="wizard-step(?: active)?" data-step="(\d+)"/g)].map(m=>({
        dataset:{step:m[1]},active:false,classList:{toggle(key,value){ steps.find(s=>s.dataset.step===m[1]).active=value; }},
    }));
    const ctx=vm.createContext({
        currentStep:0,wizardFlow:'first-run',editingSection:1,summaryReturn:false,displayedStep:null,hardwareProbe:null,
        wizardState:{},selectedHermesMode:null,wizardRuntime:{packaged:true},_summarySaving:false,
        document:{body:{dataset:{}},getElementById:node,
            querySelectorAll:()=>steps,
            querySelector:q=>{
                const active=steps.find(s=>s.active);
                if(q==='.wizard-step.active')return active;
                if(q==='.wizard-step.active h2'){
                    if(!headings.has(active.dataset.step))headings.set(active.dataset.step,{focus(){calls.push(['focus',active.dataset.step]);}});
                    return headings.get(active.dataset.step);
                }
                return null;
            }},
        window:{scrollTo:options=>calls.push(['scroll',options.top])},
        stopCloneRecording(){},applyHermesModeOnEntry(){calls.push(['entry']);},
        detectHardware:async()=>calls.push(['hardware']),checkHermesOld:async()=>calls.push(['status']),
        _enterProviderCatalogSection:async()=>{},_leaveProviderCatalogSection(){},loadLlmSetup:async()=>{},
        updateVoiceSelectorVisibility(){},refreshExtrasStatus:async()=>{},refreshAnimationSlots(){},
        checkModelPendingOnStepChange(){},showSummaryAndSave(){calls.push(['save']);},
        validateIdentityStep:()=>true,markModelStepDone(){},selectedLLMProvider:()=>'',selectedLLMModel:()=>'',
        commitInstallerTheme:async()=>{},setInstallerMode(mode){ctx.wizardFlow=mode;},setInstallerStatus(){},setInstallerModeLabel(){},_installerTool:()=>'',
        setTimeout(){throw new Error('Opening a section must not queue an engine action');},
    });
    vm.runInContext(routes+between('function activeFlowSteps()', 'function setInstallerMode(')+navigation,ctx);
    return {ctx,steps,node,calls};
}

test('the first-run route opens with the appearance and has six numbered sections; old audio/key screens cannot be entered',()=>{
    const {ctx,node,steps}=setup();
    ctx.startFirstInstall();
    const visited=[];
    for(let i=0;i<6;i++){
        visited.push(ctx.currentStep);
        assert.match(node('step-indicator').textContent,new RegExp(`Paso ${i+1} de 6`));
        assert.deepEqual(steps.filter(s=>s.active).map(s=>Number(s.dataset.step)),[ctx.currentStep]);
        ctx.nextStep();
    }
    assert.deepEqual(visited,[2,1,3,4,5,8]);
    assert.match(node('step-indicator').textContent,/Resumen/);
    assert.equal(ctx.currentStep,8);
    ctx.prevStep();assert.equal(ctx.currentStep,5);
    ctx.prevStep();assert.equal(ctx.currentStep,4);
    ctx.prevStep();assert.equal(ctx.currentStep,3);
    ctx.prevStep();assert.equal(ctx.currentStep,1);
    ctx.prevStep();assert.equal(ctx.currentStep,2);
    assert.match(node('step-indicator').textContent,/Paso 1 de 6 · Apariencia/);
    ctx.prevStep();assert.equal(ctx.currentStep,0);
    assert.equal(node('step-indicator').textContent,'Bienvenida');
});

// QA12: «primero seleccionar tema y ahí sí después esta ventana».
test('a first start chooses the appearance before the welcome, and the setup does not ask again',()=>{
    const {ctx,node}=setup();
    ctx.startThemeFirst();
    assert.equal(ctx.currentStep,2);
    assert.equal(node('step-indicator').textContent,'Antes de empezar · Apariencia');
    ctx.prevStep();assert.equal(ctx.currentStep,2,'nothing comes before it');
    ctx.nextStep();
    assert.equal(ctx.currentStep,0);
    assert.equal(ctx.wizardState.theme_chosen_first,true);
    ctx.startFirstInstall();
    const visited=[];
    for(let i=0;i<5;i++){ visited.push(ctx.currentStep); assert.match(node('step-indicator').textContent,new RegExp(`Paso ${i+1} de 5`)); ctx.nextStep(); }
    assert.deepEqual(visited,[1,3,4,5,8]);
    ctx.startBasicSetup();
    const basic=[];
    for(let i=0;i<4;i++){ basic.push(ctx.currentStep); ctx.nextStep(); }
    assert.deepEqual(basic,[1,4,5,8]);
    ctx.openWizardSection(2);assert.equal(ctx.currentStep,2,'the appearance can still be changed');
});

test('only a profile that never chose an edition starts on it',()=>{
    const init=source.slice(source.indexOf('async function initWizard()'));
    assert.match(init,/document\.body\.dataset\.configured !== 'true' && !config\.visual_theme_selected\s*&& !visualThemeTouched && currentStep === 0\) \{\s*startThemeFirst\(\);/);
});

test('the personalization route (no AI) also starts with the appearance',()=>{
    const {ctx}=setup();
    ctx.startBasicSetup();
    const visited=[];
    for(let i=0;i<5;i++){ visited.push(ctx.currentStep); ctx.nextStep(); }
    assert.deepEqual(visited,[2,1,4,5,8]);
});

test('the appearance step holds the real edition picker; the welcome no longer hides it in a fold',()=>{
    const step=html.slice(html.indexOf('data-step="2"'),html.indexOf('data-step="1"'));
    for(const theme of ['iris','rose','verdant','sunrise'])assert.match(step,new RegExp(`name="visual_theme" value="${theme}"`));
    assert.match(step,/id="edition-live-preview"/);assert.match(step,/id="edition-save-state"/);
    assert.match(step,/<h2 id="edition-step-title">/);
    assert.equal(html.match(/name="visual_theme"/g).length,4,'one picker only');
    assert.doesNotMatch(html,/Elegir la apariencia · opcional/);
    assert.match(html,/onclick="openWizardSection\(2\)"/);assert.match(html,/onclick="reviewWizardSection\(2\)"/);
});

for(const section of [1,2,3,4,5])test(`editing section ${section} goes directly to it, then summary and back`,()=>{
    const {ctx,node}=setup();
    ctx.openWizardSection(section);
    assert.equal(ctx.currentStep,section);
    assert.doesNotMatch(node('step-indicator').textContent,/Paso/);
    ctx.nextStep();assert.equal(ctx.currentStep,8);
    ctx.prevStep();assert.equal(ctx.currentStep,section);
    ctx.prevStep();assert.equal(ctx.currentStep,0);
});

test('a summary correction returns to summary rather than walking through unrelated sections',()=>{
    const {ctx}=setup();ctx.openWizardSection(4);ctx.nextStep();
    ctx.reviewWizardSection(3);assert.equal(ctx.currentStep,3);
    assert.equal(ctx.wizardState.hermes_mode,'full');
    ctx.nextStep();assert.equal(ctx.currentStep,8);
    ctx.reviewWizardSection(1);ctx.prevStep();assert.equal(ctx.currentStep,8);
});

test('a pending save cannot be left while it may still persist older form values',()=>{
    const {ctx}=setup();ctx.openWizardSection(4);ctx.nextStep();ctx._summarySaving=true;
    ctx.reviewWizardSection(1);assert.equal(ctx.currentStep,8);
    ctx.prevStep();assert.equal(ctx.currentStep,8);
    ctx._summarySaving=false;ctx.reviewWizardSection(1);assert.equal(ctx.currentStep,1);
});

test('new section focuses its heading and scrolls to top; refreshes do not steal focus',()=>{
    const {ctx,calls}=setup();ctx.startFirstInstall();
    assert.deepEqual(calls.slice(-2),[['focus','2'],['scroll',0]],'the appearance opens the setup');
    const before=calls.length;ctx.updateUI();assert.equal(calls.length,before);
    ctx.nextStep();assert.ok(calls.some(c=>c[0]==='focus'&&c[1]==='1'));
    ctx.nextStep();assert.ok(calls.some(c=>c[0]==='focus'&&c[1]==='3'));
});

test('entering IA or diagnostics only reads status, without start/restart/install timers',async()=>{
    const {ctx,calls,node}=setup();ctx.startRepairFlow();
    assert.equal(ctx.currentStep,3);assert.equal(node('setup-diagnostics').open,true);
    await ctx.hardwareProbe;
    assert.deepEqual(calls.filter(c=>['hardware','status'].includes(c[0])),[['hardware'],['status']]);
    ctx.prevStep();ctx.openWizardSection(3);await ctx.hardwareProbe;
    assert.equal(calls.filter(c=>c[0]==='hardware').length,1);
});

test('mode entry itself never imports or writes Hermes settings',()=>{
    let imported=0;
    const ctx=vm.createContext({wizardState:{hermes_mode:'repair'},wizardRuntime:{packaged:true},
        maybeImportHermesConfig(){imported++;},document:{getElementById:()=>({innerHTML:''})}});
    vm.runInContext(between('function applyHermesModeOnEntry()', '/**\n * Run the same /api/hermes-status'),ctx);
    ctx.applyHermesModeOnEntry();assert.equal(imported,0);
});

test('selecting an Edge voice updates the keyboard control without playing it',()=>{
    const cards=['es-MX-DaliaNeural','es-MX-JorgeNeural'].map(id=>({dataset:{voiceId:id},pressed:null,selected:false,
        classList:{remove(){cards.find(c=>c.dataset.voiceId===id).selected=false;},add(){cards.find(c=>c.dataset.voiceId===id).selected=true;}},
        querySelector:()=>({setAttribute(key,value){cards.find(c=>c.dataset.voiceId===id).pressed=value;}})}));
    const nodes={};const node=id=>nodes[id]??=( {style:{},textContent:''} );
    const ctx=vm.createContext({selectedVoice:'es-MX-DaliaNeural',wizardState:{},document:{getElementById:node,querySelectorAll:()=>cards},
        fetch(){throw Error('Selection is not a preview');},escapeHtml:String});
    vm.runInContext(between('function selectVoice(', 'let _voicePreviewGeneration'),ctx);
    ctx.selectVoice('es-MX-JorgeNeural','Jorge');
    assert.equal(ctx.selectedVoice,'es-MX-JorgeNeural');
    assert.equal(cards[1].pressed,'true');assert.equal(cards[0].pressed,'false');
    assert.equal(node('voice-selected-name').textContent,'Jorge');
    assert.equal(node('voice-current-choice').textContent,'Voz elegida: Jorge (México)');
});

// 2026-10-19 review: offline, /api/voices answers 200 {voices:[], error} and the list said
// «No hay voces para este filtro», as if the filter were wrong.
test('voices that cannot be listed say why, offer a retry and never show the raw error',async()=>{
    for(const [reply,expected] of [
        [{voices:[],error:'Cannot connect to host 127.0.0.1:9 ssl:<ssl.SSLContext object>'},/Necesita conexión a internet/],
        [{voices:[],error:'edge-tts not installed'},/no está instalado/],
        [null,/Necesita conexión a internet/],
    ]){
        const list={innerHTML:''};
        const ctx=vm.createContext({AbortController,_voiceListGeneration:0,_voiceListController:null,allVoices:[],
            document:{getElementById:id=>id==='voice-list'?list:null},
            fetch:async()=>{if(!reply)throw new TypeError('Failed to fetch');return{json:async()=>reply};},
            renderVoices(){list.innerHTML='RENDERED';}});
        vm.runInContext(between('async function loadVoices()', 'function filterVoices()'),ctx);
        await ctx.loadVoices();
        assert.match(list.innerHTML,expected);
        assert.match(list.innerHTML,/onclick="loadVoices\(\)">Reintentar</);
        assert.match(list.innerHTML,/Voces locales/);
        assert.doesNotMatch(list.innerHTML,/No hay voces para este filtro|SSLContext|Cannot connect|RENDERED/);
    }
    // A real empty filter still renders the list (renderVoices owns that message).
    const list={innerHTML:''};
    const ctx=vm.createContext({AbortController,_voiceListGeneration:0,_voiceListController:null,allVoices:[],
        document:{getElementById:()=>list},fetch:async()=>({json:async()=>({voices:[],count:0})}),
        renderVoices(){list.innerHTML='RENDERED';}});
    vm.runInContext(between('async function loadVoices()', 'function filterVoices()'),ctx);
    await ctx.loadVoices();
    assert.equal(list.innerHTML,'RENDERED');
});

test('voice render offers separate native choose/listen buttons with names',()=>{
    const list={innerHTML:''};
    const ctx=vm.createContext({selectedVoice:'es-MX-JorgeNeural',document:{getElementById:()=>list},escapeHtml:String});
    vm.runInContext(between('function renderVoices(', 'function selectVoice('),ctx);
    ctx.renderVoices([{id:'es-MX-JorgeNeural',lang:'es-MX',name:'Jorge',gender:'Male'}]);
    assert.match(list.innerHTML,/<button type="button" class="voice-select voice-info" aria-pressed="true"/);
    assert.match(list.innerHTML,/aria-label="Elegir Jorge · es-MX"/);
    assert.match(list.innerHTML,/aria-label="Escuchar Jorge · es-MX"/);
});

test('entering IA populates the real form once, preserving edits on return',async()=>{
    const section={dataset:{}};let active=true;const calls=[];
    const ctx=vm.createContext({loadProvidersFromBackend:async()=>calls.push('catalog'),
        _isProviderCatalogSectionActive:()=>active,document:{getElementById:()=>section},
        _originalCheckHermes:async()=>calls.push('hydrate'),_maybeAutoRefreshProviderCatalog(){},
        _latestProviderCatalogPayload:{},console});
    vm.runInContext(between('async function _enterProviderCatalogSection()', 'function _leaveProviderCatalogSection()'),ctx);
    await ctx._enterProviderCatalogSection();await ctx._enterProviderCatalogSection();
    assert.deepEqual(calls,['catalog','hydrate','catalog']);
    active=false;await ctx._enterProviderCatalogSection();
    assert.equal(calls.filter(x=>x==='hydrate').length,1);
});

for(const provider of ['elevenlabs','minimax','fish-audio'])test(`preview uses only the ${provider} credential beside its voice`,async()=>{
    let sent;
    const nodes={
        'tts-test-result':{style:{},innerHTML:'',querySelector:()=>null},'tts-test-text':{value:'Frase inventada'},
        'key-elevenlabs':{value:'fake-eleven-key'},'key-minimax':{value:'fake-minimax-key'},
        'key-elevenlabs-voice':{value:'eleven-voice'},'key-minimax-voice':{value:'mini-voice'},
        'key-fish-audio':{value:'fake-fish-key'},'key-fish-audio-voice':{value:'fish-voice'},'fish-audio-model':{value:'s2.1-pro-free'},
    };
    const ctx=vm.createContext({document:{getElementById:id=>nodes[id]},wizardState:{},
        _ttsPreviewGeneration:0,_ttsPreviewController:null,_ttsPreviewBlobUrl:'',
        AbortController,escapeHtml:String,console,
        fetch:async(url,options)=>{sent={url,...options};return {headers:{get:()=> 'application/json'},json:async()=>({message:'Test without audio'})};},
    });
    vm.runInContext(between('async function testTTS(', 'function _escapeHtml('),ctx);
    await ctx.testTTS(provider);
    assert.equal(sent.url,'/api/test-tts-audio');
    assert.equal(sent.headers['X-IRIS-TTS-Key'],{'elevenlabs':'fake-eleven-key','minimax':'fake-minimax-key','fish-audio':'fake-fish-key'}[provider]);
    assert.equal(sent.headers['X-IRIS-Intent'],'preview-tts');
    assert.ok(!sent.url.includes('key'));
    assert.equal(JSON.parse(sent.body).voice,{'elevenlabs':'eleven-voice','minimax':'mini-voice','fish-audio':'fish-voice'}[provider]);
    if(provider==='fish-audio')assert.equal(JSON.parse(sent.body).model,'s2.1-pro-free');
});

test('voice engine selection is exclusive across collapsed groups and the recommended card',()=>{
    const cards=['edge-tts','kokoro','elevenlabs','hf-s2s'].map((value,i)=>{
        const listeners={};
        const input={name:'tts',value,checked:i===0};
        const card={listeners,input,tabIndex:-1,selected:i===0,attrs:{},
            querySelector:q=>q.includes('input')?input:{id:`title-${i}`},
            setAttribute(key,value){this.attrs[key]=value;},
            addEventListener(type,fn){listeners[type]=fn;},
            // The recommended card deliberately has no .radio-group ancestor.
            closest:()=>null,
            classList:{toggle(name,checked){card.selected=checked;},add(){},remove(){}},
        };
        return card;
    });
    const ctx=vm.createContext({document:{querySelectorAll:()=>cards},wizardState:{},
        stopCloneRecording(){},updateVoiceSelectorVisibility(){},console});
    vm.runInContext(between("document.querySelectorAll('.radio-card').forEach((card, index)", 'const PERSONALITY_GUIDE'),ctx);
    for(const card of [cards[1],cards[2],cards[0],cards[3]]){
        card.listeners.click({target:{closest:()=>null}});
        assert.equal(cards.filter(c=>c.selected).length,1);
        assert.equal(cards.filter(c=>c.input.checked).length,1);
        assert.equal(card.attrs['aria-checked'],'true');
        assert.equal(ctx.wizardState.tts_provider,card.input.value);
    }
});


test('leaving the voice step retires Fish samples, but refreshing that step does not',()=>{
    const {ctx}=setup();
    ctx.openWizardSection(4);
    let stopped=0;
    ctx._stopFishAudioSample=()=>{stopped++;};
    ctx.updateUI();
    assert.equal(stopped,0);
    ctx.nextStep();
    assert.equal(stopped,1,'the summary must not retain a playing or pending Fish sample');
    ctx.openWizardSection(4);
    assert.equal(stopped,1);
    ctx.prevStep();
    assert.equal(stopped,2,'returning to welcome also stops the sample');
});
