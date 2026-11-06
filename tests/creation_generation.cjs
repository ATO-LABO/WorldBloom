const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const c={window:{},structuredClone};vm.createContext(c);
for(const n of ['creation-workflow-model','creation-generation'])vm.runInContext(fs.readFileSync(require.resolve('../viewer/static/'+n+'.js'),'utf8'),c);
const M=c.window.WorldBloomCreationModel,G=c.window.WorldBloomCreationGeneration,plain=x=>JSON.parse(JSON.stringify(x));
const content=()=>({version:1,name:'人の名前',overview:'人の概要',draft_text:JSON.stringify(M.empty('人の名前','人の概要')),time:{enabled:false,days:'',slots:'朝\n昼\n夕方\n夜'},authoring:{version:1,type:'unknown-type',outline:{genre:'任意',introduction:'旧導入',future:'保持'}},source_snapshot:{private:'preserve'},future_field:false,workflow:{version:1,step:1,category:'time',selected:0}});
const storage=()=>{const m=new Map();return {setItem:(k,v)=>m.set(k,v),getItem:k=>m.get(k)??null};};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const id='a'.repeat(32),wid='b'.repeat(32);
(async()=>{
 let base=content();assert(G.eligible(base));
 for(const [key,val]of [['people',[{name:'人'}]],['initial_story','導入'],['future',false],['future',0],['future',[]],['people',null],['settings',null]]){
  const x=content(),d=JSON.parse(x.draft_text);d[key]=val;x.draft_text=JSON.stringify(d);assert.equal(G.eligible(x),false,key);
 }
 assert(!G.eligible({...base,draft_text:'{ invalid'}));assert(G.eligible({...base,draft_text:''}));
 let d=JSON.parse(base.draft_text);d.settings={version:1,time:{days:19,slots:['early','late'],future:{v:false}}};base.draft_text=JSON.stringify(d);
 let result={request_id:id,state:'review',revision:'r',draft:{...M.empty(),people:[{name:'AI甲'},{name:'AI乙'}],places:[{name:'AI場所'}],settings:{version:1,time:{days:2,slots:['bad']}}}};
 const applied=G.entered(base,result);assert.equal(JSON.parse(applied.draft_text).settings.time.days,19);assert.deepEqual(plain(applied.authoring),base.authoring);assert.deepEqual(plain(applied.source_snapshot),base.source_snapshot);assert.equal(applied.future_field,false);assert.equal(applied.workflow.step,2);assert.equal(JSON.parse(applied.draft_text).name,base.name);assert.equal(JSON.parse(applied.draft_text).overview,base.overview);
 const removed=G.entered(content(),result);assert.equal(JSON.parse(removed.draft_text).settings,undefined);assert.equal(removed.time.enabled,false);const reopened=JSON.parse(removed.draft_text);M.hydrateTiming(reopened,removed.time);assert.equal(reopened.settings,undefined);
 let post=0,reads=0,wait=deferred(),disk=storage(),s=new G.Session({storage:disk,uuid:()=>id,api:async(path,body)=>{if(body){post++;return wait.promise;}reads++;return result;}});
 const snapshot={kind:'new',id:wid,revision:'v1',content:base};let recorded=false;
 const sent=s.send(snapshot,r=>{recorded=true;assert.equal(s.read(r.id).state,'pending');});assert(recorded);assert.equal(post,1);
 await assert.rejects(s.send(snapshot,()=>{}));assert.equal(post,1);
 const reloaded=new G.Session({storage:disk,uuid:()=>{throw Error('No new ID on recovery');},api:s.api});reloaded.read(id);assert.equal(post,1);await reloaded.check();assert.equal(reads,1);assert.equal(post,1);
 wait.resolve(result);await sent;assert.equal(s.receipt.state,'ready');assert.equal(s.receipt.result,undefined);assert(s.matches(snapshot));assert(!s.matches({...snapshot,revision:'v2'}));assert(!s.matches({...snapshot,id:'c'.repeat(32)}));assert(!s.matches({...snapshot,content:{...base,name:'新しい入力'}}));
 s.api=async()=>({revision:'v2',content:base});assert.equal(await s.current(snapshot),false);s.api=async()=>({revision:'v1',content:base});assert.equal(await s.current(snapshot),true);
 s.detach();assert(s.receipt.detached);s.mark('applied');const reloadApplied=new G.Session({storage:disk,api:async()=>result,uuid:()=>id});assert.equal(reloadApplied.read(id).state,'applied');await assert.rejects(reloadApplied.send(snapshot,()=>{}));
 const unavailable=new G.Session({storage:{setItem:()=>{throw Error('quota');},getItem:()=>null},uuid:()=>id,api:()=>{throw Error('Must not POST');}});await assert.rejects(unavailable.send(snapshot,()=>{}),/quota/);
 let lostCalls=0;const lost=new G.Session({storage:storage(),uuid:()=>id,api:async(path,body)=>{if(body){lostCalls++;throw Error('lost response');}return result;}});await assert.rejects(lost.send(snapshot,()=>{}));assert.equal(lost.receipt.state,'unknown');await assert.rejects(lost.send(snapshot,()=>{}));await lost.check();assert.equal(lostCalls,1);assert.equal(lost.receipt.state,'ready');
 for(const [state,want]of [['generating','pending'],['unknown','unknown'],['invalid','failed'],['cancelled','failed']]){const x=new G.Session({storage:storage(),uuid:()=>id,api:async()=>({...result,state})});await x.send(snapshot,()=>{});assert.equal(x.receipt.state,want);assert.throws(()=>G.entered(base,{...result,state}));}
 const corrupted=storage();corrupted.setItem('worldbloom.creation-generation.v1.'+id,JSON.stringify({version:1,id,content:base,signature:'wrong'}));assert.throws(()=>new G.Session({storage:corrupted}).read(id));
 const restricted={get localStorage(){throw Error('blocked');}},restrictedStorage=G.receiptStorage(restricted);assert.throws(()=>restrictedStorage.getItem('x'),/再送しません/);assert.throws(()=>restrictedStorage.setItem('x','y'),/開始していません/);
 console.log('PASS generation receipts: no double POST/reload replay, durable snapshot, quota-before-POST, lost response GET, late/detached and peer revision guard, exact human settings/unknown metadata preservation, terminal failure boundaries.');
})().catch(e=>{console.error(e);process.exitCode=1;});

// Exercise the actual mounted event handlers, not a reimplementation of their
// guards. Both recovery GETs can resolve/reject after the dialog was dismissed.
function recoveryHarness({stage='proposal',resultState='review',detached=false,creation=false}={}){
 const doc={activeElement:null};c.document=doc;
 class Element{
  constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.dataset={};this.disabled=false;this.open=false;this.textContent='';}
  append(...items){this.children.push(...items);}prepend(...items){this.children.unshift(...items);}
  replaceChildren(...items){this.children=[...items];}
  addEventListener(name,fn){(this.events[name]??=[]).push(fn);}
  fire(name,extra={}){const event={preventDefault(){},...extra};return Promise.all((this.events[name]||[]).map(fn=>fn(event)));}
  focus(){doc.activeElement=this;}showModal(){this.open=true;}close(){this.open=false;}
  querySelectorAll(tag){return this.children.flatMap(x=>[...(x.tag===tag?[x]:[]),...x.querySelectorAll(tag)]);}
 }
 doc.createElement=tag=>new Element(tag);
 const fields=Object.fromEntries(['dialog','title','body','actions','recovery','status','recover'].map(k=>[k,new Element(k==='dialog'?'dialog':k==='recover'?'button':'div')]));
 const disk=storage();c.window.localStorage=disk;
 const base=content(),snapshot={kind:'new',id:wid,revision:'v1',content:base},held=structuredClone(snapshot),delayed=deferred();
 const result={request_id:id,state:resultState,revision:'r',draft:{...M.empty(),people:[{name:'AI甲'},{name:'AI乙'}],places:[{name:'AI場所'}]}};
 const log={proposalGET:0,workspaceGET:0,post:0,applied:0,manual:0,waiting:false};
 const adapter={uuid:()=>id,message:()=>{},track:()=>{},lock:()=>{},save:async()=>true,snapshot:()=>held,
  manual(){log.manual++;held.content={...held.content,workflow:{...held.content.workflow,step:2}};},
  apply(){log.applied++;},
  async api(path,body){
   if(body){log.post++;if(creation&&path==='/api/world-drafts')return result;throw Error('Recovery must never POST');}
   if(path==='/api/world-draft-generation')return {status:'configured',strategies:{'goal-plan-v1':{status:'configured'}}};
   if(path==='/api/world-drafts/'+id){log.proposalGET++;if(stage==='proposal'){log.waiting=true;return delayed.promise;}return result;}
   if(path==='/api/world-draft-workspaces/'+wid){log.workspaceGET++;if(stage==='workspace'){log.waiting=true;return delayed.promise;}return {revision:'v1',content:base};}
   throw Error('Unexpected path '+path);
  }};
 const mounted=G.mount({querySelector:s=>fields[s.slice('[data-cwg-'.length,-1)]},adapter);
 if(!creation){mounted.session.persist({version:1,id,workspace_id:wid,revision:'v1',content:base,signature:M.fingerprint(base),state:'unknown',detached});mounted.resume(id);}
 mounted.open();
 const action=name=>{const b=fields.actions.children.find(x=>x.textContent===name);assert(b,'Missing action '+name);return b;};
 return {doc,fields,adapter,mounted,held,log,delayed,result,base,action};
}
async function reachPending(h){for(let i=0;i<25&&!h.log.waiting;i++)await Promise.resolve();assert(h.log.waiting,'Expected delayed '+h.log.workspaceGET+' GET');}
(async()=>{
 let cases=0;
 for(const stage of ['proposal','workspace'])for(const exit of ['Escape','Manual','Back','Close','Different draft']){
  const h=recoveryHarness({stage}),pending=h.action('送信した結果を確認').fire('click');await reachPending(h);
  if(exit==='Escape')await h.fields.dialog.fire('cancel');
  else if(exit==='Manual')await h.action('マニュアルで入力する').fire('click');
  else if(exit==='Close')h.fields.dialog.close();
  else{await h.action('戻る').fire('click');if(exit==='Different draft')h.held.id='c'.repeat(32);}
  assert.equal(h.fields.dialog.open,false,exit);
  h.delayed.resolve(stage==='proposal'?h.result:{revision:'v1',content:h.base});await pending;
  assert.equal(h.log.applied,0,stage+' '+exit);assert.equal(h.fields.dialog.open,false,'No late modal '+stage+' '+exit);
  assert.equal(h.log.post,0);assert.equal(h.mounted.session.receipt.state,'ready');cases++;
 }
 for(const stage of ['proposal','workspace']){
  const h=recoveryHarness({stage}),pending=h.action('送信した結果を確認').fire('click');await reachPending(h);await h.fields.dialog.fire('cancel');h.delayed.reject(Error('late GET failure'));await pending;
  assert.equal(h.log.applied,0);assert.equal(h.fields.dialog.open,false,'No error modal after Escape');assert.equal(h.log.post,0);cases++;
 }
 for(const resultState of ['generating','unknown','invalid']){
  const h=recoveryHarness({resultState}),pending=h.action('送信した結果を確認').fire('click');await reachPending(h);await h.fields.dialog.fire('cancel');h.delayed.resolve(h.result);await pending;
  assert.equal(h.log.applied,0);assert.equal(h.fields.dialog.open,false,'No late progress/error modal');cases++;
 }
 // A new explicit recovery is allowed even if an older presentation detached.
 for(const stage of ['proposal','workspace']){
  const h=recoveryHarness({stage,detached:true}),b=h.action('送信した結果を確認'),pending=b.fire('click');await reachPending(h);await b.fire('click');
  assert.equal(h.log.proposalGET,1,'Duplicate recovery button must not duplicate GET');h.delayed.resolve(stage==='proposal'?h.result:{revision:'v1',content:h.base});await pending;
  assert.equal(h.log.applied,1,'A new explicit recovery can apply');assert.equal(h.fields.dialog.open,false);assert.equal(h.log.post,0);cases++;
 }
 const normal=recoveryHarness({stage:'none',creation:true});await normal.action('3段階で設定を作る').fire('click');assert.equal(normal.log.post,1);assert.equal(normal.log.applied,1,'Internal close before saving must preserve the original explicit generation run');assert.equal(normal.fields.dialog.open,false);cases++;
 console.log('PASS recovery lifecycle '+cases+' scenarios: proposal/workspace GET delayed success+Escape/Manual/Back/Close/different draft, delayed failure+Escape, no late progress/error, duplicate recovery click, fresh explicit recovery after detach, and explicit three-stage generation.');
})().catch(e=>{console.error(e);process.exitCode=1;});
