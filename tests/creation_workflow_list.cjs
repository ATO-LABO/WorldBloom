const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const ui=require('../viewer/static/creation-workflow-list.js');
class El{constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.attrs={};this.textContent='';this.listeners={};}append(...els){this.children.push(...els);}replaceChildren(){this.children=[];}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this.listeners[k]=f;}}
const doc={createElement:t=>new El(t),createElementNS:(ns,t)=>new El(t)},host=new El('ul');
let rows=ui.normalize([{workspaces:[{workspace_id:'a'.repeat(32),name:'<script>plain</script>',overview:'長い概要'.repeat(60),workflow_step:3,updated_at:2},{workspace_id:'b'.repeat(32),name:'',updated_at:null}]},{workspaces:[{workspace_id:'c'.repeat(32),name:'取込',workflow_step:2,updated_at:1},{workspace_id:'d'.repeat(32),unsupported:true}]}]);
assert.equal(rows.length,4);assert.equal(rows[0].step,3);assert.equal(rows[1].kind,'native');assert.equal(rows[2].step,null);assert.equal(ui.stageLabel(null),'保存段階不明');assert.equal(ui.dateLabel(null),'保存日時不明');
let opened=[];ui.render(host,rows,(kind,id)=>opened.push([kind,id]),doc);assert.equal(host.children.length,4);
const [icon,body,meta,open]=host.children[0].children;assert.equal(body.children[0].textContent,'<script>plain</script>');assert.equal(body.children[1].textContent,rows[0].overview);assert.equal(icon.attrs['aria-hidden'],'true');assert.equal(open.attrs['aria-label'],'<script>plain</script>を再開する');open.listeners.click();assert.deepEqual(opened,[['new','a'.repeat(32)]]);
assert(host.children.find(e=>e.children[3].dataset.cwLocked==='true').children[3].disabled);
const source=fs.readFileSync(require.resolve('../viewer/static/creation-workflow.js'),'utf8');const line=name=>source.split('\n').find(x=>x.startsWith('async function '+name+'('));
const controls={},pending=[],rendered=[],events={};let saveCount=0;
const c={listEpoch:0,listBusy:false,resuming:false,Promise,document:doc,window:{WorldBloomSavedDrafts:{normalize:v=>v,render:(h,v)=>rendered.push(v)}},$:k=>controls[k]||(controls[k]=new El(k)),controls:()=>{},api:path=>new Promise((resolve,reject)=>pending.push({path,resolve,reject})),guard:()=>true,canSwitch:async()=>c.allowSwitch,run:async(k,fn)=>fn(),loadNative:r=>opened.push(['native',r.id]),loadNew:async r=>opened.push(['new',r.id]),allowSwitch:true};
vm.createContext(c);vm.runInContext(line('refresh')+'\n'+line('openSaved'),c);
(async()=>{
 const a=c.refresh(),b=c.refresh();assert.equal(pending.length,4);
 pending[2].resolve({workspaces:['latest']});pending[3].resolve({workspaces:[]});await b;
 pending[0].resolve({workspaces:['stale']});pending[1].resolve({workspaces:[]});await a;assert.equal(rendered.length,1);assert.equal(rendered[0][0].workspaces[0],'latest');assert.equal(c.listBusy,false);
 const failed=c.refresh();pending[4].reject(Error('offline'));pending[5].resolve({workspaces:[]});await failed;assert.match(controls['list-status'].textContent,/取得できません/);assert.equal(controls['list-count'].textContent,'件数未確認');assert.equal(c.listBusy,false);
 c.allowSwitch=false;const count=pending.length;await c.openSaved('native','c'.repeat(32));assert.equal(pending.length,count);assert.equal(c.resuming,false);
 c.allowSwitch=true;const one=c.openSaved('native','c'.repeat(32));const two=c.openSaved('new','a'.repeat(32));await new Promise(resolve=>setImmediate(resolve));assert.equal(pending.length,count+1);pending[count].resolve({id:'chosen'});await Promise.all([one,two]);assert.deepEqual(opened.at(-1),['native','chosen']);assert.equal(c.resuming,false);
 console.log('PASS saved draft rows: real metadata/unknowns, text safety, correct per-row ID, unsupported disabled, out-of-order reads ignored, list failure retryable, cancelled switch and double resume guarded. Offline only.');
})().catch(e=>{console.error(e);process.exitCode=1;});

const publishedRows=ui.normalize([{workspaces:[]},{workspaces:[{workspace_id:'e'.repeat(32),name:'作成済み',published_world:'world-example',workflow_step:2}]}]);assert.equal(publishedRows[0].readOnly,true);ui.render(host,publishedRows,(kind,id)=>opened.push([kind,id]),doc);assert.equal(host.children[0].children[2].children[0].textContent,'作成済み・閲覧専用');assert.equal(host.children[0].children[3].textContent,'内容を見る');assert.equal(host.children[0].children[3].attrs['aria-label'],'作成済みを閲覧する');
