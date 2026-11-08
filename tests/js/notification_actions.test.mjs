import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareNotificationChatDraft } from '../../face/js/notification_chat_draft.js';
import { NotificationCenterApp, normalizeNotifications } from '../../face/js/notification_center.js';

const draft = { notification_id:'a'.repeat(32), text:'¿Me ayudas a organizar una pausa?',
    sent:false, contains_observation_data:false };
const note = { schema:'iris.notification/v1', id:draft.notification_id, source:'vision',
    kind:'long_session', title:'Pausa', body:'Aviso local.', unread:true, status:'active' };
const payload = { items:[note], revision:7, storage_available:true, retention_days:7 };
const root = () => ({ innerHTML:'', addEventListener(){}, querySelectorAll(){return [];} });

test('explicit draft enters composer but never sends or submits', () => {
    let opened=0, focused=0, resized=0;
    const panel = { chatInput:{value:'', focus(){focused++;}, dispatchEvent(){}},
        _autoResizeTextarea(){resized++;}, onSendMessage(){throw Error('automatic send');}, _attachments:[] };
    assert.equal(prepareNotificationChatDraft(panel, draft, ()=>opened++), true);
    assert.equal(panel.chatInput.value, draft.text);
    assert.equal(opened, 1); assert.equal(focused, 1); assert.equal(resized, 1);
});
test('existing text or attachments are never replaced or mixed', () => {
    for(const [value,files] of [['user draft',[]],[' ',[]],['',[{name:'private.pdf'}]]]){
        const panel={chatInput:{value}, _attachments:files};
        assert.throws(()=>prepareNotificationChatDraft(panel,draft,()=>assert.fail('must not open')));
        assert.equal(panel.chatInput.value,value);
        assert.equal(panel._attachments,files);
    }
});
test('invalid or failed draft handoff leaves the composer untouched', () => {
    const panel={chatInput:{value:''},_attachments:[]};
    assert.throws(()=>prepareNotificationChatDraft({...panel,_pendingAttachLoads:1},draft,()=>assert.fail('files pending')));
    assert.equal(panel.chatInput.value,'');
    for(const invalid of [{...draft,sent:true},{...draft,text:'/run command'},
        {...draft,contains_observation_data:true},{...draft,text:'x'.repeat(601)},{}]){
        assert.throws(()=>prepareNotificationChatDraft(panel,invalid,()=>{}));
        assert.equal(panel.chatInput.value,'');
    }
    assert.throws(()=>prepareNotificationChatDraft(panel,draft,()=>{throw Error('no panel');}));
    assert.equal(panel.chatInput.value,'');
});
test('snoozed and dismissed notes do not inflate active unread counts', () => {
    const state=normalizeNotifications({...payload,items:[note,
        {...note,id:'b'.repeat(32),status:'snoozed'},{...note,id:'c'.repeat(32),status:'dismissed'}]});
    assert.equal(state.unread,1);
    const app=new NotificationCenterApp({root:root()});
    app.state=state;app.loaded=true;app.filter='snoozed';app.render();
    assert.ok(app.root.innerHTML.includes('Mostrar ahora'));
    assert.ok(!app.root.innerHTML.includes('data-notify-action="conversation"'));
    app.filter='dismissed';app.render();
    assert.ok(app.root.innerHTML.includes('Restaurar aviso'));
    const focused=[];
    const summary=key=>({tagName:'SUMMARY',dataset:{},parentElement:{dataset:{detailKey:key}},
        closest:()=>({dataset:{id:note.id}}),focus:()=>focused.push(key)});
    const explanation=summary('evidence'), options=summary('actions');
    const keyboardRoot={...root(),ownerDocument:{activeElement:options},contains:()=>true,
        querySelectorAll:selector=>selector==='button, select, summary'?[explanation,options]:[]};
    new NotificationCenterApp({root:keyboardRoot});
    assert.deepEqual(focused,['actions']); // The other summary in the same card is not equivalent.
});
test('mutations carry the loaded revision and explicit choices', async () => {
    const app=new NotificationCenterApp();
    app.state=normalizeNotifications(payload);
    let request;
    app._request=async (...args)=>{request=args;};
    await app._mutation('/action',{id:note.id,action:'snooze',minutes:10});
    assert.deepEqual(request,['/action',{id:note.id,action:'snooze',minutes:10,confirm:true,expected_revision:7}]);
});
test('conversation response is only handed to the initiating composer once', async () => {
    const original=globalThis.fetch;
    let prepared=0, calls=0;
    globalThis.fetch=async()=>{calls++;return {ok:true,json:async()=>({draft})};};
    try{
        const app=new NotificationCenterApp({onPrepareConversation:value=>{
            assert.deepEqual(value,draft);prepared++;return true;
        }});
        app.state=normalizeNotifications(payload);
        await Promise.all([app._request('/conversation',{id:note.id,confirm:true}),
            app._request('/conversation',{id:note.id,confirm:true})]);
        assert.equal(calls,1);assert.equal(prepared,1);
        assert.equal(app.state.unread,1);assert.equal(app.state.revision,7);
    }finally{globalThis.fetch=original;}
});
test('failed handoff stays explicit without pretending a message was sent', async () => {
    const original=globalThis.fetch;
    globalThis.fetch=async()=>({ok:true,json:async()=>({draft})});
    try{
        const app=new NotificationCenterApp({onPrepareConversation:()=>{throw Error('Borrador existente');}});
        app.state=normalizeNotifications(payload);
        await app._request('/conversation',{id:note.id,confirm:true});
        assert.equal(app.error,'Borrador existente');
        assert.equal(app.state.unread,1);
    }finally{globalThis.fetch=original;}
});
test('one deadline refresh runs only while visible and never loops after failure', async () => {
    const oldFetch=globalThis.fetch, oldSet=globalThis.setTimeout, oldClear=globalThis.clearTimeout;
    let callback, scheduled=0, calls=0, visible=true;
    globalThis.setTimeout=(fn)=>{callback=fn;scheduled++;return 1;};
    globalThis.clearTimeout=()=>{};
    globalThis.fetch=async()=>{calls++;throw Error('offline');};
    try{
        const app=new NotificationCenterApp({isVisible:()=>visible});
        app.state=normalizeNotifications({...payload,server_now:1000,next_change_at:1060});
        app._scheduleRefresh();assert.equal(scheduled,1);
        visible=false;callback();assert.equal(calls,0);
        visible=true;app._scheduleRefresh();callback();
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(calls,1);assert.equal(scheduled,2);assert.equal(app.wakeTimer,null);
        app._scheduleRefresh();visible=false;callback();
        assert.equal(calls,1);
    }finally{
        globalThis.fetch=oldFetch;globalThis.setTimeout=oldSet;globalThis.clearTimeout=oldClear;
    }
});
