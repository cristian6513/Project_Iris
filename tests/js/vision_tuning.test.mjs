import test from 'node:test';
import assert from 'node:assert/strict';
import { VisionCaptureTuning } from '../../face/js/vision_capture_tuning.js';
import { HeadMotionEstimator } from '../../face/js/vision_motion.js';
import { applyVisionCalibration } from '../../face/js/vision_calibration_command.js';
import { CameraMocap } from '../../face/js/camera_mocap.js';
import { normalizeNotifications, NotificationCenterApp } from '../../face/js/notification_center.js';
import { filteredDiagnostics } from '../../face/js/vision_tuning_panel.js';

test('a live socket with stalled inference does not report a functioning sensor', () => {
    const cam=new CameraMocap();cam.running=true;cam.tuning.state='observing';
    assert.equal(cam.diagnostics().state,'unavailable');
    cam.presenceObservation={present:true,timestamp:performance.now()};
    assert.equal(cam.diagnostics().state,'observing');
    cam.presenceObservation.timestamp-=4000;
    assert.equal(cam.diagnostics().state,'unavailable');
});


test('late initial samples cannot masquerade as 15 seconds of continuous calibration', () => {
    const tune=new VisionCaptureTuning();tune.configure({enabled:true});tune.startCalibration(0);
    for(let t=12000;t<=16000;t+=100)tune.sample(.1,t);
    assert.equal(tune.calibration,'collecting');
    tune.sample(.1,30000); assert.equal(tune.calibration,'insufficient');
});
test('an in-flight greeting respects a newly disabled gesture or silent profile', async () => {
    const {VisionPresentation}=await import('../../face/js/vision_presentation.js');
    for(const experience of [{greetings:false},{profile:'silent'}]) {
        const presentation=new VisionPresentation({now:()=>1001});
        let calls=0;
        const result=await presentation.present({id:'f'.repeat(32),surface_id:'face',
            kind:'returned',animation:'wave',delivery:'inbox',contains_media:false,
            created_at:1000,expires_at:1008},{surfaceId:'face',vision:{enabled:true,experience},
            status:'idle',documentLike:{visibilityState:'visible'},idle:{isExplicitAnimationActive:()=>false},
            director:{play:async()=>{calls++;}}});
        assert.equal(result,false);assert.equal(calls,0);
    }
});


test('inference cadence follows the selected profile when tuning is enabled', () => {
    const tune = new VisionCaptureTuning();
    assert.equal(tune.interval,0);
    for (const [profile,interval] of [['economy',200],['balanced',67],['responsive',34]]) {
        tune.reset(); tune.configure({enabled:true,performance:profile});
        assert.equal(tune.interval,interval);
        assert.equal(tune.shouldProcess(1000),true);
        assert.equal(tune.shouldProcess(1000+interval-1),false);
        assert.equal(tune.shouldProcess(1000+interval),true);
    }
    tune.configure({enabled:false}); assert.equal(tune.interval,0);
});
test('hidden or overloaded sampling produces no fabricated absence and cancels calibration', () => {
    const cam = new CameraMocap();
    cam.tuning.configure({enabled:true});
    cam.presenceObservation={present:true,timestamp:1000};
    cam.tuning.startCalibration(1000);
    assert.equal(cam.latestPresence(),null);
    cam.tuning.shouldProcess(2000,false);
    assert.equal(cam.latestPresence(),null);
    assert.equal(cam.tuning.calibration,'insufficient');
    cam.tuning.shouldProcess(2200,true);
    for(let i=0;i<3;i++) cam.tuning.latency(250,2200+i*250);
    assert.equal(cam.tuning.state,'overloaded');
    assert.equal(cam.latestPresence(),null);
    assert.equal(cam.tuning.shouldProcess(2800,true),false);
    assert.equal(cam.tuning.shouldProcess(5000,true),true);
});
test('calibration needs opt-in, a continuous quiet window, and discards all samples', () => {
    const tune=new VisionCaptureTuning();
    assert.equal(tune.startCalibration(0),false);
    tune.configure({enabled:true}); assert.equal(tune.startCalibration(0),true);
    for(let t=100;t<=15000;t+=100) tune.sample(.25,t);
    assert.equal(tune.calibration,'ready');
    assert.equal(tune.stillThreshold,.375); assert.equal(tune.activeThreshold,1.5);
    assert.deepEqual(tune.samples,[]);
    tune.reset(); assert.equal(tune.stillThreshold,.3); assert.equal(tune.calibration,'idle');
});
test('noisy, missing and interrupted calibration never applies a new threshold', () => {
    for(const failure of ['noise','gap','timeout']) {
        const tune=new VisionCaptureTuning(); tune.configure({enabled:true}); tune.startCalibration(0);
        if(failure==='noise') for(let t=100;t<=15000;t+=100) tune.sample(3,t);
        if(failure==='gap') {tune.sample(.1,100);tune.sample(.1,900);}
        if(failure==='timeout') tune.shouldProcess(30000,true);
        assert.equal(tune.calibration,failure==='noise'?'noisy':'insufficient');
        assert.equal(tune.stillThreshold,.3); assert.deepEqual(tune.samples,[]);
    }
});
test('motion resets a stale or invalid pose instead of comparing across an interruption', () => {
    const pose=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1];
    const moved=[...pose];moved[12]=20;
    const motion=new HeadMotionEstimator();
    motion.update(pose,1000);
    assert.equal(motion.update(moved,2000),'still');
    assert.equal(motion.instantaneous,null);
    motion.update([NaN],2100);
    assert.equal(motion.previous,null);
});
test('calibration commands are single-use, expiring and cannot create a camera', () => {
    let count=0;
    const cam={running:true,configureVision:()=>{},calibrate:()=>{count++;return true;}};
    const vision={enabled:true};
    const message={id:'c'.repeat(32),surface_id:'face',action:'start',created_at:1000,expires_at:1005};
    assert.equal(applyVisionCalibration(message,{mocap:cam,vision,surfaceId:'other',now:1001}),false);
    assert.equal(applyVisionCalibration({...message,id:'d'.repeat(32)},{mocap:cam,vision,surfaceId:'face',now:1006}),false);
    assert.equal(applyVisionCalibration(message,{mocap:cam,vision,surfaceId:'face',now:1001}),true);
    assert.equal(applyVisionCalibration(message,{mocap:cam,vision,surfaceId:'face',now:1002}),false);
    assert.equal(applyVisionCalibration({...message,id:'e'.repeat(32)},{mocap:null,vision,surfaceId:'face',now:1001}),false);
    assert.equal(count,1);
});
test('UI migration defaults preserve existing settings and export filters remove unknown media fields', () => {
    const state=normalizeNotifications({diagnostics:{rows:[
        {at:1,kind:'long_session',outcome:'suppressed',surface:'face',image:'secret'},
        {at:2,kind:'returned',outcome:'delivered',surface:'desktop_companion'},
    ]}});
    assert.equal(state.preferences.experience.profile,'custom');
    assert.equal(state.preferences.experience.performance,'balanced');
    assert.equal(JSON.stringify(state.diagnostics.rows).includes('secret'),false);
    assert.equal(filteredDiagnostics(state.diagnostics.rows,{kind:'returned',surface:'desktop_companion'}).length,1);
    assert.equal(filteredDiagnostics(state.diagnostics.rows,{kind:'returned',outcome:'suppressed'}).length,0);
});
test('tuning and feedback require explicit confirmation and carry the inbox revision', async () => {
    const app=new NotificationCenterApp();
    app.state=normalizeNotifications({revision:7});
    let calls=0;app._mutation=async(path,body)=>{calls++;assert.equal(path,'/experience');assert.equal(body.enabled,false);};
    const button={disabled:false,dataset:{notifyAction:'tune-greetings'}};
    const previous=globalThis.window;
    try {
        globalThis.window={confirm:()=>false};
        await app._onClick({target:{closest:()=>button}}); assert.equal(calls,0);
        globalThis.window.confirm=()=>true;
        await app._onClick({target:{closest:()=>button}}); assert.equal(calls,1);
    } finally {globalThis.window=previous;}
});
test('stopping pending MediaPipe startup cannot restart capture after cancellation', async () => {
    const previousVideo=globalThis.HTMLVideoElement;
    const previousRaf=globalThis.requestAnimationFrame;
    class Video {readyState=2;}
    let finish;let started;
    const loading=new Promise(resolve=>{started=resolve;});
    globalThis.HTMLVideoElement=Video;
    globalThis.requestAnimationFrame=()=>{throw Error('must not restart');};
    const cam=new CameraMocap({moduleLoader:async()=>({
        FilesetResolver:{forVisionTasks:async()=>({})},
        FaceLandmarker:{createFromOptions:()=>{started();return new Promise(resolve=>{finish=resolve;});}}
    })});
    try {
        const pending=cam.start(new Video()); await loading;
        cam.stop(); finish({close:()=>{}});
        await pending;
        assert.equal(cam.running,false);assert.equal(cam.videoEl,null);
        cam.dispose();
    } finally {globalThis.HTMLVideoElement=previousVideo;globalThis.requestAnimationFrame=previousRaf;}
});
