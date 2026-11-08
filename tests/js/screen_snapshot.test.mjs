import test from 'node:test';
import assert from 'node:assert/strict';
import { ScreenSnapshotRequests } from '../../face/js/screen_snapshot.js';
import { irisImageConfirm } from '../../face/js/iris_dialog.js';

const request = id => ({ request_id: id, destination: {provider:'test',model:'vision'}, timeout_s:5 });
const flush = async () => { for(let i=0;i<30;i++) await Promise.resolve(); };
function harness(overrides={}) {
    const sent=[], events=[], tracks=[{stop:()=>events.push('stop-video')},{stop:()=>events.push('stop-audio')}];
    const stream={getTracks:()=>tracks};
    const options={ send:r=>sent.push(r), doc:new EventTarget(),
        confirm:async()=>{events.push('consent');return true;},
        media:{getDisplayMedia:async constraints=>{assert.deepEqual(constraints,{video:true,audio:false});events.push('picker');return stream;}},
        capture:async()=>{events.push('frame');return {image:'data:image/jpeg;base64,AAAA'};},
        review:async()=>{assert.deepEqual(events,['consent','picker','frame','stop-video','stop-audio']);events.push('review');return true;},
        ...overrides};
    return {requests:new ScreenSnapshotRequests(options),sent,events,stream};
}
test('one image, tracks closed before review, one answer, duplicate request ignored',async()=>{
    const h=harness(); await h.requests.handle(request('snap-1'));
    assert.deepEqual(h.sent,[{request_id:'snap-1',status:'ok',confirmed:true,image:'data:image/jpeg;base64,AAAA'}]);
    await h.requests.handle(request('snap-1')); assert.equal(h.sent.length,1);
});
test('declining first asks no OS source; declining review sends no bytes',async()=>{
    for(const stage of ['confirm','review']) {
        const h=harness({[stage]:async()=>false}); await h.requests.handle(request(stage));
        assert.deepEqual(h.sent,[{request_id:stage,status:'cancelled'}]);
        if(stage==='confirm') assert.deepEqual(h.events,[]);
    }
});
test('late picker approval after Core cancellation stops every track and sends nothing',async()=>{
    let finish; const h=harness({media:{getDisplayMedia:()=>new Promise(r=>{finish=r;})}});
    const running=h.requests.handle(request('late')); await flush(); h.requests.cancel('late');
    await running; finish(h.stream); await flush();
    assert.deepEqual(h.events,['consent','stop-video','stop-audio']); assert.deepEqual(h.sent,[]);
});
test('cancel during review or behind another dialog returns without sending an image',async()=>{
    for(const stage of ['confirm','review']) {
        let finish; const h=harness({[stage]:()=>new Promise(r=>{finish=r;})});
        const running=h.requests.handle(request(stage)); await flush(); h.requests.cancel(stage);
        await running; finish(true); await flush(); assert.deepEqual(h.sent,[]);
    }
});
test('capture errors release all tracks; unsupported capture never reports success',async()=>{
    const h=harness({capture:async()=>{throw Error('failed');}});
    await h.requests.handle(request('bad')); assert.equal(h.sent[0].status,'unavailable');
    assert.ok(h.events.includes('stop-video')); assert.ok(h.events.includes('stop-audio'));
    const u=harness({media:{}}); await u.requests.handle(request('none'));
    assert.equal(u.sent[0].status,'unsupported');
});
test('no DOM can approve an image review or load a remote image',async()=>{
    assert.equal(await irisImageConfirm({image:'https://example.invalid/image.png'}),false);
    assert.equal(await irisImageConfirm({image:'data:image/jpeg;base64,AAAA'}),false);
});
test('Core cancelling a stalled frame closes its tracks promptly',async()=>{
    const h=harness({capture:()=>new Promise(()=>{})});
    const running=h.requests.handle(request('stalled'));await flush();h.requests.cancel('stalled');
    await running;assert.ok(h.events.includes('stop-video'));assert.deepEqual(h.sent,[]);
});
test('a capture Iris asks for names its question; under Permitir it skips only the review',async()=>{
    for(const review of [true,false]) {
        let options; const h=harness({confirm:async value=>{options=value;h.events.push('consent');return true;},
            review:async()=>{h.events.push('review');return true;}});
        await h.requests.handle({...request(`iris-${review}`),requested_by:'iris',review,question:'¿qué opinas de esto?'});
        assert.equal(options.title,'Iris pide ver tu pantalla');
        assert.match(options.message,/^Iris decidió que necesita ver tu pantalla para responder a «¿qué opinas de esto\?»/);
        assert.equal(/sin revisión, porque lo permitiste/.test(options.message),!review);
        assert.equal(h.events.includes('picker'),true);  // a click and the OS picker either way
        assert.equal(h.events.includes('review'),review);
        assert.deepEqual(h.sent,[{request_id:`iris-${review}`,status:'ok',confirmed:true,image:'data:image/jpeg;base64,AAAA'}]);
    }
});
test('a capture the user asked for is always reviewed, whatever the payload says',async()=>{
    const h=harness({review:async()=>{h.events.push('review');return true;}});
    await h.requests.handle({...request('own'),review:false});
    assert.ok(h.events.includes('review'));
});
