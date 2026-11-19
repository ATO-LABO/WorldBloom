const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../viewer/static/creation-workflow.js'),'utf8');
const line=name=>source.split('\n').find(x=>x.startsWith('function '+name+'(')||x.startsWith('async function '+name+'('));
function setup(){
 const fields={},events={},calls={save:0,push:0,render:0,refresh:0,ensure:0};
 const S={screen:'workflow',active:true,step:2,id:'a'.repeat(32),prep:{name:'keep',overview:'unsaved'},raw:'keep raw',epoch:0};
 const c={S,calls,aiTargets:{},savedConsultation:null,historyIndex:0,bouncing:false,
  location:{href:'http://localhost/worlds/new?mode=create'},URL,
  guard:()=>!c.blocked,dirty:()=>c.isDirty,saveDraft:async()=>{calls.save++;return c.saveOK;},
  window:{confirm:()=>c.confirmed,addEventListener:(k,f)=>events[k]=f},
  $:k=>fields[k]||(fields[k]={}),message:()=>{},pushNavigation:()=>calls.push++,render:()=>calls.render++,
  refresh:async()=>calls.refresh++,ensureDraft:()=>calls.ensure++,carry:()=>{c.carriedId=S.id;},history:{go:d=>c.delta=d},
  confirmed:false,isDirty:true,saveOK:true,blocked:false};
 vm.createContext(c);vm.runInContext(['canSwitch','beginNew','navigateScreen','navigate'].map(line).join('\n'),c);
 vm.runInContext(source.split('\n').find(x=>x.startsWith("window.addEventListener('popstate'")),c);
 return {c,S,calls,events};
}
(async()=>{
 let {c,S,calls,events}=setup();const before=JSON.stringify(S);
 await c.beginNew();assert.equal(JSON.stringify(S),before);assert.equal(calls.save,0);assert.equal(calls.push,0);
 c.confirmed=true;c.saveOK=false;await c.beginNew();assert.equal(JSON.stringify(S),before);assert.equal(calls.save,1);
 c.saveOK=true;await c.beginNew();assert.equal(calls.save,2);assert.equal(S.id,null);assert.equal(S.step,1);assert.equal(S.prep.name,'');assert.equal(calls.push,1);
 ({c,S,calls,events}=setup());c.navigateScreen('entry');assert.equal(S.raw,'keep raw');assert.equal(S.prep.overview,'unsaved');assert.equal(calls.save,0);
 c.navigateScreen('world-import');assert.equal(S.screen,'world-import');assert.equal(S.raw,'keep raw');c.navigateScreen('zip-import');assert.equal(S.screen,'zip-import');assert.equal(S.raw,'keep raw');c.navigateScreen('import');assert.equal(S.screen,'world-import');c.navigateScreen('resume');assert.equal(calls.refresh,1);c.navigate(2);assert.equal(S.screen,'workflow');assert.equal(calls.ensure,1);
 c.blocked=true;const held=JSON.stringify(S);assert.equal(c.navigateScreen('import'),false);await c.beginNew();assert.equal(JSON.stringify(S),held);assert.equal(calls.save,0);
 c.historyIndex=4;events.popstate({state:{cwIndex:3,cwScreen:'entry',cwStep:1}});assert.equal(c.delta,1);assert.equal(JSON.stringify(S),held);
 c.blocked=false;c.bouncing=false;events.popstate({state:{cwIndex:3,cwScreen:'entry',cwStep:1}});assert.equal(S.screen,'entry');assert.equal(S.raw,'keep raw');assert.equal(c.carriedId,S.id);events.popstate({state:{cwIndex:2,cwScreen:'import',cwStep:1}});assert.equal(S.screen,'world-import');assert.equal(S.raw,'keep raw');
 console.log('PASS creation navigation: cancelled/failed save preserves buffer; confirmed switch saves once; entry/history retain edits; resume refresh; pending/conflict/unknown guard blocks transitions. Controller functions, offline only.');
})().catch(e=>{console.error(e);process.exitCode=1;});
