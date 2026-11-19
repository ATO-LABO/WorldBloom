const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const{environment}=require('./purpose_dom_ui.cjs');
const base=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/melos-independent100-review-v1.json')));
const saved={mode:'create',world:'momotaro',note:'以前のメモ',history:[{...base,id:'other',world:'romance'},{...base,id:'own',world:'momotaro'}],selected:0};
const e=environment({initialSaved:saved,rootDataset:{worldReview:'momotaro',worldAliases:'["momotaro"]'}}),f=e.fields;
assert.equal(f.world.value,'momotaro');assert.equal(e.label.textContent,'既存物語レビュー');assert.equal(f.note.value,'以前のメモ');
assert.equal(f.history.children.length,1);assert.equal(f.history.children[0].textContent.includes('own'),true);
assert.equal(e.stored().history.length,2);f.note.value='世界別メモ';f.note.fire('input');
assert.equal(e.context.WorldBloomPurpose.readWorkNote('momotaro'),'世界別メモ');assert.equal(e.context.WorldBloomPurpose.readWorkNote('romance'),'');
f.file.files=[{size:100}];f.import.fire('click');e.load(0,{...base,id:'foreign-new',world:'romance'});assert.match(f.status.textContent,/別の世界/);assert.equal(e.stored().history.length,2);
f.file.files=[{size:100}];f.import.fire('click');e.load(1,{...base,id:'own-new',world:'momotaro'});assert.equal(e.stored().history.length,3);assert.equal(f.history.children.length,2);
// A second scoped page must not erase a receipt written by another page.
const fresh=e.stored();fresh.history.push({...base,id:'other-tab',world:'romance'});e.map.set('worldbloom-purpose-v1',JSON.stringify(fresh));f.note.fire('input');assert.equal(e.stored().history.length,4);
// Legacy pending requests migrate only into the owning world's session.
const C=f.status.constructor,controls={};for(const key of ['config','seeds','start','retry','cancel','status','plan'])controls[key]=new C();controls.seeds.value='0';Object.defineProperty(controls.config,'options',{get(){return this.children;}});
const independentRoot=new C();independentRoot.querySelector=s=>controls[s.match(/data-independent-(.*)\]/)[1]];
const original=e.context.document.querySelector;e.context.document.querySelector=s=>s==='[data-independent-root]'?independentRoot:s==='[data-purpose-world]'?f.world:original(s);
e.context.crypto=crypto.webcrypto;e.context.setTimeout=()=>1;e.context.clearTimeout=()=>{};
const H='a'.repeat(64),cap={schema_version:1,supported:true,world:'momotaro',config_id:'cfg-own',method:'independent_trials',population:'unselected',selection:'none',reference_guided:false,max_trials:100,...Object.fromEntries(['run_spec_hash','world_revision','policy_revision','settings_version','provider_revision'].map(k=>[k,H]))};
const request={request_id:'review-old',kind:'independent_trials',config_id:'cfg-own',method:'independent_trials',selection:'none',reference_guided:false};e.map.set('worldbloom-independent-job-v1',JSON.stringify({active:{request,job:null,resultLoaded:false}}));let posts=0;
e.context.fetch=async url=>{if(url==='/api/independent/capabilities')return{ok:true,json:async()=>({configurations:[cap,{...cap,world:'romance',config_id:'cfg-other'}]})};posts++;throw Error('no automatic execution');};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../viewer/static/purpose-independent.js'),'utf8'),e.context);
setImmediate(()=>{assert.equal(controls.config.children.length,1);assert.equal(controls.config.children[0].value,'cfg-own');assert.equal(posts,0);assert.equal(JSON.parse(e.map.get('worldbloom-independent-job-v1:momotaro')).active.request.request_id,'review-old');assert(e.map.has('worldbloom-independent-job-v1'));console.log('World review scope checks passed: notes, result identity, preserving other worlds and pending requests, no automatic execution.');});
