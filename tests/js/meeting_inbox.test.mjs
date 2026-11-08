import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeInbox, calendarOpenPayload, sourceProblems } from '../../face/js/inbox_state.js';

const ID='a'.repeat(32), EVENT='event_'+'b'.repeat(32);
const job={schema:'iris.inbox-item/v1',key:`meeting:default:${ID}`,origin:'meeting',profile:'default',source_id:ID,
    source_revision:2,title:'Preparación: reunión',text:'Puntos y preguntas guardados.',outcome:'completed',
    calendar_event_id:EVENT,calendar_date:'2026-10-17',created_at:100,updated_at:101,
    delivery:{status:'pending',revision:2},actions:['read','dismiss']};
test('F15 result stays separate from deterministic reminders, with a safe Calendar destination',()=>{
    const data=normalizeInbox({items:[job]}); assert.equal(data.items.length,1);assert.equal(data.unread,1);
    assert.deepEqual(calendarOpenPayload(data.items[0]),{event_id:EVENT,date:'2026-10-17'});
    assert.deepEqual(data.items[0].actions,['read','dismiss']); assert.equal(data.items[0].joinUrl,'');
});
test('F15 cannot invent a join URL or a remote destination, and invalid result states are rejected',()=>{
    for(const patch of [{join_url:'https://meet.google.com/a'}, {calendar_event_id:'bad'}, {calendar_date:'2026-02-30'}, {outcome:'notice'}]){
        assert.equal(normalizeInbox({items:[{...job,...patch}]}).items.length,0);
    }
});
test('F15 unavailable source is reported without hiding a readable failed preparation',()=>{
    const data=normalizeInbox({items:[{...job,outcome:'failed'}],sources:{meeting:{status:'unavailable',message:'No se pudo leer preparación'}}});
    assert.equal(data.items[0].outcome,'failed');assert.equal(data.unread,1);assert.match(sourceProblems(data).join(),/preparación/);
});
