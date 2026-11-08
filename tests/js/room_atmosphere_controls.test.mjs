import test from 'node:test';
import assert from 'node:assert/strict';
import { RoomAtmosphere } from '../../face/js/room_atmosphere.js';
import { bindRoomAtmosphereControls } from '../../face/js/room_atmosphere_controls.js';

function view(getController) {
    const select = new EventTarget();
    Object.assign(select, {value:'',disabled:false,ownerDocument:{createElement:()=>({})},replaceChildren(...options){this.options=options;}});
    const note={textContent:''},root={querySelector:s=>s==='[data-room-atmosphere]'?select:note};
    const binding=bindRoomAtmosphereControls(root,getController);
    return {select,note,binding,choose(mode){select.value=mode;select.dispatchEvent(new Event('change'));}};
}
const create=()=>new RoomAtmosphere({storage:()=>null,reducedMotion:()=>true});

test('Quick Configuration waits for the lazily assigned room, then restores its selection',()=>{
    let room=null;const ui=view(()=>room);
    assert.equal(ui.select.disabled,true);assert.match(ui.note.textContent,/no disponible/);
    room=create();room.setMode('night');ui.binding.refresh();
    assert.equal(ui.select.disabled,false);assert.equal(ui.select.value,'night');
    assert.deepEqual(ui.select.options.map(o=>o.value),['original','work','relax','night']);
    ui.choose('work');assert.equal(room.mode,'work');
});

test('both controls remain synchronized and display storage failure honestly',()=>{
    const room=create(),a=view(()=>room),b=view(()=>room);
    a.choose('relax');assert.equal(b.select.value,'relax');
    assert.match(a.note.textContent,/Solo esta sesión/);assert.match(b.note.textContent,/Solo esta sesión/);
    b.choose('original');assert.equal(a.select.value,'original');
});

test('replacing a room unsubscribes the old controller and disposal removes the UI action',()=>{
    let room=create();const old=room,ui=view(()=>room);
    room=create();room.setMode('night');ui.binding.refresh();
    old.setMode('work');assert.equal(ui.select.value,'night');
    ui.binding.dispose();ui.choose('relax');assert.equal(room.mode,'night');
});

test('missing optional root is safe',()=>{
    const binding=bindRoomAtmosphereControls(null,()=>null);
    assert.doesNotThrow(()=>{binding.refresh();binding.dispose();});
});
