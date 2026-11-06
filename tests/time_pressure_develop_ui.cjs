// Offline state and develop-adapter contracts; no browser or HTTP listener.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'viewer/static/time-settings.js'),'utf8');
let calls=[],reply,hold=null,mode='ok';
const context={window:{},document:{addEventListener(){}},addEventListener(){},history:{state:{},replaceState(state,_title,hash){this.state=state;context.location.hash=hash;}},location:{hash:'#screen=time&tab=pressure'},
 URLSearchParams,JSON,Promise,console,confirm:()=>true,fetch:async(url,request={})=>{
  calls.push({url,request});if(hold)await hold;if(mode==='network')throw Error('offline fixture');
  return {ok:mode==='ok'||mode==='malformed',status:mode==='ok'||mode==='malformed'?200:Number(mode),json:async()=>mode==='malformed'?{}:structuredClone(reply)};
 }};
vm.createContext(context);vm.runInContext(source+'\nthis.Settings=WorldTimeSettings;this.Pressure=WorldTimePressure;',context);
const initial={revision:'r1',world_revision:'w1',settings:{version:1,sources:[]},catalog:{goals:[],events:[],actors:[],facts:[]},availability:{available:true,reason:'fixture'}};
function pressure(){const value=new context.Pressure({id:'fixture',editable:true});value.accept(structuredClone(initial));
 value.render=()=>{};value.host={querySelector(selector){return selector==='form'?{reportValidity:()=>true}:{focus(){}};}};value.focus=()=>{};return value;}
function edit(value){value.settings.sources.push({id:'source',anchor:{kind:'goal_period',id:'goal'},response_goal_id:'goal',actor_ids:['hero'],awareness:{kind:'public'}});}
(async()=>{
 const p=pressure();edit(p);const draft=JSON.stringify(p.settings);reply={...structuredClone(initial),revision:'r2',settings:JSON.parse(draft)};
 let release;hold=new Promise(resolve=>release=resolve);const first=p.save();assert(p.busy);await p.save();assert.equal(calls.length,1);
 const payload=JSON.parse(calls[0].request.body);assert.equal(payload.revision,'r1');assert.equal(payload.world_revision,'w1');
 release();await first;hold=null;assert(!p.busy);assert(!p.dirty());assert.equal(p.data.revision,'r2');
 for(const failure of ['409','500','network','malformed']){
  const q=pressure();edit(q);const before=JSON.stringify(q.settings);mode=failure;const start=calls.length;
  await q.save();assert(q.blocked);assert.equal(JSON.stringify(q.settings),before);assert(q.error);await q.save();assert.equal(calls.length,start+1);
 }
 mode='ok';const q=pressure();edit(q);q.model.editable=false;const start=calls.length;await q.save();assert.equal(calls.length,start);
 const t=new context.Settings({id:'fixture',editable:true});const calendar={hidden:false},panel={hidden:true};
 const buttons=['calendar','pressure'].map(key=>({dataset:{tsPressureTab:key},setAttribute(name,value){this[name]=value;}}));
 t.frameHost={querySelectorAll(){return buttons;},querySelector(selector){return selector==='[data-tp-root]'?panel:calendar;}};
 let mounted=0;t.pressure.mount=host=>{assert.equal(host,panel);mounted++;};
 const calendarDraft={retained:'calendar'},pressureDraft={version:1,sources:[{id:'unsaved'}]};t.settings=calendarDraft;t.pressure.settings=pressureDraft;
 t.selectPressureTab('pressure');assert(calendar.hidden);assert(!panel.hidden);assert.equal(buttons[1]['aria-selected'],'true');
 t.selectPressureTab('calendar');assert(!calendar.hidden);assert(panel.hidden);assert.equal(t.settings,calendarDraft);assert.equal(t.pressure.settings,pressureDraft);
 t.pressure.busy=true;t.selectPressureTab('pressure');assert.equal(t.pressureTab,'calendar');assert.equal(mounted,1);
 assert(source.includes("event.key==='ArrowRight'"));assert(source.includes("event.key==='Home'"));
 assert(!source.includes("['overrides','曜日・休日']"),'Do not carry unrelated six-tab redesign into develop');
 assert(fs.readFileSync(path.join(root,'viewer/static/world-prototype.js'),'utf8').includes("new URLSearchParams(location.hash.slice(1)).get('screen')==='time'"));
 assert(!fs.readFileSync(path.join(root,'viewer/run_settings.py'),'utf8').includes("scripts=('ui-help.js'"));
 console.log('PASS pressure: dual revisions, saved state, pending/double-submit, conflict/network/malformed retention, readonly, two-panel draft retention, keyboard/deep-link contracts. Offline state fixtures only.');
})().catch(error=>{console.error(error);process.exitCode=1;});
