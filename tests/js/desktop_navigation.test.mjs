import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { homeRecovery } from '../../face/js/os_recovery.js';
const source = readFileSync(new URL('../../face/js/hermes_activity.js',import.meta.url),'utf8');
test('the real navigation distinguishes Home and Panel, including sidebar and dock, and keeps Settings separate',()=>{
    const code=source.match(/^    _renderDesktopNavigation\([^]*?^    \}/m)[0];const render=new Function(`return ({${code}})._renderDesktopNavigation`)();
    const node=dataset=>({dataset,selected:false,current:false,classList:{toggle(){}},setAttribute(){this.current=true},removeAttribute(){this.current=false}});
    const nodes=[node({workbenchTab:'desktop'}),node({workbenchTab:'settings'}),node({osDesktopMode:'home'}),node({osDesktopMode:'dashboard'})];
    const view={_activeTab:'desktop',_desktopMode:'dashboard',panel:{querySelectorAll:()=>nodes}};
    render.call(view);assert.deepEqual(nodes.map(n=>n.current),[false,false,false,true]);
    view._desktopMode='home';render.call(view);assert.deepEqual(nodes.map(n=>n.current),[true,false,true,false]);
    view._activeTab='settings';render.call(view);assert.deepEqual(nodes.map(n=>n.current),[false,true,false,false]);
});
test('home failures offer recovery separately from technical codes and never assert empty/default data',()=>{
    const reply=homeRecovery({status:503,message:'HTTP 503 / internal_code'});assert.doesNotMatch(reply.message,/HTTP|internal_code/);assert.match(reply.detail,/HTTP/);
    assert.match(reply.message,/no se restablecieron/);assert.equal(reply.action,'Volver a cargar');
    assert.match(homeRecovery({status:409,message:'conflict'}).message,/otra ventana/);
    assert.match(homeRecovery({status:422,message:'bad'}).message,/anterior se conserva/);
});
test('updates and permissions are one click away in the room control center (user feedback, 2026-11-18)',()=>{
    // The desktop sidebar keeps its three: Inicio, Ajustes and Cámara (user's choice).
    const nav=source.slice(source.indexOf('<nav class="workbench-tabs"'),source.indexOf('</nav>',source.indexOf('<nav class="workbench-tabs"')));
    assert.deepEqual([...nav.matchAll(/aria-label="([^"]+)"/g)].map(m=>m[1]).filter(l=>l!=='Vistas de IRIS OS'&&!l.includes('navegación')),['Inicio','Ajustes','Cámara']);
    // The control center (room) holds the quick access, wired straight into IRIS OS.
    const html=readFileSync(new URL('../../face/index.html',import.meta.url),'utf8');
    const app=readFileSync(new URL('../../face/js/app.js',import.meta.url),'utf8');
    const panel=html.slice(html.indexOf('id="side-panel"'));
    assert.ok(panel.indexOf('id="panel-shortcuts"')<panel.indexOf('id="panel-camera"'),'first section of the control center');
    assert.match(panel,/id="panel-open-updates"[^]*?>Buscar actualizaciones<span class="panel-shortcut-dot"/);
    assert.match(panel,/>Permisos<\/button>/);
    assert.match(app,/getElementById\('panel-open-updates'\)[^]*?this\.activityCanvas\?\.openSystemUpdates\?\.\(\)/);
    assert.match(app,/getElementById\('panel-open-permissions'\)[^]*?this\.activityCanvas\?\.openApp\?\.\('permissions'\)/);
    // IRIS OS opens at Ajustes, its updates section, then checks the channel after the local read.
    const open=source.match(/^    async openSystemUpdates\(\) \{[^]*?^    \}/m)[0];
    assert.ok(open.indexOf('this.open()')<open.indexOf("this._setTab('settings')"));
    assert.ok(open.indexOf("this._setTab('settings')")<open.indexOf('#settings-system'));
    assert.ok(open.indexOf('await this._systemUpdateResume')<open.indexOf('this._checkSystemUpdates(false)'));
});

test('the updates shortcut checks the channel after a slow local status read', async t => {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const code = source.match(/^    async openSystemUpdates\(\) \{[^]*?^    \}/m)[0];
    const open = new Function(`return ({${code}}).openSystemUpdates`)();
    let finishStatus;
    let finished = false;
    let checks = 0;
    const statusRead = new Promise(resolve => { finishStatus = resolve; });
    const view = {
        open() {},
        _setTab() {
            this._systemUpdateLoading = true;
            this._systemUpdateResume = statusRead;
        },
        _systemUpdateApplying: false,
        async _checkSystemUpdates() {
            if (!this._systemUpdateLoading) checks++;
        },
    };
    const opening = open.call(view).then(() => { finished = true; });
    for (let i = 0; i < 30; i++) {
        t.mock.timers.tick(150);
        await Promise.resolve();
    }
    assert.equal(finished, false, 'the channel request must wait for the local read');
    assert.equal(checks, 0);
    view._systemUpdateLoading = false;
    finishStatus();
    await opening;
    assert.equal(checks, 1, 'the requested channel check still runs after the status read');
});
test('the visible corner grip sits above the window content, so it can be dragged (user report, 2026-11-18)',()=>{
    // Without a z-index the app's view covered the grip; only the invisible 6 px edges resized.
    const css=readFileSync(new URL('../../face/css/styles.css',import.meta.url),'utf8');
    // The block that starts with exactly this selector (plain text search, no regex escaping).
    const rule=(selector)=>{
        const start=css.indexOf(`\n${selector} {`);
        return start<0?'':css.slice(css.indexOf('{',start)+1,css.indexOf('}',start));
    };
    const z=(selector)=>Number(rule(selector).match(/z-index:\s*(\d+)/)?.[1]??NaN);
    assert.ok(z('.os-window-resize')>z('.os-window-edge'),'grip above the edge strips');
    assert.ok(z('.os-window-edge')>=3);
});
