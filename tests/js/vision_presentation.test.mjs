import test from 'node:test';
import assert from 'node:assert/strict';
import { VisionPresentation } from '../../face/js/vision_presentation.js';
import { AnimationDirector } from '../../face/js/animation_director.js';
import { NotificationCenterApp, normalizeNotifications } from '../../face/js/notification_center.js';

const receipt = { id:'a'.repeat(32), kind:'returned', surface_id:'face',
    animation:'wave', created_at:999, expires_at:1007, delivery:'inbox', contains_media:false };
const options = () => ({ surfaceId:'face', vision:{enabled:true,paused_until:0},
    status:'idle', documentLike:{visibilityState:'visible',activeElement:{tagName:'BODY'}},
    idle:{isExplicitAnimationActive:()=>false}, director:{play:async()=>{}} });

test('one passive receipt runs once, without manifest reload', async()=>{
    let calls=0;
    const opts=options();
    opts.director.play=async(name,config)=>{calls++;assert.equal(name,'wave');assert.deepEqual(config,{reload:false});};
    const presenter=new VisionPresentation({now:()=>1000});
    assert.equal(await presenter.present(receipt,opts),true);
    assert.equal(await presenter.present(receipt,opts),false);
    assert.equal(calls,1);
});
test('hidden, paused, quiet, reduced motion, typing and busy avatars do not greet', async()=>{
    const variants=[
        {vision:{enabled:false}}, {vision:{enabled:true,paused_until:1001}},
        {status:'speaking'}, {status:'thinking'}, {status:'tool'}, {status:'unknown'},
        {quiet:true}, {reducedMotion:true}, {idle:{isExplicitAnimationActive:()=>true}},
        {documentLike:{visibilityState:'hidden'}},
        {documentLike:{visibilityState:'visible',activeElement:{tagName:'TEXTAREA'}}},
        {documentLike:{visibilityState:'visible',activeElement:{isContentEditable:true}}},
    ];
    for(const change of variants){
        const presenter=new VisionPresentation({now:()=>1000});
        const opts={...options(),...change,director:{play:()=>assert.fail('not permitted')}};
        assert.equal(await presenter.present(receipt,opts),false);
        assert.equal(await presenter.present(receipt,options()),false); // No deferred replay.
    }
});
test('wrong surface, expired, future or malformed receipt is ignored', async()=>{
    for(const invalid of [{...receipt,surface_id:'other'},{...receipt,expires_at:999},
        {...receipt,created_at:1001},{...receipt,expires_at:1100},{...receipt,contains_media:true},
        {...receipt,id:'bad'},{...receipt,animation:'execute'},{}]){
        const presenter=new VisionPresentation({now:()=>1000});
        assert.equal(await presenter.present(invalid,{...options(),
            director:{play:()=>assert.fail('invalid receipt')}}),false);
    }
});
test('failed gesture is not retried or reported as presented', async()=>{
    const presenter=new VisionPresentation({now:()=>1000});
    assert.equal(await presenter.present(receipt,{...options(),
        director:{play:async()=>{throw Error('missing avatar');}}}),false);
    assert.equal(await presenter.present(receipt,options()),false);
});
test('passive director skips async reload but preserves normal Hermes behavior', async()=>{
    let reloads=0, avatars=0;
    const idle={trigger:()=>false,playClipExternal:()=>false,reloadManifest:async()=>{reloads++;}};
    const director=new AnimationDirector({getIdle:()=>idle,
        getAvatarManager:()=>({playAnimation:()=>avatars++}),logger:{}});
    await director.play('wave',{reload:false});
    assert.equal(reloads,0);assert.equal(avatars,1);
    await director.play('wave');
    assert.equal(reloads,1);assert.equal(avatars,2);
});
const note = {schema:'iris.notification/v1',id:'b'.repeat(32),source:'vision',
    kind:'long_session',title:'Pausa',body:'Pendiente',unread:true,status:'active'};
const summary = {...note,id:receipt.id,kind:'returned',title:'Tu resumen de regreso',
    explanation:{observed_seconds:72,threshold_seconds:30,confidence:.9,related_ids:[note.id]}};
const root=()=>({innerHTML:'',addEventListener(){},querySelectorAll(){return [];}});
const click=dataset=>({target:{closest:()=>({dataset})}});
test('return control defaults off, confirms enable, and preserves revision', async()=>{
    const app=new NotificationCenterApp();
    app.state=normalizeNotifications({revision:4});
    assert.equal(app.state.preferences.returnSummary,false);
    const oldWindow=globalThis.window;
    let accept=false, request;
    globalThis.window={confirm:()=>accept};
    app._request=async(...args)=>{request=args;};
    try{
        await app._onClick(click({notifyAction:'enable_return'}));
        assert.equal(request,undefined);
        accept=true;await app._onClick(click({notifyAction:'enable_return'}));
        assert.deepEqual(request,['/preferences',{action:'enable_return',confirm:true,expected_revision:4}]);
        accept=false;await app._onClick(click({notifyAction:'disable_return'}));
        assert.equal(request[1].action,'disable_return');
    }finally{globalThis.window=oldWindow;}
});
test('summary shows absence evidence and links only its related inbox IDs', async()=>{
    const app=new NotificationCenterApp({root:root()});
    app.state=normalizeNotifications({items:[summary,note,{...note,id:'d'.repeat(32)}]});
    app.loaded=true;app.render();
    assert.ok(app.root.innerHTML.includes('Ausencia observada: 72 s'));
    assert.ok(app.root.innerHTML.includes('Ver avisos relacionados'));
    await app._onClick(click({notifyAction:'related',id:summary.id}));
    assert.equal(app.filter,'related');
    assert.ok(app.root.innerHTML.includes('data-id="'+note.id+'"'));
    assert.ok(!app.root.innerHTML.includes('data-id="'+summary.id+'"'));
    assert.ok(!app.root.innerHTML.includes('data-id="'+'d'.repeat(32)+'"'));
    const filtered=normalizeNotifications({items:[{...summary,explanation:{related_ids:['bad',note.id,note.id]}}]});
    assert.deepEqual(filtered.items[0].relatedIds,[note.id]);
});
