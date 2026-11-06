const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const generate='3\u6bb5\u968e\u3067\u8a2d\u5b9a\u3092\u4f5c\u308b',cancel='\u30ad\u30e3\u30f3\u30bb\u30eb',manual='\u30de\u30cb\u30e5\u30a2\u30eb\u3067\u5165\u529b\u3059\u308b';
const configured={status:'configured',strategies:{'goal-plan-v1':{status:'configured',required_calls:3}}};
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
async function flush(){for(let i=0;i<20;i++)await Promise.resolve();}
function harness(status=configured){
 const doc={activeElement:null};
 class E{constructor(tag){this.tag=tag;this.children=[];this.events={};this.dataset={};this.disabled=false;this.open=false;this.textContent='';}append(...x){this.children.push(...x);}prepend(...x){this.children.unshift(...x);}replaceChildren(...x){this.children=x;}addEventListener(k,f){(this.events[k]??=[]).push(f);}fire(k){if(k==='click'&&this.disabled)return Promise.resolve();return Promise.all((this.events[k]||[]).map(f=>f({preventDefault(){}})));}focus(){doc.activeElement=this;}showModal(){this.open=true;}close(){this.open=false;}querySelectorAll(tag){return this.children.flatMap(x=>[...(x.tag===tag?[x]:[]),...x.querySelectorAll(tag)]);}}
 doc.createElement=t=>new E(t);
 const disk=new Map(),c={window:{localStorage:{setItem:(k,v)=>disk.set(k,v),getItem:k=>disk.get(k)}},document:doc,structuredClone};vm.createContext(c);
 for(const f of ['creation-workflow-model','creation-generation'])vm.runInContext(fs.readFileSync(require.resolve('../viewer/static/'+f+'.js'),'utf8'),c);
 const G=c.window.WorldBloomCreationGeneration,M=c.window.WorldBloomCreationModel,id='a'.repeat(32),wid='b'.repeat(32);
 const content={version:1,name:'world',overview:'overview',draft_text:JSON.stringify(M.empty('world','overview')),authoring:null,workflow:{version:1,step:1,category:'people',selected:0},time:{enabled:false,days:'',slots:''}};
 const snapshot={kind:'new',id:wid,revision:'v1',content};
 const fields=Object.fromEntries(['dialog','title','body','actions','recovery','status','recover'].map(k=>[k,new E(k==='dialog'?'dialog':k==='recover'?'button':'div')]));
 const log={posts:[],manual:0,saves:0},pending=deferred();
 const adapter={uuid:()=>id,message(){},track(){},lock(){},snapshot:()=>snapshot,save:async()=>{log.saves++;return true;},manual(){log.manual++;},apply(){},api:async(path,body)=>{if(body){log.posts.push({path,body});return pending.promise;}if(path==='/api/world-draft-generation'){if(status instanceof Error)throw status;return status;}if(path==='/api/world-draft-workspaces/'+wid)return {revision:'v1',content};throw Error('Unexpected '+path);}};
 const mounted=G.mount({querySelector:s=>fields[s.slice('[data-cwg-'.length,-1)]},adapter);
 return {G,content,fields,log,pending,mounted,button:label=>{const b=fields.actions.children.find(x=>x.textContent===label);assert(b,'Missing '+label);return b;}};
}
(async()=>{
 let h=harness();h.mounted.open();assert.deepEqual(h.fields.actions.children.map(x=>x.textContent),[cancel,manual,generate]);assert(h.button(generate).disabled);await flush();assert(!h.button(generate).disabled);assert(!h.fields.actions.children.some(x=>x.textContent==='OK'));
 const b=h.button(generate),first=b.fire('click'),second=b.fire('click');await flush();assert.equal(h.log.posts.length,1);assert.equal(h.log.posts[0].body.strategy,'goal-plan-v1');assert.equal(h.log.saves,1);h.pending.resolve({request_id:'a'.repeat(32),state:'unknown',errors:{}});await Promise.all([first,second]);assert.equal(h.log.posts.length,1);
 for(const status of [{status:'unavailable'},{status:'configured',strategies:{'goal-plan-v1':{status:'insufficient_budget'}}},{status:'configured'},new Error('status unavailable')]){h=harness(status);h.mounted.open();await flush();assert.equal(h.fields.actions.children.length,3);assert(h.button(generate).disabled);await h.button(generate).fire('click');assert.equal(h.log.posts.length,0);assert.equal(h.log.saves,0);assert(!h.fields.actions.children.some(x=>x.textContent==='OK'));}
 for(const label of [cancel,manual]){h=harness();h.mounted.open();await flush();await h.button(label).fire('click');assert.equal(h.fields.dialog.open,false);assert.equal(h.log.posts.length,0);assert.equal(h.log.manual,label===manual?1:0);}
 h=harness();assert.equal(h.G.payload(h.content,'test').strategy,undefined);assert.equal(h.G.payload(h.content,'test','goal-plan-v1').strategy,'goal-plan-v1');
 console.log('PASS single-action dialog: exactly cancel/manual/3-stage; fail-closed availability; explicit strategy; double-click one POST; manual/cancel no inference; legacy payload helper retained.');
})().catch(e=>{console.error(e);process.exitCode=1;});
