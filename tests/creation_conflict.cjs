const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const C=require('../viewer/static/creation-conflict.js');
const copy=structuredClone;
function setup(kind){
 const local=kind==='native'?{version:1,source_digest:'immutable',edits:[{kind:'time',values:{days:8}}],workflow:{version:1,step:2}}:{version:1,name:'held input',overview:'not saved',workflow:{version:1,step:2},draft_text:'{ incomplete'};
 const stored=copy(local);if(kind==='native')stored.edits[0].values.days=7;else stored.name='other tab';
 const server={revision:'r1',[kind==='native'?'buffer':'content']:stored,published_world:null},requests=[];let fail=false,defer=null;
 const api=async(path,body)=>{requests.push({path,body:body&&copy(body)});if(!body)return copy(server);if(body.revision!==server.revision||server.published_world)throw Object.assign(Error('updated'),{known:true,status:409});if(defer)await defer;server.revision+='n';server[kind==='native'?'buffer':'content']=copy(body[kind==='native'?'buffer':'content']);if(fail)throw Error('lost response');return copy(server);};
 const session=new C.Session({kind,id:'a'.repeat(32),local,api,uuid:()=> 'b'.repeat(32)});
 return {session,local,server,requests,api,lose:()=>fail=true,hold:p=>defer=p};
}
(async()=>{
 for(const kind of ['new','native']){
  let f=setup(kind),original=copy(f.local);await f.session.read();assert.deepEqual(f.local,original);assert.equal(f.requests.filter(r=>r.body).length,0);assert(C.differences(f.local,C.content(kind,f.server)).length>0);
  let pending;await f.session.commit('current',p=>pending=p);assert.deepEqual(C.content(kind,f.server),original);assert.equal(pending.id,'a'.repeat(32));assert.deepEqual(pending.held,original);assert(!f.requests.some(r=>/fork|create|publish|adopt/.test(r.path)));
  f=setup(kind);await f.session.read();const latest=copy(C.content(kind,f.server));await f.session.commit('saved',()=>{});assert.deepEqual(C.content(kind,f.server),latest);assert.deepEqual(f.local,original);
  f=setup(kind);await f.session.read();f.server.revision='r2';await assert.rejects(()=>f.session.commit('current',()=>{}),e=>e.status===409);assert.deepEqual(f.local,original);await f.session.read();await f.session.commit('current',()=>{});assert.deepEqual(C.content(kind,f.server),original);
  f=setup(kind);await f.session.read();f.lose();let uncertain;await assert.rejects(()=>f.session.commit('current',p=>uncertain=p));const posts=f.requests.filter(r=>r.body).length;await assert.rejects(()=>f.session.commit('current',()=>{}));assert.equal(f.requests.filter(r=>r.body).length,posts);assert.deepEqual(C.content(kind,await f.api(f.session.path)),uncertain.content);assert.deepEqual(f.local,original);
  f=setup(kind);await f.session.read();let release;f.hold(new Promise(resolve=>release=resolve));const first=f.session.commit('current',()=>{});await assert.rejects(()=>f.session.commit('saved',()=>{}));release();await first;assert.equal(f.requests.filter(r=>r.body).length,1);
 }
 let f=setup('native');f.server.published_world='world-done';await f.session.read();await assert.rejects(()=>f.session.commit('current',()=>{}));const count=f.requests.length;const shown=await f.session.commit('saved',()=>{});assert.equal(shown.published_world,'world-done');assert.equal(f.requests.length,count+1);assert(!f.requests.some(r=>r.body));
 f=setup('native');await f.session.read();f.server.published_world='world-done';await assert.rejects(()=>f.session.commit('current',()=>{}),e=>e.status===409);await f.session.read();assert.equal(f.session.readOnly,true);
 // Exercise the actual recovery function: saved stage metadata changes without replacing held input.
 const src=fs.readFileSync(require.resolve('../viewer/static/creation-workflow.js'),'utf8');
 const pending={action:'save',kind:'new',id:'c'.repeat(32),content:{name:'saved',workflow:{version:1,step:2,category:'people',selected:0}}};
 const c={S:{busy:false,pending,kind:'new',originalContent:{workflow:{version:1,step:1}},prep:{name:'held untouched'},unknown:true,conflict:false},copy,fp:C.fingerprint,api:async()=>({revision:'r2',content:copy(pending.content)}),semantic:v=>C.fingerprint(v),controls:()=>{},message:()=>{},$:()=>({textContent:''}),savedConsultation:null};
 vm.createContext(c);vm.runInContext(src.slice(src.indexOf('async function recover(){'),src.indexOf('function closeConflictDialog(){')),c);vm.runInContext(src.split('\n').find(l=>l.startsWith('function savedStage()')),c);await c.recover();assert.equal(c.savedStage(),'② 内容を整える');assert.equal(c.S.prep.name,'held untouched');assert.equal(c.S.unknown,false);
 for(const kind of ['new','native']){
  const value={name:'confirmed choice',workflow:{version:1,step:2}},pending={action:'resolve',kind,id:'d'.repeat(32),content:copy(value)};let opened=null,requested=null;
  const ctx={S:{busy:false,pending,unknown:true,conflict:true},window:{WorldBloomCreationConflict:C},fp:C.fingerprint,api:async path=>{requested=path;return{revision:'r3',[kind==='native'?'buffer':'content']:copy(value)};},controls:()=>{},message:()=>{},$:()=>({textContent:''}),closeConflictDialog:()=>{},loadNative:r=>{opened=r.buffer;},loadNew:async r=>{opened=r.content;}};
  vm.createContext(ctx);vm.runInContext(src.slice(src.indexOf('async function recover(){'),src.indexOf('function closeConflictDialog(){')),ctx);await ctx.recover();assert.equal(requested,(kind==='native'?'/api/native-world-drafts/':'/api/world-draft-workspaces/')+'d'.repeat(32));assert.deepEqual(opened,value);assert.equal(ctx.S.unknown,false);assert.equal(ctx.S.pending,null);
 }
 const malformed={version:1,edits:[{future:'keep'}],source_digest:'unchanged'};assert.deepEqual(C.display('native',malformed,{},()=>{throw Error('unsupported');}),malformed);assert.deepEqual(malformed.edits,[{future:'keep'}]);assert.equal(C.display('new',{draft_text:'{ invalid'},null,null).draft_text,'{ invalid');
 const ro={S:{kind:'native',native:{},published:'world-just-created'}};vm.createContext(ro);vm.runInContext(src.split('\n').find(l=>l.startsWith('function publishedNative()')),ro);assert.equal(ro.publishedNative(),'world-just-created');ro.S.published=null;ro.S.native.published_world='world-reopened';assert.equal(ro.publishedNative(),'world-reopened');ro.S.kind='new';assert.equal(ro.publishedNative(),null);
 console.log('PASS conflict sessions: both kinds, read/cancel, either explicit same-ID choice, CAS recurrence, lost response no resend, double click one POST, publication race read-only, confirmed stage metadata and immediate/reopened read-only predicates.');
})().catch(e=>{console.error(e);process.exitCode=1;});
