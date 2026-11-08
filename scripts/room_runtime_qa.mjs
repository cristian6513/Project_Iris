import assert from 'node:assert/strict';

export async function verifyRoomRuntime(page) {
    const result=await page.evaluate(async()=>{
        const {room,scene,THREE,IdleSystem,SceneActionManager}=roomQA;
        // Import only the relevant real methods, not the app constructor.
        async function method(file,name,deps={}) {
            const source=await (await fetch('/face/js/'+file)).text();
            const block=source.match(new RegExp('^    '+name+'\\([^]*?^    \\}','m'))?.[0];
            if(!block)throw Error('Missing '+name);
            return new Function(...Object.keys(deps),'return ({'+block.trim()+'})')(...Object.values(deps))[name];
        }
        const exit=await method('app.js','_toggleInspectorMode');
        const apply=await method('hermes_activity.js','_applyMonitorMaterials',{
            STATE_META:{idle:{glow:0x123456},thinking:{glow:0xabcdef}},
        });
        room.atmosphere.setMode('night',{immediate:true});
        const activity={room,state:'thinking',_monitorMeshes:room.getObject('monitor_screens')};
        apply.call(activity);
        const night=room.getObject('monitor_glow').intensity===1.28*0.3;
        activity.state='idle';apply.call(activity);
        const idleNight=room.getObject('monitor_glow').intensity===0.72*0.3;
        room.atmosphere.setMode('original',{immediate:true});
        const original=room.getObject('monitor_glow').intensity===0.72;

        const group=new THREE.Group();group.position.set(0,0.035,0);
        const avatar={avatar:{group,restYaw:Math.PI}};
        const idle=new IdleSystem(avatar);
        const actions=new SceneActionManager(scene,avatar,room,{}, {idleSystem:idle});
        const app={inspectorMode:true,camera:new THREE.PerspectiveCamera(),sceneActions:actions,
            idle,avatarManager:avatar,speechAnimator:{_talking:false},
            _setInspectPresetHudVisible(){},_setActiveInspectPresetButton(){},_setActiveRoomActionButton(){}};
        actions.execute('lookOutWindow');
        actions.update(0.5,0);
        if(group.position.z===0)throw Error('Trip was not in flight');
        exit.call(app);
        for(let i=0;i<60;i++){idle.update(1/60,i/60);actions.update(1/60,i/60);}
        const centered=()=>Math.abs(group.position.x)<1e-9&&Math.abs(group.position.z)<1e-9&&Math.abs(group.position.y-0.035)<1e-9;
        const cancelled=centered()&&!actions.hotspotTransition&&!actions.actionTimer&&!idle.paused;

        // Simulate a held interaction without loading private VRM/VRMA assets.
        group.position.set(2.5,0.035,-1.58);idle._captureReturnBaseline(group);
        idle.currentPose='pc_seated_idle';idle._manualPoseActive=true;
        group.position.y-=0.5;
        app.inspectorMode=true;app.speechAnimator._talking=true;exit.call(app);
        for(let i=0;i<60;i++){idle.update(1/60,i/60);actions.update(1/60,i/60);}
        const seated=centered()&&idle.currentPose===null&&!idle._returningToIdle&&idle.paused&&app.speechAnimator._talking;
        actions.reset();
        return {hermesActivityAtmosphere:night&&idleNight&&original,exitDuringMovement:cancelled,seatedRootAndSpeechPreserved:seated};
    });
    for(const [name,passed]of Object.entries(result))assert.equal(passed,true,name);
    return result;
}
