import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const source = readFileSync(new URL('../../face/js/hermes_activity.js',import.meta.url),'utf8');
const method = name => source.slice(source.indexOf(`    ${name}(`)).split('\n    }')[0]+'\n    }';
function fixture(choice = 'once') {
    const questions = [];
    const context = vm.createContext({AbortController,setTimeout,clearTimeout,
        OS_APPS:{get:()=>({id:'qa',name:'App QA'}),hasOwnCode:()=>true},
        appActionMessage:(line)=>line,
        irisConfirm: async options => {questions.push(options);return true;},
        irisChoice: async options => {questions.push(options);return choice;},
    });
    const owner = {};
    owner.handleAppIntent = vm.runInContext('('+method('async handleAppIntent').trim().replace('async handleAppIntent','async function handleAppIntent')+')',context);
    owner.cancelAppIntent = vm.runInContext('('+method('cancelAppIntent').trim().replace('cancelAppIntent','function cancelAppIntent')+')',context);
    const intent = {request_id:'one',app:'qa',action:'count',confirm_only:true,
        expires_at:Date.now()/1000+60,permission:{requires_confirmation:true,remember_scope:'Solo esta operación.'},operation:{label:'contar'}};
    return {owner,intent,questions,context};
}
test('only the explicit always choice sends the scoped remember receipt',async()=>{
    for(const choice of ['once','always',null]){
        const f=fixture(choice);const result=await f.owner.handleAppIntent(f.intent);
        assert.equal(result.status,choice===null?'denied':'ok');
        assert.equal(result.data.permission_remember===true,choice==='always');
        assert.match(f.questions[0].message,/Solo esta operación/);
        assert.equal(f.owner._appIntentControllers.size,0);
    }
});
test('Core closing a pending intent aborts its dialog and cannot authorize it later',async()=>{
    const f=fixture();let pending;
    f.context.irisChoice = options => new Promise(resolve=>{
        pending={options,resolve};options.signal.addEventListener('abort',()=>resolve(null),{once:true});
    });
    const run=f.owner.handleAppIntent(f.intent);
    f.owner.cancelAppIntent('one');
    assert.equal((await run).status,'denied');
    pending.resolve('always');
    assert.equal(f.owner._appIntentControllers.size,0);
});
