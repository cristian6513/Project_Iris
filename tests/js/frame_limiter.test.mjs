import test from 'node:test';
import assert from 'node:assert/strict';
import { FrameLimiter, renderLimit } from '../../face/js/frame_limiter.js';

for (const hz of [60, 120, 144, 165, 180, 240]) {
    for (const fps of [30, 60, 0]) test(`${hz} Hz: ${fps || 'uncapped'} budget keeps real-time cadence`, () => {
        const gate = new FrameLimiter(fps); let count = 0;
        for (let i=0;i<hz*10;i++) if(gate.ready(i*1000/hz)) count++;
        assert.ok(Math.abs(count - (fps || hz)*10) <= 1, `${count} rendered`);
    });
}
for(const mode of ['anchored','delta']) for(const hz of [59.94,60,144,165,180,240,1000]) for(const limit of [30,60]) {
    test(`${hz} Hz with ±0.3 ms ${mode} jitter retains the requested budget (${limit})`,()=>{
        const gate=new FrameLimiter(limit);let seed=123456789,count=0,now=0;
        for(let i=0;i<Math.round(hz*30);i++){
            seed=(Math.imul(seed,1664525)+1013904223)>>>0;
            const jitter=(seed/2**32*2-1)*0.3;
            now=mode==='anchored'?i*1000/hz+jitter:now+1000/hz+jitter;
            if(gate.ready(now))count++;
        }
        const fps=count/(now/1000);
        assert.ok(Math.abs(fps-Math.min(hz,limit))<0.15,`${fps.toFixed(3)} FPS`);
    });
}
test('invalid preferences fall back, live changes reset budget, hidden views do not render', () => {
    const gate = new FrameLimiter();
    assert.equal(renderLimit('0'), 60); assert.equal(renderLimit(90), 60);
    assert.equal(gate.ready(0), true); assert.equal(gate.ready(1), false);
    gate.setLimit(30); assert.equal(gate.ready(1), true); assert.equal(gate.ready(20), false);
    assert.equal(gate.ready(10000, false), false); assert.equal(gate.ready(10001), true);
    gate.setLimit(0); assert.equal(gate.ready(10002), true);
});
